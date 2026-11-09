# 旧版对照、完整核心包与升级追溯补验

2026-11-09，北京时间。本轮继续原1.7候选开发；目标正式版仍为3.5.17，所有新Linux执行都在有界GitHub CI内。本地只做Windows文件/Git/结果分析；未访问VPS、未更改可见性、未改写历史、未合并main或发布生产核心。

## 当前结论

**满载高RTT排队也存在于旧balanced2，不能仅凭新优化版相对慢官方版的RTT增加，认定新版特有回归。** 两个独立任务160条32MiB计量流全部通过SHA/ACK，另128条双SNI预热，模拟DC总计288；没有OOM，实验进程/网络区清理完成。但两任务都未满足预登记的全部吞吐/尾延迟/容量/探测规则，不改判、不宣称全面优于旧版。

缩短私有实验路由队列把两核心四流TCP RTT从约220–230ms降到约138–140ms，同时造成单流吞吐下降和ICMP探测丢失。没有采纳为生产默认，更不能当作新核心代码优化或实际ISP队列修复。原官方17单下载约21Mbps的退化结论保持；原生优化候选约106Mbps恢复，但当前不作高延迟生产默认升级推荐。

交付机制有实际新增：固定二进制的完整、确定性审查安装包；包含所有新增源文件的统一补丁；真实安装/重复拒绝和systemd升级、重复/崩溃恢复/精确回滚；审计归档以0600私有留存、回滚仍保留原始许可/源码/二进制。295项root回归通过，无跳过。原生ARM优化候选也已补齐构建及720条协议/资源初验；并不等于ARM生产安装器、官方客户端或长期验收。

## 两个独立任务的条件和数据

