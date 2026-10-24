"""Registrations and history. Every running task owns tasks/<id>.json and holds an flock on it: the lock
dies with the process, so any record whose lock can be taken is a zombie and gets removed on the next read.
That is why no daemon is needed and a crashed task never holds budget forever.

State lives in ~/.local/state/ramgate (override with RAMGATE_HOME)."""
import contextlib, fcntl, json, os, time
from pathlib import Path


def home():
    return Path(os.environ.get('RAMGATE_HOME') or '~/.local/state/ramgate').expanduser()


def tasks_dir():
    d = home() / 'tasks'; d.mkdir(parents=True, exist_ok=True); return d


class Registration:
    """A held registration; keep it alive. close() or process exit releases it."""
    def __init__(self, path, fh, record):
        self.path, self.fh, self.record = path, fh, record

    def update(self, **fields):
        self.record.update(fields)
        self.fh.seek(0); self.fh.truncate(); self.fh.write(json.dumps(self.record, ensure_ascii=False)); self.fh.flush()

    def close(self):
        with contextlib.suppress(FileNotFoundError): self.path.unlink()
        self.fh.close()


def register(record):
    record = {'started': time.time(), **record}
    record.setdefault('id', f"{record['started']:.3f}-{record.get('pid', os.getpid())}")
    path = tasks_dir() / f"{record['id']}.json"
    fh = open(path, 'w')
    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    reg = Registration(path, fh, {}); reg.update(**record)
    return reg


def tasks():
    """Live registrations sorted by start time; zombies are cleaned up on the way."""
    alive = []
    for path in sorted(tasks_dir().glob('*.json')):
        try: fh = open(path, 'r+')
        except FileNotFoundError: continue
        with fh:
            try: fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                for _ in range(5):  # the owner may be rewriting it right now
                    try: alive.append(json.loads(fh.read())); break
                    except json.JSONDecodeError: time.sleep(0.02); fh.seek(0)
                continue
            with contextlib.suppress(FileNotFoundError): path.unlink()
    return sorted(alive, key=lambda t: t['started'])


@contextlib.contextmanager
def admit_lock():
    """Reading state and registering must happen under one lock, or two waiters admit each other."""
    home().mkdir(parents=True, exist_ok=True)
    with open(home() / 'admit.lock', 'a') as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield


def history_path():
    return home() / 'history.jsonl'


def append_history(record):
    home().mkdir(parents=True, exist_ok=True)
    with open(history_path(), 'a') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')


def history():
    try: lines = history_path().read_text().splitlines()
    except FileNotFoundError: return []
    out = []
    for l in lines:
        with contextlib.suppress(json.JSONDecodeError): out.append(json.loads(l))
    return out


def recent_peaks(profile, days):
    """Sustained peaks (held for 3 consecutive samples) of recent runs of this profile. A one-second
    spike while loading weights must not inflate the budget; killed runs without a sustained value are skipped."""
    since = time.time() - days * 86400
    peaks = [h.get('peak_sustained_gib') or (None if h.get('killed') else h['peak_gib'])
             for h in history() if h.get('profile') == profile and h.get('ended', 0) >= since and h.get('peak_gib')]
    return [p for p in peaks if p]


def note_kill():
    """Remember the last red-line kill: swap takes tens of seconds to settle, nobody else should act yet."""
    home().mkdir(parents=True, exist_ok=True)
    (home() / 'last_kill').write_text(str(time.time()))


def seconds_since_kill():
    try: return time.time() - float((home() / 'last_kill').read_text())
    except (FileNotFoundError, ValueError): return float('inf')
