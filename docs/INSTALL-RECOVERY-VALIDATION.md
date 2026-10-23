# 安装、升级与恢复验证

**历史记录：** 下文是1.4.2阶段。最新1.5.0新安装默认启用x86_64 audit2，回归和实际安装结果见[新安装验收](FRESH-INSTALL-1.5.0.md)；不再新增无损升级机制。

2026-10-23。本地候选1.4.2，未提交、未推送，没有访问生产三机。默认核心仍是官方Telemt3.5.5；实验提速核心未进入安装或a update。

## 当前验收范围

| 检查 | 结果与边界 |
|---|---|
| 最新管理器及核心工具 | 本地Linux及原生Ubuntu22.04通过111项Python回归；原生另通过Bash语法、ShellCheck、自检和真实HAProxy行为检查；最新完整记录见核心验收清单 |
| 安装中断恢复 | 最新源码通过27个隔离文件系统场景；宿主账号文件不变，服务等操作为测试边界 |
| 三套Ubuntu/systemd | 完成节点安装、双WG、真实健康代理、四条HA逻辑路径及真实Telegram DC公开握手 |
| GitHub一键命令 | 第二后端实际从main取得1.4.1并安装；磁盘事件后的重建另有披露 |
| 运行中管理器更新 | 三角色更新及重复安装保持配置、密钥、链接和服务进程身份；入口及后端2另完成管理器回退 |
| 实际文件和容量 | 未测真实Telegram文件、生产路径或三台独立VPS的1Gbps容量 |
| 核心轮换与回退 | 本轮已在临时后端实际启用候选、双SNI真实DC握手、显式回退和健康拒绝恢复；未接入生产更新 |

回退加固快照摘要为`49b930433d2df3d2c18ca477f90dcc1e1439812f6b7a8c7bce0d8eb951642426`，对应[当时88项原生日志](benchmarks/native-rollback-final-checks.txt)、[27场景逐项结果](benchmarks/native-rollback-recovery.json)和[回退修复清单](benchmarks/rollback-hardening-manifest.json)。本轮管理器仅进一步保留脱敏版本中的非官方构建标识，避免把实验核心显示为官方3.5.5；另新增核心事务和打包工具，完整范围见[核心验收](CORE-ENABLEMENT-VALIDATION.md)。旧88项记录不冒称涵盖新增工具。

三虚拟机完整功能验收使用此前的`5d877748810c02dc7090eb5d3434789b4963633b5bdf9824d2bffc6cc8ebfff2`快照，见[虚拟机清单](benchmarks/vm-acceptance-manifest.json)。随后只修改管理器回退的异常目标检查及失败清理，增加3项回归，没有改传输配置模板。不能将旧快照的三机验收称为在最新任意源码上重跑。此前64项、85项日志作为历史证据保留，不相加扩大数量。

## 已发现并修复的问题

1. 安装事务原先缺少显式退出和信号的统一清理。新增EXIT及HUP/INT/TERM处理，以BASHPID确定归属，成功管道或子进程不会清理父事务，子进程失败由父进程回滚一次。
2. 菜单终止时转发中止请求并等待安装清理。继承的环境变量不能指定无关PID；清理开始后忽略重复中断，避免断线再次截断清理。SIGKILL、断电和存储损坏不在这一保证内。
3. 实际APT更新遇到镜像同步窗口：预期3908872字节、收到3908884字节。增加连接/数据期限、下载重试和有限索引重新获取，部分索引失败也返回错误。保留签名、摘要、有效期验证，不重复失败的dpkg配置阶段，不强制解锁。
4. 官方最小镜像的裁剪内核缺少fq/BBR。检查现已前移到写入业务配置之前；已有modprobe时在完整依赖安装前检查。生产安装器不更换内核，不再将官方裁剪内核误称为非官方。
5. 管理器回退原先会把备份移动到异常目录内并报告成功，也会替换符号链接路径。现与更新路径一样拒绝异常类型，使用同目录原子替换；写入或重命名失败时保留当前管理器及备份，清理临时文件。新增测试先在旧快照复现4个失败断言，修复后13项管理器生命周期测试全部通过。

