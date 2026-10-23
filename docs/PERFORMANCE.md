# Telemt Dual-Hop 性能审计与验证手册

**2026-10-23 新安装交付说明：** 下文保留1.4.2审计阶段的判断和更新边界。最新1.5.0已将audit2设为x86_64新安装默认核心；安装、测速取舍和发布状态以[1.5.0验收](FRESH-INSTALL-1.5.0.md)为准。`a update`仍不更换已有核心。

审计日期：2026-10-21。项目基线：`452c50d268c8a95d68bdda0d27878f4e9ddcefb2`，管理器 1.3.1、schema 5、节点协议 1.2.1、Telemt 3.5.5。本次管理器为 1.4.2，schema、协议和 Telemt 固定版本均保持不变。

本地后续候选的完整加密传输、真实 DC 公开握手和维护锁回归见 [本地补充验证](LOCAL-VALIDATION.md)。依用户要求，这轮改动尚未推送；请区分本地候选与 `a update` 当前可下载的发布内容。

最新复核发现 Telemt 两侧显式固定 TCP socket 缓冲的后端疑点；splice 已提升为优先默认启用验证候选，新增完整加密短传与5分钟splice长传检查。事实、证据边界和后端实验顺序见 [入口与后端复核方案](BACKEND-AND-SPLICE.md)。这不改变当前尚未应用生产参数的状态。

2026-10-22补充：[后端核心原生验证](NATIVE-BACKEND-VALIDATION.md)已实际复现高RTT吞吐限制与一类尾部停滞，完成同工具链原版、flush修复、自动缓冲修复的编译对照及可审阅源码补丁。普通100ms矩阵显示单流合成下载约3.81倍收益；这是明确受控条件下的数据，不是生产速度承诺。下表较早的候选项判断应结合这份新证据阅读。

**没有连接或修改生产 VPS，没有获得三台生产服务器、用户网络或 Telegram 文件的测速数据。生产主瓶颈尚未确定，也不能认定已经接近线路上限。** 用户另行提供的一台临时 1 核 1 GB Ubuntu 22.04 测试机已用于真实 5.15 内核兼容、WireGuard 私有网络与 Telemt 二进制验证，详见 [临时 VPS 验证报告](VPS-VALIDATION.md)。单机合成路径不能代表三机公网线路。本次交付还包含只读脱敏采样、自动化回归和受控测速工具，没有默认堆叠 sysctl、修改生产 MTU/offload、改变主备策略或升级生产 Telemt。

## 1. 真实数据路径与容量边界

```text
Telegram 客户端
  └─ TCP① / FakeTLS → 入口公网 IPv4:443
      HAProxy：读取 ClientHello SNI，保留原始字节，不终止 TLS
      apple.com → telemt_a：VPS1 主，VPS2 备
      gs.apple.com → telemt_b：VPS2 主，VPS1 备
        └─ TCP② + PROXYv2 / WireGuard UDP → 所选后端 WG-IP:24431
            一个 Telemt 进程同时服务两个 Secret / SNI
              └─ TCP③ / obfuscated MTProto → Telegram DC:443
```

每个正常代理流经过 **HAProxy、Telemt 两次用户态转发，三个独立 TCP 段，一条 WG 隧道**。WG 不是第三个用户态 TCP 代理；同一连接不会依次通过两个后端。返回数据沿上述连接反向传输。HAProxy→后端带 PROXYv2，Telemt 只信任入口 WG `/32`，API 绑定 `127.0.0.1:19091`，健康 agent 绑定 WG-IP:19101；隧道 AllowedIPs 为对端 `/32`。本次不改变这些边界。

未认证/未知 SNI 会走 Apple 伪装回退。两台后端不可用时，Apple backup 仍能提供伪装，但**不提供 Telegram 代理服务**。主备切换只重新选择新 TCP 连接，无法迁移既有 Telegram 会话；客户端需要自行重连。

