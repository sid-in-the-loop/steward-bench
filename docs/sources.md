# Adding a source

A source is one module in steward/sources/ with one class that subclasses `Source`
(steward/sources/base.py), plus one line in steward/sources/registry.py and a test with a small
synthetic fixture.

## The loader

```python
class MySource(Source):
    info = SourceInfo(
        name="my_source",            # directory under data/raw and the trajectory_id prefix
        domain="swe",                # default domain
        real_user=False,
        location="hf:owner/dataset",
        license="cc-by-4.0",
        notes="",                    # anything SOURCES_STATUS.md should say
    )

    def download(self, settings: Settings) -> None: ...
    def load(self, settings: Settings) -> Iterator[Trajectory]: ...
```

`download` fetches into `settings.paths.raw / info.name`. It must be idempotent and resumable;
`download_hf` in steward/sources/hf.py handles Hugging Face datasets. If the data cannot be
fetched, raise `Unavailable` with the reason, and add an `UnavailableSource` entry to the registry
saying what would unblock it.

`load` yields `Trajectory` objects (steward/schema.py) in a deterministic order. The rules come
from brief section 5:

- trajectory_id is `{source}:{native_id}`, where native_id comes from the data: a session id, a
  task id, or a run and task id joined with `/`. Never invent identifiers.
- Steps are user, agent_text, action, result or system. Use `StepBuilder` from
  steward/sources/transcripts.py so indices are contiguous from 0. Drop thinking and reasoning
  blocks. Keep user messages, actions and results in full. Put the tool name in `tool`, the
  arguments in `tool_input`, and a readable command in `content` (`action_content` does this).
- Set `is_interruption` on real user stops.
- Fill history_author and harness whenever the source records them.
- Set task_env for a known task environment (for example `swe-bench-verified:<instance_id>`), or
  repo and base_commit for a public repository. Stage A check A3 needs one of the two.
- Mixed real-user sources take their domain from `assign_domain(steps)`. When it returns None,
  use the source default and set `domain_decided_by_rule=False`; Stage B then decides.
- Keep small source-specific fields in `meta` verbatim. Never keep API keys, tokens or other
  secrets.

## Reusable parsers

| Format | Parser |
|---|---|
| Claude Code JSONL | `parse_claude_code` in steward/sources/transcripts.py |
| Codex CLI JSONL | `parse_codex` |
| Pi session JSONL | `parse_pi` |
| Copilot CLI events | `parse_copilot` |
| Any of the above, detected | `parse_records(read_jsonl_records(path))` |
| OpenAI-style message lists (OpenHands, SWE-agent, HAL) | `parse_openai_messages` in steward/sources/chat_messages.py |
| ChatML with `<tool_use>` and `<tool_result>` tags | `parse_chatml_tool_markup` |
| Session exports with a `messages` list (DataClaw, SWE-Together) | `parse_session_messages`, `parse_markup_messages` in steward/sources/session_export_formats.py |
| OpenHands event streams (TheAgentCompany, OpenAgentSafety) | `parse_openhands_events` in steward/sources/openhands_events.py |
| Harbor ATIF trajectory.json (Terminal-Bench) | `parse_atif` in steward/sources/harbor_atif.py |
| SWE-bench submission formats (SWE-agent, mini-swe-agent, OpenHands, others) | `detect_format` and one parser per format in steward/sources/swe_bench_formats.py |
| HAL agent calls, tool-calling and code-agent scaffolds | `parse_hal_messages` in steward/sources/hal_messages.py |

Helpers: `read_json` and `json_files_by_stem` (steward/sources/json_files.py) read `.json` or
`.json.gz`; `sparse_checkout` (steward/sources/git_sparse.py) fetches pinned paths of a GitHub
repository; `card_license` (steward/sources/hf_card.py) reads a dataset card's license;
`infer_for_source` (steward/sources/commit_inference.py, command `steward infer-commits`) fills
missing base commits for sessions on public repositories.

## Register, test, ingest

1. Add an instance to `LOADERS` in steward/sources/registry.py.
2. Add tests/sources/test_<name>.py with a hand-written fixture under tests/fixtures/<name>/.
   Never copy real data into tests: some sources are gated.
3. Run `uv run steward download <name>`, then `uv run steward ingest <name>`, then
   `uv run steward validate normalized data/normalized/<name>`.
4. Run `uv run steward stage-a`, then `uv run steward sources-status`, and update the docs in the
   same commit.
