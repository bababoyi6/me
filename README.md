# 安装

```bash
curl -fL --retry 3 https://raw.githubusercontent.com/bababoyi6/me/main/telemt-dual-hop.sh -o /tmp/telemt-dual-hop.sh && sudo bash /tmp/telemt-dual-hop.sh
```

# 更新

```bash
a update
```

更新只替换管理脚本，不重启服务、不修改已有配置，也不自动升级 Telemt。

# 性能采样

更新到 1.4.0 管理器后，在入口和两个后端分别运行：

```bash
a perf 5
```

只读采样 5 秒，输出脱敏统计，不发起测速或上传数据。控制面板也提供此功能。

完整源码审计、实际本机对照数据、三段测速方案、显式试用与回滚步骤见 [性能审计手册](docs/PERFORMANCE.md)。目前没有生产 VPS 或 Telegram 实测提速结论。入口试用使用 `a perf-apply`，恢复使用 `a perf-rollback`；请先按手册采集基线数据。原安装配置和 Telemt 3.5.5 保持不变。
