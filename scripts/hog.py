"""占内存的测试进程：hog.py <GiB> [保持秒=10] [每步GiB=0.5] [步间隔秒=0.3]。逐页写入，确保真占物理内存。"""
import os, sys, time

pattern = os.urandom(16384)

gib, hold = float(sys.argv[1]), float(sys.argv[2]) if len(sys.argv) > 2 else 10
step = float(sys.argv[3]) if len(sys.argv) > 3 else 0.5
pause = float(sys.argv[4]) if len(sys.argv) > 4 else 0.3
chunks, got = [], 0.0
while got < gib:
    n = int(min(step, gib - got) * 2**30)
    b = bytearray(pattern * (n // 16384))  # 每页都是随机字节：压缩器压不掉，才是真占内存
    chunks.append(b); got += n / 2**30
    print(f'hog {got:.1f} GiB', flush=True); time.sleep(pause)
print(f'hog 到位 {got:.1f} GiB，保持 {hold} 秒', flush=True)
time.sleep(hold)
print('hog 退出', flush=True)
