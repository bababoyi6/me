# Telemt Dual-Hop 安全与性能审计

2026-11-13 · 本地分支 `dev/20261113-security-reliability`，从 `626e578` 开始。正式管理器仍为 v1.7.0。GitHub 实时查询显示远程 `main` 为 `0c6f423ec3053e38a9cf472b3cfe2813d02a1649`，仓库返回 `private:false`，与本地交接记载的私有状态不同。当前 CLI 默认账号只有读权限。开发分支、候选和构建入口留在本地。荷兰 VPS 在最后的交接中已过期。

## 已确认问题与边界

| 优先级 | 位置与根因 | 影响与本轮处理 |
| --- | --- | --- |
| P0 | `telemt-dual-hop.sh` 的旧 `update_manager` 从可变 `main` 下载，内容标记、版本字段、Bash 语法和自检均来自同一份待验脚本。原实现最后才计算摘要 | 候选可在自检阶段取得当前 root 权限。现限定固定稳定标签，下载前取得独立预期 SHA256，下载后执行前验证，并在原子替换前复核暂存文件。兼容性和已有回退仍走正常入口。 |
| P0 | 旧 `rollback_manager` 检查文件类型和协议后，直接以当前权限运行 `.previous` 自检 | 增加属主、写权限、硬链接和父目录检查，并复用现有核心验证的元数据规则。回退信任本地受保护的副本。旧管理器的备份来源仍需信任，跨文件断电耐久性待验。 |
| P0 | balanced2 的确切上游 `src/tls_front/emulator.rs` 在两个模拟 ApplicationData 分支中写入固定字节，托管模板默认启用 TLS 模拟 | 确认受影响。旧候选回移植 #966，保留证书载荷位置、记录长度、HMAC 和密钥语义。Rust 回归先验证旧实现出现预期失败，再验证新实现，包括 TLS 1.2 客户端证书载荷和 TLS 1.3 无载荷。当前只有源码与补丁检查，运行证据待补。 |
| P0 | `src/maestro/tls_bootstrap.rs` 的 BestEffort 初次抓取可失败，常规重试间隔为 4–8 小时 | DNS 或源临时不可达时，受影响域长期用默认 profile。旧候选沿用现有代际取消和抓取总预算，先完成初次抓取，再只对缺失域快速重试。退避窗口 15–30 秒开始，封顶 300–600 秒，每周期最多七次，随后走 4–8 小时常规刷新，恢复后重置。新候选采用上游实现，长期失败仍按封顶间隔持续重试。 |
| P0 | 旧 `src/tls_front/cache.rs` 使用按路径读取和原位写入，启动时按文件 mtime 跳过超过 72 小时的条目 | 托管服务以专用 UID、`UMask=0077` 和限定可写工作目录运行，降低外部篡改面。旧缓存写入被打断可能丢失条目，运行中已有条目过旧仍不触发本轮快速重试。描述符锚定和原子写入属于更广泛的上游变更，旧路线尚未移植。 |
| P1 | 正式 3.5.17 的 `src/transport/socket.rs` 对 client 和 Direct socket 固定请求 256 KiB | 高 RTT 单流窗口受限。新候选复用已有 Linux 自动缓冲、短 RTT 接收保护与 256 KiB 未发送字节阈值。实际新版本吞吐和公平性待测。 |

源码还显示无效认证、未知 SNI 和前置不可达会进入各自现有拒绝或回落路径。新增私有回环烟测检查错误 Secret、双 SNI 双向完整性、API 401/403 和进程清理。异常 ClientHello、多种分段、来源伪造、缓存真实恢复、真实 Telegram 客户端和抗识别效果仍需对应证据。

### 已有防护

- `core_install.py` 在解析包、启动版本查询前检查归档摘要、成员边界、ELF 架构和二进制摘要。版本查询以专用身份执行，审计包保留许可和出处。
- `core_upgrade.py` 与 `core_transaction.py` 先保存受保护配置、核心和元数据，以摘要绑定事务日志，原子替换并同步文件及目录。回退检查配置漂移和历史核心完整性。Linux 强杀、磁盘不足及重启验收仍按原事务测试执行。
- `health_agent.py` 在回环校验 API，拒绝重定向，限制读取，结合五 DC 多数票、滞回和进度超时。单个 DC 不可达可继续服务。现有系统证据见 [架构与健康检查](ARCHITECTURE-1.7.md) 和 [恢复及混合负载](STABILITY-MIXED-20261109.md)。
- 后端数据监听在 WireGuard 地址，PROXY v2 仅信入口 WG 地址，API 回环、Bearer 和只读。配置由 root 管理，Telemt 组只读；状态、WG 私钥、PSK、加入码相关状态和事务快照受限。已启用 `NoNewPrivileges`、空 capability、`ProtectSystem=strict` 和工作目录白名单。HAProxy TCP 443、双 SNI 主备、WireGuard+PSK、BBR+fq、关闭 WEB、禁用内联防火墙管理以及原有用户身份继续沿用正式模板。已有文件和运行证据见 [1.7.0 放行记录](RELEASE-ACCEPTANCE-1.7.0.md)。

