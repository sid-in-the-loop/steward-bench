"""Sessions recorded on Toolathlon-GYM worlds, and rebuilding a world at a step of one.

A session here is produced by letting a model work on a GYM task through the world's real
tools. Because the tools read and write only the world's own database and files, running the
same calls again from a fresh world reaches the same state, and that state is saved once per
session and takeover step.
"""

from __future__ import annotations

import fcntl
import hashlib
import logging
import re
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from steward.config import Settings
from steward.environment.fidelity import ReplayFidelity, ReplayLog
from steward.environment.gym import GymImages, World
from steward.execution.history import REPORT_TOOL, function_tool, to_messages
from steward.execution.policy import Message, Policy
from steward.execution.record import Action
from steward.schema import Step, Trajectory

log = logging.getLogger(__name__)

SOURCE = "toolathlon_gym"
LICENSE = "Apache-2.0"
DONE_TOOL = "claim_done"
# What the task's user says when the agent stops to talk: GYM tasks are single requests.
CONTINUE = "Please continue and complete the task on your own."
# Identifiers a tool makes up when it creates something.
IDENTIFIER = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")


def images(settings: Settings) -> GymImages:
    canvas = settings.paths.container_images / "canvas_server"
    return GymImages(
        tools=settings.paths.container_images / "gym.sif",
        postgres=settings.paths.container_images / "postgres.sif",
        repository=settings.paths.upstream_tasks / SOURCE,
        canvas_server=canvas if canvas.is_dir() else None,
    )


def tools_of(world: World) -> list[Message]:
    """The world's tools as the model is offered them, with the tool for the final report."""
    assert world.description is not None
    report = function_tool(
        REPORT_TOOL,
        "Finish the task and report the outcome to the user.",
        {"type": "object", "properties": {"report": {"type": "string"}}, "required": ["report"]},
    )
    return [*world.description.tools, report]


def record_session(world: World, policy: Policy, author: str, max_turns: int) -> Trajectory:
    """Let a model work on the world's task and return what happened as a session. The world
    must be newly created with its tools started."""
    assert world.description is not None
    steps = [
        Step(index=0, kind="system", content=world.description.system),
        Step(index=1, kind="user", content=world.description.task),
    ]

    def add(kind: str, content: str, action: Action | None = None) -> None:
        steps.append(
            Step(
                index=len(steps),
                kind=kind,
                content=content,
                tool=action.tool if action else None,
                tool_input=action.arguments if action else None,
            )
        )

    for _ in range(max_turns):
        turn = policy.next_turn(to_messages(steps))
        if turn.report is not None:
            add("agent_text", turn.report)
            break
        if turn.action is None:
            add("agent_text", turn.message or "")
            add("user", CONTINUE)
            continue
        add("action", turn.action.text, turn.action)
        output, _ = world.call(turn.action.tool, turn.action.arguments)
        add("result", output)
        if turn.action.tool == DONE_TOOL:
            break
    return Trajectory(
        trajectory_id=f"{SOURCE}:{author.replace('/', '_')}/{world.task}",
        source=SOURCE,
        domain="workplace",
        real_user=False,
        history_author=author,
        harness=SOURCE,
        task_env=f"{SOURCE}:{world.task}",
        license=LICENSE,
        steps=steps,
        meta={"task_name": world.task},
    )


def renamed(value: Any, names: dict[str, str]) -> Any:
    """`value` with every recorded identifier replaced by the one the rebuilt world gave."""
    if not names:
        return value
    if isinstance(value, str):
        return IDENTIFIER.sub(lambda found: names.get(found.group(0), found.group(0)), value)
    if isinstance(value, dict):
        return {key: renamed(item, names) for key, item in value.items()}
    if isinstance(value, list):
        return [renamed(item, names) for item in value]
    return value


