from steward.analysis.sources_status import write_sources_status
from steward.sources.registry import LOADERS, UNAVAILABLE
from steward.store import write_source
from tests.conftest import make_trajectory


def test_every_registered_source_is_listed(settings) -> None:
    write_source(
        settings.paths.normalized,
        "swe_chat",
        [make_trajectory("s1", source="swe_chat", domain="terminal_devops")],
    )
    text = write_sources_status(settings)
    for name in [*LOADERS, *UNAVAILABLE]:
        assert f"| {name} |" in text
    assert "| swe_chat | swe | ingested | 1 |" in text
    assert "| devops_sessions_in_swe_sources | terminal_devops | ingested | 1 |" in text
    assert (settings.paths.reports / "SOURCES_STATUS.md").exists()