APT选项依据Ubuntu22.04的[apt-get手册](https://manpages.ubuntu.com/manpages/jammy/man8/apt-get.8.html)和[apt.conf手册](https://manpages.ubuntu.com/manpages/jammy/man5/apt.conf.5.html)。无效签名、缺少可信公钥或持续校验失败仍停止。

## 自动恢复测试

`tests/install_recovery_lab.py`执行3角色×9模式：显式失败退出、命令失败、TERM、INT、HUP、清理途中HUP、未提交却正常退出、已提交保留，以及无EXIT处理的对照。在独立mount及网络namespace中，将/etc、/usr/local、/var/lib和/run映射到私有空目录，以生产绝对路径检查文件删除。服务、防火墙、调优和账号操作为记录边界，宿主systemd/DBus不可达，无关文件保留。

以下仅供可丢弃的root Linux测试机使用，不是a菜单或生产更新步骤：

```bash
sudo install -d -m 0700 /tmp/tdh-audit-recovery
sudo timeout 60 python3 tests/install_recovery_lab.py --root /tmp/tdh-audit-recovery
```

Actions已增加相同检查；未推送，因此新增流程尚未在GitHub运行。

## 完整系统功能验证

临时荷兰1核1GB测试机提供嵌套KVM。使用[Ubuntu官方22.04最小镜像](https://cloud-images.ubuntu.com/minimal/releases/jammy/release/)，验证Ubuntu云镜像签名及SHA256。QEMU及依赖只解压在root私有目录，没有安装到宿主包数据库。

三个节点分别为1/2/1GB逻辑内存、各1个vCPU，使用文件支持的共享guest RAM。QEMU组物理内存上限512MiB、CPU配额为宿主一核的80%；管理端口只绑定回环，独立虚拟二层网络承载合成地址及WG。**这是功能和权限验证，不能作为三台独立VPS的容量或吞吐验收。** 目标三机的标称1Gbps未在此验证。

最小镜像原内核5.15.0-1109-kvm缺少fq/BBR。仅在实验guest准备官方通用内核5.15.0-198-generic，宿主和生产安装器未更换内核。入口完成实际APT、TLS检查、加入码和a入口初始化；后端运行真实WG、Telemt和健康代理，使用telemt账号，ProtectSystem=strict、ProtectHome=yes、NoNewPrivileges=yes，API ready、agent up，默认CC为bbr。

后端1可见内存约1962MiB，生成3924连接上限和490MiB用户态Direct预算；后端2为1912连接和239MiB预算。这是配置及启动验证，不能当作数千活跃大流容量。见[后端1安装](benchmarks/vm-backend1-install.json)、[后端2安装](benchmarks/vm-backend2-install.json)及[更新后服务快照](benchmarks/vm-three-node-updated-snapshots.json)。

[完整链路记录](benchmarks/vm-three-node-e2e.json)覆盖双SNI、分片Hello、FakeTLS认证和真实DC的resPQ nonce核验。实际停止后端1的Telemt后，两条新连接均分配到后端2；恢复后回到原主备选择。使用实际安装的健康代理及五DC检查，没有换成合成agent。只做公开握手，不创建用户会话，没有已有TCP会话迁移测试。

## 运行中升级和回退

| 节点 | 检查 | 不变记录 |
|---|---|---|
| 入口 | 更新、重复安装、管理器回退、再次更新 | 19条文件及服务身份记录 |
| 后端1 | 相同内容更新、重复安装；已有相同候选，不凭空创建历史备份 | 17条记录 |
| 后端2 | 从真实发布1.4.1更新、重复安装、管理器回退、再次更新 | 17条记录 |

见[运行中更新记录](benchmarks/vm-three-node-updates.json)。为遵守不发布要求，仅管理器下载URL由guest内临时curl边界提供本地候选；安装器、管理命令、文件替换和服务均真实执行。不能称候选已发布到GitHub，也不能称已通过a update升级核心。旧1.3.1首次更新边界仍见[发布验收状态](RELEASE-READINESS.md)。

实际安装的a perf均成功执行：[入口](benchmarks/vm-entry-perf.json)、[后端1](benchmarks/vm-backend1-perf.json)、[后端2](benchmarks/vm-backend2-perf.json)。这些空闲功能采样不能当作生产负载基线。

## 实验失败及恢复边界

测试控制器曾遗留SSH会话，使下一次操作被真实管理锁拒绝。修复会话清理后继续，没有绕过锁，也没有归因于代理。

三个guest共用15GB宿主磁盘时写满。部分测试工具成为空文件，单看退出码会误判；后端2的WG私钥、WG配置及两份服务unit随后读到0字节，与安装完成时私有摘要不符。入口健康门控正确拒绝启用。见[磁盘事件](benchmarks/vm-disk-incident.json)、[初次安装](benchmarks/vm-backend2-initial-install.json)和[初次服务快照](benchmarks/vm-backend2-initial-snapshot.json)。

停止guest并释放RAM文件，仅清理guest的APT下载缓存，以discard回收空间；控制器增加启动余量检查及低空间停机保护，工具包经SHA256校验后重传。旧WG私钥无法恢复，因此仅对可丢弃后端2重建部署和登记，不能将夹具修复当作原WG身份无损升级。重建后正常关机重启，7份受保护文件摘要一致，见[重启验证](benchmarks/vm-backend2-reboot.json)。后续升级验收从该健康基线开始。

“在测试后端替换实验核心并注入启动失败”的早期步骤被自动审批拒绝，工具仅返回blocked by policy，没有更具体原因，未执行。此后以固定程序、明确guest身份及健康判定拒绝的范围申请执行并获准；已完成真实服务启用和回退，见[核心验收](CORE-ENABLEMENT-VALIDATION.md)。新测试不含损坏二进制或实际进程崩溃，不能扩大为这些失败注入已通过。

## 实验收尾和剩余条件

[收尾清点](benchmarks/vm-final-inventory.json)确认三台guest、测试服务器及端口停止，专用cgroup移除，RAM文件为0，OOM增量为0。三份磁盘和实验SSH密钥仍留在0700的root私有目录，密钥0600，没有宣称全部实验资料已删除。宿主软件包和服务未因该VM实验改动。

真实Telegram文件、洛杉矶/纽约公网路径、更长时间多用户与内核socket内存压力、ARM发行兼容，以及可校验的核心发行与恢复流程仍未完成。受控高RTT收益见[后端原生报告](NATIVE-BACKEND-VALIDATION.md)，不能代替这些放行条件。
