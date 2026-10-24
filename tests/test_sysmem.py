import os
from ramgate import sysmem


def test_snapshot_reads_sane_values():
    s = sysmem.snapshot()
    assert 16 <= s.total_gib <= 1024
    assert 0 <= s.free_pct <= 100
    assert abs(s.free_gib - s.total_gib * s.free_pct / 100) < 0.01
    assert s.swap_used_gib >= 0


def test_footprint_of_self_is_positive():
    assert sysmem.footprint_gib(os.getpid()) > 0
    assert sysmem.footprint_gib(999999) is None


def test_tree_footprint_includes_children():
    import subprocess, time
    p = subprocess.Popen(['sh', '-c', 'python3 -c "import time; x = bytearray(100 * 2**20); time.sleep(1)"; true'])
    time.sleep(0.5)
    assert sysmem.tree_footprint_gib(p.pid) - sysmem.footprint_gib(p.pid) > 0.08
    p.wait()


def test_big_processes_lists_this_interpreter_when_threshold_is_tiny():
    rows = sysmem.big_processes(set(), 0.001)
    assert any(pid == os.getpid() for pid, _, _ in rows)
    assert not any(pid == os.getpid() for pid, _, _ in sysmem.big_processes({os.getpid()}, 0.001))
