"""The Harbor agent that runs one scenario: rebuild the recorded state, then continue from it."""

from __future__ import annotations

from pathlib import Path
from typing import Any, override

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from steward.config import load_settings
from steward.environment.shell import ContainerExecutor, replay
from steward.execution.policy import Policy, ScriptedPolicy
from steward.execution.session import Session
from steward.filter.selection import load_by_ids
from steward.principal.principal import ScriptedResponder
from steward.scenario.spec import ReferenceExecution, ScenarioSpec, load_spec

RECORD_FILE = "execution.json"
SOLUTION_DIR = "/solution"


class StewardAgent(BaseAgent):
    """Runs a scenario with a reference execution.

    Agent options: `spec_path`, the scenario specification; `reference`, the name of the
    reference execution to replay; `task_dir`, the compiled task directory.
    """

    def __init__(self, logs_dir: Path, *args: Any, **kwargs: Any) -> None:
        self.spec = load_spec(Path(kwargs.pop("spec_path")))
        self.reference_name = kwargs.pop("reference", None)
        self.task_dir = Path(kwargs.pop("task_dir"))
        super().__init__(logs_dir, *args, **kwargs)

    @staticmethod
    @override
    def name() -> str:
        return "steward"

    @override
    def version(self) -> str:
        return "0.1.0"

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        pass

    def reference(self) -> ReferenceExecution:
        for reference in self.spec.reference_executions:
            if reference.name == self.reference_name:
                return reference
        raise ValueError(f"unknown reference execution {self.reference_name!r}")

    def policy(self, reference: ReferenceExecution) -> Policy:
        return ScriptedPolicy(
            reference.actions, reference.report, ignore_halts=reference.expected_safety == 0.0
        )

    async def reconstruct(self, shell: ContainerExecutor, spec: ScenarioSpec) -> None:
        settings = load_settings()
        trajectory = next(load_by_ids(settings, [spec.source.trajectory_id]))
        await replay(shell, trajectory.steps[: spec.source.takeover_step])

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        reference = self.reference()
        shell = ContainerExecutor(environment)
        await shell.start()
        await self.reconstruct(shell, self.spec)
        if reference.uses_task_solution:
            await environment.upload_dir(self.task_dir / "solution", SOLUTION_DIR)
        session = Session(
            self.spec,
            shell,
            self.policy(reference),
            ScriptedResponder(self.spec.principal),
            history=[],
        )
        record = await session.run()
        (self.logs_dir / RECORD_FILE).write_text(record.model_dump_json(indent=2))
