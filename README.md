# Telemt Dual-Hop 一键安装

最新实验候选见[原生背压候选与逐轮结果](docs/UPLOAD-CANDIDATES-20261024.md)：balanced2在明确采用80%软节流线的32×8MiB两轮对照中达到约定门槛，速度为旧固定核心的104.32%/95.016%，128条上传全部校验成功。第二轮贴近边界，仍为测试候选；默认安装核心保持audit2，未发布正式版本或宣称完整生产验收。前阶段交付见[2026-10-24候选报告](docs/NEXT-CAUSAL-20261024.md)。

合并前补充修复与边界见[合并复核](docs/MERGE-REVIEW-20261024.md)。

> 本轮优化基于 1.5.0，新增健康状态过期保护、恢复滞回、更新下载保护及有资源上限的单 VPS 实验室；没有新的已验证吞吐补丁。合并 main 不代表完整生产验收或正式 Release。下方安装命令下载 main 的最新脚本。结果与未完成项见 [本轮验收](docs/NEXT-VALIDATION.md)，代码审计见 [后续审计](docs/NEXT-AUDIT.md)，复验方法见 [单 VPS 实验室](docs/SINGLE-VPS-LAB.md)。

**1.5.0** 面向首次一键部署。Ubuntu 22.04 x86_64 新后端默认安装已测的非官方核心 `3.5.5+tdh.audit2`，修复一类转发尾部停滞，并解除固定 TCP 缓冲对高延迟单连接的限制；保留双 SNI、WireGuard 加密、Direct-DC、BBR/fq 和双后端主备。ARM 继续使用官方核心，不宣称获得相同提速。

```bash
bash -c 'set -e; tmp=$(mktemp); trap '\''rm -f -- "$tmp"'\'' EXIT; curl -q -fL --retry 3 --connect-timeout 10 --max-time 120 --max-filesize 1048576 --proto "=https" --tlsv1.2 https://raw.githubusercontent.com/bababoyi6/me/main/telemt-dual-hop.sh -o "$tmp"; test -s "$tmp"; grep -Fqx "# Telemt Dual-Hop - three VPS / two native MTProxy links" "$tmp"; bash -n "$tmp"; if [ "$(id -u)" -eq 0 ]; then bash "$tmp"; else sudo bash "$tmp"; fi'
```

三台 VPS 分别执行同一条命令，在中文菜单选择角色：先初始化入口，再把入口给出的加入码分别交给两个后端，最后将后端回执录入入口。无需手动编写 HAProxy、WireGuard 或 Telemt 配置。

安装器会检查归档与核心摘要、架构、构建标识及运行结果，x86_64 提速核心的原许可和源码变更说明随安装保留；校验或下载失败会停止安装并执行安装事务清理，不悄悄切换其他核心。不会在 1GB 后端现场编译，也不安装新的常驻升级服务。

本轮新安装、损坏归档拒绝、主备及重启的具体范围见[1.5.0 新安装验收](docs/FRESH-INSTALL-1.5.0.md)。

# 做了什么，实际快了多少

突破来自后端源码修复：补齐等待新输入时的加密写入 flush；取消 client 和 Direct outgoing socket 的固定256KiB收发请求，让Linux自动调节TCP缓冲。没有关闭加密、减少认证或扩大用户态复制预算，也没有通过换入口、端口或绕过WireGuard换取数字。

下表是同一台荷兰1核1GB测试机的**合成路径实测**，不是Telegram文件或美国三台VPS的测速。不同测试条件不可互相比大小。

| 测试条件 | 对照 | 新核心 | 结论 |
|---|---:|---:|---|
| 完整WG+PSK双后端路径，100ms/100Mbps，单流16MiB下载，3轮中位数 | 官方3.5.5：22.745Mbps | 55.915Mbps | 此条件下约2.46倍 |
| 原始DC TCP段，100ms/1000Mbps预算，单流64MiB下载，3轮中位数 | 已修flush、仍固定缓冲：16.146Mbps | 156.682Mbps | 解除窗口限制有明显收益 |
| 同上，单流64MiB上传 | 已修flush、仍固定缓冲：23.044Mbps | 169.434Mbps | 不是未经修改原版的对照 |
| 100ms/1000Mbps预算，32并发、每流8MiB上传，交换版本顺序后3轮中位数 | 已修flush、仍固定缓冲：375.806Mbps | 273.477Mbps | **约27%回退，非所有负载都更快** |

自动缓冲可能增加内核socket内存。一次慢读测试出现4条流因模拟DC的8秒发送期限而结束；原失败记录保留，25秒模拟DC期限下的多轮对照和后续观察共768条大流均校验通过，没有OOM。没有修改生产服务超时。并发回退的完整成因尚未确定，不把重传计数当作公网线路丢包。

三套Ubuntu22.04测试节点完成同一安装命令、主备及重启验收；129项回归、27个隔离安装恢复场景通过。三节点共用测试宿主，验证功能而非三台物理VPS容量；没有真实Telegram文件、生产线路或长期满载数据，不能承诺1Gbps跑满、所有场景大幅提速或零bug。详见[版本说明与逐项证据](docs/RELEASE-1.5.0.md)及[完整验收](docs/FRESH-INSTALL-1.5.0.md)。

# 已有节点

本轮不再新增无损核心升级机制。`a update` 仍只更新管理器；重复执行安装命令也不会重建已有节点、替换运行核心或重新生成连接信息。1.5.0 的默认提速核心适用于新部署。

常用命令：`a` 打开中文菜单，`a status` 查看状态，`a links` 显示代理链接/二维码，`a diagnose` 检查异常，`a update` 更新管理器，`a update-rollback` 回退上次管理器备份。管理器回退不等于核心回退；不要通过重复安装尝试升级或迁移运行中的核心。代理链接包含访问凭据，分享范围越大，滥用与固定入口风险越高。

# 可选性能诊断

安装后如需定位实际瓶颈，可在三台分别执行 `a perf 5`。只读采样5秒并输出脱敏统计，不启动测速或上传；此命令不是安装必做步骤。

完整分析、受控测速和回退边界见 [性能审计手册](docs/PERFORMANCE.md)，测试机实测与逐轮数据见 [VPS 验证报告](docs/VPS-VALIDATION.md)。

目标三机配置、历史安装/更新验收范围见[发布验收状态](docs/RELEASE-READINESS.md)。

10月22日的[后端核心对照验证](docs/NATIVE-BACKEND-VALIDATION.md)已在临时VPS的100ms合成路径复现固定TCP缓冲限制及一类上传尾部停滞，并完成独立编译修复。它显示了有条件的大幅吞吐改善，不能当作真实Telegram提速承诺。

10月23日的[实验核心真实服务启用与回退](docs/CORE-ENABLEMENT-VALIDATION.md)保留早期内部验收，包括双SNI到真实Telegram DC的公开握手、健康判定拒绝后的实际恢复及离线产物准备。该文中的未发布状态是历史记录；1.5.0新安装默认核心及其适用边界以本页和新安装验收为准。
