# 1.5.0 新安装交付与验证

2026-10-23。本版重点是首次一键部署，不再新增无损核心升级机制，没有访问生产三台VPS。本文保留发布前的验收条件与原始失败记录，公开版本的变化和限制见[1.5.0版本说明](RELEASE-1.5.0.md)。

## 新安装实际改变了什么

x86_64后端首次安装默认使用已测的`3.5.5+tdh.audit2`，不需要事后手动开启性能开关。核心修复转发在等待新输入时未及时flush的一类尾部停滞，取消client及Direct outgoing socket固定256KiB收发请求，使Linux默认TCP自动缓冲能够生效。未改密码算法、认证、线上协议、访问控制或用户态复制预算。内核socket内存可能增加，不能以RSS近似不变推断内存成本相同。

入口与后端配置生成仍通过原有模板摘要对照：保留双SNI、WireGuard及PSK、Direct-DC、fast_mode、主备、五DC健康检查与现有服务权限。BBR/fq默认值沿用原方案，不盲目更换内核、全局放大缓冲或调整NIC offload。ARM保持官方3.5.5，本轮未构建ARM提速核心。

[单文件安装器](../telemt-dual-hop.sh)内嵌[已测试的安全归档安装逻辑](../tools/core_install.py)，由[同步工具](../tools/embed_core_install.py)和回归核对一致，不在用户机器下载额外Python模块或现场编译。先验证固定归档摘要，拒绝链接、目录穿越、重复/多余成员和超限数据，再验证逐文件清单、架构、核心摘要及非官方版本；以telemt账号执行版本检查后，才排他安装核心。原许可、源码补丁及构建记录保存在核心目录中的`core-source-notice/`。

新安装还会在软件包、加入码提示和事务写入之前拒绝已有同名目录、核心、管理入口、WireGuard配置及项目服务，防止失败清理误删预先存在的内容。入口不再创建无用的后端工作目录。一键命令拒绝空下载、错误项目和无效Bash，并忽略用户curl配置、限制协议/时间/已知文件大小，退出时删除独立临时文件。

## 同一条命令的三节点实际验证

在获授权的荷兰1核1GB临时宿主中，使用三套Ubuntu22.04/systemd测试系统，逻辑资源分别为1核1GB、1核2GB和1核1GB。清除这些可丢弃guest的旧项目状态后，三台均执行[README中的同一条命令](../README.md)，通过中文菜单选择角色，真实完成APT调用、配置生成、核心运行、加入码/回执配对与服务启动。

系统软件包和测试用generic内核此前已经准备，**不是三个全新出厂镜像的重复验收**。三guest共用一台宿主，QEMU组物理内存上限512MiB、CPU为宿主一核的80%，所以本组只验证功能、权限、持久性及错误处理，不证明生产三台独立VPS的容量。

验收当时尚未发布GitHub，仅候选脚本及配套核心下载在guest内使用curl边界提供本地文件；命令本身与README一致，其余安装、文件与服务操作真实执行。发布后的公开资产另行核对下载摘要，不能把这组验收当作当时已从GitHub下载候选版本。

| 项目 | 结果和证据 |
|---|---|
| 入口新安装 | [同一命令完成](benchmarks/vm-fresh-150-entry.json) |
| 两后端默认核心 | [后端1](benchmarks/vm-fresh-150-backend1.json)、[后端2](benchmarks/vm-fresh-150-backend2.json)都验证audit2摘要、非特权运行和许可保留 |
| 预先存在的目录 | [实际拒绝测试](benchmarks/vm-fresh-150-existing-path.json)在加入码提示前拒绝，原测试文件内容不变 |
| 损坏核心归档 | [拒绝记录](benchmarks/vm-fresh-150-rejected-archive.json)确认摘要失败，项目状态、核心、WG配置和快捷入口均未遗留，随后正常重试安装成功 |
| 健康及真实握手 | [验收汇总](benchmarks/vm-fresh-150-acceptance.json)三节点诊断及只读采样通过；两个后端均显示audit2 |
| 主备 | [真实DC及切换记录](benchmarks/vm-fresh-150-e2e.json)覆盖双SNI/分片Hello、真实resPQ nonce、实际停主后端后的新连接切备及恢复 |
| 正常关机再启动 | [重启记录](benchmarks/vm-fresh-150-reboot.json)三节点自动恢复；13/16/16份配置、密钥、核心及服务文件内容不变，重启后双SNI真实握手通过 |

