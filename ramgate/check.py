"""`ramgate check`: exit 2 when a command line looks like a direct model launch that bypasses ramgate.
With --hook it follows the Claude Code PreToolUse contract: JSON on stdin, reason on stderr, exit 2 blocks."""
import json, sys
from .config import load_profiles


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


def main(argv):
    if argv and argv[0] == '--hook':
        try: command = json.load(sys.stdin).get('tool_input', {}).get('command', '')
        except (json.JSONDecodeError, AttributeError): return 0
        hit = match(command)
        if hit: print(advice(hit, command), file=sys.stderr); return 2
        return 0
    command = ' '.join(argv)
    hit = match(command)
    if hit: print(advice(hit, command)); return 2
    return 0
