from steward.schema import Step
from steward.sources.domain_rule import assign_domain


def actions(commands: list[str]) -> list[Step]:
    return [Step(index=i, kind="action", content=c, tool="Bash") for i, c in enumerate(commands)]


def test_devops() -> None:
    assert (
        assign_domain(actions(["docker build .", "kubectl get pods", "terraform plan", "ls"]))
        == "terminal_devops"
    )


def test_ai_rnd() -> None:
    assert (
        assign_domain(actions(["nvidia-smi", "python train.py", "wandb login", "ls"])) == "ai_rnd"
    )


def test_plain_swe() -> None:
    assert assign_domain(actions(["pytest", "git diff", "ls"])) == "swe"


def test_undecided_when_signal_is_weak_but_present() -> None:
    commands = ["docker ps", "docker logs x", "docker build ."] + ["pytest"] * 30
    assert assign_domain(actions(commands)) is None


def test_no_actions_is_undecided() -> None:
    assert assign_domain([]) is None
