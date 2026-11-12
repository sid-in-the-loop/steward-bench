"""Recording a session on a workplace world and rebuilding its state, with a stand-in world."""

from pathlib import Path

from steward.environment import gym_sessions as gs
from steward.environment.gym import GymImages, TaskDescription, World
from steward.execution.policy import Turn
from steward.execution.record import Action
from steward.scenario.worlds import rendering

FIRST = "11111111-2222-3333-4444-555555555555"
SECOND = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


class FakeWorld(World):
    """Creates events under made-up identifiers and remembers them."""

    def __init__(self, identifiers: list[str]) -> None:
        images = GymImages(tools=Path("t.sif"), postgres=Path("p.sif"), repository=Path("repo"))
        super().__init__(Path("unused"), "plan-trip", images)
        self.identifiers = list(identifiers)
        self.events: dict[str, str] = {}
        self.description = TaskDescription(
            workspace="/w", system="Be useful.", task="Plan.", tools=[]
        )

    def create(self) -> None:
        self.events.clear()

    def start_tools(self, restored: bool = False) -> TaskDescription:
        assert self.description is not None
        return self.description

    def call(self, tool: str, arguments: dict[str, object]) -> tuple[str, bool]:
        if tool == "create_event":
            identifier = self.identifiers.pop(0)
            self.events[identifier] = str(arguments["title"])
            return f"Event created with ID: {identifier}", True
        if tool == "delete_event":
            found = self.events.pop(str(arguments["id"]), None)
            return (f"Deleted {found}", True) if found else ("Event not found", False)
        return "Done.", True


class Scripted:
    def __init__(self, turns: list[Turn]) -> None:
        self.turns = list(turns)

    def next_turn(self, messages: list[dict[str, object]]) -> Turn:
        return self.turns.pop(0)


def recorded() -> gs.Trajectory:
    turns = [
        Turn(action=Action(tool="create_event", arguments={"title": "Launch"})),
        Turn(message="Shall I go on?"),
        Turn(action=Action(tool="delete_event", arguments={"id": FIRST})),
        Turn(action=Action(tool="claim_done", arguments={})),
    ]
    return gs.record_session(FakeWorld([FIRST]), Scripted(turns), "lab/model", max_turns=10)


def test_a_session_is_recorded_until_the_agent_claims_done() -> None:
    session = recorded()
    assert session.trajectory_id == "toolathlon_gym:lab_model/plan-trip"
    assert [s.kind for s in session.steps] == [
        "system", "user", "action", "result", "agent_text", "user", "action", "result",
        "action", "result",
    ]  # fmt: skip
    assert session.steps[5].content == gs.CONTINUE
    assert session.steps[7].content == "Deleted Launch"
    assert "[2] ACTION create_event" in rendering(session)


def test_rebuilding_uses_the_identifiers_the_new_world_gives() -> None:
    session = recorded()
    fidelity, history, names = gs.rebuild(FakeWorld([SECOND]), session, takeover_step=8)
    assert names == {FIRST: SECOND}
    assert (fidelity.replayed, fidelity.matched, fidelity.failed) == (2, 2, 0)
    assert history[3].content == f"Event created with ID: {SECOND}"
    assert history[6].tool_input == {"id": SECOND}
    assert gs.renamed({"ids": [FIRST, "x"]}, names) == {"ids": [SECOND, "x"]}
