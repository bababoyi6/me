# 单 VPS 实验室（开发候选）

本分支基于 1.5.0，尚未发布或完成生产验收。只允许在已授权测试机运行。它不需要重装 Ubuntu、开公网监听、修改宿主防火墙/网卡、增加 VPS，或安装三个完整虚拟机。

## 先规划，再运行

```bash
python3 tools/lab_preflight.py
python3 tools/lab_runner.py --root /root/tdh-audit-lab --profile smoke
```

第二条命令只打印计划，不创建目录、namespace 或服务。预检仅读取系统资源、服务名称、监听端口和工具能力，不读取密钥、Secret、SSH 配置、项目状态文件或服务日志。操作者仍须确认其他业务不能受到影响。

需要 Linux root、systemd、cgroup v2 的 memory/cpu/pids 控制器、Python cryptography、iproute2、ethtool、WireGuard 工具、HAProxy。缺少条件会拒绝执行，不自动安装依赖、扩容 Swap 或退化为无资源上限的高权限实验。没有 Linux 权限时，可运行单元测试与 `tests/lab.py` 的回环 HAProxy 测试；这不覆盖 WireGuard、内核网络隔离或完整三角色部署。

为避免重复下载/编译，复用已验证实验核心。专用的 root 所有、0700 的 `tdh-audit-*` 目录应事先放入：

| 文件 | 用途 |
|---|---|
| `telemt-auto`、`auto-build.json` | audit2，元数据包含已确认的 `sha256` 和 `version` |
| `telemt-flush`、`flush-build.json` | flush-only 对照；A/B 需要 |
| `telemt-3.5.5/telemt` | 原版工具的可选官方对照 |

元数据必须来自可信构建记录，不能从待执行的未知文件自行计算摘要后当作可信依据。现有已测二进制摘要和复现流程见 [patches/README](../patches/README.md)。工具会在执行前再次验证文件与版本，并在复制后复核摘要。

## 功能与故障恢复

```bash
sudo python3 tools/lab_runner.py --root /root/tdh-audit-lab --profile smoke --execute
```

入口、WAN 路由、后端 A、后端 B、模拟 DC 共五个轻量 network namespace。入口没有接入宿主公网；两条实际 WireGuard 隧道使用独立密钥和 PSK，HAProxy 原样转发 FakeTLS/PROXYv2；两个 Telemt 后端经独立 veth 访问模拟 DC。模拟 DC 的两个地址位于同一个独立 namespace，分别记录两个后端的请求。它们仍共享同一宿主 CPU/内存。

覆盖双 SNI、分片 ClientHello、SHA256 完整性、agent down/up、主备选择、HAProxy 重载期间的既有传输、实际 Telemt 后端进程停止/重建、第一条实验 WG 接口断开/恢复。这里的 agent 是受控故障注入器，**不等于生产 API + 五 DC 健康检查的端到端验收**；生产 agent 逻辑另由回归验证。故障切换只能重新分配新连接，不能迁移既有 TCP 会话。

namespace 使用全宿主的专用互斥锁，防止不同实验目录同时操作固定名称。遇到已有实验 namespace 或不安全的目录/锁文件时拒绝继续。清理只删除本次成功创建的 namespace 与密钥；删除失败会明确报错，不把失败标为清理成功。不要手工清理来源不明的残留。

## 上传 A/B 与资源升级

```bash
sudo python3 tools/lab_runner.py --root /root/tdh-audit-lab --profile ab --execute
```

默认 100ms RTT、100Mbps、1/4 并发、每项总计 16MiB、3 轮。每对对照使用相同数据量和条件，以 `flush→auto / auto→flush / flush→auto` 顺序交替，顺序运行，避免两个测试器相互争抢 CPU。记录上传、小文件探测、进程 CPU/RSS、宿主可用内存和 namespace/qdisc 计数；不将重传直接解释为丢包根因。小文件时延包含建连和握手，不等于已有聊天会话的消息时延。

这里的 `flush` 是 audit1，`auto` 是 audit2；**它不是 1.5.0 与本开发分支的两种新旧核心**。本分支没有引入新的吞吐补丁，audit2 与 1.5.0 默认核心字节相同。

默认关闭详细 socket 查询，需要时加 `--socket-details`。新版通过 Python 标准库直接读取 Netlink，不再逐次启动 `ss` 或 `ip netns exec … tc`。每次观测的目标期限为2秒，最多4MiB、4096条；不完整、截断或中断的 dump 会判为测量失败。系统调用、调度或内存回收仍可能超过期限；v2 在返回后再次核对包含描述符读取/解码/namespace 恢复的总耗时，迟到样本也判为无效，不承诺硬实时抢占。socket 查询只允许私有客户端 namespace，以进程持有的 socket inode 与两侧地址筛选；最多每秒一次，保存每批耗时、样本编号、缓冲上限和实际记账字段。缓冲上限不是实际内存占用，各内核记账字段也不能任意相加。

