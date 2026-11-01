"""`ramgate run`: queue, inject env, start the child, watch it, record history.
The task is bound to the agent session (Claude Code / Codex) that started it and ends with it; a command stopped
from outside leaves a marker so an agent cannot simply start it again during the cooldown."""
import argparse, os, signal, subprocess, sys, time
from . import lease, policy, state
from .config import Config, load_profiles
from .lease import log
from .watch import Sampler

KILLED_RC = 137
STOPPED_RC = 4
AGENTS = {'claude', 'codex'}  # executable names of agent sessions a task belongs to


def owner_pid():
    """Nearest ancestor that is an agent session, else None (started by hand from a terminal: SIGHUP covers that).
    RAMGATE_OWNER_PID pins it (0 = unbound)."""
    if 'RAMGATE_OWNER_PID' in os.environ: return int(os.environ['RAMGATE_OWNER_PID']) or None
    try: out = subprocess.run(['ps', '-A', '-o', 'pid=,ppid=,comm='], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired): return None
    procs = {}
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3: procs[int(parts[0])] = (int(parts[1]), os.path.basename(parts[2]).lower())
    pid = os.getppid()
    while pid > 1 and pid in procs:
        ppid, name = procs[pid]
        if name in AGENTS: return pid
        pid = ppid
    return None


def alive(pid):
    try: os.kill(pid, 0); return True
    except ProcessLookupError: return False
    except PermissionError: return True


def parse(argv):
    p = argparse.ArgumentParser(prog='ramgate run', description='Start a command once its memory budget fits')
    p.add_argument('--profile', required=True, help='profile name (see `ramgate profiles`)')
    p.add_argument('--mem', type=float, help='override the budget (GiB)')
    p.add_argument('--name', help='label shown in status; defaults to the command')
    p.add_argument('--resident', action='store_true', help='long-running server: budget stays held, marked in status')
    p.add_argument('--detach', action='store_true', help='keep running after the agent session that started it ends')
    p.add_argument('--force', action='store_true', help='start even though this command was stopped recently (only when Carl asked)')
    p.add_argument('--no-wait', action='store_true', help='exit 3 immediately when no slot is free')
    p.add_argument('--timeout', type=float, help='wait at most this many seconds, then exit 3')
    p.add_argument('cmd', nargs=argparse.REMAINDER)
    a = p.parse_args(argv)
    if a.cmd and a.cmd[0] == '--': a.cmd = a.cmd[1:]
    if not a.cmd: p.error('missing the command to run (after --)')
    return a


