# Environment engineering in Graham Neubig's group and collaborators (OpenHands, TheAgentCompany, WebArena, VisualWebArena, SWE-bench family)

Research date: 2026-11-11. Each finding names its source; dates of sources are given where the source carries one. "Read in primary source" means a paper, official docs page, repository file or GitHub API response fetched during this session. Anything else is marked as secondary or as inference. GitHub repository metadata (licence, last push, issue titles, PR state) was read from the public GitHub API on 2026-11-11.

## How is each environment built (images, services, data seeding) and how is it reset between tasks?

### Takeaway
All of these systems ship state as pre-populated Docker images and reset by destroying and re-creating containers from those images; none uses a finer-grained reset than "container back to image" except TheAgentCompany's per-service reset endpoints, which do the same thing per service. The SWE-bench family avoids reset entirely by giving every task its own disposable container built from a three-layer image stack.

### Cited Findings

OpenHands runtime (V0 architecture docs, undated page; example tags reference v0.9.x)
- The user supplies a base Docker image; OpenHands builds an "OH runtime image" on top that adds the OpenHands code and runtime client, launches a container, and an `ActionExecutor` inside the container initialises a bash shell and plugins. The backend sends actions and receives observations over a REST API. — [OpenHands runtime architecture](https://docs.openhands.dev/usage/architecture/runtime)
- The action execution server lives at `openhands/runtime/action_execution_server.py`, the client at `openhands/runtime/impl/action_execution/action_execution_client.py`; runtime implementations are under `openhands/runtime/impl/{docker,local,remote}`. — [OpenHands runtime architecture](https://docs.openhands.dev/usage/architecture/runtime)
- Runtime images carry three tags: a versioned tag `oh_v{version}_{base_image}`, a lock tag `oh_v{version}_{16-digit hash of base image name + pyproject.toml + poetry.lock}`, and a source tag that appends a hash of the OpenHands source directory. Build reuses the most specific existing tag (no rebuild, then build on lock tag, then on versioned tag, then full build). — [OpenHands runtime architecture](https://docs.openhands.dev/usage/architecture/runtime)
- Plugins (Jupyter via Kernel Gateway, VS Code, Agent Skills) are initialised when the runtime starts. Volumes support bind mounts, named volumes and a copy-on-write overlay mode (`:overlay`, requires `SANDBOX_VOLUME_OVERLAYS`). — [OpenHands runtime architecture](https://docs.openhands.dev/usage/architecture/runtime)
- The newer OpenHands Software Agent SDK (arXiv 2511.03690, submitted 2025-11-05, revised 2026-04-22, MLSys 2026) describes "seamless local-to-remote execution portability", integrated REST/WebSocket services, native sandboxed execution and lifecycle control, and reports "negligible event-sourcing overhead". — [OpenHands Software Agent SDK abstract](https://arxiv.org/abs/2511.03690)
- The All-Hands-AI/OpenHands GitHub path now returns "Moved Permanently"; the SDK and benchmark harness live at OpenHands/software-agent-sdk and OpenHands/benchmarks (both pushed in November 2026). — [GitHub API: All-Hands-AI/OpenHands](https://api.github.com/repos/All-Hands-AI/OpenHands); [OpenHands/software-agent-sdk](https://github.com/OpenHands/software-agent-sdk); [OpenHands/benchmarks](https://github.com/OpenHands/benchmarks)

TheAgentCompany (arXiv 2412.14161, v1 2024-12-18, v3 2025-09-10)
- 175 tasks in a simulated small software company. Four self-hosted services: GitLab (repositories and wiki), ownCloud (documents and collaborative editing), Plane (issues, sprints, roadmaps), RocketChat (internal messaging). Data comes from real software-project data plus data manually curated by co-authors. — [TheAgentCompany paper v3](https://arxiv.org/html/2412.14161v3)
- Setup is one script (`setup.sh` from release `setup-script-20241208` in the `the-agent-company-backup-data` repo) that checks Docker and Compose, pulls server images, starts all servers and waits for health. An `api-server` on port 2999 is the controller that launches the other services. Plane is deployed from a compose file in the `TheAgentCompany/plane` fork; RocketChat depends on a `bitnami/mongodb` container. — [TheAgentCompany SETUP.md](https://github.com/TheAgentCompany/TheAgentCompany/blob/main/docs/SETUP.md)
- Service ports and seeded credentials: GitLab 8929, ownCloud 8092, Plane 8091, RocketChat 3000; the benchmark assumes all are reachable at hostname `the-agent-company.com`, which the task init script writes into `/etc/hosts`. — [TheAgentCompany servers/README.md](https://github.com/TheAgentCompany/TheAgentCompany/blob/main/servers/README.md)
- Each of the 175 tasks is its own Docker image. A run is: start the task container (`--network host` when servers are local), run `/utils/init.sh` (resets data in the services the task depends on and waits for health checks; "up to 10 minutes"), have the agent follow `/instruction/task.md`, then run `/utils/eval.py`. — [TheAgentCompany EVALUATION.md](https://github.com/TheAgentCompany/TheAgentCompany/blob/main/docs/EVALUATION.md)
- Reset is implemented as HTTP endpoints on the api-server: `POST /api/reset-owncloud`, `/api/reset-rocketchat`, `/api/reset-plane`, `/api/reset-gitlab`, plus `GET /api/healthcheck/{owncloud,gitlab,rocketchat,plane,redis,sotopia}`. The GitLab and ownCloud resets are synchronous; RocketChat and Plane resets are launched asynchronously. — [api-server.py](https://github.com/TheAgentCompany/TheAgentCompany/blob/main/servers/api-server/api-server.py)
- The reset targets in the Makefile do `docker compose -p theagentcompany stop <svc>`, `rm -f <svc>`, `up <svc> -d` for GitLab, ownCloud (with `owncloud-collabora`) and the Sotopia Redis stack (`redis-stack`, `redis-stack-npc-data-population`); RocketChat reset runs `/rocketchat/restore.sh`. — [API_MAKEFILE](https://github.com/TheAgentCompany/TheAgentCompany/blob/main/servers/api-server/API_MAKEFILE)

WebArena (arXiv 2307.13854, v1 2023-07-25, v4 2024-04-16)
- 812 tasks from 241 templates. Sites: OneStopShop on Adobe Magento with about 90k products from the WebShop dump; a Postmill forum with 95 subreddits, 127,390 posts and 661,781 users; GitLab with 300 repositories and over 1,000 accounts; the Magento admin portal with sample data; OpenStreetMap limited to the northeast US; offline English Wikipedia via Kiwix. — [WebArena paper v4](https://arxiv.org/html/2307.13854v4)
- Each website ships as a self-contained Docker image holding code, database and data; reset is stopping containers and restarting from the original images, described as taking seconds to about a minute. Agents are logged in through pre-cached cookies. — [WebArena paper v4](https://arxiv.org/html/2307.13854v4)
- The recommended deployment is a public AWS AMI (`ami-08a862bf98e3bd7aa`, us-east-2) on a t3a.xlarge with a 1000 GB EBS volume. Sites load from image tarballs (`shopping_final_0712`, `shopping_admin_final_0719`, `postmill-populated-exposed-withimg`, `gitlab-populated-final-port8023`), Kiwix serves `wikipedia_en_all_maxi_2022-05.zim`, and the map is the OpenStreetMap website under docker compose. Images are mirrored on Google Drive, archive.org and a CMU server. — [WebArena environment_docker README](https://github.com/web-arena-x/webarena/blob/main/environment_docker/README.md)
- After start, Magento needs its base URL rewritten in config and database and the cache flushed; GitLab needs at least 5 minutes to boot, then `external_url` set and `gitlab-ctl reconfigure`. — [WebArena environment_docker README](https://github.com/web-arena-x/webarena/blob/main/environment_docker/README.md)
- The documented reset is to run it after evaluating the 812 examples: stop and remove the shopping_admin, forum, gitlab and shopping containers and re-create them from the images. — [WebArena environment_docker README](https://github.com/web-arena-x/webarena/blob/main/environment_docker/README.md)

VisualWebArena (arXiv 2401.13649)
- 910 tasks from 314 templates across Classifieds, Shopping and Reddit. Classifieds is new, built on OSClass with 65,955 listings scraped from Craigslist over three weeks, PII redacted with scrubadub. Shopping and Reddit are inherited from WebArena. — [VisualWebArena paper](https://arxiv.org/html/2401.13649)
- Setup follows `environment_docker/README.md`; a pre-installed AMI contains all VWA and WebArena sites; `prepare.sh` generates auto-login cookies; the Classifieds site has a reset token (default `4b61655535e7ed388f0d40a93600254c`) tied to its `docker-compose.yml`. — [VisualWebArena README](https://github.com/web-arena-x/visualwebarena/blob/main/README.md)

SWE-bench harness
- Images are built in three layers: one base image, about 60 environment images (Python environments per configuration), and one instance image per task. — [SWE-bench Docker setup guide](https://www.swebench.com/SWE-bench/guides/docker_setup/)
- Each evaluation applies the predicted patch inside a container, runs the repository's tests, and writes per-instance `report.json`, `test_output.txt`, `run_instance.log`, `eval.sh` and `patch.diff` under `logs/evaluation/<run_id>/<model>/<instance_id>/`. — [SWE-bench evaluation guide](https://www.swebench.com/SWE-bench/guides/evaluation/)

SWE-bench Multimodal (arXiv 2410.03859)
- 619 instances from 17 JavaScript repositories (517 test, about 100 dev). The authors added Node.js and Chrome to the SWE-bench containers and wrote per-repository install and test scripts, averaging about ten hours of manual work per repository. Environments worked for 679 of 1,478 candidates. — [SWE-bench Multimodal paper](https://arxiv.org/html/2410.03859)

SWE-Gym (arXiv 2412.21139, v1 2024-12-30, ICML 2025; Neubig is a co-author)
- 2,438 validated Python instances from 11 repositories, cut down from 64,689 raw instances over 358 repositories. Environments were configured semi-manually per instance from requirements files, CI scripts and docs: about 200 human annotation hours and about 10,000 CPU core hours of validation. Pre-built per-instance Docker images total 6 TB. — [SWE-Gym paper v2](https://arxiv.org/html/2412.21139v2)
- Trajectories and unit tests were run in parallel on the OpenHands remote runtime. — [SWE-Gym paper v2](https://arxiv.org/html/2412.21139v2)

SWE-rebench (arXiv 2505.20411, v1 2025-05-26, NeurIPS 2025; Nebius, not Neubig's group)
- Fully automated: about 450,000 PRs mined from over 30,000 repositories; Qwen2.5-72B-Instruct generates up to three candidate install recipes per task with an LLM correction pass over error logs; a working recipe was found for 31% of repositories; result is 21,336 tasks from 3,468 repositories. — [SWE-rebench paper v2](https://arxiv.org/html/2505.20411v2)
- Builds run on TractoAI with buildah and tmpfs, an internal image registry and PyPI/APT mirrors; dependency versions are frozen with `pip freeze` and `conda env export` after a successful setup. — [SWE-rebench paper v2](https://arxiv.org/html/2505.20411v2)

### Inferences
- Reset granularity is the main design difference. SWE-bench style tasks are hermetic (one container per task, no shared state), while WebArena and TheAgentCompany share long-lived services across tasks and must restore them. A handoff benchmark that needs concurrent, independent runs will find the per-task-container model much easier to parallelise than the shared-service model.
- TheAgentCompany's reset for GitLab and ownCloud is container re-creation from a pre-seeded image, so the seeded state must live in the image rather than in a volume. This is an inference from the Makefile targets; I did not read the compose file's volume definitions.
- WebArena's README frames reset as something done after a full pass over 812 tasks, not per task, so state-changing tasks can affect later tasks within one pass unless the runner resets more often.

### Gaps
- The exact contents of TheAgentCompany's `/utils/init.sh`, `restore.sh` and the `reset-plane` target were not read, so the Plane and RocketChat restore mechanics (database dump restore versus container re-creation) are unconfirmed.
- VisualWebArena's per-task reset behaviour (which tasks require reset, and the Classifieds reset endpoint) is not described in the README or the portion of the paper that was read.
- OpenHands V1 SDK internals (agent-server image contents, how workspaces are torn down) were only read at abstract and one-guide level.
- SWE-bench Verified's own environment changes could not be read from OpenAI's post (HTTP 403).

## How is mid-run state captured, snapshotted or reconstructed, if at all?

### Takeaway
None of the benchmark environments reviewed documents a mid-task snapshot or restore facility; state exists only as "initial image" and "whatever the live containers hold now". The closest reusable pieces are OpenHands' event-sourced conversation state and trajectory logs, and TheAgentCompany's requirement that a full trajectory be supplied to the grader.

### Cited Findings
- The OpenHands SDK paper reports "negligible event-sourcing overhead" and "lifecycle control" for sandboxed execution, indicating conversation state is kept as an event log. — [OpenHands Software Agent SDK abstract](https://arxiv.org/abs/2511.03690)
- The OpenHands runtime client tracks sandbox state such as the working directory and loaded plugins, and supports copy-on-write overlay mounts with per-container upper and work directories. — [OpenHands runtime architecture](https://docs.openhands.dev/usage/architecture/runtime)
- The ApptainerWorkspace guide does not mention pause, resume or snapshot features. — [OpenHands Apptainer sandbox guide](https://docs.openhands.dev/sdk/guides/agent-server/apptainer-sandbox.md)
- TheAgentCompany's grader takes an optional `--trajectory_path`; the trajectory "must record every step the examinee took" and is used for partial credit. Evaluation is not time-limited. — [TheAgentCompany EVALUATION.md](https://github.com/TheAgentCompany/TheAgentCompany/blob/main/docs/EVALUATION.md)
- WebArena and TheAgentCompany describe only whole-environment reset to the initial image. — [WebArena paper v4](https://arxiv.org/html/2307.13854v4); [API_MAKEFILE](https://github.com/TheAgentCompany/TheAgentCompany/blob/main/servers/api-server/API_MAKEFILE)
- SWE-bench results are cached by `run_id` and `instance_id`; the harness state per instance is the applied `patch.diff` plus logs. — [SWE-bench evaluation guide](https://www.swebench.com/SWE-bench/guides/evaluation/)
- WebArena-Verified evaluates offline from recorded HAR network traces, so a completed run can be re-scored without a live environment. — [WebArena-Verified docs](https://servicenow.github.io/webarena-verified/latest/)
- A WebArena issue opened 2026-10-20 asks about "provenance and evaluator metadata for released WebArena / VisualWebArena trajectories", and remains open. — [webarena issue 278](https://github.com/web-arena-x/webarena/issues/278)

### Inferences
- For coding tasks, mid-run state is cheap to reconstruct: the environment is the instance image plus a working-tree diff, so a handoff point can be materialised by starting the instance image and applying the diff captured at step t (plus any installed packages, which a diff will not capture).
- For service-backed environments (GitLab, RocketChat, Magento), reconstructing step-t state means either replaying the first agent's actions against a fresh reset, or committing/checkpointing the service containers and their databases at step t. Neither path is provided by these projects. Replay is fragile where services are nondeterministic (timestamps, LLM-backed colleagues); container commit is heavy for images of this size.
- TheAgentCompany's simulated colleagues hold conversational state in Redis (the reset recreates `redis-stack` and an NPC data population container), so a faithful mid-run snapshot would need to include that store as well as the four services. This is inferred from the Makefile service names.
- OpenHands' event log is the most directly useful artefact for a takeover benchmark: it is the format in which a second agent could be given the first agent's history. Whether the SDK exposes resume-from-event-N against a restored workspace was not confirmed.

### Gaps
- No primary source was found that documents snapshotting (docker commit, CRIU, VM snapshots, database dumps at step t) in any of these projects. This is an absence in what was read, not proof that no such tooling exists.
- OpenHands SDK conversation persistence, pause/resume and replay APIs were not read in the SDK docs; only the abstract was fetched.

## How are outcomes verified: final environment state, tests, checkpoints, or action logs? Any LLM judges?

### Takeaway
Verification splits three ways: unit tests on final repository state (SWE-bench family), programmatic checks of final site state plus string matching on answers (WebArena family), and per-task checkpoint scripts over state, chat history and trajectory (TheAgentCompany). LLM judges appear in all of the web and workplace benchmarks, and the later WebArena-Verified effort removed them specifically because they were a source of error.

### Cited Findings
- SWE-bench: the harness applies the model patch, runs repository tests and reports resolved or unresolved; errors mean no `report.json` was produced. — [SWE-bench evaluation guide](https://www.swebench.com/SWE-bench/guides/evaluation/)
- SWE-bench Multimodal: fail-to-pass and pass-to-pass tests as in SWE-bench; 69 instances (Chart.js, openlayers) use pixel-level comparison of rendered screenshots against 273 reference screenshots. — [SWE-bench Multimodal paper](https://arxiv.org/html/2410.03859)
- SWE-Gym validates instances by checking that the gold patch passes more unit tests than the original code; unit tests are the reward signal. — [SWE-Gym paper v2](https://arxiv.org/html/2412.21139v2)
- SWE-rebench requires that at least one test fails before the solution patch, that initially failing tests pass afterwards, and that initially passing tests still pass; a fine-tuned Qwen2.5-72B labels task quality with 81% accuracy on complexity, 67% on test-patch correctness and 79% on issue clarity. — [SWE-rebench paper v2](https://arxiv.org/html/2505.20411v2)
- WebArena: information answers use `exact_match`, `must_include`, or `fuzzy_match` judged by gpt-4-0613 (82 examples; agreed with humans on 39 of 40 checked). State-changing tasks use locators that fetch state by database query, site API or JavaScript element selection, then apply the same matchers. Unachievable tasks expect "N/A". — [WebArena paper v4](https://arxiv.org/html/2307.13854v4)
- VisualWebArena adds `must_exclude`, `fuzzy_match` judged by GPT-4-Turbo, `eval_vqa` using BLIP-2-T5XL, and `eval_fuzzy_image_match` using SSIM against a threshold. Rewards are binary. — [VisualWebArena paper](https://arxiv.org/html/2401.13649)
- TheAgentCompany: each task has checkpoints with points. Full completion is 1 only if all checkpoints pass; the partial score is 0.5 x (points awarded / total) + 0.5 x full completion. Evaluators are mostly deterministic Python functions over environment state, colleague interactions or the trajectory; 51 tasks (29%) use LLM-based evaluators, with deterministic keyword matching first and the LLM as fallback. — [TheAgentCompany paper v3](https://arxiv.org/html/2412.14161v3)
- TheAgentCompany encrypts `/utils/evaluator.py` in each task image so the agent cannot read it; `/utils/eval.py` decrypts it with a `DECRYPTION_KEY` that is published in the docs. The environment LLM (graders and colleagues) is configured separately from the agent LLM; baselines used claude-3-5-sonnet-20241022, and submitters are asked to report the environment model. — [TheAgentCompany EVALUATION.md](https://github.com/TheAgentCompany/TheAgentCompany/blob/main/docs/EVALUATION.md)
- Simulated colleagues are Sotopia characters with profiles (name, role, responsibilities, project affiliations), backed by Claude 3.5 Sonnet. — [TheAgentCompany paper v3](https://arxiv.org/html/2412.14161v3)
- WebArena-Verified (ServiceNow, NeurIPS 2025; docs report version 2.0.0) re-audited every task, reference answer and evaluator, removed LLM-as-judge and substring matching in favour of type-aware normalisation, requires a structured agent response (`task_type`, `status`, `retrieved_data`, `error_details`), and scores from HAR network traces. It defines a 258-task Hard subset. — [WebArena-Verified docs](https://servicenow.github.io/webarena-verified/latest/)
- The WebArena-Verified paper abstract (as returned by search, not fetched in full) says it audits all 812 tasks, verifies backend state for state-changing tasks, and reduces false negatives by about 11% on a baseline agent. — [WebArena Verified, NeurIPS 2025](https://neurips.cc/virtual/2025/124576)

### Inferences
- TheAgentCompany's checkpoint model is the closest precedent for scoring a handoff: checkpoints can reward intermediate milestones and behaviours (did the agent ask the right colleague) rather than only final state, and the trajectory is a first-class grader input.
- The evaluator encryption in TheAgentCompany is obfuscation rather than security, since the key is public and the decryption code sits in the same container; it only prevents accidental reads.
- The environment LLM is a hidden variable in TheAgentCompany scores: both colleague behaviour and 29% of graders depend on it, so results are only comparable when the environment model is held fixed.

### Gaps
- OpenAI's description of how SWE-bench Verified was annotated could not be fetched directly (HTTP 403). Search results report 93 developers annotating 1,699 samples to produce 500, each sample labelled three times; the 38.3% underspecified and 61.1% unfair-test figures were found only in a secondary source. — [OpenAI post (not fetched)](https://openai.com/index/introducing-swe-bench-verified/); [Kili Technology blog (secondary)](https://kili-technology.com/blog/data-story-ai-benchmark-audit-swe-bench-pro-verified)
- Timeout values in the SWE-bench harness and the schema of TheAgentCompany's result JSON were not found in the pages read.

## What can run without Docker (Apptainer/Singularity, enroot, Modal, remote runtimes, Kubernetes) on an HPC cluster?

### Takeaway
OpenHands is the only project here with first-party Apptainer support: an `ApptainerWorkspace` in the SDK (merged December 2025) and an Apptainer path for SWE-bench scoring and image use in its benchmarks repository (merged June 2026). SWE-bench itself offers Modal and a hosted `sb-cli` instead of local Docker; the multi-service web and workplace environments have no documented non-Docker path.

### Cited Findings
- OpenHands SDK `ApptainerWorkspace` takes `server_image`, `base_image` or an existing `sif_file`, plus `host_port` and `platform`; needs no root and no daemon; caches SIF files in `~/.cache/apptainer/`; uses host networking; can use the pre-built `ghcr.io/openhands/agent-server:latest-python` image or build from a base image (first build "several minutes"; needs internet). The docs rate HPC compatibility as excellent for Apptainer and limited for Docker. — [OpenHands Apptainer sandbox guide](https://docs.openhands.dev/sdk/guides/agent-server/apptainer-sandbox.md)
- The PR "Add ApptainerWorkspace implementation for rootless container support" was opened 2025-10-24 and merged 2025-12-29. — [software-agent-sdk PR 892](https://github.com/OpenHands/software-agent-sdk/pull/892)
- OpenHands/benchmarks merged "Add Apptainer SWE-bench evaluation" on 2026-06-16 and "Add SWE-bench Apptainer image builds and runtime wiring" on 2026-06-22. — [benchmarks PR 743](https://github.com/OpenHands/benchmarks/pull/743); [benchmarks PR 745](https://github.com/OpenHands/benchmarks/pull/745)
- Per search-result summaries of those PRs (read through a mirror, not the PR pages themselves): an `--apptainer` flag scores SWE-bench predictions without Modal or Docker using writable Apptainer sandboxes built from the official instance images; images are built on a Docker-capable machine and pushed to a registry, with a local sandbox build as fallback. — [benchmarks PR 743](https://github.com/OpenHands/benchmarks/pull/743); [benchmarks PR 745](https://github.com/OpenHands/benchmarks/pull/745)
- Other SDK workspace types are `DockerWorkspace`, a remote API-based sandbox, and a non-sandboxed local workspace. — [OpenHands Apptainer sandbox guide](https://docs.openhands.dev/sdk/guides/agent-server/apptainer-sandbox.md)
- The legacy V0 runtime list is Docker (default), OpenHands Remote (cloud, beta, for parallel execution), Local (explicitly without Docker), and third-party E2B, Modal, Runloop and Daytona runtimes installed with `openhands-ai[third_party_runtimes]` and supported by their vendors. Kubernetes is not mentioned on that page. — [OpenHands V0 runtimes overview](https://docs.openhands.dev/openhands/usage/v0/runtimes/V0_overview.md)
- SWE-bench supports Modal evaluation (`pip install modal swebench[modal]`, `--modal true`, optional `--parallelism`) and hosted evaluation through `sb-cli` (`sb submit --predictions <path>`). — [SWE-bench evaluation guide](https://www.swebench.com/SWE-bench/guides/evaluation/)
- SWE-rebench builds images with buildah rather than a Docker daemon. — [SWE-rebench paper v2](https://arxiv.org/html/2505.20411v2)
- TheAgentCompany requires Docker and Docker Compose, mounts the Docker socket into the api-server container (`chmod 666 /var/run/docker.sock`) and uses host networking. — [TheAgentCompany SETUP.md](https://github.com/TheAgentCompany/TheAgentCompany/blob/main/docs/SETUP.md)
- WebArena is documented only as an AWS AMI or locally loaded Docker tarballs. — [WebArena environment_docker README](https://github.com/web-arena-x/webarena/blob/main/environment_docker/README.md)

### Inferences
- SWE-bench style single-container tasks are the easy case on HPC: instance images can be pulled as SIF and run rootless, and the OpenHands benchmarks repository already does this.
- TheAgentCompany is the hard case. Its controller drives `docker compose` through a mounted Docker socket to reset services, so the reset path itself assumes a Docker daemon. Running it under Apptainer would mean replacing the api-server's reset logic and starting GitLab, ownCloud, Plane (itself a multi-container compose stack), RocketChat, MongoDB and Redis as separate Apptainer instances with writable overlays. A more practical layout is to host the services on a Docker-capable VM and run only the agent sandbox on the cluster, which the `SERVER_HOSTNAME` parameter in the task init script appears designed to allow.
- WebArena's Magento, GitLab and Postmill images bundle their own databases, need post-start reconfiguration by `docker exec`, and write into their own filesystems; under Apptainer each would need a writable overlay or sandbox directory. Nothing in the sources read indicates anyone in these projects supports that.
- The Apptainer docs note monolithic SIF images and slower builds than Docker's layered builds, which matters at SWE-bench scale (thousands of instance images).

### Gaps
- No source was found on enroot or Kubernetes support for any of these environments. OpenHands' remote runtime is described only as a cloud service; its backing infrastructure was not documented in the pages read.
- The Apptainer PR details were read through search summaries of a third-party mirror; only title, state and merge dates were confirmed through the GitHub API.
- No source was found describing WebArena or TheAgentCompany run under Apptainer, Podman or Singularity.

## What are the licences, hardware and time costs, and known validity or reproducibility problems (flaky sites, state leakage, broken tasks)?

### Takeaway
Code licences are permissive (MIT or Apache-2.0) throughout, but the costs are substantial: 1 TB disks for WebArena, 120 GB to 2 TB for SWE-bench depending on cache level, 6 TB of images for SWE-Gym, and thousands of person-hours for TheAgentCompany. Every benchmark has documented validity problems, and two (WebArena, SWE-bench) have needed third-party "Verified" re-audits.

### Cited Findings

Licences (GitHub API, 2026-11-11)
- TheAgentCompany MIT; webarena Apache-2.0; visualwebarena MIT; SWE-bench MIT; OpenHands software-agent-sdk MIT; OpenHands benchmarks MIT; SWE-Gym Apache-2.0; ServiceNow webarena-verified Apache-2.0; SWE-rebench's SWE-bench fork MIT. — [TheAgentCompany](https://github.com/TheAgentCompany/TheAgentCompany); [webarena](https://github.com/web-arena-x/webarena); [visualwebarena](https://github.com/web-arena-x/visualwebarena); [SWE-bench](https://github.com/SWE-bench/SWE-bench); [software-agent-sdk](https://github.com/OpenHands/software-agent-sdk); [benchmarks](https://github.com/OpenHands/benchmarks); [SWE-Gym](https://github.com/SWE-Gym/SWE-Gym); [webarena-verified](https://github.com/ServiceNow/webarena-verified); [SWE-rebench fork](https://github.com/SWE-rebench/SWE-bench-fork)

Hardware and time
- TheAgentCompany: at least 30 GB disk for the services; baselines ran on EC2 t3.2xlarge; first setup takes a few minutes to about 30 minutes; per-task init up to 10 minutes. — [TheAgentCompany SETUP.md](https://github.com/TheAgentCompany/TheAgentCompany/blob/main/docs/SETUP.md); [TheAgentCompany EVALUATION.md](https://github.com/TheAgentCompany/TheAgentCompany/blob/main/docs/EVALUATION.md)
- TheAgentCompany build effort: about 20 contributors over roughly two months, around 3,000 person-hours; some tasks took over 10 hours each. Best reported agent (Gemini 2.5 Pro) averaged about 27.2 steps and about $4.2 per task. — [TheAgentCompany paper v3](https://arxiv.org/html/2412.14161v3)
- WebArena: t3a.xlarge with 1000 GB EBS; self-hosting the map backend needs a second such instance, a download of about 180 GB, and 60 to 90 minutes of setup. — [WebArena environment_docker README](https://github.com/web-arena-x/webarena/blob/main/environment_docker/README.md)
- VisualWebArena captioning baselines need about 12 GB of GPU memory for BLIP-2-T5XL. — [VisualWebArena README](https://github.com/web-arena-x/visualwebarena/blob/main/README.md)
- SWE-bench: at least 120 GB free disk, 16 GB RAM, 8 CPUs; `--cache_level env` uses about 100 GB, `instance` about 2,000 GB; recommended workers below `min(0.75 * cpu_count, 24)`. — [SWE-bench Docker setup guide](https://www.swebench.com/SWE-bench/guides/docker_setup/)
- SWE-Gym: 6 TB of images, about 200 human hours and 10,000 CPU core hours. — [SWE-Gym paper v2](https://arxiv.org/html/2412.21139v2)
- SWE-bench Multimodal: about ten hours of manual setup per repository. — [SWE-bench Multimodal paper](https://arxiv.org/html/2410.03859)

Known validity and reproducibility problems
- WebArena open issues include "Indeterminism in shopping-admin best-seller report leads to very flaky eval" (2024-02-11), "Efficient method to restart the websites" (2025-05-12), "AMI aws not working" (2025-02-27), "Could I do multi-thread evaluation?" (2024-07-21) and "Sorting issue for task id 49" (2024-04-24). The repository had 106 open issues on 2026-11-11. — [webarena issue 100](https://github.com/web-arena-x/webarena/issues/100); [webarena issue 206](https://github.com/web-arena-x/webarena/issues/206); [webarena issue 200](https://github.com/web-arena-x/webarena/issues/200); [webarena issue 161](https://github.com/web-arena-x/webarena/issues/161); [webarena issue 135](https://github.com/web-arena-x/webarena/issues/135)
- WebArena's README carries a GitLab 502 recovery procedure (remove `postmaster.pid`, `pg_resetwal -f`, restart). — [WebArena environment_docker README](https://github.com/web-arena-x/webarena/blob/main/environment_docker/README.md)
- With the unachievable-task hint, GPT-4 wrongly judged 54.9% of feasible WebArena tasks impossible. — [WebArena paper v4](https://arxiv.org/html/2307.13854v4)
- A 2026 paper auditing five benchmarks reports, per its search-result summary, 21.7% false negatives from WebArena's answer matching (not fetched; treat as secondary). — [How Benchmarks Mis-Score Computer-Use Agents](https://arxiv.org/pdf/2607.28367)
- visualwebarena was last pushed 2024-11-09. — [visualwebarena](https://github.com/web-arena-x/visualwebarena)
- TheAgentCompany issues include "Rocketchat disappearing accounts" (2025-07-21, closed), "Regarding the problem of NPCs not interacting after local reproduction" (2025-05-09, closed), "hr-get-valid-password-image: Can't get response from Mark Johnson" (2025-08-20, closed), "Infra: synchronous reset" (2024-11-28, closed), and still-open "Failed to pull 'bitnami/mongodb:5.0' during setup.sh execution" (2025-10-28), "ownCloud service ignores OWNCLOUD_TRUSTED_DOMAINS" (2025-10-17) and "Gitlab bugs when initializing the enviroment" (2025-11-29). The repository was last pushed 2025-11-17. — [TheAgentCompany issue 1055](https://github.com/TheAgentCompany/TheAgentCompany/issues/1055); [issue 1043](https://github.com/TheAgentCompany/TheAgentCompany/issues/1043); [issue 1067](https://github.com/TheAgentCompany/TheAgentCompany/issues/1067); [issue 843](https://github.com/TheAgentCompany/TheAgentCompany/issues/843); [issue 1082](https://github.com/TheAgentCompany/TheAgentCompany/issues/1082); [issue 1079](https://github.com/TheAgentCompany/TheAgentCompany/issues/1079); [issue 1088](https://github.com/TheAgentCompany/TheAgentCompany/issues/1088)
- TheAgentCompany setup troubleshooting covers the api-server waiting forever without host networking, Plane failing to come up, and the `bitnami/mongodb` container failing on Apple Silicon. — [TheAgentCompany SETUP.md](https://github.com/TheAgentCompany/TheAgentCompany/blob/main/docs/SETUP.md)
- TheAgentCompany's stated limitations: tasks skew to what can be auto-evaluated, only two scaffolds tested, no human baseline, and task content created by introspection. — [TheAgentCompany paper v3](https://arxiv.org/html/2412.14161v3)
- SWE-bench Multimodal found tests whose result changed across repeated runs of the same patch and removed them (679 to 643 candidates), then annotators removed 24 impossible tasks. — [SWE-bench Multimodal paper](https://arxiv.org/html/2410.03859)
- SWE-rebench reruns tests several times and excludes any instance that fails in at least one run; it attributes some setup failures to unpinned or obsolete dependencies. — [SWE-rebench paper v2](https://arxiv.org/html/2505.20411v2)
- SWE-Gym's limitations: 11 repositories, Python only, bias to easier tasks. — [SWE-Gym paper v2](https://arxiv.org/html/2412.21139v2)
- OpenAI has a post titled "Why we no longer evaluate SWE-bench Verified"; a search summary dates it February 2026 and says it recommends SWE-bench Pro. The post itself was not fetched. — [OpenAI post (not fetched)](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/)

### Inferences
- The `bitnami/mongodb:5.0` pull failure reported in October 2025 suggests TheAgentCompany's setup depends on a third-party image tag that is no longer reliably available; anyone reusing it should mirror all service images first. I read only the issue title, not its resolution.
- "Infra: synchronous reset" as a closed issue, together with the code comments that GitLab and ownCloud resets must be synchronous, indicates reset races were a real failure mode during development; RocketChat and Plane resets are still asynchronous and rely on health checks.
- Shared, long-lived services combined with a whole-suite reset cadence make cross-task state leakage a structural risk in WebArena; this is consistent with, but not proven by, the flaky-evaluation issues listed.
- Maintenance has slowed on the original web benchmarks (visualwebarena untouched since 2024, TheAgentCompany since late 2025), so reuse means owning the images.

### Gaps
- Licences of the seeded data and of the bundled applications were not checked. Magento Open Source, GitLab CE, Postmill, ownCloud, Plane, RocketChat and OSClass each have their own licences, and the Craigslist-derived and Amazon-derived data carry their own terms.
- Tarball sizes for the WebArena images and total disk use of the TheAgentCompany task images are not stated in the pages read.
- Issue bodies were not read; only titles, states and dates were retrieved.
- No wall-clock figure for a full SWE-bench Verified evaluation was found in the official guides.

## Which concrete components (code, images, scripts) could be reused directly?

### Takeaway
The most reusable pieces for a mid-session takeover benchmark are OpenHands' sandbox layer (including the Apptainer workspace), SWE-bench's instance images and harness, and TheAgentCompany's api-server reset pattern and checkpoint grader structure. The WebArena images are reusable as seeded sites but bring heavy hosting and reset costs.

### Cited Findings
- OpenHands SDK workspaces (`DockerWorkspace`, `ApptainerWorkspace`, remote API workspace) with the pre-built `ghcr.io/openhands/agent-server:latest-python` image; `workspace.execute_command(...)` returns command, exit code and stdout. MIT. — [OpenHands Apptainer sandbox guide](https://docs.openhands.dev/sdk/guides/agent-server/apptainer-sandbox.md); [software-agent-sdk](https://github.com/OpenHands/software-agent-sdk)
- OpenHands/benchmarks contains an Apptainer SWE-bench evaluation path and Apptainer image builds (merged June 2026). MIT. — [benchmarks PR 743](https://github.com/OpenHands/benchmarks/pull/743); [benchmarks PR 745](https://github.com/OpenHands/benchmarks/pull/745)
- OpenHands' image tagging scheme (version, lock hash, source hash) for reproducible, cache-friendly runtime images. — [OpenHands runtime architecture](https://docs.openhands.dev/usage/architecture/runtime)
- SWE-bench harness: `python -m swebench.harness.run_evaluation` with `--cache_level`, `--max_workers`, `--modal true`; predictions as JSONL of `instance_id`, `model_name_or_path`, `model_patch`; per-instance logs and reports. MIT. — [SWE-bench evaluation guide](https://www.swebench.com/SWE-bench/guides/evaluation/); [SWE-bench Docker setup guide](https://www.swebench.com/SWE-bench/guides/docker_setup/)
- SWE-Gym: 2,438 instances with pre-built per-instance images and released agent trajectories. Apache-2.0 code. — [SWE-Gym paper v2](https://arxiv.org/html/2412.21139v2); [SWE-Gym abstract](https://arxiv.org/abs/2412.21139)
- SWE-rebench: dataset `nebius/SWE-rebench` on Hugging Face and a fork of the SWE-bench harness. — [SWE-rebench abstract](https://arxiv.org/abs/2505.20411); [SWE-rebench fork](https://github.com/SWE-rebench/SWE-bench-fork)
- TheAgentCompany: `servers/` (compose file, per-service directories for gitlab, owncloud, plane, rocketchat, `api-server` with reset and health endpoints, `setup.sh`), 175 task images with `/utils/init.sh`, `/instruction/task.md`, `/utils/eval.py` and encrypted `/utils/evaluator.py`, and `evaluation/` as a template for an OpenHands-driven pipeline (including `evaluation/browsing.py` for pre-login). MIT. — [TheAgentCompany servers directory](https://github.com/TheAgentCompany/TheAgentCompany/tree/main/servers); [TheAgentCompany EVALUATION.md](https://github.com/TheAgentCompany/TheAgentCompany/blob/main/docs/EVALUATION.md)
- WebArena: site image tarballs on three mirrors, the public AMI, the OpenStreetMap compose setup with `webarena-map-backend-boot-init.yaml`, and curl-based health checks. Apache-2.0. — [WebArena environment_docker README](https://github.com/web-arena-x/webarena/blob/main/environment_docker/README.md)
- VisualWebArena: Classifieds site with its compose file and reset token, and `prepare.sh` for auto-login cookies. MIT. — [VisualWebArena README](https://github.com/web-arena-x/visualwebarena/blob/main/README.md)
- WebArena-Verified: self-contained evaluator image `ghcr.io/servicenow/webarena-verified:latest`, deterministic comparators, and HAR-trace offline scoring. Apache-2.0. — [WebArena-Verified docs](https://servicenow.github.io/webarena-verified/latest/); [webarena-verified](https://github.com/ServiceNow/webarena-verified)

### Inferences
- For an HPC cluster without Docker, the lowest-risk stack is SWE-bench or SWE-Gym instance images run through OpenHands' Apptainer workspace, since that combination already exists upstream and the mid-run state is a working-tree diff.
- TheAgentCompany's task-image convention (init script, instruction file, encrypted evaluator, checkpoint scores, trajectory as grader input) is a good template for handoff tasks even if its services are not reused; the handoff point could be modelled as a pre-seeded state in the init step.
- WebArena-Verified's choice to score from recorded traces and structured responses, rather than from a live site and an LLM judge, is the direction to copy for a grader that must be deterministic and re-runnable.
- Reusing WebArena or TheAgentCompany services directly on the cluster is likely to cost more engineering than building small purpose-made services, given the Docker-socket reset design and image sizes. This is a judgement, not something a source states.

### Gaps
- No computer-use (desktop GUI) benchmark or environment from Neubig's group was found. One search returned only third-party work (OSWorld 2.0, OpenComputer, MyPCBench) with no link to the group. The group's browser-based work (WebArena, VisualWebArena, TheAgentCompany's browsing) is the nearest equivalent found. A targeted check of Neubig's publication list was not done.
- Talks and blog posts by Neubig and co-authors on environment engineering were not searched.
- The Docker Hub namespace for pre-built SWE-bench instance images and whether SWE-rebench publishes its images were not confirmed in the pages read.
- Whether TheAgentCompany task images are still pullable from their registry in November 2026 was not tested.
