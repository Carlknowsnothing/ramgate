import sys, time
from conftest import rg, rg_bg

from ramgate import state


def test_exit_code_passthrough_and_history(home):
    r = rg('run', '--profile', 'hog', '--mem', '0.1', '--name', 'probe', '--', sys.executable, '-c',
           'import time; x = bytearray(200 * 2**20); print("hi", flush=True); time.sleep(0.3); raise SystemExit(7)')
    assert r.returncode == 7 and r.stdout == 'hi\n'
    (h,) = state.history()
    assert h['profile'] == 'hog' and h['name'] == 'probe' and h['rc'] == 7 and h['peak_gib'] > 0.15 and not h['killed']
    assert state.tasks() == []


def test_profile_without_mem_requires_override(home):
    r = rg('run', '--profile', 'hog', '--', 'true')
    assert r.returncode == 2 and '--mem' in r.stderr


def test_env_injected_only_when_unset(home):
    r = rg('run', '--profile', 'withenv', '--mem', '0.1', '--', 'sh', '-c', 'echo $RAMGATE_TEST_VAR')
    assert r.stdout.strip() == 'from-profile'
    r = rg('run', '--profile', 'withenv', '--mem', '0.1', '--', 'sh', '-c', 'echo $RAMGATE_TEST_VAR', env={'RAMGATE_TEST_VAR': 'mine'})
    assert r.stdout.strip() == 'mine'


def test_child_sees_held_marker(home):
    r = rg('run', '--profile', 'hog', '--mem', '0.1', '--', 'sh', '-c', 'echo $RAMGATE_HELD')
    assert r.stdout.strip() == '1'


def test_second_task_waits_until_first_finishes(home):
    a = rg_bg('run', '--profile', 'hog', '--mem', '0.6', '--name', 'A', '--', 'sleep', '2')
    time.sleep(0.5)
    assert [t['name'] for t in state.tasks()] == ['A']
    refused = rg('run', '--profile', 'hog', '--mem', '0.6', '--no-wait', '--', 'true')
    assert refused.returncode == 3 and 'A' in refused.stderr
    t0 = time.time()
    b = rg('run', '--profile', 'hog', '--mem', '0.6', '--name', 'B', '--', 'true')
    assert b.returncode == 0 and time.time() - t0 > 1.0
    assert 'queued' in b.stderr and 'A' in b.stderr
    a.wait()
    ended = {h['name']: h for h in state.history()}
    assert ended['B']['started'] >= ended['A']['ended']


def test_timeout_gives_up(home):
    a = rg_bg('run', '--profile', 'hog', '--mem', '0.6', '--', 'sleep', '3')
    time.sleep(0.5)
    r = rg('run', '--profile', 'hog', '--mem', '0.6', '--timeout', '0.5', '--', 'true')
    assert r.returncode == 3
    a.kill(); a.wait()


def test_resident_flag_recorded(home):
    a = rg_bg('run', '--profile', 'hog', '--mem', '0.1', '--resident', '--', 'sleep', '1')
    time.sleep(0.5)
    assert state.tasks()[0]['resident'] is True
    a.wait()