## 上游核对与移植

GitHub Releases API 返回最新正式版 **3.5.17**，标签对象 `881210fd4c320735fad3978a291b565afae4b61e`，源码 `66708d2866f3f3a4541c4fa67984d408e71a8af4`。实时 `main` 为 `b594ee55270798fe21f23b721b50eab46c2e0bec`。3.5.5、3.5.17 和该 main 的许可证 Git blob 均为 `6adee358ac2dfc1e15f94094a331a507594e813e`，内容为 Telemt License 3.3。归档保留许可证、版权和非官方修改说明。 [正式发布](https://github.com/telemt/telemt/releases/tag/3.5.17) · [固定源码及许可](https://github.com/telemt/telemt/blob/b594ee55270798fe21f23b721b50eab46c2e0bec/LICENSE)

| PR | 实时 GitHub 状态 | 与项目的关系 |
| --- | --- | --- |
| [#966](https://github.com/telemt/telemt/pull/966) | 已合并 `b594ee5`，晚于正式版 | A 回移植并加记录边界和 HMAC 检查。B 直接继承。 |
| [#968](https://github.com/telemt/telemt/pull/968) | 已合并 `f3cc201`，晚于正式版 | A 回移植并限制连续快速重试次数。B 直接继承上游。 |
| [#965](https://github.com/telemt/telemt/pull/965) | 开放，head `cf15354` | 同样针对固定缓冲。B 继续使用已研究的 balanced2 socket 策略，保留低 RTT 接收保护和未发送阈值。 |
| [#950](https://github.com/telemt/telemt/pull/950) | 开放，head `2765a75` | 公网入口转发的未认证连接仍消耗后端握手资源。现有全局连接上限与超时提供边界。per-IP 方案需验证可信 PROXY v2、NAT 共享、跨代限额及有界状态，暂缓纳入。 |
| [#972](https://github.com/telemt/telemt/pull/972) | 开放，head `7e6fc15` | 针对显式 client MSS 分段路径。当前模板没有启用该实验配置。待有受控丢包与分段实验后再评估。 |
| [#970](https://github.com/telemt/telemt/pull/970) | 已合并 `66708d2`，在正式 3.5.17 | 新 API/配置发现和状态能力由 B 继承。管理器继续只读回环接口，未接入新写操作。 |

3.5.8 至 3.5.17 的优先复核范围：

- **当前启用路径**。描述符锚定文件操作涉及配置和 TLS 磁盘缓存。托管 root 配置目录缓解外部改写，服务 UID 的工作目录仍保留持久化差异。TLS replay in-flight 所有权改动覆盖现用握手，A 尚有差异，跨代与高并发下的实际影响待验。 [文件加固](https://github.com/telemt/telemt/commit/02f66c542ede299faed6baaf941a097aa2a15d0d) · [Replay 所有权](https://github.com/telemt/telemt/commit/b37f1ebdeb13bac5acdaa50e50dd26939dd9ff8a)
- **可能影响**。进程级并发、取消和 Direct buffer budget 等变更包含现用代码。A 仅修两个 TLS 问题，维护时仍要验证重载下的资源回收、弱网连接及事务后可用性。 [并发与取消修复](https://github.com/telemt/telemt/commit/f1107c21d94f97df3e71a71f8e796f4b14a115f9)
- **未启用或受限**。WEB Base Path、WEB 会话和 Middle Proxy 的专属修复不进入当前 Direct 数据路径。API 写配置和内联 conntrack 在托管模板中关闭，不能据此推断其他部署安全。 [3.5.8 发布记录](https://github.com/telemt/telemt/releases/tag/3.5.8) · [3.5.17 项目兼容核对](UPSTREAM-3.5.17.md)

## 候选和验证方式

| 路线 | 源码与构建 | 取舍 |
| --- | --- | --- |
| A · `3.5.5+tdh.balanced2.security1` | `ac71d92ec41dea00a7eafd6b8d350c3486633500`，历史构建 `02ec71320c831735b025107f5b2181097657ac96` 的 socket/relay 策略，新增三份 TLS 补丁 | 变更小，最贴近已部署吞吐。旧上游文件与运行时差异需要长期维护。 |
| B · `3.5.17+tdh.main.b594ee5.balanced1` | 固定 `b594ee5` 上游和既有 socket helper。GitHub 查到的 socket.rs blob 与 3.5.17 完全相同 | 继承新版安全改进；新提交仍需配置、性能、资源和回退联合验收。 |

Windows 上对匿名下载的 balanced2 归档核对包 SHA256 `b4fec3efd10288a9f211ad958337fa314a15486e72fd5c50ab870f22a19737e5`、x86_64 ELF 和二进制 SHA256 `5dc5d9b5ac43a0f12a402159e127b3b69e18ccb355c99b0322e73d870d907100`。发行归档内嵌 `build-provenance.json` 给出确切 builder 和 helper；归档中的基础补丁不涉及 TLS。旧候选构建时重新比对该历史输入、Rust 1.99.1、2 jobs、thin LTO、8 codegen units、两个 helper 和未发送阈值。两条路线记录工具链、源码提交、锁文件和产物摘要，重新编译可追溯，尚无相同二进制摘要保证。 [历史构建清单](benchmarks/bulk-admission-20261024/results/core-manifest.json)

复现入口是已有 [stable-candidate.yml](../.github/workflows/stable-candidate.yml) 的手动输入。分支进入已授权的私有 CI 后，分别用 `tls_candidate=manager`、`tls_candidate=old` 和 `tls_candidate=new` 派发，执行管理器权限及回退回归、单条核心构建及回环协议烟测。核心烟测使用 1 CPU、512 MiB、无 swap、最多 128 个任务、120 秒截止。样本数据为合成，runner 生成的 `tls-candidate-evidence` 包含测试结果与产物身份。

核心烟测通过后可在同次任务用 `tls_parity=true` 执行首轮 WireGuard 对照。复用历史分进程工具，固定 100 ms/200 Mbps、32 MiB、双向 1/4 流、AB/BA 两轮，候选和发行 balanced2 使用相同模拟 TLS 配置。整组 3 CPU/1.5 GiB/无 swap，单独绑核心一逻辑 CPU，420 秒截止。逐对门槛为吞吐 ≥95%、实际客户端 TCP 中位 RTT ≤110%、容量对照 ≥100 Mbps、负载发生侧 CPU <70%，同时要求身份、完整性、资源和清理证据。只据此决定是否值得进入混合和完整弱网矩阵。

## 性能与稳定性证据

| 既有对照 | 原始结果与边界 |
| --- | --- |
| [42496474174](benchmarks/upstream-3517/42496474174/analysis.json) | 同机、100 ms、200 Mbps、32 MiB 单下载、三次轮换的中位数为 balanced2 **105.27**、正式 3.5.17 **21.24**、3.5.17 预加载自动缓冲实验 **105.52 Mbps**。真实 HAProxy/WG，合成 DC，核心绑一逻辑 CPU，整组 3 CPU/1.5 GiB/无 swap，峰值约 301.84 MiB、OOM 0。 |
| [两台机器的 native17 对照](NATIVE-QUALIFICATION-20261109.md) | 100 ms/200 Mbps，160 条 32 MiB 计量流全部完整，整组峰值约 292 MiB、OOM 0。深队列四下载中位吞吐相近，实际 TCP RTT 约 220–230 ms，旧 balanced2 同样受影响。缩短模型队列时单流约 91–93 Mbps，探测丢失或逐对尾延迟超门槛，联合规则未通过。 |
| [32 流与软节流](UPLOAD-CANDIDATES-20261024.md) | 首批固定核心对照 0/32 完成，`memory.high` 23,494 次、PSI full avg10 94.51%、OOM 0，候选未运行。另批 balanced1 逐对比值 83.04%/100.43%，一次跌破 95%。历史 balanced2 的独立 80% 软限批次为 104.32%/95.02%，128 条完整、OOM 0。批次条件不同，保留原判定。 |
| [后端队列](MANAGED-QUEUE-20261109.md) | 满载消息及新建连接延迟下降，但四对均未满足新连接绝对预算。64 KiB 调度、共享 IFB 和额外限速实验继续不进入默认。 |

正式版固定缓冲差异有源码与历史同机结果支持。现有 relay helper 已覆盖 Pending 时的 flush、背压、部分写、EOF 和错误传播；没有从 32 流记录中确认新的 relay 失效机制。满载小消息和建连延迟要与同一条件的吞吐、实际 RTT、CPU、RSS、socket memory 及 `memory.high` 一并判断。候选后续从 100 ms 双向 1/4 流 AB/BA 和一个混合负载开始，过关再扩展 0/50/100/200 ms、4/16/32 流、丢包/抖动、慢读、并发和故障恢复。使用已有隔离 WG/HAProxy 测量工具和正式版摘要；逐对吞吐至少 95%，尾延迟门槛以对照波动预先登记。

## 放行建议

保留 x86 的精确 balanced2 默认，ARM64 继续独立使用锁定的官方 3.5.17。已确认的 TLS 问题使两条候选都有测试价值，现阶段仍缺实际 Linux 构建、完整协议与安全回归、A/B 资源和性能证据。新版 3.5.17 本身的高 RTT 单流退化已复现；旧版原生 socket 移植曾恢复吞吐，当前 B 的联合门槛仍待验。两候选均未取得替换资格。管理器的摘要校验加固可独立交付，需通过候选 Linux CI 并使用已核验摘要的迁移入口。详见 [稳定版更新](MANAGER-UPDATE.md)。

**NOT RUN** · 候选 Rust 编译和前后回归、新候选 x86/ARM 二进制与部署/回滚、双 WG 故障矩阵和新 A/B 性能矩阵、真实客户端、真实公网三 VPS、1Gbps、24 小时、抗封锁。历史证据仅用于对应构建。旧耐久实验在约 56 分钟时提前停止并清理。
