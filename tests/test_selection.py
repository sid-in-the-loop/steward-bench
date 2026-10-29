from steward.filter.selection import round_robin, select_with_floor, with_full_domains


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


def test_with_full_domains_adds_every_item_of_listed_domains() -> None:
    items = [("swe", 3), ("swe", 2), ("workplace", 1), ("workplace", 0)]
    chosen = [items[0]]
    result = with_full_domains(chosen, items, lambda i: i[0], lambda i: (-i[1],), ["workplace"])
    assert result == [("swe", 3), ("workplace", 1), ("workplace", 0)]
