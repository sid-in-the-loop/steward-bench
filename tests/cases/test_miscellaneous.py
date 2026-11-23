"""The Miscellaneous domain and its base script: the domain key and the tools the base builds,
read from the script without running it."""

import ast
import re
import subprocess
from pathlib import Path
from typing import get_args

from steward.cases.case import DOMAIN_NAMES, Domain, Repo, case_identifier, load_case
from steward.cases.exchanges import SHOP_TOPICS
from steward.cases.sessions import load_session_spec

CASES = Path(__file__).parents[2] / "cases"
BASE = CASES / "_bases" / "home.sh"
TOOLS = (
    "access",
    "bookings",
    "calendar",
    "chat",
    "devices",
    "jobs",
    "ledger",
    "mail",
    "orders",
    "publish",
    "tickets",
)


def test_miscellaneous_is_a_domain_with_its_own_name_and_identifiers() -> None:
    assert "misc" in get_args(Domain)
    assert DOMAIN_NAMES["misc"] == "Miscellaneous"
    assert set(DOMAIN_NAMES) == set(SHOP_TOPICS)
    identifier = case_identifier("misc", "Posting a creator's scheduled content")
    assert identifier.startswith("misc-") and len(identifier) == len("misc-") + 10


def test_the_base_script_parses() -> None:
    done = subprocess.run(["bash", "-n", str(BASE)], text=True, capture_output=True, check=False)
    assert done.returncode == 0, done.stderr


def written_files() -> dict[str, str]:
    """Each file the script writes with a quoted here-document, by its path."""
    pattern = r"cat >>? (\S+) <<'EOF'\n(.*?)\nEOF\n"
    return dict(re.findall(pattern, BASE.read_text(), flags=re.DOTALL))


def test_the_base_builds_the_documented_tools() -> None:
    files = written_files()
    assert {name for name in files if name.startswith("tools/")} == {
        f"tools/{name}" for name in (*TOOLS, "_home.py")
    }
    readme = files["README.md"]
    for name in TOOLS:
        assert f"`tools/{name}`" in readme


def test_every_tool_is_valid_python_with_a_main_entry() -> None:
    for path, text in written_files().items():
        if path.startswith("tools/"):
            ast.parse(text)
            if path != "tools/_home.py":
                assert text.startswith("#!/usr/bin/env python3\n")
                assert 'if __name__ == "__main__":' in text


def test_the_job_runner_can_simulate_a_later_time() -> None:
    text = written_files()["tools/jobs"]
    assert '"--at"' in text and "HOME_NOW" in text


def test_the_first_miscellaneous_task_is_on_the_home_base() -> None:
    directory = CASES / "misc-2968d0cbce"
    case = load_case(directory)
    spec = load_session_spec(directory, case)
    assert spec is not None and spec.goal is not None and spec.conflict is not None
    assert case.domain == "misc"
    assert case.id == directory.name == case_identifier("misc", case.title)
    assert isinstance(case.environment, Repo) and case.environment.base == "home.sh"
    assert case.setup == spec.item == (directory / "item.sh").read_text()
    assert [person.role for person in case.people] == ["requester", "owner", "bystander"]