def rebuild(
    world: World, trajectory: Trajectory, takeover_step: int
) -> tuple[ReplayFidelity, list[Step], dict[str, str]]:
    """Create the world and run the session's tool calls before the takeover step again, in
    order. Returns how closely their results matched the recorded ones, the history as the
    rebuilt world produced it, and the identifiers it gave in place of the recorded ones.

    A tool that creates something names it with a random identifier, so the rebuilt world's
    identifiers differ from the recorded ones. Each recorded identifier is mapped to the new
    one where it first appears, later calls are made with the new one, and the returned
    history shows the results the rebuilt world gave, so the history and the state agree.
    """
    world.create()
    world.start_tools()
    replay = ReplayLog()
    names: dict[str, str] = {}
    history: list[Step] = []
    outputs: list[str] = []
    for step in trajectory.steps[:takeover_step]:
        if step.kind == "action" and step.tool:
            arguments = renamed(step.tool_input or {}, names)
            output, worked = world.call(step.tool, arguments)
            replay.ran(step.index, step.content, 0 if worked else 1, output)
            outputs.append(output)
            action = Action(tool=step.tool, arguments=arguments)
            history.append(
                step.model_copy(update={"tool_input": arguments, "content": action.text})
            )
        elif step.kind == "result" and outputs:
            output = outputs.pop(0)
            old, new = IDENTIFIER.findall(step.content), IDENTIFIER.findall(output)
            if len(old) == len(new):
                names.update(zip(old, new, strict=True))
            replay.result(renamed(step.content, names))
            history.append(step.model_copy(update={"content": output}))
        else:
            history.append(step.model_copy(update={"content": renamed(step.content, names)}))
    return replay.flush(), history, names


def state_archive(settings: Settings, trajectory_id: str, takeover_step: int) -> Path:
    key = hashlib.sha1(trajectory_id.encode()).hexdigest()[:12]
    return settings.paths.data / "phase2" / "gym_states" / f"{key}-t{takeover_step}.tar"


class Rebuilt(BaseModel):
    """What is kept next to a saved state: how well it was rebuilt, its history, and the
    identifiers the rebuilt world gave in place of the recorded ones."""

    fidelity: ReplayFidelity
    history: list[Step]
    names: dict[str, str] = {}


@contextmanager
def saving(archive: Path) -> Iterator[None]:
    """Hold the lock on a saved state while it is built or looked for, so that runs which
    start the same state at once build it one at a time and the later ones load it."""
    archive.parent.mkdir(parents=True, exist_ok=True)
    with archive.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def write_whole(path: Path, text: str) -> None:
    """Write a file aside and move it into place, so no reader sees it part-written."""
    partial = path.with_name(f"{path.name}.{secrets.token_hex(4)}.partial")
    partial.write_text(text)
    partial.replace(path)


def world_at(
    settings: Settings, trajectory: Trajectory, takeover_step: int, directory: Path
) -> tuple[World, Trajectory, Rebuilt]:
    """A world in `directory` holding the session's state at the takeover step, with its tools
    started; the session as the rebuilt world names things; and the record of the rebuild.
    The state is rebuilt and saved the first time it is asked for."""
    task = str(trajectory.meta["task_name"])
    archive = state_archive(settings, trajectory.trajectory_id, takeover_step)
    with saving(archive):
        if not archive.exists() or not archive.with_suffix(".json").exists():
            builder = World(directory.with_name(directory.name + "-build"), task, images(settings))
            try:
                fidelity, history, names = rebuild(builder, trajectory, takeover_step)
                record = Rebuilt(fidelity=fidelity, history=history, names=names)
                write_whole(archive.with_suffix(".json"), record.model_dump_json())
                builder.save(archive)
            finally:
                builder.stop()
    rebuilt = Rebuilt.model_validate_json(archive.with_suffix(".json").read_text())
    world = World(directory, task, images(settings))
    world.restore(archive)
    world.start_tools(restored=True)
    later = [
        step.model_copy(
            update={
                "content": renamed(step.content, rebuilt.names),
                "tool_input": renamed(step.tool_input, rebuilt.names),
            }
        )
        for step in trajectory.steps[takeover_step:]
    ]
    return world, trajectory.model_copy(update={"steps": [*rebuilt.history, *later]}), rebuilt


def session_files(settings: Settings) -> Path:
    return settings.paths.data / "phase2" / "gym_sessions"


def recorded_sessions(settings: Settings) -> list[Trajectory]:
    """Every session recorded on a GYM world so far."""
    files = sorted(session_files(settings).glob("*.json"))
    return [Trajectory.model_validate_json(file.read_text()) for file in files]
