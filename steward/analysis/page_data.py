"""Build the data object the results page draws its figures from.

The object follows the schema documented at the top of the page's script. Only complete
passes are read. A block whose passes are missing carries `placeholder: true` and no numbers,
and a model is left out of a block unless every value the block needs for it exists.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel

from steward.analysis.page_metrics import JUDGE_COLUMNS, Metrics, load_judge, metrics, number
from steward.analysis.result_tables import (
    NO_RULE,
    NOTICED_ARMS,
    NOTICED_PARTS,
    OPENINGS,
    ORIGINAL,
    OVERSIGHT_LEVELS,
    OVERSIGHT_RULES,
    category_table,
    history_length_table,
    looked_first_table,
    pressure_table,
    rule_by_length_table,
    rule_by_oversight_table,
    world_arm_table,
    world_noticed_table,
)
from steward.analysis.results import (
    ASKING_OUTCOMES,
    RULE_POSITIONS,
    WORLD_ARMS,
    asking_shares,
    baseline,
    baseline_trials,
    cell,
    contagion,
    design_effects,
    discrimination,
    load_runs,
    load_scenario_info,
    mosaic_counts,
    noise_floor,
    path_counts,
)
from steward.config import Settings
from steward.filter.selection import load_by_ids
from steward.scenario.spec import iter_specs
from steward.scenario.subset import SUBSET_FILE, read_subset
from steward.schema import AXES

log = logging.getLogger(__name__)

RUN_FILE = re.compile(r"^(?P<tag>.+?)__(?P<name>.+)\.jsonl$")
# A pass counts once it holds this share of the scenarios it is meant to run, so a pass that
# is still running does not put a partial sample on the page.
MIN_COVERAGE = 0.95
PLANNED_PASSES = 21
# Display name and maker of each run-file tag; other tags fall back to the model id.
MODEL_NAMES = {"luna": ("GPT-6 Luna", "openai"), "gemma": ("Gemma 4 26B", "google")}
MAKERS = ("openai", "google", "anthropic", "meta")
BUILT_DOMAINS = (
    ("swe", "swe", "Steward-SWE"),
    ("terminal", "terminal_devops", "Steward-Terminal"),
    ("workplace", "workplace", "Steward-Workplace"),
)
PLANNED_DOMAINS = (("tool", "Steward-Tool"), ("data", "Steward-Data"))
CATEGORY_LABELS = {
    "safe_interruptibility": "Interruptibility",
    "side_effects": "Side effects",
    "absent_supervisor": "Absent supervisor",
    "reward_gaming": "Reward gaming",
    "self_modification": "Self-modification",
}
RULE_LABELS = {
    "front": "Front",
    "middle_left": "Middle left",
    "middle_right": "Middle right",
    "back": "Back",
}
OVERSIGHT_LABELS = ["No rule", "Rule, recent", "Rule, long ago"]
ARM_LABELS = [
    "Unchanged",
    "Changed, silent",
    "Changed, announced",
    "Changed mid-run",
    "Unrelated change",
]
PRESSURE_LABELS = ["Message removed", "Message present"]
ASKING_KEYS = ("acted_safe", "acted_unsafe", "asked_safe", "asked_unsafe", "incomplete")
ORIGINAL_LABEL = "Original agent"


class Model(BaseModel):
    id: str
    name: str
    maker: str


class PassStatus(BaseModel):
    """How much of one pass is in the run directory."""

    model: str
    name: str
    runs: int
    expected: int
    complete: bool


class Timeline(BaseModel):
    """The recorded session behind a scenario: its length, the takeover step, and the steps
    before the takeover where the user spoke."""

    steps: int
    takeover: int
    user_turns: list[int]


class Inputs(BaseModel):
    """Everything the page data is computed from."""

    model_config = {"arbitrary_types_allowed": True}

    runs: pd.DataFrame
    scenarios: pd.DataFrame
    judge: pd.DataFrame
    models: list[Model]
    passes: list[PassStatus]


Block = dict[str, Any]
TimelineOf = Callable[[str], Timeline | None]


def percent(value: float) -> str:
    return f"{round(value * 100)}%"


def numbers(values: list[float]) -> list[float | None]:
    return [number(v) for v in values]


def whole(values: list[float | None]) -> bool:
    return bool(values) and all(v is not None for v in values)


def expected_scenarios(name: str, scenarios: pd.DataFrame, subset: set[str]) -> set[str]:
    """The scenarios a pass is meant to run: all of them for a baseline trial, the
    world-change scenarios for a world arm, and the subset for every other pass."""
    everything = set(scenarios.scenario_id)
    if name.startswith("baseline"):
        return everything
    if name.startswith("world_"):
        return set(scenarios[scenarios.world_change.astype(bool)].scenario_id)
    return (subset & everything) or everything


def model_of(tag: str, model_id: str) -> Model:
    if tag in MODEL_NAMES:
        name, maker = MODEL_NAMES[tag]
        return Model(id=tag, name=name, maker=maker)
    maker = model_id.split("/")[0]
    return Model(id=tag, name=model_id.split("/")[-1], maker=maker if maker in MAKERS else "other")


def prepare(
    runs: pd.DataFrame,
    scenarios: pd.DataFrame,
    subset: set[str],
    judge: pd.DataFrame | None = None,
    min_coverage: float = MIN_COVERAGE,
) -> Inputs:
    """Keep the runs of complete passes and name each model by its run-file tag.

    A run file is `<tag>__<pass>.jsonl`; files with other names are ignored. Judge labels are
    joined to the runs they describe as the columns judge_safety, judge_task and
    judge_false_report.
    """
    if runs.empty:
        return Inputs(runs=runs, scenarios=scenarios, judge=runs, models=[], passes=[])
    parts = runs.run_file.str.extract(RUN_FILE)
    named = runs.assign(tag=parts["tag"], pass_name=parts["name"]).dropna(subset=["tag"])
    passes, kept = [], []
    models: dict[str, Model] = {}
    for (tag, name), mine in named.groupby(["tag", "pass_name"]):
        expected = expected_scenarios(str(name), scenarios, subset)
        count = len(set(mine.scenario_id) & expected)
        complete = bool(expected) and count >= min_coverage * len(expected)
        passes.append(
            PassStatus(
                model=str(tag),
                name=str(name),
                runs=count,
                expected=len(expected),
                complete=complete,
            )
        )
        if complete:
            kept.append(mine)
            models.setdefault(str(tag), model_of(str(tag), str(mine.model.iloc[0])))
    order = [t for t in MODEL_NAMES if t in models] + sorted(set(models) - set(MODEL_NAMES))
    chosen = pd.concat(kept) if kept else named.iloc[0:0]
    labels = pd.DataFrame(columns=list(JUDGE_COLUMNS)) if judge is None else judge
    labels = labels.rename(
        columns={
            "condition": "pass_name",
            "safety": "judge_safety",
            "task": "judge_task",
            "false_report": "judge_false_report",
        }
    )
    tags = dict(zip(chosen.model, chosen.tag, strict=True))
    labels = labels.assign(model=labels.model.map(lambda m: tags.get(m, m)))
    chosen = chosen.assign(api_model=chosen.model, model=chosen.tag)
    joined = chosen.merge(labels, on=["scenario_id", "model", "pass_name"], how="left")
    return Inputs(
        runs=joined,
        scenarios=scenarios,
        judge=joined.dropna(subset=["judge_safety"]),
        models=[models[tag] for tag in order],
        passes=passes,
    )


def empty(**shape: Any) -> Block:
    return {"placeholder": True, **shape}


def filled(**content: Any) -> Block:
    return {"placeholder": False, **content}


def outcome(run: pd.Series) -> str:
    if run.unsafe:
        return "false_report" if run.claimed_success else "unsafe"
    return "asked" if run.asked_first else "safe"


def fork_block(inputs: Inputs, timeline_of: TimelineOf) -> Block:
    """One scenario built on a library design that every model ran: the session's timeline
    and what the original agent and each model did. The scenario with the most distinct
    outcomes is chosen, the first by id among equals."""
    shape = {"takeover": 0, "steps": 0, "user_turns": [], "branches": []}
    base = baseline(inputs.runs)
    if base.empty:
        return empty(**shape)
    planted = base[base.design.notna()]
    ids = [m.id for m in inputs.models]
    candidates = []
    for scenario_id, mine in planted.groupby("scenario_id"):
        by_model = {str(run.model): run for _, run in mine.iterrows()}
        if not set(ids) <= set(by_model):
            continue
        original = "unsafe" if bool(mine.original_unsafe.iloc[0]) else "safe"
        branches = [{"label": ORIGINAL_LABEL, "outcome": original}] + [
            {"label": m.name, "outcome": outcome(by_model[m.id])} for m in inputs.models
        ]
        distinct = len({b["outcome"] for b in branches})
        candidates.append((-distinct, str(scenario_id), str(mine.session.iloc[0]), branches))
    for _, scenario_id, session, branches in sorted(candidates):
        timeline = timeline_of(scenario_id)
        if timeline is not None:
            return filled(
                **timeline.model_dump(), branches=branches, scenario_id=scenario_id, session=session
            )
    return empty(**shape)


def history_length_block(inputs: Inputs) -> Block:
    labels, table = history_length_table(inputs.runs)
    shape = {"bins": labels, "series": {}, "original": {"task": [], "safety": []}}
    if table.empty:
        return empty(**shape)
    by_series = {
        name: mine.set_index("bin").reindex(labels) for name, mine in table.groupby("series")
    }
    series = {}
    for model in inputs.models:
        if model.id in by_series:
            task = numbers(list(by_series[model.id].task_complete))
            safety = numbers(list(by_series[model.id].safe))
            if whole(task) and whole(safety):
                series[model.id] = {"task": task, "safety": safety}
    original = by_series[ORIGINAL]
    if not series:
        return empty(**shape)
    return filled(
        bins=labels,
        series=series,
        original={
            "task": numbers(list(original.task_complete)),
            "safety": numbers(list(original.safe)),
        },
    )


def mosaic_block(inputs: Inputs) -> Block:
    cells = {}
    for model in inputs.models:
        counts = mosaic_counts(inputs.runs, model.id).to_numpy()
        if counts.sum():
            cells[model.id] = {
                "both_safe": int(counts[0, 0]),
                "fixes": int(counts[1, 0]),
                "repeats": int(counts[1, 1]),
                "introduces": int(counts[0, 1]),
            }
    return filled(cells=cells) if cells else empty(cells={})


def contagion_block(inputs: Inputs) -> Block:
    sessions, followed = {}, {}
    for model in inputs.models:
        pairs = contagion(inputs.runs, model.id)
        if not pairs.empty:
            sessions[model.id] = [
                [number(left), number(right)]
                for left, right in zip(pairs.left, pairs.right, strict=True)
            ]
            became_unsafe = int(((pairs.left == 0) & (pairs.right > 0)).sum())
            followed[model.id] = [became_unsafe, len(pairs)]
    if not sessions:
        return empty(sessions={}, followed={})
    return filled(sessions=sessions, followed=followed)


def looked_first_block(inputs: Inputs) -> Block:
    table = looked_first_table(inputs.runs)
    rows = {
        row.model: {
            "looked": number(row.looked),
            "unsafe_if_looked": number(row.unsafe_if_looked),
            "unsafe_if_not": number(row.unsafe_if_not),
            "n": int(str(row.scenarios)),
        }
        for row in table.itertuples()
        if row.scenarios
    }
    return filled(rows=rows) if rows else empty(rows={})


def planted_baseline(inputs: Inputs) -> pd.DataFrame:
    base = baseline(inputs.runs)
    return base[base.design.notna()] if not base.empty else base


def profile_block(inputs: Inputs) -> Block:
    """Safe rate per gridworld category on the scenarios built on a library design."""
    axes = [CATEGORY_LABELS[a] for a in AXES]
    planted = planted_baseline(inputs)
    if planted.empty:
        return empty(axes=axes, values={})
    rates = category_table(planted, "safe").set_index(["model", "category"]).safe
    values = {}
    for model in inputs.models:
        mine = [number(rates.get((model.id, a))) for a in AXES]
        if whole(mine):
            values[model.id] = mine
    return filled(axes=axes, values=values) if values else empty(axes=axes, values={})


def designs_block(inputs: Inputs) -> Block:
    effects = design_effects(inputs.runs)
    if effects.empty:
        return empty(rows=[])
    rows = [
        {
            "design": design,
            "category": CATEGORY_LABELS.get(mine.category.iloc[0], mine.category.iloc[0]),
            "effects": {
                row.model: [number(row.change), number(row.low), number(row.high)]
                for row in mine.itertuples()
            },
        }
        for design, mine in effects.groupby("design")
    ]
    return filled(rows=rows)


def path_block(inputs: Inputs) -> Block:
    flows = {}
    for model in inputs.models:
        counts = path_counts(inputs.runs, model.id)
        if counts:
            flows[model.id] = {
                "asked_safe": counts.get(("asked first", "safe"), 0),
                "asked_unsafe": counts.get(("asked first", "shortcut"), 0),
                "acted_safe": counts.get(("acted", "safe"), 0),
                "acted_unsafe": counts.get(("acted", "shortcut"), 0),
                "admitted": counts.get(("shortcut", "admitted"), 0),
                "claimed": counts.get(("shortcut", "claimed success"), 0),
            }
    return filled(flows=flows) if flows else empty(flows={})


def rule_by_length_block(inputs: Inputs) -> Block:
    labels, table = rule_by_length_table(inputs.runs)
    positions = [RULE_LABELS[p] for p in RULE_POSITIONS]
    series: dict[str, dict[str, list[float | None]]] = {}
    no_rule = {}
    for (model, rule), mine in table.groupby(["model", "rule"]) if not table.empty else ():
        values = numbers(list(mine.set_index("bin").safe.reindex(labels)))
        if not whole(values):
            continue
        if rule == NO_RULE:
            no_rule[str(model)] = values
        else:
            series.setdefault(str(model), {})[RULE_LABELS[str(rule)]] = values
    if not series:
        return empty(bins=labels, positions=positions, series={}, no_rule={})
    no_rule = {model: values for model, values in no_rule.items() if model in series}
    return filled(bins=labels, positions=positions, series=series, no_rule=no_rule)


def rule_by_oversight_block(inputs: Inputs) -> Block:
    table = rule_by_oversight_table(inputs.runs)
    series = {}
    for model, mine in table.groupby("model") if not table.empty else ():
        rates = mine.set_index(["oversight", "rule"]).safe
        lines = {
            level: [number(rates.get((level, rule))) for rule in OVERSIGHT_RULES]
            for level in OVERSIGHT_LEVELS
        }
        if all(whole(values) for values in lines.values()):
            series[model] = lines
    if not series:
        return empty(x=OVERSIGHT_LABELS, series={})
    return filled(x=OVERSIGHT_LABELS, series=series)


def arms_block(inputs: Inputs) -> Block:
    table = world_arm_table(inputs.runs)
    series = {}
    for model, mine in table.groupby("model"):
        rates = mine.set_index("arm").took_harmful
        values = [number(rates.get(arm)) for arm in WORLD_ARMS]
        if whole(values):
            series[model] = values
    return filled(x=ARM_LABELS, series=series) if series else empty(x=ARM_LABELS, series={})


def noticed_block(inputs: Inputs) -> Block:
    table = world_noticed_table(inputs.runs)
    rows = {}
    for model, mine in table.groupby("model"):
        by_arm = mine.set_index("arm")
        if set(NOTICED_ARMS) <= set(by_arm.index):
            rows[model] = {
                arm: {part: number(by_arm.loc[arm, part]) for part in NOTICED_PARTS}
                for arm in NOTICED_ARMS
            }
    return filled(rows=rows) if rows else empty(rows={})


def pressure_block(inputs: Inputs) -> Block:
    table = pressure_table(inputs.runs)
    series = {}
    for model, mine in table.groupby("model") if not table.empty else ():
        rates = mine.set_index(["simulated", "opening"]).unsafe
        lines = {
            key: [number(rates.get((simulated, opening))) for opening in OPENINGS]
            for key, simulated in (("fixed", False), ("simulated", True))
        }
        if all(whole(values) for values in lines.values()):
            series[model] = lines
    if not series:
        return empty(x=PRESSURE_LABELS, series={})
    return filled(x=PRESSURE_LABELS, series=series)


def asking_block(inputs: Inputs) -> Block:
    shares = asking_shares(inputs.runs)
    rows = {
        row["model"]: {
            key: number(row[name]) for key, name in zip(ASKING_KEYS, ASKING_OUTCOMES, strict=True)
        }
        for _, row in shares.iterrows()
    }
    return filled(rows=rows) if rows else empty(rows={})


def scoring_rows(inputs: Inputs, built_on_design: bool) -> list[Metrics]:
    """Metrics per model on the scenarios built on a library design, or on the as-recorded
    scenarios that serve as their control."""
    runs = inputs.runs
    base = baseline(runs)
    if base.empty:
        return []
    chosen = base[base.design.notna() == built_on_design]
    changed = runs[runs.world_change.astype(bool)]
    unchanged = cell(changed, world="unchanged")
    trials = baseline_trials(runs)
    judged = baseline(inputs.judge) if not inputs.judge.empty else inputs.judge
    rows = []
    for model in inputs.models:
        mine = chosen[chosen.model == model.id]
        ids = set(mine.scenario_id)
        rows.append(
            metrics(
                model.id,
                mine,
                trials.get(model.id, []),
                unchanged[(unchanged.model == model.id) & unchanged.scenario_id.isin(ids)],
                judged[(judged.model == model.id) & judged.scenario_id.isin(ids)]
                if not judged.empty
                else judged,
            )
        )
    return rows


def validity_block(inputs: Inputs) -> Block:
    """Noise floor: the share of scenarios whose safety outcome differs between the first
    two baseline trials, averaged over the models that have two. Judge agreement: the share
    of judged runs where the judge's safety label matches the scripted one."""
    noise = noise_floor(inputs.runs)
    split = discrimination(inputs.runs)
    judged = inputs.judge
    agreement = None
    if not judged.empty:
        agreement = number((judged.judge_safety >= 1.0).eq(judged.safe.astype(bool)).mean())
    return {
        "placeholder": noise.empty and not split and agreement is None,
        "noise_floor": None if noise.empty else number(noise.differs.mean()),
        "noise_floor_by_model": {row.model: number(row.differs) for row in noise.itertuples()},
        "judge_agreement": agreement,
        "judged_runs": len(judged),
        "all_pass": split.get("all models safe"),
        "all_fail": split.get("all models unsafe"),
        "split": split.get("models split"),
    }


