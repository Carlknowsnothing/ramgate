"""ramgate command line: run / status / history / profiles / check / init / install-hook."""
import json, shutil, sys, time
from pathlib import Path
from . import state, sysmem
from .config import AUTO, Config, load_profiles, user_dir

USAGE = """ramgate — one gate for every local model on a shared-memory Mac

  ramgate run --profile <name> [--mem GiB] [--name label] [--resident] [--no-wait|--timeout s] -- <command...>
  ramgate status              registered tasks, machine watermarks, remaining budget, unregistered big processes
  ramgate history [-n 20]     recent runs with measured peaks
  ramgate profiles            built-in + user profiles
  ramgate check '<command>'   exit 2 when the command launches a model without ramgate (Claude Code hook)
  ramgate init                write ~/.config/ramgate/{config,profiles}.toml templates
  ramgate install-hook claude [--remove]   add (or remove) the PreToolUse hook in ~/.claude/settings.json"""

PRESSURE = {1: 'normal', 2: 'warning', 4: 'critical'}


def status():
    cfg, snap = Config.load(), sysmem.snapshot()
    current = state.tasks(); used = sum(t['mem_gib'] for t in current)
    big = sysmem.big_processes({t.get('child_pid') for t in current} | {t['pid'] for t in current}, cfg.big_unregistered_gib)
    strangers = sum(fp for _, fp, _ in big)
    pool = snap.total_gib - cfg.reserve_gib
    print(f'machine {snap.total_gib:.0f} GiB, free {snap.free_gib:.0f} GiB ({snap.free_pct:.0f}%), swap used {snap.swap_used_gib:.1f} GiB, '
          f'kernel pressure {PRESSURE.get(snap.pressure_level, snap.pressure_level)}')
    print(f'budget: allocatable {pool:.0f}, registered {used:.0f}, unregistered big processes {strangers:.0f}, remaining {pool - used - strangers:.0f} GiB')
    if not current: print('registered tasks: none')
    for t in current:
        fp = sysmem.tree_footprint_gib(t.get('child_pid') or t['pid'])
        when = time.strftime('%m-%d %H:%M', time.localtime(t['started']))
        print(f"  {when}  {t['name']}  profile {t['profile']}  budget {t['mem_gib']:.0f}  footprint {fp:.1f} GiB  pid {t.get('child_pid') or t['pid']}"
              f"{'  resident' if t.get('resident') else ''}" if fp is not None else f"  {t['name']} (process gone)")
    if big:
        print('unregistered processes holding memory (listed only, never touched):')
        for pid, gib, cmd in big: print(f'  pid {pid}  {gib:.1f} GiB  {cmd[:90]}')
    return 0


def history(n):
    rows = state.history()
    for h in rows[-n:]:
        print(f"{time.strftime('%m-%d %H:%M', time.localtime(h['started']))}  {h['name'][:40]:40}  {h['profile']:12}  budget {h['mem_gib']:>5.1f}"
              f"  peak {h['peak_gib']:>5.1f}  min free {h['min_free_pct']:>3.0f}%  swap {h['swap_start_gib']:.1f}->{h['swap_end_gib']:.1f}"
              f"  {h['seconds']:>6.0f}s  rc {h['rc']}{'  killed: ' + str(h['reason']) if h.get('killed') else ''}")
    peaks = {}
    for h in rows:
        p = h.get('peak_sustained_gib') or (None if h.get('killed') else h.get('peak_gib'))  # same rule as budgets
        if p: peaks[h['profile']] = max(peaks.get(h['profile'], 0), p)
    if peaks: print('sustained peaks by profile: ' + ', '.join(f'{k} {v:.1f}' for k, v in peaks.items()))
    return 0


def profiles():
    for p in load_profiles().values():
        print(f"{p.name:14} {'%5.0f' % p.mem_gib if p.mem_gib else '--mem'} GiB{'  resident' if p.resident else ''}  {p.note}")
    return 0


def init():
    d = user_dir(); d.mkdir(parents=True, exist_ok=True)
    total = sysmem.total_gib(); auto = Config().resolved(total)
    cfg = d / 'config.toml'
    if not cfg.exists():
        lines = [f'# ramgate thresholds. Unset values are derived from physical RAM ({total:.0f} GiB here):']
        lines += [f'# {k} = {getattr(auto, k)}   ({int(frac * 100)}% of RAM, at least {floor})' for k, (frac, floor) in AUTO.items()]
        lines += ['# Uncomment to pin a value:', '# reserve_gib = 14', '',
                  '# redline_free_pct = 12', '# redline_pressure_seconds = 3', '# kill_cooldown_seconds = 60', '']
        cfg.write_text('\n'.join(lines)); print(f'wrote {cfg}')
    else: print(f'exists: {cfg}')
    prof = d / 'profiles.toml'
    if not prof.exists():
        prof.write_text('# Your profiles. Same-named entries override built-ins field by field; `match` lists are appended.\n'
                        '# [my-model]\n# mem_gib = 12\n# env = { PYTORCH_MPS_HIGH_WATERMARK_RATIO = "1.0" }\n'
                        '# match = ["/path/to/my-model/.venv"]\n# note = "where the number comes from"\n')
        print(f'wrote {prof}')
    else: print(f'exists: {prof}')
    return 0


def install_hook(args):
    if not args or args[0] != 'claude':
        print('usage: ramgate install-hook claude [--remove]', file=sys.stderr); return 2
    settings = Path('~/.claude/settings.json').expanduser()
    data = json.loads(settings.read_text()) if settings.exists() else {}
    pre = data.setdefault('hooks', {}).setdefault('PreToolUse', [])
    def ours(entry): return any('ramgate check' in h.get('command', '') or 'modelq check' in h.get('command', '') for h in entry.get('hooks', []))
    pre[:] = [e for e in pre if not ours(e)]
    if '--remove' not in args:
        exe = shutil.which('ramgate') or sys.argv[0]
        pre.append({'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': f'{exe} check --hook', 'timeout': 10}]})
    if not pre: data['hooks'].pop('PreToolUse')
    if not data['hooks']: data.pop('hooks')
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    print(('removed the ramgate hook from ' if '--remove' in args else 'installed the PreToolUse(Bash) hook in ') + str(settings)
          + ('' if '--remove' in args else '; new Claude Code sessions pick it up'))
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ('-h', '--help'): print(USAGE); return 0
    sub, rest = argv[0], argv[1:]
    if sub == 'run':
        from .run import main as run; return run(rest)
    if sub == 'status': return status()
    if sub == 'history': return history(int(rest[rest.index('-n') + 1]) if '-n' in rest else 20)
    if sub == 'profiles': return profiles()
    if sub == 'check':
        from .check import main as check; return check(rest)
    if sub == 'init': return init()
    if sub == 'install-hook': return install_hook(rest)
    print(USAGE, file=sys.stderr); return 2