HAProxy 不做加解密；入口内核做 WG ChaCha20-Poly1305 加密/解密，后端内核承担对应操作。Telemt 处理 FakeTLS 认证/记录边界和 MTProto obfuscation；用户消息的 MTProto 加密仍由 Telegram 客户端与 DC 处理。`fast_mode` 在上游 nonce 中复用、反转密钥/IV 材料，3.5.5 的 Direct relay 仍使用 CryptoReader/CryptoWriter，不能说它消除了所有 AES 成本。

每段均有独立拥塞窗口、socket 收发队列和流控。其间还有 NIC/虚拟化队列、WG 加解密调度、HAProxy 缓冲、Telemt 自适应 copy buffer 及 DC 排队。持续吞吐上界受最弱段约束；入口限额还可能同时计入公网和隧道流量。下载尤其要关注入口→用户的出口和后端→入口的出口；上传关注相反方向。公网速率、隧道内有效速率与 Telegram 文件有效速率不能直接等同。

入口全局连接上限取入口/VPS1/VPS2 声明容量的最小值；一台后端接管双线路时不会按两台容量相加。安装容量是 `min(内存 MiB×2, vCPU×4000)`，限制在 1000–30000，最低要求 768 MiB。连接数量不是用户数量，也不是带宽估算。两个链接不把一个下载叠加到两台后端，单用户若选中较差线路，另一台空闲也不会自动加速。

## 2. 按证据排序的发现与候选项

| 优先级 | 证据/疑点 | 指标、风险与验证方法 | 本次处置 |
|---|---|---|---|
| P0 | 当前仓库没有性能采样；无法证明运行中接口确为 fq、连接确为 BBR | 原 `tuning_is_active` 仅检查两个默认 sysctl；默认值不会重配已存在接口或旧 TCP socket。用 `a perf` 观察实际 qdisc 和分段 TCP | 修正“已实际生效”的过度表述，增加只读采样 |
| P0 | 用户→入口、入口→后端、后端→DC 的路由/丢包未知 | 同一时段分段测单流/四流、双向、CPU/steal、重传与队列。仅见低速不能归因程序 | 不编造生产瓶颈和加速比例 |
| P1 | HAProxy 没有启用 splice，持续转发会经过用户态复制 | 同速减少HAProxy进程CPU成本的结果已复现；pipe/fd资源与启发式启用仍依赖负载。需区别进程CPU改善与文件提速 | 已补原生完整WG双后端短期兼容；该组splice-auto未启用pipe，尚不外推完整路径CPU收益 |
| P1 | Telemt 固定256KiB收发请求；WSL实际512KiB，原生Ubuntu实际416KiB记账上限 | 已在高RTT原生合成路径证明固定缓冲限制；同工具链对照与独立编译补丁分离flush完整性修复和自动缓冲收益 | 源码补丁、100/200ms及64MiB对照已交付；未验证生产因果或默认引入非官方核心，不盲调sysctl |
| P1 | WG 未显式设置 MTU，采用 wg-quick 自动推导 | 自动 MTU 不等于跨网 PMTU；不对称路由、UDP 分片/黑洞可能导致卡顿。双向 DF 探测配合计数器和传输测量，不能仅凭 ping 推断 WG 容量 | 采集实际 MTU、分片/重组失败；不盲设 1280/1420 |
| P1 | 健康策略要求全部五个固定 DC 可达 | 每 10 秒探测，连续两轮失败置 down；API 每秒检查，单次失败即影响 agent。显式 down 不受普通 TCP check 的 `fall 3` 延迟保护。一个 DC 局部故障也会使整后端退出新连接选择 | 保留可用性策略，采集 chkdown/downtime；建议先关联波动与 DC/agent 状态再改策略 |
| P2 | 单实例 Direct copy buffer 有进程预算和压力降级 | 预算安装时取内存 1/4，最大 1 GiB；3.5.5 从 4/8 KiB 起步，分别增长至默认 64/256 KiB，进程内存/连接/预算压力会降级。不是为每个连接立刻分配 320 KiB；copy buffer 也不是 TCP 接收窗口 | 不提高预算；现有 API 不直接暴露预算全部指标，报告不能据 RSS 宣称预算饱和 |
| P2 | 更新只换管理器 | `update_manager` 下载/身份/版本/schema/协议/语法/自检后原子替换；不更新包、二进制、unit、WG、HAProxy 或 Telemt 配置 | 保持一步管理器升级，增加管理器备份/回退，不提供生产参数试用入口 |

