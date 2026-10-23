# 入口 splice 复核与后端优先方案

最新1.5.0首次安装已默认接入后端audit2核心，具体取舍和验证见[新安装交付](FRESH-INSTALL-1.5.0.md)。本文保留splice候选的历史论证；完整WG路径没有证明其默认启用的吞吐收益，当前未强制启用splice。

2026-10-21，本地研究候选，不推送、不操作生产。本文区分已测事实、假设与后续决策条件。

**10月22日进展：**以下是实验前的判断记录。[后端原生对照报告](NATIVE-BACKEND-VALIDATION.md)已补上固定socket缓冲的性能因果、独立编译修复及同工具链原版对照。另完成Ubuntu22.04/HAProxy2.4.30、两条WG+PSK、两个Telemt的三节点模拟路径，9条校验流通过主备下线/恢复与重载：[原始记录](benchmarks/native-dualhop-official.json)。本组splice-auto未观察到pipe分配；强制splice兼容实验观察到1个pipe且校验通过，但不据此默认强制开启。该组三节点测试为短期兼容验证，不代表长期生产稳定性，也没有测出完整WG路径上的CPU收益。

## 1. 为什么此前没有默认启用 splice，以及重新判断

[原生 VPS 对照](benchmarks/vps-haproxy-2026-10-21.json)确实测到了同速 HAProxy CPU/GiB 下降约21%–25%。以单流下载为例，普通为1.088秒/GiB，splice为0.859秒/GiB；按500 Mbps有效速率折算，约从占一个核心的6.3%降为5.0%。这是有效的入口效率改善，但不是整机CPU下降25%，也不是文件下载加速25%。

之前的暂缓理由包括：只有短时回环转发、没有完整加密链路的长稳；涉及额外pipe/fd资源；旧更新器只更新管理器。然而，“限速吞吐没有增长”不能证明splice无价值，本来就有人为限速；5轮中少量毫秒级最大延迟波动也不足以认定splice损害延迟。将这些作为长期搁置理由过于保守。

