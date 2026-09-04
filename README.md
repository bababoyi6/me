# Telemt Dual-Hop

三台 Ubuntu 22.04 VPS 的双线路 Telemt 部署和管理脚本。

## 安装

在每台 VPS 上运行：

```bash
curl -fL --retry 3 https://raw.githubusercontent.com/bababoyi6/me/main/telemt-dual-hop.sh -o /tmp/telemt-dual-hop.sh && sudo bash /tmp/telemt-dual-hop.sh
```

控制面板会引导你选择入口 VPS、后端 VPS1 或后端 VPS2。安装完成后，无论当前登录的是 root 还是 sudo 用户，直接输入：

```bash
a
```

即可打开可连续操作的中文控制面板，不需要反复执行安装命令。也可以使用 `a status`、`a links`、`a diagnose` 等快捷命令。

正常部署时，公网 IPv4 会通过多个独立检测源自动确认，WireGuard 端口也会自动分配；默认端口被占用时会自动顺延寻找可用端口。只有公网 IP 检测无法取得一致结果，或自动端口范围全部被占用时，才会要求手动输入。MTP 公网端口固定为 TCP `443`。

## 快捷更新

安装后的任意节点都可以执行：

```bash
a update
```

也可以使用简写 `a u`，或者在控制面板中选择“安全更新管理脚本”。更新会先完成脚本身份、版本兼容性、Bash 语法和内置自检，全部通过后才替换当前管理器，不会重启服务或修改集群配置。下载、校验或安装失败时，当前可用版本会被保留。

## 已安装 VPS 增加 `a` 快捷入口

每台已安装的 VPS 只需重新运行一次上面的安装命令。脚本会更新管理器并创建 `a` 快捷入口，不会重装或改动现有集群配置。

如果系统中已经存在其他程序创建的 `/usr/local/bin/a`，脚本会停止并提示，不会擅自覆盖。