源码定位使用基线的函数名便于跨版本查阅：[原始脚本](https://github.com/bababoyi6/me/blob/452c50d268c8a95d68bdda0d27878f4e9ddcefb2/telemt-dual-hop.sh)，重点为 `write_haproxy_config`、`write_entry_wg`、`write_backend_wg`、`write_health_agent`、`calculate_direct_buffer_budget`、`apply_tuning`、`update_manager`、`complete_entry`。已完整检查仓库三份原始文件：脚本、README、Actions 工作流。

### HAProxy 的具体结论

`tcp-request content accept if { req.ssl_hello_type 1 }` 在完整 ClientHello 可判定后结束检查。`inspect-delay 3s` 是最大等待窗口，并非固定额外 3 秒；缩短会使慢网/分片 ClientHello 更容易错误落入伪装回退。本机分片测试验证了提前放行，因此保留 3s。

`tcp-smart-accept` 会影响 ACK 合并时机，不能直接认定为每次 40ms 延迟；缺少抓包/真实短消息证据，不修改。HAProxy 的线程数由构建和 CPU affinity 自动选择，不硬编码 `nbthread`；CPU/GiB 实验固定一线程是为了可重复比较，不能据此要求生产单线程。

HAProxy 保持内核 socket buffer 自动调节，不强制 `tune.rcvbuf.*` / `tune.sndbuf.*`。增大 `tune.bufsize` 会增加每连接内存，且不是增大 TCP 窗口的等价做法。当前 unit 的 fd 上限 524288、Telemt 为 262144，相对最大 30000 连接有余量；是否受有效 Maxsock、内核文件总量、内存或 CPU 约束仍需运行数据。健康 TCP check 和 agent-check 各有职责：端口可连接不代表 agent 返回 up；重复检查有少量固定开销，但没有证据表明它是吞吐瓶颈。

### WireGuard / Linux 的具体结论

这是 TCP-in-UDP，没有外层 TCP 重传与内层 TCP 重传相互放大的 TCP-over-TCP 问题；外层 UDP 丢包仍会转化为内层 TCP 丢包、重排和拥塞响应。WG 并不修复恶劣跨境线路。入口两条接口和后端被动学习 endpoint 的布局合理，`PersistentKeepalive=25` 用于保持 NAT/可达性，不是速率旋钮。

wg-quick 通常按出接口/endpoint 推导 MTU 并预留封装空间；实际值必须读取，不能假定所有机器都是 1420。IPv4 UDP/WG 基本封装约 60 字节，另有填充；常见自动预留 80 字节还覆盖 IPv6 情况。不要关闭认证、加密、PSK 或扩大 AllowedIPs。TSO/GSO/GRO 是否有利取决于虚拟网卡/宿主实现，本次只读可选 `ethtool` 状态，不安装它，也不切换能力。

带宽时延积 `BDP = bit/s × RTT(s) ÷ 8`。例如 **计算示例，非实测**：100 Mbps、200ms 需要约 2.5 MB 在途数据。只有观察到窗口/内存瓶颈才考虑提高 TCP 自动调节上限，并同时评估并发内存。BBR 不能消除物理限速，且本地拥塞算法只控制本地发送方向；用户上传还取决于客户端、DC 下载出口还取决于 Telegram 的发送端。没有证据支持更换内核、上不稳定拥塞补丁或全局关闭 PMTUD。

## 3. Telemt 版本审查

截至审计日，[官方 latest 稳定版本为 3.5.14](https://github.com/telemt/telemt/releases/tag/3.5.14)，2026-10-05 发布、非 prerelease，标签提交 `242946ba9cb4b868a7abaec07b94c55dad1ff6a7`。3.5.5 标签提交为 `ac71d92`。通过官方 Release API 核对了当前脚本两种架构的 SHA256，与 3.5.5 发布资产 digest 一致。

已对照 [3.5.5→3.5.14 源码](https://github.com/telemt/telemt/compare/3.5.5...3.5.14)。新版本包含运行时生命周期、配额/并发、文件系统防护、握手处理及 Direct budget 控制器所有权修复；3.5.14 的主要新增变更在 WEB/ME，不能直接推导本项目 `use_middle_proxy=false` 的 Direct 大文件加速。Direct buffer 核心 relay 并未换成一条已证明更快的数据路径，nonce fast_mode 和主要流包装也不能作为新版本加速证据。

官方 3.5.8 发布说明还明确：`server.max_connections`、`server.conntrack_control`、`general.direct_relay_buffer_budget_max_bytes` 为进程级 authority，变更需要重启；文件系统安全校验可能拒绝过去允许的路径。当前 `config_strict=true` 必须逐字段核对新版本，不能仅用语法能解析判定兼容。新增 WEB/ME 功能默认值及其依赖也要审查，本方案不启用它们。

结论：**保持 3.5.5 固定版本与原校验值，不提供未经三机兼容/真实 Telegram 验证的自动 Telemt 升级。** 若后续验证升级，应先在隔离克隆验证完整原配置、服务账号/工作目录权限、两个 SNI/原链接/Secret、只读认证 API 与 agent；下载固定标签并核对两种架构官方 digest，保存原二进制和配置，先验证备用后端再轮换另一台。重启后端会断开它当前连接，需维护窗口和明确授权，不能声称“无中断”。回退恢复对应旧二进制/配置；不要用 `a complete` 或重新安装三台来尝试升级。

## 4. 本次实际对照数据

原始逐轮结果：[初测批次](benchmarks/haproxy-loopback-2026-10-21.json)、[复核批次](benchmarks/haproxy-loopback-confirmed-2026-10-21.json)，下表采用复核批次。测试为本机 Linux/WSL2 内核 `6.18.40.1-microsoft-standard-WSL2`，Ubuntu 26.04 用户空间，**从 Ubuntu 官方包提取的 22.04 HAProxy 2.4.30 构建**，不是生产 Ubuntu 22.04/5.15 的完整环境。HAProxy 不终止 TLS，OpenSSL 库版本差异也已存在；不能将本机结果外推为生产保证。

两批各40次传输，共80次：每批1/4连接、上传/下载、普通复制/splice-auto各5轮，顺序交替；每次约3秒，聚合限速500 Mbps，全部通过SHA256。管道可能在两次采样之间立即归还，所以单个`PipesUsed=0`不能证明未启用；复核批次补充`PipesUsed + PipesFree`在传输中/后的观察值，以核对实际管道分配。另做了双SNI、分片ClientHello、PROXYv2、agent down→备用→主恢复，以及真实master/worker USR2重载期间既有传输完整性测试。

| 场景 | 普通吞吐中位数 Mbps | splice 吞吐中位数 Mbps | 普通 CPU秒/GiB 中位数 | splice CPU秒/GiB 中位数 | CPU 降幅 |
|---|---:|---:|---:|---:|---:|
| 单流下载 | 499.125 | 499.143 | 1.031 | 0.859 | 16.7% |
| 单流上传 | 499.141 | 499.204 | 1.031 | 0.802 | 22.2% |
| 四流下载 | 498.575 | 498.646 | 1.031 | 0.859 | 16.7% |
| 四流上传 | 498.644 | 498.684 | 0.974 | 0.802 | 17.6% |

复核批次普通CPU P95为1.031–1.088秒/GiB，splice为0.859–0.916；具体分组和min/max见原始文件。普通吞吐范围498.476–499.237 Mbps，splice为498.619–499.227 Mbps。P95采用nearest-rank，5轮下等于最大值，样本量较小；进程CPU计时有tick量化误差，且不计整个内核/宿主总CPU。

复核批次本机首字节延迟（合成ClientHello后端响应，非Telegram）单流各组最大值不超过0.953ms，四流不超过2.896ms；该短测试不能证明移动网络P95、抖动、丢包或长期稳定性。它未包括WG加密、Telemt、真实MTProto会话、DC流控、客户端缓存或公网拥塞。

**吞吐几乎相同，不能报“提速 17%–22%”。** 这里只验证同速入口 CPU 开销下降；限速测试只建立容量下界，未测极限吞吐。若生产入口 CPU 不饱和，真实体感可能没有变化。为稳定性优先，splice 不进入生产管理器；不通过 `a update` 自动开启，也不要求用户额外执行试用命令。

本机复现（只绑定 loopback，有时长/速率/轮数上限）：

```bash
python3 tests/lab.py --haproxy /path/to/haproxy \
  --benchmark --rate-mbps 500 --seconds 3 --rounds 5 --output lab-results.json
```

## 5. 三台 VPS 的安全采样与真实测速方案

### 首先只读采样

安装 1.4.2 管理器后，在三台分别执行：

```bash
a perf 5
# 可选择保存；文件名自行区分 entry/backend1/backend2
(umask 077; a perf 5 > performance.json)
```

`a perf` 默认 5 秒，可选 2–30 秒；整个操作有 55 秒超时，子命令和读取大小有上限，预算不足的字段返回 null。它不安装依赖、不启动测速服务、不发 ping/iperf、不改配置/防火墙/sysctl、不重启服务、不上传。角色只做文本匹配，不 source 状态文件；不调用会暴露密钥的 `wg show ... dump`，不输出日志、命令行、连接地址、真实 IP、WG 公钥/私钥、Secret、API token 或配置正文。普通网卡匿名为 nicN，TDH 专用接口保留名称。

包含：CPU 每核忙碌/steal、服务组进程 CPU/RSS/fd/限制、内存、接口 MTU/字节及错误/丢弃增量、实际 qdisc、可选 offload、WG 握手年龄、按方向分组的 TCP 队列/RTT/cwnd/BBR 计数、重传/分片/监听丢弃增量、HAProxy 队列/失败/切换统计和实际组件版本。1.4.2 将每核 busy 与宿主抢占的 steal 分开计算，并加入 OOM、主缺页与换页计数的总量/增量，避免把内存不足或宿主争抢误判为代理算力不足。若受保护的API token文件可用，仅对固定回环地址执行一次只读 GET，保留连接/握手/上游失败的白名单计数，不读取用户或配置API，不通过命令行/环境变量传token，也不跟随代理或重定向。CPU 100% 指一个核心，haproxy 包含 worker；服务更换 PID 时 CPU 返回 null。socket RTT P95 是当前连接分布，不能当作连续时间序列的抖动 P95。内核系统计数（包括 OOM）还包含本机其他业务，需结合负载对齐。

报告没有收集全部 TCP_INFO、路由表、Telemt 内部 buffer pressure 或主动 PMTU，不能凭报告一项数字自动诊断全链路。采集三台空闲、真实下载中、真实上传中各 5 轮，记录相同客户端、线路 A/B、运营商、日期时段、负载。报告虽不含凭据，依然由用户决定是否分享其运行统计。

### 受控主动测试（由用户在授权环境自行执行）

| 测试 | 路径/方法 | 约束与解读 |
|---|---|---|
| 单流/聚合 | 入口→后端1/2，经 WG 分别做 `iperf3 -P 1/4`、正反向 | 工具总速率默认20 Mbps，不是每流20；从小负载开始，到达限速只能证明下界 |
| 公网对照 | 在隔离克隆或维护窗口比较入口→后端公网原始路径与 WG | 公网 iperf 仅允许入口源 IP、临时端口、timeout；不得暴露 Telemt/API。没有源限定与清理条件就不做 |
| 用户→入口 | 同一用户网络在临时、源限定端点测单流/四流 | 与真实 Telegram 对比，检测接入线路、晚高峰和运营商差异；服务器互测无法代表用户线路 |
| 后端→DC | 当前代理会话的 TCP_INFO、重传、DC 连接耗时，配合实际文件 | 不在 Telegram DC 上运行 iperf，不用五个 TCP 握手成功冒充吞吐测试 |
| RTT/抖动/丢包 | 每段双向 `timeout 25 ping -n -c 20 -i 1 -W 1 TARGET`；可选 `timeout 40 mtr -n -r -c 10 TARGET` | ICMP 优先级和逐跳不回包不等于转发丢包；关注最终目的丢包及与 TCP 重传的关联；原始路径含 IP，勿直接提交 |
| PMTU | 双向、先小后大的 `timeout 8 ping -n -c 3 -W 1 -M do -s PAYLOAD WG_PEER` | 隧道内 IPv4 payload 上限由实际 MTU−28 得出；外层公网也需单独测，ICMP/WG 的策略可能不同；每次只测少数尺寸 |
| 长传/并发 | 按用户真实文件限速持续10分钟；逐步从1、2到4个客户端 | 同时采 CPU/队列/重传，内存、worker、断连数；一旦消息延迟/丢包显著恶化立即停止；别直接压满生产 |
| 故障切换 | 本地已验证 agent down/up；真实服务故障在隔离三机克隆 | 记录新连接恢复时间、旧连接重连、A/B 两方向、两端同时不可用时仅剩伪装；生产不能擅自停服务 |

WG 测速工具为 [tools/iperf_matrix.py](../tools/iperf_matrix.py)。不随单文件安装隐式下载，也不集成进只读菜单，按需从经审核的同一提交取得。仅支持两个固定 WG 后端目标，绑定对应入口 WG 源地址，端口5201；不会安装 iperf3/启动服务/修改访问控制。若运行需要开放该隧道端口，由用户临时源限定放行并清理；已有规则不得删除。

后端在一个终端运行临时服务，VPS2 把 `10.77.1.2` 换为 `10.77.2.2`。日志不上传。下面 timeout 不超过四分钟，结束即停止：

```bash
timeout 240 iperf3 -s -B 10.77.1.2 -p 5201
```

入口另一终端执行：

```bash
python3 tools/iperf_matrix.py --peer 10.77.1.2 --rate-mbps 20 --seconds 8 --rounds 5
```

默认 20 次测试，每次8秒、间隔3秒，总计约220秒（连接失败会增加时间）；每项另有连接和整体超时。端口服务先启动再跑，测试完成关闭，不开放公网5201。VPS2单独重复，避免同时争抢入口。脚本 JSON 只保留白名单指标和泛化错误，不输出 iperf 原始连接信息。1.4.2 校验收发统计时段及数值：时段差大于 max(1秒,设定时长的20%) 的结果保留指标、标为异常并排除汇总；这不是自动认定工具或线路故障，需要检查结束阶段、排队和交叉传输。若担心业务影响，可用3轮、每轮3秒、间隔2秒并相应缩短服务timeout。生产带宽余量小于默认预算时继续下调速率。UDP 丢包/抖动测试只在隔离环境或另行审批窗口做，小速率并设置时间上限；不将其误认为 TCP 文件有效速率。

### Telegram 真实体验的 A/B 记录

固定客户端版本、同一设备/运营商/时段、同一文件/会话和线路链接；在自己的测试聊天发送无敏感内容的小文件、图片、视频和大文件。记录点击到首屏/首字节、完整下载/上传有效字节÷耗时、100条小消息的延迟分布、失败/重连次数。客户端已缓存的文件单独标记，不能与首次下载混在一起；上传和下载单独统计。Telegram/CDN 缓存无法完全控制，应交替 A/B/回退，不只测一次。

每种方案至少5轮，建议20轮后再讨论P95；同时记录 min/max、丢失样本和并发人数，保留原始CSV。固定端点与Secret；比较优化前、试用后、回滚后的同等条件。若中位数差异落在原波动范围内、P95恶化或重连上升，就不认定有益并回滚。工具没有机器人/Telegram 用户凭据，不自动替用户登录、发消息或下载私有文件。

## 6. 只运行 a update 的兼容升级边界

本次发布定位是**保留现有连接信息的诊断与维护升级，没有已证明的生产大幅提速**。本机 splice 对照只证明入口 CPU 开销降低，不能证明生产吞吐或稳定性；因此生产脚本移除了 `perf-apply`、参数试用菜单及相应配置修改路径。研究代码仅在 `tests/lab.py` 的回环环境内运行。

发布到 `bababoyi6/me/main` 后，在三台 VPS **分别只执行一次**：

```bash
a update
```

即完成该节点本次管理器升级，不需要重装、`a complete`、参数应用命令、重新生成链接或客户端改动。可选的 `a perf` 只是之后定位瓶颈的工具，不是升级步骤。入口执行一次不会远程修改另外两台；现有项目不保存远程管理凭据，本次也不引入它们。

schema保持5、节点协议保持1.2.1、Telemt固定3.5.5及校验值不变。HAProxy/WireGuard/Telemt配置生成器和服务unit模板与1.3.1一致；运行中的Secret、完整链接、入口IPv4和443端口、WG密钥、访问控制、伪装和主备顺序均不改写，不重启或重载数据服务。没有通过改内核、sysctl、MTU、offload换取未验证的速度。

自动测试直接使用从基线提取的**真实1.3.1更新函数**，模拟入口/backend1/backend2各运行一次旧版 `a update`，核对新管理器成功安装，状态、连接信息、隧道文件、密钥和服务配置逐字节保持不变，而且没有systemctl操作。不是只测试新版更新新版。

需要明确旧更新器的技术边界：1.3.1会校验下载的新脚本，再替换管理器；它不会执行新版的运行配置迁移钩子。不能既保留其行为，又宣称首次 `a update` 已自动升级所有正在运行的组件。本次选择不迁移数据面，所以单次升级无需补做配置应用。这不构成生产速度提升的承诺。

从新管理器进行后续更新时，会保存上次不同版本的管理器到 `/usr/local/sbin/telemt-dual-hop.previous`，可用 `a update-rollback` 恢复兼容且自检通过的备份。重复安装相同内容不覆盖备份；快捷入口准备失败不替换当前管理器。下载失败、身份/协议/语法/自检不通过时，现有服务配置不变。

**首次从原版1.3.1升级时，它本身不创建旧管理器备份**；不能谎称新版能在旧进程已执行的步骤里补上备份，也不把有副作用的迁移塞进 `--self-test`。若首次升级后需要退回管理界面，可从已审计的基线下载旧管理器到临时文件，先校验SHA256（基线SHA256见下文），再原子替换管理器；无需回退任何数据面配置，因为本次没有修改。后续版本才有上述一键管理器回退。管理器回退不等于组件回退。

基线脚本SHA256：`07642df50587cbad056865445bfe4d38802322b2eb02d564234d1b4252a528dc`。首次管理器回退可执行（只恢复管理脚本，不改服务配置）：

```bash
t=$(mktemp)
curl --proto '=https' --proto-redir '=https' --tlsv1.2 -fL --connect-timeout 10 --max-time 120 \
  https://raw.githubusercontent.com/bababoyi6/me/452c50d268c8a95d68bdda0d27878f4e9ddcefb2/telemt-dual-hop.sh -o "$t" &&
printf '%s  %s\n' 07642df50587cbad056865445bfe4d38802322b2eb02d564234d1b4252a528dc "$t" | sha256sum -c - &&
bash -n "$t" && bash "$t" --self-test &&
sudo install -m 0755 "$t" /usr/local/sbin/telemt-dual-hop.rollback &&
sudo mv -f /usr/local/sbin/telemt-dual-hop.rollback /usr/local/sbin/telemt-dual-hop
rm -f "$t"
```


更老schema的历史架构迁移不包含在本次发布中；遇到不兼容配置会拒绝迁移，而不是改Secret或重部署集群。本次源码变更从来不会自行操作生产VPS。

## 7. 架构建议与收益边界

**A：原架构内。** 先用三段数据选瓶颈，再研究入口splice、经双向PMTU证据支持的单隧道MTU、确有窗口限制时的有限buffer上限调整。这些运行参数本次均未应用。当前资源策略、SNI、两后端优先级、安全边界均保留；不为漂亮数字去掉WG或认证。

**B：使用方式不变的增强。** 若同一时段同一DC/文件多轮证明某后端始终更优，可研究保持SNI/Secret/公网443的连接级主后端选择或带滞回的运维选择；只影响新连接，不保证一个下载合并带宽。需要测两链接负载、故障恢复与振荡，先不加自动跨站测速/动态调度守护进程。若发现agent对单DC故障过于敏感，应测试“按DC可达性与连接失败率决策”的明确语义，不能随意把all改any牺牲完整DC覆盖。

**C：额外成本/线路/特性取舍。** 更换入口运营商/地区、后端到DC的优质线路或更高配独享CPU，可能比软件微调有效得多；第二入口解决入口单点，但涉及IP/链接和成本，不擅自引入。直连后端可能减少一跳，但失去当前入口/WG隔离等性质，不作为默认方案。新购服务器前，用临时短租/供应商测试端点，在相同用户运营商和晚高峰按上述矩阵对比；如果用户→入口持续丢包，优先改善入口线路，而不是提升后端内存。

只有实测三段均接近可用带宽、CPU/窗口/队列无异常、真实Telegram结果也相符，才能判断已接近上限。当前证据尚不能作出该判断。入口splice已确认同速CPU成本下降；后续后端实验也已确认高RTT合成路径的吞吐收益，详见[原生核心验证](NATIVE-BACKEND-VALIDATION.md)。两者都尚未默认应用生产，**生产整体可能提升很小乃至0%；能否大幅改善仍取决于实际受限段。**

## 8. 验证与参考

```bash
bash -n telemt-dual-hop.sh
shellcheck -x -S style telemt-dual-hop.sh
bash telemt-dual-hop.sh --self-test
python3 -m compileall -q tests tools
python3 -m unittest discover -s tests -p 'test_*.py' -v
timeout 60 python3 tests/lab.py
```

Actions 在Ubuntu22.04安装测试用HAProxy/ShellCheck，运行上述检查。本机另使用Ubuntu22.04 HAProxy2.4.30执行配置/行为测试；临时VPS增加了原生Ubuntu22.04/5.15验证，依然不等于生产三机验证。自动测试覆盖配置生成、安全绑定、旧schema5兼容、更新拒绝不兼容内容、管理器回退、自检超时、旧更新器一步兼容、连接信息不变、诊断脱敏/不执行state/时长与子进程输出限制、CPU steal/OOM、认证头代理与进程参数保护、iperf聚合限速/计时质量、实验清理，以及实际HAProxy分流/故障切换/重载传输。静态测试通过不代表真实速度提升。

主要参考为[HAProxy 2.4官方配置手册](https://docs.haproxy.org/2.4/configuration.html)（inspect-delay、splice、socket buffer、agent语义）、[Linux TCP参数文档](https://docs.kernel.org/networking/ip-sysctl.html)、[wg-quick官方实现](https://git.zx2c4.com/wireguard-tools/tree/src/wg-quick/linux.bash)、[Telemt 3.5.5 Direct relay](https://github.com/telemt/telemt/blob/3.5.5/src/proxy/direct_relay.rs)、[Direct budget](https://github.com/telemt/telemt/blob/3.5.5/src/proxy/direct_buffer_budget.rs)、[nonce实现](https://github.com/telemt/telemt/blob/3.5.5/src/proxy/handshake/nonce.rs)和[官方3.5.8兼容性说明](https://github.com/telemt/telemt/releases/tag/3.5.8)。官方WireGuard网页上的历史测速很旧，未用它估算当前VPS容量。
