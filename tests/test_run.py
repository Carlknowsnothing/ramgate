import sys, time
from conftest import PROJECT, rg, rg_bg

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


def _alive(pid):
    import os
    try: os.kill(pid, 0); return True
    except ProcessLookupError: return False


def _child_pid(timeout=3):
    t0 = time.time()
    while time.time() - t0 < timeout:
        current = state.tasks()
        if current and current[0].get('child_pid'): return current[0]['child_pid']
        time.sleep(0.05)
    raise AssertionError('task never registered a child')


def test_task_ends_when_owner_session_exits(home):
    import subprocess
    owner = subprocess.Popen(['sleep', '30'])
    a = rg_bg('run', '--profile', 'hog', '--mem', '0.1', '--name', 'bound', '--', 'sleep', '30', env={'RAMGATE_OWNER_PID': str(owner.pid)})
    child = _child_pid()
    owner.kill(); owner.wait()
    a.wait(timeout=5)
    time.sleep(0.2)
    assert not _alive(child)
    assert 'owner' in a.stderr.read()
    (h,) = state.history()
    assert h['reason'] == 'owner session ended'
    assert not state.stopped_marker(['sleep', '30'], str(PROJECT))  # the owner is gone; nobody to block


def test_detach_and_resident_survive_owner_exit(home):
    import subprocess
    for flag in ('--detach', '--resident'):
        owner = subprocess.Popen(['sleep', '30'])
        a = rg_bg('run', '--profile', 'hog', '--mem', '0.1', flag, '--', 'sleep', '1.5', env={'RAMGATE_OWNER_PID': str(owner.pid)})
        _child_pid(); owner.kill(); owner.wait()
        assert a.wait(timeout=5) == 0, flag


def test_externally_stopped_task_is_not_rerun(home):
    cmd = ('run', '--profile', 'hog', '--mem', '0.1', '--', 'sleep', '30')
    a = rg_bg(*cmd)
    _child_pid()
    a.terminate(); a.wait(timeout=5)
    r = rg(*cmd, timeout=5)
    assert r.returncode == 4 and 'do not retry' in r.stderr
    assert rg('run', '--profile', 'hog', '--mem', '0.1', '--', 'sleep', '0.1').returncode == 0  # other commands unaffected
    assert rg('run', '--profile', 'hog', '--mem', '0.1', '--force', '--', 'sleep', '0.1').returncode == 0


def test_killing_the_child_directly_also_blocks_rerun(home):
    import os, signal
    cmd = ('run', '--profile', 'hog', '--mem', '0.1', '--', 'sleep', '30')
    a = rg_bg(*cmd)
    os.kill(_child_pid(), signal.SIGKILL); a.wait(timeout=5)
    assert rg(*cmd, timeout=5).returncode == 4


def test_force_clears_marker_and_cooldown_expires(home):
    # Same command text each time; $D only shortens the forced rerun.
    cmd = ('run', '--profile', 'hog', '--mem', '0.1', '--', 'sh', '-c', 'sleep ${D:-30}')
    a = rg_bg(*cmd); _child_pid(); a.terminate(); a.wait(timeout=5)
    assert rg(*cmd, timeout=5).returncode == 4
    assert rg(*cmd[:5], '--force', *cmd[5:], env={'D': '0.1'}, timeout=5).returncode == 0
    assert rg(*cmd, env={'D': '0.1'}, timeout=5).returncode == 0  # --force removed the marker
    (home / 'config' / 'config.toml').write_text((home / 'config' / 'config.toml').read_text() + 'stop_cooldown_minutes = 0\n')
    a = rg_bg(*cmd); _child_pid(); a.terminate(); a.wait(timeout=5)
    assert rg(*cmd, env={'D': '0.1'}, timeout=5).returncode == 0  # cooldown 0: admitted again


def test_stop_and_unblock_commands(home):
    a = rg_bg('run', '--profile', 'hog', '--mem', '0.1', '--name', 'render-frames', '--', 'sleep', '30')
    _child_pid()
    r = rg('stop', 'render')
    assert r.returncode == 0 and 'render-frames' in r.stdout
    a.wait(timeout=5)
    assert rg('run', '--profile', 'hog', '--mem', '0.1', '--', 'sleep', '30', timeout=5).returncode == 4
    assert 'sleep 30' in rg('status').stdout
    assert rg('unblock').returncode == 0
    assert rg('run', '--profile', 'hog', '--mem', '0.1', '--no-wait', '--', 'sleep', '0.1').returncode == 0
    assert rg('stop', 'nothing-like-this').returncode == 2
