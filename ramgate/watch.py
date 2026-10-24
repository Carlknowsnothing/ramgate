"""Background sampler: machine free memory, swap, kernel pressure and the watched process tree footprint.
Used while queued (is swap rising?) and while running (red lines, peaks)."""
import collections, threading, time
from . import sysmem


class Sampler(threading.Thread):
    def __init__(self, cfg, pid=None):
        super().__init__(daemon=True)
        self.cfg, self.pid = cfg, pid
        self.series = collections.deque(maxlen=int(600 / max(cfg.sample_seconds, 0.05)))  # (时间, swap, free_pct)
        self.peak_gib = 0.0; self.peak_sustained_gib = 0.0; self.min_free_pct = 100.0
        self._recent_fp = collections.deque(maxlen=3); self.critical_since = None
        self.last = None; self._stop = threading.Event(); self._lock = threading.Lock()
        self.on_sample = None  # 每次采样后的回调（看红线）

    def watch(self, pid):
        self.pid = pid

    def sample_once(self):
        s = sysmem.snapshot()
        fp = sysmem.tree_footprint_gib(self.pid) if self.pid else None  # 含子孙进程
        with self._lock:
            self.last = s
            self.series.append((time.time(), s.swap_used_gib, s.free_pct))
            if fp:
                self.peak_gib = max(self.peak_gib, fp); self._recent_fp.append(fp)
                if len(self._recent_fp) == 3: self.peak_sustained_gib = max(self.peak_sustained_gib, min(self._recent_fp))  # 连续 3 次采样都到的值，瞬间尖峰不算
            if s.pressure_level >= 4: self.critical_since = self.critical_since or time.time()
            else: self.critical_since = None
            if self.pid: self.min_free_pct = min(self.min_free_pct, s.free_pct)
        return s, fp

    def run(self):
        while not self._stop.is_set():
            try:
                s, fp = self.sample_once()
                if self.on_sample: self.on_sample(s, fp)
            except Exception as e:  # 采样失败不杀任务
                print(f"[ramgate] sampling failed: {e}", flush=True)
            self._stop.wait(self.cfg.sample_seconds)

    def critical_seconds(self):
        return time.time() - self.critical_since if self.critical_since else 0.0

    def stop(self):
        self._stop.set()

    def swap_rise(self, window_seconds):
        """窗口内 swap 的涨幅（现在 − 窗口起点）。"""
        if window_seconds <= 0: return 0.0
        with self._lock:
            if not self.series: return 0.0
            now, swap_now, _ = self.series[-1]
            old = [sw for t, sw, _ in self.series if t <= now - window_seconds] or [self.series[0][1]]
            return swap_now - old[-1] if len(self.series) > 1 else 0.0

    def swap_rising(self, window_seconds):
        return self.swap_rise(window_seconds) > 0.05  # 50 MiB 以内当没涨
