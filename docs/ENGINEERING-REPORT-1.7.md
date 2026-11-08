# Telemt 3.5.17 兼容升级工程报告

当前更新：2026-11-09。**当前目标为3.5.17，284项回归通过，完整本轮结果/命令/风险见[最新验收报告](FINAL-ACCEPTANCE-20261109.md)及[上游17审计](UPSTREAM-3.5.17.md)。** 以下保留11月7–8日3.5.16阶段的原始问题、配置审查、改动和验收，数值不改写为17。

历史起始日期：2026-11-07，北京时间。开发分支：`dev/telemt-stable-compat-20261107`。

> **最新长链路结论：** 两个独立任务共 360 条计量流确认 100ms 单流下载退化：balanced2 约 105Mbps、官方 3.5.16 约 21Mbps。已实现 `3.5.16+tdh.balanced1` 原生实验移植，单流恢复到约 106Mbps，80 条长传输、720 条并发协议、实际 systemd 升级/重复/崩溃恢复/回滚和单核/768MiB 复验通过；271 项回归通过。候选四流下载 RTT 仍有约 204–226ms 的排队代价，尚未发布或接入安装器，不能把此前普通/高并发通过当作高延迟全面生产放行。见[长链路报告](LONG-PATH-20261108.md)。

> **11 月 8 日完整补验更新：** 修复后的两任务三条件完整矩阵共 **20,160 条负载、0 错误**，267 项 root 回归无跳过；三 KVM 新增原生命令应用/重复、配置漂移拒绝、回退与重启全部通过。原始失败保留，普通/弱网未证实跨任务重复提速，持续组受生成器限制，不作抗封锁或全面生产放行承诺。见 [完整补验](benchmarks/paired-20261108/README.md)；前一阶段 2400 条针对性修复记录仍保留。

## 3.5.16阶段结论与交付范围（历史）

已在原项目上实现管理器 1.7.0 候选：固定官方 3.5.16 新安装、明确的核心升级/回滚、入口排空/恢复、健康代理独立更新/回退、失效和抖动保护，以及若干文件/下载安全加固。原单入口、双 SNI、双 WG、独立主备后端、Direct-DC、Secret/SNI 和代理链接模型保留。

实际验证采用本机 WSL、真实官方/历史核心二进制、真实 HAProxy/WireGuard/systemd，协议负载使用既有 Fake TLS/obfuscated2/padded-intermediate 工具和本地模拟 DC。没有访问三台美国生产服务器或已过期的荷兰 VPS，没有购买资源，没有更改仓库可见性，没有恢复长时实验或暂停的自动任务。

**本轮有界自动实验室验收通过，交付审查和隔离测试候选。** 已补验完整新参数矩阵、ARM64、短时资源回收、真实命令和三系统安装/维护/正常重启；初始失败不改判，逐批提交和范围单独列示。官方客户端、三台物理 VPS 公网跨地区、长期稳定性仍未验证。没有千兆跑满、全面客户端兼容、抗封锁或 24 小时稳定性的结论。

## 原项目身份与保护

- 先读取 `README-本地目录.md`。唯一开发仓库仍为 `telemt-dual-hop/`，初始工作树干净，main 为 `4510829288fa9779ea06dd6b543327392bda4999`，远程 main 查询结果相同。
- 原项目管理器实际为 1.6.0；默认 x86_64 核心为 `3.5.5+tdh.balanced2`，有固定 audit2 备选，ARM 为官方 3.5.5。没有从 v1.2.0 的假定重建项目。
- 检查了近期 Git 历史、1.3.1 原始脚本、安装/重复安装/管理器更新与回退、配置/服务模板、核心事务库、常驻健康代理、现有 CI 与证据目录。原有大量测试和恢复工具继续使用。
- `telemt-upstream-audit/` 只取固定 tag 和审计源码，仍保留原 detached HEAD；未把它变成管理器项目。
- 冻结 Release 目录、`legacy/` 脚本和历史实验归档未修改；原始脚本 SHA256 检查通过。
- 本次 GitHub API 返回原仓库 `private=false`，与 10 月历史目录说明不同；本次没有改变可见性。首次默认登录身份对原仓库返回 `push=false`；后续找到已有所有者登录并完成分支推送和 PR，不涉及仓库可见性更改。
- 原 v1.6.0 Release API 仍返回管理器资产 SHA256 `376f00d36a4fb1aa082002d96612d297d4f01e2e55935a1a3cf7155ed9f5d26f`。匿名下载 main 管理器也实际得到相同摘要；固定官方 3.5.16 GNU 资产匿名 HEAD 返回 200。仅验证下载可用性，没有执行该下载，也不把它当作 1.7 候选的公开安装成功证据，见 `anonymous-downloads.json`。

## 首阶段3.5.16上游锁定与差异（历史）

实际查询 `GET /repos/telemt/telemt/releases?per_page=30`，筛除 draft、prerelease 后最新正式版为 **3.5.16**，发布时间 `2026-10-31T11:59:29Z`。解析 Git 标签，下载两架构包并实际计算摘要，没有使用 main 或 latest 资产。

| 项目 | 固定值 |
|---|---|
| 旧官方 commit / tag | `ac71d92ec41dea00a7eafd6b8d350c3486633500` / `3.5.5` |
| 目标 commit / tag | `84e5b827a6619fb2e09ad3d09f253cdffe04bc2f` / `3.5.16` |
| annotated tag object | `210883304cae6a9aed583631634081087baaa4da` |
| x86_64 GNU 包 SHA256 | `e6f46b2f3becf29bfd555cec8a059e429f7c082c1af402499b3a7940cf8ebd34` |
| x86_64 二进制 SHA256 | `1954f18faeb9f5838e9444880f2ffb00704c6bd5b2836bc86dd64611537d49b8` |
| ARM64 GNU 包 SHA256 | `1e1bc87f05923b9673cbbaf34a3e50f7fdcf7d496cd24efcdb34c278ed18a659` |
| ARM64 二进制 SHA256 | `ebaec212fd5556ceca7fb0fcc4d3d23c01a8883d20a4cbc567219022d57c2a0b` |