def main(argv):
    a = parse(argv)
    cfg, profiles = Config.load(), load_profiles()
    if a.profile not in profiles:
        log(f'no profile {a.profile}; known: {" ".join(profiles)}'); return 2
    prof = profiles[a.profile]
    try: need = lease.budget_for(prof, a.mem, cfg)
    except SystemExit as e: print(e, file=sys.stderr); return 2
    name = a.name or ' '.join(a.cmd)[:60]
    cwd = os.getcwd()
    if a.force: state.clear_stopped(a.cmd, cwd)
    elif (stop := state.stopped_marker(a.cmd, cwd, cfg.stop_cooldown_minutes * 60)):
        ago = (time.time() - stop['at']) / 60
        log(f'refused: this command was stopped {ago:.0f} min ago ({stop["reason"]}). The user stopped it on purpose: '
            f'do not retry, ask the user first. Rerun with --force only when they say so; `ramgate unblock` clears all markers.')
        return STOPPED_RC
    resident = a.resident or prof.resident
    owner = None if (a.detach or resident) else owner_pid()
    sampler = Sampler(cfg); sampler.start()
    try: reg = lease.wait_and_register(prof, name, need, cfg, sampler, resident=resident, no_wait=a.no_wait, timeout=a.timeout)
    except lease.Refused: sampler.stop(); return 3

    # Caller's env wins over profile env; RAMGATE_HELD tells a nested acquire() that the slot is already held.
    env = {**{k: str(v) for k, v in prof.env.items()}, **os.environ, 'RAMGATE_HELD': '1'}
    swap0 = sampler.last.swap_used_gib; started = time.time()
    killed = {'reason': None}; warned = [False]; external = {'reason': None}; child = None

    def signal_group(sig):
        if child is None: return  # not started yet: the check right after Popen delivers it
        try: os.killpg(child.pid, sig)
        except ProcessLookupError: pass

    # Installed before the child starts, so a stop in between can never orphan it.
    def forward(sig, _):
        external['reason'] = f'ramgate received signal {sig}'
        log(f'got signal {sig}, forwarding to the child'); signal_group(sig)
    for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP): signal.signal(s, forward)

    # Own process group: a kill takes the whole tree (bash wrappers, split-phase grandchildren); signals are forwarded.
    child = subprocess.Popen(a.cmd, env=env, start_new_session=True)
    if external['reason']: signal_group(signal.SIGTERM)
    reg.update(child_pid=child.pid); sampler.watch(child.pid); sampler.sample_once()  # even a 50 ms command gets one reading
    if owner: log(f'bound to agent session pid {owner}: the task ends when that session ends (--detach to keep it)')

    def terminate(reason, redline=True):
        if killed['reason']: return
        killed.update(reason=reason, at=time.time())
        if redline:
            state.note_kill(); log(f'red line: {reason}; this task registered last, terminating {name} (pid {child.pid})')
        else: log(f'{reason}; terminating {name} (pid {child.pid})')
        signal_group(signal.SIGTERM)

    def on_sample(snap, fp):
        if child.poll() is not None: return
        if fp and not warned[0] and fp > need * cfg.overrun_warn_ratio:
            warned[0] = True; log(f'warning: {name} uses {fp:.1f} GiB, over {cfg.overrun_warn_ratio}x its {need:.0f} GiB budget')
        why = policy.redline(snap.free_pct, sampler.swap_rise(cfg.redline_window_seconds), cfg,
                             sampler.swap_rise(cfg.redline_swap_recent_seconds), sampler.critical_seconds(), state.seconds_since_kill(),
                             over_budget=bool(fp and fp > need * cfg.overrun_warn_ratio))
        if why and youngest_is(reg):
            terminate(why)
    sampler.on_sample = on_sample

    while True:
        try: rc = child.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            if owner and not killed['reason'] and not alive(owner): terminate('owner session ended', redline=False)
            if killed['reason'] and time.time() - killed['at'] > cfg.kill_grace_seconds:
                log('grace period over, SIGKILL'); signal_group(signal.SIGKILL)
            continue
        break
    sampler.stop(); ended = time.time()
    if rc < 0 and not external['reason']: external['reason'] = f'the task was killed by signal {-rc}'
    if rc < 0: rc = 128 - rc  # killed by a signal: shell convention 128+signo
    if killed['reason']: rc = KILLED_RC
    elif external['reason']:
        state.mark_stopped(a.cmd, cwd, external['reason'])
        log(f'stopped from outside ({external["reason"]}); the same command is refused for {cfg.stop_cooldown_minutes:.0f} min')
    state.append_history({'id': reg.record['id'], 'name': name, 'profile': prof.name, 'mem_gib': need, 'resident': reg.record['resident'],
                          'started': started, 'ended': ended, 'seconds': round(ended - started, 1), 'rc': rc,
                          'peak_gib': round(sampler.peak_gib, 3), 'peak_sustained_gib': round(sampler.peak_sustained_gib, 3),
                          'min_free_pct': sampler.min_free_pct,
                          'swap_start_gib': round(swap0, 2), 'swap_end_gib': round(sampler.last.swap_used_gib, 2),
                          'killed': bool(killed['reason']), 'reason': killed['reason'] or external['reason'], 'cwd': cwd})
    reg.close()
    log(f'done: {name} exit {rc}, peak {sampler.peak_gib:.1f} GiB (budget {need:.0f}), min free {sampler.min_free_pct:.0f}%, '
        f'swap {swap0:.1f}->{sampler.last.swap_used_gib:.1f} GiB')
    return rc


def youngest_is(reg):
    """Only the most recently registered task acts on a red line; the others wait for memory to recover."""
    current = state.tasks()
    return not current or max(current, key=lambda t: t['started'])['id'] == reg.record['id']