qdisc 查询只进入固定的三个实验 namespace，并在主线程 `finally` 中恢复原网络环境；恢复失败会终止 worker。先用低负载核对工具验证读数，再进入 A/B：

```bash
sudo python3 tools/lab_runner.py --root /root/tdh-audit-lab --profile observer --execute
```

该核对使用真实 `ss`、`tc`、socket 参数及稳定计数，仍由同一资源 supervisor 管理。它只证明所测内核/字段，不证明所有内核 ABI。旧的全量/过滤命令观测器曾在32流实验中超时，失败记录继续保留。观测失败时保存已完成传输并将 measurement 标记无效，不据此发布提速比例。关闭详细查询不提供 socket 样本；不同观测器或模式的结果不能混算提速。

数据走原始 DC TCP 段，虽然初始化了 WG，该 A/B 的有效载荷不经过 WG。应标记 `SINGLE_VPS_SYNTHETIC`；完整拓扑功能测试标记 `SINGLE_VPS_VIRTUAL_3NODE`。两者都不是 Telegram 文件测速。

资源良好时才逐级测试 8、16 条大流。32 条只用于明确的回退调查，先检查前级的 supervisor 资源报告，再决定执行：

```bash
sudo python3 tools/lab_runner.py --root /root/tdh-audit-lab --profile ab \
  --min-streams 8 --max-streams 16 --rounds 1 --execute
# 仅在前级通过且资源有余量时：每流 8MiB、3轮、1Gbps合成限额
sudo python3 tools/lab_runner.py --root /root/tdh-audit-lab --profile ab \
  --min-streams 32 --max-streams 32 --payload-mib 256 --rate-mbps 1000 --execute
python3 tools/analyze_upload_ab.py /root/tdh-audit-lab/results/ab-实际编号.json
```

每轮 worker 有有限超时。失败会保留结果并停止后续负载；缺失、重复或失败的配对不产生提速比例，不同数据量/带宽/环境的结果不能混算。RTT 可选 20/50/100/200ms。已有 `tests/high_rtt_matrix.py` 保留受控丢包测试，但老入口不自动获得新 supervisor 的资源保护，不应直接在唯一测试机上运行高负载。

## 资源与清理

预检为宿主保留 `max(256MiB, 总内存/3)`；实验内存上限为 `min(384MiB, (可用内存−预留)×3/4)`，不足 192MiB 则拒绝。还要求至少 768MiB 总内存、1GiB 剩余磁盘。内存高水位取上限的 3/4，实验禁用 Swap，CPUQuota 为整颗 CPU 的 60%，TasksMax=192，单次运行最长 600 秒。它们是**实验进程组预算**，不是生产 Telemt 容量推荐，也不能完全限制内核后台线程的 CPU。

每 0.5 秒检查宿主可用内存、OOM 计数、cgroup 事件和新增输出；跌破预留、OOM、输出约 32MiB 或磁盘低于 512MiB 时停止本次 unit。采样不是连续的绝对峰值证明；Ubuntu 5.15 缺少 `memory.peak` 时，报告只给采样最大值。输出阈值存在一次采样间隔的超量可能，单文件另有硬限制。

每次生成独立 `run-tdh-audit-*` 目录，保存命令、源码摘要、源码基准提交/dirty 标识、资源预算、原始日志、采样和清理核查。systemd 只停止本次随机生成的 unit，并回收其整个进程组。没有新增常驻维护服务。

```bash
# 受限约5分钟低速长流；通过 smoke 后再执行
sudo python3 tools/lab_runner.py --root /root/tdh-audit-lab --profile soak --execute
```

退出后必须检查 `supervisor.json` 的退出码、stop_reason、残留 namespace、密钥文件及 cgroup PID。SIGKILL、宿主断电或内核故障仍可能留下 namespace；工具会报告残留，不能盲目删除其他任务资源。全局 `/run/lock/tdh-audit-network.lock` 为零字节协调文件，保留它可避免解除锁与新实验之间的竞态。

导出结果后，操作者可删除**已确认归属本次实验的专用目录**。不得清理历史实验、非项目服务或宿主配置。三角色 systemd 安装、重启持久化仍应复用已有分阶段隔离 guest 方法；本工具的进程拓扑通过不等于新版本完整安装验收。
