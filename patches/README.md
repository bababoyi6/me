# Telemt 后端实验补丁

此目录是可审阅、可复现的非官方核心修改源码。**1.5.0已将audit2接入x86_64新安装；a update不替换已有核心。** ARM仍使用官方3.5.5。非官方核心不能冒称Telemt官方发布，也不能当作生产已提速的承诺；公开版本范围见[版本说明](../docs/RELEASE-1.5.0.md)。

补丁基于官方 `3.5.5`，确切提交为 `ac71d92ec41dea00a7eafd6b8d350c3486633500`。仅修改版本标识、Direct 自适应转发复制函数和两个 TCP socket 初始化位置；保留原依赖锁、认证、密码算法、线上协议、限额、访问控制、主备及用户配置。完整变更见 `telemt-3.5.5-relay-backpressure.patch`。

- 转发修复：在源读取处于 Pending 时继续驱动目标的 flush，使已经接受的明文对应密文能够发送；不再依赖下一批输入来唤醒尾部。保留部分写、背压、EOF 和错误传播，不增加缓冲、任务或锁。
- TCP 实验：取消 client 和 Direct outgoing socket 的固定 256 KiB 收发请求，交还 Linux TCP 自动缓冲策略。**内核 socket 内存可能增加**，尤其是高延迟、多活跃连接；用户态复制预算并不约束这些内核内存。保留 fallback socket 原策略。
- 标识为 `3.5.5+tdh.audit2`；`audit1` 对照只包含转发 flush 修复，`baseline` 是同工具链、同构建参数的未修改 3.5.5。

`flushed_copy.rs` 与补丁加入的 helper 字节一致，由独立 Rust 回归直接引用。回归验证缓冲尾部推进、背压下完整字节和关闭、目标错误传播。它不替代真实 Telemt、WireGuard、故障切换和长稳测试。

## 在隔离构建机复现

不要在生产后端编译或替换运行中二进制。以下步骤在空闲构建机的新目录执行；`PATCH` 指本目录补丁的绝对路径。取得源码需要网络，构建需要适合该目标的 C/C++ 编译工具、CMake/Ninja 和 Rust。实验未引入运行时长期依赖。

```bash
git clone https://github.com/telemt/telemt.git telemt-experiment
cd telemt-experiment
git checkout --detach ac71d92ec41dea00a7eafd6b8d350c3486633500
git -c core.autocrlf=false apply --check "$PATCH"
git -c core.autocrlf=false apply "$PATCH"
cargo +1.99.1 build --locked --release --bin telemt
sha256sum target/release/telemt
target/release/telemt --version
```

本次已测 x86_64 构建采用 Rust 1.99.1、Zig 0.17.0 GNU 2.35 目标、CMake 4.4.4、Ninja 1.13.2，`CARGO_BUILD_JOBS=2`、`CARGO_PROFILE_RELEASE_LTO=thin`、`CARGO_PROFILE_RELEASE_CODEGEN_UNITS=8`。基线与候选一致。以上简化命令使用本机编译器，**不承诺产生相同二进制摘要或兼容较旧 glibc**；每份产物必须独立记录工具链、源提交、补丁和二进制摘要。ARM 未构建、未验证，不允许沿用 x86_64 摘要。

实验源码仍适用上游 [Telemt 许可](TELEMT-LICENSE.txt)，应保留全部归属及许可；补丁额外加入 `AUDIT-MODIFICATIONS.md` 并使用非官方版本标识。不得把本地补丁产物冒称官方版本。

## 合成高延迟复验

`tests/high_rtt_matrix.py` 仅用于可丢弃的 root Linux 测试机。需要 iproute2、ethtool、WireGuard 工具、HAProxy、Python cryptography；不自动安装、不打开公网监听、不访问生产配置。创建的 namespace 没有连接宿主外网。它模拟 DC 段 RTT，实际数据走原始 veth TCP，**不是 WG 数据吞吐或 Telegram 文件测速**。

准备私有 0700 的 `tdh-audit-*` 目录：官方二进制在 `telemt-3.5.5/telemt`；三个实验文件分别为 `telemt-baseline`、`telemt-flush`、`telemt-auto`。对应 `baseline-build.json` 等仅包含本次已核对的 `sha256` 和 `version`，不是下载清单。工具先校验二进制摘要和版本，再复制并降权运行；实验产物禁止用于公开 DC 探测。

```bash
timeout --signal=TERM --kill-after=5 600 python3 tests/high_rtt_matrix.py \
  --root "$LAB" --rtt 100 --haproxy "$HAPROXY" --wg "$WG"
timeout --signal=TERM --kill-after=5 240 python3 tests/high_rtt_matrix.py \
  --root "$LAB" --rtt 100 --stress --haproxy "$HAPROXY" --wg "$WG"
timeout --signal=TERM --kill-after=5 420 python3 tests/high_rtt_matrix.py \
  --root "$LAB" --rtt 100 --long --builds auto flush --haproxy "$HAPROXY" --wg "$WG"
```

