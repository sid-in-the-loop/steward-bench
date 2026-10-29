from steward.filter.selection import round_robin, select_with_floor


def test_floor_then_fill() -> None:
    items = [("a", 10), ("a", 9), ("a", 8), ("b", 1), ("c", 5)]
    chosen = select_with_floor(items, lambda x: x[0], lambda x: (-x[1],), top_n=3, floor=1)
    assert sorted(chosen) == sorted([("a", 10), ("b", 1), ("c", 5)])
    chosen = select_with_floor(items, lambda x: x[0], lambda x: (-x[1],), top_n=4, floor=1)
    assert ("a", 9) in chosen and len(chosen) == 4


def test_floor_can_exceed_top_n() -> None:
    items = [(d, 1) for d in "abcde"]
    assert len(select_with_floor(items, lambda x: x[0], lambda x: (x[0],), top_n=2, floor=1)) == 5


def test_round_robin() -> None:
    items = [("a", 3), ("a", 2), ("b", 1)]
    assert round_robin(items, lambda x: x[0], lambda x: (-x[1],), 3) == [
        ("a", 3),
        ("b", 1),
        ("a", 2),
    ]
