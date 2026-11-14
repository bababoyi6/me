适用于 Ubuntu 22.04。在三台准备好的服务器上分别执行：

<!-- install:stable -->
```bash
bash -c 'set -e; tmp=$(mktemp); trap '\''rm -f -- "$tmp"'\'' EXIT; curl -q -fL --retry 3 --connect-timeout 10 --max-time 120 --max-filesize 1048576 --proto "=https" --proto-redir "=https" --tlsv1.2 https://raw.githubusercontent.com/bababoyi6/me/v1.7.1/telemt-dual-hop.sh -o "$tmp"; test -s "$tmp"; printf "%s  %s\n" "ffa6d8edb386aa5cc833f814ca1a26c9ba1c314df44e4feccff17191940ac8a3" "$tmp" | sha256sum --check --status; grep -Fqx "# Telemt Dual-Hop - three VPS / two native MTProxy links" "$tmp"; bash -n "$tmp"; if [ "$(id -u)" -eq 0 ]; then bash "$tmp"; else sudo bash "$tmp"; fi'
```

1. 第一台选择「安装入口」，保存两个加入码。
2. 另外两台分别选择「后端 VPS1」「后端 VPS2」，粘贴对应加入码。
3. 将两台后端生成的回执码录入入口，按提示获取代理链接。

安装后输入 `a` 打开管理菜单。加入码、回执码及代理链接请自行保管。
