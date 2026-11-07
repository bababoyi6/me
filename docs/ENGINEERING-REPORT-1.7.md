# Telemt 3.5.16 兼容升级工程报告

日期：2026-11-07，北京时间。开发分支：`dev/telemt-stable-compat-20261107`。

## 结论与交付范围

已在原项目上实现管理器 1.7.0 候选：固定官方 3.5.16 新安装、明确的核心升级/回滚、入口排空/恢复、健康代理独立更新/回退、失效和抖动保护，以及若干文件/下载安全加固。原单入口、双 SNI、双 WG、独立主备后端、Direct-DC、Secret/SNI 和代理链接模型保留。

实际验证采用本机 WSL、真实官方/历史核心二进制、真实 HAProxy/WireGuard/systemd，协议负载使用既有 Fake TLS/obfuscated2/padded-intermediate 工具和本地模拟 DC。没有访问三台美国生产服务器或已过期的荷兰 VPS，没有购买资源，没有更改仓库可见性，没有恢复长时实验或暂停的自动任务。

**当前适合代码审查和实验室交付，尚不足以宣称完成全部生产放行条件。** Ubuntu 22.04 的完整三系统安装/重启、ARM 运行、官方客户端、公网跨地区、长时资源趋势等仍需后续验收。没有千兆跑满、全面客户端兼容、抗封锁或 24 小时稳定性的结论。

## 原项目身份与保护

- 先读取 `README-本地目录.md`。唯一开发仓库仍为 `telemt-dual-hop/`，初始工作树干净，main 为 `4510829288fa9779ea06dd6b543327392bda4999`，远程 main 查询结果相同。
- 原项目管理器实际为 1.6.0；默认 x86_64 核心为 `3.5.5+tdh.balanced2`，有固定 audit2 备选，ARM 为官方 3.5.5。没有从 v1.2.0 的假定重建项目。
- 检查了近期 Git 历史、1.3.1 原始脚本、安装/重复安装/管理器更新与回退、配置/服务模板、核心事务库、常驻健康代理、现有 CI 与证据目录。原有大量测试和恢复工具继续使用。
- `telemt-upstream-audit/` 只取固定 tag 和审计源码，仍保留原 detached HEAD；未把它变成管理器项目。
- 冻结 Release 目录、`legacy/` 脚本和历史实验归档未修改；原始脚本 SHA256 检查通过。
- 本次 GitHub API 返回原仓库 `private=false`，与 10 月历史目录说明不同；本次没有改变可见性。当前 API 身份对原仓库返回 `push=false`，不假定拥有发布权限。
- 原 v1.6.0 Release API 仍返回管理器资产 SHA256 `376f00d36a4fb1aa082002d96612d297d4f01e2e55935a1a3cf7155ed9f5d26f`。匿名下载 main 管理器也实际得到相同摘要；固定官方 3.5.16 GNU 资产匿名 HEAD 返回 200。仅验证下载可用性，没有执行该下载，也不把它当作 1.7 候选的公开安装成功证据，见 `anonymous-downloads.json`。

## 上游锁定与差异

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

[机器可读版本锁](telemt-stable-lock.json)和[原始正式 Release 摘要](benchmarks/stable-20261107/upstream-release.json)随交付保留。摘要用于锁定收到的资产，不等同于独立可复现构建或第三方供应链证明。

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
| censorship/SNI/Fake TLS、用户 Secret | 保留；不存在本模板必须重命名的键 | 原模板被新版解析并启动；不重签用户链接 |
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
| 无明确健康代理更新/回退入口 | `health-refresh` / `health-rollback` 复用同一事务引擎，独立备份 | 原子/幂等/回退测试，实际新代理逻辑独立进程验收 |
| 新安装队列最低值过大 | 65535/32768 的强制最低值改为 4096，保持更高用户原值 | 私有 mount 中实际生成 sysctl 文件，宿主 sysctl 操作替身，不改宿主参数 |
| 健康 HTTPError 资源回收、HTTPS 重定向、Windows 换行 | 显式 close HTTPError、限制重定向 HTTPS、活动脚本 LF 属性 | 重定向令牌保护回归、ShellCheck、真实 README 下载边界测试 |
| 老三节点工具仅接受旧核心，隔离冷启动容易误判 | 加入固定 stable 核心、脱敏启动诊断、测试专用 offline bootstrap、丢包/抖动参数 | 实际发现并保留失败，然后完成正常及受损网络验收 |

主要文件为 `telemt-dual-hop.sh`、`tools/health_agent.py`、`tools/core_transaction.py`、`tools/core_upgrade.py`、`tools/entry_maintenance.py` 和对应测试。嵌入副本由生成器和一致性测试校验，生产交付仍是单文件管理器，无新生产语言依赖。

