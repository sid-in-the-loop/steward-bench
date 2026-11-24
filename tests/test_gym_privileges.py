"""The agent's tool servers run as a role that cannot see the private schema."""

from pathlib import Path

from steward.environment.gym import (
    AGENT_USER,
    PRIVATE_SCHEMA,
    SHARE_WITH_AGENT,
    USER,
    GymImages,
    World,
)


def world(tmp_path: Path) -> World:
    images = GymImages(
        postgres=tmp_path / "pg.sif", tools=tmp_path / "tools.sif", repository=tmp_path
    )
    return World(tmp_path / "world", "task", images)


def test_the_agent_role_is_never_given_the_private_schema() -> None:
    granted = [line for line in SHARE_WITH_AGENT.splitlines() if "grant" in line]
    assert all(PRIVATE_SCHEMA not in line for line in granted)
    assert f"revoke all on schema {PRIVATE_SCHEMA} from public" in SHARE_WITH_AGENT
    assert "superuser" not in SHARE_WITH_AGENT


def test_setup_and_checks_keep_the_owner_and_the_agent_tools_do_not(tmp_path: Path) -> None:
    owner = " ".join(world(tmp_path).tools("bash", "-c", "true"))
    agent = " ".join(world(tmp_path).tools("bash", "-c", "true", user=AGENT_USER))
    assert f"PGUSER={USER}" in owner and f"PG_USER={USER}" in owner
    assert f"PGUSER={AGENT_USER}" in agent and f"PG_USER={AGENT_USER}" in agent


def test_a_schema_created_later_is_shared_unless_it_is_the_private_one() -> None:
    assert "create event trigger share_new_schema on ddl_command_end" in SHARE_WITH_AGENT
    assert f"object_identity <> '{PRIVATE_SCHEMA}'" in SHARE_WITH_AGENT
