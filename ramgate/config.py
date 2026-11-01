"""Thresholds and profiles, in two layers: built-in defaults (shipped with the package) and the
user's ~/.config/ramgate/{config,profiles}.toml. Size-dependent thresholds default to a fraction of
physical RAM so the same rules work on a 16 GiB laptop and a 128 GiB workstation.

Environment: RAMGATE_CONFIG_DIR overrides the user config directory (tests point it at a temp dir)."""
import os, tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

PKG = Path(__file__).resolve().parent
BUILTIN_PROFILES = PKG / 'profiles.toml'


def user_dir():
    return Path(os.environ.get('RAMGATE_CONFIG_DIR') or '~/.config/ramgate').expanduser()


# Fractions of physical RAM used when a value is not set explicitly (and their floors in GiB).
AUTO = {
    'reserve_gib': (0.22, 6),            # kept for the OS and the agents themselves; 64 GiB -> 14
    'admit_margin_gib': (0.125, 2),      # live free memory must exceed budget by this much; 64 -> 8
    'redline_swap_rise_gib': (0.0625, 1),  # swap growth per window that counts as a red line; 64 -> 4
    'big_unregistered_gib': (0.0625, 1),   # unregistered model processes at least this big count as occupied; 64 -> 4
}


@dataclass
class Config:
    reserve_gib: float | None = None
    admit_margin_gib: float | None = None
    swap_quiet_seconds: float = 30      # no swap growth for this long before admitting
    redline_pressure_seconds: float = 3  # kernel "critical" must persist this long (weight loading spikes it for ~1 s)
    redline_free_pct: float = 12         # free memory below this -> red line
    redline_swap_rise_gib: float | None = None
    redline_window_seconds: float = 60
    redline_swap_free_pct: float = 25    # swap rule applies when free % is below this, or when the task is over its own budget
    redline_swap_recent_seconds: float = 5  # ...and only if swap is still rising over the last few seconds
    kill_cooldown_seconds: float = 60    # after a kill, the swap rule stays quiet this long (no cascades)
    overrun_warn_ratio: float = 1.3      # exceeding your own budget by this factor only warns
    history_days: int = 30               # budgets consider sustained peaks from the last N days
    queue_report_seconds: float = 60
    sample_seconds: float = 1.0
    kill_grace_seconds: float = 10
    big_unregistered_gib: float | None = None
    stop_cooldown_minutes: float = 30    # a command stopped from outside is refused this long (agents must ask first)

    def resolved(self, total_gib):
        """Fill size-dependent values from physical RAM; explicit values win."""
        for name, (frac, floor) in AUTO.items():
            if getattr(self, name) is None:
                setattr(self, name, round(max(floor, frac * total_gib)))
        return self

    @classmethod
    def load(cls, total_gib=None):
        path = user_dir() / 'config.toml'
        data = tomllib.loads(path.read_text()) if path.exists() else {}
        known = {f.name for f in fields(cls)}
        cfg = cls(**{k: v for k, v in data.items() if k in known})
        if total_gib is None:
            from . import sysmem
            total_gib = sysmem.total_gib()
        return cfg.resolved(total_gib)


@dataclass
class Profile:
    name: str
    mem_gib: float | None = None   # None: caller must pass --mem (hog, adhoc)
    resident: bool = False
    env: dict = field(default_factory=dict)
    match: list = field(default_factory=list)
    note: str = ''


def load_profiles():
    """Built-in profiles, overlaid with the user's. Same-named entries merge field by field;
    `match` lists are concatenated so the user can add their own paths without losing the defaults."""
    data = tomllib.loads(BUILTIN_PROFILES.read_text())
    path = user_dir() / 'profiles.toml'
    user_names = []
    if path.exists():
        for name, spec in tomllib.loads(path.read_text()).items():
            base = dict(data.get(name, {}))
            if 'match' in spec and 'match' in base: spec = {**spec, 'match': base['match'] + [m for m in spec['match'] if m not in base['match']]}
            base.update(spec); data[name] = base; user_names.append(name)
    # The user's own profiles are checked first by the hook, so a specific venv path beats a generic built-in match.
    order = user_names + [n for n in data if n not in user_names]
    return {name: Profile(name=name, **data[name]) for name in order}
