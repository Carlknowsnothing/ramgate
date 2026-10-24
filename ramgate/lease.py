"""Queueing for a slot: shared by `ramgate run` and the in-process acquire()."""
import os, sys, time
from . import policy, state, sysmem


class Refused(Exception):
    """--no-wait or timeout: did not get a slot."""


def log(msg):
    print(f'[ramgate] {msg}', flush=True, file=sys.stderr)


def budget_for(profile, mem, cfg):
    need = policy.budget_gib(profile.mem_gib, mem, state.recent_peaks(profile.name, cfg.history_days))
    if need <= 0:
        raise SystemExit(f'[ramgate] profile {profile.name} has no budget; pass --mem <GiB>')
    return need


def describe(cmd):
    """Short label for an unregistered process: the venv/project directory, else the command head."""
    head = cmd.split()[0] if cmd.split() else cmd
    parts = head.split('/')
    return '/'.join(parts[-4:-2]) if len(parts) > 4 else head[:30]


def wait_and_register(profile, name, need, cfg, sampler, *, resident=False, no_wait=False, timeout=None, pid=None, extra=None):
    """Register and return once admission passes; otherwise wait (forever by default)."""
    deadline = time.time() + timeout if timeout is not None else None
    last_report, first = 0.0, True
    while True:
        with state.admit_lock():
            current = state.tasks()
            snap, _ = sampler.sample_once()
            fps = [sysmem.tree_footprint_gib(t.get('child_pid') or t['pid']) or 0 for t in current]
            unrealized = sum(max(0.0, t['mem_gib'] - fp) for t, fp in zip(current, fps))
            known = {t['pid'] for t in current} | {t.get('child_pid') for t in current} | {os.getpid(), pid or 0}
            strangers = sysmem.big_processes(known, cfg.big_unregistered_gib)
            committed = sum(max(t['mem_gib'], fp) for t, fp in zip(current, fps)) + sum(fp for _, fp, _ in strangers)
            ok, why = policy.admit(need, committed, snap.total_gib, snap.free_gib,
                                   sampler.swap_rising(cfg.swap_quiet_seconds), cfg, unrealized_gib=unrealized)
            if ok:
                reg = state.register({'name': name, 'profile': profile.name, 'mem_gib': need, 'pid': pid or os.getpid(),
                                      'resident': bool(resident or profile.resident), 'cwd': os.getcwd(), **(extra or {})})
                if not first: log(f'slot acquired, starting: {name} (budget {need:.0f} GiB)')
                return reg
        holders = ', '.join(f"{t['name']} ({t['mem_gib']:.0f} GiB{', resident' if t.get('resident') else ''})" for t in current) or 'none'
        others = ', '.join(f'pid {p} {fp:.0f} GiB {describe(cmd)}' for p, fp, cmd in strangers)
        msg = (f'{why}; running: {holders}' + (f'; unregistered but holding memory: {others} (stop them if idle)' if others else '')
               + f'; free {snap.free_gib:.0f} GiB, swap {snap.swap_used_gib:.1f} GiB')
        if no_wait or (deadline and time.time() >= deadline):
            log(('timed out: ' if deadline else 'not waiting: ') + msg); raise Refused(msg)
        if time.time() - last_report >= cfg.queue_report_seconds:
            log(f'queued: {name} needs {need:.0f} GiB; {msg}'); last_report = time.time()
        first = False
        time.sleep(min(2.0, cfg.queue_report_seconds or 2.0))
