# Telemt Dual-Hop 1.7.0

**一个固定公网入口、两个独立后端、两条原生 Telegram 代理线路。**
中文菜单引导部署；WireGuard 加密中转；HAProxy 自动主备切换。

[正式版本](https://github.com/bababoyi6/me/releases/tag/v1.7.0) · [安装器检查](https://github.com/bababoyi6/me/actions/workflows/validate.yml) · [更新记录](docs/RELEASE-1.7.0.md)

## 三台机器，四步完成

准备三台干净的 **Ubuntu 22.04 LTS** VPS，各有公网 IPv4；支持 x86_64 / ARM64，建议每台至少 1 vCPU、1GB 内存。需要 root 或 sudo，以及可用的 TCP 443 和 WireGuard UDP 通信。入口和后端不要与其他占用这些端口的服务混装。

在三台机器上分别复制同一条命令。它固定下载 **v1.7.0**，验证 SHA256 和脚本语法，下载失败不执行，退出自动清理临时文件。

<!-- install:stable -->
```bash
bash -c 'set -e; tmp=$(mktemp); trap '\''rm -f -- "$tmp"'\'' EXIT; curl -q -fL --retry 3 --connect-timeout 10 --max-time 120 --max-filesize 1048576 --proto "=https" --proto-redir "=https" --tlsv1.2 https://raw.githubusercontent.com/bababoyi6/me/v1.7.0/telemt-dual-hop.sh -o "$tmp"; test -s "$tmp"; printf "%s  %s\n" "33c90f9289375cbccf9a6480d7026a58b1bef03ba8e50421b2d7c9d919a6e2ef" "$tmp" | sha256sum --check --status; grep -Fqx "# Telemt Dual-Hop - three VPS / two native MTProxy links" "$tmp"; bash -n "$tmp"; if [ "$(id -u)" -eq 0 ]; then bash "$tmp"; else sudo bash "$tmp"; fi'
```

1. **入口机**：选 `1 安装入口 VPS`，确认公网 IP，保存两个加入码。
2. **洛杉矶后端**：选 `2 安装后端 VPS1`，粘贴 VPS1 加入码，复制生成的回执码。
3. **纽约后端**：选 `3 安装后端 VPS2`，粘贴 VPS2 加入码，复制生成的回执码。
4. **回到入口**：输入 `a`，选 `2`，依次录入两个回执；自动启用后选 `4` 查看两条链接及二维码。

加入码、回执、Secret 和链接都是私密材料，不要发到公开截图或 Issue。按提示只复制完整的一行码，不带代码框符号。中途退出可再次输入 `a`；入口菜单 `3` 可查看未用加入码，过期自动刷新，后端菜单 `2` 可重新显示回执。启用失败先运行诊断，修复后入口选 `6` 重试，无需重装。

```text
                         ┌─ WireGuard ─ VPS1 洛杉矶 ─ Telegram DC
Telegram ─ 入口 TCP 443 ──┤
                         └─ WireGuard ─ VPS2 纽约 ─── Telegram DC
```

线路 A：`apple.com`，VPS1 主、VPS2 备；线路 B：`gs.apple.com`，VPS2 主、VPS1 备。两台后端独立服务，流量只经过入口和所选后端。故障自动改派**新连接**；已有 TCP 会话无法跨后端无损迁移，客户端需重连。

## 默认速度与安全策略

- **x86_64 默认保留已发布的 `3.5.5+tdh.balanced2`**，避免官方新版已复现的高 RTT 单流吞吐退化。1.7.0 是管理、安全和恢复能力升级，不宣称比相同 balanced2 核心全面更快。
- **ARM64 默认官方 Telemt 3.5.17**。新版兼容目标与精确版本、提交和双摘要见[版本锁](docs/telemt-stable-lock.json)。ARM 的功能验证不等于与 x86 相同的高 RTT 性能。
- 保留 Direct-DC、原生 Fake TLS、双 SNI、已有 Secret 和链接；默认不启用 WEB、实验核心或实验整形，不增加生产 Docker 依赖。管理 API 只读且仅回环可达，后端监听限私有隧道。
- 低配机器使用保守资源预算。满载仍可能增加消息与新连接延迟；没有证据支持“无限用户”“永久抗封锁”或“1Gbps 跑满”的承诺。

## 已安装：更新、诊断和回退

输入 `a` 打开中文菜单。状态、诊断、链接和维护均可按提示操作；普通更新**不替换核心、不重建隧道、不更改用户链接**。

```bash
a update
a status
a diagnose
```

升级管理脚本前保留现有备份。需要回退时，入口菜单 `10` / 后端菜单 `8` 选择管理脚本回退，或运行 `a update-rollback`；退出菜单后重新输入 `a`。不要拿旧安装命令重装正在运行的节点。

后端核心维护用菜单 `7`。先在入口菜单 `10` 排空对应后端，确认另一台健康，再逐台维护，完成后回入口恢复接收新连接。排空期间不要重载或重启入口，运行时排空状态不会跨入口重载保留。核心重启会断开该后端旧会话。

[完整升级与回退步骤](docs/UPGRADE-1.7.md) · [故障排查](docs/TROUBLESHOOTING-1.7.md) · [架构与健康判定](docs/ARCHITECTURE-1.7.md)

## 实测与边界

复用已完成的真实核心、原生协议、HAProxy 双 SNI、WireGuard、systemd、三 Ubuntu KVM 安装/重复安装/重启、升级回退和 30/50/100 模拟并发证据。详细执行版本与失败记录均保留，不能将不同阶段的结果混称为同一次测试。

[1.7.0 放行依据](docs/RELEASE-ACCEPTANCE-1.7.0.md) · [默认核心与回退实证](docs/CORE-POLICY-20261110.md) · [工程报告](docs/ENGINEERING-REPORT-1.7.md) · [混合负载与排队限制](docs/STABILITY-MIXED-20261109.md)

尚未验证真实 Telegram 官方客户端全平台、公网三机跨运营商、24 小时长期运行及抗封锁效果。本次发行不以这些项目作为完成声明，也不自动访问或部署任何生产节点。实验调度、共享队列方案未达到综合门槛，未纳入默认。

## 历史版本

[1.6.0 balanced2](https://github.com/bababoyi6/me/releases/tag/v1.6.0) 保留作对照和恢复来源。以下 **1.3.1 原始快照**不含后续修复，仅供明确需要历史版本的新环境使用，禁止覆盖当前节点：

<!-- install:original -->
```bash
bash -c 'set -e; tmp=$(mktemp); trap '\''rm -f -- "$tmp"'\'' EXIT; curl -q -fL --retry 3 --connect-timeout 10 --max-time 120 --max-filesize 1048576 --proto "=https" --proto-redir "=https" --tlsv1.2 https://raw.githubusercontent.com/bababoyi6/me/v1.3.1-original/telemt-dual-hop.sh -o "$tmp"; test -s "$tmp"; printf "%s  %s\n" "07642df50587cbad056865445bfe4d38802322b2eb02d564234d1b4252a528dc" "$tmp" | sha256sum --check --status; grep -Fqx "# Telemt Dual-Hop - three VPS / two native MTProxy links" "$tmp"; bash -n "$tmp"; if [ "$(id -u)" -eq 0 ]; then bash "$tmp"; else sudo bash "$tmp"; fi'
```

[原始脚本及来源](legacy/README.md) · [上游 Telemt](https://github.com/telemt/telemt)