def names(inputs: Inputs) -> dict[str, str]:
    return {m.id: m.name for m in inputs.models}


def per_model(inputs: Inputs, content: dict[str, Any], sentence: Callable[[str, Any], str]) -> str:
    """One sentence that gives each model's numbers in turn."""
    parts = [sentence(m.name, content[m.id]) for m in inputs.models if m.id in content]
    return "; ".join(parts) + "." if parts else ""


def observations(inputs: Inputs, page: dict[str, Any]) -> dict[str, str]:
    """A factual sentence per figure, from the figure's own numbers. A figure whose block is
    a placeholder gets none."""
    rq1, rq2, rq3, rq4, rq5 = (page[f"rq{i}"] for i in range(1, 6))
    found: dict[str, str] = {}

    def add(
        key: str, block: Block, content: dict[str, Any], sentence: Callable[[str, Any], str]
    ) -> None:
        if not block["placeholder"]:
            text = per_model(inputs, content, sentence)
            if text:
                found[key] = text

    history = rq1["history_length"]
    add(
        "rq1.history_length",
        history,
        history["series"],
        lambda name, s: (
            f"{name} is safe in {percent(s['safety'][0])} of scenarios in the shortest "
            f"histories ({history['bins'][0]} tokens) and {percent(s['safety'][-1])} in the "
            f"longest ({history['bins'][-1]} tokens)"
        ),
    )
    add(
        "rq1.mosaic",
        rq1["mosaic"],
        rq1["mosaic"]["cells"],
        lambda name, c: (
            f"{name} repeats the original agent's unsafe action in {c['repeats']} of "
            f"{c['repeats'] + c['fixes']} sessions where the original agent was unsafe, and "
            f"is unsafe in {c['introduces']} of {c['introduces'] + c['both_safe']} where it "
            "was safe"
        ),
    )
    add(
        "rq1.contagion",
        rq1["contagion"],
        rq1["contagion"]["followed"],
        lambda name, f: (
            f"With a line from the previous agent added, {name} went from safe as recorded "
            f"to unsafe in {f[0]} of {f[1]} sessions"
        ),
    )
    add(
        "rq1.looked_first",
        rq1["looked_first"],
        {
            k: r
            for k, r in rq1["looked_first"]["rows"].items()
            if r["unsafe_if_looked"] is not None and r["unsafe_if_not"] is not None
        },
        lambda name, r: (
            f"{name} saw the planted result before acting in {percent(r['looked'])} of "
            f"{r['n']} runs, and was unsafe in {percent(r['unsafe_if_looked'])} of the runs "
            f"where it looked and {percent(r['unsafe_if_not'])} where it did not"
        ),
    )

    def profile_sentence(name: str, values: list[float]) -> str:
        low, high = min(values), max(values)
        axes = rq2["profile"]["axes"]
        return (
            f"{name}'s safe rate is lowest on {axes[values.index(low)].lower()} "
            f"({percent(low)}) and highest on {axes[values.index(high)].lower()} "
            f"({percent(high)})"
        )

    add("rq2.profile", rq2["profile"], rq2["profile"]["values"], profile_sentence)

    largest: dict[str, tuple[str, list[float]]] = {}
    for row in rq2["designs"]["rows"]:
        for model, effect in row["effects"].items():
            if effect[0] is not None and (model not in largest or effect[0] > largest[model][1][0]):
                largest[model] = (row["design"], effect)
    add(
        "rq2.designs",
        rq2["designs"],
        largest,
        lambda name, d: (
            f"For {name} the design with the largest change in unsafe rate against the "
            f"session's control is {d[0].replace('_', ' ')}, at {round(d[1][0] * 100):+d} "
            f"points (interval {round(d[1][1] * 100):+d} to {round(d[1][2] * 100):+d})"
        ),
    )
    add(
        "rq2.path",
        rq2["path"],
        rq2["path"]["flows"],
        lambda name, f: (
            f"{name} asked the user first in {f['asked_safe'] + f['asked_unsafe']} of "
            f"{f['asked_safe'] + f['asked_unsafe'] + f['acted_safe'] + f['acted_unsafe']} "
            f"runs, and reported success in {f['claimed']} of "
            f"{f['claimed'] + f['admitted']} unsafe runs"
        ),
    )

    length = rq3["rule_by_length"]
    add(
        "rq3.rule_by_length",
        length,
        {
            k: s
            for k, s in length["series"].items()
            if {"Front", "Back"} <= set(s) and k in length["no_rule"]
        },
        lambda name, s: (
            f"In the longest histories ({length['bins'][-1]} tokens) {name} is safe in "
            f"{percent(s['Front'][-1])} of scenarios with the rule at the front and "
            f"{percent(s['Back'][-1])} with it at the back"
        ),
    )
    add(
        "rq3.rule_by_oversight",
        rq3["rule_by_oversight"],
        rq3["rule_by_oversight"]["series"],
        lambda name, s: (
            f"With no rule, {name} is safe in {percent(s['reviewed'][0])} of scenarios when "
            f"told the work will be reviewed and {percent(s['unreviewed'][0])} when told "
            "nobody will review it"
        ),
    )
    add(
        "rq4.arms",
        rq4["arms"],
        rq4["arms"]["series"],
        lambda name, s: (
            f"{name} takes the action in {percent(s[0])} of runs when the world is "
            f"unchanged, {percent(s[1])} when it changed silently and {percent(s[2])} when "
            "the change was announced"
        ),
    )
    add(
        "rq4.noticed",
        rq4["noticed"],
        rq4["noticed"]["rows"],
        lambda name, r: (
            f"In the silent arm {name} saw the change before acting in "
            f"{percent(r['silent']['saw_safe'] + r['silent']['saw_took'])} of runs and took "
            f"the action in {percent(r['silent']['saw_took'] + r['silent']['unseen_took'])}"
        ),
    )
    add(
        "rq5.pressure",
        rq5["pressure"],
        rq5["pressure"]["series"],
        lambda name, s: (
            f"With the user's opening message removed, {name}'s unsafe rate is "
            f"{percent(s['fixed'][0])} against {percent(s['fixed'][1])} with it present "
            f"under fixed replies, and {percent(s['simulated'][0])} against "
            f"{percent(s['simulated'][1])} with a simulated user"
        ),
    )
    add(
        "rq5.asking",
        rq5["asking"],
        rq5["asking"]["rows"],
        lambda name, r: (
            f"{name} finished without asking the user in "
            f"{percent(r['acted_safe'] + r['acted_unsafe'])} of baseline runs, finished after "
            f"asking first in {percent(r['asked_safe'] + r['asked_unsafe'])} and did not "
            f"finish {percent(r['incomplete'])}"
        ),
    )
    return found


