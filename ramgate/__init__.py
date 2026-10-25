"""ramgate: one gate for every local model on a shared-memory Mac. CLI: `ramgate run`; in-process:

    import ramgate
    lease = ramgate.acquire('ace-step', name='round 3')   # returns once the budget fits; keep the object alive
    ...                                                    # run the model; a watcher thread SIGTERMs this process on a red line
    lease.release()                                        # optional: process exit releases and records history anyway

RAMGATE_HELD=1 in the environment means a parent already holds the slot (the parent queued, the child
just runs): acquire() returns an empty lease immediately.
"""
import atexit, os, signal, time
from . import lease as _lease, policy, state, sysmem
from .config import Config, load_profiles
from .watch import Sampler

HELD_ENV = 'RAMGATE_HELD'
Refused = _lease.Refused


class Lease:
    def __init__(self, reg, sampler, cfg, need):
        self.reg, self.sampler, self.cfg, self.need = reg, sampler, cfg, need
        self.started, self.swap0 = time.time(), sampler.last.swap_used_gib
        self.killed_reason, self._done, self._warned = None, False, False
        sampler.on_sample = self._on_sample
        atexit.register(self.release)
        # Default SIGTERM/SIGINT handling exits without atexit; if the host has no handler of its own,
        # record history first and then die the default way.
        for sig in (signal.SIGTERM, signal.SIGINT):
            if signal.getsignal(sig) in (signal.SIG_DFL, signal.default_int_handler):
                signal.signal(sig, self._die)

    def _die(self, sig, _frame):
        self.release()
        signal.signal(sig, signal.SIG_DFL); os.kill(os.getpid(), sig)

    def _on_sample(self, snap, fp):
        if self._done: return
        if fp and not self._warned and fp > self.need * self.cfg.overrun_warn_ratio:
            self._warned = True
            _lease.log(f"warning: {self.reg.record['name']} uses {fp:.1f} GiB, over {self.cfg.overrun_warn_ratio}x its {self.need:.0f} GiB budget")
        why = policy.redline(snap.free_pct, self.sampler.swap_rise(self.cfg.redline_window_seconds), self.cfg,
                             self.sampler.swap_rise(self.cfg.redline_swap_recent_seconds), self.sampler.critical_seconds(),
                             state.seconds_since_kill(), over_budget=bool(fp and fp > self.need * self.cfg.overrun_warn_ratio))
        if why and not self.killed_reason and _youngest(self.reg):
            self.killed_reason = why; state.note_kill()
            _lease.log(f"red line: {why}; this task registered last, terminating itself and its children (pid {os.getpid()})")
            for c in reversed(sysmem.descendants(os.getpid())):  # children first (e.g. a pipeline's worker), then self
                try: os.kill(c, signal.SIGTERM)
                except ProcessLookupError: pass
            os.kill(os.getpid(), signal.SIGTERM)

    def release(self):
        if self._done: return
        self._done = True; self.sampler.stop(); ended = time.time()
        state.append_history({'id': self.reg.record['id'], 'name': self.reg.record['name'], 'profile': self.reg.record['profile'],
                              'mem_gib': self.need, 'resident': self.reg.record['resident'], 'started': self.started, 'ended': ended,
                              'seconds': round(ended - self.started, 1), 'rc': 137 if self.killed_reason else 0,
                              'peak_gib': round(self.sampler.peak_gib, 3), 'peak_sustained_gib': round(self.sampler.peak_sustained_gib, 3),
                              'min_free_pct': self.sampler.min_free_pct,
                              'swap_start_gib': round(self.swap0, 2), 'swap_end_gib': round(self.sampler.last.swap_used_gib, 2),
                              'killed': bool(self.killed_reason), 'reason': self.killed_reason, 'cwd': os.getcwd(), 'in_process': True})
        self.reg.close()


class _HeldLease:
    """A parent already queued for us: nothing to do."""
    def release(self): pass


def _youngest(reg):
    current = state.tasks()
    return not current or max(current, key=lambda t: t['started'])['id'] == reg.record['id']


def acquire(profile, name=None, mem=None, *, resident=False, no_wait=False, timeout=None):
    """Queue for the profile's budget, register this process and start watching; returns a Lease."""
    if os.environ.get(HELD_ENV): return _HeldLease()
    cfg, profiles = Config.load(), load_profiles()
    prof = profiles[profile]
    need = _lease.budget_for(prof, mem, cfg)
    sampler = Sampler(cfg, pid=os.getpid()); sampler.start()
    try: reg = _lease.wait_and_register(prof, name or profile, need, cfg, sampler, resident=resident, no_wait=no_wait, timeout=timeout)
    except Refused: sampler.stop(); raise
    sampler.sample_once()
    return Lease(reg, sampler, cfg, need)
