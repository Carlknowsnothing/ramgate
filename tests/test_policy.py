from ramgate.config import Config
from ramgate import policy

CFG = Config().resolved(64)  # auto thresholds on a 64 GiB machine: reserve 14 (pool 50), margin 8, swap 4


def test_auto_thresholds_scale_with_ram_and_have_floors():
    assert (CFG.reserve_gib, CFG.admit_margin_gib, CFG.redline_swap_rise_gib, CFG.big_unregistered_gib) == (14, 8, 4, 4)
    small = Config().resolved(16)
    assert (small.reserve_gib, small.admit_margin_gib, small.redline_swap_rise_gib) == (6, 2, 1)
    assert Config(reserve_gib=20).resolved(64).reserve_gib == 20  # explicit wins


def test_budget_prefers_override():
    assert policy.budget_gib(profile_mem=28, override=10, recent_peaks=[30]) == 10


def test_budget_takes_max_of_profile_and_recent_peak_times_1_1():
    assert policy.budget_gib(28, None, []) == 28
    assert policy.budget_gib(28, None, [20, 30]) == 33  # 30 x 1.1
    assert policy.budget_gib(28, None, [20]) == 28


def test_admit_requires_budget_watermark_and_quiet_swap():
    ok, why = policy.admit(need_gib=28, committed_gib=10, total_gib=64, free_gib=40, swap_rising=False, cfg=CFG)
    assert ok, why
    ok, why = policy.admit(28, committed_gib=25, total_gib=64, free_gib=40, swap_rising=False, cfg=CFG)
    assert not ok and 'budget' in why  # 25+28 > 50
    ok, why = policy.admit(28, 10, 64, free_gib=35, swap_rising=False, cfg=CFG)
    assert not ok and 'free memory' in why  # 35 < 28+8
    ok, why = policy.admit(28, 10, 64, free_gib=40, swap_rising=True, cfg=CFG)
    assert not ok and 'swap' in why


def test_admit_discounts_budget_not_yet_used_by_running_tasks():
    # a running task registered 20 but uses 3 so far: it will take 17 more, so free 31 is really 14
    ok, why = policy.admit(20, committed_gib=20, total_gib=64, free_gib=31, swap_rising=False, cfg=CFG, unrealized_gib=17)
    assert not ok and 'unused budget' in why
    ok, _ = policy.admit(20, 20, 64, free_gib=31, swap_rising=False, cfg=CFG, unrealized_gib=0)
    assert ok


def test_task_bigger_than_pool_runs_alone_but_never_with_others():
    ok, _ = policy.admit(need_gib=55, committed_gib=0, total_gib=64, free_gib=60, swap_rising=False, cfg=CFG)
    assert ok  # exclusive
    ok, why = policy.admit(55, committed_gib=4, total_gib=64, free_gib=60, swap_rising=False, cfg=CFG)
    assert not ok and 'budget' in why


def test_redline_on_low_free_or_swap_surge():
    assert policy.redline(free_pct=11, swap_rise_gib=0, cfg=CFG)
    assert policy.redline(free_pct=20, swap_rise_gib=4.5, cfg=CFG, swap_rise_recent_gib=0.3)
    assert policy.redline(free_pct=12, swap_rise_gib=3.9, cfg=CFG) is None


def test_swap_redline_needs_rising_swap_and_kernel_critical_needs_persistence():
    assert policy.redline(free_pct=36, swap_rise_gib=5, cfg=CFG, swap_rise_recent_gib=0.5) is None  # within budget, free still high: a server loading weights
    assert policy.redline(free_pct=36, swap_rise_gib=5, cfg=CFG, swap_rise_recent_gib=0.5, over_budget=True)  # runaway
    assert policy.redline(free_pct=20, swap_rise_gib=5, cfg=CFG, swap_rise_recent_gib=0.5)  # free already low
    assert policy.redline(free_pct=20, swap_rise_gib=5, cfg=CFG, swap_rise_recent_gib=0.0) is None  # no longer rising
    assert policy.redline(free_pct=60, swap_rise_gib=0, cfg=CFG, critical_seconds=3)
    assert policy.redline(free_pct=60, swap_rise_gib=0, cfg=CFG, critical_seconds=1) is None  # a 1 s flash


def test_swap_redline_cools_down_after_a_kill():
    assert policy.redline(free_pct=20, swap_rise_gib=5, cfg=CFG, swap_rise_recent_gib=0.5, seconds_since_kill=3) is None
    assert policy.redline(free_pct=20, swap_rise_gib=5, cfg=CFG, swap_rise_recent_gib=0.5, seconds_since_kill=61)
    assert policy.redline(free_pct=5, swap_rise_gib=0, cfg=CFG, seconds_since_kill=3)  # low free never waits for cooldown
