# Forking, handoff, takeover and replay: how an intermediate agent state is obtained and continued

Research date: 2026-11-11. All sources were fetched on that date.

Method note on verification. Except for "Replay Fidelity in LLM Agent Evaluation" (whose PDF text I extracted and read directly), every paper and repository page below was read through a fetch tool that summarises the page with a small model. Numbers are therefore reported as returned by that tool from the arXiv HTML full text, not checked by me character by character. Where the tool reported that part of a page was truncated, or where a detail was absent, this is flagged under Gaps. The ACM Digital Library page for "Parallel Environments for Agents" returned HTTP 403; that entry rests on the conference demo page only.

## 1. For each paper: how is the intermediate state obtained, and how is the rebuilt state validated?

### Takeaway
Four mechanisms are in use: keep the same live container across the switch (Handoff Tax, SWE-Touch, AgentBaton online), start a fresh container and re-execute the recorded prefix actions (Replay Gap, AgentBaton checkpoint study, Replay Fidelity), restore an OS-level snapshot (Crab, DeltaBox, TClone, ParallelEnv), or rebuild from a pinned commit and start from turn zero with a simulated user (SWE-Together). Only the re-execution papers report an explicit fidelity audit of the rebuilt state; the live-container papers need none, and SWE-Together does not attempt mid-session state at all.

### Cited Findings

Summary table (details and citations follow per paper).

| Work | Date | State mechanism | Validation of state |
|---|---|---|---|
| The Replay Gap | 8 Aug 2026 | Fresh container, re-execute recorded pre-fork actions, seed message history with recorded prefix | Return-code match 99.99% of 11,702; 707/708 branches reconstruct exactly; same-model control forks |
| The Handoff Tax | 25 Aug 2026 | Same agent loop and Docker environment kept alive across the model switch | Per-call tagging, cost reconciliation audit, official SWE-bench evaluator; no state rebuild so no fidelity check |
| AgentBaton | 25 Oct 2026 | Checkpoint study: replay recorded actions in a task container. Online: receiver "inherits the current workspace" | None reported |
| SWE-Touch | 3 Aug 2026 | Live repository; counter-edit applied in place at a trigger step | Offline three-way test check on each edit; runtime record of whether the edit applied |
| SWE-Together | 29 Jun 2026 | Clone repo at pinned commit, prebuilt image, start from the first user message; LLM user simulator replays intent | Deterministic verifiers, reconstructed reference patch scored (about 78% pass) |
| Replay Fidelity (AppWorld) | 8 Nov 2026 | Fresh AppWorld world per attempt, archived code re-executed unchanged | Printed output, 12 pre-oracle DB delta files, native verdict and test counts, in two fresh processes |
| SWE-chat | 22 Apr 2026 (v2 1 Oct 2026) | Observational dataset; checkpoints linked to commits | Not an environment benchmark |
| ParallelEnv | May 2026 demo | State graph over immutable environment snapshots; Docker and VM backends | Not described on the page I could reach |
| Crab | 30 Apr 2026 | runc + CRIU for processes, OpenZFS snapshots for files, per-turn eBPF classification | Recovery correctness 100% vs 8% chat-only |
| DeltaBox | 21 May 2026 (v2 8 Jun) | Overlayfs layer switch plus CRIU/fork-from-template inside Firecracker microVMs | Latency benchmarks |
| TClone | 17 May 2026 | Sibling containers, kernel-module CoW memory, CoW filesystem | Latency and memory benchmarks |
| AgentRewind | 14 Aug 2026 | Bare git repo outside the workspace commits the work tree per LLM node; context replayed from recorded log | Ablation of environment rewind |

