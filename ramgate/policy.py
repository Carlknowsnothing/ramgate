"""Admission, red lines and budgets: pure functions, no system reads. Everything measured on
2026-10-25 on a 64 GiB M5 Pro; see docs/ for the runs behind each rule."""


def budget_gib(profile_mem, override, recent_peaks):
    """--mem wins; otherwise the larger of the profile value and 1.1 × the recent sustained peak,
    so an underestimated profile corrects itself after one run."""
    if override is not None:
        return float(override)
    peak = max(recent_peaks) * 1.1 if recent_peaks else 0
    return round(max(float(profile_mem or 0), peak), 2)


def admit(need_gib, committed_gib, total_gib, free_gib, swap_rising, cfg, unrealized_gib=0.0):
    """All three conditions must hold; otherwise returns the reason shown while queued.

    committed_gib: memory already spoken for — registered tasks count max(budget, footprint), plus the
    footprint of big *unregistered* model processes. Without the latter the kernel happily swaps an idle
    resident model out to make room, swap jumps 18 GiB and "free" still looks fine.
    unrealized_gib: budget that registered tasks have not consumed yet; they are about to, so it is
    subtracted from live free memory (a task 2 s into loading weights has not shown up in free yet)."""
    pool = total_gib - cfg.reserve_gib
    if committed_gib == 0:
        # Exclusive run: with nothing else running, a task bigger than the whole pool may still run alone
        # (XL rendering: 45 GiB sustained footprint on a 64 GiB machine), otherwise it would queue forever.
        need_gib = min(need_gib, pool)
    if committed_gib + need_gib > pool:
        return False, f'budget: {committed_gib:.0f} already held + {need_gib:.0f} for this task > {pool:.0f} GiB allocatable'
    expect = free_gib - unrealized_gib
    if expect < need_gib + cfg.admit_margin_gib:
        return False, (f'free memory {free_gib:.0f} GiB minus {unrealized_gib:.0f} unused budget of running tasks '
                       f'= {expect:.0f}, need >= {need_gib:.0f}+{cfg.admit_margin_gib:.0f}')
    if swap_rising:
        return False, f'swap still growing over the last {cfg.swap_quiet_seconds:.0f} s'
    return True, ''


def redline(free_pct, swap_rise_gib, cfg, swap_rise_recent_gib=None, critical_seconds=0.0, seconds_since_kill=float('inf'), over_budget=False):
    """Returns the reason when a red line is crossed, else None.

    Three lines: kernel pressure "critical" sustained for a few seconds (loading XL weights flashes critical
    for ~1 s and recovers — a single sample must not kill); free memory too low; swap growth over the window
    above the limit *and* still rising *and* no kill in the last minute (after a kill the window still shows
    the growth, so without cooldown the next-youngest task dies too) *and* either this task is over its own
    budget or free memory is already low. macOS keeps "free" looking healthy by swapping other processes out,
    so on Apple Silicon the swap line is the one that actually fires — but a server loading 30 GiB of weights
    within its budget also pushes a few GiB of idle app pages to swap, and that must not count as a runaway
    (2026-10-25: a ComfyUI was killed 40 s after start that way)."""
    if critical_seconds >= cfg.redline_pressure_seconds:
        return f'kernel memory pressure critical for {critical_seconds:.0f} s'
    if free_pct < cfg.redline_free_pct:
        return f'free memory {free_pct:.0f}% < {cfg.redline_free_pct:.0f}%'
    still_rising = swap_rise_recent_gib is None or swap_rise_recent_gib > 0.05
    cooling = seconds_since_kill < cfg.kill_cooldown_seconds
    culpable = over_budget or free_pct < cfg.redline_swap_free_pct
    if swap_rise_gib > cfg.redline_swap_rise_gib and still_rising and not cooling and culpable:
        return (f'swap grew {swap_rise_gib:.1f} GiB in {cfg.redline_window_seconds:.0f} s and is still rising '
                f'({"over its budget" if over_budget else f"free {free_pct:.0f}%"})')
    return None
