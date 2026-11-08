# 3.5.17 最终有界验收与排队研究报告

2026-11-09，北京时间。管理器 1.7.0 候选，开发分支 `dev/telemt-stable-compat-20261107`；未发布、未合并 main、未部署生产。历史 3.5.16 工程报告与全部原始失败保留，不把旧版矩阵算到新版。

## 当前判断

官方最新正式版已按 GitHub Releases API 过滤 draft/prerelease，固定到 **3.5.17**（2026-11-08 12:08:51 UTC 发布），准确 tag/commit/双架构包与二进制摘要见[版本锁](telemt-stable-lock.json)。已兼容新 API、保留旧配置与链接，并实际验证 3.5.16→3.5.17→3.5.16 回退。单入口 443、双 SNI、两独立后端、WireGuard、Direct-DC 与原生 TCP 透传保持；没有启用 WEB/ME 或新增生产层。

**仍不建议把官方默认核心直接替换到高延迟生产线路。** 本轮真实官方 3.5.17 再现 100ms 单流下载约21Mbps，旧 `3.5.5+tdh.balanced2` 同任务约105Mbps。复用 1.6 socket 策略的 `3.5.17+tdh.balanced1` 恢复约106Mbps，但四流满载排队代价仍在；不能宣传全面快于 balanced2。三种简单调参没有达到联合门槛，不加入生产默认。该原生候选仅为可重建实验源码，未发行、未加入安装器白名单。

## 代码与兼容审查

3.5.16→3.5.17 涉及86文件，重点是 API schema/capabilities、desired/effective state、dry-run、DNS override、source-deny、配置 epoch/watcher 与缓存。整个 transport、client/relay 和 crypto 目录没有变更，固定 socket 缓冲仍在；没有把 API 更新误认作传输修复。当前模板未发现必须改名的字段。API 回环、鉴权、read-only 与 WEB=false 继续显式设置。[详细差异及准确摘要](UPSTREAM-3.5.17.md)。

管理器增加前一正式版双架构精确二进制识别及同 schema/protocol 加入码兼容，旧 balanced2/audit2/3.5.5 识别继续保留。核心事务仍先备份二进制、配置、密钥、单元与权限，再预检/原子替换；配置字节、Secret/SNI 不静默迁移。独立连接预算维护、回滚、漂移拒绝和目录权限迁移复用已验机制。`a update` 仅更新管理器，候选未合并前远程 main 仍是1.6。

新测试工具固定16/17各自版本，不静默改变历史配方；新原生构建固定官方 commit、Rust1.99.1、thin LTO/8 codegen units，复用1.6的自动缓冲、低 RTT 接收保护和 outgoing LOWAT256KiB。没有生产 LD_PRELOAD、宿主 qdisc/sysctl/防火墙改动或全局限速。新增加的客户端 LOWAT/WG fq 仅在私有 namespace 的对照实验中存在。

## 实际 CI 与证据

所有本轮 Linux 构建和执行在 Ubuntu22.04 GitHub runner；本地只有 Windows 文件/Git/数据分析。没有连接生产或过期 VPS、购买资源、改仓库可见性、恢复旧耐久/自动任务。每个运行号目录保存 CI metadata、准确 source commit 和原始 JSON；[上游与兼容证据](benchmarks/upstream-3517/README.md)、[排队与资源证据](benchmarks/queue-20261108/README.md)均有逐文件 SHA256 manifest。

