# ramgate

One gate for every local model on a shared-memory Mac. [中文说明](README.zh.md)

Apple Silicon has one pool of memory for CPU and GPU and no isolation between the processes that use it. When two or three AI coding sessions (Claude Code, Codex, Cursor…) each start a local model — ACE-Step, ComfyUI, LTX, Whisper, a TTS — nobody knows about the others, and a 64 GiB machine ends in a Jetsam "memory exhausted" force-quit dialog. ramgate is what the sessions go through instead:

```bash
ramgate run --profile ace-step -- .venv/bin/python generate.py round3   # starts when the budget fits, otherwise queues and says why
ramgate run --profile comfyui --resident -- python main.py --listen     # long-running server: budget stays held
ramgate run --profile adhoc --mem 8 -- python my_experiment.py          # anything without a profile
ramgate status                                                          # who holds memory, remaining budget, strangers
ramgate history                                                         # measured peaks of recent runs
```

In-process, for scripts that already know their phases:

```python
import ramgate
lease = ramgate.acquire('ace-step', name='round 3')   # returns when the budget fits; keep it alive
...                                                     # process exit releases it and records the peak
```

No daemon, no dependencies (Python ≥ 3.11 standard library). Every running task holds an `flock` on its own registration file; when the process dies the lock dies, so a crash never holds budget.

## Install

```bash
uv tool install ramgate          # or: pipx install ramgate
ramgate init                     # writes ~/.config/ramgate/{config,profiles}.toml templates
ramgate install-hook claude      # optional: Claude Code refuses Bash commands that launch a model without ramgate
```

Until it is on PyPI: `uv tool install --editable /path/to/ramgate`.

## What it does

**Admission.** A task is started only when all three hold, otherwise it waits (default forever, `--no-wait` exits 3, `--timeout` too):

1. *Budget*: memory already held + this task's budget ≤ allocatable (physical RAM − reserve). "Held" counts registered tasks at max(budget, footprint) **and the footprint of big unregistered model processes** — an idle ComfyUI you forgot about counts, because otherwise the kernel swaps it out to make room and swap jumps 18 GiB while "free" still looks fine.
2. *Watermark*: live free memory, minus budget that running tasks have not consumed yet, ≥ this task's budget + margin.
3. *Quiet swap*: no swap growth in the last 30 s.

With nothing else running, a task bigger than the whole pool may still run alone (XL rendering has a 45 GiB sustained footprint on a 64 GiB machine).

**Budgets** come from the profile, or `--mem`, and are raised automatically to 1.1 × the *sustained* peak (held for 3 consecutive samples) of recent runs — a one-second spike while loading weights does not count.

**Red lines.** While running, each task samples once a second. When a line is crossed, the **most recently registered** task terminates its own process group (SIGTERM, SIGKILL after 10 s, exit 137); older tasks and resident servers are never touched, and processes not started through ramgate are only named in `status`.

- kernel memory pressure *critical* for ≥ 3 s (loading XL weights flashes critical for ~1 s and recovers);
- free memory < 12 %;
- swap grew > 4 GiB in 60 s **and** is still rising **and** no kill happened in the last 60 s (after a kill the window still shows the growth; without the cooldown the next task dies too).

On macOS the swap line is the one that fires: the kernel keeps "free" healthy by paging other processes out.

**Environment injection.** Profiles carry variables that are set only when the caller has not: `PYTORCH_MPS_HIGH_WATERMARK_RATIO=1.0` for PyTorch/MPS tasks, cache limits for MLX, and so on.

**Session binding.** `ramgate run` looks up its nearest ancestor named `claude` or `codex` (the agent session) and ends the task's process group when that session exits, so a render started with `nohup … &` does not outlive the conversation. Started by hand from a terminal, nothing is bound (closing the terminal sends SIGHUP, which is forwarded). `--resident` servers and `--detach` tasks are never bound; `RAMGATE_OWNER_PID` pins the owner (0 = none).

**Stopped means stopped.** When a task is stopped from outside — `ramgate stop <id|name>`, a `kill` of ramgate or of the child, Ctrl-C — ramgate leaves a marker for that command line in that directory. For `stop_cooldown_minutes` (30) the same command is refused with exit 4 and a message telling the agent to ask the user instead of retrying; `--force` runs it anyway and clears the marker, `ramgate unblock` clears all. Red-line kills and session-end kills leave no marker. CPU is never limited: a render may use every core.

