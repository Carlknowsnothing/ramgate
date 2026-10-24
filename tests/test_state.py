import json, os, signal, subprocess, sys, time
from pathlib import Path
from conftest import PROJECT

from ramgate import state


def test_register_then_close_removes_record(home):
    reg = state.register({'name': 'a', 'profile': 'hog', 'mem_gib': 1, 'pid': os.getpid()})
    assert [t['name'] for t in state.tasks()] == ['a']
    reg.update(child_pid=123)
    assert state.tasks()[0]['child_pid'] == 123
    reg.close()
    assert state.tasks() == []


def test_record_of_dead_process_is_cleaned_as_zombie(home):
    code = ('import os, sys, time; sys.path.insert(0, %r); from ramgate import state; '
            'keep = state.register({"name": "z", "profile": "hog", "mem_gib": 1, "pid": os.getpid()}); '
            'print("ok", flush=True); time.sleep(30)') % str(PROJECT)
    p = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE, text=True, env=os.environ)
    assert p.stdout.readline().strip() == 'ok'
    assert [t['name'] for t in state.tasks()] == ['z']
    p.kill(); p.wait()
    assert state.tasks() == []
    assert list(state.tasks_dir().glob('*.json')) == []


def test_history_roundtrip_and_recent_peaks(home):
    state.append_history({'profile': 'ace-step', 'peak_gib': 47.0, 'peak_sustained_gib': 19.5, 'ended': time.time()})  # 预算看持续峰值
    state.append_history({'profile': 'ace-step', 'peak_gib': 22.0, 'ended': time.time() - 40 * 86400})
    state.append_history({'profile': 'tts', 'peak_gib': 5, 'ended': time.time()})
    assert state.recent_peaks('ace-step', days=30) == [19.5]
    assert len(state.history()) == 3
