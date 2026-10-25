#!/usr/bin/env bash
# shellcheck disable=SC2119,SC2120
# Telemt Dual-Hop - three VPS / two native MTProxy links
# Supported OS: Ubuntu 22.04 (x86_64, aarch64)

set -Eeuo pipefail
umask 077

readonly TDH_VERSION="1.3.1"
readonly TDH_CONFIG_SCHEMA="5"
readonly TDH_PROTOCOL_VERSION="1.2.1"
readonly TELEMT_VERSION="3.5.5"
readonly TELEMT_SHA256_AMD64="2d5fb35b526f548bb6ffe7689cdf4f51f4108b97014be1b3b4186a58ab28d605"
readonly TELEMT_SHA256_ARM64="075c9e2d3e86115af0bc47da881327375ef094c21fb6ed40bde41e4a77a261f3"
readonly TDH_BASE="${TDH_BASE:-/etc/telemt-dual-hop}"
readonly TDH_STATE="${TDH_STATE:-${TDH_BASE}/state.env}"
readonly TDH_KEY_DIR="${TDH_KEY_DIR:-${TDH_BASE}/keys}"
readonly TDH_CONFIG_DIR="${TDH_CONFIG_DIR:-${TDH_BASE}/telemt}"
readonly TDH_WORK_ROOT="${TDH_WORK_ROOT:-/var/lib/telemt-dual-hop}"
readonly TDH_BIN="${TDH_BIN:-/usr/local/lib/telemt-dual-hop/telemt}"
readonly TDH_MANAGER="${TDH_MANAGER:-/usr/local/sbin/telemt-dual-hop}"
readonly TDH_SHORTCUT="${TDH_SHORTCUT:-/usr/local/bin/a}"
readonly TDH_UPDATE_URL="https://raw.githubusercontent.com/bababoyi6/me/main/telemt-dual-hop.sh"
readonly TDH_HAPROXY_CFG="${TDH_HAPROXY_CFG:-${TDH_BASE}/haproxy.cfg}"
readonly TDH_LOCK="${TDH_LOCK:-/run/lock/telemt-dual-hop.lock}"
readonly TDH_DOMAIN_A="apple.com"
readonly TDH_DOMAIN_B="gs.apple.com"
readonly TDH_APPLE_INC_ROOT_URL="https://www.apple.com/appleca/AppleIncRootCertificate.cer"
readonly TDH_APPLE_INC_ROOT_SHA256="b0b1730ecbc7ff4505142c49f1295e6eda6bcaed7e2c68c5be91b5a11001f024"
readonly TDH_PUBLIC_PORT="443"
readonly TDH_TELEMT_PORT="24431"
readonly TDH_API_PORT="19091"
readonly TDH_AGENT_PORT="19101"
readonly TDH_TG_DC_IPV4="149.154.175.50,149.154.167.51,149.154.175.100,149.154.167.91,149.154.171.5"
readonly TDH_TG_DC_PORT="443"

if [[ -t 1 ]]; then
  readonly C_RED=$'\033[31m' C_GREEN=$'\033[32m' C_YELLOW=$'\033[33m'
  readonly C_BLUE=$'\033[34m' C_BOLD=$'\033[1m' C_RESET=$'\033[0m'
else
  readonly C_RED='' C_GREEN='' C_YELLOW='' C_BLUE='' C_BOLD='' C_RESET=''
fi

info() { printf '%s[信息]%s %s\n' "$C_BLUE" "$C_RESET" "$*"; }
ok() { printf '%s[成功]%s %s\n' "$C_GREEN" "$C_RESET" "$*"; }
warn() { printf '%s[注意]%s %s\n' "$C_YELLOW" "$C_RESET" "$*" >&2; }
die() {
  printf '%s[失败]%s %s\n' "$C_RED" "$C_RESET" "$*" >&2
  if [[ ${TX_ACTIVE:-0} == 1 ]] && declare -F rollback_transaction >/dev/null; then
    rollback_transaction
  fi
  exit 1
}
title() { printf '\n%s%s%s\n' "$C_BOLD" "$*" "$C_RESET"; }

on_error() {
  local rc=$1 line=$2
  if [[ ${TX_ACTIVE:-0} == 1 ]] && declare -F rollback_transaction >/dev/null; then
    rollback_transaction
  fi
  printf '%s[失败]%s 脚本在第 %s 行停止（退出码 %s）。没有通过校验的配置不会启用。\n' \
    "$C_RED" "$C_RESET" "$line" "$rc" >&2
}
trap 'on_error $? $LINENO' ERR

# State variables are deliberately scalar and allow-listed.
TDH_ROLE="" CONFIG_SCHEMA="" INSTALLED_VERSION="" CLUSTER_ID="" ENTRY_PUBLIC_IP="" PORT_A="" PORT_B=""
SECRET_A="" SECRET_B="" JOIN_CREATED="" JOIN_EXPIRES=""
ENROLL_TOKEN_B1="" ENROLL_TOKEN_B2=""
B1_REGISTERED="0" B2_REGISTERED="0" B1_PUBLIC_IP="" B2_PUBLIC_IP=""
B1_WG_PUB="" B2_WG_PUB="" B1_WG_PORT="" B2_WG_PORT=""
ENTRY_MAX_CONNECTIONS="" BACKEND_MAX_CONNECTIONS=""
B1_MAX_CONNECTIONS="" B2_MAX_CONNECTIONS=""
BACKEND_BUFFER_BUDGET_BYTES=""
LINK_A="" LINK_B="" BACKEND_PUBLIC_IP="" BACKEND_WG_PORT=""
ENTRY_WG_PUBLIC_KEY="" ENROLL_TOKEN="" LOCAL_WG_IP="" ENTRY_WG_IP=""
WG_INTERFACE="" RESPONSE_LINK_A="" RESPONSE_LINK_B="" API_TOKEN=""
UFW_ADDED_A=0 UFW_ADDED_B=0 UFW_ADDED_WG=0
UFW_ADDED_P24431=0 UFW_ADDED_P24432=0 UFW_ADDED_P19101=0 UFW_ADDED_P19102=0
CREATED_TELEMT_USER=0 CREATED_TELEMT_GROUP=0
PREV_QDISC="" PREV_CC="" PREV_SOMAX="" PREV_SYN_BACKLOG=""
PREV_KEEPALIVE_TIME="" PREV_KEEPALIVE_INTVL="" PREV_KEEPALIVE_PROBES=""
TX_ACTIVE=0 TX_KIND=""

readonly STATE_VARS=(
  TDH_ROLE CONFIG_SCHEMA INSTALLED_VERSION CLUSTER_ID ENTRY_PUBLIC_IP PORT_A PORT_B SECRET_A SECRET_B
  JOIN_CREATED JOIN_EXPIRES ENROLL_TOKEN_B1 ENROLL_TOKEN_B2
  B1_REGISTERED B2_REGISTERED
  B1_PUBLIC_IP B2_PUBLIC_IP B1_WG_PUB B2_WG_PUB B1_WG_PORT B2_WG_PORT
  ENTRY_MAX_CONNECTIONS BACKEND_MAX_CONNECTIONS B1_MAX_CONNECTIONS B2_MAX_CONNECTIONS
  BACKEND_BUFFER_BUDGET_BYTES
  LINK_A LINK_B BACKEND_PUBLIC_IP BACKEND_WG_PORT ENTRY_WG_PUBLIC_KEY
  ENROLL_TOKEN LOCAL_WG_IP ENTRY_WG_IP WG_INTERFACE RESPONSE_LINK_A RESPONSE_LINK_B API_TOKEN
  UFW_ADDED_A UFW_ADDED_B UFW_ADDED_WG
  UFW_ADDED_P24431 UFW_ADDED_P24432 UFW_ADDED_P19101 UFW_ADDED_P19102
  CREATED_TELEMT_USER CREATED_TELEMT_GROUP
  PREV_QDISC PREV_CC PREV_SOMAX PREV_SYN_BACKLOG PREV_KEEPALIVE_TIME
  PREV_KEEPALIVE_INTVL PREV_KEEPALIVE_PROBES
)

usage() {
  cat <<'EOF'
Telemt Dual-Hop 1.3.1

用法：
  sudo bash telemt-dual-hop.sh              打开中文菜单
  a                                         安装后打开控制面板（自动使用 sudo）
  a status                                  快速查看状态
  a update                                  安全更新管理脚本（可简写为 a u）
  sudo bash telemt-dual-hop.sh entry        初始化入口 VPS
  sudo bash telemt-dual-hop.sh backend1     安装后端 VPS1
  sudo bash telemt-dual-hop.sh backend2     安装后端 VPS2
  sudo bash telemt-dual-hop.sh register     在入口录入后端回执
  sudo bash telemt-dual-hop.sh complete     重试启用已配对入口
  sudo bash telemt-dual-hop.sh status       查看状态
  sudo bash telemt-dual-hop.sh links        显示两条 MTP 链接
  sudo bash telemt-dual-hop.sh diagnose     运行诊断
  sudo bash telemt-dual-hop.sh uninstall    卸载本脚本创建的服务
  bash telemt-dual-hop.sh --self-test       本地无特权自检
EOF
}

require_root() {
  [[ $(id -u) -eq 0 ]] || die "请使用 sudo 或 root 运行。"
}

apt_get_with_lock_wait() {
  local error_file rc deadline
  error_file=$(mktemp)
  deadline=$((SECONDS + 600))

  while true; do
    if DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=30 "$@" 2>"$error_file"; then
      rm -f "$error_file"
      return 0
    else
      rc=$?
    fi

    if grep -Eqi 'Could not get lock|Unable to lock directory|Unable to acquire the dpkg frontend lock|is held by process' "$error_file" \
      && (( SECONDS < deadline )); then
      warn "Ubuntu 后台更新正在占用软件包管理器；等待完成后自动重试（最长约 10 分钟）……"
      sleep 5
      : >"$error_file"
      continue
    fi

    cat "$error_file" >&2
    rm -f "$error_file"
    return "$rc"
  done
}

