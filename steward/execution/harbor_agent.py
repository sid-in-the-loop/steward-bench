"""The Harbor agent that runs one scenario: rebuild the recorded state, then continue from it."""

from __future__ import annotations

from pathlib import Path
from typing import Any, override

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from steward.config import load_settings
from steward.environment.fidelity import ReplayFidelity
from steward.environment.recorded import PlantedFirst
from steward.environment.shell import ContainerExecutor, recorded_host, replay
from steward.execution.policy import Policy, ScriptedPolicy
from steward.execution.record import ExecutionRecord
from steward.execution.session import Session
from steward.filter.selection import load_by_ids
from steward.principal.principal import ScriptedResponder
from steward.scenario.spec import ReferenceExecution, load_spec
from steward.schema import Trajectory

RECORD_FILE = "execution.json"
RESULT_FILE = "run.json"
FIDELITY_FILE = "replay.json"
SOLUTION_DIR = "/solution"


class StewardAgent(BaseAgent):
    """Runs a scenario with a reference execution or with a model.

    Agent options: `spec_path`, the scenario specification; `task_dir`, the compiled task
    directory; either `reference`, the name of the reference execution to replay, or
    `request_path`, a file holding the run request for a model; and optionally
    `trajectory_path`, the recorded session as a file, which saves searching the corpus.
    """

    def __init__(self, logs_dir: Path, *args: Any, **kwargs: Any) -> None:
        self.spec = load_spec(Path(kwargs.pop("spec_path")))
        self.reference_name = kwargs.pop("reference", None)
        self.request_path = kwargs.pop("request_path", None)
        self.trajectory_path = kwargs.pop("trajectory_path", None)
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

    def trajectory(self) -> Trajectory:
        """The recorded session: from the file the caller wrote, or found in the corpus."""
        if self.trajectory_path:
            return Trajectory.model_validate_json(Path(self.trajectory_path).read_text())
        return next(iter(load_by_ids(load_settings(), [self.spec.source.trajectory_id])))

    def policy(self, reference: ReferenceExecution) -> Policy:
        return ScriptedPolicy(
            reference.actions, reference.report, ignore_halts=reference.expected_safety == 0.0
        )

    async def reconstruct(self, shell: ContainerExecutor, trajectory: Trajectory) -> ReplayFidelity:
        fidelity = await replay(shell, trajectory.steps[: self.spec.source.takeover_step])
        (self.logs_dir / FIDELITY_FILE).write_text(fidelity.model_dump_json())
        return fidelity

    async def run_model(
        self, shell: ContainerExecutor, trajectory: Trajectory, fidelity: ReplayFidelity
    ) -> ExecutionRecord:
        # Imported here: the evaluation module reaches this one through the trial runner.
        from steward.execution.evaluate import RunRequest, continue_scenario

        settings = load_settings()
        request = RunRequest.model_validate_json(Path(self.request_path).read_text())
        endpoint = request.endpoint
        result = await continue_scenario(
            endpoint.client(settings),
            endpoint.model,
            self.spec,
            trajectory,
            request.condition,
            lambda recorded: PlantedFirst(shell, recorded),
            provider=endpoint.provider,
            pricing=request.pricing,
            dialect=endpoint.dialect,
        )
        result.replay_fidelity = fidelity.rate
        (self.logs_dir / RESULT_FILE).write_text(result.model_dump_json())
        return result.record

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        shell = ContainerExecutor(environment)
        trajectory = self.trajectory()
        await shell.start(recorded_host(trajectory.steps))
        fidelity = await self.reconstruct(shell, trajectory)
        if self.request_path:
            record = await self.run_model(shell, trajectory, fidelity)
        elif self.reference_name is None:
            # Only the prefix was asked for: the replay record is the result.
            record = ExecutionRecord(scenario_id=self.spec.scenario_id)
        else:
            reference = self.reference()
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
