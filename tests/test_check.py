import json
import pytest
from conftest import rg


@pytest.fixture
def builtins_only(tmp_path, monkeypatch):
    monkeypatch.setenv('RAMGATE_CONFIG_DIR', str(tmp_path))  # empty: built-in profiles only


def test_check_rejects_direct_model_launch_and_suggests_wrapper(builtins_only):
    r = rg('check', '/Users/x/ace-step-music/upstream/.venv/bin/python -c "import acestep"')
    assert r.returncode == 2 and 'ramgate run --profile ace-step' in r.stdout


def test_check_allows_wrapped_or_unrelated_commands(builtins_only):
    assert rg('check', 'ramgate run --profile ace-step -- python -c "import acestep"').returncode == 0
    assert rg('check', 'ls -la && git status').returncode == 0
    assert rg('check', 'cd tools/ComfyUI && .venv/bin/python main.py --listen').returncode == 2
    assert rg('check', 'tools/ltx-2-mlx/.venv/bin/ltx-2-mlx generate').returncode == 2


def test_check_uses_user_profile_matches_too(home):
    assert rg('check', '/opt/fake-model/.venv/bin/python go.py').returncode == 2


def test_check_reads_claude_hook_json_from_stdin(builtins_only):
    payload = json.dumps({'tool_name': 'Bash', 'tool_input': {'command': 'mlx_whisper audio.wav'}})
    r = rg('check', '--hook', input=payload)
    assert r.returncode == 2 and 'whisper' in r.stderr
    assert rg('check', '--hook', input=json.dumps({'tool_input': {'command': 'echo hi'}})).returncode == 0


def test_user_profiles_are_matched_before_builtins(home):
    (home / 'config' / 'profiles.toml').write_text('[mine]\nmem_gib = 1\nmatch = ["acestep-fork/.venv"]\n')
    r = rg('check', '/x/acestep-fork/.venv/bin/python go.py')
    assert r.returncode == 2 and '--profile mine' in r.stdout


def test_check_ignores_commands_that_merely_mention_a_model(builtins_only):
    assert rg('check', 'grep -n "ComfyUI\\|acestep\\|ltx-2.5" work/comfy.log | tail').returncode == 0
    assert rg('check', 'ls models/ltx-2.5-q8').returncode == 0


def test_check_catches_background_blender_render(builtins_only):
    assert rg('check', 'blender -b -P anim.py -- out/frames 100 all 48').returncode == 2
    assert rg('check', '/Applications/Blender.app/Contents/MacOS/Blender -b -P scene.py').returncode == 2
    assert rg('check', 'ramgate run --profile blender -- blender -b -P anim.py').returncode == 0
