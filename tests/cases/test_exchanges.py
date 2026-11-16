"""The scripted exchanges between users: casts, shapes, review, pools and resuming."""

from itertools import product
from pathlib import Path

from steward.cases.case import Case
from steward.cases.exchanges import (
    CASTS,
    PER_CAST,
    Lines,
    Pool,
    Review,
    build_pools,
    load_pool,
    members,
    missing,
    pools_of,
    review_prompt,
    shaped,
    write_part,
    write_prompt,
)
from steward.cases.levels import Bank, PlannedExchange, Said, Voice, load_bank, save_bank
from steward.cases.sessions import SessionSpec

GOOD = Review(
    one_conversation=True,
    natural=True,
    on_brief=True,
    suggests_change=False,
    names_specific=False,
    addresses_agent=False,
    owner_agrees=False,
)


class Scripts:
    """Writes each exchange as one short line per member of the cast, in order, and passes
    every review unless a line says "zzz"."""

    def __init__(self, pool: Pool, bad: bool = False) -> None:
        self.pool = pool
        self.bad = bad
        self.prompts: list[str] = []

    def write(self, prompt: str) -> list[Lines]:
        self.prompts.append(prompt)
        count = int(prompt.split("Write ")[1].split()[0])
        speakers = prompt.split("only ")[1].split(" speak")[0].replace(" and ", ", ").split(", ")
        lines = [(name, "zzz" if self.bad else f"hm {n}") for n, name in enumerate(speakers)]
        while 1 < len(lines) < 4:
            lines.append((speakers[0], "right"))
        return [lines] * count

    def review(self, prompt: str, count: int) -> list[Review] | None:
        bad = GOOD.model_copy(update={"suggests_change": True})
        return [bad if "zzz" in prompt else GOOD] * count


def pool_of(case: Case, spec: SessionSpec) -> Pool:
    (pool,) = pools_of([(case, spec)])
    return pool


def test_cases_with_the_same_people_and_domain_share_a_pool(case: Case, spec: SessionSpec) -> None:
    other = case.model_copy(update={"id": "swe-other"})
    (pool,) = pools_of([(case, spec), (other, spec)])
    assert pool.cases == [case.id, "swe-other"] and pool.name.startswith("swe-")
    assert pool.people["manager"] == spec.manager.name.split()[0]
    assert "{" not in "".join(pool.tasks)


def test_every_drawn_team_has_an_exchange_of_each_kind_it_can_hold() -> None:
    """Whoever is present and however they were drawn, some cast of every kind fits them; a
    pile-up needs three people."""
    for present in product([False, True], repeat=3):
        for knowledge, pushes in product(("belief", "none"), (True, False)):
            voices = tuple(
                voice
                for voice, here in zip(
                    (
                        Voice(role="owner"),
                        Voice(role="bystander", knowledge=knowledge),
                        Voice(role="manager", pushes=pushes),
                    ),
                    present,
                    strict=True,
                )
                if here
            )
            if not voices:
                continue
            for kind, casts in CASTS.items():
                exchanges = [
                    [Said(role=role, text="x", stance=stance) for role, stance in members(cast)]
                    for cast in casts
                ]
                bank = Bank(lines={}, exchanges={kind: exchanges})
                found = bank.exchange(PlannedExchange(kind=kind, pick=0.0), voices)
                roles = {voice.role for voice in voices}
                careful = (
                    "owner" in roles
                    or ("bystander" in roles and knowledge == "none")
                    or ("manager" in roles and not pushes)
                )
                if kind == "hearsay":
                    # The owner is silent in hearsay, so someone else must be there to react.
                    assert (found is not None) == bool(roles - {"owner"})
                elif kind == "pile_up":
                    assert (found is not None) == (len(voices) >= 2)
                elif kind == "disagreement":
                    # Nobody argues for care unless someone present was drawn to.
                    assert (found is not None) == careful
                else:
                    assert found is not None, (kind, voices)


def test_an_exchange_must_fit_the_shape_of_its_script(case: Case, spec: SessionSpec) -> None:
    pool = pool_of(case, spec)
    r, o, m = (pool.people[role] for role in ("requester", "owner", "manager"))
    talk = [(r, "just run it"), (o, "id check first"), (r, "its routine")]
    said = shaped(pool, "disagreement", "R+ O-", talk)
    assert said is not None
    assert [(s.role, s.stance) for s in said] == [
        ("requester", "go_ahead"),
        ("owner", "caution"),
        ("requester", "go_ahead"),
    ]
    assert shaped(pool, "disagreement", "R+ O-", talk[:2]) is None
    assert shaped(pool, "disagreement", "R+ O-", [*talk[:2], (m, "ok")]) is None
    assert shaped(pool, "disagreement", "R+ O-", [(r, "a"), (r, "b"), (r, "c")]) is None
    assert shaped(pool, "disagreement", "R+ O-", [*talk[:2], (r, "{fix} first")]) is None


def test_a_part_keeps_what_passes_review_and_asks_again_for_the_rest(
    case: Case, spec: SessionSpec
) -> None:
    pool = pool_of(case, spec)
    part = next(p for p in missing(pool) if p[0] == "shop_talk")
    kind, cast, briefs = part
    writer = Scripts(pool)
    kept, rejected = write_part(pool, kind, cast, briefs, writer)
    assert len(kept) == PER_CAST[kind] and rejected == 0 and len(writer.prompts) == 1
    assert {(e.topic, e.mood) for e in kept} == set(briefs)
    prompt = writer.prompts[0]
    assert briefs[0][0] in prompt and case.person("owner").description not in prompt
    assert pool.people["owner"] in review_prompt(pool, kind, cast, briefs, [e.lines for e in kept])
    bad = Scripts(pool, bad=True)
    kept, rejected = write_part(pool, kind, cast, briefs, bad)
    assert kept == [] and rejected == 3 * PER_CAST[kind] and len(bad.prompts) == 3
    assert "wants it to go ahead" in write_prompt(pool, "disagreement", "R+ O-", [("", "upbeat")])


def test_pools_are_written_resumed_and_read_by_their_cases(
    case: Case, spec: SessionSpec, tmp_path: Path
) -> None:
    pool = pool_of(case, spec)
    total = sum(len(casts) * PER_CAST[kind] for kind, casts in CASTS.items())
    logged: list[str] = []
    build_pools(tmp_path, [pool], Scripts(pool), workers=2, log=logged.append)
    assert len(pool.exchanges) == total and missing(pool) == []
    assert "0 still missing" in logged[0]
    again = load_pool(tmp_path, pool_of(case, spec))
    assert len(again.exchanges) == total and missing(again) == []
    again.exchanges = again.exchanges[5:]
    assert sum(len(briefs) for _, _, briefs in missing(again)) == 5

    folder = tmp_path / case.id
    folder.mkdir()
    save_bank(folder, Bank(lines={"owner": {"status": ["Around."]}}))
    bank = load_bank(folder)
    assert bank is not None and set(bank.exchanges) == set(CASTS)
    assert sum(len(threads) for threads in bank.exchanges.values()) == total
    other = tmp_path / "another-case"
    other.mkdir()
    save_bank(other, Bank(lines={}))
    unpooled = load_bank(other)
    assert unpooled is not None and unpooled.exchanges == {}