**Coverage without a list.** The hook blocks profile `match` patterns *and* any program started from a venv that has torch, mlx or onnxruntime installed (it follows `cd` and `source …/activate`; only the executable position of each `;`/`&&`/`|` segment counts, so `ls .venv/bin/python` passes). A new tool is covered before anyone writes a profile for it.

**Patrol.** `ramgate install-patrol` runs `ramgate patrol` under launchd. Every 2 s it looks at processes an agent session (`claude`, `codex`) started that are unregistered and hold ≥ `big_unregistered_gib`, and ends one (children first, SIGKILL after the grace period, plus a macOS notification and a line in `~/.local/state/ramgate/patrol.log`) when its session has ended, or when a red line is crossed and it is the youngest such process and younger than every registered task. Ownership is remembered from the first sighting, so a process whose shell already exited still belongs to its session. Apps you start yourself have no agent ancestor and are never touched; `ramgate status` shows whether the patrol is alive.

## Thresholds scale with RAM

Unset values are fractions of physical memory with floors, so the defaults make sense from 16 to 128 GiB:

| value | default | 16 GiB | 64 GiB |
|---|---|---|---|
| `reserve_gib` (kept for the OS and the agents) | 22 %, ≥ 6 | 6 | 14 |
| `admit_margin_gib` | 12.5 %, ≥ 2 | 2 | 8 |
| `redline_swap_rise_gib` (per 60 s) | 6.25 %, ≥ 1 | 1 | 4 |
| `big_unregistered_gib` | 6.25 %, ≥ 1 | 1 | 4 |

Pin any of them in `~/.config/ramgate/config.toml`. The fixed ones (`redline_free_pct = 12`, `redline_pressure_seconds = 3`, `kill_cooldown_seconds = 60`, …) are listed by `ramgate init`.

## Profiles

Built-in profiles (`ramgate profiles`) cover ACE-Step, ComfyUI, LTX, mlx-lm, Whisper, TTS and stem separation with budgets measured on a 64 GiB M5 Pro. Your `~/.config/ramgate/profiles.toml` adds or overrides entries field by field; `match` lists are appended, so you can add the paths of your own virtualenvs for the hook:

```toml
[comfyui]
mem_gib = 30
match = ["/Users/me/ComfyUI/.venv"]

[my-model]
mem_gib = 12
env = { PYTORCH_MPS_HIGH_WATERMARK_RATIO = "1.0" }
match = ["/path/to/my-model/.venv"]
note = "measured 11.3 GiB on 2026-10-25"
```

## Claude Code hook

`ramgate install-hook claude` adds a `PreToolUse` hook on `Bash` to `~/.claude/settings.json`. Any command whose text contains a profile's `match` substring and no `ramgate` is refused with the exact wrapper to use instead. It is substring matching, deliberately dumb: a command that merely *mentions* a model name in a heredoc will be refused too — wrap it in `ramgate run --profile adhoc --mem 1 -- …` or keep model names out of `match`. New Claude Code sessions pick the hook up; `--remove` takes it out.

For agents without hooks (Codex and friends) the rule lives in their instructions file: "local models are always started through `ramgate run`".

## Measured on a 64 GiB M5 Pro (macOS 26)

Two 10 GiB synthetic hogs submitted 2 s apart: the second queued until the first exited; swap unchanged. A runaway task declared at 3 GiB and growing to 21 GiB was terminated when swap had grown 5.5 GiB in a minute, while the older 10 GiB task finished normally. A real ACE-Step XL render (sustained 45 GiB footprint) and an LTX-2.5 generation (18 GiB) submitted together: LTX queued, both completed, free memory bottomed at 31 %, swap +0.7 GiB, kernel pressure never past *warning*. The three things these runs changed in the rules are described above (unregistered processes count, sustained peaks, kill cooldown); the raw numbers are in `docs/`.

## Non-goals

Admission needs no daemon (the patrol is a safety net, not a scheduler); no pausing, down-grading or CPU limits; never kills a process that neither it nor an agent session started; no shell parsing in the hook; macOS only (it reads `kern.memorystatus_level`, `vm.swapusage` and `proc_pid_rusage`).

## Development

```bash
uv venv && uv pip install pytest
.venv/bin/python -m pytest tests -q     # ~20 s, 35 tests; subprocess tests run the CLI with isolated state
```

State lives in `~/.local/state/ramgate/` (`tasks/` registrations, `history.jsonl`, `admit.lock`, `last_kill`); set `RAMGATE_HOME` and `RAMGATE_CONFIG_DIR` to isolate.

MIT.
