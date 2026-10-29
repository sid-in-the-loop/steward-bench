from steward.store import is_written, iter_trajectories, read_meta, write_source, written_sources
from tests.conftest import make_trajectory


def test_round_trip(tmp_path) -> None:
    trajectories = [make_trajectory("a"), make_trajectory("b", n_actions=5)]
    assert write_source(tmp_path, "fixture", trajectories) == 2
    assert is_written(tmp_path, "fixture")
    assert written_sources(tmp_path) == ["fixture"]
    loaded = list(iter_trajectories(tmp_path, "fixture"))
    assert [t.trajectory_id for t in loaded] == ["fixture:a", "fixture:b"]
    assert loaded[0] == trajectories[0]
    metas = read_meta(tmp_path, "fixture")
    assert metas[1].agent_actions == 5


def test_duplicates_dropped_and_filter_by_id(tmp_path) -> None:
    write_source(
        tmp_path, "fixture", [make_trajectory("a"), make_trajectory("a"), make_trajectory("c")]
    )
    assert len(read_meta(tmp_path, "fixture")) == 2
    only = list(iter_trajectories(tmp_path, "fixture", only={"fixture:c"}))
    assert [t.trajectory_id for t in only] == ["fixture:c"]
