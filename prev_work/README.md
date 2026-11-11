# Previous work on agent benchmark environments

Research done on 2026-11-11 into how other agent benchmarks build, reset, fork and verify their
execution environments, to guide the Steward Bench environments.

The report with the comparison table and recommendations is
`reports/Agent benchmark environment construction.md`.

The notes behind it are in `research_notes/Agent benchmark environment construction/`:

| File | Covers |
|---|---|
| `neubig_group_environments.md` | OpenHands, TheAgentCompany, WebArena, VisualWebArena, the SWE-bench harness |
| `computer_use_environments.md` | OSWorld, WindowsAgentArena, AndroidWorld, snapshot and fork tooling |
| `terminal_environments.md` | Terminal-Bench 2.0, Harbor and its container backends |
| `tool_use_environments.md` | Toolathlon, Toolathlon-GYM, AppWorld, tau-bench, MCP benchmarks |
| `forking_and_takeover_methods.md` | The Replay Gap, The Handoff Tax, SWE-Together, replay fidelity, checkpoint and restore |
| `safety_benchmark_environments.md` | OpenAgentSafety, ROGUE, Saber, AgentDojo, SHADE-Arena, ToolEmu and others |

Most sources were read through a summarising fetch tool, so check a figure against the paper
before quoting it. Each notes file lists what was not verified.
