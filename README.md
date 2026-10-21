# 安装

```bash
curl -fL --retry 3 https://raw.githubusercontent.com/bababoyi6/me/main/telemt-dual-hop.sh -o /tmp/telemt-dual-hop.sh && sudo bash /tmp/telemt-dual-hop.sh
```

# 更新

在入口和两个后端各运行一次：

```bash
a update
```

即可完成本次管理器兼容升级，无需重装、重新生成链接或额外应用参数。保留现有入口 IP、端口、Secret、链接、WireGuard 密钥、伪装和主备配置，不重启或重载代理服务。

**本次没有已经证实的生产大幅提速。** 本机实验只测得入口 CPU 开销下降，吞吐基本不变，因此不把实验参数自动应用到生产，也不自动升级 Telemt 或内核。

# 可选性能诊断

升级后如需定位实际瓶颈，可在三台分别执行 `a perf 5`。只读采样5秒并输出脱敏统计，不启动测速或上传；此命令不是升级步骤。

完整分析、两批原始数据、受控测速和回退边界见 [性能审计手册](docs/PERFORMANCE.md)。
