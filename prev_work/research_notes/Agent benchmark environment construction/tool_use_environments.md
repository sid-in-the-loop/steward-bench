# Tool-use and workplace agent benchmark environments: real versus mocked services

Research date: 2026-11-11. All sources were read on that date through a fetch tool that summarises pages; where a detail came only from such a summary of a long page and could not be cross-checked, it is flagged. "Primary" below means the benchmark's own paper, repository or dataset card.

## 1. For each benchmark: which services are real, locally deployed, or mocked, and how is per-task state seeded and isolated?

### Takeaway
The benchmarks fall into three families: live third-party accounts plus self-hosted apps (Toolathlon, MCPMark, MCP-Universe, MCP-Atlas, LiveMCPBench, WorkArena), self-written apps over a local database (AppWorld, Toolathlon-GYM), and in-process Python tools over JSON or in-memory state (tau-bench, tau2-bench, ToolSandbox). Only the second and third families give full reset and isolation without external accounts; the first family depends on per-task clean-and-refill scripts against shared remote accounts.

### Cited Findings

Toolathlon (primary; details in section 2)
- 32 applications, 604 tools, 108 tasks, about 20 turns per task on average; submitted 29 Oct 2025, v2 26 Feb 2026, ICLR 2026 — [arXiv abstract](https://arxiv.org/abs/2510.25726)
- Appendix A Table 4 marks each MCP server Remote or Local. Remote: arXiv, arXiv LaTeX, Fetch, GitHub, Google Calendar, Google Cloud, Google Forms, Google Maps, Google Sheets, HowToCook, Hugging Face, Notion, Playwright, 12306, Scholarly, Snowflake, Weights & Biases, Yahoo Finance, YouTube, YouTube Transcript. Local: Canvas-LMS, Emails (Poste.io), Excel, Filesystem, Git, Kubernetes, Memory, PDF Tools, PowerPoint, Word, WooCommerce. Terminal is marked both — [arXiv HTML v2](https://arxiv.org/html/2510.25726v2)
- Section 2.2 states that remote services are hard to reset and to populate with realistic data, which is why Poste.io (replacing Gmail), Canvas, Kubernetes and WooCommerce are deployed locally in containers — [arXiv HTML v2](https://arxiv.org/html/2510.25726v2)
- 72 of 108 tasks (67%) have an initial state, provided by a state-initialisation script run at execution time, a pre-set initial_workspace directory, or both; scripts clear and refill state so each run starts identically; each task runs in its own container — [arXiv HTML v2](https://arxiv.org/html/2510.25726v2)

Toolathlon-GYM (primary; details in section 3)
- 503 tasks, 25 MCP servers, all data served from a local PostgreSQL database; "no external API calls" at runtime or evaluation; blog dated 12 March 2026 — [Eigent blog](https://www.eigent.ai/blog/toolathlon-gym-large-scale-long-horizon-environments-for-tool-use-agents)

MCP-Atlas (Scale AI)
- 1,000 tasks (500 public, 500 private) over 36 real MCP servers and 220 tools; submitted 31 Jan 2026. Public-subset servers listed in Appendix H include notion, slack, airtable, mongodb, google-workspace, github, google-maps, exa, brave-search, oxylabs, alchemy, twelvedata, e2b-server, filesystem, git, memory, fetch, wikipedia, arxiv, pubmed — [arXiv HTML](https://arxiv.org/html/2602.00933v1)
- Tasks are read-only: the paper "focuses exclusively on read-only operations (queries, searches, data retrieval)"; write operations are deferred to future work with controlled synthetic environments. Servers run in a version-pinned container with sandboxed filesystem and allow-listed egress, restarted per run; only the task's servers are mounted — [arXiv HTML](https://arxiv.org/html/2602.00933v1)
- Third-party summaries say about 18% of tasks are solvable with the 20 servers needing no API keys and the rest need credentials for services such as Exa, Airtable, MongoDB and Brave Search (secondary source, not verified in the paper) — [search summary citing EvalScope docs](https://evalscope.readthedocs.io/en/latest/benchmarks/mcp_atlas.html)
- Scale's blog gives "40+ servers and 300+ tools", conflicting with the paper's 36 and 220 — [Scale blog](https://scale.com/blog/mcp-atlas); contradicted by [arXiv HTML](https://arxiv.org/html/2602.00933v1)

AppWorld
- 9 simulated everyday apps (Gmail, Venmo, Amazon, Spotify, phone, file system and others) exposing 457 APIs, implemented by the authors with FastAPI, SQLite and SQLModel (about 26K lines, 1,780 unit tests) — [arXiv HTML](https://arxiv.org/html/2407.18901)
- A Base DB of 101 tables and about 370K rows covers about 106 fictitious people with two or more years of activity, populated through the apps' own APIs rather than raw SQL so derived state stays consistent — [arXiv HTML](https://arxiv.org/html/2407.18901)
- Each task has a Setup program that copies the Base DB, applies task-specific changes (distractors, hurdles), picks the supervisor and sets a start timestamp; time is frozen with freezegun; the engine runs in one process using in-memory SQLite and FastAPI TestClient, with task load under 0.5 s; a Docker image with gVisor is offered for containment — [arXiv HTML](https://arxiv.org/html/2407.18901)
- 750 tasks from 250 scenarios (Train 105, Dev 60, Test-N 168, Test-C 417) — [arXiv HTML](https://arxiv.org/html/2407.18901)

tau-bench
- Each domain is a set of JSON database files (retail: 500 users, 50 products, 1,000 orders; airline: 500 users, 300 flights, 2,000 reservations) reachable only through Python-function tools with deterministic state transitions; database entries were generated with gpt-4-written code and manually fixed — [arXiv HTML](https://arxiv.org/html/2406.12045)
- The user is an LLM simulator (gpt-4-0613, temperature 1.0) holding a hidden instruction; 115 retail and 50 airline tasks; 30-action limit — [arXiv HTML](https://arxiv.org/html/2406.12045)

tau2-bench
- Adds a dual-control telecom domain modelled as a Dec-POMDP in which both agent and user call tools on a shared environment; tasks come from a compositional generator over atomic components; the user simulator is constrained by its tools and observable state; submitted 9 June 2025 — [arXiv abstract](https://arxiv.org/abs/2506.07982)

MCPMark
- 127 tasks over five environments built from 38 curated initial states. Notion and GitHub use the official remote APIs; Filesystem, PostgreSQL and Playwright are local services. Initial states: 9 Notion Marketplace templates, GitHub repositories with real history, curated folder trees, template databases (chinook, employees, lego, sports, dvdrental), and self-authored or WebArena-derived web pages — [arXiv HTML](https://arxiv.org/html/2509.24002v1)
- Tasks run in "mirrored or isolated" sandboxes and "the environment is reset to its original state" after verification — [arXiv HTML](https://arxiv.org/html/2509.24002v1)

MCP-Universe (Salesforce)
- 231 tasks over 6 domains using 11 real-world MCP servers with 133 tools; 84 evaluators — [arXiv HTML](https://arxiv.org/html/2508.14704v1)

LiveMCPBench
- 95 tasks over LiveMCPTool, a deployable collection of 70 MCP servers and 527 tools — [arXiv HTML](https://arxiv.org/html/2508.01780v2)

WorkArena
- Runs against real ServiceNow instances; access is through a gated Hugging Face dataset, ServiceNow/WorkArena-Instances; the repository contains a monitor_pool_usage.py script. WorkArena-L1 has 19,912 instances from 33 atomic tasks; WorkArena++ L2 has 682 tasks — [GitHub repository](https://github.com/ServiceNow/WorkArena)
- Earlier instructions required requesting a personal developer instance and running workarena-install to upload benchmark data (from a search summary of the repository README at an earlier date) — [search summary of repository](https://awesome.ecosyste.ms/projects/github.com%2Fservicenow%2Fworkarena)

ToolSandbox (Apple)
- Python-native execution environment with an LLM-simulated user, the agent, and an environment holding mutable world state and executing tools; 1,032 scenarios — [arXiv HTML](https://arxiv.org/html/2408.04682v1)

### Inferences
- Toolathlon's "Remote" column mixes two different things: services that need the evaluator's own account and hold writable state (GitHub, Google Calendar/Cloud/Forms/Sheets, Hugging Face, Notion, Snowflake, Weights & Biases) and read-only public internet sources (arXiv, Fetch, Yahoo Finance, YouTube, 12306, Scholarly, HowToCook, Playwright). Only the first group needs state reconstruction; the second group creates time-dependence instead. This split is my reading of the table combined with the account registration guide in section 2, not a classification the paper makes.
- For continuing a recorded session mid-way, the database-backed designs (AppWorld, tau-bench, Toolathlon-GYM) are the only ones in which the complete environment state is a file that can be copied; the live-account designs keep part of the state on a third party's servers.
- MCP-Atlas is of limited use as a model for state reconstruction because it deliberately excludes writes.

### Gaps
- MCP-Atlas: the paper does not say which servers need accounts or how Notion, Airtable, MongoDB and Slack content was seeded. The harness repository was not read.
- WorkArena: per-task record creation, teardown and whether instances are a shared hosted pool are not described on the repository front page; the papers (arXiv 2403.07718, 2407.05291) were not read.
- tau2-bench, MCP-Universe, LiveMCPBench: repository-level seeding and reset mechanics were not read; only abstracts and search summaries were used.
- tau-bench: the paper does not describe reset between episodes. That it reloads JSON per episode is likely but unverified here.

## 2. Toolathlon specifically: task layout, container image, remote versus local MCP servers, local deployments, hosted evaluation service, released trajectories

### Takeaway
Toolathlon needs one account each on Google, GitHub, Weights & Biases, Notion, Snowflake and Hugging Face, plus locally deployed Canvas, Poste.io, WooCommerce and kind-based Kubernetes, with each task executed in a container from a published image. A public hosted evaluation service removes the account setup but returns only results and trajectories; it does not expose the environment for arbitrary mid-session continuation.

### Cited Findings

Repository layout and running
- Tasks live in tasks/finalpool and are addressed as finalpool/{taskname}. Top-level directories include configs (global_configs.py, ports_config.yaml, token_key_session.py), deployment, global_preparation, local_binary, scripts, utils and vis_traj; top-level files include main.py, run_parallel.py, eval_client.py, eval_server.py, Dockerfile, DECOUPLED_AGENT_LOOP.md, EVAL_SERVICE_README.md, README_nodocker.md and UpdateLogs_CommonIssues.md — [GitHub README](https://github.com/hkust-nlp/Toolathlon)
- Setup sequence: install_env_minimal.sh, pull_toolathlon_image.sh, deploy_containers.sh, check_installation_containerized.sh, then scripts/run_single_containerized.sh or scripts/run_parallel.sh; the README says to restart the deployed apps before each formal run. Defaults are 5,400 seconds per task and 64K output tokens per turn — [GitHub README](https://github.com/hkust-nlp/Toolathlon)
- The image named in the decoupled-mode command is lockon0927/toolathlon-task-image:1016beta; Docker or Podman is chosen in configs/global_configs.py. Decoupled mode supports agent frameworks toolathlon_default and claude_agent_sdk — [GitHub README](https://github.com/hkust-nlp/Toolathlon)
- Each task has a configuration file listing its MCP servers (fewer than 10); all tools of each selected server are loaded — [arXiv HTML v2](https://arxiv.org/html/2510.25726v2)
- The per-task directory layout (task_config.json, docs/task.md and agent_system_prompt.md, preprocess/main.py, evaluation/main.py, initial_workspace/, groundtruth_workspace/) is documented in the Toolathlon-GYM repository, which states that its task format originates from Toolathlon — [Toolathlon-GYM repository](https://github.com/eigent-ai/toolathlon_gym)

Remote accounts
- The registration guide requires one each of: a Google account (OAuth client saved as configs/gcp-oauth.keys.json, API key and service account via automated_google_setup.sh), GitHub (tokens), Weights & Biases (token), Notion (a new workspace, a duplicated source page, a "Notion Eval Page", and two integration keys of which the evaluation key can see only the evaluation page), Snowflake (account, role, user and an RSA key pair), and Hugging Face (token). Serper appears in a heading without details — [how2register_accounts.md](https://raw.githubusercontent.com/hkust-nlp/Toolathlon/main/global_preparation/how2register_accounts.md)
- Snowflake single-factor authentication stopped working when trial accounts expired, so the code moved to RSA keys on 2025-12-12; the official Notion MCP server changed its authentication on 2025-12-14 — [UpdateLogs_CommonIssues.md](https://raw.githubusercontent.com/hkust-nlp/Toolathlon/main/UpdateLogs_CommonIssues.md)

Local deployments
- Local accounts are created automatically by deploy_containers.sh from configs/users_data.json; all are registered in Canvas and Poste, and accounts 81 to 100 are also registered in WooCommerce with one subsite each — [how2register_accounts.md](https://raw.githubusercontent.com/hkust-nlp/Toolathlon/main/global_preparation/how2register_accounts.md)
- deploy_containers.sh takes a flag enabling plaintext auth in dovecot for poste.io; per-app setup scripts are at deployment/*/scripts/setup.sh; ports_config.yaml covers k8s and canvas resources for multi-instance runs — [GitHub README](https://github.com/hkust-nlp/Toolathlon)
- Without Docker or Podman, install_env.sh installs Node.js, kind, kubectl, Playwright and packages on the host, but "some tasks may fail" because the apps cannot be deployed and kind cannot launch a cluster — [README_nodocker.md](https://raw.githubusercontent.com/hkust-nlp/Toolathlon/main/README_nodocker.md)
- A Notion MCP schema patch (notion-mcp-patches/notion-openapi.json) is bind-mounted over the Notion MCP v1.9.0 schema — [UpdateLogs_CommonIssues.md](https://raw.githubusercontent.com/hkust-nlp/Toolathlon/main/UpdateLogs_CommonIssues.md)

Hosted evaluation service
- Announced 2025-11-28. The server (47.253.6.47, ports 8080 and 8081) holds the MCP accounts and runs each evaluation in Docker. In public mode the client sends its OpenAI-compatible endpoint and key to the server; in private mode the server forwards inference requests over a WebSocket to the client so the key stays local — [EVAL_SERVICE_README.md](https://raw.githubusercontent.com/hkust-nlp/Toolathlon/main/EVAL_SERVICE_README.md)
- One job at a time; 240-minute task timeout; default limits per IP per 24 hours of 180 minutes and 3 submissions; outputs are eval_stats.json, traj_log_all.jsonl and a finalpool/ directory of per-task results; job directories are deleted after 7 days — [EVAL_SERVICE_README.md](https://raw.githubusercontent.com/hkust-nlp/Toolathlon/main/EVAL_SERVICE_README.md)
- Open issues report the service being unavailable (#83, 17 Aug 2026; #89, 7 Sep 2026) — [issue tracker](https://github.com/hkust-nlp/Toolathlon/issues)

Released trajectories
- Toolathlon-Trajectories (2025-12-05) covers gemini-3-pro, claude-4.5-opus, gpt-5.1 and deepseek-v3.2-thinking; Toolathlon-Verified_Trajectories was released 2026-06-30 — [GitHub README](https://github.com/hkust-nlp/Toolathlon)
- The Verified dataset is tar.gz archives (23.1 GB), one per model, each bundling three runs of the 108 tasks with task artifacts and a run-level eval_stats.json, plus DECONTAMINATION.md and decontamination.json. Credentials, endpoint URLs and signed-URL parameters were sanitised. Known issues: stale summary.config_used labels and incomplete AvgTurns coverage — [Hugging Face dataset card](https://huggingface.co/datasets/hkust-nlp/Toolathlon-Verified_Trajectories)
- The dataset card lists 12 models, whereas the README says 8 models at release plus a Muse Spark 1.1 update on 2026-07-09 — [Hugging Face dataset card](https://huggingface.co/datasets/hkust-nlp/Toolathlon-Verified_Trajectories); differs from [GitHub README](https://github.com/hkust-nlp/Toolathlon)
- The repository ships a trajectory viewer: uv run vis_traj/server.py --res_path {dump}/finalpool/ — [GitHub README](https://github.com/hkust-nlp/Toolathlon)

### Inferences
- The trajectories record tool calls and results plus final workspace artifacts, but there is no indication they include snapshots of remote service state (Notion pages, Sheets, Snowflake tables) or of the Canvas, WooCommerce and Poste databases at intermediate steps. Mid-session state would therefore have to be rebuilt by running preprocess and then re-applying the recorded write calls.
- The hosted service cannot be used to continue a session from the middle: its interface accepts a model endpoint and a task list and runs tasks from the start.
- The Docker-free path is explicitly degraded, so on a cluster with only Apptainer the Canvas, WooCommerce, Poste and Kubernetes tasks are the ones at risk. Which tasks those are could be computed from each task_config.json.

### Gaps
- The internal schema of a trajectory record (field names, whether tool results are stored in full or truncated) is not stated on the dataset card; it must be read from the archives or from vis_traj.
- The exact image tag pulled by pull_toolathlon_image.sh and the Canvas, WooCommerce and Poste image versions were not read.
- How "Verified" tasks were verified is not explained on the dataset card; UpdateLogs_CommonIssues.md lists per-task fixes (section 6).

## 3. Toolathlon-GYM specifically: the 25 MCP servers, PostgreSQL schema and mock data, relation to Toolathlon tasks, isolation, and running without Docker

### Takeaway
Toolathlon-GYM replaces Toolathlon's remote accounts and heavyweight apps with MCP servers backed by a single PostgreSQL 15 database loaded from an 8.2 MB dump, and its 503 tasks are new tasks in the Toolathlon format, not ports of the 108 originals. The shipped tooling is Docker-only, but the stack is just one Postgres instance plus Python processes configured by PG* environment variables, so an Apptainer or plain-process port looks feasible though undocumented.

### Cited Findings
- Server names appearing in the repository: filesystem, excel, emails, terminal, word, pptx, pdf-tools, woocommerce, snowflake, canvas, yahoo_finance, youtube, train, notion, google_calendar, google_forms, rail_12306, arxiv-latex, arxiv_local, scholarly, playwright_with_chunk, fetch, howtocook, memory, youtube-transcript. The README states 25 servers but does not give one enumerated list; this list of 25 was assembled from names across the page — [GitHub repository](https://github.com/eigent-ai/toolathlon_gym)
- Six servers are described as database-backed with listed data volumes: canvas (22 courses, 28,865 users, 32,663 enrollments, 206 assignments, 173,912 submissions, 77 quizzes), snowflake (50,000 employees, 20,000 sales orders, 31,588 support tickets), woocommerce (82 products, 150 orders, 50 customers, 396 reviews), yahoo_finance (50 tickers, 3,510 price records), youtube (3 channels, 2 playlists, 135 videos), train (8 trains, 16 routes) — [GitHub repository](https://github.com/eigent-ai/toolathlon_gym)
- playwright_with_chunk and fetch retrieve data from mock local servers — [Eigent blog](https://www.eigent.ai/blog/toolathlon-gym-large-scale-long-horizon-environments-for-tool-use-agents)
- Data sources: Kaggle OULAD (LMS), Kaggle HR Analytics, Yahoo Finance API, Kaggle Amazon product data and DummyJSON — [README](https://raw.githubusercontent.com/eigent-ai/toolathlon_gym/main/README.md)
- docker-compose.yml defines two services: postgres (image postgres:15, container toolathlon_pg, database toolathlon_gym, db/init.sql.gz mounted into /docker-entrypoint-initdb.d, port 5432, pg_isready health check) and toolathlon (image toolathlon-pack:latest built from the repository, PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE, LOCAL_SERVERS_PATH=/opt/local_servers, PYTHON_BIN=/opt/venv/bin/python3), on a bridge network toolathlon_net — [docker-compose.yml](https://raw.githubusercontent.com/eigent-ai/toolathlon_gym/main/docker-compose.yml)
- scripts/run_containerized.sh starts a fresh agent container per task, takes an exclusive file lock "because all tasks share one Postgres database", runs a psycopg2 snippet altering a foreign key on email.sent_log, then runs main.py --task_dir <task> --max_steps <n>; its comments say preprocessing resets shared schema state — [run_containerized.sh](https://raw.githubusercontent.com/eigent-ai/toolathlon_gym/main/scripts/run_containerized.sh)
- The README says that in parallel runs each task gets its own PostgreSQL instance, agent container and Docker network, with concurrency limited by a FIFO semaphore — [README](https://raw.githubusercontent.com/eigent-ai/toolathlon_gym/main/README.md)
- The existence of a table email.sent_log shows at least an email schema in addition to the six listed domains — [run_containerized.sh](https://raw.githubusercontent.com/eigent-ai/toolathlon_gym/main/scripts/run_containerized.sh)
- Task relation: "the task format, evaluation framework, MCP server interfaces, and database schema design all originate from the Toolathlon project"; GYM "extends the original with additional tasks and larger-scale mock data". Tasks use 4 to 8 servers (123, 133, 105, 126 and 16 tasks respectively). Task descriptions avoid brand names ("knowledge base" for Notion) — [Eigent blog](https://www.eigent.ai/blog/toolathlon-gym-large-scale-long-horizon-environments-for-tool-use-agents)
- Outputs go to dumps/ (traj.json, camel_logs/) and benchmark_logs/; model providers are openai_compatible, openai, anthropic and gemini — [GitHub repository](https://github.com/eigent-ai/toolathlon_gym)
- The only listed prerequisites are Docker and Docker Compose; running without Docker is not mentioned — [README](https://raw.githubusercontent.com/eigent-ai/toolathlon_gym/main/README.md)
- The GitHub page reported Apache-2.0 in the sidebar and a LICENSE file, while the README text states no licence — [GitHub repository](https://github.com/eigent-ai/toolathlon_gym); [README](https://raw.githubusercontent.com/eigent-ai/toolathlon_gym/main/README.md)
- Repository activity at time of reading: 27 commits, 153 stars — [GitHub repository](https://github.com/eigent-ai/toolathlon_gym)

### Inferences
- The 503 tasks are new and do not map one-to-one to the 108 Toolathlon tasks: neither source names any original task, and the server set drops GitHub, Google Sheets, Google Cloud, Hugging Face, Weights & Biases, Kubernetes, Git and Google Maps that originals rely on. A recorded Toolathlon trajectory therefore cannot be dropped into GYM unchanged; tool names and schemas may match for shared servers (canvas, woocommerce, emails, notion, google_calendar, snowflake) because interfaces "originate from" Toolathlon, but the seeded data differs, so recorded object IDs and contents will not match.
- Notion, google_calendar, google_forms and emails in GYM must be local mocks too, given the no-external-API claim, most likely further PostgreSQL schemas (email.sent_log supports this for email). The sources do not state the schema names.
- Apptainer or plain-process feasibility: the agent side needs only reachability to Postgres through PG* variables, a Python environment at a known path, and the local server sources; the Docker-specific parts are the network DNS name, docker inspect health check, and docker exec lifecycle. Postgres itself runs as an unprivileged process or under Apptainer with a writable data directory, loading init.sql.gz with psql. Running one Postgres per task on a distinct port or Unix socket would reproduce the parallel isolation. This is inference from the compose file and run script; nobody is documented as having done it.
- The two isolation descriptions (shared database with a global lock in the single-run script; dedicated Postgres per task in the parallel script) are both from primary sources and describe different scripts, not a contradiction, but they mean preprocess must be assumed to mutate shared schemas.

### Gaps
- The actual PostgreSQL schema names and table definitions inside db/init.sql.gz were not inspected.
- How the 503 tasks were authored (human or model-generated) and validated is not stated in the blog or README.
- No published results or validity audit for GYM were found.
- Whether the Dockerfile installs anything that requires root at runtime was not checked.

## 4. How are outcomes verified?

### Takeaway
Final-state checking by task-specific scripts is the dominant method for write-capable benchmarks (Toolathlon, Toolathlon-GYM, AppWorld, tau-bench, MCPMark, MCP-Universe); LLM judges appear where tasks are read-only or answers vary with live data (MCP-Atlas, LiveMCPBench). ToolSandbox is the main example that scores the trajectory of state changes.

### Cited Findings
- Toolathlon: deterministic hand-written scripts check final environment state without LLM judges, either against a static ground-truth snapshot or by re-running a reference workflow that fetches live information (examples: latest NVIDIA shareholder data, real-time train schedules) — [arXiv HTML v2](https://arxiv.org/html/2510.25726v2)
- Toolathlon Notion evaluation uses a separate integration key that can see only the evaluation page — [how2register_accounts.md](https://raw.githubusercontent.com/hkust-nlp/Toolathlon/main/global_preparation/how2register_accounts.md)
- Toolathlon-GYM: evaluation/main.py checks agent outputs against groundtruth_workspace; no human graders or live services — [GitHub repository](https://github.com/eigent-ai/toolathlon_gym)
- AppWorld: a hash-based diff of start and end database states yields added, updated and deleted rows; each task asserts expected changes and permits only allowed changes, which detects collateral damage; about 8 tests per task — [arXiv HTML](https://arxiv.org/html/2407.18901)
- tau-bench: reward is r_action times r_output; r_action is 1 only if the final database exactly equals the unique annotated goal database, and r_output checks required substrings in agent messages. The authors note reward 1 is necessary but not sufficient, since for example a missing user confirmation is not detected — [arXiv HTML](https://arxiv.org/html/2406.12045)
- tau2-bench telecom evaluation includes database checks, communicated-information checks, natural-language assertions, action matching and state assertions (from a secondary summary) — [search summary citing AIS-Bench docs](https://ais-bench-benchmark.readthedocs.io/en/latest/extended_benchmark/agent/tau2_bench.html)
- MCPMark: one programmatic script per task checks final environment state; average 209.8 lines — [arXiv HTML](https://arxiv.org/html/2509.24002v1)
- MCP-Universe: execution-based evaluators only, split into format (4.8%), static (38.1%) and dynamic (57.1%) evaluators, the last fetching real-time ground truth; explicitly rejects LLM-as-judge — [arXiv HTML](https://arxiv.org/html/2508.14704v1)
- MCP-Atlas: claims-based scoring by an LLM judge (Gemini 2.5 Pro in the paper), each claim scored 1, 0.5 or 0, pass at coverage 0.75 or above; judge agreed with majority human vote on 78% of 100 sampled tasks — [arXiv HTML](https://arxiv.org/html/2602.00933v1)
- Scale upgraded the MCP-Atlas judge and added retry handling for transient tool errors in April 2026 — [Scale leaderboard](https://labs.scale.com/leaderboard/mcp_atlas)
- LiveMCPBench: LiveMCPEval LLM judge, 81% agreement with humans reported in v1 — [arXiv abstract v1](https://arxiv.org/abs/2508.01780v1)
- ToolSandbox: Milestones (events that must occur) and Minefields (events that must not; violation zeroes the score) over the trajectory — [arXiv HTML](https://arxiv.org/html/2408.04682v1)
- WorkArena: each task exposes validate(page, chat_messages) returning reward, stop, message and info — [GitHub repository](https://github.com/ServiceNow/WorkArena)

### Inferences
- Final-state evaluators are well suited to mid-session continuation because they do not care how the state was reached: if the prefix is replayed faithfully, the original evaluation script can score the continued run unchanged. Trajectory-based scoring (ToolSandbox) would need the prefix events as well.
- Final-state evaluators are blind to unsafe intermediate actions that are later undone, and to things said rather than done; tau-bench's authors make the equivalent point about missing confirmations. AppWorld's allowed-changes check is the only mechanism found that penalises unrequested side effects by construction.

### Gaps
- How much of Toolathlon's 108 evaluation scripts query remote services at evaluation time versus the local workspace was not counted.

## 5. Has anyone reconstructed mid-session state for these environments, or reported replay divergence?

### Takeaway
No published work was found that resumes a Toolathlon, AppWorld, tau-bench or MCP-benchmark episode from the middle of a recorded trajectory. The nearest evidence is Toolathlon's own change log, which documents the divergence sources one would hit (IDs of copied objects, live data revisions, timezone and date dependence, propagation lag), and general agent-replay papers.

### Cited Findings
- Toolathlon changing IDs: for update-material-inventory, the copied spreadsheet's ID is written to config.json in the agent workspace, meaning the ID differs per run — [UpdateLogs_CommonIssues.md](https://raw.githubusercontent.com/hkust-nlp/Toolathlon/main/UpdateLogs_CommonIssues.md)
- Toolathlon time dependence: language-school was updated to current-year deadlines; search-ca-school is graded against live CSRankings; nvidia-stock-analysis was frozen to a 2026-06-12 snapshot because Yahoo revises historical share counts; game-statistics preprocess and evaluation are aligned to launch date; student-interview needed UTC to +08:00 conversion — [UpdateLogs_CommonIssues.md](https://raw.githubusercontent.com/hkust-nlp/Toolathlon/main/UpdateLogs_CommonIssues.md)
- Toolathlon propagation lag and flakiness: Google Sheets read quotas (retry budget about 110 s), Drive and GCS listing lag, late IMAP delivery, Notion pagination at 100 blocks — [UpdateLogs_CommonIssues.md](https://raw.githubusercontent.com/hkust-nlp/Toolathlon/main/UpdateLogs_CommonIssues.md)
- Toolathlon residual state between runs: apply-phd-email clears the receiver inbox before the run; inventory-sync config persists across retries; canvas-do-quiz had to handle pre-completed quizzes — [UpdateLogs_CommonIssues.md](https://raw.githubusercontent.com/hkust-nlp/Toolathlon/main/UpdateLogs_CommonIssues.md)
- Open Toolathlon issues report Yahoo Finance rate limiting (#94, 23 Sep 2026), external API instability causing non-model failures (#85, 19 Aug 2026) and Notion MCP timeouts (#14, 17 Dec 2025) — [issue tracker](https://github.com/hkust-nlp/Toolathlon/issues)
- AppWorld freezes time with freezegun and can reset each task's database and clock to the start state — [arXiv HTML](https://arxiv.org/html/2407.18901)
- MCP-Atlas requires time-invariant answers at authoring time and acknowledges that real APIs may change, rate-limit or disappear — [arXiv HTML](https://arxiv.org/html/2602.00933v1)
- A GUI-agent paper restores any node of a trajectory tree by replaying the unique root-to-node action prefix on the initial snapshot, and disables drift sources such as automatic updates (from a search summary; paper not read) — [arXiv 2605.29447](https://arxiv.org/pdf/2605.29447)
- Chronicle (17 Sep 2026) records agent runs at non-deterministic boundaries and replays a chosen subset from the record while running the rest live; evaluated on 6 recorded failures with simulated model boundaries, not on these benchmarks — [arXiv 2609.20625](https://arxiv.org/abs/2609.20625)
- An open Toolathlon issue asks for evaluation data in a Harbor-compatible format (#74, 11 Aug 2026); none of the 22 open issues mentions replay or Apptainer — [issue tracker](https://github.com/hkust-nlp/Toolathlon/issues)

### Inferences
- Prefix replay (run preprocess, then re-execute the recorded tool calls up to step t against a fresh environment) is the natural reconstruction method, and the Toolathlon change log predicts where it diverges: server-assigned IDs (spreadsheet, Notion page, Canvas submission, WooCommerce order, email message IDs), timestamps, live external data, and eventual consistency. A replayer would need an ID remapping table from recorded to fresh IDs and a check comparing each fresh tool result with the recorded one.
- In a database-backed environment (GYM, AppWorld) divergence can be reduced further by controlling sequences and the clock, which is not possible with real Google, Notion or Snowflake accounts.
- Read-only calls in the prefix need not be re-executed for state purposes; only writes matter, though the model's context should still show the recorded results.

### Gaps
- No source was found that measures replay divergence rates on Toolathlon or any of the listed benchmarks. This is an absence of evidence from a limited search (one query plus the Toolathlon open issue list), not proof that none exists; closed issues were not visible.
- The GUI-agent and other replay papers surfaced by search were not read in full, and vendor blog claims on replay were discarded as unreliable.

## 6. Licences and known validity problems

### Takeaway
Toolathlon has no licence file as of August 2026 and its Verified trajectories carry restrictive use terms (no training, no public mirroring); Toolathlon-GYM appears to be Apache-2.0. Validity problems are documented for Toolathlon (many grader and ground-truth fixes, evaluator bypasses reported in November 2026), tau-bench and tau2-bench (annotation errors prompting a verified fork), and LLM-judged benchmarks (judge agreement near 80%).

### Cited Findings

Licences
- Toolathlon: issue #87 "Licensing / LICENSE file is missing" (24 Aug 2026) and #97 on the licence of leaderboard results (1 Oct 2026) are open — [issue tracker](https://github.com/hkust-nlp/Toolathlon/issues)
- Toolathlon paper is CC BY 4.0 — [arXiv HTML v2](https://arxiv.org/html/2510.25726v2)
- Toolathlon-Verified_Trajectories: gated; restricted to evaluation, reproducibility, auditing and error analysis; must not be used for training, distillation or retrieval corpora; raw trajectories must not be publicly mirrored; no formal licence stated — [Hugging Face dataset card](https://huggingface.co/datasets/hkust-nlp/Toolathlon-Verified_Trajectories)
- Toolathlon-GYM: Apache-2.0 per the repository sidebar — [GitHub repository](https://github.com/eigent-ai/toolathlon_gym)
- MCPMark component licences: MCP servers MIT (Playwright MCP Apache-2.0); Notion templates may not be redistributed; PostgreSQL sample databases under mixed licences — [arXiv HTML](https://arxiv.org/html/2509.24002v1)
- AppWorld: paper promises a permissive licence for engine and benchmark — [arXiv HTML](https://arxiv.org/html/2407.18901)
- MCP-Atlas paper CC BY 4.0; public 500-task subset on Hugging Face — [arXiv HTML](https://arxiv.org/html/2602.00933v1); [Hugging Face dataset](https://huggingface.co/datasets/ScaleAI/mcp-atlas)

Validity problems
- Toolathlon grader and ground-truth corrections: k8s-mysql ground truth changed from 3 drivers to 104; wandb-shortest-length, latex-prompt-box, reimbursement-form-filler, merge-hf-datasets, notion-movies and music-analysis graders were loosened or fixed; several task scopes were reversed more than once (sync-todo-to-readme, email-paper-homepage, imagenet adopted 2026-06-25; identify-all-songs, privacy-desensitization, detect-revised-terms) — [UpdateLogs_CommonIssues.md](https://raw.githubusercontent.com/hkust-nlp/Toolathlon/main/UpdateLogs_CommonIssues.md)
- Toolathlon evaluator integrity issues opened 7 Nov 2026: an agent-written /workspace/tasks/__init__.py makes the containerised evaluator exit 0 (#100); a TypeError fails a correct answer in experiments-recordings (#101); task_artifact_guard misses four protected files and two answer keys (#102) — [issue tracker](https://github.com/hkust-nlp/Toolathlon/issues)
- Toolathlon includes an old gt_record.md with a stale 2025 deadline that must not be used — [UpdateLogs_CommonIssues.md](https://raw.githubusercontent.com/hkust-nlp/Toolathlon/main/UpdateLogs_CommonIssues.md)
- Toolathlon scores shifted greatly between releases: the paper's best was Claude-4.5-Sonnet at 38.6%, while the Verified dataset card reports Pass@1 around 76% for the top models — [arXiv abstract](https://arxiv.org/abs/2510.25726); [Hugging Face dataset card](https://huggingface.co/datasets/hkust-nlp/Toolathlon-Verified_Trajectories)
- tau2-bench: Amazon's tau2-bench-verified says the original had tasks where "task definitions, expected actions, and evaluation criteria did not properly align with the stated policies or database contents" — [tau2-bench-verified repository](https://github.com/amazon-agi/tau2-bench-verified)
- tau-bench authors' stated limitations: simulated users make errors, prompts were tuned against a gpt-4-turbo agent, and rule-based reward misses policy requirements — [arXiv HTML](https://arxiv.org/html/2406.12045)
- MCP-Atlas: LLM judge agreement with humans 78%; possible bias toward verbose or terse answers; dependence on live API stability — [arXiv HTML](https://arxiv.org/html/2602.00933v1)
- MCPMark: high run-to-run variance (pass^4 much lower than pass@1) — [arXiv HTML](https://arxiv.org/html/2509.24002v1)

### Inferences
- With no licence on the Toolathlon repository, reuse of its task files, preprocess scripts and custom MCP servers in a derived environment is legally unclear; GYM's Apache-2.0 code (which says it derives its format and interfaces from Toolathlon) is the lower-risk base. Whether GYM's Apache licence validly covers material derived from an unlicensed upstream is an open question I could not resolve.
- The Verified trajectory terms permit auditing and error analysis but not redistribution of raw trajectories, which constrains what a derived benchmark can publish (continuation prefixes are raw trajectory content).
- The jump from about 39% to about 76% top score combines model progress and benchmark fixes; the sources do not separate the two.
- Issue #100 shows the evaluator trusts the agent-writable workspace, a risk any continuation harness that reuses Toolathlon's evaluation scripts inherits.

### Gaps
- AppWorld, tau-bench, tau2-bench, WorkArena, MCP-Universe, LiveMCPBench and ToolSandbox repository licence files were not read directly.
- The Toolathlon-GYM licence conflict (sidebar says Apache-2.0, README silent) was not resolved by opening the LICENSE file.
- No independent audit of Toolathlon-GYM task validity was found.
- The dataset card summary gave Kimi K3 a Pass@1 (76.54%) higher than the model it called highest (Claude Opus 4.8, 76.23%); the card's own table should be checked before quoting either number.
