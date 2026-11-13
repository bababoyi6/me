# x86 高 RTT 下的 Telemt 发送积压

复核 [balanced2 窗口对照](X86-GIGABIT-QUALIFICATION.md) 中已保存的 socket 和队列数据。下面是两轮各状态的采样峰值，单位为十进制 MB。Telemt socket 仅纳入样本时累计确认或接收超过 1,000,000 字节的连接。不同层的峰值可能出现在不同时间。

| 对照轮次 | TCP 缓冲上限 | Telemt 发往入口的单连接未发送量 | 共享模拟 WAN 下载队列 |
| --- | --- | ---: | ---: |
| 1 | 默认 | 1.49 MB | 19.70 MB |
| 1 | 32 MiB | 28.69 MB | 38.02 MB |
| 2 | 默认 | 2.71 MB | 17.60 MB |
| 2 | 32 MiB | 24.61 MB | 32.92 MB |

两个 Telemt 进程的合计 RSS 峰值在约 48–49 MiB。数据表明内核 socket 和模拟 WAN 队列都有更大的积压。WAN 队列样本包含用于模拟传播延迟的持包，不能把表中数字全部算作额外排队。socket 的未发送量也不能和 WAN 持包直接相加后推算时延。

构建器的可选参数 `--security-main --client-unsent-limit` 在固定的候选 B 上复用现有 256 KiB `TCP_NOTSENT_LOWAT`，新增到 Telemt 接受的客户端 socket。在本项目拓扑里，该 socket 的对端是入口 HAProxy。候选使用独立版本 `3.5.17+tdh.main.b594ee5.clientlowat1`，构建清单记录阈值。保持 TCP 自动发送缓冲和已有短 RTT 接收策略。Linux 用该阈值约束应用继续提交未发送数据的条件，已经发出但还未确认的数据仍由 TCP 窗口和拥塞控制管理。256 KiB 沿用现有 DC 侧的起始值，实际队列峰值可能超过该阈值。[Linux TCP 参数文档](https://www.kernel.org/doc/html/latest/networking/ip-sysctl.html#tcp-notsent-lowat-unsigned-integer)

在 `stable-candidate.yml` 选择 `new-client-lowat`。同一个固定上游提交和工具链构建 B 及带阈值的候选，在 Linux 回环上读取实际 socket 选项并确认发送缓冲初始值。两组使用相同的 100 ms RTT、1,000 Mbps WAN、32 MiB TCP 自动缓冲上限和原队列，按 AB/BA 交换顺序。每轮分别测两条 SNI 单流双向及双隧道各 30 个逻辑连接的混合负载。

逐对筛选要求所有单流以及双隧道上下行各保留至少 95% 吞吐。同一线路的小消息和新连接带载 p95 各不超过基线的 110%，且相对本候选空载增量不超过 100 ms。每路每类有效带载探测至少 20 条，文件负载重叠至少 1 秒。大发送流的内核未发送量采样峰值至少减少 50%。同时记录数据完整性、清理、CPU、内存和异常。单连接阈值对共享 WireGuard 队列的影响由结果判定。
