# ramgate 中文说明

共享内存 Mac 上所有本地模型的统一入口。起因是 2026-10-24 三个互不知情的模型进程合计 100 GB 触发 Jetsam；英文 README 是完整说明，这里只写用法和本机专属的部分。

## 用法

```bash
ramgate run --profile ace-xl-split -- /path/.venv/bin/python run.py r3   # 排到额度才启动，否则排队并说明原因
ramgate run --profile comfyui --resident -- python main.py --listen …    # 常驻服务，额度一直占着
ramgate run --profile adhoc --mem 8 -- …                                  # 没有档案的临时任务
ramgate stop <id|名字>   # 停掉一个任务；之后同一条命令冷却期内被拒
ramgate unblock     # 清掉所有停止标记
ramgate status      # 谁在占内存、预算余量、未登记的大进程、停止标记
ramgate history     # 近期任务实测峰值
ramgate profiles    # 内置＋用户档案
```

进程内：

```python
import ramgate  # 用 uv tool 安装的话脚本里 sys.path 指向仓库目录即可
lease = ramgate.acquire('ace-2b', name='r5')   # 对象要一直留着；进程退出自动注销并记历史
```

父进程已经拿到额度、子进程不必再排：给子进程环境加 `RAMGATE_HELD=1`（`ramgate run` 已自动加）。

## 规则

- 可分配 = 物理内存 − 保留（默认 22%、至少 6 GiB；64 GiB 机器是 14 → 池子 50）。
- 放行：已占（登记任务取 max(预算, footprint)，加上没登记的 ≥ 4 GiB 模型进程的 footprint）+ 本任务 ≤ 池子，且实时空闲扣掉在跑任务未用完的预算后 ≥ 本任务预算 + 8 GiB，且 30 秒内 swap 没涨；否则排队（`--no-wait` 立即退出码 3，`--timeout` 到期同样）。什么都没在跑时，比池子还大的任务可独占跑。
- 预算 = `--mem`，否则档案值与近 30 天实测持续峰值 ×1.1 取大。
- 红线：内核压力“严重”持续 3 秒，或整机空闲 < 12%，或 60 秒内 swap 涨 > 4 GiB 且还在涨（杀过一个后冷却 60 秒）→ **最晚登记的任务**终止自己的子进程（SIGTERM，10 秒后 SIGKILL，退出码 137）。先起的任务和常驻服务不受连累；不是 ramgate 启动的进程只在 status 里点名。
- 超出自己预算 1.3 倍只告警。
- 会话绑定：`ramgate run` 往上找最近的 `claude` / `codex` 祖先进程（agent 会话），会话退出就结束任务的整个进程组，`nohup … &` 起的渲染也不会比对话活得久。终端里手动起的不绑定（关终端有 SIGHUP 转发）。`--resident`、`--detach` 不绑定；`RAMGATE_OWNER_PID` 可指定（0 = 不绑）。
- 停了就别重拉：任务被外部停掉（`ramgate stop`、kill ramgate 或子进程、Ctrl-C）后，按「命令＋目录」留停止标记，30 分钟内（`stop_cooldown_minutes`）同一条命令退出码 4 被拒，提示 agent 先问人。`--force` 照跑并清标记，`ramgate unblock` 全清。红线和会话结束造成的终止不留标记。不限 CPU，渲染可以用满所有核。
- macOS 靠把别的进程换出去维持“空闲”好看，所以 swap 增速才是主要信号；进程 footprint（含 Metal 分配和压缩页）比 MLX 在用峰值高一截（XL 渲染 45 vs 26.5）。

## 本机专属设置

机器专属的档案（含本机路径）放 `~/.config/ramgate/profiles.toml`，阈值放同目录 `config.toml`，不进仓库。各项目入口脚本怎么接、规则文件写在哪，见本机的开发记录（不入库）。

## 开发

```bash
.venv/bin/python -m pytest tests -q          # 35 个测试，约 20 秒
scripts/实测.py <场景> -- <命令1> ;; <命令2>   # 压力实测
```

状态在 `~/.local/state/ramgate/`。
