# 3.5.17 正式版本兼容审计与后续实验

2026 年 11 月 8 日继续任务时重新查询 GitHub Releases API，过滤 draft/prerelease，发现当日 **12:08:51 UTC** 发布的正式 `3.5.17`。原 `3.5.16` 仍作为历史基线保留，未覆盖它的测试结果或正式资产。本分支尚未发布、合并主分支或部署 VPS。

## 固定版本与变更范围

- Release tag `3.5.17`；annotated tag 对象 `881210fd4c320735fad3978a291b565afae4b61e`；commit `66708d2866f3f3a4541c4fa67984d408e71a8af4`。
- amd64 GNU 包 SHA256 `0570762ddcd79530d666b09e955b955bb52c9e134cc8b8ed1a0f50ad28d58a0e`，二进制 `e8ff277bbb8ec4676d6264e593fe2a0d255facec8f1cb305e357a3859198ffd4`。
- ARM64 GNU 包 SHA256 `f0d3d06d029f2b21dc66a44069bcc7bd20d32fcb34b5867c5250cee647785de6`，二进制 `e5ca8befdc1428fcd66537ef04f3a95e5f278ebf3c3dd0d0909ce25a390f502b`。

GitHub 资产 digest 与 CI 实际读取归档的 SHA 一致，解包后检查 ELF 架构并计算上述二进制摘要。固定资产 ID、包大小与 URL 见 [版本锁](telemt-stable-lock.json)；API 原文、tag 和前版锁在 [原始证据](benchmarks/upstream-3517/)。没有使用 main 或未核实 latest 下载。

3.5.16→3.5.17 共 86 个文件变动，主要是 API 能力/配置 schema 发现、dry-run 预检、desired/effective 状态、DNS override、来源拒绝策略和 API 可观察性；同时调整配置快照 epoch、watcher 准备/观察与跨代缓存。Cargo 新增 `toml_edit`。本项目使用的配置字段没有发现必须改名或迁移的项，新增 source-deny 默认不启用，旧连接预算和 API read-only 继续显式配置。

整个 `src/transport` 差异为空，`src/transport/socket.rs` 两版 Git 对象 SHA256 相同；没有移除固定 256KiB 缓冲。因此“升级 API 正式版”不等于解决此前高延迟吞吐与排队问题。本轮新正式二进制已再现100ms单流下载退化，见[最终结果](FINAL-ACCEPTANCE-20261109.md)；旧版吞吐结果仍独立保留。

审查重点是本项目用到的 API 认证/只读、配置读取、watcher、runtime 投影、就绪契约及对旧模板的影响；不声称已逐行审完全部新增 API 或完整上游源码。项目不接入新增 API 写操作，不启用 WEB、ME、DNS override 或 source-deny 管理功能；管理监听仍限回环。

## 兼容实现

管理器固定新包；保留两架构 3.5.16 的精确二进制识别，在执行版本查询前仍核对属主、路径权限与摘要。3.5.16 的同 schema 5 / 节点协议 1.2.1 加入码可继续使用，后端安装新的锁定版本；不把任意旧版本号纳入白名单。1.6 集群按原流程先更新管理脚本再生成加入码，Secret/SNI 与用户代理链接不变。

核心升级继续先保存旧二进制、配置、密钥和服务单元，执行旧配置预检与 MainPID 核对，使用既有原子事务。3.5.17 核心替换不自动重写配置；显式连接参数维护和回退仍为独立事务，维持 0640、UID/GID 与受保护文件漂移拒绝。没有扩大公网 API，也没有把 WEB/HTTP Listener 当成原生 PROXY v2 使用。

历史测试程序保留 `3.5.16` 固定默认；当前工作流显式传入 `--stable-release 3.5.17`。这样旧 3.5.16 的长链路/配对配方仍能复现，不会因名为 stable 的变量被静默换版。原生实验重建到 `3.5.17+tdh.control1` / `3.5.17+tdh.balanced1`，与官方版本明确区分，不自动接入安装器。

## 已执行的预检和兼容验收

| 任务 | 实际范围与结果 |
|---|---|
| 42494335986 | 正式包双架构摘要/ELF；amd64 真实 Fake TLS 双 SNI 上下行；ready envelope；新 config/state、capabilities、schema 鉴权；只读写拒绝；配置字节保持，PASS |
| 42494911735 | 新官方 ARM64 原生安装、API、双链接及协议并发，PASS |
| 42494911723 | 实际 systemd 新核心安装/升级/回退、健康代理、30/50/100 负载、双 WG 和故障恢复，PASS |
| 42494738574 | 三 KVM 从当前候选安装、重复安装、原生命令维护/回退、配置漂移拒绝、三次正常重启，PASS |

预检失败也保留：`42493889571` 读取新 schema 时被旧测试工具的 256KiB 上限截断；改为仅该探测接口的 2MiB 有界读取，实际 schema 272,500 字节。`42494072895` 的 reload 权限检查路径写错，得到 404；官方为 `/v1/system/reload`，更正后返回 403。一次错误路径的重复任务已请求取消。它们是测试夹具问题，不当作核心故障或权限绕过。

原生17构建、实际新包长链路、前版3.5.16精确回退、短时资源及排队/同负载/30–100并发补验均已完成，284项root回归无跳过。三种简单策略无联合收益、不采纳；720条4MiB流全部成功但不作满载延迟已修复结论。逐批结果与失败修正见[11月9日验收报告](FINAL-ACCEPTANCE-20261109.md)，事先门槛见[实验计划](QUEUE-STUDY-20261108.md)。

官方客户端、真实 Telegram 文件、物理公网三机、24 小时和抗封锁仍未验证。没有访问生产或过期 VPS；本地只做 Windows 文件/Git/数据分析，所有 Linux 执行在有界 GitHub CI 内。
