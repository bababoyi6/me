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

单 socket 阈值能否降低总体延迟仍需对照。共享 WireGuard 带宽的分配由队列层处理。未来验证时，先核对实际 socket 读取到的阈值和 TCP 自动发送缓冲状态，再登记候选身份。两组采用相同的 TCP 缓冲上限和 WAN 队列，只切换此选项，检查完整性、吞吐、带载 p95、内核未发送量、CPU 和内存。若未发送量降低而带载延迟仍超标，再回到 WireGuard 加密前的队列分配。本次完成源码改动，Linux 构建、候选实验身份登记和运行验证仍待执行。