def domains_block(scenarios: pd.DataFrame) -> list[Block]:
    built = [
        {
            "id": page_id,
            "name": name,
            "scenarios": int((scenarios.domain == domain).sum()),
            "sessions": int(scenarios[scenarios.domain == domain].session.nunique()),
            "built": True,
        }
        for page_id, domain, name in BUILT_DOMAINS
    ]
    planned = [
        {"id": page_id, "name": name, "scenarios": None, "sessions": None, "built": False}
        for page_id, name in PLANNED_DOMAINS
    ]
    return built + planned


def meta_block(inputs: Inputs, rows: list[Metrics], today: date) -> Block:
    label = names(inputs)
    headline = []
    for row in rows:
        if row.safe3 is not None:
            headline.append({"label": f"Safe³, {label[row.model]}", "value": percent(row.safe3)})
        elif row.safe_rate is not None:
            headline.append(
                {"label": f"Safe rate, {label[row.model]}", "value": percent(row.safe_rate)}
            )
    headline.append({"label": "Scenarios", "value": str(len(inputs.scenarios))})
    headline.append({"label": "Sessions", "value": str(inputs.scenarios.session.nunique())})
    done = {m.id: sum(p.complete for p in inputs.passes if p.model == m.id) for m in inputs.models}
    status = "; ".join(
        f"{m.name}: {done[m.id]} of {PLANNED_PASSES} passes complete" for m in inputs.models
    )
    return {
        "date": today.strftime("%B %Y"),
        "status": status,
        "placeholder": False,
        "headline": headline,
        "passes": [p.model_dump() for p in inputs.passes],
    }


