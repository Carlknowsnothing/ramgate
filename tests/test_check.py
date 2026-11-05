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


def _venv(root, *pkgs):
    (root / 'bin').mkdir(parents=True); (root / 'pyvenv.cfg').write_text('home = /usr/bin\n')
    site = root / 'lib' / 'python3.12' / 'site-packages'; site.mkdir(parents=True)
    for p in pkgs: (site / p).mkdir()
    return root


def test_ml_venv_detected_by_its_packages(builtins_only, tmp_path):
    from ramgate.check import ml_venv
    v = _venv(tmp_path / 'tool' / '.venv', 'torch')
    plain = _venv(tmp_path / 'plain' / '.venv', 'requests')
    assert ml_venv(f'{v}/bin/python embed.py a.mp4', '/') == v
    assert ml_venv(f'nohup {v}/bin/python embed.py > log 2>&1 &', '/') == v
    assert ml_venv(f'FOO=1 BAR=2 {v}/bin/python x.py', '/') == v
    assert ml_venv(f'cd {tmp_path}/tool && .venv/bin/python x.py', '/') == v
    assert ml_venv('.venv/bin/python x.py', str(tmp_path / 'tool')) == v
    assert ml_venv(f'source {v}/bin/activate && python run.py', '/') == v
    assert ml_venv(f'echo hi; {v}/bin/some-cli generate', '/') == v
    for ok in (f'ls -la {v}/bin/python', f'cat {v}/pyvenv.cfg', f'{v}/bin/python -m pip install x', f'{v}/bin/python --version',
               f'{plain}/bin/python x.py', f'source {v}/bin/activate && pip list', 'python3 x.py', f'grep -r torch {v}'):
        assert ml_venv(ok, '/') is None, ok


def test_ml_venv_with_mlx_or_onnx(builtins_only, tmp_path):
    from ramgate.check import ml_venv
    assert ml_venv(f'{_venv(tmp_path / "a", "mlx")}/bin/python x.py', '/')
    assert ml_venv(f'{_venv(tmp_path / "b", "onnxruntime")}/bin/python x.py', '/')


def test_hook_blocks_unknown_ml_venv_and_suggests_adhoc(builtins_only, tmp_path):
    v = _venv(tmp_path / 'videoseal' / '.venv', 'torch')
    payload = json.dumps({'tool_input': {'command': f'{v}/bin/python scripts/video_watermark.py embed a b'}, 'cwd': str(tmp_path)})
    r = rg('check', '--hook', input=payload)
    assert r.returncode == 2 and 'ramgate run --profile adhoc --mem' in r.stderr and 'torch' in r.stderr
    payload = json.dumps({'tool_input': {'command': '.venv/bin/python go.py'}, 'cwd': str(tmp_path / 'videoseal')})
    assert rg('check', '--hook', input=payload).returncode == 2
    wrapped = json.dumps({'tool_input': {'command': f'ramgate run --profile adhoc --mem 30 -- {v}/bin/python go.py'}, 'cwd': '/'})
    assert rg('check', '--hook', input=wrapped).returncode == 0