The Replay Gap (Gonuguntla, arXiv 2608.08239, submitted 8 Aug 2026)
- Each fork (i) starts a fresh container, (ii) re-executes the base trajectory's recorded pre-fork actions, and (iii) seeds the agent's message history with the recorded prefix; the branch model then runs to termination. The container is not kept live and no snapshot restore is described. — [arXiv HTML](https://arxiv.org/html/2608.08239)
- Replay fidelity is logged: 99.99% of 11,702 replayed return codes match and 707 of 708 branches reconstruct exactly. — [arXiv HTML](https://arxiv.org/html/2608.08239)
- Setup: mini-SWE-agent (bash-only ReAct), 50-step budget, 28k-token context, SWE-bench Verified, 30 instances per run pair, six run pairs, about 900 rollouts and 717 scored branch pairs; fork points at 30% and 70% of base trajectory length; models Qwen3-4B-Instruct (FP8) and Qwen3-14B (AWQ, thinking disabled) on vLLM on one 24 GB GPU. — [arXiv HTML](https://arxiv.org/html/2608.08239)
- The repository README says each fork starts "at a chosen step into a fresh container (replaying the prefix actions)", lists Docker on x86_64 as a prerequisite, puts the logic in `src/replay_gap/branching.py` and the audit in `scripts/replay_fidelity.py`; code is MIT; full study used 42.5 GPU-hours. — [GitHub AshrithaG/replay-gap](https://github.com/AshrithaG/replay-gap)
- Trajectories are released at a Hugging Face dataset. — [HF ashritha0907/replay-gap-trajectories](https://huggingface.co/datasets/ashritha0907/replay-gap-trajectories) (URL as given in the paper; not opened)
- Venue: the arXiv listing says accepted at COLM 2026; the README says Efficient Reasoning Workshop at COLM 2026. — [arXiv abs](https://arxiv.org/abs/2608.08239); [GitHub](https://github.com/AshrithaG/replay-gap)

The Handoff Tax (Ganz, Shpigel Nacson, Kalyanpur, Litman, arXiv 2608.24358, submitted 25 Aug 2026)
- A single agent loop and Docker environment are kept for the whole run; the paper states the "repository state, including all edits made by M_pre, persists after handoff". There is no snapshot and no re-execution. — [arXiv HTML](https://arxiv.org/html/2608.24358)
- Validation is bookkeeping rather than state comparison: each response is tagged with model, call number, phase, threshold and difficulty; prefix, handoff and suffix costs must reconcile with total instance cost; every task-condition run needs an audited terminal trajectory and evaluator outcome; infrastructure failures are rerun rather than scored; final patches use the official SWE-bench evaluator. — [arXiv HTML](https://arxiv.org/html/2608.24358)
- Scope: SWE-bench Verified, all 500 instances, mini-SWE-agent with a Bash tool; Claude Haiku 4.5 to/from Opus 4.7, and GPT-5.6 Luna to/from Sol. Switch points are percentiles {5, 10, 15, 25, 35, 45, 50} of the starting model's step count within difficulty buckets. — [arXiv HTML](https://arxiv.org/html/2608.24358)
- Conditions: Raw (full trajectory verbatim, no model-change notice), Compact-pre and Compact-suf (summary by prefix or suffix model), Traj-drop (no trajectory, only a fixed continuation message, working tree kept), plus "Abort + HC fresh" and "LC-full + HC-full" reference rows. — [arXiv HTML](https://arxiv.org/html/2608.24358)
- On BrowseComp the prefix tools are not re-executed; the receiver inherits the sender's recorded prefix including tool outputs. — [arXiv HTML](https://arxiv.org/html/2608.24358)
- Headline results (Claude escalation, averaged over switch points): low-cost only 60.7% at $0.40, high-cost only 79.2% at $0.72; Raw handoff 69.2% at $1.61 (quality recovery 47); Compact-pre 71.8% at $0.75; Traj-drop 72.4% at $0.81. GPT escalation: Raw 67.5%, Traj-drop 79.7% against 58.7% and 83.7% single-model rows. Dropping the trajectory helps escalation (+3.1 pp Claude, +12.2 pp GPT) and hurts downshift (−4.7 pp, −5.6 pp). — [arXiv HTML](https://arxiv.org/html/2608.24358)

AgentBaton (Li et al., arXiv 2610.35959, submitted 25 Oct 2026)
- For the checkpoint experiments the authors replay "its recorded actions in a task container to restore the workspace state after a selected turn". In the online system the receiving model "inherits the current workspace"; the paper does not say whether that is a live container, a snapshot or a re-execution. — [arXiv HTML](https://arxiv.org/html/2610.35959)
- Harness mini-SWE-agent v2.4.0; evaluation on Terminal-Bench 2.1 and DeepSWE v1.1; training data from a 2,000-task random subset of SWE-rebench V2; 11 model pairs. — [arXiv HTML](https://arxiv.org/html/2610.35959)
- Two context-transfer variants: in-context (pre-handoff conversation placed in the receiver's context) and logfile (a .jsonl file in the task environment that the receiver inspects with cat, grep, sed). — [arXiv HTML](https://arxiv.org/html/2610.35959)
- Results: 65.9% average API cost reduction and up to 8.8 pp accuracy gain over capable-only execution; capable-to-efficient handoff works, efficient-to-capable generally underperforms capable-only. — [arXiv abs](https://arxiv.org/abs/2610.35959)
- No state-fidelity validation of the replayed workspace is reported. — [arXiv HTML](https://arxiv.org/html/2610.35959)

SWE-Touch (Tan et al., arXiv 2608.02499, submitted 3 Aug 2026)
- The counter-edit is applied to the agent's current live repository state at the trigger step (written as Apply(R_tj, p)); the agent observes the new state before its next action. No pause/snapshot/re-execution machinery is described. — [arXiv HTML](https://arxiv.org/html/2608.02499)
- Patches are applied by context-matched unified diff rather than fixed line numbers, so they land even if the file shifted; application fails only if the agent has rewritten the target region. — [arXiv HTML](https://arxiv.org/html/2608.02499)
- Triggers: shell commands are classified as reads or edits; a trigger fires when the accessed span overlaps the target region, or on file-path match when line numbers are unavailable. Default up to K=3 deliveries. Longer-horizon tasks instead inject at 25%, 50% and 75% of the Vanilla command count. — [arXiv HTML](https://arxiv.org/html/2608.02499)
- Offline validation of each edit: the user edit alone fails the fail-to-pass tests, the reference patch alone passes, and the combination fails; edits may touch only implementation code. — [arXiv HTML](https://arxiv.org/html/2608.02499)
- Runtime validation: the harness records whether the edit actually changed the repository. Across 5,399 Counter-Edit runs it applied zero times in 346, once in 253, twice in 374, three times in 4,426. Of 200 tasks, 192 received a code edit and 8 fell back to text-only. — [arXiv HTML](https://arxiv.org/html/2608.02499)
- Average resolve-rate drop 7.7 points across nine models on SWE-bench Verified (for example Claude Opus 4.8 85.2 to 83.3; Qwen3-Coder-480B 57.2 to 40.7). — [arXiv HTML](https://arxiv.org/html/2608.02499)

SWE-Together (Wu et al., arXiv 2606.29957, submitted 29 Jun 2026)
- Tasks are not mid-session forks. A sandboxed task-generation agent clones the repository at a pinned commit, identifies setup and test commands and writes task artifacts; evaluation starts from the real user's first message and a reactive LLM user simulator supplies later intent. — [arXiv HTML](https://arxiv.org/html/2606.29957)
- Validation: original reference patches are reconstructed and checked through deterministic verifiers; the reference row scores about 78% pass with mean judge 0.90, and about 35% of unmet reference goals are process requirements a final patch cannot satisfy. — [arXiv HTML](https://arxiv.org/html/2606.29957)
- See section 3 for the full treatment.

Replay Fidelity in LLM Agent Evaluation (Liu and Lu, arXiv 2611.11216, submitted 8 Nov 2026)
- "Each reconstruction starts a fresh AppWorld 0.1.0 world and executes the archived code unchanged." Two controls: N uses reconstructed declared settings (seed 123, 60 s cell timeout); C additionally calls `random.seed(123)` immediately before every recorded interaction. — [arXiv PDF](https://arxiv.org/pdf/2611.11216)
- Corpus: 147 official historical train/dev ReAct executions generated under GPT-4o-2024-05-13; 76 successes and 71 failures across 32 failure-bearing task families; every failure is replayed; 284 attempts (N and C, two fresh processes each). — [arXiv PDF](https://arxiv.org/pdf/2611.11216)
- Admission rule: O (equality of all printed outputs after CRLF normalisation and outer-whitespace stripping) AND D_pre (byte equality of all 12 archived database delta files with immutable pre-oracle snapshots) AND V (native success flag and passed/failed/total test counts), in both fresh processes. — [arXiv PDF](https://arxiv.org/pdf/2611.11216)
- Isolation: network-disabled containers, read-only inputs, two CPUs, 4 GB RAM, 120 s external watchdog; zero new model API calls. — [arXiv PDF](https://arxiv.org/pdf/2611.11216)

SWE-chat (Baumann et al., arXiv 2604.20779, v1 22 Apr 2026, v2 1 Oct 2026)
- Data comes from public GitHub repositories whose developers opted into Entire.io's CLI checkpoint logging, which records agent session transcripts on a dedicated branch; each checkpoint is linked to a commit with line-level human versus agent attribution. — [arXiv HTML](https://arxiv.org/html/2604.20779)
- Recorded per session: user prompts, agent responses, tool calls, tool return values, token usage, subagent transcripts, reasoning traces for more than 6,600 sessions, commit linkage, over 54,000 checkpoints, and pre- and post-commit repository snapshots (used for Semgrep analysis). — [arXiv HTML](https://arxiv.org/html/2604.20779)
- Scale: about 18,000 sessions, more than 229,000 user prompts, roughly 2 million tool calls; Claude Code about 69% of sessions. — [arXiv abs](https://arxiv.org/abs/2604.20779); [arXiv HTML](https://arxiv.org/html/2604.20779)
- Dataset and code locations. — [HF SALT-NLP/SWE-chat](https://huggingface.co/datasets/SALT-NLP/SWE-chat); [GitHub SALT-NLP/SWE-chat](https://github.com/SALT-NLP/SWE-chat) (URLs as given in the paper; not opened)
- Limitation relevant to takeover: abandoned sessions are never committed and so are not captured. — [arXiv HTML](https://arxiv.org/html/2604.20779)

Parallel Environments for Agents / ParallelEnv (Tan, Zhang, Zaharia; DOI 10.1145/3786335.3813189)
- Represents execution as a state graph over immutable environment snapshots, with pluggable backends including Docker containers and VMs and priority, beam and round-robin scheduling; reports 48% on SWE-bench Pro at $0.50 and a 15-point gain over single-path baselines at matched compute. Shown as a demo at CAIS 2026 on 27 May 2026. — [CAIS 2026 demo page](https://caisconf.org/program/2026/demos/parallel-environments-for-agents/)

AgentRewind (Zhuang et al., arXiv 2608.14380, submitted 14 Aug 2026)
- Environment checkpoints are git-based: a bare git repository kept outside the workspace, with the live workspace as its work tree and a private index, using low-level commands that "write objects and commits without touching the working tree". The workspace is committed once per LLM node. Only the workspace directory tree is restored. — [arXiv HTML](https://arxiv.org/html/2608.14380)
- Context side: the retained prefix is replayed from recorded LLM responses and tool results in a JSON-lines log rather than re-run; one real LLM call is made at the checkpoint. — [arXiv HTML](https://arxiv.org/html/2608.14380)
- Removing environment rewind was the largest ablation loss (success down to 43.9% from 87.8% on MettleBench with mini-SWE-agent and GPT-5.4). Effects outside the workspace (network, external services, runtime state) cannot be undone. — [arXiv HTML](https://arxiv.org/html/2608.14380)
- Code. — [GitHub Futuresis/replay-agent-recorder](https://github.com/Futuresis/replay-agent-recorder) (URL as given in the paper; not opened)

Crab, DeltaBox and TClone are covered in section 6 with their privilege requirements.

### Inferences
- The live-container design (Handoff Tax, SWE-Touch) only works when the same evaluator produces both the prefix and the suffix in one run. It cannot continue a session that was recorded elsewhere, which is the Steward Bench situation. For recorded sessions the available options are prefix re-execution or rebuild-from-commit.
- Prefix re-execution worked almost perfectly in The Replay Gap (707/708) because the prefix was produced minutes earlier by the same harness in the same image with a bash-only action space. That figure should not be assumed for third-party sessions recorded on user machines with unknown dependencies.
- SWE-Together's choice to restart from turn zero with a simulator, rather than fork mid-session, is itself evidence that mid-session state recovery from wild sessions was judged impractical by a team that had the transcripts.
- AgentRewind's out-of-tree bare git repository is a cheap, unprivileged way to checkpoint a workspace per step; it captures files only, not processes or installed packages outside the tree.

### Gaps
- The ACM full text for ParallelEnv was not reachable (HTTP 403 at https://dl.acm.org/doi/10.1145/3786335.3813189), so how snapshots are taken (docker commit, VM snapshot, other) is unknown.
- AgentBaton does not state how the online "inherits the current workspace" is implemented, and gives no code URL.
- The Handoff Tax gives no code or artifact URL in the text read; the last roughly 7,500 characters of the HTML were not read.
- The final portions of the SWE-Touch (about 56k characters), SWE-chat (about 69k), AgentRewind (about 34k) and Crab (about 15k) HTML pages were truncated by the fetch tool and not read.
- SWE-chat: the paper does not say whether repository state at each turn (as opposed to each commit) can be reconstructed.

## 2. What controls are used, and what did they find about nondeterminism?

### Takeaway
Only The Replay Gap runs same-model control forks, and they show that temperature 0 is not a reliable noise floor: it depends on the serving stack. The handoff papers (Handoff Tax, AgentBaton) run one episode per cell with no same-model handoff control, so their effects are not separated from run-to-run variance.

### Cited Findings
- The Replay Gap: each fork point has a same-model control arm that "absorbs sampler nondeterminism, batching effects, and environment-replay drift". All runs use temperature 0 on vLLM. — [arXiv HTML](https://arxiv.org/html/2608.08239)
- The Replay Gap: AWQ-served 14B controls have normalised edit distance 0.16 to 0.23 and half never diverge; FP8-served 4B controls diverge on 90 to 96% of forks with edit distance 0.49 to 0.67. The authors conclude that "batched FP8 serving is not a deterministic oracle" and attribute the difference to the serving stack without isolating a specific cause. — [arXiv HTML](https://arxiv.org/html/2608.08239)
- The Replay Gap: swaps exceed their matched control floors by +0.25 to +0.66 normalised edit distance with multiplicity-corrected confidence intervals excluding zero; swaps rewrite 61 to 94% of post-fork actions; for early swaps 74 to 77% diverge at the first post-fork action against 6 to 35% of controls; all five outcome flips occur in swap arms and none across 359 control forks. — [arXiv abs](https://arxiv.org/abs/2608.08239)
- The Replay Gap, stated limitations: one scaffold, one benchmark family, one model family in two quantised forms, 30 instances per run pair, resolution rates of 0 to 3% under the 24 GB budget, and quantisation confounds capability with serving stack. The branch model also inherits the base model's in-context style through the replayed prefix. — [arXiv HTML](https://arxiv.org/html/2608.08239)
- The Handoff Tax: no same-model handoff control is reported; one episode per task per configuration; variability across repeated runs is not estimated. Temperature is 0 for Haiku and provider defaults for GPT-5.6 (medium reasoning effort) and Opus 4.7 (high reasoning effort). Statistics are a task-clustered bootstrap with 10,000 replicates. A disclosure ablation (telling the receiver a model change occurred) gives 70.5 versus 69.3 pass for Raw at higher cost. — [arXiv HTML](https://arxiv.org/html/2608.24358)
- AgentBaton: no same-model handoff control, no repeated runs or variance, temperature unspecified beyond "recommended sampling parameters where available, otherwise serving defaults". — [arXiv HTML](https://arxiv.org/html/2610.35959)
- SWE-Touch controls: Text-only (message without repository change), Code-only (edit without message), Both at K=1, 3 and 5, and Co-Edit (a helpful non-solving patch). Three runs per condition on SWE-bench Verified, two on the longer-horizon sets. Temperature is fixed per model (1.0 for GLM 5.1, MiniMax, Kimi, DeepSeek; 0.7 for Qwen), not varied. — [arXiv HTML](https://arxiv.org/html/2608.02499)
- SWE-Together: k = 2 replicates per task; reports pass@1, a stable solve rate on the averaged score, and pass-squared (both replicates must pass). The gap between pass@1 and pass-squared is large, for example 63% versus 52% for Claude Opus 4.8 and 48% versus 29% for DeepSeek-V4-Pro. — [arXiv HTML](https://arxiv.org/html/2606.29957)
- Replay Fidelity: with no model in the loop at all (archived code re-executed), reconstructed declared settings admit only 42 of 71 cases; reseeding Python's RNG before every interaction admits 68 of 71. All 25 first-output N stops are "token-expiry text variations". For traces with at least two login requests admission goes from 1/30 (N) to 27/30 (C); traces with at most one login pass 41/41 under both. — [arXiv PDF](https://arxiv.org/pdf/2611.11216)
- Replay Fidelity: "Two fixed-seed processes assess technical repeatability here; they do not estimate a stochastic failure probability." — [arXiv PDF](https://arxiv.org/pdf/2611.11216)

### Inferences
- There are two distinct noise sources and they need separate controls: model-side (sampling, batching, quantised serving) and environment-side (RNG stream position, token expiry, time). The Replay Gap measures the first with control forks; Replay Fidelity measures the second with no model in the loop.
- None of the papers reports provider pinning for hosted APIs. The Replay Gap sidesteps it by self-hosting; The Handoff Tax and AgentBaton do not discuss it.
- A takeover study that compares models on a continued session should include a control in which the original model continues from the same rebuilt state, run more than once, so that the between-model difference can be read against a floor. SWE-Together's pass@1 versus pass-squared gap indicates that two replicates already expose substantial instability.

### Gaps
- No paper in the set reports a noise floor for a hosted frontier model continuing its own trajectory after a rebuild.
- The specific cause of FP8 nondeterminism is not identified by The Replay Gap.

## 3. SWE-Together specifically

### Takeaway
SWE-Together distils 11,260 public sessions into 109 tasks that restart from a pinned commit in a prebuilt image; it ships an enroot-on-Slurm backend that needs no Docker daemon. Each task directory is documented as containing `original_session.json`, and the one I opened holds a message list with tool calls and results embedded as text, so a mid-session takeover prompt can be built from it, but the benchmark provides no mid-session environment state.

### Cited Findings

Filtering
- Sources are public Hugging Face datasets: DataClaw (2,228 raw sessions, 29 final tasks), Pi-staging (2,397, 23), Hyperswitch (784, 9) and SWE-chat (5,851, 48). These sum to 11,260 sessions and 109 tasks, a yield of 0.97%. — [arXiv HTML](https://arxiv.org/html/2606.29957)
- Step 1 is deterministic with no LLM calls: requires multiple genuine user messages, concrete agent actions or code edits and enough repository signal; favours public mature repositories; drops sessions where the human authored most of the final change. — [arXiv HTML](https://arxiv.org/html/2606.29957)
- Step 2 is an LLM viability screen over a compact session summary; it rejects sessions whose main deliverable depends on external state such as PR management, deployment or private credentials; it does not judge correctness. — [arXiv HTML](https://arxiv.org/html/2606.29957)
- Step 3 runs a sandboxed task-generation agent that clones the repository at a pinned commit, identifies setup and test commands and writes the task artifacts. — [arXiv HTML](https://arxiv.org/html/2606.29957)
- The abstract summarises the selection criteria as recoverable repository states, clear user goals and observable outcomes. — [arXiv abs](https://arxiv.org/abs/2606.29957)

Repository state and dependencies
- The base state is the repository cloned at a pinned commit inside an isolated sandbox, with a pinned execution environment and deterministic verifier artifacts. — [arXiv HTML](https://arxiv.org/html/2606.29957)
- Each task directory holds `environment/Dockerfile`, `task.toml`, `instruction.md` (the real user's first message), `tests/test.sh`, `tests/test_manifest.yaml` (F2P/P2P gates for the legacy tier), `tests/install_config.json`, `tests/log_parsers.py`, `tests/swe_constants.py`, `user_simulation_prompt.md`, `original_session.json` and `README.md`. Example task ids: `agent-swarm-task-4a881b`, `cli-task-0ec2e9`, `hyperswitch-8008`, `pi-mono-auto-0467b78e`, `dataclaw-anonymizer-tests`. — [GitHub tasks/](https://github.com/Togetherbench/SWE-Together/tree/main/tasks)
- Task images include a DNS sinkhole (`environment/seal-dns.sh`); the docs describe it as the only network control on e2b and docker and as bypassable. — [docs/sandboxes.md](https://github.com/Togetherbench/SWE-Together/blob/main/docs/sandboxes.md)

Image naming
- Format `ghcr.io/togetherbench/multi-user-turn-codebench/<task>:<tag>`; all images public; the tag is the image's content hash from `task.toml`. The judge re-applies the agent's patch in a fresh container from the same image. — [docs/sandboxes.md](https://github.com/Togetherbench/SWE-Together/blob/main/docs/sandboxes.md)
- 109 images, about 98 GB compressed and about 130 GB as `.sqsh`. — [docs/sandboxes.md](https://github.com/Togetherbench/SWE-Together/blob/main/docs/sandboxes.md)

Hugging Face dataset
- 109 rows, one `test` split, Apache-2.0, 31 columns: `task_id`, `instruction`, `repo`, `repo_url`, `base_commit`, `language`, `difficulty`, `category`, `tags`, `scoring_tier` (swerebench or legacy), `num_user_intents`, `expert_time_estimate_min`, `junior_time_estimate_min`, `agent_timeout_sec`, `docker_image`, `allow_internet`, `cpus`, `memory`, `fail_to_pass`, `pass_to_pass`, `test_cmd`, `log_parser`, `source_files`, `reference_patch`, `patch_files_changed`, `patch_additions`, `patch_deletions`, `patch_is_agent_author`, `oracle_intents`, `completeness_goals`, `test_manifest`. — [HF dataset card](https://huggingface.co/datasets/yfwu/SWE-Together)
- The card fetched at the `yfwu/SWE-Together` URL identified the dataset as `yifannnwu/SWE-Together`; the GitHub README still loads `yfwu/SWE-Together`. — [HF dataset card](https://huggingface.co/datasets/yfwu/SWE-Together); [GitHub README](https://github.com/Togetherbench/SWE-Together)
- The dataset rows do not contain session transcripts: each row has the first user message, an ordered list of intents with short verbatim excerpts, and a reconstructed reference patch. — [HF dataset card](https://huggingface.co/datasets/yfwu/SWE-Together)

Enroot on Slurm
- `SWT_SANDBOX=enroot` runs trials and the judge in enroot containers on Slurm compute nodes; requirements are enroot 3.5 or later, Slurm, and tmpfs at `/dev/shm`; no sandbox account. Other backends are `e2b` (default) and `docker` (whose judge falls back to E2B). — [docs/sandboxes.md](https://github.com/Togetherbench/SWE-Together/blob/main/docs/sandboxes.md)
- Workflow: `python scripts/slurm/smoke_enroot.py`; `python scripts/slurm/launch.py prepull --submit` (about 20 minutes as an sbatch array); `launch.py run --tag <tag> --shards 12 --workers 6 --submit`; `launch.py judge ... --submit`. Every subcommand is a dry run without `--submit`; `run` resumes with `--skip-existing`. — [docs/sandboxes.md](https://github.com/Togetherbench/SWE-Together/blob/main/docs/sandboxes.md)
- Each Docker image is converted once with `enroot import` to a `.sqsh` file under `<SWT_IMAGE_STORE>/_store/<task>__<tag>.sqsh`; containers are then created in about 2 s. — [docs/sandboxes.md](https://github.com/Togetherbench/SWE-Together/blob/main/docs/sandboxes.md)
- `ENROOT_TEMP_PATH` and `ENROOT_DATA_PATH` must be tmpfs because Lustre and NFS cannot create the whiteout devices enroot needs; the runtime refuses to start otherwise. The container rootfs is tmpfs charged to the job's memory cgroup; budget about 4 GB process plus 8 GB rootfs per concurrent trial; defaults 32 CPU, 128 GB, 8 h per array task, 6 concurrent trials. — [docs/sandboxes.md](https://github.com/Togetherbench/SWE-Together/blob/main/docs/sandboxes.md)
- enroot below 4.0 cannot import the Go-based `cli-*` images (read-only module cache breaks `tar -px`); import those on a host with enroot 4.0 or later. — [docs/sandboxes.md](https://github.com/Togetherbench/SWE-Together/blob/main/docs/sandboxes.md)
- Caveats: no PID namespace, so background processes survive `enroot remove` and the code kills tagged PIDs; the agent runs as root inside the container; egress is enforced only on enroot, through a default-deny allowlist proxy in an unprivileged user and network namespace (`unshare -Urn`); the leaderboard refuses e2b or docker runs unless `--allow-porous-sandbox` is passed. — [docs/sandboxes.md](https://github.com/Togetherbench/SWE-Together/blob/main/docs/sandboxes.md)
- The sandbox documentation does not mention Apptainer or Singularity. — [docs/sandboxes.md](https://github.com/Togetherbench/SWE-Together/blob/main/docs/sandboxes.md)

Original transcripts
- The README's per-task layout lists `original_session.json` as "Raw session data (provenance)". — [GitHub tasks/](https://github.com/Togetherbench/SWE-Together/tree/main/tasks)
- For `tasks/hyperswitch-8008/original_session.json` the file is one minified JSON object of about 301 KB with top-level keys `session_id`, `model` (an "unknown" placeholder) and `messages`; each message has `role` and `content`; tool calls and tool results are embedded as text blocks inside `content` keyed by call id; no timestamps were visible; the base commit SHA appears in the first user message; some assistant turns are truncated mid-argument. Only the first third of the file was inspected. — [original_session.json](https://github.com/Togetherbench/SWE-Together/blob/main/tasks/hyperswitch-8008/original_session.json)
- The paper itself does not state whether original transcripts are released; it says the sessions come from openly released community datasets. — [arXiv HTML](https://arxiv.org/html/2606.29957)

User simulator and scoring (context for takeover designs)
- After each completed agent turn the simulator makes one structured decision among no-op (default), question, redirect, new-requirement and check-external, conditioned on a summary of the live trajectory, the latest agent response, elapsed time, observed repository changes and its own prior decisions. It cannot interrupt mid-turn or edit files. Turing pass rate in a human study was 46% (95% CI 40.5 to 51.6). — [arXiv HTML](https://arxiv.org/html/2606.29957)
- Primary signal is an agentic rubric judge with a frozen weighted rubric per task and threshold 0.85; User Correction = N_correction + 0.2 × N_nudge, with Pearson −0.92 against pass@1. — [arXiv HTML](https://arxiv.org/html/2606.29957)

### Inferences
- A model could take over a SWE-Together session mid-way at the conversation level, because `original_session.json` carries the prior messages and embedded tool results, but the only environment state on offer is the base commit image. Reaching the mid-session filesystem would require re-executing the recorded tool calls inside that image, and the truncated assistant turns observed in one file suggest this would not be loss-free.
- 48 of the 109 tasks derive from SWE-chat, whose Entire checkpoints are commit-linked. For those tasks an intermediate state at a checkpoint commit may be recoverable from the source repository's history rather than by re-execution; this is not something SWE-Together does.
- The enroot backend's constraints (tmpfs rootfs, no network-filesystem overlays, memory-charged rootfs) are the same class of constraint an Apptainer port would face.

### Gaps
- The paper does not report intermediate counts after filtering steps 1 and 2.
- The paper does not describe how Dockerfiles were validated beyond deterministic verifiers and reference-patch scoring; whether every task has fail-to-pass tests that were confirmed to fail before and pass after is not stated in the text read (the dataset has `fail_to_pass` only for the swerebench tier).
- Whether every one of the 109 task directories actually contains `original_session.json`, and whether all are complete transcripts, was verified for one task only.
- Whether enroot requires any site-admin setup on the host is not stated in the SWE-Together docs.
- Why the Hugging Face card resolves to `yifannnwu/SWE-Together` (rename or redirect) was not confirmed.

## 4. What does the literature say about log replay or emulated tool results as a substitute for a live environment?

### Takeaway
The evidence is consistently negative for using recorded logs to predict what a different continuation would do, and positive only for the narrow use of replaying a fixed prefix to rebuild context. Restoring the conversation without the environment recovers 6 to 13% of tasks in Crab's study.

### Cited Findings
- The Replay Gap's log-stitching evaluator, which predicts a switch outcome from the target model's logged run on the same instance, agrees with branched ground truth 96.6 to 100% of the time on raw outcome, but that is the majority failure class. On the five success-relevant calls it is 0 for 5 (missed all three switching successes and produced two false successes). A constant always-fail predictor scores 2 of 5. Predicted patches have 0.00 to 0.11 similarity to the patches the switches actually produced. — [arXiv HTML](https://arxiv.org/html/2608.08239)
- The Replay Gap defines replay validity as the prefix-match fraction: the share of the base's post-fork actions before the first divergence (exact command-string comparison). It is 3.2% and 8.0% for early swaps (up and down) and 34.4% and 39.4% for late swaps. — [arXiv HTML](https://arxiv.org/html/2608.08239)
- The Replay Gap recommends live or branched evaluation for agentic routers and says off-policy estimators (importance sampling, doubly robust) might be cheaper approximations but must be validated against branched data. — [arXiv HTML](https://arxiv.org/html/2608.08239)
- Crab: restoring conversation alone "treats the sandbox as stateless and loses both filesystem and process state after a crash"; chat-only recovery reaches 8% on iFlow-cli Terminal-Bench, 13% on Claude Code, and 6% in the replay study, against 100% with Crab. — [arXiv HTML](https://arxiv.org/html/2604.28138)
- Crab also finds that over 75% of agent turns produce no recovery-relevant state; Claude Code on Terminal-Bench needs filesystem checkpoints for 5% of turns and full checkpoints for 8%. — [arXiv abs](https://arxiv.org/abs/2604.28138); [arXiv HTML](https://arxiv.org/html/2604.28138)
- AgentRewind uses recorded LLM responses and tool results to rebuild the context prefix without re-running it, while restoring the workspace separately from git; removing the environment rewind is the most damaging ablation. — [arXiv HTML](https://arxiv.org/html/2608.14380)
- The Handoff Tax on BrowseComp passes the recorded prefix including tool outputs to the receiver without re-executing tools; this is a read-only web setting. — [arXiv HTML](https://arxiv.org/html/2608.24358)
- Replay Fidelity: completing 25 text-mismatched replays with the recorded code held fixed resolves 24 to matching database and verdict endpoints, but the authors warn that "masking text is not a demonstrated solution for live agents: an agent could react to a changed observation", and that fixed-code completion "cannot predict a live actor's response to changed observations". — [arXiv PDF](https://arxiv.org/pdf/2611.11216)
- DeltaBox measures replay as a restore mechanism for tree search on SWE-bench: "replay+cp" costs 27,694 ms per restore on weighted average against 811 ms for CRIU+cp and 1.86 ms for DeltaBox. — [arXiv HTML](https://arxiv.org/html/2605.22781)

### Inferences
- Recorded tool results are acceptable for the prefix (what the taking-over model reads) but not for the suffix (what it does next). Once the new model issues a command that differs from the log, there is no recorded result, and The Replay Gap shows this happens at the first post-fork action in roughly three quarters of early swaps.
- Read-only or stateless tools (web search in BrowseComp) tolerate recorded prefixes with no state rebuild. Stateful workspaces do not.
- Most turns do not change recoverable state (Crab), which suggests that fork points can be chosen at turns where the state is easy to reconstruct, and that re-execution can skip read-only actions if their outputs are taken from the log. This is my inference; no paper in the set tests selective re-execution.

### Gaps
- I found no paper in this set that evaluates an LLM-emulated environment (a model generating tool results) as a substitute for a live sandbox in a takeover setting.
- Replay Fidelity cites further work on this question that I did not open or verify: ReplayRCA, SymTrace, Chronicle, Who&When Pro, and Pakhomov and Nijkamp on RNG-stream omissions in agent snapshots. They are named only in that paper's related-work section. — [arXiv PDF](https://arxiv.org/pdf/2611.11216)

## 5. What metrics exist for replay fidelity, and what practices are recommended?

### Takeaway
Metrics fall into three levels: action-level agreement (return-code match, exact branch reconstruction, prefix-match fraction, normalised edit distance), state-level agreement (database delta bytes, table-row fingerprints, patch similarity) and verdict-level agreement (pass/fail and test counts). Replay Fidelity shows these levels disagree on the same cases and recommends reporting a ladder of them rather than one replayability flag.

### Cited Findings
- Replayed return-code match rate (99.99% of 11,702) and exact branch reconstruction count (707 of 708). — [The Replay Gap](https://arxiv.org/html/2608.08239)
- Prefix-match fraction as "replay validity", first-divergence rate at the first post-fork action, normalised edit distance over post-fork actions against a same-model control floor, patch similarity, and outcome flips. — [The Replay Gap](https://arxiv.org/html/2608.08239)
- The Replay Gap advises reporting patch metrics only on subsets where both sides produced non-empty patches, since naive identity scores inflate. — [The Replay Gap](https://arxiv.org/html/2608.08239)
- Replay Fidelity's contract ladder on 71 cases, as matched/different/unknown: O AND D_pre AND V gives N 42/29/0 and C 68/2/1; D_pre AND V gives N 66/4/1 and C 70/0/1; V only gives 70/0/1 for both. The N-to-C gap is 26 under the strict rule, 4 under database plus verdict, 0 under verdict only. — [arXiv PDF](https://arxiv.org/pdf/2611.11216)
- Replay Fidelity: four cases match on outputs and verdict yet differ in materialised phone-table rows; a byte mismatch in change streams was confirmed as a real content difference by sorting and hashing encoded rows per table. — [arXiv PDF](https://arxiv.org/pdf/2611.11216)
- Replay Fidelity: in one case that passed output, delta and verdict checks under both controls, an identical API call (`send_password_reset_code` with the same literal argument) returned the same HTTP 200 and printed response but produced different post-call database rows, because the Python RNG states differed; seeding the future RNG identically removed the difference in both paired repetitions. — [arXiv PDF](https://arxiv.org/pdf/2611.11216)
- Replay Fidelity: credential state is not in the database deltas. Logout blacklists a token value in an in-memory manager set, so two admitted reconstructions gave 200 versus 401 on the same later read and write. — [arXiv PDF](https://arxiv.org/pdf/2611.11216)
- Replay Fidelity: running the oracle in-process rebinds ORM classes, so a naive post-oracle export would cut admission from 42 to 35 (N) and 68 to 58 (C); exporting from retained output trackers restored the comparison in all 10 affected cases. — [arXiv PDF](https://arxiv.org/pdf/2611.11216)
- Replay Fidelity's guidance, quoted: "Name the reference object and observation stage; separate attempt completeness from endpoint knowledge; report a contract ladder rather than one context-free replayability flag. Keep immutable pre-oracle execution snapshots. If exporting after evaluation, verify the data source rather than trusting a mutable class binding. Declare the continuation program class and randomness coupling; use fixed-argument and state-alignment controls when explaining a behavioral witness. Retain missingness and complete the same frozen case inventory without searching for replacement successes." — [arXiv PDF](https://arxiv.org/pdf/2611.11216)
- Replay Fidelity keeps match, difference and unknown as three separate values: "A missing endpoint is not an observed state difference." It also notes that per-interaction reseeding is "a controlled execution-policy change", not a generally correct restoration policy. — [arXiv PDF](https://arxiv.org/pdf/2611.11216)
- Replay Fidelity scope limits: one old AppWorld version (0.1.0), one source harness and model lineage, no new policy sampling; a 12-case BFCL pilot (24 attempts, 254 decode events, 196 tool returns, 114 public snapshot groups) showed no discrepancy but is not a prevalence estimate. — [arXiv PDF](https://arxiv.org/pdf/2611.11216)
- SWE-Touch's runtime record of whether an injected edit actually changed the repository (346 of 5,399 runs applied it zero times) is a fidelity metric for the intervention itself. — [SWE-Touch](https://arxiv.org/html/2608.02499)
- Crab's inspector accuracy on 2,063 labelled turns: 100% for process changes, 98.3% for filesystem changes, 2.3% false positives, zero false negatives. — [Crab](https://arxiv.org/html/2604.28138)
- The Handoff Tax's practice: rerun infrastructure failures rather than score them, exclude no task, and require an audited terminal trajectory per cell. — [The Handoff Tax](https://arxiv.org/html/2608.24358)

### Inferences
- For a coding workspace the analogue of Replay Fidelity's ladder would be: per-command return code and output match during prefix re-execution; filesystem state match at the fork point (tree hash or `git status`/diff against a reference); and verifier outcome match when the original model's own suffix is re-run. No paper in the set defines this ladder for coding sandboxes; The Replay Gap covers only the first rung plus exact reconstruction.
- Verdict-level agreement is the weakest check and is the one most benchmarks rely on. Replay Fidelity's 70/71 at verdict level against 42/71 at the strict level is the clearest quantification of that gap.
- Hidden state that a filesystem or database diff cannot see (RNG position, in-memory token sets, current working directory) matters only if the continuation touches it, so fidelity is relative to the continuation being evaluated. The paper states this as "declare the continuation program class".

### Gaps
- The Replay Fidelity paper's code or data URL was not found in the body text I read (the reference list was not inspected). The arXiv listing calls it v1 while the PDF header reads "Preprint preparation | v3, 7 November 2026".
- No source gives a threshold for acceptable replay fidelity.

## 6. Which approaches work on an HPC cluster with Apptainer only (no Docker, no root)?

### Takeaway
Re-executing a recorded prefix in a fresh container, rebuilding from a pinned commit, and git-based workspace checkpoints need nothing beyond an unprivileged container runtime, and SWE-Together demonstrates the pattern on Slurm with enroot. The OS-level checkpoint systems (Crab, DeltaBox, TClone) depend on eBPF, kernel modules, Firecracker, ZFS or CRIU and are not usable without host control. No source in this set documents an Apptainer backend.

### Cited Findings
- SWE-Together runs its whole benchmark on Slurm without Docker by converting each public ghcr.io image once to a squashfs file with `enroot import` and running containers with a tmpfs rootfs; overlay state must live on tmpfs because Lustre and NFS cannot create whiteout devices. Its egress control uses an unprivileged user and network namespace (`unshare -Urn`). — [docs/sandboxes.md](https://github.com/Togetherbench/SWE-Together/blob/main/docs/sandboxes.md)
- The Replay Gap's fork procedure needs only "start a fresh container, re-execute recorded actions, seed history"; its released harness lists Docker as a prerequisite and does not mention Apptainer. — [arXiv HTML](https://arxiv.org/html/2608.08239); [GitHub](https://github.com/AshrithaG/replay-gap)
- AgentRewind's checkpoints are a bare git repository outside the workspace, operating on the work tree with a private index; the paper states no privilege requirement and says it works whether the agent runs locally or in an isolated container. — [arXiv HTML](https://arxiv.org/html/2608.14380)
- Crab uses runc with CRIU for process state and OpenZFS snapshots for filesystem state, with an eBPF inspector on syscall tracepoints that reads `/proc/PID/pagemap` and `/proc/PID/clear_refs`. Process checkpoints take about 700 to 1,000 ms and filesystem-only checkpoints tens of ms. — [arXiv HTML](https://arxiv.org/html/2604.28138)
- DeltaBox is an overlayfs extension shipped as a Linux 6.8 kernel module inside Firecracker microVMs, on XFS with reflink, orchestrating CRIU with lazy-pages restore; it reports 10.83 ms checkpoint and 1.86 ms fast-path restore. It does not support network I/O rollback. — [arXiv HTML](https://arxiv.org/html/2605.22781)
- DeltaBox's survey table of alternatives (mostly literature or vendor figures): git stash/branch 100 ms to 1 s; `shutil.copytree` 100 ms to 10 s; docker commit plus restart 50 ms to 10 s checkpoint and 1 to 10 s restore; btrfs/LVM snapshot 10 to 100 ms; Firecracker VM snapshot 200 ms to 2 s; E2B about 4 s per GiB RAM. — [arXiv HTML](https://arxiv.org/html/2605.22781)
- TClone modifies the Linux kernel, adds a kernel module for per-VMA copy-on-write sharing and extends CRIU; code is promised on acceptance. It reports container fork up to 4.9 times faster than KVM and 3.4 times faster than CRIU. External side effects are not rolled back. — [arXiv HTML](https://arxiv.org/html/2605.17320)
- ParallelEnv lists Docker containers and VMs as its backends. — [CAIS 2026 demo page](https://caisconf.org/program/2026/demos/parallel-environments-for-agents/)
- Apptainer's own checkpoint feature is based on DMTCP, not CRIU, and is marked experimental. It injects a host DMTCP install into the container; the application must be dynamically linked against glibc. Commands: `apptainer checkpoint create <name>`, `apptainer instance start --dmtcp-launch`, `apptainer checkpoint instance <instance>`, `apptainer instance start --dmtcp-restart`. The checkpoint store lives in the user's home directory. — [Apptainer user guide: checkpoint](https://apptainer.org/docs/user/main/checkpoint.html)
- Apptainer persistent overlays: "ext3 file system images can be used as overlays without root privileges", created with `apptainer overlay create --size <MiB> <file>` (options `--sparse`, `--fakeroot`, `--create-dir`). Directory overlays need root under a setuid install, or `--userns`/`--fakeroot` with unprivileged user namespaces, and "generally do not work well on network file servers". A single overlay file is easier to transport than a directory; sparse images must be copied with a sparse-aware tool. — [Apptainer user guide: persistent overlays](https://apptainer.org/docs/user/main/persistent_overlays.html)
- CRIU without root: secondary sources returned by search state that Podman's documented checkpoint workflow is for root containers and that CRIU has limited non-root support via `CAP_CHECKPOINT_RESTORE`; the runc source notes checkpoint is untested with rootless containers. These are third-party blog and search-summary claims, not checked against CRIU's own documentation. — [oneuptime blog, 18 Mar 2026](https://oneuptime.com/blog/post/2026-03-18-create-container-checkpoints-podman/view); [CRIU main page](https://criu.org/Main_Page)

Feasibility summary for Apptainer-only, no root (classification is my inference from the findings above).

| Approach | Needs | Apptainer-only verdict |
|---|---|---|
| Rebuild from pinned commit in a prebuilt image (SWE-Together) | Pull OCI image, unprivileged container | Feasible in principle; demonstrated with enroot, not Apptainer |
| Re-execute recorded prefix in a fresh container (Replay Gap, AgentBaton) | Container exec, writable rootfs | Feasible in principle; released harness assumes Docker |
| Git-based workspace checkpoint (AgentRewind) | git only | Feasible; files in the workspace only |
| Copy an ext3 overlay image as a filesystem snapshot | Apptainer overlay images | Plausible; documented as rootless, snapshot-by-copy not documented |
| Keep the live container across a switch (Handoff Tax, SWE-Touch) | A long-lived container per run | Feasible but only for sessions generated in-house |
| DMTCP process checkpoint | Host DMTCP, glibc | Experimental |
| docker commit | Docker daemon | Not available |
| CRIU, Crab, DeltaBox, TClone | Root or capabilities, eBPF, kernel modules, ZFS, Firecracker | Not available |

### Inferences
- The practical route on an Apptainer-only cluster is the one The Replay Gap and SWE-Together already use between them: a per-task image converted once to an immutable squashfs/SIF, a writable layer on node-local tmpfs or an ext3 overlay file, prefix re-execution to reach the fork point, and a fresh container per fork. Process state is not carried; the Crab data (full checkpoints needed on 5 to 8% of turns) suggests process state is rarely essential for coding agents, though that is a Terminal-Bench measurement, not a general result.
- Because an Apptainer ext3 overlay is a single file, copying it after prefix re-execution would give a reusable fork-point snapshot so each arm does not repeat the prefix. This follows from the overlay documentation but is not described as a supported snapshot workflow there or in any paper here.
- SWE-Together's enroot caveats are likely to carry over to Apptainer: overlay upper directories fail on Lustre/NFS, and without a PID namespace background processes leak between runs. Apptainer's handling of PID namespaces was not checked in this session.
- The ghcr.io/togetherbench images are public OCI images, so an OCI-capable unprivileged runtime should be able to pull them; I did not test or source an Apptainer pull of these images, and the enroot note about Go module caches breaking unprivileged `tar` extraction suggests some images may fail to convert.

### Gaps
- No primary source in this set reports running any of these benchmarks under Apptainer. The verdicts in the table are inferences.
- CRIU's and Docker's own documentation on rootless checkpointing and `docker commit` were not fetched; the statements about them rest on a search summary and on DeltaBox's survey table.
- Crab, DeltaBox and TClone do not state their privilege requirements explicitly; "not available" is inferred from their use of eBPF, kernel modules, ZFS and Firecracker. None of the three provides a code URL in the text read.
- A related paper surfaced by search but not read: "Fork, Explore, Commit: OS Primitives for Agentic Exploration" (arXiv 2602.08199, February 2026). — [arXiv abs](https://arxiv.org/abs/2602.08199v2)