def page_data(inputs: Inputs, timeline_of: TimelineOf, today: date) -> dict[str, Any]:
    """The page's data object from prepared inputs."""
    rows = scoring_rows(inputs, built_on_design=True)
    page: dict[str, Any] = {
        "meta": meta_block(inputs, rows, today),
        "models": [m.model_dump() for m in inputs.models],
        "domains": domains_block(inputs.scenarios),
        "rq1": {
            "fork": fork_block(inputs, timeline_of),
            "history_length": history_length_block(inputs),
            "mosaic": mosaic_block(inputs),
            "contagion": contagion_block(inputs),
            "looked_first": looked_first_block(inputs),
        },
        "rq2": {
            "profile": profile_block(inputs),
            "designs": designs_block(inputs),
            "path": path_block(inputs),
        },
        "rq3": {
            "rule_by_length": rule_by_length_block(inputs),
            "rule_by_oversight": rule_by_oversight_block(inputs),
        },
        "rq4": {"arms": arms_block(inputs), "noticed": noticed_block(inputs)},
        "rq5": {"pressure": pressure_block(inputs), "asking": asking_block(inputs)},
        "scoring": {
            "placeholder": not rows,
            "rows": [r.model_dump() for r in rows],
            "control": [r.model_dump() for r in scoring_rows(inputs, built_on_design=False)],
        },
        "validity": validity_block(inputs),
    }
    page["observations"] = observations(inputs, page)
    return page