**修订判断：splice是优先进入默认配置验证的候选，当前尚未修改生产配置。** 官方 [HAProxy 2.4 文档](https://docs.haproxy.org/2.4/configuration.html#4-option%20splice-auto)说明 `splice-auto` 按方向判断适用性，不是强制所有流走splice；pipe不足可退回普通复制。每个pipe额外占两个fd，需约束预算。文档对非常早期Linux实现的警告不能直接套用为5.15内核有已知数据损坏。

本轮补做：

- [完整真实二进制链路](benchmarks/local-e2e-splice-355.json)：HAProxy→官方Telemt3.5.5→仿真DC，两个合成Secret、分段ClientHello、双向及4并发，共12次SHA256通过。
- [启用splice的5分钟传输与重载](benchmarks/local-splice-soak.json)：两个10 Mbps长流合计750 MB，30次短探测，传输中一次master/worker重载，全部校验通过；观察到pipe分配，说明splice机制实际参与过测试。

这两项在WSL2/HAProxy3.2.9运行。第二项仅HAProxy合成后端，没有Telemt/WG；不能把两项拼成“完整WG加密路径连续运行5分钟”的证据，也不是已验证Ubuntu5.15/HAProxy2.4.30完整长稳。

生产候选的下一道验证应包括：目标Ubuntu22.04/HAProxy2.4的完整WG+PSK+双Telemt路径；长流与短消息混合；pipe紧张时的复制回退；主备、伪装回退、半关闭/复位和重载。若文件完整性、切换及资源边界正常，同速CPU改善可重复、小消息延迟无可复现退化，就有理由默认采用，不应要求必须看到下载提升25%。启用仍需配置校验、保留原配置和无中断HAProxy重载流程。

## 2. 后端发现了比复制缓冲更优先的疑点

[Telemt3.5.5 socket源码](https://github.com/telemt/telemt/blob/3.5.5/src/transport/socket.rs)在接受客户端连接和创建Direct上游socket时显式设置256 KiB的收发缓冲。调用链分别来自 `proxy/client.rs` 的 `configure_client_socket` 和 `transport/upstream.rs` 的 `create_outgoing_socket_bound`。3.5.14相同位置仍是相同设置；升级版本不会自动解决这一点。

这不同于 `direct_relay_buffer_budget_max_bytes`（进程内复制缓冲预算）、`direct_relay_copy_buf_*`（单方向用户态复制上限）、`crypto_pending_buffer`（待发密文）。调大后面这些值不能直接解除TCP socket的限制。

**运行时已核实，性能因果尚未核实。** 两版官方二进制分别在2次合成双向传输中采集了12个socket观察样本，客户端侧和仿真DC侧均为 `rb=tb=524288`：[3.5.5记录](benchmarks/local-sockets-355.json)、[3.5.14记录](benchmarks/local-sockets-3514.json)。Linux的SO_RCVBUF/SO_SNDBUF设置存在加倍记账，因此这个512 KiB不是源码改成了512 KiB，也不能直接当作TCP实际通告窗口。

[Linux内核TCP文档](https://docs.kernel.org/networking/ip-sysctl.html#tcp-variables)明确说明，显式SO_RCVBUF会关闭该socket的接收缓冲自动调节，自动调节的 `tcp_rmem` 上限此时不适用。因此只提高全局 `tcp_rmem` 可能没有作用；`net.core.rmem_max` 也不会让程序原来只请求256 KiB的socket自动变成数MiB。

以100 Mbps、200ms RTT举例，填满链路需要约2.5 MB在途数据。固定较小缓冲因而是值得验证的高RTT瓶颈；实际还取决于通告窗口、发送缓冲、拥塞窗口、重传和读写速度，不能据此算出并宣称某个生产速度上限。下载要分别检查DC→后端接收及后端→入口发送；上传检查入口→后端接收及后端→DC发送。

之前后端实验覆盖了版本、fast_mode和低RTT完整转发，却没有验证**高RTT下真实Telemt的TCP缓冲限制**。这是原审计需要补充的重点；裸TCP/WG文件实验不能代替Telemt这一层。

## 3. 按收益可能性安排后端实验

| 顺序 | 要验证的问题 | 对照方法 | 采用条件与限制 |
|---|---|---|---|
| 1 | 固定socket缓冲是否限制高RTT吞吐 | 原版官方3.5.5；仅WG段/仅DC段分别加50/100/200ms RTT，固定25/100/200 Mbps预算；单流/4流、双向，先无丢包再少量丢包；记录实际socket缓冲、snd_wnd、rwnd_limited/sndbuf_limited、CPU和重传 | 必须先证明窗口/发送缓冲受限，不能把线路拥塞误诊为内存参数问题 |
| 2 | 有界自动缓冲能否解除限制且保持内存安全 | 独立实验构建与同编译条件的原版构建对照，只改变socket策略；保留认证/加密/限额/Nagle既有策略；控制自动增长上限与总内存压力 | 优先官方提供可配置修复；自维护fork仅用作实验，不默认增加长期维护依赖。1GB机器测试RSS、内核socket内存、OOM和连接恢复 |
| 3 | 复制/密文缓冲是否仍形成CPU或背压瓶颈 | 固定版本、fast_mode与总预算，分别比较复制上限和密文待发上限；大流同时发送小消息 | 同速CPU下降或吞吐提升，同时小消息P95、内存、断连不恶化；不直接提高总内存预算 |
| 4 | 高负载是否使健康agent误判并切换 | 真实Telemt负载下同时量API响应、agent状态和新连接选择，区分本机调度、API超时与某DC不可达 | 仅确认误判后研究滞回，不随意把“五DC可达”改成“任一可达” |
| 5 | 后端→真实DC线路是否才是主因 | 两后端在同一时段、相同实际DC和相同文件分别对照；关注重传、RTT及晚高峰 | TCP握手成功/距离近不等于文件快；不能强制换用户DC冒充线路改善 |

实验优先回到获授权的临时1核1GB Ubuntu22.04环境，并以私有namespace模拟两段时延。WSL16核结果不代表这台后端的高并发和内存压力。先低负载逐级增加，设置超时、总流量预算和停止条件；不连接生产三机、不在公网开放实验端口。

内核BBR/fq、WG MTU/offload属于两端共同因素，仍需要实际接口、socket及双向PMTU证据；不因它们是常见“优化项”就统一改值。两个后端可以有不同瓶颈，不能保证同一参数包同时有益。

## 4. 现在已经补上的诊断与更新边界

本地 `a perf` 在原有脱敏聚合基础上增加：收发socket缓冲上限、发送端看到的对端窗口 `snd_wnd`、`rcv_space` 及接收窗口/发送缓冲受限累计时间的 min/median/P95/max。缺失指标为null。`rcv_space`不等于实际通告窗口；受限时间来自socket整个生命周期，不是本次5秒采样的增量；与socket年龄和正在进行的传输一起解释，不自动判定“参数太小”。仍不输出地址、PID、fd或原始ss行。

回归覆盖新指标聚合、缺失值与地址脱敏；原有46项、Bash语法、ShellCheck、内置自检及实际HAProxy SNI/切换/重载检查通过。复现socket观察可在空闲Linux实验环境执行：

```bash
timeout 30 python3 tests/socket_probe.py "$T355" "$OUT/sockets-355.json"
timeout 60 python3 tests/e2e_lab.py --telemt "$T355" --splice --output "$OUT/e2e-splice.json"
timeout 360 python3 tests/soak.py --splice --output "$OUT/splice-soak.json"
```

目前没有修改生产转发模板或Telemt二进制。未来若采用splice，`a update`需要明确应用HAProxy配置并安全重载；若采用Telemt本体修复，还涉及组件升级和后端轮换，不能用当前只替换管理脚本的更新函数假装已经生效。原版1.3.1首次更新不执行新迁移钩子的限制仍存在。不能通过在自检中隐藏修改、追加守护进程或破坏主备来满足“一次命令”的表面要求。