普通矩阵每项共16 MiB、1/4连接、双向、3轮；单方向上限100 Mbps。压力矩阵每项128 MiB、16连接、双向、1轮，叠加1 KiB探测。单项65秒期限，worker 210秒期限，低于192 MiB可用内存或OOM计数增长时停止；失败原样记录，不算作零速成功样本。固定版本批次顺序存在时序偏差，应交换 `--builds` 顺序复验，不把三轮P95当成稳定尾延迟估计。退出时清理本次 namespace、私钥和子进程；SIGKILL/宿主断电无法保证 finally 执行，异常终止后须核查残留。

`--long`为每项64 MiB、单连接双向、3轮及小文件探测；同样有单项期限。仿真DC接入队列为64，避免默认5槽队列在16流突发时成为实验端瓶颈；监听溢出计数随DC结果保存。探测时延包含连接建立，不能冒称既有消息时延。

1.5.0新增的有界对照可用`--rate-mbps 1000 --backpressure --dc-timeout 25 --rounds 3 --builds flush auto`复验，每方向32×8MiB。25秒只调整模拟DC的单次socket期限，默认仍为8秒；该选项及显式轮数仅允许背压模式，文件名包含期限/轮数以保留旧结果。`--observe-network`增加独立文件标签并记录实验namespace的TCP重传、WAN netem及端点fq计数。各工具及wg需预先准备并通过`--wg`或受限PATH选择；见[新安装报告](../docs/FRESH-INSTALL-1.5.0.md)中的初次超时、并发回退及完整范围。

`tests/dualhop_lab.py`另建入口、两个后端与路由namespace，两个WG隧道都带PSK，实际数据经过HAProxy/Telemt/WG，DC在各后端本地仿真。代理仅绑定回环/WG私有地址；合成健康agent用于验证主备状态，不替代生产五DC健康策略。`--soak`保留约5分钟的75 MB低速长流，在传输中切换agent状态、恢复与重载；强制splice仅为兼容实验。示例：

```bash
timeout --signal=TERM --kill-after=5 420 python3 tests/dualhop_lab.py \
  --root "$LAB" --build auto --rtt 100 --soak --haproxy "$HAPROXY" --wg "$WG" run

timeout --signal=TERM --kill-after=5 240 python3 tests/dualhop_lab.py \
  --root "$LAB" --build official --rtt 100 --benchmark --haproxy "$HAPROXY" --wg "$WG" run

timeout --signal=TERM --kill-after=5 240 python3 tests/dualhop_lab.py \
  --root "$LAB" --build auto --rtt 100 --benchmark --haproxy "$HAPROXY" --wg "$WG" run

timeout --signal=TERM --kill-after=5 420 python3 tests/high_rtt_matrix.py \
  --root "$LAB" --rtt 200 --loss-percent 0.5 --builds auto flush --haproxy "$HAPROXY" --wg "$WG"
```

`--benchmark`与`--soak`互斥。前者不叠加长流，先测试双SNI/双后端，再在A主后端执行16 MiB单流双向各3轮。基线失败保留为失败，不用零速混进成功中位数；路由用HAProxy分配连接计数，避免超时旧流在DC稍后完成污染判据。丢包矩阵另用4 MiB单/4连接双向各3轮、DC原始TCP段每方向0.5%随机丢包，未固定种子，不能与WG外层UDP丢包或不同文件大小组直接混算。

这些验证仍不足以默认替换生产核心。正式采用前需要真实业务文件对照、更长稳态、多用户与低内存压力、ARM/发行兼容、校验发布和可演练的后端轮换回退。旧版管理器只替换脚本的更新边界也必须单独解决，不能在 `--self-test` 中隐藏升级。

## 后续启用和资源验收

[核心服务验收](../docs/CORE-ENABLEMENT-VALIDATION.md)记录已完成的临时后端实际启用、真实双SNI/DC握手、显式回退和健康拒绝恢复，以及离线产物准备；默认生产下载仍未切换。

`tests/backend_endurance.py`在原始DC TCP段模拟100ms/100Mbps，执行有界双向长流和前后两轮32个暂停读取的接收者，同时记录小文件、RSS、fd及全机内存。它不是WG或真实Telegram文件测速。`--duration`只允许300/600/900秒，任意低于192MiB可用内存或OOM增长即停止；仅限可丢弃root测试机。

```bash
timeout --signal=TERM --kill-after=45 780 python3 tests/backend_endurance.py \
  --root "$LAB" --haproxy "$HAPROXY" --wg "$WG" --duration 600

timeout --signal=TERM --kill-after=20 350 python3 tests/high_rtt_matrix.py \
  --root "$LAB" --rtt 100 --backpressure --builds auto flush --haproxy "$HAPROXY" --wg "$WG"
```

`--backpressure`与普通/长流/丢包模式分开记录，32连接每项共256MiB、双向各一轮。大流直接进入Telemt并发送PROXYv2，避免HAProxy的其他socket缓冲掩盖背压；客户端请求32KiB接收缓冲，下载暂停读取5秒，DC保持打开等待ACK。小文件探测仍走HAProxy。这个100Mbps合成分段测试不能充当完整WG路径或1Gbps容量验收，也不能把单轮结果解释为稳定的性能分布。