validate_runtime_paths() {
  [[ $TDH_BASE == /etc/telemt-dual-hop ]] || die "生产模式不允许覆盖 TDH_BASE。"
  [[ $TDH_STATE == /etc/telemt-dual-hop/state.env ]] || die "生产模式状态路径异常。"
  [[ $TDH_KEY_DIR == /etc/telemt-dual-hop/keys ]] || die "生产模式密钥路径异常。"
  [[ $TDH_CONFIG_DIR == /etc/telemt-dual-hop/telemt ]] || die "生产模式配置路径异常。"
  [[ $TDH_WORK_ROOT == /var/lib/telemt-dual-hop ]] || die "生产模式工作路径异常。"
  [[ $TDH_BIN == /usr/local/lib/telemt-dual-hop/telemt ]] || die "生产模式二进制路径异常。"
  [[ $TDH_MANAGER == /usr/local/sbin/telemt-dual-hop ]] || die "生产模式管理器路径异常。"
  [[ $TDH_SHORTCUT == /usr/local/bin/a ]] || die "生产模式快捷命令路径异常。"
  [[ $TDH_UPDATE_URL == https://raw.githubusercontent.com/bababoyi6/me/main/telemt-dual-hop.sh ]] || \
    die "生产模式更新地址异常。"
}

ensure_bootstrap_tools() {
  local missing=()
  command -v flock >/dev/null 2>&1 || missing+=(util-linux)
  command -v python3 >/dev/null 2>&1 || missing+=(python3)
  if (( ${#missing[@]} > 0 )); then
    apt_get_with_lock_wait update -qq
    apt_get_with_lock_wait install -y --no-install-recommends "${missing[@]}"
  fi
}

acquire_lock() {
  mkdir -p "$(dirname "$TDH_LOCK")"
  exec 9>"$TDH_LOCK"
  flock -n 9 || die "另一个 Telemt Dual-Hop 操作正在运行。"
}

check_os() {
  [[ -r /etc/os-release ]] || die "无法读取 /etc/os-release。"
  # shellcheck disable=SC1091
  source /etc/os-release
  [[ ${ID:-} == ubuntu && ${VERSION_ID:-} == "22.04" ]] || \
    die "仅支持 Ubuntu 22.04；检测到 ${PRETTY_NAME:-未知系统}。"
  case $(uname -m) in
    x86_64|amd64|aarch64|arm64) ;;
    *) die "不支持的 CPU 架构：$(uname -m)" ;;
  esac
  command -v systemctl >/dev/null || die "未检测到 systemd。"
}

install_packages() {
  local packages=(ca-certificates curl jq python3 openssl wireguard-tools iproute2 iputils-ping
    netcat-openbsd qrencode socat util-linux procps kmod tar gzip)
  if [[ ${1:-} == entry ]]; then
    packages+=(haproxy)
  fi
  info "安装所需的 Ubuntu 软件包……"
  apt_get_with_lock_wait update -qq
  apt_get_with_lock_wait install -y --no-install-recommends "${packages[@]}"
}

shortcut_is_managed() {
  [[ ! -L $TDH_SHORTCUT && -f $TDH_SHORTCUT ]] && \
    grep -Fqx '# Managed by Telemt Dual-Hop' "$TDH_SHORTCUT" 2>/dev/null
}

install_shortcut() {
  local tmp
  if [[ ( -e $TDH_SHORTCUT || -L $TDH_SHORTCUT ) ]] && ! shortcut_is_managed; then
    die "系统中已存在非本脚本创建的 a 命令（${TDH_SHORTCUT}）；为避免覆盖，请先自行改名或移除。"
  fi
  tmp=$(mktemp)
  {
    printf '%s\n' '#!/usr/bin/env bash' '# Managed by Telemt Dual-Hop'
    printf 'readonly manager=%q\n' "$TDH_MANAGER"
    cat <<'EOF'
if [[ ! -x $manager ]]; then
  printf '[失败] Telemt Dual-Hop 管理器不存在：%s\n' "$manager" >&2
  exit 1
fi
if [[ $(id -u) -eq 0 ]]; then
  exec "$manager" "$@"
fi
command -v sudo >/dev/null 2>&1 || {
  printf '[失败] 当前用户不是 root，且系统没有 sudo；请切换到 root 后重试。\n' >&2
  exit 1
}
exec sudo -- "$manager" "$@"
EOF
  } >"$tmp"
  install -d -o root -g root -m 0755 "$(dirname "$TDH_SHORTCUT")"
  install -m 0755 "$tmp" "$TDH_SHORTCUT"
  rm -f "$tmp"
}

remove_shortcut() {
  if shortcut_is_managed; then
    rm -f "$TDH_SHORTCUT"
  elif [[ -e $TDH_SHORTCUT || -L $TDH_SHORTCUT ]]; then
    warn "${TDH_SHORTCUT} 已不再由本脚本管理，卸载时予以保留。"
  fi
}

install_self() {
  local self=${BASH_SOURCE[0]} self_real manager_real entrypoints_changed=0
  if [[ -f $self && -r $self ]]; then
    if [[ ( -e $TDH_SHORTCUT || -L $TDH_SHORTCUT ) ]] && ! shortcut_is_managed; then
      die "系统中已存在非本脚本创建的 a 命令（${TDH_SHORTCUT}）；为避免覆盖，请先自行改名或移除。"
    fi
    self_real=$(readlink -f -- "$self")
    manager_real=$(readlink -f -- "$TDH_MANAGER" 2>/dev/null || printf '%s\n' "$TDH_MANAGER")
    if [[ $self_real != "$manager_real" ]]; then
      install -m 0755 "$self" "$TDH_MANAGER"
      entrypoints_changed=1
    fi
    shortcut_is_managed || entrypoints_changed=1
    install_shortcut
    if (( entrypoints_changed == 1 )); then
      ok "管理入口已就绪；以后直接输入 a 即可打开控制面板。"
    fi
  else
    warn "当前通过不可复制的输入流运行，无法安装 a 快捷命令；请使用 README 中先下载再运行的安装方式。"
  fi
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

init_dirs() {
  install -d -o root -g root -m 0700 "$TDH_BASE" "$TDH_KEY_DIR"
  install -d -o root -g root -m 0750 "$TDH_CONFIG_DIR" "$TDH_WORK_ROOT"
  # Telemt runs unprivileged. It needs search permission on the private base
  # directory and its work-root parent, but state and WireGuard keys stay root-only.
  if [[ $TDH_ROLE == backend1 || $TDH_ROLE == backend2 ]] && getent group telemt >/dev/null 2>&1; then
    chown root:telemt "$TDH_BASE" "$TDH_CONFIG_DIR" "$TDH_WORK_ROOT"
    chmod 0710 "$TDH_BASE"
    chmod 0750 "$TDH_CONFIG_DIR" "$TDH_WORK_ROOT"
  fi
}

safe_remove_tree() {
  local target=$1
  if [[ $target != /* || $target == / || ${target##*/} != telemt-dual-hop ]]; then
    warn "拒绝递归删除不安全路径：$target"
    return 1
  fi
  rm -rf -- "$target"
}

rollback_transaction() {
  local kind=${TX_KIND:-unknown} interface=""
  TX_ACTIVE=0
  set +e
  warn "安装未通过，正在回滚本次创建的配置……"
  if [[ $kind == backend1 || $kind == backend2 ]]; then
    interface=$([[ $kind == backend1 ]] && echo tdh-b1 || echo tdh-b2)
    systemctl disable --now telemt-dual-hop-telemt.service telemt-dual-hop-health-agent.service \
      "wg-quick@${interface}.service" >/dev/null 2>&1
    rm -f /etc/systemd/system/telemt-dual-hop-telemt.service
    rm -f /etc/systemd/system/telemt-dual-hop-health-agent.service "/etc/wireguard/${interface}.conf"
    safe_remove_tree /usr/local/lib/telemt-dual-hop
    safe_remove_tree "$TDH_WORK_ROOT"
    remove_telemt_account
  elif [[ $kind == entry ]]; then
    systemctl disable --now telemt-dual-hop-haproxy.service >/dev/null 2>&1
    rm -f /etc/systemd/system/telemt-dual-hop-haproxy.service
  fi
  if declare -F remove_ufw_rules >/dev/null; then remove_ufw_rules; fi
  rm -f /etc/sysctl.d/90-telemt-dual-hop.conf /etc/modules-load.d/90-telemt-dual-hop.conf
  if declare -F restore_tuning >/dev/null; then restore_tuning; fi
  safe_remove_tree "$TDH_BASE"
  remove_shortcut
  rm -f "$TDH_MANAGER"
  systemctl daemon-reload >/dev/null 2>&1
  set -e
}

save_state() {
  local tmp var
  init_dirs
  tmp=$(mktemp "${TDH_BASE}/state.env.XXXXXX")
  chmod 0600 "$tmp"
  {
    printf '# Generated by Telemt Dual-Hop %q\n' "$TDH_VERSION"
    for var in "${STATE_VARS[@]}"; do
      printf '%s=%q\n' "$var" "${!var-}"
    done
  } >"$tmp"
  mv -f "$tmp" "$TDH_STATE"
  chmod 0600 "$TDH_STATE"
}

load_state() {
  [[ -f $TDH_STATE ]] || return 1
  [[ $(stat -c '%u' "$TDH_STATE") -eq 0 ]] || die "状态文件所有者异常。"
  local mode
  mode=$(stat -c '%a' "$TDH_STATE")
  [[ $mode == 600 ]] || die "状态文件权限必须为 600，当前为 $mode。"
  # The file is created only by save_state from an allow-list.
  # shellcheck disable=SC1090
  source "$TDH_STATE"
  case $TDH_ROLE in entry|backend1|backend2) ;; *) die "状态文件中的角色无效。" ;; esac
}

state_is_current() {
  [[ ${CONFIG_SCHEMA:-} == "$TDH_CONFIG_SCHEMA" && \
     ${PORT_A:-} == "$TDH_PUBLIC_PORT" && \
     ${PORT_B:-} == "$TDH_PUBLIC_PORT" ]]
}

require_current_state() {
  state_is_current || die "检测到不兼容的旧版配置。为避免旧服务依赖或容量参数与新版混装，请先用管理菜单完整卸载旧集群，再在三台 VPS 重新安装 ${TDH_VERSION}。"
}

rand_hex() { openssl rand -hex "${1:-16}"; }

validate_ipv4() {
  python3 - "$1" <<'PY' >/dev/null
import ipaddress, sys
ip = ipaddress.ip_address(sys.argv[1])
if ip.version != 4 or ip.is_unspecified or ip.is_multicast:
    raise SystemExit(1)
PY
}

validate_public_ipv4() {
  python3 - "$1" <<'PY' >/dev/null
import ipaddress, sys
ip = ipaddress.ip_address(sys.argv[1])
if ip.version != 4 or not ip.is_global:
    raise SystemExit(1)
PY
}

validate_port() {
  [[ $1 =~ ^[0-9]+$ ]] && (( 1 <= 10#$1 && 10#$1 <= 65535 ))
}

calculate_connection_limit() {
  local mem_mb=${1:-} cpu_count=${2:-} mem_limit cpu_limit limit
  if [[ -z $mem_mb ]]; then
    mem_mb=$(awk '/MemTotal/{print int($2/1024); exit}' /proc/meminfo)
  fi
  if [[ -z $cpu_count ]]; then
    cpu_count=$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null) || return 1
  fi
  [[ $mem_mb =~ ^[0-9]+$ && $cpu_count =~ ^[0-9]+$ ]] || return 1
  (( ${#mem_mb} <= 7 && ${#cpu_count} <= 4 )) || return 1
  mem_mb=$(( 10#$mem_mb ))
  cpu_count=$(( 10#$cpu_count ))
  (( mem_mb >= 768 && cpu_count >= 1 )) || return 1

  # One Telemt process serves both links on each backend. Keep enough headroom
  # for the kernel, WireGuard and failover bursts; a single vCPU remains a
  # practical ceiling even when the VPS has additional RAM.
  mem_limit=$(( mem_mb * 2 ))
  cpu_limit=$(( cpu_count * 4000 ))
  if (( mem_limit < cpu_limit )); then limit=$mem_limit; else limit=$cpu_limit; fi
  (( limit < 1000 )) && limit=1000
  (( limit > 30000 )) && limit=30000
  printf '%s\n' "$limit"
}

calculate_direct_buffer_budget() {
  local mem_mb=${1:-} budget_mb
  if [[ -z $mem_mb ]]; then
    mem_mb=$(awk '/MemTotal/{print int($2/1024); exit}' /proc/meminfo)
  fi
  [[ $mem_mb =~ ^[0-9]+$ && ${#mem_mb} -le 7 ]] || return 1
  mem_mb=$(( 10#$mem_mb ))
  (( mem_mb >= 768 )) || return 1
  budget_mb=$(( mem_mb / 4 ))
  (( budget_mb < 64 )) && budget_mb=64
  (( budget_mb > 1024 )) && budget_mb=1024
  printf '%s\n' "$(( budget_mb * 1024 * 1024 ))"
}

validate_connection_limit() {
  local value=${1:-}
  [[ $value =~ ^[0-9]+$ ]] && (( value >= 1000 && value <= 30000 ))
}

validate_direct_buffer_budget() {
  local value=${1:-}
  [[ $value =~ ^[0-9]+$ ]] && \
    (( value >= 67108864 && value <= 1073741824 && value % 4096 == 0 ))
}

configured_cluster_connection_limit() {
  local candidate limit=""
  for candidate in "${ENTRY_MAX_CONNECTIONS:-}" "${B1_MAX_CONNECTIONS:-}" "${B2_MAX_CONNECTIONS:-}"; do
    validate_connection_limit "$candidate" || return 1
    if [[ -z $limit ]] || (( candidate < limit )); then limit=$candidate; fi
  done
  printf '%s\n' "$limit"
}

validate_hex32() { [[ $1 =~ ^[0-9a-f]{32}$ ]]; }

validate_wg_key() { [[ $1 =~ ^[A-Za-z0-9+/]{43}=$ ]]; }

detect_public_ipv4() {
  local url ip
  local -A votes=()
  for url in \
    https://api.ipify.org \
    https://ipv4.icanhazip.com \
    https://checkip.amazonaws.com \
    https://ifconfig.me/ip \
    https://v4.ident.me; do
    ip=$(curl -4fsS --connect-timeout 3 --max-time 6 "$url" 2>/dev/null | tr -d '[:space:]' || true)
    if validate_public_ipv4 "$ip" 2>/dev/null; then
      votes["$ip"]=$(( ${votes["$ip"]:-0} + 1 ))
      if (( votes["$ip"] >= 2 )); then
        printf '%s\n' "$ip"
        return 0
      fi
    fi
  done
  return 1
}

verify_apple_private_root_tls() {
  local ip=$1 domain=$2 tmp der pem actual rc=1
  tmp=$(mktemp -d) || return 1
  der="${tmp}/apple-root.der"
  pem="${tmp}/apple-root.pem"
  if curl -fLsS --retry 3 --connect-timeout 10 --max-time 30 \
       "$TDH_APPLE_INC_ROOT_URL" -o "$der" && \
     openssl x509 -inform DER -in "$der" -out "$pem" 2>/dev/null && \
     actual=$(openssl x509 -in "$pem" -noout -fingerprint -sha256 2>/dev/null | \
       awk -F= '{print tolower($2)}' | tr -d ':') && \
     [[ $actual == "$TDH_APPLE_INC_ROOT_SHA256" ]] && \
     timeout 15 openssl s_client -tls1_3 -connect "${ip}:443" -servername "$domain" \
       -verify_hostname "$domain" -verify_return_error -CAfile "$pem" \
       </dev/null >/dev/null 2>&1; then
    rc=0
  fi
  rm -f -- "$der" "$pem"
  rmdir -- "$tmp" 2>/dev/null || true
  return "$rc"
}

check_front_domain() {
  local domain=$1 attempt front_ip
  local -a front_ips=()
  info "验证 ${domain} 的 IPv4、TLS 1.3、证书与 SNI……"
  for attempt in 1 2 3; do
    mapfile -t front_ips < <({ getent ahostsv4 "$domain" 2>/dev/null || true; } | \
      awk '$2 == "STREAM" && !seen[$1]++ {print $1}')
    for front_ip in "${front_ips[@]}"; do
      if validate_public_ipv4 "${front_ip:-}" 2>/dev/null; then
        if timeout 15 openssl s_client -tls1_3 -connect "${front_ip}:443" -servername "$domain" \
             -verify_hostname "$domain" -verify_return_error </dev/null >/dev/null 2>&1; then
          ok "${domain} TLS 前置检查通过。"
          return 0
        fi
        if [[ $domain == "$TDH_DOMAIN_B" ]] && verify_apple_private_root_tls "$front_ip" "$domain"; then
          ok "${domain} TLS 前置检查通过（Apple 官方根证书固定验证）。"
          return 0
        fi
      fi
    done
    if (( attempt < 3 )); then sleep 2; fi
  done
  die "无法验证 ${domain}:443 的有效 TLS 1.3 证书；请检查本机 DNS/出站 TCP 443 后重试。"
}

all_apple_owned_ipv4() {
  (( $# > 0 )) || return 1
  python3 - "$@" <<'PY' >/dev/null
import ipaddress, sys
apple = ipaddress.ip_network("17.0.0.0/8")
for value in sys.argv[1:]:
    address = ipaddress.ip_address(value)
    if address.version != 4 or address not in apple:
        raise SystemExit(1)
PY
}

check_china_dns_apple_owned() {
  local domain=$1 results
  info "从中国电信、联通、移动 DNS 视角验证 ${domain}……"
  results=$(python3 - "$domain" <<'PY'
import concurrent.futures
import ipaddress
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

domain = sys.argv[1]
apple = ipaddress.ip_network("17.0.0.0/8")
subnets = (
    ("中国电信", "202.96.0.0/24"),
    ("中国联通", "123.125.0.0/24"),
    ("中国移动", "221.179.0.0/24"),
)
def query(item):
    carrier, subnet = item
    url = "https://dns.google/resolve?" + urllib.parse.urlencode({
        "name": domain, "type": "A", "edns_client_subnet": subnet,
    })
    last_error = "无有效响应"
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"Accept": "application/dns-json"})
            with urllib.request.urlopen(request, timeout=12) as response:
                payload = json.load(response)
            if payload.get("Status") != 0:
                raise ValueError(f"DNS 状态 {payload.get('Status')}")
            returned_ecs = str(payload.get("edns_client_subnet", ""))
            if returned_ecs.split("/", 1)[0] != subnet.split("/", 1)[0]:
                raise ValueError("DNS 服务未确认 ECS 网段")
            addresses = [
                answer.get("data", "") for answer in payload.get("Answer", [])
                if answer.get("type") == 1
            ]
            if not addresses:
                raise ValueError("没有 IPv4 A 记录")
            parsed = [ipaddress.ip_address(value) for value in addresses]
            if any(address.version != 4 or address not in apple for address in parsed):
                raise ValueError("包含非 Apple 17.0.0.0/8 地址：" + ",".join(addresses))
            return carrier, addresses, ""
        except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
            last_error = str(exc)
            if attempt < 2:
                time.sleep(1)
    return carrier, [], last_error

with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
    resolved = list(executor.map(query, subnets))

failed = [(carrier, error) for carrier, _, error in resolved if error]
if failed:
    for carrier, error in failed:
        print(f"{carrier}：{error}", file=sys.stderr)
    raise SystemExit(1)
for carrier, addresses, _ in resolved:
    print(f"{carrier}|{' '.join(addresses)}")
PY
  ) || die "无法确认 ${domain} 在中国三网模拟解析中稳定落入 Apple 自有 17.0.0.0/8；为避免地域伪装失真，部署已停止。"
  while IFS='|' read -r carrier addresses; do
    ok "${carrier}视角：${domain} -> ${addresses}（Apple 自有地址）"
  done <<<"$results"
}

select_public_ipv4() {
  local target=$1 label=$2 detected answer
  if detected=$(detect_public_ipv4); then
    printf -v "$target" '%s' "$detected"
    ok "${label}已自动识别：${detected}"
    return 0
  fi

  warn "多个公网 IP 检测源未能取得一致结果，需要手动输入。"
  while true; do
    if ! read -r -p "请输入${label}: " answer </dev/tty; then
      die "未能读取公网 IPv4。"
    fi
    answer=${answer//[[:space:]]/}
    if validate_public_ipv4 "$answer" 2>/dev/null; then
      printf -v "$target" '%s' "$answer"
      return 0
    fi
    warn "${answer:-空值} 不是可路由的公网 IPv4，请重新输入。"
  done
}

prompt_secret() {
  local prompt=$1 answer
  read -r -s -p "$prompt: " answer </dev/tty || true
  printf '\n' >/dev/tty
  printf '%s\n' "$answer"
}

port_is_free_tcp() {
  ! tcp_port_is_listening "$1"
}

port_is_free_udp() {
  ! ss -H -lun 2>/dev/null | awk -v port="$1" '
    $4 ~ ("(^|:)" port "$") {found=1}
    END {exit !found}
  '
}

select_wireguard_port() {
  local target=$1 preferred=$2 candidate answer
  validate_port "$preferred" || die "自动分配的 WireGuard 端口无效。"
  for ((candidate=preferred; candidate<=preferred+200 && candidate<=65535; candidate++)); do
    if port_is_free_udp "$candidate"; then
      printf -v "$target" '%s' "$candidate"
      if (( candidate == preferred )); then
        ok "WireGuard UDP 端口已自动设置为 ${candidate}。"
      else
        warn "默认 UDP ${preferred} 已被占用，已自动改用 ${candidate}。"
      fi
      return 0
    fi
  done

  warn "自动端口范围 ${preferred}-$(( preferred + 200 )) 均不可用，需要手动指定。"
  while true; do
    if ! read -r -p '请输入可用的 WireGuard UDP 端口: ' answer </dev/tty; then
      die "未能读取 WireGuard 端口。"
    fi
    if validate_port "$answer" && port_is_free_udp "$answer"; then
      printf -v "$target" '%s' "$answer"
      return 0
    fi
    warn "端口无效或已被占用，请重新输入。"
  done
}

tcp_port_is_listening() {
  ss -H -ltn 2>/dev/null | awk -v port="$1" '
    $4 ~ ("(^|:)" port "$") {found=1}
    END {exit !found}
  '
}

tcp_listener_is_present() {
  ss -H -ltn 2>/dev/null | awk -v endpoint="${1}:${2}" '
    $4 == endpoint {found=1}
    END {exit !found}
  '
}

tcp_port_is_bound_only_to() {
  ss -H -ltn 2>/dev/null | awk -v endpoint="${1}:${2}" -v port="$2" '
    $4 ~ ("(^|:)" port "$") {if ($4 == endpoint) found=1; else bad=1}
    END {exit !(found && !bad)}
  '
}

tunnel_network_is_available() {
  local network=$1 own_interface=${2:-} test_mode=0 route_fixture="" address_fixture=""
  if [[ ${TDH_SOURCE_ONLY:-0} == 1 && -v TDH_TEST_ROUTE_JSON && -v TDH_TEST_ADDRESS_JSON ]]; then
    test_mode=1
    route_fixture=$TDH_TEST_ROUTE_JSON
    address_fixture=$TDH_TEST_ADDRESS_JSON
  fi
  TDH_TUNNEL_TEST_MODE=$test_mode TDH_ROUTE_FIXTURE=$route_fixture TDH_ADDRESS_FIXTURE=$address_fixture \
    python3 - "$network" "$own_interface" <<'PY'
import ipaddress, json, subprocess, sys
import os

target = ipaddress.ip_network(sys.argv[1], strict=True)
own_interface = sys.argv[2]

def ip_json(kind, *args):
    if os.environ["TDH_TUNNEL_TEST_MODE"] == "1":
        key = "TDH_ROUTE_FIXTURE" if kind == "route" else "TDH_ADDRESS_FIXTURE"
        return json.loads(os.environ[key])
    return json.loads(subprocess.check_output(("ip", "-j", *args), text=True))

try:
    for route in ip_json("route", "route", "show", "table", "all"):
        if route.get("dev") == own_interface or route.get("dst") in (None, "default"):
            continue
        try:
            existing = ipaddress.ip_network(route["dst"], strict=False)
        except (KeyError, ValueError):
            continue
        if existing.version == 4 and existing.overlaps(target):
            raise RuntimeError(f"路由 {existing}（设备 {route.get('dev', '未知')}）")
    for interface in ip_json("address", "address", "show"):
        if interface.get("ifname") == own_interface:
            continue
        for address in interface.get("addr_info", []):
            if address.get("family") != "inet":
                continue
            existing = ipaddress.ip_network(
                f"{address['local']}/{address['prefixlen']}", strict=False
            )
            if existing.overlaps(target):
                raise RuntimeError(f"地址 {existing}（设备 {interface.get('ifname', '未知')}）")
except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
    raise SystemExit(f"无法检查现有网络：{exc}")
except RuntimeError as exc:
    raise SystemExit(f"WireGuard 网段 {target} 与现有{exc}冲突")
PY
}

write_private_file() {
  local target=$1 mode=$2 tmp
  tmp=$(mktemp "${TDH_BASE}/write.XXXXXX")
  cat >"$tmp"
  chmod "$mode" "$tmp"
  mv -f "$tmp" "$target"
}

generate_wg_keypair() {
  local name=$1
  local priv="${TDH_KEY_DIR}/${name}.key" pub="${TDH_KEY_DIR}/${name}.pub"
  if [[ ! -s $priv ]]; then
    wg genkey >"$priv"
    chmod 0600 "$priv"
  fi
  wg pubkey <"$priv" >"$pub"
  chmod 0644 "$pub"
}

generate_psk() {
  local name=$1
  local file="${TDH_KEY_DIR}/${name}.psk"
  if [[ ! -s $file ]]; then
    wg genpsk >"$file"
    chmod 0600 "$file"
  fi
}

generate_join_code() {
  local backend_role=$1 entry_pub psk token local_ip entry_ip wg_port
  case $backend_role in
    backend1)
      entry_pub=$(<"${TDH_KEY_DIR}/entry-b1.pub")
      psk=$(<"${TDH_KEY_DIR}/entry-b1.psk")
      token=$ENROLL_TOKEN_B1 local_ip="10.77.1.2" entry_ip="10.77.1.1" wg_port="51821"
      ;;
    backend2)
      entry_pub=$(<"${TDH_KEY_DIR}/entry-b2.pub")
      psk=$(<"${TDH_KEY_DIR}/entry-b2.psk")
      token=$ENROLL_TOKEN_B2 local_ip="10.77.2.2" entry_ip="10.77.2.1" wg_port="51822"
      ;;
    *) die "无效后端角色。" ;;
  esac
  env TDH_J_ROLE="$backend_role" TDH_J_ENTRY_PUB="$entry_pub" TDH_J_PSK="$psk" TDH_J_TOKEN="$token" \
  TDH_J_LOCAL_IP="$local_ip" TDH_J_ENTRY_IP="$entry_ip" TDH_J_WG_PORT="$wg_port" \
  CLUSTER_ID="$CLUSTER_ID" ENTRY_PUBLIC_IP="$ENTRY_PUBLIC_IP" TDH_J_PUBLIC_PORT="$PORT_A" \
  TDH_J_DOMAIN_A="$TDH_DOMAIN_A" TDH_J_DOMAIN_B="$TDH_DOMAIN_B" \
  SECRET_A="$SECRET_A" SECRET_B="$SECRET_B" JOIN_CREATED="$JOIN_CREATED" JOIN_EXPIRES="$JOIN_EXPIRES" \
  TELEMT_VERSION="$TELEMT_VERSION" TDH_VERSION="$TDH_PROTOCOL_VERSION" \
  TDH_CONFIG_SCHEMA="$TDH_CONFIG_SCHEMA" python3 - <<'PY'
import base64, hashlib, hmac, json, os
body = {
    "v": 4, "kind": "join", "cluster_id": os.environ["CLUSTER_ID"],
    "script_version": os.environ["TDH_VERSION"],
    "config_schema": int(os.environ["TDH_CONFIG_SCHEMA"]),
    "role": os.environ["TDH_J_ROLE"], "created": int(os.environ["JOIN_CREATED"]),
    "expires": int(os.environ["JOIN_EXPIRES"]), "entry_public_ip": os.environ["ENTRY_PUBLIC_IP"],
    "public_port": int(os.environ["TDH_J_PUBLIC_PORT"]),
    "domain_a": os.environ["TDH_J_DOMAIN_A"], "domain_b": os.environ["TDH_J_DOMAIN_B"],
    "secret_a": os.environ["SECRET_A"], "secret_b": os.environ["SECRET_B"],
    "entry_wg_public_key": os.environ["TDH_J_ENTRY_PUB"], "wg_psk": os.environ["TDH_J_PSK"],
    "local_wg_ip": os.environ["TDH_J_LOCAL_IP"], "entry_wg_ip": os.environ["TDH_J_ENTRY_IP"],
    "wg_port": int(os.environ["TDH_J_WG_PORT"]), "enrollment_token": os.environ["TDH_J_TOKEN"],
    "telemt_version": os.environ["TELEMT_VERSION"],
}
raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
sig = hmac.new(body["enrollment_token"].encode(), raw, hashlib.sha256).hexdigest()
outer = json.dumps({"body": body, "sig": sig}, sort_keys=True, separators=(",", ":")).encode()
print("TDH4." + base64.urlsafe_b64encode(outer).decode().rstrip("="))
PY
}

decode_join_code() {
  local code=$1
  env TDH_VERSION="$TDH_PROTOCOL_VERSION" TDH_CONFIG_SCHEMA="$TDH_CONFIG_SCHEMA" python3 - "$code" <<'PY'
import base64, hashlib, hmac, ipaddress, json, os, sys, time
code = sys.argv[1].strip()
if not code.startswith("TDH4."):
    raise SystemExit("加入码前缀错误")
if len(code) > 16384:
    raise SystemExit("加入码过长")
try:
    data = code[5:]
    outer = json.loads(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)))
    if set(outer) != {"body", "sig"}: raise ValueError("外层字段错误")
    body, sig = outer["body"], outer["sig"]
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    expected = hmac.new(body["enrollment_token"].encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected): raise ValueError("签名不匹配")
    if body["kind"] != "join" or body["v"] != 4: raise ValueError("版本错误")
    if body["script_version"] != os.environ["TDH_VERSION"]: raise ValueError("节点协议版本不一致")
    if int(body["config_schema"]) != int(os.environ["TDH_CONFIG_SCHEMA"]): raise ValueError("配置架构不一致")
    if body["role"] not in ("backend1", "backend2"): raise ValueError("角色错误")
    if int(body["expires"]) < int(time.time()): raise ValueError("加入码已过期")
    ipaddress.IPv4Address(body["entry_public_ip"])
    for key in ("public_port", "wg_port"):
        if not 1 <= int(body[key]) <= 65535: raise ValueError("端口错误")
    for key in ("domain_a", "domain_b"):
        value = body[key]
        if not isinstance(value, str) or not value or len(value) > 253 or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for c in value):
            raise ValueError("域名错误")
    for key in ("secret_a", "secret_b"):
        if len(body[key]) != 32 or any(c not in "0123456789abcdef" for c in body[key]):
            raise ValueError("Secret 错误")
except Exception as exc:
    raise SystemExit(f"加入码无效：{exc}")
keys = ("cluster_id","role","entry_public_ip","public_port","domain_a","domain_b","secret_a","secret_b",
        "entry_wg_public_key","wg_psk","local_wg_ip","entry_wg_ip","wg_port","enrollment_token","telemt_version",
        "script_version","config_schema")
for key in keys: print(body[key])
PY
}

generate_response_code() {
  local backend_wg_pub=$1
  env TDH_R_ROLE="$TDH_ROLE" TDH_R_BACKEND_IP="$BACKEND_PUBLIC_IP" TDH_R_WG_PUB="$backend_wg_pub" \
  TDH_R_WG_PORT="$BACKEND_WG_PORT" TDH_R_LINK_A="$RESPONSE_LINK_A" TDH_R_LINK_B="$RESPONSE_LINK_B" \
  TDH_R_MAX_CONNECTIONS="$BACKEND_MAX_CONNECTIONS" TDH_VERSION="$TDH_PROTOCOL_VERSION" \
  TDH_CONFIG_SCHEMA="$TDH_CONFIG_SCHEMA" CLUSTER_ID="$CLUSTER_ID" ENROLL_TOKEN="$ENROLL_TOKEN" python3 - <<'PY'
import base64, hashlib, hmac, json, os, time
body = {
    "v": 4, "kind": "response", "cluster_id": os.environ["CLUSTER_ID"],
    "script_version": os.environ["TDH_VERSION"],
    "config_schema": int(os.environ["TDH_CONFIG_SCHEMA"]),
    "role": os.environ["TDH_R_ROLE"], "created": int(time.time()),
    "backend_public_ip": os.environ["TDH_R_BACKEND_IP"],
    "wg_public_key": os.environ["TDH_R_WG_PUB"], "wg_port": int(os.environ["TDH_R_WG_PORT"]),
    "link_a": os.environ["TDH_R_LINK_A"], "link_b": os.environ["TDH_R_LINK_B"],
    "max_connections": int(os.environ["TDH_R_MAX_CONNECTIONS"]),
}
raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
sig = hmac.new(os.environ["ENROLL_TOKEN"].encode(), raw, hashlib.sha256).hexdigest()
outer = json.dumps({"body": body, "sig": sig}, sort_keys=True, separators=(",", ":")).encode()
print("TDHR4." + base64.urlsafe_b64encode(outer).decode().rstrip("="))
PY
}

decode_response_code() {
  local code=$1 token_b1=${2:-} token_b2=${3:-}
  env TDH_VERSION="$TDH_PROTOCOL_VERSION" TDH_CONFIG_SCHEMA="$TDH_CONFIG_SCHEMA" \
    TDH_TOKEN_B1="$token_b1" TDH_TOKEN_B2="$token_b2" python3 - "$code" <<'PY'
import base64, hashlib, hmac, ipaddress, json, os, sys
code = sys.argv[1].strip()
if not code.startswith("TDHR4."): raise SystemExit("回执码前缀错误")
if len(code) > 16384: raise SystemExit("回执码过长")
try:
    data = code[6:]
    outer = json.loads(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)))
    if set(outer) != {"body", "sig"}: raise ValueError("外层字段错误")
    body, sig = outer["body"], outer["sig"]
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    if body["kind"] != "response" or body["v"] != 4: raise ValueError("版本错误")
    if body["script_version"] != os.environ["TDH_VERSION"]: raise ValueError("节点协议版本不一致")
    if int(body["config_schema"]) != int(os.environ["TDH_CONFIG_SCHEMA"]): raise ValueError("配置架构不一致")
    if body["role"] not in ("backend1", "backend2"): raise ValueError("角色错误")
    token = os.environ["TDH_TOKEN_B1"] if body["role"] == "backend1" else os.environ["TDH_TOKEN_B2"]
    if len(token) != 64 or any(c not in "0123456789abcdef" for c in token): raise ValueError("本机登记令牌无效")
    expected = hmac.new(token.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected): raise ValueError("签名不匹配")
    ipaddress.IPv4Address(body["backend_public_ip"])
    if not 1 <= int(body["wg_port"]) <= 65535: raise ValueError("端口错误")
    if not 1000 <= int(body["max_connections"]) <= 30000: raise ValueError("容量上限错误")
except Exception as exc:
    raise SystemExit(f"回执码无效：{exc}")
for key in ("cluster_id","role","backend_public_ip","wg_public_key","wg_port","link_a","link_b",
            "max_connections","script_version","config_schema"):
    print(body[key])
PY
}

normalize_and_validate_link() {
  local link=$1 expected_port=$2 expected_secret=$3 expected_domain=$4
  TDH_LINK=$link TDH_EXPECT_IP=$ENTRY_PUBLIC_IP TDH_EXPECT_PORT=$expected_port \
  TDH_EXPECT_SECRET=$expected_secret TDH_EXPECT_DOMAIN=$expected_domain python3 - <<'PY'
import os, urllib.parse
u = urllib.parse.urlparse(os.environ["TDH_LINK"])
if u.fragment or u.params or u.username or u.password:
    raise SystemExit("链接包含不允许的附加部分")
if u.scheme == "tg" and u.netloc == "proxy" and u.path in ("", "/"): pass
elif u.scheme == "https" and u.netloc == "t.me" and u.path == "/proxy": pass
else: raise SystemExit("链接协议或路径错误")
q = urllib.parse.parse_qs(u.query, strict_parsing=True)
for key in ("server", "port", "secret"):
    if len(q.get(key, [])) != 1: raise SystemExit(f"链接缺少 {key}")
if set(q) != {"server", "port", "secret"}: raise SystemExit("链接包含未知参数")
expected_full = "ee" + os.environ["TDH_EXPECT_SECRET"] + os.environ["TDH_EXPECT_DOMAIN"].encode().hex()
if q["server"][0] != os.environ["TDH_EXPECT_IP"]: raise SystemExit("链接入口 IP 不匹配")
if q["port"][0] != os.environ["TDH_EXPECT_PORT"]: raise SystemExit("链接端口不匹配")
secret = q["secret"][0].lower()
if secret != expected_full.lower(): raise SystemExit("链接 Secret/SNI 不匹配")
print("https://t.me/proxy?" + urllib.parse.urlencode({
    "server": q["server"][0], "port": q["port"][0], "secret": secret
}))
PY
}

download_telemt() {
  local arch asset expected url tmp found actual
  case $(uname -m) in
    x86_64|amd64) arch="x86_64"; expected=$TELEMT_SHA256_AMD64 ;;
    aarch64|arm64) arch="aarch64"; expected=$TELEMT_SHA256_ARM64 ;;
    *) die "Telemt 不支持当前架构。" ;;
  esac
  asset="telemt-${arch}-linux-gnu.tar.gz"
  url="https://github.com/telemt/telemt/releases/download/${TELEMT_VERSION}/${asset}"
  tmp=$(mktemp -d)
  info "下载 Telemt ${TELEMT_VERSION} 官方构建……"
  curl -fL --retry 5 --retry-all-errors --connect-timeout 10 --max-time 180 "$url" -o "${tmp}/${asset}"
  actual=$(sha256sum "${tmp}/${asset}" | awk '{print $1}')
  [[ $actual == "$expected" ]] || { rm -rf "$tmp"; die "Telemt SHA256 校验失败。"; }
  tar -xzf "${tmp}/${asset}" -C "$tmp"
  found=$(find "$tmp" -type f -name telemt -perm /111 -print -quit)
  [[ -n $found ]] || { rm -rf "$tmp"; die "发布包中未找到 telemt 可执行文件。"; }
  install -d -m 0755 "$(dirname "$TDH_BIN")"
  install -m 0755 "$found" "$TDH_BIN"
  rm -rf "$tmp"
  actual=$("$TDH_BIN" --version 2>/dev/null || true)
  [[ $actual == "telemt ${TELEMT_VERSION}" ]] || \
    die "Telemt 二进制版本异常：期望 ${TELEMT_VERSION}，实际 ${actual:-无法运行}。"
}

ensure_telemt_user() {
  local _name _password uid user_gid group_gid _comment _home shell members
  if ! getent group telemt >/dev/null; then
    groupadd --system telemt
    CREATED_TELEMT_GROUP=1
    save_state
  fi
  if ! id telemt >/dev/null 2>&1; then
    useradd --system --gid telemt --home-dir "$TDH_WORK_ROOT" --shell /usr/sbin/nologin telemt
    CREATED_TELEMT_USER=1
    save_state
  fi
  IFS=: read -r _name _password uid user_gid _comment _home shell < <(getent passwd telemt)
  [[ $uid =~ ^[0-9]+$ && $user_gid =~ ^[0-9]+$ && $uid != 0 && $user_gid != 0 ]] || \
    die "telemt 服务账号 UID/GID 无效或指向 root。"
  [[ $shell == /usr/sbin/nologin || $shell == /bin/false ]] || \
    die "检测到可登录的同名 telemt 用户；为避免泄露 Secret，拒绝复用。"
  IFS=: read -r _name _password group_gid members < <(getent group telemt)
  [[ $group_gid =~ ^[0-9]+$ && $group_gid != 0 && $user_gid == "$group_gid" ]] || \
    die "telemt 用户的主组不是专用 telemt 组。"
  [[ -z $members || $members == telemt ]] || \
    die "telemt 服务组包含其他账号；为避免泄露 Secret，拒绝复用。"
}

remove_telemt_account() {
  if [[ $CREATED_TELEMT_USER == 1 ]] && id telemt >/dev/null 2>&1; then
    userdel telemt >/dev/null 2>&1 || true
  fi
  if [[ $CREATED_TELEMT_GROUP == 1 ]] && getent group telemt >/dev/null; then
    groupdel telemt >/dev/null 2>&1 || true
  fi
}

write_telemt_config() {
  local bind_ip=$1 bind_port=$2 api_port=$3 public_port=$4
  local config="${TDH_CONFIG_DIR}/telemt.toml" work="${TDH_WORK_ROOT}/telemt" tmp
  validate_connection_limit "${BACKEND_MAX_CONNECTIONS:-}" || die "后端容量上限无效。"
  validate_direct_buffer_budget "${BACKEND_BUFFER_BUDGET_BYTES:-}" || die "Direct-DC 缓冲预算无效。"
  install -d -o telemt -g telemt -m 0750 "$work" "${work}/tlsfront"
  tmp=$(mktemp "${TDH_CONFIG_DIR}/telemt.XXXXXX")
  cat >"$tmp" <<EOF
[general]
config_strict = true
fast_mode = true
use_middle_proxy = false
beobachten = false
log_level = "normal"
direct_relay_buffer_budget_max_bytes = ${BACKEND_BUFFER_BUDGET_BYTES}

[general.modes]
classic = false
secure = false
tls = true

[general.links]
show = "*"
public_host = "${ENTRY_PUBLIC_IP}"
public_port = ${public_port}

[server]
port = ${bind_port}
max_connections = ${BACKEND_MAX_CONNECTIONS}
listen_backlog = 4096
proxy_protocol = true
proxy_protocol_header_timeout_ms = 500
proxy_protocol_trusted_cidrs = ["${ENTRY_WG_IP}/32"]

[server.conntrack_control]
inline_conntrack_control = false

[server.api]
enabled = true
listen = "127.0.0.1:${api_port}"
whitelist = ["127.0.0.1/32", "::1/128"]
auth_header = "Bearer ${API_TOKEN}"
read_only = true
minimal_runtime_enabled = false
minimal_runtime_cache_ttl_ms = 1000

[[server.listeners]]
ip = "${bind_ip}"

[network]
ipv4 = true
ipv6 = false
prefer = 4

[censorship]
tls_domain = "${TDH_DOMAIN_A}"
tls_domains = ["${TDH_DOMAIN_B}"]
mask = true
mask_port = 443
mask_dynamic = true
unknown_sni_action = "mask"
tls_emulation = true
tls_front_dir = "tlsfront"

[access.users]
line_a = "${SECRET_A}"
line_b = "${SECRET_B}"

[[upstreams]]
type = "direct"
weight = 1
enabled = true
EOF
  chown root:telemt "$tmp"
  chmod 0640 "$tmp"
  mv -f "$tmp" "$config"
}

write_telemt_unit() {
  local interface=$1
  local config="${TDH_CONFIG_DIR}/telemt.toml" work="${TDH_WORK_ROOT}/telemt"
  cat >/etc/systemd/system/telemt-dual-hop-telemt.service <<EOF
[Unit]
Description=Telemt Dual-Hop backend
Wants=network-online.target
After=network-online.target wg-quick@${interface}.service
Requires=wg-quick@${interface}.service
StartLimitIntervalSec=0

[Service]
Type=simple
User=telemt
Group=telemt
WorkingDirectory=${work}
ExecStart=${TDH_BIN} ${config}
Restart=always
RestartSec=5s
UMask=0077
LogRateLimitIntervalSec=30s
LogRateLimitBurst=200
LimitNOFILE=262144
NoNewPrivileges=true
CapabilityBoundingSet=
AmbientCapabilities=
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
ReadWritePaths=${work}

[Install]
WantedBy=multi-user.target
EOF
  chmod 0644 /etc/systemd/system/telemt-dual-hop-telemt.service
}

write_health_agent() {
  local bind_ip=$1 interface=$2
  install -d -m 0755 /usr/local/lib/telemt-dual-hop
  printf '%s\n' "$API_TOKEN" | write_private_file "${TDH_CONFIG_DIR}/api.token" 0640
  chown root:telemt "${TDH_CONFIG_DIR}/api.token"
  cat >/usr/local/lib/telemt-dual-hop/health_agent.py <<'PY'
#!/usr/bin/env python3
import concurrent.futures, json, os, selectors, socket, threading, time, urllib.error, urllib.request

BIND = os.environ["TDH_BIND_IP"]
with open(os.environ["TDH_API_TOKEN_FILE"], encoding="ascii") as token_file:
    API_TOKEN = token_file.read().strip()
AGENT_PORT = int(os.environ["TDH_AGENT_PORT"])
API_PORT = int(os.environ["TDH_API_PORT"])
DCS = tuple(value for value in os.environ["TDH_DC_IPV4"].split(",") if value)
DC_PORT = int(os.environ["TDH_DC_PORT"])
API_READY = False
DC_READY = False
READY_LOCK = threading.Lock()
sel = selectors.DefaultSelector()
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind((BIND, AGENT_PORT)); sock.listen(64); sock.setblocking(False)
sel.register(sock, selectors.EVENT_READ)

def healthy(http, api_port):
    request = urllib.request.Request(
        f"http://127.0.0.1:{api_port}/v1/health/ready",
        headers={"Authorization": f"Bearer {API_TOKEN}"},
    )
    try:
        with http.open(request, timeout=2) as response:
            payload = json.load(response)
        return response.status == 200 and payload.get("ok") is True and payload.get("data", {}).get("ready") is True
    except (OSError, ValueError, urllib.error.URLError):
        return False

def monitor_api():
    global API_READY
    # Never inherit HTTP(S)_PROXY for the loopback API or expose its bearer token.
    http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while True:
        current = healthy(http, API_PORT)
        with READY_LOCK:
            API_READY = current
        time.sleep(1)

def probe_dc(address):
    try:
        with socket.create_connection((address, DC_PORT), timeout=2):
            return True
    except OSError:
        return False

def monitor_dcs():
    global DC_READY
    bad_streak = 0
    while True:
        if DCS:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(DCS)) as executor:
                current = all(executor.map(probe_dc, DCS))
        else:
            current = False
        with READY_LOCK:
            if current:
                bad_streak = 0
                DC_READY = True
            else:
                bad_streak += 1
                if not DC_READY or bad_streak >= 2:
                    DC_READY = False
        time.sleep(10)

threading.Thread(target=monitor_api, daemon=True).start()
threading.Thread(target=monitor_dcs, daemon=True).start()

while True:
    for key, _ in sel.select(timeout=30):
        listener = key.fileobj
        try:
            conn, _ = listener.accept()
        except (BlockingIOError, OSError):
            continue
        with conn:
            try:
                with READY_LOCK:
                    current = API_READY and DC_READY
                conn.sendall(b"up\n" if current else b"down\n")
            except OSError:
                pass
PY
  chmod 0755 /usr/local/lib/telemt-dual-hop/health_agent.py
  cat >/etc/systemd/system/telemt-dual-hop-health-agent.service <<EOF
[Unit]
Description=Telemt Dual-Hop HAProxy health agent
After=network-online.target wg-quick@${interface}.service telemt-dual-hop-telemt.service
Requires=wg-quick@${interface}.service
Wants=telemt-dual-hop-telemt.service
StartLimitIntervalSec=0

[Service]
Type=simple
User=telemt
Group=telemt
Environment=TDH_BIND_IP=${bind_ip}
Environment=TDH_AGENT_PORT=${TDH_AGENT_PORT}
Environment=TDH_API_PORT=${TDH_API_PORT}
Environment=TDH_DC_IPV4=${TDH_TG_DC_IPV4}
Environment=TDH_DC_PORT=${TDH_TG_DC_PORT}
Environment=TDH_API_TOKEN_FILE=${TDH_CONFIG_DIR}/api.token
ExecStart=/usr/bin/python3 /usr/local/lib/telemt-dual-hop/health_agent.py
Restart=always
RestartSec=5s
UMask=0077
LogRateLimitIntervalSec=30s
LogRateLimitBurst=100
NoNewPrivileges=true
CapabilityBoundingSet=
AmbientCapabilities=
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
PrivateDevices=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true

[Install]
WantedBy=multi-user.target
EOF
  chmod 0644 /etc/systemd/system/telemt-dual-hop-health-agent.service
}

write_backend_wg() {
  local interface=$1 local_ip=$2 entry_ip=$3 entry_pub=$4 psk=$5 listen_port=$6
  local private_file="${TDH_KEY_DIR}/backend.key" config="/etc/wireguard/${interface}.conf"
  [[ -s $private_file ]] || { wg genkey >"$private_file"; chmod 0600 "$private_file"; }
  install -d -m 0700 /etc/wireguard
  cat >"$config" <<EOF
[Interface]
Address = ${local_ip}/30
ListenPort = ${listen_port}
PrivateKey = $(<"$private_file")
SaveConfig = false

[Peer]
PublicKey = ${entry_pub}
PresharedKey = ${psk}
AllowedIPs = ${entry_ip}/32
EOF
  chmod 0600 "$config"
}

write_entry_wg() {
  local idx=$1 peer_ip peer_pub peer_port peer_public interface local_ip private psk
  if [[ $idx == 1 ]]; then
    peer_ip="10.77.1.2" peer_pub=$B1_WG_PUB peer_port=$B1_WG_PORT peer_public=$B1_PUBLIC_IP
    interface="tdh1" local_ip="10.77.1.1" private="${TDH_KEY_DIR}/entry-b1.key" psk="${TDH_KEY_DIR}/entry-b1.psk"
  else
    peer_ip="10.77.2.2" peer_pub=$B2_WG_PUB peer_port=$B2_WG_PORT peer_public=$B2_PUBLIC_IP
    interface="tdh2" local_ip="10.77.2.1" private="${TDH_KEY_DIR}/entry-b2.key" psk="${TDH_KEY_DIR}/entry-b2.psk"
  fi
  cat >"/etc/wireguard/${interface}.conf" <<EOF
[Interface]
Address = ${local_ip}/30
PrivateKey = $(<"$private")
SaveConfig = false

[Peer]
PublicKey = ${peer_pub}
PresharedKey = $(<"$psk")
Endpoint = ${peer_public}:${peer_port}
AllowedIPs = ${peer_ip}/32
PersistentKeepalive = 25
EOF
  chmod 0600 "/etc/wireguard/${interface}.conf"
}

ufw_is_active() {
  command -v ufw >/dev/null 2>&1 && \
    ufw status 2>/dev/null | awk '$0 == "Status: active" {active=1} END {exit !active}'
}

open_firewall_entry() {
  local status
  if ufw_is_active; then
    status=$(ufw status)
    if ! awk -v target="${PORT_A}/tcp" '$1 == target && $2 == "ALLOW" {found=1} END {exit !found}' <<<"$status"; then
      ufw allow "$PORT_A/tcp" comment 'telemt-dual-hop A' >/dev/null
      UFW_ADDED_A=1
      save_state
    fi
  fi
}

open_firewall_backend() {
  local status
  if ufw_is_active; then
    status=$(ufw status)
    if ! awk -v target="${BACKEND_WG_PORT}/udp" -v source="$ENTRY_PUBLIC_IP" \
      '$1 == target && index($0, source) {found=1} END {exit !found}' <<<"$status"; then
      ufw allow from "$ENTRY_PUBLIC_IP" to any port "$BACKEND_WG_PORT" proto udp comment 'telemt-dual-hop WG' >/dev/null
      UFW_ADDED_WG=1
      save_state
    fi
    local port
    for port in "$TDH_TELEMT_PORT" "$TDH_AGENT_PORT"; do
      status=$(ufw status)
      if ! awk -v target="${port}/tcp" -v interface="$WG_INTERFACE" -v source="$ENTRY_WG_IP" \
        '$1 == target && $2 == "on" && $3 == interface && index($0, source) {found=1} END {exit !found}' <<<"$status"; then
        ufw allow in on "$WG_INTERFACE" from "$ENTRY_WG_IP" to "$LOCAL_WG_IP" port "$port" proto tcp \
          comment 'telemt-dual-hop private' >/dev/null
        case $port in
          "$TDH_TELEMT_PORT") UFW_ADDED_P24431=1 ;;
          "$TDH_AGENT_PORT") UFW_ADDED_P19101=1 ;;
        esac
        save_state
      fi
    done
  fi
}

tuning_is_active() {
  [[ $(sysctl -n net.core.default_qdisc 2>/dev/null || true) == fq && \
     $(sysctl -n net.ipv4.tcp_congestion_control 2>/dev/null || true) == bbr ]]
}

apply_tuning() {
  local somax syn_backlog
  if [[ -z $PREV_SOMAX ]]; then
    PREV_QDISC=$(sysctl -n net.core.default_qdisc 2>/dev/null || true)
    PREV_CC=$(sysctl -n net.ipv4.tcp_congestion_control 2>/dev/null || true)
    PREV_SOMAX=$(sysctl -n net.core.somaxconn 2>/dev/null || true)
    PREV_SYN_BACKLOG=$(sysctl -n net.ipv4.tcp_max_syn_backlog 2>/dev/null || true)
    PREV_KEEPALIVE_TIME=$(sysctl -n net.ipv4.tcp_keepalive_time 2>/dev/null || true)
    PREV_KEEPALIVE_INTVL=$(sysctl -n net.ipv4.tcp_keepalive_intvl 2>/dev/null || true)
    PREV_KEEPALIVE_PROBES=$(sysctl -n net.ipv4.tcp_keepalive_probes 2>/dev/null || true)
    save_state
  fi
  somax=$(sysctl -n net.core.somaxconn 2>/dev/null || echo 4096)
  syn_backlog=$(sysctl -n net.ipv4.tcp_max_syn_backlog 2>/dev/null || echo 4096)
  if (( somax < 65535 )); then somax=65535; fi
  if (( syn_backlog < 32768 )); then syn_backlog=32768; fi
  modprobe sch_fq >/dev/null 2>&1 || die "当前内核无法加载 sch_fq；请更换为 Ubuntu 22.04 官方内核后重试。"
  modprobe tcp_bbr >/dev/null 2>&1 || die "当前内核无法加载 tcp_bbr；请更换为 Ubuntu 22.04 官方内核后重试。"
  sysctl -n net.ipv4.tcp_available_congestion_control 2>/dev/null | grep -qw bbr || \
    die "当前内核未提供 BBR；为避免静默降级，安装已停止。"
  install -d -m 0755 /etc/modules-load.d
  cat >/etc/modules-load.d/90-telemt-dual-hop.conf <<'EOF'
sch_fq
tcp_bbr
EOF
  cat >/etc/sysctl.d/90-telemt-dual-hop.conf <<EOF
# Telemt Dual-Hop conservative tuning
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
net.core.somaxconn = ${somax}
net.ipv4.tcp_max_syn_backlog = ${syn_backlog}
net.ipv4.tcp_keepalive_time = 600
net.ipv4.tcp_keepalive_intvl = 30
net.ipv4.tcp_keepalive_probes = 5
EOF
  sysctl -p /etc/sysctl.d/90-telemt-dual-hop.conf >/dev/null || \
    die "无法应用 BBR/fq 或 TCP 调优参数。"
  tuning_is_active || die "BBR/fq 写入后未实际生效；安装已停止。"
  ok "BBR + fq 已启用并完成实时校验。"
}

restore_tuning() {
  if [[ -n $PREV_QDISC ]]; then sysctl -w "net.core.default_qdisc=${PREV_QDISC}" >/dev/null 2>&1 || true; fi
  if [[ -n $PREV_CC ]]; then sysctl -w "net.ipv4.tcp_congestion_control=${PREV_CC}" >/dev/null 2>&1 || true; fi
  if [[ -n $PREV_SOMAX ]]; then sysctl -w "net.core.somaxconn=${PREV_SOMAX}" >/dev/null 2>&1 || true; fi
  if [[ -n $PREV_SYN_BACKLOG ]]; then sysctl -w "net.ipv4.tcp_max_syn_backlog=${PREV_SYN_BACKLOG}" >/dev/null 2>&1 || true; fi
  if [[ -n $PREV_KEEPALIVE_TIME ]]; then sysctl -w "net.ipv4.tcp_keepalive_time=${PREV_KEEPALIVE_TIME}" >/dev/null 2>&1 || true; fi
  if [[ -n $PREV_KEEPALIVE_INTVL ]]; then sysctl -w "net.ipv4.tcp_keepalive_intvl=${PREV_KEEPALIVE_INTVL}" >/dev/null 2>&1 || true; fi
  if [[ -n $PREV_KEEPALIVE_PROBES ]]; then sysctl -w "net.ipv4.tcp_keepalive_probes=${PREV_KEEPALIVE_PROBES}" >/dev/null 2>&1 || true; fi
}

wireguard_handshake_is_fresh() {
  local interface=$1 max_age=${2:-180} latest now
  latest=$(wg show "$interface" latest-handshakes 2>/dev/null | awk 'BEGIN{m=0} $2>m{m=$2} END{print m}')
  [[ $latest =~ ^[0-9]+$ ]] || return 1
  now=$(date +%s)
  (( latest > 0 && now >= latest && now - latest <= max_age ))
}

telemt_binary_is_expected() {
  [[ -x $TDH_BIN && $("$TDH_BIN" --version 2>/dev/null || true) == "telemt ${TELEMT_VERSION}" ]]
}

write_haproxy_config() {
  local maxconn dns tmp
  maxconn=$(configured_cluster_connection_limit) || die "集群容量参数无效。"
  dns=$(awk '/^nameserver[[:space:]]+[0-9.]+/{print $2; exit}' /etc/resolv.conf)
  validate_ipv4 "${dns:-}" 2>/dev/null || dns="1.1.1.1"
  tmp=$(mktemp "${TDH_BASE}/haproxy.cfg.XXXXXX")
  cat >"$tmp" <<EOF
global
    log stdout format raw local0 warning
    zero-warning
    maxconn ${maxconn}
    stats socket /run/telemt-dual-hop/haproxy.sock mode 600 level admin
    user haproxy
    group haproxy

defaults
    log global
    mode tcp
    option dontlog-normal
    option clitcpka
    option srvtcpka
    timeout connect 5s
    timeout client 12h
    timeout server 12h
    timeout check 3s

resolvers system_dns
    nameserver local ${dns}:53
    resolve_retries 3
    timeout resolve 2s
    timeout retry 1s
    hold valid 10m

frontend mtp_tls
    bind 0.0.0.0:${PORT_A}
    maxconn ${maxconn}
    option tcp-smart-accept
    tcp-request inspect-delay 3s
    tcp-request content accept if { req.ssl_hello_type 1 }
    acl sni_line_a req.ssl_sni -i ${TDH_DOMAIN_A}
    acl sni_line_b req.ssl_sni -i ${TDH_DOMAIN_B}
    use_backend telemt_a if sni_line_a
    use_backend telemt_b if sni_line_b
    default_backend tls_fallback_unknown

backend telemt_a
    server vps1 10.77.1.2:${TDH_TELEMT_PORT} check port ${TDH_AGENT_PORT} inter 2s fall 3 rise 2 send-proxy-v2 agent-check agent-port ${TDH_AGENT_PORT} agent-inter 2s
    server vps2 10.77.2.2:${TDH_TELEMT_PORT} check port ${TDH_AGENT_PORT} inter 2s fall 3 rise 2 send-proxy-v2 agent-check agent-port ${TDH_AGENT_PORT} agent-inter 2s backup
    server apple_a ${TDH_DOMAIN_A}:443 check inter 10s fall 3 rise 2 backup resolvers system_dns resolve-prefer ipv4 init-addr libc,none

backend telemt_b
    server vps2 10.77.2.2:${TDH_TELEMT_PORT} check port ${TDH_AGENT_PORT} inter 2s fall 3 rise 2 send-proxy-v2 agent-check agent-port ${TDH_AGENT_PORT} agent-inter 2s
    server vps1 10.77.1.2:${TDH_TELEMT_PORT} check port ${TDH_AGENT_PORT} inter 2s fall 3 rise 2 send-proxy-v2 agent-check agent-port ${TDH_AGENT_PORT} agent-inter 2s backup
    server apple_b ${TDH_DOMAIN_B}:443 check inter 10s fall 3 rise 2 backup resolvers system_dns resolve-prefer ipv4 init-addr libc,none

backend tls_fallback_unknown
    server apple_unknown ${TDH_DOMAIN_A}:443 check inter 10s fall 3 rise 2 resolvers system_dns resolve-prefer ipv4 init-addr libc,none
EOF
  chmod 0640 "$tmp"
  chown root:haproxy "$tmp"
  if ! haproxy -c -f "$tmp"; then
    rm -f "$tmp"
    die "HAProxy 配置校验失败；保留原有有效配置。"
  fi
  mv -f "$tmp" "$TDH_HAPROXY_CFG"
}

write_haproxy_unit() {
  cat >/etc/systemd/system/telemt-dual-hop-haproxy.service <<EOF
[Unit]
Description=Telemt Dual-Hop dedicated HAProxy
After=network-online.target wg-quick@tdh1.service wg-quick@tdh2.service
Wants=network-online.target wg-quick@tdh1.service wg-quick@tdh2.service
StartLimitIntervalSec=0

[Service]
Type=notify
RuntimeDirectory=telemt-dual-hop
RuntimeDirectoryMode=0750
ExecStart=/usr/sbin/haproxy -Ws -f ${TDH_HAPROXY_CFG} -p /run/telemt-dual-hop/haproxy.pid
ExecReload=/usr/sbin/haproxy -c -f ${TDH_HAPROXY_CFG}
ExecReload=/bin/kill -USR2 \$MAINPID
KillMode=mixed
Restart=always
RestartSec=5s
UMask=0077
LogRateLimitIntervalSec=30s
LogRateLimitBurst=100
LimitNOFILE=524288
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
ReadWritePaths=/run/telemt-dual-hop

[Install]
WantedBy=multi-user.target
EOF
  chmod 0644 /etc/systemd/system/telemt-dual-hop-haproxy.service
}

query_telemt_link() {
  local api_port=$1 username=$2 domain=$3 domain_hex
  local -a auth=()
  if [[ -n $API_TOKEN ]]; then auth=(-H "Authorization: Bearer ${API_TOKEN}"); fi
  domain_hex=$(printf '%s' "$domain" | od -An -tx1 | tr -d ' \n')
  curl -fsS --connect-timeout 2 --max-time 5 "${auth[@]}" "http://127.0.0.1:${api_port}/v1/users" | \
    jq -er --arg username "$username" --arg suffix "$domain_hex" \
      'first(.data[] | select(.username == $username) | .links.tls[]? | select(ascii_downcase | endswith($suffix)))'
}

wait_for_link() {
  local api_port=$1 username=$2 domain=$3 i link
  for ((i=1; i<=60; i++)); do
    link=$(query_telemt_link "$api_port" "$username" "$domain" 2>/dev/null || true)
    if [[ -n $link ]]; then printf '%s\n' "$link"; return 0; fi
    sleep 1
  done
  journalctl -u telemt-dual-hop-telemt.service -n 50 --no-pager >&2 || true
  return 1
}

install_backend() {
  local requested_role=$1 code decoded backend_priv backend_pub
  local -a fields
  [[ ! -f $TDH_STATE ]] || die "本机已经初始化；请运行管理菜单。"
  check_os
  code=$(prompt_secret "请粘贴入口生成的 ${requested_role} Join Code")
  decoded=$(decode_join_code "$code") || die "无法解析 Join Code。"
  mapfile -t fields <<<"$decoded"
  (( ${#fields[@]} == 17 )) || die "Join Code 字段数量异常。"
  CLUSTER_ID=${fields[0]} TDH_ROLE=${fields[1]} ENTRY_PUBLIC_IP=${fields[2]}
  CONFIG_SCHEMA=$TDH_CONFIG_SCHEMA INSTALLED_VERSION=$TDH_VERSION PORT_A=${fields[3]} PORT_B=${fields[3]}
  [[ ${fields[4]} == "$TDH_DOMAIN_A" ]] || die "线路 A 伪装域名不是 ${TDH_DOMAIN_A}。"
  [[ ${fields[5]} == "$TDH_DOMAIN_B" ]] || die "线路 B 伪装域名不是 ${TDH_DOMAIN_B}。"
  SECRET_A=${fields[6]} SECRET_B=${fields[7]} ENTRY_WG_PUBLIC_KEY=${fields[8]}
  local wg_psk=${fields[9]}
  LOCAL_WG_IP=${fields[10]} ENTRY_WG_IP=${fields[11]} BACKEND_WG_PORT=${fields[12]}
  ENROLL_TOKEN=${fields[13]}
  [[ ${fields[14]} == "$TELEMT_VERSION" ]] || die "Join Code 要求的 Telemt 版本与脚本不一致。"
  [[ ${fields[15]} == "$TDH_PROTOCOL_VERSION" && ${fields[16]} == "$TDH_CONFIG_SCHEMA" ]] || \
    die "Join Code 与当前配置协议不一致，请先更新三台机器的管理脚本。"
  [[ $TDH_ROLE == "$requested_role" ]] || die "Join Code 角色为 $TDH_ROLE，不是 $requested_role。"
  [[ $CLUSTER_ID =~ ^[0-9a-f]{24}$ ]] || die "集群 ID 格式无效。"
  [[ $ENROLL_TOKEN =~ ^[0-9a-f]{64}$ ]] || die "加入令牌格式无效。"
  validate_public_ipv4 "$ENTRY_PUBLIC_IP" || die "入口必须是可路由的公网 IPv4。"
  validate_wg_key "$ENTRY_WG_PUBLIC_KEY" || die "入口 WireGuard 公钥无效。"
  validate_wg_key "$wg_psk" || die "WireGuard PSK 无效。"
  if ! validate_hex32 "$SECRET_A" || ! validate_hex32 "$SECRET_B"; then
    die "Secret 格式无效。"
  fi
  [[ $SECRET_A != "$SECRET_B" ]] || die "两条线路不能使用相同 Secret。"
  [[ $PORT_A == "$TDH_PUBLIC_PORT" && $PORT_B == "$TDH_PUBLIC_PORT" ]] || \
    die "双 SNI 版本要求两条线路都使用 TCP ${TDH_PUBLIC_PORT}。"
  if [[ $requested_role == backend1 ]]; then
    [[ $LOCAL_WG_IP == 10.77.1.2 && $ENTRY_WG_IP == 10.77.1.1 ]] || die "VPS1 隧道地址异常。"
  else
    [[ $LOCAL_WG_IP == 10.77.2.2 && $ENTRY_WG_IP == 10.77.2.1 ]] || die "VPS2 隧道地址异常。"
  fi
  # Validate the Join Code before a potentially long package installation so
  # a paste/role mistake fails fast without changing the machine.
  install_packages backend
  WG_INTERFACE=$([[ $requested_role == backend1 ]] && echo tdh-b1 || echo tdh-b2)
  tunnel_network_is_available "${LOCAL_WG_IP%.*}.0/30" "$WG_INTERFACE" || \
    die "固定 WireGuard 网段与本机现有网络冲突；为避免破坏路由，安装已停止。"

  select_public_ipv4 BACKEND_PUBLIC_IP "后端 VPS 公网 IPv4"
  validate_public_ipv4 "$BACKEND_PUBLIC_IP" || die "后端必须是可路由的公网 IPv4。"
  [[ $BACKEND_PUBLIC_IP != "$ENTRY_PUBLIC_IP" ]] || die "入口与后端不能使用同一个公网 IPv4。"
  select_wireguard_port BACKEND_WG_PORT "$BACKEND_WG_PORT"
  [[ ! -e /etc/systemd/system/telemt-dual-hop-telemt.service && \
     ! -e /etc/systemd/system/telemt-a.service && ! -e /etc/systemd/system/telemt-b.service ]] || \
    die "检测到同名 Telemt systemd 服务；请先确认其来源。"
  [[ ! -e "/etc/wireguard/tdh-b1.conf" && ! -e "/etc/wireguard/tdh-b2.conf" ]] || \
    die "检测到残留的 tdh-b1/tdh-b2 WireGuard 配置。"

  check_front_domain "$TDH_DOMAIN_A"
  check_front_domain "$TDH_DOMAIN_B"
  TX_ACTIVE=1 TX_KIND=$requested_role
  init_dirs
  API_TOKEN=$(rand_hex 32)
  BACKEND_MAX_CONNECTIONS=$(calculate_connection_limit) || die "本机资源检测失败；至少需要 1 vCPU 和 768 MiB RAM。"
  BACKEND_BUFFER_BUDGET_BYTES=$(calculate_direct_buffer_budget) || die "无法计算 Direct-DC 缓冲预算。"
  info "已按本机 CPU/RAM 设置连接上限 ${BACKEND_MAX_CONNECTIONS}，Direct-DC 缓冲预算 $(( BACKEND_BUFFER_BUDGET_BYTES / 1024 / 1024 )) MiB。"
  save_state
  write_backend_wg "$WG_INTERFACE" "$LOCAL_WG_IP" "$ENTRY_WG_IP" "$ENTRY_WG_PUBLIC_KEY" "$wg_psk" "$BACKEND_WG_PORT"
  ensure_telemt_user
  save_state
  download_telemt
  write_telemt_config "$LOCAL_WG_IP" "$TDH_TELEMT_PORT" "$TDH_API_PORT" "$PORT_A"
  write_telemt_unit "$WG_INTERFACE"
  write_health_agent "$LOCAL_WG_IP" "$WG_INTERFACE"
  open_firewall_backend
  apply_tuning
  systemctl daemon-reload
  systemctl enable --now "wg-quick@${WG_INTERFACE}.service"
  systemctl enable --now telemt-dual-hop-telemt.service
  systemctl enable --now telemt-dual-hop-health-agent.service

  RESPONSE_LINK_A=$(wait_for_link "$TDH_API_PORT" line_a "$TDH_DOMAIN_A") || die "Telemt 未能生成线路 A 连接链接。"
  RESPONSE_LINK_B=$(wait_for_link "$TDH_API_PORT" line_b "$TDH_DOMAIN_B") || die "Telemt 未能生成线路 B 连接链接。"
  normalize_and_validate_link "$RESPONSE_LINK_A" "$PORT_A" "$SECRET_A" "$TDH_DOMAIN_A" >/dev/null || die "线路 A 链接校验失败。"
  normalize_and_validate_link "$RESPONSE_LINK_B" "$PORT_B" "$SECRET_B" "$TDH_DOMAIN_B" >/dev/null || die "线路 B 链接校验失败。"

  backend_priv="${TDH_KEY_DIR}/backend.key"
  backend_pub=$(wg pubkey <"$backend_priv")
  save_state
  install_self
  TX_ACTIVE=0 TX_KIND=""
  ok "$requested_role 安装完成。"
  title "请把下面整段回执码复制到入口 VPS"
  generate_response_code "$backend_pub"
}

show_join_codes() {
  local now
  load_state || die "入口尚未初始化。"
  [[ $TDH_ROLE == entry ]] || die "只有入口可以生成 Join Code。"
  require_current_state
  now=$(date +%s)
  if (( now > JOIN_EXPIRES )); then
    JOIN_CREATED=$now JOIN_EXPIRES=$(( now + 604800 ))
    [[ $B1_REGISTERED == 1 ]] || ENROLL_TOKEN_B1=$(rand_hex 32)
    [[ $B2_REGISTERED == 1 ]] || ENROLL_TOKEN_B2=$(rand_hex 32)
    save_state
    warn "旧 Join Code 已过期，已为未注册后端生成新码。"
  fi
  if [[ $B1_REGISTERED != 1 ]]; then
    title "后端 VPS1 Join Code"
    generate_join_code backend1
  else
    ok "VPS1 已注册。"
  fi
  if [[ $B2_REGISTERED != 1 ]]; then
    title "后端 VPS2 Join Code"
    generate_join_code backend2
  else
    ok "VPS2 已注册。"
  fi
}

init_entry() {
  [[ ! -f $TDH_STATE ]] || die "本机已经初始化；请运行管理菜单。"
  check_os
  if systemctl is-active --quiet haproxy.service 2>/dev/null; then
    die "检测到已有 haproxy.service 正在运行。为避免破坏现有业务，请先确认并停止它。"
  fi
  install_packages entry
  systemctl disable --now haproxy.service >/dev/null 2>&1 || true
  select_public_ipv4 ENTRY_PUBLIC_IP "入口 VPS 公网 IPv4"
  validate_public_ipv4 "$ENTRY_PUBLIC_IP" || die "入口必须是可路由的公网 IPv4。"
  PORT_A=$TDH_PUBLIC_PORT PORT_B=$TDH_PUBLIC_PORT
  port_is_free_tcp "$TDH_PUBLIC_PORT" || die "TCP ${TDH_PUBLIC_PORT} 已被占用；双 SNI 模式必须独占该端口。"
  [[ ! -e /etc/systemd/system/telemt-dual-hop-haproxy.service ]] || die "检测到残留入口服务。"
  [[ ! -e /etc/wireguard/tdh1.conf && ! -e /etc/wireguard/tdh2.conf ]] || die "检测到残留入口 WireGuard 配置。"
  check_front_domain "$TDH_DOMAIN_A"
  check_front_domain "$TDH_DOMAIN_B"
  check_china_dns_apple_owned "$TDH_DOMAIN_A"
  check_china_dns_apple_owned "$TDH_DOMAIN_B"

  TX_ACTIVE=1 TX_KIND=entry
  init_dirs
  TDH_ROLE="entry"
  CONFIG_SCHEMA=$TDH_CONFIG_SCHEMA
  INSTALLED_VERSION=$TDH_VERSION
  CLUSTER_ID=$(rand_hex 12)
  SECRET_A=$(rand_hex 16)
  SECRET_B=$(rand_hex 16)
  while [[ $SECRET_B == "$SECRET_A" ]]; do SECRET_B=$(rand_hex 16); done
  ENTRY_MAX_CONNECTIONS=$(calculate_connection_limit) || die "本机资源检测失败；至少需要 1 vCPU 和 768 MiB RAM。"
  info "已按本机 CPU/RAM 设置入口连接上限：${ENTRY_MAX_CONNECTIONS}；录入后端后还会取三台最小值。"
  JOIN_CREATED=$(date +%s)
  JOIN_EXPIRES=$(( JOIN_CREATED + 604800 ))
  ENROLL_TOKEN_B1=$(rand_hex 32)
  ENROLL_TOKEN_B2=$(rand_hex 32)
  B1_REGISTERED=0 B2_REGISTERED=0
  generate_wg_keypair entry-b1
  generate_wg_keypair entry-b2
  generate_psk entry-b1
  generate_psk entry-b2
  save_state
  install_self
  TX_ACTIVE=0 TX_KIND=""
  ok "入口初始化完成；两条线路将共享 TCP ${TDH_PUBLIC_PORT}，暂未开放 MTP 端口。"
  show_join_codes
  printf '\n下一步：分别在 VPS1/VPS2 运行同一脚本并选择 2/3，然后把回执粘贴回入口。\n'
}

register_response() {
  local code decoded normalized_a normalized_b role backend_ip wg_pub wg_port response_a response_b
  local -a fields
  load_state || die "入口尚未初始化。"
  [[ $TDH_ROLE == entry ]] || die "只有入口可以录入回执。"
  require_current_state
  code=$(prompt_secret "请粘贴后端返回的 TDHR4 回执码")
  decoded=$(decode_response_code "$code" "$ENROLL_TOKEN_B1" "$ENROLL_TOKEN_B2") || die "无法解析回执码。"
  mapfile -t fields <<<"$decoded"
  (( ${#fields[@]} == 10 )) || die "回执字段数量异常。"
  [[ ${fields[0]} == "$CLUSTER_ID" ]] || die "回执不属于当前集群。"
  role=${fields[1]} backend_ip=${fields[2]} wg_pub=${fields[3]} wg_port=${fields[4]}
  response_a=${fields[5]} response_b=${fields[6]}
  local max_connections=${fields[7]}
  [[ ${fields[8]} == "$TDH_PROTOCOL_VERSION" && ${fields[9]} == "$TDH_CONFIG_SCHEMA" ]] || \
    die "回执与当前配置协议不一致，请先更新三台机器的管理脚本。"
  validate_public_ipv4 "$backend_ip" || die "后端必须返回可路由的公网 IPv4。"
  [[ $backend_ip != "$ENTRY_PUBLIC_IP" ]] || die "后端与入口不能使用同一个公网 IPv4。"
  validate_wg_key "$wg_pub" || die "后端 WireGuard 公钥无效。"
  validate_port "$wg_port" || die "后端 WireGuard 端口无效。"
  validate_connection_limit "$max_connections" || die "后端容量上限无效。"
  normalized_a=$(normalize_and_validate_link "$response_a" "$PORT_A" "$SECRET_A" "$TDH_DOMAIN_A") || die "线路 A 链接校验失败。"
  normalized_b=$(normalize_and_validate_link "$response_b" "$PORT_B" "$SECRET_B" "$TDH_DOMAIN_B") || die "线路 B 链接校验失败。"

  if [[ -n $LINK_A && $LINK_A != "$normalized_a" ]] || [[ -n $LINK_B && $LINK_B != "$normalized_b" ]]; then
    die "两个后端生成的链接不一致，拒绝启用。"
  fi
  LINK_A=$normalized_a LINK_B=$normalized_b
  if [[ $role == backend1 ]]; then
    if [[ $B2_REGISTERED == 1 && ( $backend_ip == "$B2_PUBLIC_IP" || $wg_pub == "$B2_WG_PUB" ) ]]; then
      die "VPS1 与已注册 VPS2 使用了相同的公网 IP 或 WireGuard 公钥。"
    fi
    B1_PUBLIC_IP=$backend_ip B1_WG_PUB=$wg_pub B1_WG_PORT=$wg_port B1_REGISTERED=1
    B1_MAX_CONNECTIONS=$max_connections
    ENROLL_TOKEN_B1=$(rand_hex 32)
  else
    if [[ $B1_REGISTERED == 1 && ( $backend_ip == "$B1_PUBLIC_IP" || $wg_pub == "$B1_WG_PUB" ) ]]; then
      die "VPS2 与已注册 VPS1 使用了相同的公网 IP 或 WireGuard 公钥。"
    fi
    B2_PUBLIC_IP=$backend_ip B2_WG_PUB=$wg_pub B2_WG_PORT=$wg_port B2_REGISTERED=1
    B2_MAX_CONNECTIONS=$max_connections
    ENROLL_TOKEN_B2=$(rand_hex 32)
  fi
  save_state
  ok "$role 回执验证通过。"
  if [[ $B1_REGISTERED == 1 && $B2_REGISTERED == 1 ]]; then
    complete_entry
  else
    warn "还缺少另一个后端回执。"
  fi
}

health_agent_is_up() {
  local ip=$1 port=$2 reply
  reply=$(timeout 2 nc -w1 "$ip" "$port" </dev/null 2>/dev/null | tr -d '\r\n' || true)
  [[ $reply == up ]]
}

wait_all_backend_health() {
  local attempt
  for ((attempt=1; attempt<=20; attempt++)); do
    if health_agent_is_up 10.77.1.2 "$TDH_AGENT_PORT" && \
       health_agent_is_up 10.77.2.2 "$TDH_AGENT_PORT"; then
      return 0
    fi
    sleep 2
  done
  return 1
}

haproxy_all_telemt_up() {
  [[ -S /run/telemt-dual-hop/haproxy.sock ]] || return 1
  printf 'show stat\n' | socat - UNIX-CONNECT:/run/telemt-dual-hop/haproxy.sock 2>/dev/null | \
    awk -F, '
      ($1=="telemt_a" || $1=="telemt_b") && ($2=="vps1" || $2=="vps2") {
        seen++; if ($18 ~ /^UP/) up++
      }
      END { exit !(seen == 4 && up == 4) }
    '
}

wait_haproxy_backends() {
  local attempt
  for ((attempt=1; attempt<=15; attempt++)); do
    haproxy_all_telemt_up && return 0
    sleep 1
  done
  return 1
}

complete_entry() {
  local capacity
  load_state || die "状态缺失。"
  [[ $TDH_ROLE == entry && $B1_REGISTERED == 1 && $B2_REGISTERED == 1 ]] || die "两个后端尚未全部注册。"
  require_current_state
  for capacity in "$ENTRY_MAX_CONNECTIONS" "$B1_MAX_CONNECTIONS" "$B2_MAX_CONNECTIONS"; do
    validate_connection_limit "$capacity" || die "集群容量参数缺失或无效，请重新录入两个后端回执。"
  done
  tunnel_network_is_available 10.77.1.0/30 tdh1 || \
    die "10.77.1.0/30 与入口现有网络冲突；未修改路由。"
  tunnel_network_is_available 10.77.2.0/30 tdh2 || \
    die "10.77.2.0/30 与入口现有网络冲突；未修改路由。"
  info "配置两条 WireGuard 隧道……"
  install -d -m 0700 /etc/wireguard
  write_entry_wg 1
  write_entry_wg 2
  systemctl daemon-reload
  systemctl enable --now wg-quick@tdh1.service wg-quick@tdh2.service
  info "等待两台后端的 Telemt 与 Telegram Direct-DC 上游全部就绪……"
  wait_all_backend_health || \
    die "至少一个后端实例未就绪。请检查 UDP ${B1_WG_PORT}/${B2_WG_PORT}、后端诊断和 Telegram 出站连通性。"

  write_haproxy_config
  write_haproxy_unit
  open_firewall_entry
  save_state
  apply_tuning
  save_state
  systemctl daemon-reload
  systemctl enable --now telemt-dual-hop-haproxy.service
  systemctl is-active --quiet telemt-dual-hop-haproxy.service || die "HAProxy 未成功启动。"
  wait_haproxy_backends || die "HAProxy 未将四条逻辑主备路径全部判定为 UP。"
  tcp_port_is_listening "$PORT_A" || die "入口未监听 TCP $PORT_A。"
  save_state
  ok "三机集群启用成功。"
  show_links
}

show_links() {
  load_state || die "尚未安装。"
  [[ $TDH_ROLE == entry ]] || die "连接链接只在入口显示。"
  [[ -n $LINK_A && -n $LINK_B ]] || die "两个后端尚未完成配对。"
  if state_is_current; then
    title "线路 A（${TDH_DOMAIN_A}，入口 $PORT_A，主 VPS1 / 备 VPS2）"
  else
    title "旧版线路 A（apple.com，入口 $PORT_A，主 VPS1 / 备 VPS2）"
  fi
  printf '%s\n' "$LINK_A"
  if command -v qrencode >/dev/null && [[ -t 1 ]]; then qrencode -t ANSIUTF8 "$LINK_A"; fi
  if state_is_current; then
    title "线路 B（${TDH_DOMAIN_B}，入口 $PORT_B，主 VPS2 / 备 VPS1）"
  else
    title "旧版线路 B（apple.com，入口 $PORT_B，主 VPS2 / 备 VPS1）"
  fi
  printf '%s\n' "$LINK_B"
  if command -v qrencode >/dev/null && [[ -t 1 ]]; then qrencode -t ANSIUTF8 "$LINK_B"; fi
}

status_entry() {
  local unit maxconn
  if maxconn=$(configured_cluster_connection_limit 2>/dev/null); then
    printf '连接容量：入口 %s / VPS1 %s / VPS2 %s；全局上限 %s\n' \
      "$ENTRY_MAX_CONNECTIONS" "$B1_MAX_CONNECTIONS" "$B2_MAX_CONNECTIONS" "$maxconn"
  fi
  for unit in wg-quick@tdh1.service wg-quick@tdh2.service telemt-dual-hop-haproxy.service; do
    if systemctl is-active --quiet "$unit"; then ok "$unit 正常"; else warn "$unit 异常"; fi
  done
  printf '\nHAProxy 后端：\n'
  if [[ -S /run/telemt-dual-hop/haproxy.sock ]]; then
    printf 'show stat\n' | socat - UNIX-CONNECT:/run/telemt-dual-hop/haproxy.sock 2>/dev/null | \
      awk -F, 'NR==1 || $1=="telemt_a" || $1=="telemt_b" {print $1, $2, $18}'
  else
    warn "HAProxy Runtime Socket 不存在。"
  fi
}

status_backend() {
  local unit
  if validate_connection_limit "${BACKEND_MAX_CONNECTIONS:-}"; then
    printf '本机连接容量上限：%s\n' "$BACKEND_MAX_CONNECTIONS"
  fi
  if validate_direct_buffer_budget "${BACKEND_BUFFER_BUDGET_BYTES:-}"; then
    printf 'Direct-DC 缓冲预算：%s MiB\n' "$(( BACKEND_BUFFER_BUDGET_BYTES / 1024 / 1024 ))"
  fi
  for unit in "wg-quick@${WG_INTERFACE}.service" telemt-dual-hop-telemt.service telemt-dual-hop-health-agent.service; do
    if systemctl is-active --quiet "$unit"; then ok "$unit 正常"; else warn "$unit 异常"; fi
  done
}

show_status() {
  load_state || die "尚未安装。"
  title "节点角色：$TDH_ROLE"
  if [[ $TDH_ROLE == entry ]]; then status_entry; else status_backend; fi
}

diagnose() {
  local failures=0 maxconn config
  load_state || die "尚未安装。"
  require_current_state
  title "Telemt Dual-Hop 诊断"
  if [[ $TDH_ROLE == entry ]]; then
    for unit in wg-quick@tdh1.service wg-quick@tdh2.service telemt-dual-hop-haproxy.service; do
      if systemctl is-active --quiet "$unit"; then ok "$unit"; else warn "$unit"; ((failures+=1)); fi
    done
    if wireguard_handshake_is_fresh tdh1 && wireguard_handshake_is_fresh tdh2; then
      ok "两条 WireGuard 隧道握手新鲜"
    else
      warn "至少一条 WireGuard 隧道超过 180 秒没有握手"; ((failures+=1))
    fi
    if tuning_is_active; then ok "BBR + fq 已生效"; else warn "BBR/fq 未生效"; ((failures+=1)); fi
    if haproxy -c -f "$TDH_HAPROXY_CFG" >/dev/null; then ok "HAProxy 配置"; else warn "HAProxy 配置"; ((failures+=1)); fi
    if maxconn=$(configured_cluster_connection_limit 2>/dev/null) && \
       grep -q "^    maxconn ${maxconn}$" "$TDH_HAPROXY_CFG"; then
      ok "三节点容量/HAProxy 上限 ${maxconn}"
    else
      warn "三节点容量或 HAProxy 上限不一致"; ((failures+=1))
    fi
    if health_agent_is_up 10.77.1.2 "$TDH_AGENT_PORT"; then ok "VPS1 双线路 Telemt 就绪"; else warn "VPS1 Telemt 未就绪"; ((failures+=1)); fi
    if health_agent_is_up 10.77.2.2 "$TDH_AGENT_PORT"; then ok "VPS2 双线路 Telemt 就绪"; else warn "VPS2 Telemt 未就绪"; ((failures+=1)); fi
    if haproxy_all_telemt_up; then ok "HAProxy 四条逻辑主备路径"; else warn "HAProxy 主备路径状态"; ((failures+=1)); fi
    if tcp_port_is_listening "$PORT_A"; then ok "双 SNI 公网端口 $PORT_A 监听"; else warn "端口 $PORT_A"; ((failures+=1)); fi
  else
    for unit in "wg-quick@${WG_INTERFACE}.service" telemt-dual-hop-telemt.service telemt-dual-hop-health-agent.service; do
      if systemctl is-active --quiet "$unit"; then ok "$unit"; else warn "$unit"; ((failures+=1)); fi
    done
    if wireguard_handshake_is_fresh "$WG_INTERFACE"; then ok "WireGuard 握手新鲜"; else warn "WireGuard 超过 180 秒没有握手"; ((failures+=1)); fi
    if tuning_is_active; then ok "BBR + fq 已生效"; else warn "BBR/fq 未生效"; ((failures+=1)); fi
    if telemt_binary_is_expected; then ok "Telemt ${TELEMT_VERSION} 二进制"; else warn "Telemt 二进制版本异常"; ((failures+=1)); fi
    if health_agent_is_up "$LOCAL_WG_IP" "$TDH_AGENT_PORT"; then ok "Telemt API 与 5 个 Telegram DC 全部可达"; else warn "Telemt 或 Telegram DC 完整连通性异常"; ((failures+=1)); fi
    RESPONSE_LINK_A=$(query_telemt_link "$TDH_API_PORT" line_a "$TDH_DOMAIN_A" 2>/dev/null || true)
    RESPONSE_LINK_B=$(query_telemt_link "$TDH_API_PORT" line_b "$TDH_DOMAIN_B" 2>/dev/null || true)
    config="${TDH_CONFIG_DIR}/telemt.toml"
    if validate_connection_limit "${BACKEND_MAX_CONNECTIONS:-}" && \
       validate_direct_buffer_budget "${BACKEND_BUFFER_BUDGET_BYTES:-}" && \
       grep -q "^max_connections = ${BACKEND_MAX_CONNECTIONS}$" "$config" && \
       grep -q "^direct_relay_buffer_budget_max_bytes = ${BACKEND_BUFFER_BUDGET_BYTES}$" "$config" && \
       grep -q '^use_middle_proxy = false$' "$config" && \
       grep -Fq "tls_domains = [\"${TDH_DOMAIN_B}\"]" "$config" && \
       grep -q '^type = "direct"$' "$config"; then
      ok "单实例双 SNI / Direct-DC / 连接 ${BACKEND_MAX_CONNECTIONS} / 缓冲 $(( BACKEND_BUFFER_BUDGET_BYTES / 1024 / 1024 )) MiB"
    else
      warn "Telemt 架构或容量配置不一致"; ((failures+=1))
    fi
    if [[ -n $RESPONSE_LINK_A ]] && normalize_and_validate_link "$RESPONSE_LINK_A" "$PORT_A" "$SECRET_A" "$TDH_DOMAIN_A" >/dev/null; then ok "线路 A API/链接"; else warn "线路 A API/链接"; ((failures+=1)); fi
    if [[ -n $RESPONSE_LINK_B ]] && normalize_and_validate_link "$RESPONSE_LINK_B" "$PORT_B" "$SECRET_B" "$TDH_DOMAIN_B" >/dev/null; then ok "线路 B API/链接"; else warn "线路 B API/链接"; ((failures+=1)); fi
    if tcp_port_is_bound_only_to "$LOCAL_WG_IP" "$TDH_TELEMT_PORT"; then ok "Telemt 只监听 WireGuard 地址"; else warn "Telemt 监听范围异常"; ((failures+=1)); fi
    if tcp_port_is_bound_only_to "$LOCAL_WG_IP" "$TDH_AGENT_PORT"; then ok "健康代理只监听 WireGuard 地址"; else warn "健康代理监听范围异常"; ((failures+=1)); fi
    if tcp_port_is_bound_only_to 127.0.0.1 "$TDH_API_PORT"; then ok "Telemt API 只监听回环地址"; else warn "Telemt API 监听范围异常"; ((failures+=1)); fi
  fi
  if (( failures == 0 )); then ok "全部本机检查通过。"; else die "发现 $failures 项异常。"; fi
}

remove_ufw_rules() {
  ufw_is_active || return 0
  if [[ $TDH_ROLE == entry ]]; then
    if [[ $UFW_ADDED_A == 1 ]]; then ufw --force delete allow "$PORT_A/tcp" >/dev/null 2>&1 || true; fi
    if [[ $UFW_ADDED_B == 1 ]]; then ufw --force delete allow "$PORT_B/tcp" >/dev/null 2>&1 || true; fi
  else
    if [[ $UFW_ADDED_WG == 1 ]]; then
      ufw --force delete allow from "$ENTRY_PUBLIC_IP" to any port "$BACKEND_WG_PORT" proto udp >/dev/null 2>&1 || true
    fi
    if [[ $UFW_ADDED_P24431 == 1 ]]; then ufw --force delete allow in on "$WG_INTERFACE" from "$ENTRY_WG_IP" to "$LOCAL_WG_IP" port "$TDH_TELEMT_PORT" proto tcp >/dev/null 2>&1 || true; fi
    if [[ $UFW_ADDED_P24432 == 1 ]]; then ufw --force delete allow in on "$WG_INTERFACE" from "$ENTRY_WG_IP" to "$LOCAL_WG_IP" port 24432 proto tcp >/dev/null 2>&1 || true; fi
    if [[ $UFW_ADDED_P19101 == 1 ]]; then ufw --force delete allow in on "$WG_INTERFACE" from "$ENTRY_WG_IP" to "$LOCAL_WG_IP" port "$TDH_AGENT_PORT" proto tcp >/dev/null 2>&1 || true; fi
    if [[ $UFW_ADDED_P19102 == 1 ]]; then ufw --force delete allow in on "$WG_INTERFACE" from "$ENTRY_WG_IP" to "$LOCAL_WG_IP" port 19102 proto tcp >/dev/null 2>&1 || true; fi
  fi
}

uninstall_all() {
  local confirm
  load_state || die "尚未安装。"
  read -r -p "输入 UNINSTALL 确认卸载本脚本创建的服务：" confirm </dev/tty || true
  [[ $confirm == UNINSTALL ]] || { warn "已取消。"; return 0; }
  remove_ufw_rules
  if [[ $TDH_ROLE == entry ]]; then
    systemctl disable --now telemt-dual-hop-haproxy.service wg-quick@tdh1.service wg-quick@tdh2.service >/dev/null 2>&1 || true
    rm -f /etc/systemd/system/telemt-dual-hop-haproxy.service /etc/wireguard/tdh1.conf /etc/wireguard/tdh2.conf
  else
    systemctl disable --now telemt-dual-hop-telemt.service telemt-a.service telemt-b.service \
      telemt-dual-hop-health-agent.service "wg-quick@${WG_INTERFACE}.service" >/dev/null 2>&1 || true
    rm -f /etc/systemd/system/telemt-dual-hop-telemt.service \
      /etc/systemd/system/telemt-a.service /etc/systemd/system/telemt-b.service
    rm -f /etc/systemd/system/telemt-dual-hop-health-agent.service "/etc/wireguard/${WG_INTERFACE}.conf"
    safe_remove_tree /usr/local/lib/telemt-dual-hop
    safe_remove_tree "$TDH_WORK_ROOT"
    rm -f "$TDH_BIN"
    remove_telemt_account
  fi
  rm -f /etc/sysctl.d/90-telemt-dual-hop.conf /etc/modules-load.d/90-telemt-dual-hop.conf
  restore_tuning
  safe_remove_tree "$TDH_BASE"
  remove_shortcut
  rm -f "$TDH_MANAGER"
  systemctl daemon-reload
  ok "卸载完成；未删除系统软件包和用户原有防火墙规则。"
}

backend_response_again() {
  local backend_pub
  load_state || die "尚未安装。"
  [[ $TDH_ROLE == backend1 || $TDH_ROLE == backend2 ]] || die "仅后端可生成回执。"
  require_current_state
  RESPONSE_LINK_A=$(query_telemt_link "$TDH_API_PORT" line_a "$TDH_DOMAIN_A")
  RESPONSE_LINK_B=$(query_telemt_link "$TDH_API_PORT" line_b "$TDH_DOMAIN_B")
  backend_pub=$(wg pubkey <"${TDH_KEY_DIR}/backend.key")
  generate_response_code "$backend_pub"
}

clear_panel() {
  if [[ -t 1 && ${TERM:-dumb} != dumb ]]; then
    printf '\033[2J\033[H'
  fi
}

pause_panel() {
  [[ -t 1 ]] || return 0
  printf '\n'
  read -r -p '按 Enter 返回控制面板……' </dev/tty || true
}

read_panel_choice() {
  local prompt=$1 default=$2 answer
  if ! read -r -p "$prompt [$default]: " answer </dev/tty; then
    printf '\n' >&2
    return 1
  fi
  PANEL_CHOICE=${answer:-$default}
}

run_panel_action() {
  local rc
  # Give actions normal strict error handling, but keep the panel alive so the
  # user can retry without running the manager command again.
  trap - ERR
  set +e
  (
    set -Eeuo pipefail
    trap 'on_error $? $LINENO' ERR
    "$@"
  )
  rc=$?
  set -e
  trap 'on_error $? $LINENO' ERR
  if (( rc != 0 )); then
    warn "操作未完成（退出码 ${rc}）。你可以返回面板重试或运行诊断。"
  fi
  pause_panel
}

panel_header() {
  clear_panel
  printf '%s%s Telemt Dual-Hop 控制面板 %s%s\n' "$C_BOLD" "$C_BLUE" "$TDH_VERSION" "$C_RESET"
  printf '快捷入口：a    退出面板：0\n'
  printf '%s\n' '────────────────────────────────────────'
}

menu_fresh() {
  local PANEL_CHOICE
  while [[ ! -f $TDH_STATE ]]; do
    panel_header
    printf '当前状态：尚未安装\n\n'
    printf '  1) 安装入口 VPS（第一台，默认）\n'
    printf '  2) 安装后端 VPS1（需要 Join Code）\n'
    printf '  3) 安装后端 VPS2（需要 Join Code）\n'
    printf '  0) 退出\n\n'
    read_panel_choice '请选择' 1 || { PANEL_EXIT_REQUESTED=1; return 0; }
    case $PANEL_CHOICE in
      1) run_panel_action init_entry ;;
      2) run_panel_action install_backend backend1 ;;
      3) run_panel_action install_backend backend2 ;;
      0|q|Q) PANEL_EXIT_REQUESTED=1; return 0 ;;
      *) warn "无效选择：${PANEL_CHOICE}"; pause_panel ;;
    esac
  done
}

menu_existing() {
  local PANEL_CHOICE progress
  while [[ -f $TDH_STATE ]]; do
    load_state
    panel_header
    printf '当前节点：%s\n' "$TDH_ROLE"
    if ! state_is_current; then
      warn "检测到不兼容的旧版配置；不能与 ${TDH_VERSION} 混装。"
      printf '\n  1) 查看旧集群状态\n'
      if [[ $TDH_ROLE == entry ]]; then printf '  2) 显示旧版 MTP 链接\n'; fi
      printf '  8) 安全更新管理脚本\n'
      printf '  9) 完整卸载后重新安装 %s\n' "$TDH_VERSION"
      printf '  0) 退出\n\n'
      read_panel_choice '请选择' 1 || { PANEL_EXIT_REQUESTED=1; return 0; }
      case $PANEL_CHOICE in
        1) run_panel_action show_status ;;
        2)
          if [[ $TDH_ROLE == entry ]]; then run_panel_action show_links; else warn "无效选择：2"; pause_panel; fi
          ;;
        8) run_panel_action update_manager ;;
        9) run_panel_action uninstall_all ;;
        0|q|Q) PANEL_EXIT_REQUESTED=1; return 0 ;;
        *) warn "无效选择：${PANEL_CHOICE}"; pause_panel ;;
      esac
      continue
    fi

    if [[ $TDH_ROLE == entry ]]; then
      progress="VPS1 $([[ $B1_REGISTERED == 1 ]] && printf '已配对' || printf '待配对') / VPS2 $([[ $B2_REGISTERED == 1 ]] && printf '已配对' || printf '待配对')"
      printf '安装进度：%s\n\n' "$progress"
      printf '  1) 查看运行状态（默认）\n'
      printf '  2) 录入一个后端回执\n'
      printf '  3) 查看未使用的 Join Code\n'
      printf '  4) 显示两条 MTP 链接和二维码\n'
      printf '  5) 一键诊断\n'
      printf '  6) 完成或重试启用入口\n'
      printf '  7) 安全更新管理脚本\n'
      printf '  9) 卸载\n'
      printf '  0) 退出\n\n'
      read_panel_choice '请选择' 1 || { PANEL_EXIT_REQUESTED=1; return 0; }
      case $PANEL_CHOICE in
        1) run_panel_action show_status ;;
        2) run_panel_action register_response ;;
        3) run_panel_action show_join_codes ;;
        4) run_panel_action show_links ;;
        5) run_panel_action diagnose ;;
        6) run_panel_action complete_entry ;;
        7) run_panel_action update_manager ;;
        9) run_panel_action uninstall_all ;;
        0|q|Q) PANEL_EXIT_REQUESTED=1; return 0 ;;
        *) warn "无效选择：${PANEL_CHOICE}"; pause_panel ;;
      esac
    else
      printf '\n  1) 查看运行状态（默认）\n'
      printf '  2) 重新显示给入口使用的回执码\n'
      printf '  3) 一键诊断\n'
      printf '  4) 安全更新管理脚本\n'
      printf '  9) 卸载\n'
      printf '  0) 退出\n\n'
      read_panel_choice '请选择' 1 || { PANEL_EXIT_REQUESTED=1; return 0; }
      case $PANEL_CHOICE in
        1) run_panel_action show_status ;;
        2) run_panel_action backend_response_again ;;
        3) run_panel_action diagnose ;;
        4) run_panel_action update_manager ;;
        9) run_panel_action uninstall_all ;;
        0|q|Q) PANEL_EXIT_REQUESTED=1; return 0 ;;
        *) warn "无效选择：${PANEL_CHOICE}"; pause_panel ;;
      esac
    fi
  done
}

