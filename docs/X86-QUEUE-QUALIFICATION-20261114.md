# x86 候选的双端队列对照

既有混合验收中，balanced2 和新版 B 的满载延迟相近，路由积压峰值都约 6.2 MB。之前在同类模型中，双端加密前 HTB 与 fq_codel 组合使消息 p95 从约 300 ms 降到约 102 ms，下载保留至少 96%。仅在后端整形的实现没有满足新连接的绝对延迟预算，入口共享 IFB 实验也有上传回退。对应依据见[混合验收](SECURITY-PERFORMANCE-AUDIT-20261113.md)、[双端整形](PATH-CAPACITY-20261109.md)、[后端整形](MANAGED-QUEUE-20261109.md)和[共享 IFB](SHARED-ENTRY-20261109.md)。

IETF 的 FQ-CoDel 规范指出，按流调度可隔离短小流量，若把流封装到同一隧道，外层的流队列可能将它们视作同一条流。本轮沿用历史试验，在 WireGuard 两端加密前保留内层 TCP 流的可见性。180 Mbps 是此前已登记的 200 Mbps 实验链路参数。公网链路是否存在可控瓶颈、其容量是否稳定，仍需独立判断。[RFC 8290](https://www.rfc-editor.org/rfc/rfc8290.html)

在 `stable-candidate.yml` 选择 `tls_candidate=new-queue`。同一个固定摘要的 B 二进制在同一 Ubuntu 22.04 x86_64 runner 执行 AB/BA 两对。只切换两端 WireGuard 原队列与 180 Mbps HTB 加 fq_codel。固定 100 ms、200 Mbps、30 逻辑连接、每条文件 32 MiB、3 CPU、1.5 GiB、无 swap、540 秒。保存两个端点在每批开始和结束时的队列观测。

逐对要求上下行吞吐均至少为原队列的 95%，带载消息和新连接 p95 均至多为原队列的 90%，且两项相对空载的增量都至多 100 ms。核心 RSS 和整组 CPU 秒数均至多为原队列的 110%。同时检查容量对照、样本数、完整性和清理。这一场景的判定适用于单活动隧道。入口双隧道共享容量、弱网、真实客户端和公网稳定性仍需各自的证据。

2026-11-14 核对上游，正式版仍为 3.5.17。TLS 记录修复与缺失 profile 重试已合入上游 main，B 已包含这两项。公开研究记录过 GFW 的被动识别和主动探测，隔离测试只能验证特定实现和配置的协议行为，项目尚无实测的公网识别率。[上游发行](https://github.com/telemt/telemt/releases/tag/3.5.17) · [记录修复](https://github.com/telemt/telemt/pull/966) · [重试修复](https://github.com/telemt/telemt/pull/968) · [USENIX 研究](https://www.usenix.org/conference/usenixsecurity23/presentation/wu-mingshi)
