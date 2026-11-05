"""`ramgate patrol`: the safety net for processes that never went through ramgate (2026-11-05: three unregistered
videoseal runs, 87 GiB, swap +27 GiB). Runs under launchd (`ramgate install-patrol`) and every few seconds looks at
processes that an agent session (Claude Code / Codex) started, are not registered, and hold at least
big_unregistered_gib. It ends such a process when

- the agent session that started it has ended (the same rule `ramgate run` applies to its own tasks), or
- a memory red line is crossed and it is the youngest of them and younger than every registered task (registered
  tasks act on red lines themselves; the youngest overall goes first, as everywhere in ramgate).

Apps Carl starts himself have no agent ancestor and are never touched. Ownership is remembered from the first
sighting, so a process whose shell already exited (parent launchd) still counts as the session's. A process that
was already orphaned the first time patrol saw it is left alone: nobody can tell who started it."""
import json, os, signal, subprocess, time
from dataclasses import dataclass
from . import policy, state, sysmem
from .config import Config
from .lease import log
from .run import AGENTS
from .watch import Sampler

SAME_START = 3  # seconds: start times are derived from ps etime, which only has 1 s resolution


@dataclass
class Proc:
    pid: int
    ppid: int
    started: float
    name: str


@dataclass
class Candidate:
    pid: int
    started: float
    owner: int
    name: str
    gib: float


def read_procs(now=None):
    now = now or time.time()
    out = subprocess.run(['/bin/ps', '-axo', 'pid=,ppid=,etime=,comm='], capture_output=True, text=True).stdout
    procs = {}
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4: continue
        procs[int(parts[0])] = Proc(int(parts[0]), int(parts[1]), now - _etime(parts[2]), os.path.basename(parts[3]).lower())
    return procs


def _etime(s):
    """ps etime: [[dd-]hh:]mm:ss."""
    days, _, rest = s.rpartition('-')
    secs = 0
    for part in rest.split(':'): secs = secs * 60 + int(part)
    return secs + int(days or 0) * 86400


class Patrol:
    def __init__(self, cfg):
        self.cfg = cfg
        self.owners = {}  # pid -> (its start, owner pid, owner start)

    def agent_of(self, pid, procs):
        p = procs.get(procs[pid].ppid) if pid in procs else None
        while p and p.pid > 1:
            if p.name in AGENTS: return p.pid
            p = procs.get(p.ppid)
        return None

    def observe(self, procs):
        for pid, (started, _, _) in list(self.owners.items()):
            if pid not in procs or abs(procs[pid].started - started) > SAME_START: del self.owners[pid]  # gone or pid reused
        for pid, p in procs.items():
            if pid in self.owners or p.name in AGENTS: continue
            owner = self.agent_of(pid, procs)
            if owner: self.owners[pid] = (p.started, owner, procs[owner].started)

    def candidates(self, procs, footprints, registered):
        """Big, unregistered, agent-started processes, youngest first."""
        out = [Candidate(pid, procs[pid].started, owner, procs[pid].name, footprints.get(pid) or 0)
               for pid, (_, owner, _) in self.owners.items()
               if pid in procs and pid not in registered and (footprints.get(pid) or 0) >= self.cfg.big_unregistered_gib]
        return sorted(out, key=lambda c: -c.started)

    def owner_gone(self, cand, procs):
        _, owner, owner_started = self.owners[cand.pid]
        return owner not in procs or abs(procs[owner].started - owner_started) > SAME_START

    @staticmethod
    def redline_victim(cands, registered_started):
        if not cands: return None
        if registered_started and max(registered_started) > cands[0].started: return None
        return cands[0]


def registered_pids(tasks):
    pids = set()
    for t in tasks:
        pids |= {t['pid'], t.get('child_pid')}
        if t.get('child_pid'): pids |= set(sysmem.descendants(t['child_pid']))
        pids |= set(sysmem.descendants(t['pid']))
    return pids - {None}


def terminate_tree(pid, sig=signal.SIGTERM):
    """Children first (a pipeline's worker), then the process itself."""
    for p in list(reversed(sysmem.descendants(pid))) + [pid]:
        try: os.kill(p, sig)
        except ProcessLookupError: pass


def notify(text):
    subprocess.run(['/usr/bin/osascript', '-e', f'display notification {json.dumps(text, ensure_ascii=False)} with title "ramgate 巡查"'],
                   capture_output=True)


def main(argv):
    cfg = Config.load()
    patrol, pending = Patrol(cfg), {}  # pid -> SIGKILL deadline
    sampler = Sampler(cfg); sampler.start()
    beat = state.home() / 'patrol.json'
    log(f'patrol started (pid {os.getpid()}): every {cfg.patrol_seconds:.0f} s, agent-started unregistered processes >= '
        f'{cfg.big_unregistered_gib:.0f} GiB')

    def end(c, why, redline):
        pending[c.pid] = time.time() + cfg.kill_grace_seconds  # first: whatever fails below, it is never acted on twice
        if redline: state.note_kill()
        log(f'ending pid {c.pid} ({c.name}, {c.gib:.1f} GiB, started by agent session {c.owner}): {why}')
        terminate_tree(c.pid)
        notify(f'结束了 {c.name}（pid {c.pid}，{c.gib:.0f} GiB）：{"内存越过红线" if redline else "启动它的会话已结束"}')

    while True:
        try:
            procs = read_procs(); patrol.observe(procs)
            for pid, deadline in list(pending.items()):
                if pid not in procs: del pending[pid]
                elif time.time() > deadline:
                    log(f'pid {pid} ignored SIGTERM, SIGKILL')
                    del pending[pid]; terminate_tree(pid, signal.SIGKILL)
            tasks = state.tasks()
            fps = {pid: sysmem.footprint_gib(pid) for pid in patrol.owners}
            cands = [c for c in patrol.candidates(procs, fps, registered_pids(tasks)) if c.pid not in pending]
            for c in [c for c in cands if patrol.owner_gone(c, procs)]:
                end(c, 'its agent session ended', redline=False); cands.remove(c)
            snap = sampler.last
            if snap and cands:
                why = policy.redline(snap.free_pct, sampler.swap_rise(cfg.redline_window_seconds), cfg,
                                     sampler.swap_rise(cfg.redline_swap_recent_seconds), sampler.critical_seconds(),
                                     state.seconds_since_kill())
                victim = why and patrol.redline_victim(cands, [t['started'] for t in tasks])
                if victim: end(victim, f'red line: {why}', redline=True)
            beat.write_text(json.dumps({'pid': os.getpid(), 'at': time.time(), 'watching': len(cands)}))
        except Exception as e:  # one bad scan must not stop the patrol
            log(f'patrol scan failed: {e!r}')
        time.sleep(cfg.patrol_seconds)
