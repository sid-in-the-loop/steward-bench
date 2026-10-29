"""Deterministic input selection with per-domain floors, and loading trajectories by id."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence

from steward.config import Settings
from steward.schema import Trajectory
from steward.store import iter_trajectories


def select_with_floor[T](
    items: Sequence[T],
    domain_of: Callable[[T], str],
    sort_key: Callable[[T], tuple[object, ...]],
    top_n: int,
    floor: int,
) -> list[T]:
    """Take the top `floor` of every domain (or all of a smaller one), then fill to top_n.

    Items are ranked by sort_key ascending, so callers negate scores to rank high first.
    """
    ranked = sorted(items, key=sort_key)
    by_domain: dict[str, list[T]] = defaultdict(list)
    for item in ranked:
        by_domain[domain_of(item)].append(item)
    chosen_ids: set[int] = set()
    chosen: list[T] = []
    for domain in sorted(by_domain):
        for item in by_domain[domain][:floor]:
            chosen_ids.add(id(item))
            chosen.append(item)
    for item in ranked:
        if len(chosen) >= max(top_n, len(chosen_ids)):
            break
        if id(item) not in chosen_ids:
            chosen_ids.add(id(item))
            chosen.append(item)
    return sorted(chosen, key=sort_key)


def with_full_domains[T](
    chosen: Sequence[T],
    items: Sequence[T],
    domain_of: Callable[[T], str],
    sort_key: Callable[[T], tuple[object, ...]],
    domains: Sequence[str],
) -> list[T]:
    """The chosen items plus every item whose domain is in domains, ranked by sort_key."""
    picked = {id(item): item for item in chosen}
    for item in items:
        if domain_of(item) in domains:
            picked.setdefault(id(item), item)
    return sorted(picked.values(), key=sort_key)


def round_robin[T](
    items: Sequence[T],
    domain_of: Callable[[T], str],
    sort_key: Callable[[T], tuple[object, ...]],
    n: int,
) -> list[T]:
    """Pick n items alternating across domains, best first within each domain."""
    by_domain: dict[str, list[T]] = defaultdict(list)
    for item in sorted(items, key=sort_key):
        by_domain[domain_of(item)].append(item)
    queues = [by_domain[d] for d in sorted(by_domain)]
    picked: list[T] = []
    position = 0
    while len(picked) < n and any(position < len(q) for q in queues):
        for queue in queues:
            if position < len(queue) and len(picked) < n:
                picked.append(queue[position])
        position += 1
    return picked


def source_of(trajectory_id: str) -> str:
    return trajectory_id.split(":", 1)[0]


def load_by_ids(settings: Settings, trajectory_ids: Sequence[str]) -> Iterator[Trajectory]:
    """Stream the requested trajectories, grouped by source."""
    wanted: dict[str, set[str]] = defaultdict(set)
    for trajectory_id in trajectory_ids:
        wanted[source_of(trajectory_id)].add(trajectory_id)
    for source in sorted(wanted):
        yield from iter_trajectories(settings.paths.normalized, source, only=wanted[source])