## 升级、回退和连接影响

完整步骤见 [升级文档](UPGRADE-1.7.md)。配置迁移采用原字节保持，不在升级中修改 Secret/SNI、密钥或模板。旧二进制、配置、服务、WG、状态和必要的旧许可材料在停止前备份；候选失败恢复旧核心。配置有外部改动时拒绝自动回滚，保留人工审查路径。

入口 drain 为运行时状态，不持久化，不可在维护窗口重载入口。一次只升级一个后端。后端的 `--maintenance` 是明确维护确认，不伪装成已自动验证入口和另一台服务器。已有 TCP 会话不能跨后端迁移。

健康代理更新和核心更新分开，避免 `a update` 隐式重启业务。健康代理备份不与核心事务覆盖同一文件；更新健康代理后仍能使用对应核心回退。

## 测试环境及真实结果

WSL Ubuntu **26.04.1**，内核 `6.18.40.1-microsoft-standard-WSL2`，Python 3.14，HAProxy `3.2.9-1ubuntu2.2`；约 7.5GiB WSL 内存。目标生产 OS 检查仍只接受 Ubuntu 22.04，没有将 WSL 版本放宽为生产支持。

两架构 GNU 二进制最高 GLIBC symbol 要求均为 2.34，低于 Ubuntu 22.04 的 2.35；这只是 ABI 静态证据，不替代目标系统完整运行。生产无 Docker，实验也使用已存在的 WSL 工具，无宿主包/内核/SSH/防火墙/全局路由配置更改。

| 验收项 | 结果 | 证据/边界 |
|---|---|---|
| 改动前全部 Python 回归 | PASS，213 项 | `baseline-tests.log` |
| 最终回归、Bash、ShellCheck、嵌入一致性 | PASS，236 项回归 | 包含保留原功能、恶意输入、权限、备份、失败恢复 |
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
| 完整三角色新 OS 安装/重装、系统重启 | NOT RUN（本候选） | 历史 1.6 有证据，本轮 27 场景不能冒充完整安装/重启 |
| ARM64 运行/性能 | NOT RUN | 仅包/二进制 SHA256 与 ELF/GLIBC 静态核对 |
| 新 Ubuntu 22.04 CI 工作流 | NOT RUN / 权限受限 | 已准备 `stable-compat.yml`，不冒充已运行；现有 validate 增加生成器和 root 安全测试 |
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

[最终代码摘要](benchmarks/stable-20261107/delivery-source.json)、[236 项最终回归](benchmarks/stable-20261107/delivery-tests.log)、[最终真实升级验收](benchmarks/stable-20261107/system-upgrade-delivery.json)和[清理核对回执](benchmarks/stable-20261107/cleanup.json)均保留。本轮保留名称的命名空间、实验进程和活动测试单元均无残留；WSL 的临时目录已不存在，回执没有把自然消失的目录伪报为清理脚本实际删除。

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

## 后续放行门槛

先在有写权限的原仓库分支运行 Ubuntu 22.04 的有界新工作流，复核当前固定资产下载；再做候选版本三角色完整安装、重复安装、升级/回退后系统正常重启及持久文件一致性。ARM 单独运行，不用 x86_64 结果代替。

获得明确授权和适当测试节点后，才做真实三机公网路径及 Desktop/Android/iOS 官方客户端的连接、空闲、文件上传/下载、切网和备用恢复测试。分别核对 30/50/100 活跃用户的真实消息/文件、跨区 RTT、CPU/总内存/FD/重传、成功率和恢复分布。未获得授权前继续保持生产隔离，不要求用户为了本地开发立即购买 VPS。

最初使用 `wolfgang008` 推送被 GitHub 403 拒绝。用户指出已有写权限后，确认本机也保存了仓库所有者 `bababoyi6` 的既有登录；仅在当前操作进程内使用该登录，未改变默认账号、未打印或落盘令牌。所有者权限确认可写，审查分支已推送，[PR #9](https://github.com/bababoyi6/me/pull/9) 已创建为草稿。新的 Ubuntu 22.04 工作流因尚未位于默认分支而不能直接 dispatch，补充仅当前开发分支且限代码/测试变动的 push 入口；CI 结果待后续记录。最初的 403 保留为历史证据，当前不再属于写入受阻状态。见 [远程交付记录](benchmarks/stable-20261107/remote-delivery.json)。

生产是否部署、何时发布 Release、何时改变仓库可见性均未由本次开发自动决定。已停止的 24 小时实验和暂停自动任务维持原状。
