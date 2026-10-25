# 原始稳定版 1.3.1（历史快照）

这里保留开始性能优化前的脚本，管理器版本 **1.3.1**，后端使用 **官方 Telemt 3.5.5**。不包含 audit1、audit2、balanced2 或任何本项目的核心补丁。

- 来源提交：[`452c50d268c8a95d68bdda0d27878f4e9ddcefb2`](https://github.com/bababoyi6/me/commit/452c50d268c8a95d68bdda0d27878f4e9ddcefb2)，首次性能审计改动前的版本。
- 冻结脚本：[telemt-dual-hop-1.3.1.sh](telemt-dual-hop-1.3.1.sh)，94,637 字节，与来源提交逐字节相同，没有回填后续修改。
- 独立版本：[v1.3.1-original](https://github.com/bababoyi6/me/releases/tag/v1.3.1-original)，不替代优化版的 Latest 标记。
- 元数据：[manifest.json](manifest.json)；脚本 SHA256：`07642df50587cbad056865445bfe4d38802322b2eb02d564234d1b4252a528dc`。
- 官方核心地址和摘要保留原脚本固定值，资产来自 [Telemt 官方 3.5.5](https://github.com/telemt/telemt/releases/tag/3.5.5)。

## 一键部署

三台新的 Ubuntu 22.04 VPS 分别执行，入口先开始，再按菜单加入后端：

```bash
bash -c 'set -e; tmp=$(mktemp); trap '\''rm -f -- "$tmp"'\'' EXIT; curl -q -fL --retry 3 --connect-timeout 10 --max-time 120 --max-filesize 1048576 --proto "=https" --tlsv1.2 https://raw.githubusercontent.com/bababoyi6/me/v1.3.1-original/telemt-dual-hop.sh -o "$tmp"; test -s "$tmp"; printf "%s  %s\n" "07642df50587cbad056865445bfe4d38802322b2eb02d564234d1b4252a528dc" "$tmp" | sha256sum --check --status; grep -Fqx "# Telemt Dual-Hop - three VPS / two native MTProxy links" "$tmp"; bash -n "$tmp"; if [ "$(id -u)" -eq 0 ]; then bash "$tmp"; else sudo bash "$tmp"; fi'
```

命令固定读取原版标签并核对脚本 SHA256，不跟随 main。也可从发行页下载脚本和 SHA256SUMS，核对后再运行。

## 版本边界

“原始稳定版”指优化前使用的历史基线，不等于本轮重新完成长期稳定性认证。它没有后来的凭据保护、锁处理、备份回退等管理器修复；这些内容没有偷偷补回历史文件。

原脚本中的 `a update` 地址仍指向 main，保留当时行为。执行它会更新管理器，不能用它保持完整的历史管理器版本。当前管理器更新不替换运行核心，管理器回退也不等于核心回退。

不要在已有优化版节点上覆盖运行这份历史安装器，也不要在同一节点混装两个版本。需要历史原版时使用独立的新部署。

## 本轮检查范围

核对历史提交与冻结文件的字节/摘要、官方核心资产摘要；在 CI 检查 Bash 语法、ShellCheck、原脚本内置自检和 README 下载入口。没有重新执行性能矩阵或访问生产节点；不把当前优化版的完整安装验收说成原始版新做的验收。
