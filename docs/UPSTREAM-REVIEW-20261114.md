# x86 双跳方案调研与落地

2026-11-14。目标环境为千兆端口的 x86 VPS，项目继续使用现有的 TCP 透传、两条 WireGuard 和相互备用的 Telemt 后端。正式核心为固定摘要的 balanced2。此轮按原始项目资料、标准、源码及已有 CI 回执选择改动，只运行一次针对新配置的有界回归。

| 方向 | 外部方案和项目证据 | 处理 |
| --- | --- | --- |
| 后端可用性 | HAProxy 支持根据响应内容判断健康，并用 `track` 让多个逻辑路由引用同一后端状态。旧配置每个物理后端在两条线路上各运行 TCP 检查和 agent 检查，稳定时总计每 2 秒 8 次连接。仅 TCP 握手成功也无法证明健康代理返回了有效就绪状态。[HAProxy 2.4 手册](https://docs.haproxy.org/2.4/configuration.html#5.2-track) | 入口生成器每台后端每 2 秒检查一次 `up\n`，两条线路引用同一判定。稳定时每 2 秒 2 次检查，检查连接减少 75%。负载端口仍用 PROXY v2，健康端口使用原有响应协议。 |
| CPU 与 VPS 竞争 | HAProxy 2.4 默认按进程可用 CPU 选择线程数。同机移核曾提高吞吐，但一条线路的接收等待变长。宿主 steal、服务 CPU 配额和内核等待可各自影响尾延迟。[HAProxy 线程](https://docs.haproxy.org/2.4/configuration.html#3.1-nbthread) · [Linux PSI](https://docs.kernel.org/accounting/psi.html) · [同机对照](X86-TUNNEL-BUDGET.md) | `a perf` 读取服务 cgroup 的 CPU 配额与节流增量，以及本机 CPU、内存、I/O PSI。沿用 HAProxy 自动线程策略。 |
| 高 RTT 吞吐 | 上游 #965 提议交给内核做 TCP 自动缓冲。balanced2 已有该机制。现有 1 Gbps、100 ms 模型中把私有缓冲上限扩到 32 MiB 后，单流变快，但并发下载与尾延迟没有同时改善。发送阈值虽压低单连接积压，也未解决带载延迟。[上游 #965](https://github.com/telemt/telemt/pull/965) · [缓冲对照](X86-GIGABIT-QUALIFICATION.md) · [发送队列对照](X86-CLIENT-SEND-QUEUE.md) | 保留现有内核缓冲和 balanced2。VPS 标称 1 Gbps 与实际可用带宽、RTT、TCP 窗口及跨段瓶颈分别判读。 |
| 加密前队列与入口复制 | FQ-CoDel 按可见流调度。多个流封装进一条隧道后，外层调度只能看到外层流。已有加密前固定队列、共享 IFB 和动态分配分别出现千兆下载、上传或尾延迟退化。splice 的 CPU 收益只在部分既有条件中观测到。[RFC 8290](https://www.rfc-editor.org/rfc/rfc8290.html) · [双隧道证据](X86-TUNNEL-BUDGET.md) · [splice 记录](BACKEND-AND-SPLICE.md) | 保留现有队列与复制默认值。真实瓶颈未明前，不在公网网卡套用固定整形或全局 offload 配方。 |
| 版本与客户端兼容 | 最新 Telemt 正式版仍为 3.5.17。TLS 记录修复 #966、缺失配置退避 #968 已合入 main，两者晚于正式标签。项目已在固定源码候选中内化并完成合成协议检查。新核心尚未通过联合吞吐和延迟门槛。#972 仍为开放 PR，Telegram Desktop 的 WEB 服务端仍是概念验证，涉及独立客户端契约。[正式版](https://github.com/telemt/telemt/releases/tag/3.5.17) · [#966](https://github.com/telemt/telemt/pull/966) · [#968](https://github.com/telemt/telemt/pull/968) · [#972](https://github.com/telemt/telemt/pull/972) · [WEB 客户端契约](https://github.com/telegramdesktop/tproxy-server) · [项目候选](SECURITY-PERFORMANCE-AUDIT-20261113.md) | 保留已有链接与已锁定的核心版本，协议修复保留在候选。 |

GFW 公开研究记录了被动识别和主动探测，也说明 TLS 外观与多层封装无法作为不可识别的依据。两项研究的测量对象、时间和路径与本项目不同，无法外推为当前 Telegram 客户端或三台 VPS 的拦截率。现有候选的 CI 结果只支持合成协议与兼容性结论。[USENIX Security 2023](https://www.usenix.org/conference/usenixsecurity23/presentation/wu-mingshi) · [USENIX Security 2024](https://www.usenix.org/conference/usenixsecurity24/presentation/xue-fingerprinting)

### 本次验证与适用范围

Ubuntu 22.04 的真实 HAProxy 使用管理器生成的健康检查和路由段在回环运行。验证了两条线路原有主备映射、非就绪与空响应、路由切换、恢复及手工排空。仅生成新入口配置时采用该检查。`a update` 不重写运行中的配置。CPU/PSI 的字段解析用 Windows 本地合成输入验证，缺失与回退计数保持空值。未执行吞吐矩阵、公网检测或生产部署。[入口回归](https://github.com/bababoyi6/me/actions/runs/43574451734)

后续有可观测的实际业务窗口时，用 `a perf` 同时采集入口与后端。若节流或资源等待与吞吐下降一致，先定位 VPS 配额或宿主竞争。若 CPU 有余量且短消息随大流升高，再按入口、WireGuard、核心和 DC 分段定位。核心切换仍需候选在相同负载下同时守住上下行与尾延迟门槛。未知公网路径的峰值速度、可用性和 GFW 可达性没有可担保的测量值。
