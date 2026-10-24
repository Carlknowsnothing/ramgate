import os, subprocess, sys, time
from conftest import PROJECT

from ramgate import state

CHILD = ('import os, sys, time; sys.path.insert(0, %r); import ramgate; '
         'lease = ramgate.acquire(%r, name=%r, mem=0.6%s); print("got", time.time(), flush=True); time.sleep(%s); lease.release()')


def spawn(name, hold, extra='', env=None):
    code = CHILD % (str(PROJECT), 'hog', name, extra, hold)
    return subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env={**os.environ, **(env or {})})


def test_second_acquire_blocks_until_first_releases(home):
    a = spawn('A', hold=2)
    got_a = a.stdout.readline()
    assert got_a.startswith('got')
    b = spawn('B', hold=0)
    got_b = float(b.stdout.readline().split()[1])
    a.wait(); b.wait()
    assert got_b - float(got_a.split()[1]) >= 1.8
    assert state.tasks() == []
    assert {h['name'] for h in state.history()} == {'A', 'B'}


def test_acquire_no_wait_raises(home):
    a = spawn('A', hold=2)
    assert a.stdout.readline().startswith('got')
    b = spawn('B', hold=0, extra=', no_wait=True')
    assert b.wait() != 0 and 'Refused' in b.stderr.read()
    a.wait()


def test_held_env_skips_queue(home):
    a = spawn('A', hold=1.5)
    assert a.stdout.readline().startswith('got')
    b = spawn('B', hold=0, env={'RAMGATE_HELD': '1'})
    assert b.stdout.readline().startswith('got') and a.poll() is None  # A still running, B passes straight through
    a.wait(); b.wait()


def test_in_process_watcher_terminates_self_on_redline(home):
    d = home / 'strict'; d.mkdir()
    (d / 'config.toml').write_text((home / 'config' / 'config.toml').read_text().replace('redline_free_pct = 0', 'redline_free_pct = 100'))
    a = spawn('A', hold=30, env={'RAMGATE_CONFIG_DIR': str(d)})
    assert a.stdout.readline().startswith('got')
    assert a.wait(timeout=5) == -15 or a.returncode == 143
    (h,) = state.history()
    assert h['killed']
