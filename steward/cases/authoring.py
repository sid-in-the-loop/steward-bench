"""Start a new case from a working one, and report what a case provides at each level."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from pydantic import BaseModel

from steward.cases.bench import load_recordings
from steward.cases.case import Case, Domain, case_identifier, load_case
from steward.cases.levels import BANK_FILE, Content, load_bank
from steward.cases.sessions import SESSION_FILE, SessionSpec

# What a new case copies from the case it starts from. The message bank and the recorded
# owners are written for the new case once its own lines and goal exist.
COPIED = ("case.yaml", "setup.sh", SESSION_FILE, "item.sh", "during.sh", "levels.sh")
# The kinds of unprompted message a bank needs for Level III, by user.
NEEDED: dict[str, tuple[Content, ...]] = {
    "owner": ("signal", "status", "noise"),
    "bystander": ("approval", "noise"),
    "manager": ("pressure", "noise"),
}


class Provided(BaseModel):
    """One thing a case provides or lacks, in words."""

    what: str
    ok: bool
    detail: str


def new_case(root: Path, start: Case, title: str) -> Path:
    """Copy a working case into a folder named for the new title, with its identifier and
    title set. Everything else is the starting case's and is to be rewritten."""
    identifier = case_identifier(start.domain, title)
    target = root / identifier
    if target.exists():
        raise FileExistsError(f"{target} exists: a case with this title is already there")
    target.mkdir()
    for name in COPIED:
        source = root / start.id / name
        if source.exists():
            shutil.copy(source, target / name)
    path = target / "case.yaml"
    text = path.read_text()
    text = re.sub(r"(?m)^id: .*$", f"id: {identifier}", text, count=1)
    text = re.sub(r"(?m)^title: .*$", f"title: {title}", text, count=1)
    path.write_text(text)
    load_case(target)
    return target


def first_of(cases: list[Case], domain: Domain) -> Case | None:
    return next((case for case in sorted(cases, key=lambda c: c.id) if case.domain == domain), None)


def provided(directory: Path, case: Case, spec: SessionSpec | None) -> list[Provided]:
    """What the case has for each level, read from its folder without running anything."""
    found = []
    expected = case_identifier(case.domain, case.title)
    named = case.id == expected and directory.name == expected
    found.append(
        Provided(
            what="Identifier",
            ok=named,
            detail=case.id if named else f"is {case.id}, the title gives {expected}",
        )
    )
    if spec is None:
        found.append(Provided(what="Levels I and II", ok=False, detail=f"no {SESSION_FILE}"))
        return found
    found.append(Provided(what="Levels I and II", ok=True, detail="session form present"))
    bank = load_bank(directory)
    thin = [
        f"{role} {content}"
        for role, kinds in NEEDED.items()
        for content in kinds
        if bank is None or len(bank.lines.get(role, {}).get(content, [])) < 2  # type: ignore[call-overload]
    ]
    if bank is None:
        found.append(Provided(what="Level III", ok=False, detail=f"no {BANK_FILE}; run bank-build"))
    else:
        detail = "bank complete" if not thin else f"fewer than two wordings for: {', '.join(thin)}"
        found.append(Provided(what="Level III", ok=not thin, detail=detail))
    kinds = ", ".join(spec.second.kinds) if spec.second else ""
    found.append(
        Provided(
            what="Level IV, second change",
            ok=bool(spec.second),
            detail=kinds or "no `second` in the session file",
        )
    )
    found.append(
        Provided(
            what="Level IV, carried change",
            ok=spec.carries,
            detail="item script honours CARRIED" if spec.carries else "`carries` is not set",
        )
    )
    recordings = len(load_recordings(directory))
    if spec.generated is None:
        detail = "no `generated` in the session file"
    else:
        detail = f"{recordings} recorded owner{'s' if recordings != 1 else ''}"
    found.append(
        Provided(what="Level V", ok=spec.generated is not None and recordings > 0, detail=detail)
    )
    return found