以上停止主后端会中断其已有连接，不宣称TCP会话跨后端迁移；本组也没有Telegram用户账户或实际文件测速。

第一次准备阶段的150秒SSH期限早于guest约219秒完成启动，未执行新安装，失败保留于[初次就绪记录](benchmarks/vm-fresh-150-initial-readiness.json)。宿主没有OOM或磁盘保护触发，未扩大资源上限；待系统就绪后完成身份验证和上述安装。不能把这一实验启动超时删掉或算成安装通过。

## 最终回归与高速、并发对照

本机Linux及原生Ubuntu22.04均通过129项Python回归、Bash语法、ShellCheck及内置自测；原生另通过真实HAProxy的SNI、分片Hello、PROXYv2、完整字节校验及agent切换检查。27个安装恢复场景在隔离文件系统/网络中通过，宿主账号文件不变。[最终日志](benchmarks/native-fresh-150-checks.txt)、[检查摘要](benchmarks/fresh-150-checks-acceptance.json)和[27个恢复场景](benchmarks/native-fresh-150-install-recovery.json)记录确切范围。测试通过不能等同零bug或真实性能提升。

在guest全部停止后，原生1核1GB测试机另模拟DC段100ms RTT、每方向1000Mbps链路预算。大流实际走HAProxy→Telemt→模拟DC的原始TCP路径，**没有WG，不是公网带宽上限或Telegram文件测速**。每项单连接64MiB、双向各3轮，交替方向；两组都包含flush修复，只比较固定缓冲audit1与自动缓冲audit2。

| 单连接64MiB | 固定缓冲中位数 | 自动缓冲中位数 |
|---|---:|---:|
| 下载 | 16.146Mbps | 156.682Mbps |
| 上传 | 23.044Mbps | 169.434Mbps |

12次大流及穿插的小文件探测全部校验通过，OOM增量为0。该条件显示固定窗口限制可以造成明显吞吐损失；CPU未满载，结果也未接近1000Mbps预算，不能据此认定代理已达到物理上限。[固定缓冲逐轮记录](benchmarks/native-1g-long-flush.json)、[自动缓冲记录](benchmarks/native-1g-long-auto.json)和[DC独立计数](benchmarks/native-1g-long-dc.json)均保留。

另测32并发、每流8MiB、每方向合计256MiB，批量直接进入Telemt并携带PROXYv2，排除HAProxy吸收背压；客户端请求32KiB接收缓冲，下载暂停读取5秒，DC使用ACK保持连接。小文件仍经HAProxy。初次auto先运行的一轮，模拟DC的8秒单次socket期限导致4条下载在payload阶段TimeoutError，客户端只收到部分数据；28条校验成功，**本轮失败，不纳入成功吞吐统计**。见[原失败记录](benchmarks/native-1g-backpressure-initial-auto.json)和[DC四个超时](benchmarks/native-1g-backpressure-initial-dc.json)。

只将**模拟DC**期限由8秒改为25秒，同一核心、网络、数据及暂停条件下，auto→flush两组128条大流全部校验通过；保留客户端25秒、单项65秒和worker210秒等期限，未改生产核心或服务的超时。随后交换为flush→auto，各方向3轮，384条大流和穿插探测全部通过、无OOM，但结果证实**并发吞吐并非普遍提升**：

| 32并发三轮中位数 | 固定缓冲 | 自动缓冲 |
|---|---:|---:|
| 暂停读取后的下载 | 124.448Mbps | 118.762Mbps |
| 上传 | 375.806Mbps | 273.477Mbps |

