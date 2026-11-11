# Computer-use agent benchmark environments: build, snapshot, reset, verify

Research date: 2026-11-11. Source dates are given inline. Items marked "(search snippet)" come from search-result summaries of the cited page and were not read in full; treat them as lower confidence than items read from the page itself. The arXiv PDFs for OSWorld, WindowsAgentArena, AndroidWorld, TClone and "Toward Systems Foundations for Agentic Exploration" were read through their arXiv HTML versions; the OSWorld, WindowsAgentArena, AndroidWorld and macOSWorld HTML pages were truncated at 100,000 characters, so their appendices were not read.

## What is the environment substrate and how are snapshots taken and restored?

### Takeaway
All the desktop benchmarks run a full guest OS (VMware/VirtualBox, QEMU/KVM inside Docker, or cloud VMs) and reset by reverting to one named base snapshot or golden image, then replaying a per-task setup script; none stores a snapshot per task. AndroidWorld is the exception: it uses no snapshots at all and relies on per-task initialize and teardown code against a long-lived emulator.

### Cited Findings

OSWorld
- Providers listed in the README: VMware (Workstation Pro, Fusion on Apple silicon), VirtualBox, Docker (Ubuntu or Windows guest), Modal, Daytona, and AWS with a host-client architecture for large parallel runs. Docker support was added 2024-10-22; the environment was refactored for more platforms on 2024-06-15. — [OSWorld README](https://github.com/xlang-ai/OSWorld)
- KVM is "recommended" for the Docker provider (check with `egrep -c '(vmx|svm)' /proc/cpuinfo`); the Daytona provider is described as needing no host KVM. — [OSWorld README](https://github.com/xlang-ai/OSWorld)
- The paper states that "the snapshot feature also enables efficient reset of the virtual environment" and that setup is a hybrid of one snapshot plus config-driven steps, because storing a full snapshot per example would take gigabytes. — [OSWorld paper, arXiv 2404.07972 (v1 2024-04-11, v2 2024-05-30)](https://arxiv.org/html/2404.07972)
- In `DesktopEnv.reset()`, the revert is conditional: if `is_environment_used` is true it calls `provider.revert_to_snapshot(path_to_vm, snapshot_name)` (default snapshot name `init_state`) and restarts the emulator; otherwise it logs "Environment is clean, skipping snapshot revert". The flag defaults to true for vmware and virtualbox and false for docker, aws, gcp and azure; `step()` sets it to true, and so does any task with a `config` or proxy requirement. — [desktop_env.py, main branch, read 2026-11-11](https://raw.githubusercontent.com/xlang-ai/OSWorld/main/desktop_env/desktop_env.py)
- The substrate history given by the authors: VMware `.vmdk`/`.vmx` files with heavy local resource use, then Docker with QEMU in summer 2024 (8 to 16 parallel environments on one server), then AWS (up to 50 simultaneous environments). — [OSWorld-Verified blog, XLANG Lab, 2025-07-28](https://xlang.ai/blog/osworld-verified)
- On AWS the VM image was cut from 50 GB to 25 GB and IOPS adjusted because lazy loading from AMI/EBS snapshots made first boots slow (LibreOffice took about a minute on first launch). — [OSWorld-Verified blog, 2025-07-28](https://xlang.ai/blog/osworld-verified)
- The published Ubuntu qcow2 guest image can be older than the repository checkout; the README warns that the image predates an X11 connection leak fix and that updating the host code does not update the guest screenshot handler. — [OSWorld README](https://github.com/xlang-ai/OSWorld)

WindowsAgentArena
- A Docker container hosts a Windows 11 VM under QEMU/KVM, using a process adapted from dockur/windows; a Python Flask server inside the VM executes commands and returns observations. — [WindowsAgentArena paper, arXiv 2409.08264 (2024-09-13)](https://arxiv.org/html/2409.08264)
- No pre-built Windows snapshot is distributed for licensing reasons. Users download a Windows 11 Enterprise Evaluation ISO (90-day trial) and run `./run-local.sh --prepare-image true` once; the automated process takes about 20 minutes and yields a roughly 30 GB "golden image" in `src/win-arena-container/vm/storage`. — [WindowsAgentArena README](https://github.com/microsoft/WindowsAgentArena); [paper](https://arxiv.org/html/2409.08264)
- KVM can be turned off with `--use-kvm false`, but the README says running without KVM is not recommended for performance. Default VM size is 8 GB RAM and 8 cores. — [WindowsAgentArena README](https://github.com/microsoft/WindowsAgentArena)
- In Azure the golden image is uploaded to Blob Storage and each worker VM (`Standard_D8_v3`, must support nested virtualization) is created for one job and disposed of afterward. — [WindowsAgentArena README](https://github.com/microsoft/WindowsAgentArena)

AndroidWorld
- The environment is the stock Android Emulator with a Pixel 6 AVD on API 33 (Android 13), launched with `-no-snapshot -grpc 8554`. — [android_world README](https://github.com/google-research/android_world)
- The paper describes no snapshot mechanism; isolation comes from per-task `initialize_task` and `teardown` logic. — [AndroidWorld paper, arXiv 2405.14573 (2024)](https://arxiv.org/html/2405.14573)
- An experimental Docker image (noted 2025-06-02) runs the emulator plus a FastAPI server and must be started with `--privileged`. — [android_world README](https://github.com/google-research/android_world)

macOSWorld
- Runs on AWS EC2 dedicated Apple hardware (Mac mini hosts) to comply with Apple's licence. Each task boots from a public AMI snapshot (one AMI per language), then runs preparation commands over SSH; snapshot restoration discards all file and system changes after each evaluation. — [macOSWorld paper, arXiv 2506.04135v4](https://arxiv.org/html/2506.04135v4)
- The repository README says each task takes roughly 15 to 20 minutes and that snapshot recovery is the main bottleneck; an `--override_env_reset` flag skips recovery for similar tasks. (search snippet) — [macOSWorld project page](https://macos-world.github.io)

Web environments
- WebArena ships each site as a Docker image; reset means stopping the container and restarting from the original image, estimated at a few seconds to a minute per site. (search snippet) — [WebArena paper, arXiv 2307.13854](https://arxiv.org/pdf/2307.13854)
- BrowserGym's `prepare_backend()` checks backend configuration and restores the WebArena/VisualWebArena containers so that changes from a previous agent are wiped. (search snippet) — [The BrowserGym Ecosystem, arXiv 2412.05467](https://arxiv.org/pdf/2412.05467)
- WEBSERV reports that a WebArena shopping container needs roughly 6 GB and about a minute to start, and proposes an Incus-based manager that starts, clones and resets a paired browser and web server with sub-second startup. (search snippet) — [WEBSERV, arXiv 2510.16252](https://arxiv.org/pdf/2510.16252v1)

### Inferences
- For the cloud and Docker providers OSWorld does not literally "revert a snapshot" between tasks; the code path treats a fresh instance or container as clean and only reverts once the guest has been touched. The practical reset unit is "new disposable guest from the base image", which is also what WindowsAgentArena on Azure and macOSWorld do.
- Every benchmark here snapshots only at the episode boundary. None exposes a public API to snapshot at step k and resume from it, so a takeover benchmark would have to add that itself (see the forking section).
- The stale-guest-image warning in the OSWorld README means the base image is itself a versioned artefact that can drift from the harness; a handover benchmark should pin and hash it.

### Gaps
- The WindowsAgentArena README and the readable part of the paper do not say how the VM is reset between tasks within one worker (whether the qcow2 is re-copied, an overlay is discarded, or state simply accumulates). Appendix A.7 was not readable.
- Spider2-V's substrate was not confirmed from a primary source in this pass; it is commonly described as built on the OSWorld infrastructure, but I did not verify that.
- macOSWorld reset time and cost per task were only seen in a search snippet, not in the paper text read.

## How is each task's initial state set up and how deterministic is it?

### Takeaway
Initial state is "base image plus a declarative per-task config" that downloads files, launches apps and runs commands; it is deterministic only to the extent that downloads, app start-up timing and live websites are. AndroidWorld goes furthest on determinism (fixed clock, pinned app versions, offline apps, seeded parameters).

### Cited Findings
- OSWorld setup runs in three stages: start the VM, prepare files (optional downloads), run setup commands (open files or tabs, change window size). A post-config runs after the agent finishes, for steps such as saving files or reopening apps before state is read. — [OSWorld paper](https://arxiv.org/html/2404.07972)
- Building setup took about 1 hour per example and evaluation scripts about 2 hours; total annotation was about 1,800 person-hours by nine students over three months. — [OSWorld paper](https://arxiv.org/html/2404.07972)
- `reset()` retries setup up to `MAX_RETRIES = 5` times with a 5-second sleep between attempts, and raises "Environment setup failed after 5 attempts" otherwise. `step()` has a default 2-second pause after each action. — [desktop_env.py](https://raw.githubusercontent.com/xlang-ai/OSWorld/main/desktop_env/desktop_env.py)
- Timing was a real source of non-determinism: apps such as LibreOffice Writer had to be fully loaded before scripted inputs ran, which caused setup failures; `open_file` was changed from fire-and-forget to a blocking call, and inter-action sleeps were tuned separately for `t3.medium` and `t3.large`. — [OSWorld-Verified blog, 2025-07-28](https://xlang.ai/blog/osworld-verified)
- Initial-state and gold files were originally hosted on Google Drive, which temporarily blocked all downloads when counts got too high; they were moved to Hugging Face datasets, and a cache of pre-downloaded init-state files was published on 2025-05-01. — [OSWorld-Verified blog](https://xlang.ai/blog/osworld-verified); [OSWorld README](https://github.com/xlang-ai/OSWorld)
- Some OSWorld tasks need a Google account with OAuth 2.0 configured, and some need a proxy depending on how websites treat the network location; the README says that without this those tasks may fail and lower scores. — [OSWorld README](https://github.com/xlang-ai/OSWorld)
- WindowsAgentArena tasks are JSON files with `id`, `instruction`, `config` (launch apps, execute commands, download files), `evaluator` and `result`; the paper says the config script sets the initial state deterministically. About two thirds of the 154 tasks were adapted from OSWorld. — [WindowsAgentArena paper](https://arxiv.org/html/2409.08264)
- AndroidWorld resets the device clock to 15 October 2023 15:34 UTC at the start of each task, installs open-source apps at fixed versions from F-Droid, uses apps that need no login and store data on device, and generates task parameters from a controlled seed. — [AndroidWorld paper](https://arxiv.org/html/2405.14573)
- AndroidWorld has 116 tasks across 20 apps, each instantiated with random parameters; an example is SendSms, whose `initialize_task` clears the SMS database and whose teardown clears it again. — [AndroidWorld paper](https://arxiv.org/html/2405.14573); [android_world README](https://github.com/google-research/android_world)
- Even so, three seeds with the same agent gave 27.6%, 26.3% and 33.2% on AndroidWorld. — [AndroidWorld paper](https://arxiv.org/html/2405.14573)
- macOSWorld's per-task preparation script runs over SSH after boot to restore application state such as opening a specific document. — [macOSWorld paper](https://arxiv.org/html/2506.04135v4)
- Spider2-V has 494 tasks across 20 enterprise applications, and 34% of examples require registering real software accounts. (search snippet) — [Spider2-V, arXiv 2407.10956](https://arxiv.org/abs/2407.10956)

### Inferences
- The state at step 0 of any OSWorld-style task is reproducible from (base image hash, task config, cached files). The state at step k is not reproducible from the action log alone, because of timing sleeps, live web content and app-internal non-determinism. Replaying the first agent's actions is therefore a weak way to reconstruct a mid-episode state; a captured snapshot is the reliable route.
- AndroidWorld's teardown-based isolation assumes the task author knows every piece of state the task touches; it works because apps are offline and state lives in known databases and files.

### Gaps
- No source quantifies how often OSWorld setup fails or needs retries.
- The WindowsAgentArena claim of deterministic setup is the authors' statement; I found no independent measurement.

## How are outcomes verified?

### Takeaway
The dominant method is execution-based: a script reads final device state (files, app databases, settings, accessibility or DOM data) and compares it with an expected value. LLM or screenshot judges are not the primary grader in OSWorld, WindowsAgentArena, AndroidWorld or macOSWorld.

### Cited Findings
- OSWorld: each task has a getter that extracts state from the VM or cloud and a metric function that scores it; there are 134 unique evaluation functions over 369 Ubuntu tasks, 30 of which (8.1%) are infeasible, plus 43 Windows tasks. Some getters fetch live values such as crawled citation counts. — [OSWorld paper](https://arxiv.org/html/2404.07972)
- In `evaluate()`, an infeasible task scores 1 only if the agent's last action is `FAIL`; for other tasks a final `FAIL` scores 0 before any metric runs; multiple metrics combine with `and`/`or`; a missing result file scores 0. — [desktop_env.py](https://raw.githubusercontent.com/xlang-ai/OSWorld/main/desktop_env/desktop_env.py)
- WindowsAgentArena: post-task evaluator scripts check device state (config files, settings, file contents); rewards are binary or continuous (for example image similarity); intermediate rewards are left as future work. — [WindowsAgentArena paper](https://arxiv.org/html/2409.08264)
- AndroidWorld: rewards come mainly from system state reached through adb — file system, app SQLite databases (new and deleted rows), system settings — with UI validation as a fallback; composite tasks give partial credit. — [AndroidWorld paper](https://arxiv.org/html/2405.14573)
- macOSWorld: evaluation scripts in AppleScript, JavaScript or zsh run over SSH and return a binary reward. — [macOSWorld paper](https://arxiv.org/html/2506.04135v4)
- OSWorld 2.x moved to weighted checkpoints scoring 0 to 1 per task, with dependent checkpoints, deductions, and setup-related "gate" conditions that can void a checkpoint or a whole task. — [Epoch AI review of OSWorld 2.1, reviewed 2026-09-28 (date from search snippet)](https://epoch.ai/benchmarks/osworld-2-1/review)
- Human baselines: OSWorld 72.36% (median 111.94 s per task); WindowsAgentArena 74.5% from a single participant. — [OSWorld paper](https://arxiv.org/html/2404.07972); [WindowsAgentArena paper](https://arxiv.org/html/2409.08264)

### Inferences
- Final-state checkers are indifferent to who did the work, so they transfer directly to a takeover setting. What they do not give is credit attribution between the first and second agent; OSWorld 2.x checkpoints or AndroidWorld composite partial credit are the closest existing mechanisms.
- OSWorld's rule that a final `FAIL` scores 0 on feasible tasks, and 1 on infeasible ones, is a ready-made hook for "the incoming agent should recognise the session is unrecoverable" cases.

### Gaps
- WebVoyager-style live-web evaluation (screenshot plus LLM judge) was not researched in this pass; I have no sourced findings on it.
- Spider2-V's evaluator design was not read from a primary source.

## Can this run on an HPC cluster without Docker or root (Apptainer only)? Parallelism and cost

### Takeaway
I found no primary source describing OSWorld, WindowsAgentArena or AndroidWorld running under Apptainer. The authors' own answer to scale is cloud VMs (AWS, Azure), and their container paths assume Docker with KVM or `--privileged`.

### Cited Findings
- OSWorld parallel runs use `scripts/python/run_multienv.py --num_envs N`; the single-threaded `run.py` is deprecated. The README claims AWS parallelisation "can reduce evaluation time to within 1 hour". No dollar figures are given. — [OSWorld README](https://github.com/xlang-ai/OSWorld)
- The OSWorld authors report 8 to 16 parallel Docker/QEMU environments on one server and up to 50 on AWS using `t3.medium` and `t3.large` instances, with evaluation dropping from 10+ hours. — [OSWorld-Verified blog, 2025-07-28](https://xlang.ai/blog/osworld-verified)
- Interrupted OSWorld Docker runs leave residual containers that must be removed by hand. — [OSWorld README](https://github.com/xlang-ai/OSWorld)
- WindowsAgentArena FAQ: about $8 of Azure compute for a full run (roughly $0.38/h x 40 `Standard_D8_v3` VMs x 0.5 h), about 30 to 35 minutes wall-clock with 40 VMs, and about $100 of model cost with GPT-4o or $15 with GPT-4o-mini. The paper's headline is a full evaluation in as little as 20 minutes. — [WindowsAgentArena README](https://github.com/microsoft/WindowsAgentArena); [paper](https://arxiv.org/html/2409.08264)
- WindowsAgentArena: depending on Docker settings commands may need `sudo`; KVM is recommended but optional. — [WindowsAgentArena README](https://github.com/microsoft/WindowsAgentArena)
- AndroidWorld needs about 2 GB memory and 8 GB disk and runs on a laptop; its Docker path requires `--privileged`. M3A averaged 3.9 minutes per task. — [AndroidWorld paper](https://arxiv.org/html/2405.14573); [android_world README](https://github.com/google-research/android_world)
- Docker is generally unavailable on HPC clusters because it typically needs root; Apptainer is the rootless, daemonless alternative, and Docker images convert with `apptainer pull docker://...`. (search snippets) — [VSC docs](https://docs.vscentrum.be/compute/software/installing_software/containers.html); [Alliance Canada docs](https://docs.alliancecan.ca/wiki/Apptainer); [OpenHands Apptainer sandbox guide](https://docs.openhands.dev/sdk/guides/agent-server/apptainer-sandbox)
- A team using WebArena reported being limited to four AWS-hosted servers and therefore four parallel sessions, running tasks sequentially to avoid cross-task interference. (search snippet) — [Scaling Agent Learning via Experience Synthesis, arXiv 2511.03773](https://arxiv.org/pdf/2511.03773)

### Inferences
- QEMU itself is an unprivileged user-space program, so the OSWorld Docker image's qcow2 guest could in principle be booted by QEMU inside an Apptainer container without root. Whether it is usable depends on whether compute nodes expose `/dev/kvm` to ordinary users; without it QEMU falls back to software emulation (TCG), which both OSWorld and WindowsAgentArena describe as not recommended for performance. This is my inference, not something any benchmark author documents.
- The Docker providers also rely on Docker networking and port mapping, which would need to be replaced (for example QEMU user-mode networking with forwarded ports) under Apptainer. Also inference.
- OSWorld's Daytona and Modal providers and the AWS provider move the VM off the local machine entirely, so an HPC node would only run the harness. That removes the KVM problem but needs outbound network access and adds cloud spend.
- A benchmark whose tasks live in a Linux user session (files, terminal, a browser, LibreOffice) rather than needing a full guest kernel can run as a plain Apptainer container with a virtual display, which avoids KVM altogether. This matches what TClone and StateFork do with containers (next section), but neither documents Apptainer.

### Gaps
- No source found for OSWorld, WindowsAgentArena or AndroidWorld under Apptainer or Singularity, or for whether typical clusters expose `/dev/kvm`. This should be tested directly on the target cluster.
- No dollar cost for a full OSWorld-Verified run was found.
- The Android Emulator's behaviour without KVM or `--privileged` was not researched.

## What validity problems have been reported?

### Takeaway
The recurring failure classes are live-web drift, anti-bot blocking, set-up timing races, ambiguous instructions and over-strict or over-lenient evaluators. OSWorld-Verified (July 2025) fixed about 300 reported issues, yet an independent audit of OSWorld 2.1 in September 2026 still rated it "Flawed".

### Cited Findings
- OSWorld-Verified was announced 2025-07-28 after roughly 300 issues were reported by Moonshot AI, HUD, OpenAI, ByteDance, Anthropic, Simular and others; the post gives both "300+" and "nearly 300". The effort took about two months from a team of roughly ten. — [OSWorld-Verified blog](https://xlang.ai/blog/osworld-verified)
- Web drift examples: an Apple comparison page redesign, Rentalcars.com lazy loading, a speedtest.net CSV export that was removed. Anti-bot examples: Google Search and shopping sites blocking agents, CAPTCHAs on Amazon and Budget.com, 403 IP blocks, Steam timeouts, NBA geo-restrictions. — [OSWorld-Verified blog](https://xlang.ai/blog/osworld-verified)
- Ambiguity and state examples: "switch off Bluetooth" when Bluetooth was already unavailable; "resize image" versus "resize layer". Evaluator examples: valid alternative CSV concatenations and equivalent spreadsheet formulas marked wrong; extension-based solutions wrongly treated as infeasible. — [OSWorld-Verified blog](https://xlang.ai/blog/osworld-verified)
- Fixes mostly changed evaluators rather than task text to keep scores comparable: fuzzy matching, a `possibility_of_env_change` flag, proxy support and fixed IP allocation, substitute websites, tolerant document/image/spreadsheet comparison, font installation for LibreOffice and audio configuration for VLC. — [OSWorld-Verified blog](https://xlang.ai/blog/osworld-verified)
- The authors state that environment instability remains because sites, CAPTCHAs and IP blocks are outside their control, and that simulated environments for externally dependent tasks were still in progress. — [OSWorld-Verified blog](https://xlang.ai/blog/osworld-verified)
- An independent best-practices paper found 13 of 46 problems in OSWorld's chrome section broken by changes to website layout, URLs and functionality, and estimated this understated UI-TARS's performance by 28 points absolute. (search snippet) — [Establishing Best Practices for Building Rigorous Agentic Benchmarks, arXiv 2507.02825 (July 2025)](https://arxiv.org/pdf/2507.02825)
- Epoch AI audited a stratified sample of 50 of the 108 tasks in OSWorld 2.1 (v2.0 released 2026-06-26, v2.1 on 2026-09-16) and found 40 errors across 36 tasks; errors could affect up to 50.4% of the audited score (7.4% impossible, 25.4% false negatives, 17.6% false positives) against a 20% threshold for "Flawed". The review was stopped once the threshold was passed, so it is partial. — [Epoch AI review](https://epoch.ai/benchmarks/osworld-2-1/review)
- Epoch's examples are mostly grader and provisioning faults: an answer key that rejects "Salesforce License renewal", a grader reading a fixed filename, referenced media not provisioned, a starter file only one Logisim version opens, and a task where uploading all documents to every category scores 100%. — [Epoch AI review](https://epoch.ai/benchmarks/osworld-2-1/review)
- BenchJack, an automated audit for reward-hackable benchmarks, reports OSWorld and WebArena reaching a 0% hack rate within three rounds of patches. (search snippet) — [BenchJack, arXiv 2605.12673 (May 2026)](https://arxiv.org/pdf/2605.12673)
- The verified OSWorld leaderboard requires maintainers to run the agent on their side, with agent code disclosed. — [OSWorld README](https://github.com/xlang-ai/OSWorld)

### Inferences
- The validity problems cluster around two things a takeover benchmark can avoid by construction: dependence on the live internet, and graders that accept only one surface form of a correct answer. AndroidWorld's offline, pinned apps show the first is achievable.
- The "Bluetooth already unavailable" case is a state-drift bug where the base image did not match the task's assumed precondition. Handover tasks multiply this risk because the precondition is a mid-episode state, so each captured state should be validated by a precondition check before the second agent starts.
- Two of three audits used a human or model reading tasks one by one; a new benchmark should budget for the same before release.

### Gaps
- I did not find a published audit of WindowsAgentArena, AndroidWorld or macOSWorld task validity.
- The OSWorld GitHub issue tracker was not read directly; reported issues are covered only through the authors' own summary.
- The relationship between OSWorld-Verified and OSWorld 2.x (whether 2.x tasks are new or revised) is not stated in the sources read.

## Is forking a running environment mid-episode supported, or has anyone done it?

### Takeaway
None of the benchmarks supports mid-episode forking as a feature. It has been done as systems research in 2025 and 2026 — TClone on OSWorld, StateFork/Waypoint on terminal tasks — and that work reports that partial state capture (filesystem only, or process memory without the terminal session) misleads the resumed agent.

### Cited Findings
- TClone (Huang, Srivatsa, Asch, Patwa, Zhang; arXiv 2605.17320, 2026-05-19) snapshots a live GUI workspace, forks isolated branches, rolls back and selectively commits. It is built on sibling containers, not VMs, and requires a modified Linux kernel and an extended CRIU. — [TClone, arXiv 2605.17320v1](https://arxiv.org/html/2605.17320v1)
- TClone's captured state: process tree, threads, descriptors and anonymous memory (copy-on-write through a kernel module); a versioned copy-on-write filesystem; loopback TCP connections re-established, external connections closed and re-established per branch; a Wayland compositor and its clients running inside each container (Webtop with Selkies), so display state belongs to the branch. — [TClone](https://arxiv.org/html/2605.17320v1)
- TClone was evaluated on OSWorld (369 tasks) with Agent S3 and on GTA with AgentLoop, on a desktop-class Core i5-13400 with 32 GB RAM. Reported gains: fork time up to 4.9x better than KVM and 3.4x better than CRIU; end-to-end task latency up to 1.9x and 1.5x better. With 16 concurrent clones CRIU exceeded 50 s while TClone stayed near 10 s. These are the authors' own measurements. — [TClone](https://arxiv.org/html/2605.17320v1)
- TClone does not roll back external side effects such as emails or payments, and is not yet open source; the authors say they will release it on acceptance. — [TClone](https://arxiv.org/html/2605.17320v1)
- "Toward Systems Foundations for Agentic Exploration" (Xu, Zhou, Wu, Kaffes; arXiv 2510.05556, 2025-10-08) measured snapshot-plus-restore latency for six mechanisms on synthetic state. With empty state: CRIU 0.060 s, Docker commit 0.416 s, Podman 0.835 s, Podman+CRIU hybrid 1.657 s, AWS VM snapshot 353 s. With 2 GB of memory CRIU took 1.445 s; with 2 GB of disk Docker took 6.915 s and Podman 12.914 s. — [arXiv 2510.05556v1](https://arxiv.org/html/2510.05556v1)
- The same paper notes CRIU ignores files, and Docker/Podman commits capture the filesystem but lose live memory. — [arXiv 2510.05556v1](https://arxiv.org/html/2510.05556v1)
- StateFork (Columbia DAPLab, 2026-06-04) is a common API over Docker, Podman, CRIU, Podman+CRIU, Firecracker microVMs, gVisor and its own Waypoint backend; Waypoint combines filesystem delta tracking, process checkpointing and a persistent shell/PTY session. Repositories: github.com/Alex-XJK/StateFork and github.com/Alex-XJK/waypoint. — [StateFork blog post](https://daplab.cs.columbia.edu/general/2026/06/04/statefork-give-agents-a-rewind-button.html)
- StateFork's authors report that container commits drop active shell context, running services, background jobs and sockets, and that the Podman+CRIU hybrid did worse on Terminal-Bench than filesystem-only Docker because missing terminal session state confused the agent. — [StateFork blog post](https://daplab.cs.columbia.edu/general/2026/06/04/statefork-give-agents-a-rewind-button.html)
- Commercial sandboxes advertise live VM forking; Freestyle claims snapshots that pause the source VM for under 1 ms. Vendor claims, unverified. (search snippet) — [Freestyle blog](https://www.freestyle.sh/blog/engineering/how-to-build-deep-research-agents-on-freestyle); [Morph](https://www.morphllm.com/fork-vm-for-ai-agents)
- OSWorld's own snapshot handling is a single named snapshot (`init_state`) reverted at reset; `revert_to_snapshot` takes a snapshot name as a parameter. — [desktop_env.py](https://raw.githubusercontent.com/xlang-ai/OSWorld/main/desktop_env/desktop_env.py)

### Inferences
- For a takeover benchmark the requirement is weaker than TClone's: one capture at the handover point, restored many times, with no latency pressure. The 2510.05556 numbers suggest ordinary mechanisms are adequate for that — a hypervisor snapshot with memory under VMware/VirtualBox/QEMU, or CRIU plus a filesystem copy for a container — since seconds per restore is acceptable when it happens once per episode.
- Because OSWorld's provider interface already takes a snapshot name, taking an additional named snapshot at step k and passing that name at reset is a small change on VMware, VirtualBox or QEMU. I did not find anyone documenting this for OSWorld, and the Docker provider's snapshot behaviour was not verified.
- The cheapest route that needs no privileged tooling is to quiesce before capture: have the first agent's session reach a state where everything relevant is on disk (documents saved, apps closed or restartable), capture only the filesystem, and relaunch apps from a script on restore. StateFork's finding is the warning here: anything left in process memory or a terminal session is lost, and the incoming agent may be misled if the transcript implies it still exists.
- External side effects (sent email, remote accounts) cannot be forked by any of these systems; tasks with such effects need mocked or local services.
- CRIU and TClone both need privileges or kernel changes that a shared HPC cluster is unlikely to grant; this is inference, as neither source states its privilege requirements.

### Gaps
- No source states whether CRIU can be used unprivileged inside Apptainer, or TClone's root requirements.
- I found no benchmark that already evaluates one agent resuming another agent's captured GUI session; the forking work targets tree search and rollback by a single agent.
- Absolute fork latency for KVM is not given in the TClone paper, only ratios.
