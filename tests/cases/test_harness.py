"""The harness version: the loop's protocol and a hash of every module on the session path."""

import ast
import shutil
from pathlib import Path

from steward.cases.harness import SESSION_MODULES, SOURCE_ROOT, harness_version, module_path


def steward_imports(module: str) -> set[str]:
    """The steward modules a module imports anywhere in its source, functions included."""
    path = module_path(module)
    package = module if path.name == "__init__.py" else module.rsplit(".", 1)[0]
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            name = node.module or ""
            if node.level:
                parts = package.split(".")
                base = ".".join(parts[: len(parts) - node.level + 1])
                name = f"{base}.{name}" if name else base
            found.add(name)
            found |= {f"{name}.{alias.name}" for alias in node.names}
    return {name for name in found if name.startswith("steward") and module_path(name).exists()}


def test_every_module_run_session_reaches_is_hashed() -> None:
    reached: set[str] = set()
    todo = ["steward.cases.sessions"]
    while todo:
        module = todo.pop()
        if module not in reached:
            reached.add(module)
            todo += steward_imports(module) - reached
    assert reached | {"steward.cases.schedule"} == set(SESSION_MODULES)
    assert all(module_path(module).exists() for module in SESSION_MODULES)


def test_changing_any_session_module_makes_a_new_harness(tmp_path: Path) -> None:
    before = harness_version(5)
    assert before.startswith("5-") and before == harness_version(5)
    assert harness_version(6) != before
    copy = tmp_path / "steward"
    shutil.copytree(SOURCE_ROOT / "steward", copy)
    assert harness_version(5, tmp_path) == before
    tools = module_path("steward.environment.tools", tmp_path)
    tools.write_text(tools.read_text() + "\n# a reworded error string\n")
    assert harness_version(5, tmp_path) != before
