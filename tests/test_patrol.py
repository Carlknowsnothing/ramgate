"""Patrol decisions are pure functions over a process table; these tests build the table by hand."""
from types import SimpleNamespace
from ramgate.patrol import Proc, Patrol

CFG = SimpleNamespace(big_unregistered_gib=4)


def table(*rows):
    """rows: (pid, ppid, started, name)."""
    return {pid: Proc(pid, ppid, started, name) for pid, ppid, started, name in rows}


BASE = [(1, 0, 0, 'launchd'), (100, 1, 0, 'Claude'), (200, 100, 10, 'claude'), (300, 100, 10, 'codex'),
        (500, 1, 5, 'Google Chrome'), (210, 200, 20, 'zsh'), (310, 300, 20, 'zsh')]


def test_agent_ancestor():
    procs = table(*BASE, (211, 210, 30, 'python3.11'), (511, 500, 30, 'Chrome Helper'))
    p = Patrol(CFG)
    assert p.agent_of(211, procs) == 200
    assert p.agent_of(511, procs) is None


def test_candidates_are_big_agent_started_unregistered():
    procs = table(*BASE, (211, 210, 30, 'python3.11'), (212, 210, 31, 'python3.11'), (311, 310, 32, 'Blender'),
                  (511, 500, 30, 'Chrome Helper'), (213, 210, 33, 'python3.11'))
    fp = {211: 30.0, 212: 1.0, 311: 6.0, 511: 9.0, 213: 25.0}
    p = Patrol(CFG); p.observe(procs)
    got = p.candidates(procs, fp, registered={213})
    assert [c.pid for c in got] == [311, 211]  # youngest first; small, registered and non-agent ones left out


def test_owner_remembered_after_orphaning_and_session_end_detected():
    procs = table(*BASE, (211, 210, 30, 'Blender'))
    p = Patrol(CFG); p.observe(procs)
    orphaned = table(*[r for r in BASE if r[0] not in (200, 210)], (211, 1, 30, 'Blender'))  # session and shell gone
    p.observe(orphaned)
    (c,) = p.candidates(orphaned, {211: 6.0}, registered=set())
    assert c.owner == 200
    assert p.owner_gone(c, orphaned)


def test_process_already_orphaned_when_first_seen_is_not_claimed():
    procs = table(*BASE, (211, 1, 30, 'python3.11'))
    p = Patrol(CFG); p.observe(procs)
    assert p.candidates(procs, {211: 30.0}, registered=set()) == []


def test_redline_victim_is_youngest_and_younger_than_registered_tasks():
    procs = table(*BASE, (211, 210, 30, 'python'), (212, 210, 40, 'python'), (311, 310, 50, 'python'))
    fp = {211: 30.0, 212: 30.0, 311: 25.0}
    p = Patrol(CFG); p.observe(procs)
    cands = p.candidates(procs, fp, registered=set())
    assert p.redline_victim(cands, registered_started=[]).pid == 311
    assert p.redline_victim(cands, registered_started=[45]).pid == 311
    assert p.redline_victim(cands, registered_started=[60]) is None  # a registered task is younger: it handles itself
    assert p.redline_victim([], registered_started=[]) is None


def test_pid_reuse_forgets_old_owner():
    procs = table(*BASE, (211, 210, 30, 'python'))
    p = Patrol(CFG); p.observe(procs)
    reused = table(*BASE, (211, 1, 99, 'python'))  # same pid, different start time, parent launchd
    p.observe(reused)
    assert p.candidates(reused, {211: 30.0}, registered=set()) == []


def test_terminate_tree_ends_children_and_parent():
    import subprocess, time
    from ramgate.patrol import terminate_tree
    parent = subprocess.Popen(['sh', '-c', 'sleep 30 & sleep 30; wait'])
    time.sleep(0.3)
    from ramgate import sysmem
    kids = sysmem.descendants(parent.pid); assert kids
    terminate_tree(parent.pid); parent.wait(timeout=5); time.sleep(0.2)
    alive = [k for k in kids if subprocess.run(['kill', '-0', str(k)], capture_output=True).returncode == 0]
    assert alive == []