[该阶段机器可读版本锁](benchmarks/upstream-3517/previous-3.5.16-lock.json)和[原始正式 Release 摘要](benchmarks/stable-20261107/upstream-release.json)随交付保留。摘要用于锁定收到的资产，不等同于独立可复现构建或第三方供应链证明。

固定版本之间涉及约 600 个文件的大量重组。审计重点是本项目使用的配置加载、Direct relay、socket、认证入口、API readiness、热重载、conntrack、CLI/systemd 和 ME 生命周期相关变化；没有声称对全部上游源码和所有新增 WEB 路径完成独立形式化或逐行安全证明。

| 上游阶段/来源 | 与本项目的关系及处理 |
|---|---|
| 3.5.6–3.5.7 | ME warmup 并发控制、生命周期及 WEB 修复；本项目继续显式关闭 ME/WEB，不额外开启线程池或 Carrier |
| [3.5.8](https://github.com/telemt/telemt/releases/tag/3.5.8) | Unix 配置/包含文件安全锚定、用户准入、进程级资源 authority、quota/PID/日志安全；配置软链接、硬链接及不安全布局可能被拒绝。托管原模板通过真实升级预检与启动 |
| 3.5.9–3.5.11 | privileged helper 路径、conntrack actor 和缺失链恢复；维持非特权服务与 `inline_conntrack_control=false`，不接管用户防火墙 |
| 3.5.12 | `d65efcc` 自主 ME→Direct fallback 恢复、`84d5e98` NAT discovery cancellation；ME 未在此架构启用，仅做源码/变更边界评估 |
| [3.5.14](https://github.com/telemt/telemt/releases/tag/3.5.14) | `556affc` 自主且有生命周期边界的 ME pool convergence；不应再叠加旧管理器级 ME 重连控制器。WEB decoy DNS 为另一路径 |
| [3.5.15](https://github.com/telemt/telemt/releases/tag/3.5.15) | `77ae110` 在输入 Pending 时 flush 自适应 relay 的已写缓冲；`a8d28e6` 观察 ME 子任务完成以修复停滞；`199cc64` ME 准入变化。本项目不设无限连接，也不移植旧 flush 补丁到已含修复的核心 |
| [3.5.16](https://github.com/telemt/telemt/releases/tag/3.5.16) | 重点新增 WEB retry jitter、Retry-After 提示和请求处理调整；原生 TCP 配置没有因此改成 HTTP，WEB 保持默认 false |

逐项配置结论：

| 本项目字段 | 目标版本状态 | 实施 |
|---|---|---|
| `config_strict`、`fast_mode`、`general.modes.tls` | 保留；fast_mode 上游默认仍 true | 保留明确值，真实启动/协议验证 |
| `use_middle_proxy=false` | 保留；上游默认 true 不覆盖项目选择 | 不构建 ME pool，不混淆 ME 与 Direct-DC 的性能 |
| `direct_relay_buffer_budget_max_bytes` | 保留；3.5.8 起进程级 authority，修改需重启 | 保持原预算，核心升级明确重启 |
| `server.max_connections`、conntrack 配置 | 保留；进程级重启生效 | 不把热重载响应当作实际容量已变更 |
| `proxy_protocol`、可信 CIDR、header timeout、WG listener | 保留；listener/信任边界为重启字段 | 原配置字节保持；不暴露公网 listener |
| `server.api.*` 和 ready envelope | 本次使用字段及 `ok/data.ready` 兼容 | 真实 401、只读写入 403、ready 和实际 PID 摘要已验 |
| Metrics | 模板未启用；上游默认绑定范围有安全调整 | 没有增加监听或依赖默认公网绑定 |
| `server.conntrack_control.inline_conntrack_control` | 目标源码默认 false；部分配置文档仍列 true | 按源码评估并继续显式 false |
| censorship/SNI/Fake TLS、用户 Secret | 保留；不存在本模板必须重命名的键 | 原模板被新版解析并启动；不重签用户链接；旧 0710 父目录必须做独立权限迁移 |
| WEB listener/Carrier、quota calendar 等新增字段 | 当前模板未使用 | 不自动新增；官方客户端及定制配置另行验证 |

官方 3.5.16 的 `src/transport/socket.rs` 仍含固定收发缓冲设置。因此切换到官方核心不等于继续使用 balanced2 的所有 socket 策略；本轮没有无条件宣称提速。

## 已修复的问题与代码

| 问题 | 实际修改 | 验证 |
|---|---|---|
| 管理器默认仍安装旧核心，且缺少公共核心升级命令 | 固定官方双架构 pin；复用 `core_install.py`、`CoreTransaction`，新增 `core_upgrade.py` 与单文件嵌入生成器 | 真实首次安装、严格旧配置解析、升级/重复升级/回滚；静态版本锁一致性测试 |
| all() 要求五个 DC 都通，容易误摘除 | 多数票、阈值、恢复冷却、样本过期、监控线程退出恢复、本地端口检查 | 确定性时钟/失败输入测试，真实健康代理进程对真实核心的停机/恢复 |
| 维护前缺少方便的入口排空 | `entry_maintenance.py` 固定 HAProxy runtime 操作；peer 两线路必须 UP | 固定输入、异常输入、命令拒绝与单文件一致性测试；不冒充已完成远程集群协调 |
| 核心与配置备份可能在中断时关联错误 | snapshot 标识随 prepared/committed/prior journal 绑定；不使用独立可提前推进的指针 | 中断恢复、目标二进制已写入仍拒绝未完成事务、失败第二次升级保留 prior snapshot、配置变更拒绝回退 |
| 仅凭旧官方版本字符串识别核心 | 补齐旧双架构二进制摘要；先校验文件/父目录/摘要，再执行版本查询 | 未知/可写核心执行前拒绝，旧双架构身份测试，真实新核心识别 |
| 状态文件 source 的文件信任边界不足 | 拒绝软链接、额外硬链接、可被替换的父目录 | 真实安全路径反例与执行前拒绝测试 |
| 旧父目录 0710 不满足新版安全配置读取 | 新安装 root:telemt 0750；旧节点维护时先备份再迁移，失败/原核心回退恢复 0710，原子私有迁移记录可追踪中断 | 实际三系统发现反复退出及 os error 13；新增非特权目录打开、密钥保护、事务/失败/中断/异常记录回归 |
| 无明确健康代理更新/回退入口 | `health-refresh` / `health-rollback` 复用同一事务引擎，独立备份 | 原子/幂等/回退测试，实际新代理逻辑独立进程验收 |
| 新安装队列最低值过大 | 65535/32768 的强制最低值改为 4096，保持更高用户原值 | 私有 mount 中实际生成 sysctl 文件，宿主 sysctl 操作替身，不改宿主参数 |
| 健康 HTTPError 资源回收、HTTPS 重定向、Windows 换行 | 显式 close HTTPError、限制重定向 HTTPS、活动脚本 LF 属性 | 重定向令牌保护回归、ShellCheck、真实 README 下载边界测试 |
| 老三节点工具仅接受旧核心，隔离冷启动容易误判 | 加入固定 stable 核心、脱敏启动诊断、测试专用 offline bootstrap、丢包/抖动参数 | 实际发现并保留失败，然后完成正常及受损网络验收 |

主要文件为 `telemt-dual-hop.sh`、`tools/health_agent.py`、`tools/core_transaction.py`、`tools/core_upgrade.py`、`tools/entry_maintenance.py` 和对应测试。嵌入副本由生成器和一致性测试校验，生产交付仍是单文件管理器，无新生产语言依赖。

## 升级、回退和连接影响

完整步骤见 [升级文档](UPGRADE-1.7.md)。配置文件保持原字节，不在升级中修改 Secret/SNI、密钥或模板；旧父目录需可追踪、可回退的 0710→0750 权限迁移。旧二进制、配置、服务、WG、状态和必要的旧许可材料在停止前备份；候选失败恢复旧核心。配置有外部改动时拒绝自动回滚，保留人工审查路径。

入口 drain 为运行时状态，不持久化，不可在维护窗口重载入口。一次只升级一个后端。后端的 `--maintenance` 是明确维护确认，不伪装成已自动验证入口和另一台服务器。已有 TCP 会话不能跨后端迁移。

健康代理更新和核心更新分开，避免 `a update` 隐式重启业务。健康代理备份不与核心事务覆盖同一文件；更新健康代理后仍能使用对应核心回退。

## 测试环境及真实结果

WSL Ubuntu **26.04.1**，内核 `6.18.40.1-microsoft-standard-WSL2`，Python 3.14，HAProxy `3.2.9-1ubuntu2.2`；约 7.5GiB WSL 内存。目标生产 OS 检查仍只接受 Ubuntu 22.04，没有将 WSL 版本放宽为生产支持。

两架构 GNU 二进制最高 GLIBC symbol 要求均为 2.34，低于 Ubuntu 22.04 的 2.35；该项原为 ABI 静态证据；现已补充 Ubuntu 22.04.5 的实际核心与服务验证，仍不替代三台完整系统的安装/重启验收。生产无 Docker，实验也使用已存在的 WSL 工具，无宿主包/内核/SSH/防火墙/全局路由配置更改。

| 验收项 | 结果 | 证据/边界 |
|---|---|---|
| 改动前全部 Python 回归 | PASS，213 项 | `baseline-tests.log` |
| 此前阶段回归、Bash、ShellCheck、嵌入一致性 | PASS，249 项回归 | WSL root 全部运行，无跳过；包含原功能、恶意输入、权限迁移、备份、失败恢复；11 月 8 日最终代码为 264 项，见文末 |
| 原始 1.3.1 SHA256 | PASS | 冻结原始脚本未修改 |
| HAProxy SNI/碎片 ClientHello/PROXYv2/数据摘要/agent 切换 | PASS | `haproxy-integration.log`；loopback 集成 |
| 安装中断/错误/信号/待提交/已提交/旧状态 | PASS，27 场景 | 私有 mount+net namespaces，真实文件恢复路径；宿主服务/防火墙/账号操作是明确替身 |
| 官方核心首次安装 | PASS | 真实固定包与非特权 `--version`；测试仅把账号查找映射到已有 nobody，不创建宿主 telemt 账号 |
| 旧配置升级、同版本幂等、显式回滚 | PASS | 真实 systemd 单元和两个真实核心；旧二进制恢复，配置/令牌不变 |
| API 未认证/只读 | PASS，401 / 403 | 真实 3.5.16 API |
| 双 Secret / Fake TLS / 双 SNI | PASS（协议工具） | HMAC、MTProto framing、双向持续确认和 SHA256；不是官方客户端 |
| 核心 SIGKILL 后 systemd 恢复 | PASS | 实验单元 RestartSec=1，约 1.27s；生产模板 RestartSec=5，不承诺同样时间 |
| 生产健康代理进程 | PASS | 冷启动约 20.24s、核心停止约 2.81s 报 down、恢复约 10.02s；单个模拟 DC，3/5 票和冷却另有单元覆盖 |
| 三逻辑节点真实双 WG+PSK、HAProxy、3.5.16 | PASS | 100ms 配置延迟，独立模拟 DC；正常与 0.1% 丢包/5ms 抖动两组 |
| 主备、真实后端进程重启、WG 接口 down/up | PASS | 新连接恢复到预期后端，既有会话不承诺迁移 |
| HAProxy USR2 期间既有传输 | PASS | 持续传输跨重载并校验摘要；未默认开启 splice |
| 30/50/100 并发 | 候选各组均有 PASS，失败历史保留 | 见下表及两个原始批次；不拼成一个全通过的单批 |
| 短时 FD 释放 | PASS（有限观察） | 3.5.16 空闲后 FD 为 14，初始约 16；缓存 RSS 保留，不据此证明长期无泄漏 |
| 三角色安装、重复执行安装命令、正常系统重启 | PASS，Ubuntu 22.04 KVM | 三客体真安装和管理命令，依赖预装后克隆；重复命令进入原管理面板，非强制覆盖重装；不代表裸机或物理跨区 |
| ARM64 原生运行 | PASS | Ubuntu 22.04.5 aarch64，正式包/生产架构识别、20 条双 SNI 传输、API 401/403；不是 ARM 吞吐基准 |
| 180 秒连接反复建立/关闭 | PASS，900/900 | FD 14→14；暖机后 RSS +2.29MiB，cgroup 峰值 77.65MiB、OOM 增量 0；不能证明长期无泄漏 |
| Ubuntu 22.04 CI 工作流 | PASS | stable 验收 `42351018133`、installer/relay 回归 `42351022710`；具体范围见下文 |
| ME 实际池、WEB Carrier、定制配置迁移 | NOT RUN | 当前生产架构未启用；仅关键变更审计 |
| 官方 Desktop/Android/iOS、真实 Telegram 文件 | NOT RUN | 没有借用账号或伪称工具就是客户端 |
| 三台物理 VPS 跨地区、运营商、真实千兆容量 | NOT RUN | 严格生产隔离；过期荷兰机未访问 |
| 24 小时、长期泄漏、抗封锁/抗识别 | NOT RUN | 未重启已停止的耐久实验；无此类实证 |

### 可比较的性能数据

一次主要对照：共享同一 Linux 主机，私有 loopback namespace，qdisc 单次排队 `25ms`、共享 `100mbit` 限速；整个控制器、模拟 DC、HAProxy、核心共用 CPUQuota=100%、MemoryMax=1GiB、SwapMax=0。每条 256KiB，真实原生代理协议，持续连接尾部确认，不以 EOF 解救缓冲尾部。

此 loopback qdisc 被多段 TCP 共同经过，`100mbit` 不是独立公网端口的可用有效载荷上限。握手时间约 218ms 是完整协议握手，不是单次 TCP RTT。只做短批功能/容量比较；没有足够重复轮次或真实跨区大文件数据支持“稳定提速”。

| 并发 / 方向 | balanced2 成功率 / Mbps | 3.5.16 成功率 / Mbps | 批次 |
|---|---|---|---|
| 30 上传 | 30/30 / 22.631 | 30/30 / 22.630 | 首批 |
| 30 下载 | 30/30 / 22.656 | 30/30 / 24.403 | 首批 |
| 50 上传 | 50/50 / 24.986 | 50/50 / 25.546 | 首批 |
| 50 下载 | 50/50 / 24.331 | 50/50 / 23.745 | 首批 |
| 100 上传 | 100/100 / 25.672 | 100/100 / 26.450 | 模拟 DC backlog 修正后 |
| 100 下载 | 0/100 / 不作有效吞吐比较 | 100/100 / 25.067 | 同一修正后批次 |

候选握手 p95：首批 30/50 并发约 222–232ms，修正后 100 并发上传 268.38ms、下载 228.25ms。修正后候选 100 并发完成延迟 p50/p95：上传 7856.10/7872.16ms、下载 8259.21/8269.29ms；错误率均 0。该批峰值核心 RSS 约 25.64MiB，核心 CPU 占一个 CPU 的约 1.51%/1.08%，不包括控制器/模拟 DC/HAProxy 的开销。不能据此预测真实 100 用户的 CPU 或带宽。

[完整 p50/p95、CPU、RSS 表](benchmarks/stable-20261107/performance-table.md)及原始 JSON 保留全部数据。没有采集负载 cgroup 的 OOM 增量/峰值总内存，不能给出该指标为零的正式断言。

[最终代码摘要](benchmarks/stable-20261107/delivery-source.json)、[早期 236 项回归](benchmarks/stable-20261107/delivery-tests.log)、[最终 249 项回归](benchmarks/continuation-20261107/final-root-tests.log)、[真实升级验收](benchmarks/stable-20261107/system-upgrade-delivery.json)和[清理核对回执](benchmarks/stable-20261107/cleanup.json)均保留。该清理回执对应之前 WSL 批次；新的 CI 每批由自身监督器确认清理，不沿用旧回执冒充新结果。

### 失败记录及修复界限

1. 首批 100 上传分别只有旧 65/100、新 68/100 完成；模拟 DC accept backlog 64 低于测试突发需求。将**测试 DC**队列改为 256 后，两核心上传均为 100/100；没有把这个测试参数当作生产优化。
2. 修正后旧核心 100 下载出现 100 个等待 ACK 超时，新核心 100/100 通过。旧核心在前一批 100 下载通过，说明此负载下存在不稳定现象；未完成独立因果实验，不把所有失败单独归因于核心某一行代码，也未修改冻结旧 Release。
3. 三节点初次准备目录权限错误，随后隔离 DNS/STUN/TLS front 启动采样导致 35s 就绪窗口耗尽。增加脱敏诊断后确认具体等待，测试专用 offline bootstrap 关闭外部采样，生产模板保持原值；协议/隧道路径通过不证明生产伪装采样已验证。
4. 一次把最终单元测试和三节点实验并行运行，观察测试发现保留的 `tdh-audit-b` 名称已占用，安全拒绝并有两项跳过。实验完成清理后改为串行，全部通过；没有弱化名称/归属检查。保留 `final-tests.log` 作为失败证据。
5. 管理器版本更新后，旧测试中硬编码 1.6.0 导致预期备份没有构造出来，修正为 1.7.0 夹具。没有通过删除测试掩盖失败。

## 实际验收命令与复现

以下是实际执行命令的核心形式，完整输入归档路径在 `lab-control/work/2026-11-07-stable-compat/`，原始结果在 `lab-control/evidence/2026-11-07-stable-compat/`，已审查可分享结果位于本文相邻 `benchmarks/stable-20261107/`。

```text
git status --short --branch / git remote -v / git log -12 --oneline
git switch -c dev/telemt-stable-compat-20261107
gh api 'repos/telemt/telemt/releases?per_page=30'
git fetch origin tag 3.5.14 tag 3.5.15 tag 3.5.16       （上游参考仓库）
git rev-parse '3.5.16^{commit}'
git diff --stat 3.5.5 3.5.16
git log --oneline 3.5.14..3.5.16
gh release download 3.5.16 --repo telemt/telemt ...     （两个 GNU 包及校验文件）
readelf --version-info <固定二进制>
bash -n telemt-dual-hop.sh
shellcheck -x -S style telemt-dual-hop.sh tests/fixtures/*.sh
python3 -m compileall -q tests tools
python3 tools/embed_core_install.py --check
python3 tools/embed_health_agent.py --check
python3 tools/embed_core_upgrade.py --check
timeout 30 bash telemt-dual-hop.sh --self-test
sha256sum --check legacy/SHA256SUMS
timeout 180 python3 -m unittest discover -s tests -p 'test_*.py'
timeout 60 python3 tests/lab.py
timeout 90 python3 tests/install_recovery_lab.py --root /tmp/tdh-audit-install-final-20261107
timeout 180 unshare --net sh -c 'ip link set lo up; exec python3 tests/stable_system_lab.py ...'
systemd-run --wait --pipe --collect -p CPUQuota=100% -p MemoryMax=1G -p MemorySwapMax=0 -p RuntimeMaxSec=300 unshare --net ... tests/stable_acceptance.py ...
timeout 200 python3 tests/dualhop_lab.py --root /tmp/tdh-audit-stable-20261107 --build stable --separate-dc --rtt 100 run
timeout 200 python3 tests/dualhop_lab.py --root /tmp/tdh-audit-impaired-20261107 --build stable --separate-dc --rtt 100 --loss-percent .1 --jitter-ms 5 run
git diff --check
```

带 `...` 的行是实际命令的摘要，不应直接复制执行。可执行、完整的固定下载/包校验、命名空间、资源限制及脚本参数见 `.github/workflows/stable-compat.yml`。先校验已保存的两个 archive，再在获准的隔离 Linux 中执行；不要在三台生产机上运行实验工具。私有命名空间实验与使用相同保留名称的观察测试必须串行。

## 补充：Ubuntu 22.04 CI 已完成

代码提交 `dcdea2b20ce4ce2d3525c843aa1f3bb740abbfe5` 的 [固定核心完整验收](https://github.com/bababoyi6/me/actions/runs/42351018133) 和 [安装器/relay 回归](https://github.com/bababoyi6/me/actions/runs/42351022710) 均为 success。运行系统为 Ubuntu 22.04.5、内核 `6.8.0-1070-azure`，HAProxy 2.4.30。原始脱敏 JSON 和运行元数据保存在 [本批证据](benchmarks/stable-20261107/ci-42351018133/)。

真实官方包安装、旧配置升级、同版本幂等、原核心回滚、双 Secret/Fake TLS 传输、API 401/403、核心 SIGKILL 恢复及生产健康代理进程全部通过；旧二进制、配置和令牌保持，临时单元已删除。实验单元 RestartSec=1 下核心恢复约 1.07s，健康代理冷就绪约 20.27s、报 down 约 2.81s、恢复约 9.03s；这些不是生产 RestartSec=5 的恢复承诺。

本批仅测 3.5.16，同一批 30/50/100 并发上下行及四条小流共 **364/364** 校验成功，错误率为 0；不把它与 WSL 旧核心批次相减计算提速。

| 并发 | 方向 | 成功 | Mbps | 握手 p50 / p95 ms | 完成 p50 / p95 ms |
|---:|---|---:|---:|---:|---:|
| 30 | 上传 | 30/30 | 22.610 | 221.45 / 231.77 | 2747.28 / 2763.30 |
| 30 | 下载 | 30/30 | 21.079 | 216.58 / 221.97 | 2887.29 / 2933.88 |
| 50 | 上传 | 50/50 | 24.944 | 223.46 / 235.79 | 4170.97 / 4185.47 |
| 50 | 下载 | 50/50 | 24.026 | 217.06 / 224.30 | 4281.34 / 4321.05 |
| 100 | 上传 | 100/100 | 26.398 | 233.34 / 252.58 | 7901.17 / 7911.24 |
| 100 | 下载 | 100/100 | 25.204 | 224.26 / 237.75 | 8253.58 / 8290.14 |

此外，真实双 WG+PSK、100ms 配置延迟、0.1% 丢包、5ms 抖动、主备恢复、真实核心进程重启、WG down/up 以及 master-worker 重载期间既有传输均通过。仍使用模拟 DC，三逻辑节点共享一个 runner，不是三台独立 VPS；健康代理在系统步骤单独真实运行，三节点步骤使用进程状态代理。

首轮 `42350787027` 失败保留：核心升级、协议传输和崩溃恢复已完成，停在健康代理冷启动。后续观测确认服务 UID 无法读取 runner home 下仓库源码，修正为将完全相同的代码暂存至可读的测试目录；没有放宽宿主权限或改用 root 运行。新结果 `repository_agent_readable_by_service_uid=false` 与实际代理成功构成对应证据。旧工具硬编码 WSL 的范围标签也改为实际 OS/kernel；第一轮原始 JSON 未伪改。上述批次未运行三系统安装/重启和 ARM，后续补验见下文；官方客户端、物理公网跨地区和长期稳定性保持未验证。

## 补充：原生 ARM64 与有界资源检查

[本轮补充证据和复验入口](benchmarks/continuation-20261107/README.md)保留原始 JSON 与文件校验。ARM 工作流 `42352100821` 在原生 `ubuntu-22.04-arm` 上执行；不是交叉编译成功或 x86 模拟。共享协议夹具修改后，x86 工作流 `42352100726` 也通过。

资源工作流 `42352935615` 运行 182.0 秒，30 连接/波，共 900/900 条真实原生协议连接成功，数据摘要一致、模拟 DC 无错误；空闲 FD 14→14。核心暖机后 RSS 增长 2,404,352 字节，cgroup（含控制器、HAProxy、模拟 DC）峰值 81,424,384 字节，OOM/OOM kill 增量 0，CPU 累计约 4.17 秒。此处小数据连接检查不替代前述吞吐矩阵，也不能用短时门槛证明长期无泄漏。

三系统工作流复用维护中的 `full_system_ci.py` 和 `system_vm*.py`，不执行归档脚本。先检查真实 KVM，再校验 Ubuntu 官方镜像签名及摘要；三个客体各 1 CPU / 1GiB，控制器总限 0.6 CPU / 3GiB / 无 swap。只向宿主 loopback 暴露测试入口和 SSH，临时 SSH 密钥、客体磁盘及带凭据的交互记录不导出。

修复提交 `9dabd6a1b21c1e0b2082379a278dced38080439b` 的[三系统批次 42369032262](https://github.com/bababoyi6/me/actions/runs/42369032262)完整通过，监督器用时 584.9 秒。三个角色都执行了固定提交原脚本的真实下载/校验/安装及重复执行；入口受保护文件 12 个、各后端 9 个的摘要/模式/UID/GID保持，管理器与核心身份不变。随后实际执行入口 drain、旧 balanced2/0710 夹具准备、核心升级/重复升级、回滚/重复回滚、再次升级、健康代理更新和入口 resume；验证目录 750→710→750，用户配置不变。

正常、backend1 Telemt 停机、恢复三个阶段各两条线路均完成真实 Telegram `req_pq/resPQ`（验证 nonce），HAProxy 会话增量证明停机后两条新连接走 backend2、恢复后回到各自主节点。随后三个客体正常关机并重新启动，boot ID 全部变化，持久文件内容一致、诊断通过，两条公网协议抽检再次成功。历史工具 scope 字段称 public DH，实际仅上述握手阶段，不包含账号登录、授权密钥生成、消息或文件。监督器出口 0、宿主 OOM 增量 0、VM/磁盘/临时密钥清理通过。

三个客体共享 runner、均为 1GiB，未完全复刻洛杉矶后端 2GiB；依赖预装后克隆，不是三套裸系统安装性能评估。实际公网仅为 CI 出站至 Telegram 的有限握手，不是洛杉矶/纽约物理三机或官方客户端验收。

首轮 `42352574411` 已如实判 FAIL：入口安装成功，backend1 安装等待链接失败并自动回滚；没有继续冒充升级/重启成功。监督器记录清理完成、宿主 OOM 增量 0。诊断轮 `42353312993` 观察到实际核心退出码 1、反复重启、API 不可达及 Permission denied / os error 13。对照固定上游 `src/util/secure_fs/path.rs`，确认旧 0710 父目录不能被服务账号以 O_RDONLY 打开。此前合成工具用 0755/0750 临时父目录，未覆盖旧真实布局，因此没有发现此缺陷。修复新安装和旧节点迁移，并新增真实非特权目录读取/密钥保护、事务回退及中断恢复测试；完整复验另行记录。补充采样不导出凭据或日志原文。

用户本轮明确暂不提供设备，先完成当前环境验收。Desktop/Android/iOS 保持 NOT RUN；没有等待设备或使用代理协议工具冒充官方客户端。

### 最终交付代码复验

最终代码提交为 `94857f2847bacd199a9955c7cff660022ed75a8a`，管理器 SHA256 为 `7680bcb8e939d40002b311154505e0b5d549b1ce285fbdbf4073ea3d35d97915`。在首轮通过之后，继续补强迁移前二进制备份、中断恢复基线核对及普通状态保存不提前迁移，随后重新验收准确交付代码：

- [安装器 CI 42370201295](https://github.com/bababoyi6/me/actions/runs/42370201295)、[x86 真实核心/隧道/协议 42370197039](https://github.com/bababoyi6/me/actions/runs/42370197039)、[原生 ARM64 42370196967](https://github.com/bababoyi6/me/actions/runs/42370196967)、[三系统 42370196980](https://github.com/bababoyi6/me/actions/runs/42370196980)均 success，head SHA 一致。
- WSL root 全部 **249 项**，20.900 秒，无跳过。额外 9 项配置访问测试覆盖内核真实非特权读取、密钥不可读、迁移/回退/重试、中断恢复、被改动记录及普通状态保存；Bash、ShellCheck、嵌入一致性均通过。
- 最终 x86 为 **364/364**，100 并发上/下行 26.365 / 25.158Mbps，握手 p95 253.94 / 241.17ms，核心最高 RSS 26.44MiB，空闲 FD 14；仍是已说明的 25ms/rate100Mbps 共享 qdisc 合成条件。[完整新批次表](benchmarks/continuation-20261107/README.md)保留 p50/p95，不与不同主机或批次拼算提速。
- 最终三系统用时 **599.0 秒**，所有原生命令阶段、750/710 权限迁移与回退、三角色正常重启及两条重启后真实 req_pq/resPQ 都通过，三个 boot ID 变化、持久文件内容一致。监督器 exit=0、无停止原因、宿主 OOM 增量=0、清理确认=true。

所有新增原始结果与 CI 作业元数据见[本轮清单](benchmarks/continuation-20261107/ci-summary.json)。目录中不包含临时 SSH 私钥、客体磁盘、seed、原始交互日志、用户链接或账号凭据。代码摘要按 Git 对象字节记录，避免 Windows CRLF 工作副本与实际 Linux/ZIP 字节混淆；它不是历史批次代码身份的替代物。后续文档/证据提交不改变上述代码字节。

补充实际执行入口：

```text
wsl --cd 'D:\project\MTP脚本\telemt-dual-hop' --user root --exec timeout 180 python3 -m unittest discover -s tests -p 'test_*.py' -v
sudo env RUNNER_TEMP="$RUNNER_TEMP" TDH_FULL_SYSTEM_PROFILE=candidate TDH_CANDIDATE_REF="$GITHUB_SHA" python3 tests/full_system_ci.py
gh run view 42370196980 --repo bababoyi6/me --json status,conclusion,jobs
gh run download 42370196980 --repo bababoyi6/me --name candidate-three-system-evidence --dir <本地证据目录>
```

第二行为已完成镜像/依赖准备的临时 CI 入口，完整可复现准备与限制在相应工作流；不是生产命令。下载命令的目标目录由复验者选择。三系统工具内实际执行的 `a core-upgrade/core-rollback --maintenance`、`a backend-drain/resume` 等固定命令见 `tests/full_system_candidate.py`。

## 后续放行门槛

Ubuntu 22.04 的有界核心工作流、原生 ARM64、三系统角色安装/重复执行、原生管理命令升级/回退后正常重启及持久文件一致性已通过。当前环境验收不再受缺少测试 VPS 阻塞。发布和生产放行仍需按下面的真实环境边界安排。

获得明确授权和适当测试节点后，才做真实三机公网路径及 Desktop/Android/iOS 官方客户端的连接、空闲、文件上传/下载、切网和备用恢复测试。分别核对 30/50/100 活跃用户的真实消息/文件、跨区 RTT、CPU/总内存/FD/重传、成功率和恢复分布。未获得授权前继续保持生产隔离，不要求用户为了本地开发立即购买 VPS。

最初使用 `wolfgang008` 推送被 GitHub 403 拒绝。用户指出已有写权限后，确认本机也保存了仓库所有者 `bababoyi6` 的既有登录；仅在当前操作进程内使用该登录，未改变默认账号、未打印或落盘令牌。所有者权限确认可写，审查分支已推送，[PR #9](https://github.com/bababoyi6/me/pull/9) 已创建为草稿。新的 Ubuntu 22.04 工作流因尚未位于默认分支而不能直接 dispatch，补充仅当前开发分支且限代码/测试变动的 push 入口；复验已通过，准确运行与范围见新增 CI 小节。最初的 403 保留为历史证据，当前不再属于写入受阻状态。见 [远程交付记录](benchmarks/stable-20261107/remote-delivery.json)。

生产是否部署、何时发布 Release、何时改变仓库可见性均未由本次开发自动决定。已停止的 24 小时实验和暂停自动任务维持原状。

## 2026-11-08 连接预算修复与最终交付

最新正式 Release API 复核仍为 3.5.16，见 [复核元数据](benchmarks/paired-20261107/latest-stable-recheck-20261108.json)。原有 1.6 双线路、主备、WG、Direct-DC、fast_mode 与保守调优继续复用；其 flush 改进上游已有对应实现，socket 自动缓冲与低 RTT 补丁没有无证据叠加。

弱网逐连接日志显示：FakeTLS 和原生认证均成功，随后默认 3000ms 上游连接总预算耗尽，新旧核心都可发生。修复显式采用 `tg_connect=4`、`upstream_connect_retry_attempts=2`、`upstream_connect_budget_ms=9000`。确定性 3500ms DC SYN 延迟下，两版原默认均失败、修复后均完成；两核心各六轮 100 并发上下行共 **2400/2400** 条负载通过，OOM 增量 0。准确源码、条件、尾部 ACK、SHA256、字节数、日志和原失败见 [修复报告](PAIRED-PERFORMANCE-20261107.md)。这是连接可靠性修复，不是速度提升或长期零故障证明。

新安装生成三个参数；旧节点明确运行 `connect-refresh --maintenance`，事务只迁移缺失或上游默认值，定制值拒绝覆盖。配置以原 UID/GID、0640 原子替换，旧文件备份 0600/0700，解析预检后重启并检查就绪，失败自动恢复；`connect-rollback` 恢复原字节。并发编辑反例验证了：预检期间修改 Secret 会拒绝维护且保留新 Secret。核心升级仍不隐式修改 TOML。

此前阶段生产代码提交 `31e2ac5e9635ae2adef3b61a07b5e43807066967`，管理器 Git 对象 SHA256 `cdbca06310a09950fc6b68950d16621427ecbbfd9a61c354dd012514334cb93a`。其后的资源上限、验收工具修改与完整复验见下节。

| 实际验收 | 结果与准确来源 |
|---|---|
| Bash、ShellCheck、内置测试、完整 root 回归与原功能检查 | [42448682328](https://github.com/bababoyi6/me/actions/runs/42448682328) PASS；264 项，19.540 秒，root 无跳过 |
| 最终代码真实 systemd 配置迁移/重复执行/权限/原字节回退、核心崩溃恢复、30/50/100 协议与双 WG 恢复 | [42448677876](https://github.com/bababoyi6/me/actions/runs/42448677876) PASS；提交 31e2ac5 |
| 最终代码原生 ARM64 | [42448677887](https://github.com/bababoyi6/me/actions/runs/42448677887) PASS；提交 31e2ac5 |
| 默认连接策略变更后的三 KVM 角色安装/重复安装、原生维护、双线路公共 req_pq 与正常重启 | [42448064301](https://github.com/bababoyi6/me/actions/runs/42448064301) PASS；提交 19ead15。最终并发编辑保护另由上两项复验，不冒充最终 SHA 的完整重启测试 |
| 故障定位与确定性/弱网修复 | 42447142628、42447679131；原失败和成功分别保留 |

本轮还修正了模板保护测试的已批准摘要：只有三个连接默认参数变化，其余运行模板继续受原保护。首次 19ead15 因旧模板摘要不符而 FAIL，后续 4ed3b49 与 31e2ac5 均通过，没有删除失败测试。重复排队的三系统任务 42448677894 主动取消以避免无关整套重跑，保留在跑的 19ead15 系统验收及最后配置事务的真实 systemd 验证。KVM 初次 OS 准备的 SSH 重置后按已有有界恢复流程成功，原 `preparation-failure.json` 也保留。

实际命令均在工作流中可复验：`python3 -m unittest discover -s tests -p 'test_*.py' -v`、隔离 namespace 中的 `tests/connect_budget_lab.py`、`tests/weak_network_diagnostic.py --correlate --retry-policy`、`tests/stable_system_lab.py`、`tests/dualhop_lab.py` 与三 KVM `tests/full_system_ci.py`。CLI 参数、摘要、预算、提交和环境以相应工作流及 JSON 为准；本机仅做 Windows 文件/Git/静态处理，没有传输本地大规模负载。

此阶段结束时完整双 runner 新参数矩阵尚未重做，现已由下节补齐。官方 Desktop/Android/iOS、真实三台物理 VPS 跨地区与 Telegram 文件性能、24 小时稳定性及抗封锁证明仍未验证。初始八次失败没有逐连接日志，不能逐条补写根因。当前仍为候选，未合并、发布或部署生产。

## 2026-11-08 完整参数复验及原生命令补齐

本轮继续固定真实发布二进制，没有重建核心。完整配对 [42450540276](https://github.com/bababoyi6/me/actions/runs/42450540276) 使用提交 42ecd9a5867261c33a57a979207c30046c1be0cd，分别在 Intel Xeon Platinum 8370C 和 AMD EPYC 9V74 上完成 6/4/4 对、三条件、30/50/100 并发上下行。每个任务每核心 5040 条负载，总计 **20,160/20,160**；预热和 8/25 秒诊断不计入。两任务 OOM/OOM kill 增量 0，结束时均无遗留子进程，所有核心空闲后 FD 为 14。

普通组新版/旧版吞吐比中位数变化：30 上传 +3.57% / +3.59%，下载 -1.70% / -1.63%；50 上传 +3.59% / +3.61%，下载 -2.25% / -1.29%；100 上传 +3.03% / +3.15%，下载 +5.24% / +5.86%。**中位数超过 +5% 不等于每一对都超过门槛；按预登记的全配对与 p95 判据，普通和弱网都未证实跨任务重复领先。** 持续传输的生成器达到瓶颈，不用于核心极限排名。

两核心都使用新管理器的 4 秒单次/两次/9 秒总预算配置；这是同参数核心比较，不能冒称原样 1.6 整套部署与新部署的对照。连接修复也惠及 balanced2，不能据此说新核心天然更稳定。两次普通组 100 下载中位数分别为 23.77→25.06 Mbps、23.78→25.10 Mbps，但这是 100Mbps 多段 loopback 条件，绝不是公网千兆或 Telegram 文件测速。每轮完成 p50/p95、真实吞吐与配对范围见 [完整数表](benchmarks/paired-20261108/README.md)和原 JSON。

三条件下观测最大后端 RSS：旧/新分别为 29.23→39.69 MiB、29.14→41.74 MiB；新版此项更高，不能宣传低于旧版。RSS 为采样值，不含全部内核 socket 内存，整体 cgroup 还包含生成器、HAProxy 与模拟 DC。空闲 FD 回收及短时 OOM 为 0 不证明长期无泄漏。

代码另完成以下保护与验收：

- 1090842：连接配置维护最多读 1MiB、最多保留 8 个不同基线备份，达到上限在停服务前拒绝，不自动删除恢复资料；回退不受新增维护限额阻断。性能分析拒绝缺失、负数或非有限的生成器 CPU 数据，防止错误忽略瓶颈。
- b7b5ae1：[三 KVM 42451301643](https://github.com/bababoyi6/me/actions/runs/42451301643) 实际执行原生 a connect-refresh、重复无操作、配置漂移使 core-rollback 拒绝、connect-rollback 及重复、核心回退、再次升级和最终连接配置应用，随后两线路恢复、三个 boot ID 改变且文件一致。所有 18 项原生命令维护标记均通过。历史无该能力标记的合同仍保持兼容。
- 5a06645：汇总命令出现协议失败时返回非零退出码，仍写出失败 JSON；有实际子进程 CLI 反例。最终 [267 项 root 回归 42451808600](https://github.com/bababoyi6/me/actions/runs/42451808600) 为 20.878 秒、无跳过。
- 1090842 的 [真实 systemd/双 WG 42450779843](https://github.com/bababoyi6/me/actions/runs/42450779843) 与 [原生 ARM64 42450779836](https://github.com/bababoyi6/me/actions/runs/42450779836) 均通过。未为这些维护限额修改核心、转发层或运行配置参数。

不同提交没有拼成一次测试：测量源码、原生命令源码、回归源码及分析工具 SHA256 分别记录。独立 Git 对象重算的 [运行模板对照](benchmarks/paired-20261108/runtime-template-bridge.json) 证明测量与维护批次的受保护运行模板一致，但不把历史结果改标签为另一提交。完整证据含失败历史、合同重验、原始 JSON、资源观察和 manifest。

仍需真实条件的项目保持原边界：官方客户端消息/文件/切网、物理公网跨地区性能、长期稳定和抗封锁。没有设备与账号记录就不宣称客户端全兼容，没有持续 24 小时记录就不宣称长稳。生产服务器、主分支、仓库可见性和暂停的耐久任务均未被本次测试修改；没有采购资源。