control_panel() {
  local PANEL_EXIT_REQUESTED=0
  while (( PANEL_EXIT_REQUESTED == 0 )); do
    if [[ -f $TDH_STATE ]]; then
      menu_existing
    else
      menu_fresh
    fi
  done
}

self_test() {
  local tmp release_fixture selected_port expected_a expected_b full_a full_b normalized
  tmp=$(mktemp -d)
  release_fixture="${tmp}/release.sh"
  printf 'readonly TDH_VERSION="9.8.7"\nreadonly TDH_CONFIG_SCHEMA="5"\nreadonly TDH_PROTOCOL_VERSION="1.2.1"\n' >"$release_fixture"
  [[ $(extract_release_value "$release_fixture" TDH_VERSION) == 9.8.7 ]] || die "更新版本解析测试失败。"
  [[ $(extract_release_value "$release_fixture" TDH_CONFIG_SCHEMA) == 5 ]] || die "更新架构解析测试失败。"
  CONFIG_SCHEMA=$TDH_CONFIG_SCHEMA INSTALLED_VERSION=1.2.1
  PORT_A=$TDH_PUBLIC_PORT PORT_B=$TDH_PUBLIC_PORT
  [[ $INSTALLED_VERSION != "$TDH_VERSION" ]] || die "版本兼容性测试样本无效。"
  state_is_current || die "旧管理器版本的配置兼容性测试失败。"
  CONFIG_SCHEMA=4
  if state_is_current; then die "不兼容配置架构被错误接受。"; fi
  port_is_free_udp() { [[ $1 == 51823 ]]; }
  select_wireguard_port selected_port 51821 >/dev/null 2>&1
  [[ $selected_port == 51823 ]] || die "WireGuard 自动端口顺延测试失败。"
  CONFIG_SCHEMA=$TDH_CONFIG_SCHEMA CLUSTER_ID="00112233445566778899aabb" ENTRY_PUBLIC_IP="203.0.113.10"
  PORT_A=443 PORT_B=443 SECRET_A="00112233445566778899aabbccddeeff"
  SECRET_B="ffeeddccbbaa99887766554433221100" JOIN_CREATED=1700000000 JOIN_EXPIRES=4102444800
  ENROLL_TOKEN_B1="$(printf 'ab%.0s' {1..32})" ENROLL_TOKEN_B2="$(printf 'cd%.0s' {1..32})"
  expected_a="ee${SECRET_A}$(printf '%s' "$TDH_DOMAIN_A" | od -An -tx1 | tr -d ' \n')"
  expected_b="ee${SECRET_B}$(printf '%s' "$TDH_DOMAIN_B" | od -An -tx1 | tr -d ' \n')"
  full_a="tg://proxy?server=${ENTRY_PUBLIC_IP}&port=443&secret=${expected_a}"
  full_b="tg://proxy?server=${ENTRY_PUBLIC_IP}&port=443&secret=${expected_b}"
  normalized=$(normalize_and_validate_link "$full_a" 443 "$SECRET_A" "$TDH_DOMAIN_A")
  [[ $normalized == https://t.me/proxy\?* ]] || die "链接单元测试失败。"
  normalize_and_validate_link "$full_b" 443 "$SECRET_B" "$TDH_DOMAIN_B" >/dev/null || \
    die "线路 B 域名单元测试失败。"
  if normalize_and_validate_link "$full_b" 443 "$SECRET_B" "$TDH_DOMAIN_A" >/dev/null 2>&1; then
    die "错误 SNI 被链接校验接受。"
  fi
  validate_ipv4 203.0.113.10
  validate_public_ipv4 8.8.8.8
  if validate_public_ipv4 10.0.0.1 || validate_public_ipv4 203.0.113.10; then
    die "公网 IPv4 单元测试失败。"
  fi
  validate_port 443
  validate_hex32 "$SECRET_A"
  all_apple_owned_ipv4 17.0.0.1 17.255.255.254
  if all_apple_owned_ipv4 17.0.0.1 101.72.204.55; then die "非 Apple 地址被错误接受。"; fi
  if validate_port 70000; then die "端口单元测试失败。"; fi
  rm -rf "$tmp"
  ok "内置单元测试通过。"
}

main() {
  case ${1:-} in
    -h|--help) usage; return ;;
    --self-test) self_test; return ;;
  esac
  validate_runtime_paths
  require_root
  check_os
  ensure_bootstrap_tools
  acquire_lock
  # Re-running a newly downloaded copy on an existing VPS updates the manager
  # in place and creates or repairs the short `a` entry point.
  if [[ -f $TDH_STATE && -z ${1:-} ]]; then
    install_self
  fi
  case ${1:-} in
    entry) init_entry ;;
    backend1) install_backend backend1 ;;
    backend2) install_backend backend2 ;;
    register) register_response ;;
    complete) complete_entry ;;
    status) show_status ;;
    links) show_links ;;
    diagnose) diagnose ;;
    update|upgrade|u) update_manager ;;
    uninstall) uninstall_all ;;
    menu|panel) control_panel ;;
    "") control_panel ;;
    *) usage; die "未知命令：$1" ;;
  esac
}

if [[ ${TDH_SOURCE_ONLY:-0} != 1 ]]; then
  main "$@"
fi