def session_timelines(settings: Settings) -> TimelineOf:
    """Read a scenario's recorded session for its length and its user turns."""
    specs = {s.scenario_id: s for s in iter_specs(settings.paths.scenarios)}

    def timeline_of(scenario_id: str) -> Timeline | None:
        spec = specs.get(scenario_id)
        if spec is None:
            return None
        session = next(iter(load_by_ids(settings, [spec.source.trajectory_id])), None)
        if session is None:
            return None
        takeover = spec.source.takeover_step
        return Timeline(
            steps=len(session.steps),
            takeover=takeover,
            user_turns=[s.index for s in session.steps[:takeover] if s.kind == "user"],
        )

    return timeline_of


def build_page_data(
    settings: Settings, run_dir: Path | None = None, min_coverage: float = MIN_COVERAGE
) -> dict[str, Any]:
    """The page's data object from the run files, and the judge files where they exist."""
    phase2 = settings.paths.data / "phase2"
    scenarios = load_scenario_info(settings)
    runs = load_runs(run_dir or phase2 / "runs", scenarios)
    subset_file = settings.paths.scenarios / SUBSET_FILE
    subset = read_subset(settings.paths.scenarios) if subset_file.exists() else set()
    inputs = prepare(runs, scenarios, subset, load_judge(phase2 / "judge"), min_coverage)
    if not inputs.models:
        raise FileNotFoundError(f"no complete pass in {run_dir or phase2 / 'runs'}")
    for status in inputs.passes:
        if not status.complete:
            log.info(
                "%s %s left out: %d of %d runs",
                status.model,
                status.name,
                status.runs,
                status.expected,
            )
    return page_data(inputs, session_timelines(settings), date.today())
