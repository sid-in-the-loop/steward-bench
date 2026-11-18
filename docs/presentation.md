# The presentation

`presentation/` is one scrolling web page that presents Steward Bench to people who have not
seen it: the motivation, the design, one session played out, the findings and the plan for
scaling. It is published as a claude.ai artifact
(https://claude.ai/artifact/KUZqLWfvDitYU2mvwv5HQw) and links to the session viewer
(https://claude.ai/artifact/NCzumkvoK9xFabU5T9joP8).

The page calls a loop a shift: one handover of routine work. A changed shift is a drift loop
and a routine shift is a control loop.

## Files

| Path | What it is |
|---|---|
| `index.html` | The page: the sections, their text, and a slot for each figure |
| `scene.html` | The ML operations session, played as a scene with its lab chat |
| `assets/style.css` | Type, colours and layout |
| `assets/engine.js` | The figure engine, the figure frame and the presenter view |
| `assets/charts.js` | Model names and colours, scales, axes |
| `assets/intro.js` | Sections 1 to 4: the handover, the shift, routine and changed shifts, the settings and the three levels of visibility |
| `assets/findings.js` | The findings figures and the trade-off figure the scaling section reuses |
| `assets/honesty.js` | What the agent did against what it told people, in three stored sessions |
| `assets/scaling.js` | Where harm concentrates, case families, user profiles, the domains, frontier models |
| `assets/scene.js` | Fits the scene to the page and opens it full screen |
| `numbers/deck.js` | Every number the figures show, written by `steward deck-data` |

## Rebuilding

```bash
uv run steward bench-digest --out data/cases/analysis/digests   # the per-loop measures table
uv run steward bench-check-gap                                  # stale-check rates
uv run steward deck-data                                        # presentation/numbers/deck.js
```

The page needs no build step: open `presentation/index.html` in a browser, or publish the
folder's files as one artifact with the same paths. `steward/cases/deck_data.py` documents
which loops each number counts.

## Sections

| # | Section | Figures |
|---|---|---|
| 1 | Agents now inherit work | A handover: the next agent runs the routine from the notes over a colleague's change |
| 2 | What is a shift? | One shift (request, notes, environment, people, report), then a session of sixteen |
| 3 | Routine and changed shifts | The same request on both kinds of shift, and the three measures |
| 4 | What a session varies | The eight settings; Announced, Visible and Hidden in the shards case |
| 5 | A real MLOps session | The shards case, played from one stored session of gpt-6-luna |
| 6 | Findings | Stale looks; visibility; notes and the owner; talk around the agent; pressure; the trade-off between harm, false alarms and finishing; reports after harm |
| 7 | Scaling | Harm per case; six case families at three levels; user profiles; the domains; where frontier models will land |
| 8 | Transcripts | A link to the session viewer |

## Design rules

- Inter for text, Newsreader semibold for headings and for what people say, IBM Plex Mono
  for commands, on white. Model colours are fixed across figures; red is kept for harm.
- A figure is a list of short steps. The picture at any moment is the figure's initial state
  with every started step drawn at its progress, so play, step and seek are exact.
- Every figure, and the scene, opens paused on its first step and plays only when Play is
  pressed. Presenter view hides the captions and step narration.
- No session or case identifiers on screen; the honesty figure keeps its sources in hover
  text. Labels are checked for overlap in a headless browser before publishing.

## What the page does not claim

- The Hidden level groups nine cases by the author's reading of whether the routine's tool can
  show the change (`HIDDEN` in `deck_data.py`). The classification has not been reviewed, and
  each case exists in only one level, so Visible against Hidden compares different cases.
- The Visible row of the shards illustration is drawn for the page; the case has no such
  variant.
- Harm and honesty rest on the scorer's labels, which readers disagreed with in 92 places
  (data/cases/analysis/opencode/codebook_draft.md); they are provisional until checked.
- The honesty examples are three sessions chosen by hand from the 200 that were read; they show
  that such reports occur, not how often.
- The scene's timeline and Kofi's reactions are dramatised; the commands, messages and report
  come from the session.
- The user profiles in the scaling section are a proposal, not part of the benchmark.
- Routes to harm are left out until each case has a pattern for commands that can reveal its
  change.