| 运行 | 实际执行范围 | 结果 |
|---|---|---|
| [42494335986](https://github.com/bababoyi6/me/actions/runs/42494335986) | 正式双架构归档摘要/ELF；amd64 双 SNI 上下行 Fake TLS；ready、新 schema/state/capabilities；匿名401、只读写403、配置不变 | PASS |
| [42494911735](https://github.com/bababoyi6/me/actions/runs/42494911735) | 官方 ARM64 原生安装/API/协议并发 | PASS |
| [42496967645](https://github.com/bababoyi6/me/actions/runs/42496967645) | 旧balanced2和前版3.5.16分别实际systemd升级、重复/连接维护、崩溃恢复及精确回退；30/50/100、双WG故障恢复 | PASS |
| [42494738574](https://github.com/bababoyi6/me/actions/runs/42494738574) | 三KVM安装/重装、原生命令、漂移拒绝/回退、三次正常重启 | PASS |
| [42494738511](https://github.com/bababoyi6/me/actions/runs/42494738511) | 官方17约182秒/900次短连接；FD14→14，OOM0 | PASS；不是24小时 |
| [42496474174](https://github.com/bababoyi6/me/actions/runs/42496474174) | 官方17/旧balanced2/实验auto，72批180计量流+72预热，0/100ms真实WG、独立客户端/DC进程 | 协议PASS，确认高RTT退化 |
| [42495175073](https://github.com/bababoyi6/me/actions/runs/42495175073) | 同工具链原生17两构建，四轮AB/BA 80长传输，720条256KiB并发，systemd维护回退与资源检查 | PASS；四流下载RTT未达标 |
| [42495712435](https://github.com/bababoyi6/me/actions/runs/42495712435) | 四策略32批80计量流+64预热 | 无联合收益；探测子进程CPU未计量，严格复核DO_NOT_PROMOTE |
| [42498243533](https://github.com/bababoyi6/me/actions/runs/42498243533) | 20Mbps/流同负载，四轮AB/BA，40计量流+32预热，完整探测CPU | 两组均在预登记范围 |
| [42501930660](https://github.com/bababoyi6/me/actions/runs/42501930660) | 100ms/200Mbps，30/50/100并发双向4MiB/流，720负载+8预热，逐流分位数 | PASS，0错误/OOM，完整清理 |
| [42501933849](https://github.com/bababoyi6/me/actions/runs/42501933849) | Bash/ShellCheck/内嵌一致性、自检、HAProxy配置与切换、284项root回归、异常输入/权限/中断恢复、socket oracle、relay回归 | PASS，root无跳过 |

各项是独立源码/批次，不能相加冒充同一个提交完整验收。root运行的 PR 合并检查提交 `1e51c4f37034e4db8be95bd308ff3ce9d69a2faa` 与 PR head `8fe39203dc1a8c0df4f04b1f436dad32de26f3d9` 的 Git tree 完全相同，回执已核对。原生 socket 子集控制12/候选14通过，各有1项手工FD压力测试忽略、2836项过滤；不宣称完整上游 Rust 测试通过。

## 可比较性能与排队结论

官方17的同任务100ms单流下载：旧balanced2 **105.27Mbps**，官方17 **21.24Mbps**，官方17+实验自动缓冲 **105.52Mbps**。另一个同工具链原生17任务，控制→候选的四轮中位数：单上传22.97→105.27、单下载21.35→105.78、四上传90.71→151.48、四下载85.08→157.29Mbps；四下载由于RTT代价归类 `RTT_NOT_ESTABLISHED`。不同任务CPU/路径对照不同，不能把这些数字串成同一基线或宣称5倍快于旧balanced2。

四策略 pilot 的四下载中位数为基线154.98、LOWAT64 156.08、LOWAT256 156.15、WG fq158.03Mbps；TCP RTT仍约217–230ms，独立路径ping p95约258–299ms，私有router netem出队积压约6MB。它支持模型中确实存在路径排队，未确定真实VPS的排队位置。无策略稳定满足吞吐≥95%、TCP RTT≤80%、ping p95≤105%的联合规则，且缺探测子进程CPU，故保留探索性失败并不采纳。

同负载复验：单流约19.42Mbps两核心一致；四流约77.34/77.65Mbps。TCP RTT控制约101ms、候选103–104ms，路径ping p95控制101–102ms、候选104ms。该结果与链路利用率/排队解释一致，但只是20/80Mbps负载的机制检查，不证明满载延迟已修复、真实路由唯一根因或应在生产限速。

## 30/50/100并发资源和真实逐流耗时

固定顺序 control→balanced，各并发双向一次；4MiB/流、100ms/200Mbps真实WG和合成DC。整个实验包含核心、客户端、DC、HAProxy及控制器，共三逻辑核/768MiB、无swap；后端单逻辑核绑定。**单轮资源验收，不作重复速度排名或千兆上限证明。** 实际完成720/720，模拟DC728/728（含预热）、错误0，资源峰值672,464,896字节（641.31MiB），OOM0，退出后私有进程和网络区清理、剩余pids=1。MemoryHigh=640MiB触发239次回收事件，未触及768MiB硬限；不是“毫无内存压力”。两核心FD16→14；空闲RSS控制约31.0MiB、候选约53.2MiB，不能当成全部socket/系统内存或长期泄漏结论。

以下分位数来自真实逐流耗时，不由批次总耗时推算。完成时间包含Fake TLS、整条4MiB传输及确认；Fake TLS客户端阶段不含服务端认证/DC ready确认，均不是网络RTT。U/D分别为上传/下载。聚合吞吐为本批观测，生成端未达预登记CPU饱和标记，但仍不等于核心容量上限。

| 核心 | 并发/方向 | 聚合 Mbps | 4MiB 完成 p50/p95（秒） | Fake TLS 客户端阶段 p50/p95（毫秒） | 后端 CPU（单逻辑核%） |
|---|---|---:|---:|---:|---:|
| control | 30/U | 147.51 | 5.884/6.608 | 229.5/268.1 | 16.27 |
| control | 30/D | 137.85 | 5.617/7.037 | 215.7/221.0 | 3.29 |
| control | 50/U | 147.77 | 9.198/10.710 | 236.1/285.1 | 16.82 |
| control | 50/D | 162.51 | 8.887/10.229 | 216.2/220.8 | 3.49 |
| control | 100/U | 170.00 | 18.061/19.428 | 257.5/390.9 | 20.52 |
| control | 100/D | 171.12 | 17.142/19.333 | 215.7/222.1 | 3.57 |
| balanced | 30/U | 149.53 | 5.898/6.546 | 224.5/289.7 | 16.49 |
| balanced | 30/D | 148.76 | 5.722/6.659 | 215.6/224.5 | 3.40 |
| balanced | 50/U | 152.06 | 8.998/10.924 | 239.0/352.9 | 17.13 |
| balanced | 50/D | 162.60 | 8.771/10.067 | 215.8/222.9 | 3.68 |
| balanced | 100/U | 170.36 | 17.978/19.486 | 260.7/419.8 | 20.46 |
| balanced | 100/D | 171.19 | 17.493/19.338 | 214.9/222.4 | 3.78 |

原始抽样RSS、CPU、socket、完成/客户端阶段分位数见[汇总](benchmarks/queue-20261108/sustained-timing-summary.json)及逐批metrics。native布局中的 `official_release=3.5.16` 是未使用的历史默认字段，真实核心身份由 native_manifest、逐批version和SHA验证为3.5.17，不能将其误作官方16测量。

## 失败、修正与复验

- `42493889571` 新schema272,500字节超过测试旧256KiB读取上限；仅schema探测改为2MiB有界读，随后PASS。生产小型健康接口限额未放宽。
- `42494072895` 测试reload路径错误404；按官方 `/v1/system/reload` 更正后只读403。`42494289625`错误路径重复任务取消，未伪造PASS。
- `42494910627` 工作流行内header含冒号导致YAML无效，改为块标量后重新构建通过；该失败没有执行Linux。
- `42499438481` 压力任务首个iperf容量对照在20秒期限超时，计量负载尚未开始、无OOM且清理完成；未断言唯一根因。只将有界测试控制期限增至30秒并保留错误诊断，`42499962853`720流通过；未放宽生产TG超时。
- 早期资源通过缺逐流计量，旧值保持未计量；补仪器后`42501930660`实测通过。队列pilot缺探测CPU，保留 `PROBE_CPU_UNMEASURED`，用更严格分析复核，不追认通过。

## 已执行命令与复验入口

实际Linux命令由各CI链接日志和对应提交工作流完整保留；主要入口如下。这是复现说明，不是在本地执行Linux的记录。

```text
gh workflow run stable-candidate.yml --repo bababoyi6/me --ref dev/telemt-stable-compat-20261107
gh workflow run queue-study.yml --repo bababoyi6/me --ref dev/telemt-stable-compat-20261107 -f layout=equal-load
gh workflow run queue-study.yml --repo bababoyi6/me --ref dev/telemt-stable-compat-20261107 -f layout=sustained
python3 tests/long_path_lab.py --sustained-stress --native-directory "$RUNNER_TEMP/native-pair" --output "$RUNNER_TEMP/queue-evidence/metrics.json"
python3 tests/stress_analysis.py "$RUNNER_TEMP/queue-evidence/metrics.json" "$RUNNER_TEMP/queue-evidence/analysis.json"
sudo timeout --kill-after=10 180 python3 -m unittest discover -s tests -p 'test_*.py' -v
```

长链路入口由工作流置于限时900秒、MemoryMax/Swap/CPU限制的systemd单元内，不能删除这些外层限制后照抄到生产。当前queue工作流固定消费已核对source的`42495175073/native-pair`，二进制artifact仅保留7天；过期后须用固定源码重建、重新核验/固定新的run与SHA，再测，不能自动回退latest。交付包只含源码、补丁、文档与证据，不含临时原生二进制。

本地实际执行Windows Git状态/对象/差异检查、证据JSON分析、manifest/ZIP/补丁核对；无需Ubuntu22本机环境，不运行本地Linux。本地下载为结果JSON/日志，压力流量在CI私有网络内。

## 升级与回退、放行边界

使用[滚动维护说明](UPGRADE-1.7.md)：先升级管理器，确认另一后端两线路健康，入口排空单后端，私有备份与候选预检后显式核心维护，核对API/协议与真实业务再恢复入口，逐台执行。回滚先撤销本次独立配置维护，再按事务恢复旧核心/配置权限；漂移时拒绝静默覆盖。服务重启/隧道失效无法把已有TCP搬到另一台，客户端需要重连；新连接故障转移与systemd自动恢复已在有界环境测试。

| 尚缺验收 | 状态和下一步 |
|---|---|
| Telegram Desktop/Android/iOS官方客户端，真实账号/文件和切网 | NOT RUN；用户暂不提供设备，不再据协议工具声称全面兼容 |
| 物理公网三机、跨洛杉矶/纽约、真实运营商/1Gbps | BLOCKED；仅能在另行获准的节点或明确生产维护窗口复验，当前不操作生产 |
| 24小时稳定/长期内存与FD增长 | NOT RUN；本轮仅有界短测，旧中止耐久任务保持停止 |
| 抗封锁/抗识别 | NOT RUN；Fake TLS/SNI与TCP透传保留不等于实证，不能承诺“强抗封锁” |
| 原生17候选ARM64、三VM安装器路径/正式发行 | NOT RUN/不支持；官方ARM/三VM通过不能替代原生候选，生产安装器不接受它 |
| 满载排队/吞吐联合收益 | 未解决；三种简单调参未采纳，后续需带完整CPU的定位或不同链路独立复验 |

当前成果适合代码审查和隔离复验，**未达到全面生产推荐条件**。为追求高速稳定不应以新版本号覆盖已发现退化；既有正常工作的1.6生产继续保留，后续放行取决于上述证据，而非增加功能数量。
