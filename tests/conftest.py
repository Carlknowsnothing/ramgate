import os, subprocess, sys
from pathlib import Path
import pytest

PROJECT = Path(__file__).resolve().parents[1]
TOTAL_GIB = int(subprocess.check_output(['/usr/sbin/sysctl', '-n', 'hw.memsize'])) / 2**30


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolated state and config per test: a 1 GiB pool, swap/watermark/red-line rules disabled,
    other processes on the machine not counted."""
    cfgdir = tmp_path / 'config'; cfgdir.mkdir()
    (cfgdir / 'config.toml').write_text(
        f'reserve_gib = {TOTAL_GIB - 1}\nadmit_margin_gib = 0\nswap_quiet_seconds = 0\n'
        'redline_free_pct = 0\nredline_swap_rise_gib = 100000\nredline_pressure_seconds = 100000\n'
        'queue_report_seconds = 0.5\nsample_seconds = 0.1\nkill_grace_seconds = 1\nbig_unregistered_gib = 100000\n')
    (cfgdir / 'profiles.toml').write_text('[withenv]\nmem_gib = 1\nenv = { RAMGATE_TEST_VAR = "from-profile" }\nmatch = ["/opt/fake-model/.venv"]\n')
    monkeypatch.setenv('RAMGATE_HOME', str(tmp_path / 'state'))
    monkeypatch.setenv('RAMGATE_CONFIG_DIR', str(cfgdir))
    return tmp_path


def rg(*args, env=None, **kw):
    """Run the CLI in a subprocess; returns CompletedProcess."""
    e = {**os.environ, **(env or {})}
    return subprocess.run([sys.executable, '-m', 'ramgate', *map(str, args)], env=e, capture_output=True, text=True, cwd=PROJECT, **kw)


def rg_bg(*args, env=None):
    e = {**os.environ, **(env or {})}
    return subprocess.Popen([sys.executable, '-m', 'ramgate', *map(str, args)], env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=PROJECT)