任务[42545328188](https://github.com/bababoyi6/me/actions/runs/42545328188)使用3d649a83e83a9e6490a38465151cad5ecf5e68ba；[42546035655](https://github.com/bababoyi6/me/actions/runs/42546035655)使用ae953aa792f76cd57a95d95b8b94541e93b8016e。两者测量/分析/队列工作流字节相同，后者只增加独立打包工具。每任务四轮Williams交叉、32批、80计量流+64预热，真实HAProxy/WireGuard/FakeTLS、分离客户端/DC、合成DC，核心一逻辑CPU，整个实验三CPU/1.5GiB/无swap/900秒。两模型均100ms/200Mbps/MTU1420，deep队列10000包、bounded1500包；实际tc配置已读取核对。

所有表值为同任务四轮中位数，跨机器绝对数字不串接成单个提升。探测丢失是ICMP层观测，不能因文件全通过而写成网络零丢包。

| 任务/机器 | 条件 | 旧 balanced2 Mbps / TCP RTT ms | 原生17 Mbps / TCP RTT ms | 路径探测丢失（旧/新） |
|---|---|---:|---:|---:|
| 42545328188 / AMD EPYC7763 | deep / 1下载 | 104.83 / 113.67 | 105.82 / 115.22 | 0/0 |
| 42545328188 / AMD EPYC7763 | deep / 4下载 | 156.31 / 230.07 | 154.61 / 223.99 | 0/0 |
| 42545328188 / AMD EPYC7763 | bounded / 1下载 | 92.39 / 115.05 | 91.47 / 113.57 | 1/0 |
| 42545328188 / AMD EPYC7763 | bounded / 4下载 | 154.21 / 137.91 | 152.92 / 139.79 | 7/8 |
| 42546035655 / Intel8573C | deep / 1下载 | 105.59 / 120.62 | 105.91 / 124.21 | 0/0 |
| 42546035655 / Intel8573C | deep / 4下载 | 154.78 / 221.58 | 155.82 / 221.51 | 0/0 |
| 42546035655 / Intel8573C | bounded / 1下载 | 90.68 / 114.12 | 93.43 / 112.53 | 0/0 |
| 42546035655 / Intel8573C | bounded / 4下载 | 150.68 / 140.02 | 152.68 / 138.50 | 3/5 |

第一任务deep上行TCP容量对照78.99Mbps低于预登记100Mbps，尽管下载对照176.26Mbps，仍保留保守的`PATH_CONTROL_LIMITED`判定；bounded四流有探测丢失，标记`PROBE_FAILURE`。第二任务全部容量对照176–181Mbps；单下载的新旧保持规则通过，但deep四下载仍有单对尾延迟超过110%规则而需复核，bounded四下载因探测丢失不通过。中位数接近不能覆盖逐对门槛失败。 两次原始分析的legacy_parity_all_cases都为false；工作流绿色仅说明协议、数据完整与分析执行通过。

整个实验峰值分别292.54MiB、291.89MiB。旧/新版本、原始逐批比值、TCP RTT、ping p95、路由积压/丢包及CPU见[本轮证据](benchmarks/native-qualification-20261109/README.md)。事先规则见[对照计划](LEGACY-QUEUE-PARITY-20261109.md)，未修改上轮旧分析的绝对RTT失败规则。

netem的limit包含为了延迟而持有的包，rate也会受定时器粒度影响；缩短模型队列同时改变拥塞/丢包条件，不能将它描述为不改变网络条件的性能优化。[iproute2 netem手册](https://man7.org/linux/man-pages/man8/tc-netem.8.html)。该手册说明是模型解释，本项目对真实公网路径没有相应观测。

## 审查安装包与源码完整性

`tools/stable_core_bundle.py`复用已有`core_bundle.write_bundle`，只接受固定CI构建和精确输入摘要，不执行打包输入、不发布资产。补丁将原native-pair中分别保存的两个新增Linux helper合入统一source.patch；原artifact原本另有helper文件，本轮解决的是单独补丁不自包含的问题。

| 项目 | 固定值/结果 |
|---|---|
| 核心版本/架构 | 3.5.17+tdh.balanced1 / x86_64 GNU Linux |
| 上游commit | 66708d2866f3f3a4541c4fa67984d408e71a8af4 |
| 原始构建 | 42495175073 / dec285fbdf5b42dd8be3e9e879bc0c2ad8994bc8 |
| 二进制SHA256 | cd4c5ff500d956a8a7059e3e536ff139983a0c5897b4c410ea0e53c68a6cfa14 |
| 审查包SHA256 | c36de407f3c4602b629c3331644c6b6aa2d116fd106e9475698ebe671564febb |
| 完整source.patch SHA256 | 87a734aa1cdc409c902544f73403c252beaa6c2f46adcaca7614c485fb00a8d8 |
| 两次独立序列化 | archive字节完全一致；不声称重新编译可逐字节复现 |
| 原始许可/完整补丁 | 固定上游git apply --check通过，安装后原始字节保留 |

实际[初次打包任务42545813783](https://github.com/bababoyi6/me/actions/runs/42545813783)与[升级追溯任务42548406820](https://github.com/bababoyi6/me/actions/runs/42548406820)均通过。后者实际非特权运行版本校验、拒绝重复覆盖、原始旧配置预检、systemd升级/重复、独立连接维护及回退、核心崩溃自动恢复、健康代理摘除/恢复、精确旧核心及配置/令牌恢复；临时单元移除。

`core_upgrade.upgrade(..., audited=True)`/低层CLI的`--audited`是显式审计归档路径，默认False以保持官方升级行为。完整归档在事务目录的`audited-core-<binarySHA>.tar.gz`以0600私有存储，不覆盖运行配置/旧notice；篡改、软/硬链接或8份留档上限均在停服务前拒绝。文件复制再次验证归档摘要，留档在预检失败和回滚后保留，方便审查及离线恢复；相同核心重复升级也会补存或复核留档，保持无需重启的语义；不会自动清空有用资料。已同步单文件内嵌源码并通过一致性检查。

该低层能力不等于`a`的一键优化核心渠道已启用：生产管理器的二进制白名单/下载选择和默认核心没有扩大到实验候选。本包未发布Release，artifact只有7天有效期；后续重建必须重新核对构建source和SHA，禁止自动回退latest。本轮源码交付ZIP只含文本证据，不包含临时优化二进制；确切审查核心包保存在本地证据和CI artifact中。

## ARM、现有架构回归与完整性

原生ARM任务[42546760848](https://github.com/bababoyi6/me/actions/runs/42546760848)在Ubuntu22.04 ARM原生构建同一固定上游及helper，验证ELF AArch64/183；30/50/100并发、上下行256KiB/流，两核心共720条协议传输通过，整个测试CPU100%/768MiB/无swap，无OOM且FD回落。它不覆盖100ms真实WG性能、ARM审计归档安装器或ARM三机部署；具体二进制SHA/资源/忽略项见构建JSON和socket测试摘要。

本次确有生产升级适配器和内嵌代码变更，重新执行了官方17的systemd/前版16回退、30/50/100协议、双WG/抖动/中断恢复（42548403757）、官方ARM（42548403686）、三KVM安装/重复/维护/正常重启（42548403731）。与原生ARM任务分开标注。295项root回归来源42548408275/source7f9e2ca169a3c80ed114b9448b71f55cb2b97d23；合并检查提交与PR head的Git tree已核对相同。原生ARM和上游socket子集不能替代完整上游Rust测试。

一次只因新测试文件名匹配宽路径而重复触发的旧官方兼容任务42545813754已取消，不计作通过。正式适配器修改后的上述必要回归则保留并完成。最后一次三KVM的base客体早期SSH握手有一次connection reset（preparation-failure.json保留），既有180秒就绪等待/重试随后恢复；同任务preparation完整且全套worker、维护及正常重启最终通过。这是临时客体启动就绪事件，不改判成全程首次成功，也不当作生产代理故障。Windows导出工具一度因默认编码无法写入日志BOM，改为显式UTF8后重新导出同一已通过运行；没有因此重跑Linux测试或篡改原结果。

## 实际执行与尚缺项目

实际命令和边界在各run对应提交的工作流与日志中可复核。主要入口为：

```text
gh workflow run queue-study.yml --repo bababoyi6/me --ref dev/telemt-stable-compat-20261107 -f layout=legacy-parity
python3 tests/long_path_lab.py --legacy-queue-study --balanced2-archive "$RUNNER_TEMP/balanced2.tar.gz" --native-directory "$RUNNER_TEMP/native-pair" --output "$RUNNER_TEMP/queue-evidence/metrics.json"
python3 tests/legacy_queue_analysis.py metrics.json analysis.json
python3 tests/stable_bundle_lab.py --directory <fixed-native-pair> --upstream <fixed-upstream> --output <owned-evidence>
python3 tests/stable_system_lab.py --candidate-directory <fixed-native-pair> --candidate-package <audited-archive> --balanced2-archive <fixed-old-archive> --output <owned-system-report>
```

Linux入口始终位于工作流的限时systemd单元/私有netns中，不应删除这些边界后在生产运行。没有启动/恢复旧24小时耐久任务或自动跟进。

尚缺：全部条件下可重复的性能联合保持/收益；最终生产核心及一键发行渠道；官方Desktop/Android/iOS与真实Telegram文件；真实公网跨地区三机；24小时；抗封锁；原生ARM审计安装/完整部署。客户端设备用户暂不提供，VPS未获准，不用CI替代这些证据。当前成果仍适合审查和隔离验收，生产1.6保持原状。
