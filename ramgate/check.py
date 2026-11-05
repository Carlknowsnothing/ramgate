"""`ramgate check`: exit 2 when a command line looks like a direct model launch that bypasses ramgate.
With --hook it follows the Claude Code PreToolUse contract: JSON on stdin, reason on stderr, exit 2 blocks.

Two tests: profile `match` patterns (known tools), and the venv the command runs from — any venv with torch, mlx or
onnxruntime installed counts as a model launch, so a new tool is covered without anyone registering it first
(2026-11-05: three unlisted videoseal runs took 87 GiB). Only the executable position of each `;`/`&&`/`|` segment
is looked at, so `ls .venv/bin/python` or a grep through a venv passes."""
import glob, json, os, re, shlex, sys
from .config import load_profiles

ML_PACKAGES = ('torch', 'mlx', 'onnxruntime')
WRAPPERS = {'nohup', 'time', 'exec', 'env', 'caffeinate', 'command', 'nice', 'sudo'}
PYTHON = re.compile(r'^python(\d+(\.\d+)?)?$')


def ml_packages(root):
    return [p for p in ML_PACKAGES if glob.glob(os.path.join(root, 'lib', 'python*', 'site-packages', p))]


def _venv_of(path):
    """The venv root for <root>/bin/<exe>, when <root> holds a pyvenv.cfg."""
    root = os.path.dirname(os.path.dirname(path))
    return root if os.path.basename(os.path.dirname(path)) == 'bin' and os.path.isfile(os.path.join(root, 'pyvenv.cfg')) else None


def _harmless(tokens):
    args = tokens[1:3]
    return bool(args) and (args[0] in ('--version', '-V') or args == ['-m', 'pip'])


def ml_venv(command, cwd):
    """Root of the ML venv this command starts a program from, else None. `cd` and `source …/activate` are followed."""
    from pathlib import Path
    active = None
    def resolve(p): return os.path.normpath(os.path.join(cwd, os.path.expanduser(p)))
    for seg in re.split(r'&&|\|\||;|\||&|\n', command):
        try: tokens = shlex.split(seg)
        except ValueError: tokens = seg.split()
        while tokens and (tokens[0] in WRAPPERS or tokens[0].startswith('-') or re.match(r'^[A-Za-z_]\w*=', tokens[0])):
            tokens.pop(0)
        if not tokens: continue
        exe = tokens[0]
        if exe == 'cd':
            cwd = resolve(tokens[1]) if len(tokens) > 1 else str(Path.home()); continue
        if exe in ('source', '.') and len(tokens) > 1:
            root = _venv_of(resolve(tokens[1]))
            if root and ml_packages(root): active = root
            continue
        if PYTHON.match(exe) and active and not _harmless(tokens): return Path(active)
        if '/' in exe:
            root = _venv_of(resolve(exe))
            if root and ml_packages(root) and not _harmless(tokens): return Path(root)
    return None


def match(command, profiles=None):
    """Name of the matching profile, or None when nothing matches or the command already goes through ramgate."""
    if 'ramgate' in command: return None
    for p in (profiles or load_profiles()).values():
        if any(m in command for m in p.match): return p.name
    return None


def advice(profile, command):
    return (f'This command loads a local model directly (profile {profile}); it must queue through ramgate:\n'
            f'  ramgate run --profile {profile} -- {command}\n'
            f'False positive? Use `ramgate run --profile adhoc --mem <GiB> -- ...`. `ramgate status` shows who holds memory.')


def ml_advice(root, command):
    return (f'This command runs a program from {root}, a venv with {", ".join(ml_packages(root))} installed: treat it as a local model '
            f'and queue it through ramgate:\n  ramgate run --profile adhoc --mem <GiB> -- {command}\n'
            f'Pick --mem from what the tool needs (`ramgate history` shows measured peaks); a tool used often deserves a profile in '
            f'~/.config/ramgate/profiles.toml. Not a model run at all (tests, a quick script)? Wrap it with --mem 1.')


def verdict(command, cwd):
    """(exit code, message): 2 blocks."""
    if 'ramgate' in command: return 0, ''
    hit = match(command)
    if hit: return 2, advice(hit, command)
    root = ml_venv(command, cwd)
    if root: return 2, ml_advice(root, command)
    return 0, ''


def main(argv):
    if argv and argv[0] == '--hook':
        try:
            data = json.load(sys.stdin)
            command, cwd = data.get('tool_input', {}).get('command', ''), data.get('cwd') or os.getcwd()
        except (json.JSONDecodeError, AttributeError): return 0
        rc, msg = verdict(command, cwd)
        if rc: print(msg, file=sys.stderr)
        return rc
    rc, msg = verdict(' '.join(argv), os.getcwd())
    if rc: print(msg)
    return rc
