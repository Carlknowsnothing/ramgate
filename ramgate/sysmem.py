"""macOS memory readings. Machine level: kern.memorystatus_level (the free percentage Jetsam itself uses),
vm.swapusage and kern.memorystatus_vm_pressure_level; per process: proc_pid_rusage physical footprint."""
import ctypes, functools, re, struct, subprocess
from dataclasses import dataclass

GIB = 2**30


def _sysctl(name):
    return subprocess.run(['/usr/sbin/sysctl', '-n', name], capture_output=True, text=True, check=True).stdout.strip()


@functools.lru_cache(maxsize=None)
def total_gib():
    return int(_sysctl('hw.memsize')) / GIB


@dataclass
class Snapshot:
    total_gib: float
    free_pct: float
    free_gib: float
    swap_used_gib: float
    pressure_level: int = 1  # kern.memorystatus_vm_pressure_level：1 正常、2 警告、4 严重


def swap_used_gib():
    m = re.search(r'used = ([\d.]+)([MG])', _sysctl('vm.swapusage'))
    return float(m[1]) / (1024 if m[2] == 'M' else 1) if m else 0.0


def snapshot():
    total = total_gib()
    pct = float(_sysctl('kern.memorystatus_level'))
    try: level = int(_sysctl('kern.memorystatus_vm_pressure_level'))
    except (ValueError, subprocess.CalledProcessError): level = 1
    return Snapshot(total_gib=total, free_pct=pct, free_gib=total * pct / 100, swap_used_gib=swap_used_gib(), pressure_level=level)


_libproc = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)


def footprint_gib(pid):
    """进程 physical footprint（含压缩页），进程不存在返回 None。rusage_info_v0 偏移 72 是 ri_phys_footprint。"""
    buf = ctypes.create_string_buffer(1024)
    if _libproc.proc_pid_rusage(int(pid), 0, ctypes.byref(buf)) != 0:
        return None
    return struct.unpack_from('Q', buf.raw, 72)[0] / GIB


def descendants(pid):
    """pid 的所有后代（bash 包装、分段规划的子进程等）。"""
    children = {}
    for line in subprocess.run(['/bin/ps', '-axo', 'pid=,ppid='], capture_output=True, text=True).stdout.splitlines():
        p, pp = line.split()
        children.setdefault(int(pp), []).append(int(p))
    out, todo = [], [int(pid)]
    while todo:
        for c in children.get(todo.pop(), []): out.append(c); todo.append(c)
    return out


def tree_footprint_gib(pid):
    """进程树合计 footprint；根进程不在返回 None。"""
    root = footprint_gib(pid)
    if root is None: return None
    return root + sum(footprint_gib(c) or 0 for c in descendants(pid))


MODEL_HINTS = ('python', 'mlx', 'comfy', 'acestep', 'ltx', 'whisper')


def big_processes(known_pids, min_gib):
    """没登记却占着内存的模型类进程 [(pid, footprint_gib, 命令)]，大的在前。footprint 含压缩和换出的页，是进程的总占用。"""
    out = []
    for line in subprocess.run(['/bin/ps', '-axo', 'pid=,command='], capture_output=True, text=True).stdout.splitlines():
        pid, _, cmd = line.strip().partition(' ')
        if not pid.isdigit() or int(pid) in known_pids: continue
        if not any(k in cmd.lower() for k in MODEL_HINTS): continue
        fp = footprint_gib(int(pid))
        if fp and fp >= min_gib: out.append((int(pid), fp, cmd))
    return sorted(out, key=lambda x: -x[1])
