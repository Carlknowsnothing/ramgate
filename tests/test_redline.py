import os, signal, sys, time
from conftest import rg, rg_bg

from ramgate import state


def strict_config(home):
    """A config dir for one task only: free < 100% counts as a red line, i.e. it always fires."""
    d = home / 'strict'; d.mkdir(exist_ok=True)
    src = home / 'config' / 'config.toml'
    (d / 'config.toml').write_text(src.read_text().replace('redline_free_pct = 0', 'redline_free_pct = 100'))
    (d / 'profiles.toml').write_text('')
    return {'RAMGATE_CONFIG_DIR': str(d)}


def test_youngest_task_kills_its_child_on_redline_and_older_survives(home):
    a = rg_bg('run', '--profile', 'hog', '--mem', '0.1', '--name', 'A', '--', 'sleep', '4')
    time.sleep(0.5)
    t0 = time.time()
    b = rg('run', '--profile', 'hog', '--mem', '0.1', '--name', 'B', '--', 'sleep', '30', env=strict_config(home))
    assert b.returncode == 137 and time.time() - t0 < 3 and 'red line' in b.stderr
    assert [t['name'] for t in state.tasks()] == ['A']  # A registered earlier, untouched
    a.wait()
    hist = {h['name']: h for h in state.history()}
    assert hist['B']['killed'] and hist['B']['reason'] and not hist['A']['killed'] and hist['A']['rc'] == 0


def test_only_the_latest_registration_counts_as_youngest(home):
    from ramgate.run import youngest_is
    a = state.register({'name': 'A', 'profile': 'hog', 'mem_gib': 1, 'pid': os.getpid()})
    time.sleep(0.01)
    b = state.register({'name': 'B', 'profile': 'hog', 'mem_gib': 1, 'pid': os.getpid()})
    assert youngest_is(b) and not youngest_is(a)
    b.close()
    assert youngest_is(a)  # the latest left, now it is A's turn
    a.close()


def test_overrun_only_warns(home):
    r = rg('run', '--profile', 'hog', '--mem', '0.05', '--', sys.executable, '-c',
           'import time; x = bytearray(300 * 2**20); time.sleep(0.5)')
    assert r.returncode == 0 and 'warning' in r.stderr and 'budget' in r.stderr


def test_sigterm_is_forwarded_to_child(home):
    a = rg_bg('run', '--profile', 'hog', '--mem', '0.1', '--', 'sleep', '30')
    time.sleep(0.6)
    child = state.tasks()[0]['child_pid']
    a.send_signal(signal.SIGTERM)
    assert a.wait(timeout=5) == 143
    time.sleep(0.2)
    assert os.system(f'kill -0 {child} 2>/dev/null') != 0  # the child is gone too