上表下载耗时含5秒人为停读，不是恢复后的稳态传输速率。上传回退约27%，不能删去或用单连接收益掩盖。[固定缓冲复验](benchmarks/native-1g-backpressure-three-rounds-flush.json)、[自动缓冲复验](benchmarks/native-1g-backpressure-three-rounds-auto.json)及[DC计数](benchmarks/native-1g-backpressure-three-rounds-dc.json)提供全部逐轮数据；三轮不足以估计稳定P95。

两次补充观察继续校验成功。第二次观察中，auto上传TCP重传段增量2392，固定缓冲135；中间netem及两端fq都没有记录丢包。这些TCP计数含实验client命名空间的多条连接，**不能定位重传成因，也不能等同美区公网丢包**。两端veth为按包模拟而关闭GRO/GSO/TSO，整个生成器、DC和代理共享一核，不能据此推定生产会出现相同回退。原记录见[auto观察](benchmarks/native-1g-backpressure-endpoints-auto.json)和[固定缓冲观察](benchmarks/native-1g-backpressure-endpoints-flush.json)。未据此盲目修改生产队列、offload或添加自适应切换逻辑。

默认选择audit2的依据是：修复尾部推进、解除已证实的高RTT单连接窗口瓶颈，保留Linux自动缓冲和安全边界；同时接受并明确披露其内核内存成本与并发场景取舍。它符合大文件单连接的优化方向，**不证明全部负载都更快，也不承诺三台1Gbps跑满**。汇总和确切产物绑定见[数值汇总](benchmarks/native-1g-summary.json)及[1.5.0验收清单](benchmarks/fresh-install-150-manifest.json)。若实际生产以持续大量并发上传为主，仍需真实客户端对照后判断策略。

资源检测、连接上限及Direct用户态缓冲预算沿用原方案，没有因测速提高。连接接入上限不等于同等数量的大流容量；本轮最多32个同时活跃的大流，未证明1GB后端可以承受上千条持续满速连接。此前10分钟持续传输和内核socket内存观察见[核心资源验收](CORE-ENABLEMENT-VALIDATION.md)，同样不能替代数天的实际生产负载。

[最终实验清理记录](benchmarks/fresh-install-150-final-inventory.json)确认本次进程、监听、namespace、临时隧道私钥及VM资源组均无残留，宿主OOM计数为0。三份测试磁盘及guest专用SSH密钥保留在0700目录，密钥权限0600，不包含在交付包中。一次25秒对照的启动曾因未选择私有wg工具路径而在负载前失败，随后使用既有受限运行器修正；不计为性能样本。

## 一键安装和已有节点的边界

三台VPS分别执行同一命令、选择角色并交换加入码/回执，不要求手写代理配置。安装完成后无需运行额外提速命令。缺少内核BBR/fq支持、端口占用、已有项目文件、伪装检查失败或产物校验失败时会安全停止，不能承诺任意系统状态都强行安装。

已有节点的`a update`及重复安装命令只维护管理器，不替换现有核心、不重新生成链接，也不新增集群升级守护程序或管理API。本轮提速核心默认启用针对新部署。

## 发布顺序

核心二进制摘要为`2e5f864461be842dbca2ee8638f3b68327f56d5f1666ea0f2622a491b2ba75d1`，所用归档摘要为`c205183e479716f015378e4e2241103ae8d488e06f6ef65946be0bd94624f356`。保持已测归档原始字节；不同压缩运行时重打包可能产生不同摘要，不能随意替换。

发布顺序为先发布配套核心资产并验证下载摘要，再更新main上的脚本及README。只发脚本会导致新安装找不到核心并停止。原始验收清单中的`github_written: false`描述验收完成时的状态，保留不改；公开发布的标签、摘要和验证记录另见版本说明。

默认核心的受控收益与内存证据见[后端原生对照](NATIVE-BACKEND-VALIDATION.md)和[核心资源验收](CORE-ENABLEMENT-VALIDATION.md)。100ms/100Mbps完整WG合成单流下载曾为22.745→55.915Mbps，条件与样本均有记录；不能承诺生产或实际Telegram同等提升。洛杉矶/纽约路径、真实客户端文件和长期生产负载仍未实测，不以“零bug、永久无需维护”代替这些事实。
