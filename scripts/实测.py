"""实测脚本：按场景启动若干 ramgate run，期间每 0.5 秒采整机空闲、swap、内核压力等级，结束后打印汇总（Markdown 片段）。
用法：实测.py <场景名> -- <命令1> ;; <命令2> ...   命令之间用 ;; 分隔，依次相隔 start_gap 秒启动。
环境变量 START_GAP（默认 2 秒）。"""
import json, os, re, shlex, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ramgate import sysmem

name = sys.argv[1]
cmds = [c.strip() for c in ' '.join(sys.argv[3:]).split(';;')]
gap = float(os.environ.get('START_GAP', 2))
LEVEL = {'1': '正常', '2': '警告', '4': '严重'}


def pressure():
    return subprocess.run(['/usr/sbin/sysctl', '-n', 'kern.memorystatus_vm_pressure_level'], capture_output=True, text=True).stdout.strip()


base = sysmem.snapshot(); base_level = pressure()
print(f'# 场景：{name}\n基线：空闲 {base.free_gib:.1f} GiB（{base.free_pct:.0f}%），swap {base.swap_used_gib:.2f} GiB，压力 {LEVEL.get(base_level, base_level)}')
procs, logs, t0 = [], [], time.time()
samples = []
next_start = 0
while True:
    if next_start < len(cmds) and time.time() - t0 >= next_start * gap:
        log = open(f'/tmp/ramgate-实测-{name}-{next_start}.log', 'w'); logs.append(log.name)
        procs.append(subprocess.Popen(shlex.split(cmds[next_start]), stdout=log, stderr=subprocess.STDOUT)); next_start += 1
        print(f'{time.time() - t0:6.1f}s 启动 {next_start}: {cmds[next_start - 1][:80]}')
    s = sysmem.snapshot(); lv = pressure()
    samples.append((time.time() - t0, s.free_gib, s.free_pct, s.swap_used_gib, lv))
    if next_start == len(cmds) and all(p.poll() is not None for p in procs): break
    time.sleep(0.5)
end = sysmem.snapshot()
worst = min(samples, key=lambda x: x[1]); max_swap = max(x[3] for x in samples)
levels = sorted({x[4] for x in samples})
print(f'退出码：{[p.returncode for p in procs]}')
print(f'最低空闲：{worst[1]:.1f} GiB（{worst[2]:.0f}%，第 {worst[0]:.0f} 秒）')
print(f'swap：基线 {base.swap_used_gib:.2f} → 最高 {max_swap:.2f} → 结束 {end.swap_used_gib:.2f} GiB（涨 {max_swap - base.swap_used_gib:+.2f}）')
print(f'内核压力等级出现过：{"、".join(LEVEL.get(l, l) for l in levels)}')
for i, l in enumerate(logs):
    txt = Path(l).read_text()
    lines = [x for x in txt.splitlines() if x.startswith('[ramgate]')]
    print(f'\n任务 {i + 1} 的 ramgate 日志：'); print('\n'.join('    ' + x for x in lines))
Path(f'/tmp/ramgate-实测-{name}-samples.json').write_text(json.dumps(samples))
