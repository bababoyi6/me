#!/usr/bin/env bash
# Extracted updater bodies from production commit 452c50d (1.3.1).
# The minimal harness supplies only their globals; no installation logic runs.
# shellcheck disable=SC2119,SC2120
set -Eeuo pipefail
umask 077
readonly TDH_VERSION="1.3.1"
readonly TDH_CONFIG_SCHEMA="5"
readonly TDH_PROTOCOL_VERSION="1.2.1"
readonly TDH_MANAGER="${TDH_MANAGER:?}"
readonly TDH_SHORTCUT="${TDH_SHORTCUT:?}"
readonly TDH_UPDATE_URL="https://raw.githubusercontent.com/bababoyi6/me/main/telemt-dual-hop.sh"
readonly C_BLUE='' C_GREEN='' C_RED='' C_RESET=''
TX_ACTIVE=0
info() { printf '%s[信息]%s %s\n' "$C_BLUE" "$C_RESET" "$*"; }
ok() { printf '%s[成功]%s %s\n' "$C_GREEN" "$C_RESET" "$*"; }
die() {
  printf '%s[失败]%s %s\n' "$C_RED" "$C_RESET" "$*" >&2
  if [[ ${TX_ACTIVE:-0} == 1 ]] && declare -F rollback_transaction >/dev/null; then
    rollback_transaction
  fi
  exit 1
}

shortcut_is_managed() {
  [[ ! -L $TDH_SHORTCUT && -f $TDH_SHORTCUT ]] && \
    grep -Fqx '# Managed by Telemt Dual-Hop' "$TDH_SHORTCUT" 2>/dev/null
}

extract_release_value() {
  local file=$1 name=$2
  awk -F'"' -v prefix="readonly ${name}=\"" 'index($0, prefix) == 1 { print $2; exit }' "$file"
}

update_manager() {
  local download_tmp manager_tmp new_version new_schema new_protocol checksum
  load_state || die "尚未安装，不能使用快捷更新。"
  if [[ ( -e $TDH_SHORTCUT || -L $TDH_SHORTCUT ) ]] && ! shortcut_is_managed; then
    die "系统中已存在非本脚本创建的 a 命令（${TDH_SHORTCUT}）；更新已取消。"
  fi

  download_tmp=$(mktemp)
  info "正在从 GitHub 获取最新管理脚本……"
  if ! curl -fL --retry 3 --retry-delay 2 --connect-timeout 10 --max-time 120 \
      --proto '=https' --tlsv1.2 "$TDH_UPDATE_URL" -o "$download_tmp"; then
    rm -f "$download_tmp"
    die "下载失败；当前版本未作任何修改。"
  fi

  if ! grep -Fqx '# Telemt Dual-Hop - three VPS / two native MTProxy links' "$download_tmp"; then
    rm -f "$download_tmp"
    die "下载内容不是有效的 Telemt Dual-Hop 脚本；当前版本未作任何修改。"
  fi
  new_version=$(extract_release_value "$download_tmp" TDH_VERSION)
  new_schema=$(extract_release_value "$download_tmp" TDH_CONFIG_SCHEMA)
  new_protocol=$(extract_release_value "$download_tmp" TDH_PROTOCOL_VERSION)
  if [[ ! $new_version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ || ! $new_schema =~ ^[0-9]+$ || \
        ! $new_protocol =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    rm -f "$download_tmp"
    die "下载脚本的版本信息无效；当前版本未作任何修改。"
  fi
  if [[ $new_schema != "$TDH_CONFIG_SCHEMA" || $new_protocol != "$TDH_PROTOCOL_VERSION" ]]; then
    rm -f "$download_tmp"
    die "新版本涉及配置协议升级，不能快捷更新；请查看发布说明后手动升级。"
  fi
  if ! dpkg --compare-versions "$new_version" ge "$TDH_VERSION"; then
    rm -f "$download_tmp"
    die "远端版本 ${new_version} 低于当前版本 ${TDH_VERSION}，已拒绝降级。"
  fi
  if ! bash -n "$download_tmp"; then
    rm -f "$download_tmp"
    die "新脚本未通过 Bash 语法检查；当前版本未作任何修改。"
  fi
  if ! bash "$download_tmp" --self-test >/dev/null; then
    rm -f "$download_tmp"
    die "新脚本未通过内置自检；当前版本未作任何修改。"
  fi

  checksum=$(sha256sum "$download_tmp" | awk '{print $1}')
  manager_tmp=$(mktemp "${TDH_MANAGER}.update.XXXXXX")
  if ! install -m 0755 "$download_tmp" "$manager_tmp"; then
    rm -f "$download_tmp" "$manager_tmp"
    die "无法准备新管理器；当前版本未作任何修改。"
  fi
  if ! mv -f "$manager_tmp" "$TDH_MANAGER"; then
    rm -f "$download_tmp" "$manager_tmp"
    die "无法替换管理器；当前版本未作任何修改。"
  fi
  rm -f "$download_tmp"
  install_shortcut
  if [[ $new_version == "$TDH_VERSION" ]]; then
    ok "已重新安装最新版 ${new_version}（SHA256: ${checksum}）。"
  else
    ok "管理脚本已从 ${TDH_VERSION} 更新到 ${new_version}（SHA256: ${checksum}）。"
  fi
  info "服务和集群配置均未改动；退出当前面板后再次输入 a 即可使用新版本。"
}
