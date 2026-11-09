#!/usr/bin/env bash
# shellcheck disable=SC2119,SC2120
# Telemt Dual-Hop - three VPS / two native MTProxy links
# TDH_QUEUE_PROTOCOL=1
# Supported OS: Ubuntu 22.04 (x86_64, aarch64)

set -Eeuo pipefail
umask 077

readonly TDH_VERSION="1.7.0"
readonly TDH_CONFIG_SCHEMA="5"
readonly TDH_PROTOCOL_VERSION="1.2.1"
readonly TELEMT_VERSION="3.5.17"
readonly TELEMT_COMMIT="66708d2866f3f3a4541c4fa67984d408e71a8af4"
readonly TELEMT_SHA256_AMD64="0570762ddcd79530d666b09e955b955bb52c9e134cc8b8ed1a0f50ad28d58a0e"
readonly TELEMT_BINARY_SHA256_AMD64="e8ff277bbb8ec4676d6264e593fe2a0d255facec8f1cb305e357a3859198ffd4"
readonly TELEMT_BINARY_SHA256_ARM64="e5ca8befdc1428fcd66537ef04f3a95e5f278ebf3c3dd0d0909ce25a390f502b"
readonly TELEMT_PREVIOUS_VERSION="3.5.16"
readonly TELEMT_PREVIOUS_BINARY_SHA256_AMD64="1954f18faeb9f5838e9444880f2ffb00704c6bd5b2836bc86dd64611537d49b8"
readonly TELEMT_PREVIOUS_BINARY_SHA256_ARM64="ebaec212fd5556ceca7fb0fcc4d3d23c01a8883d20a4cbc567219022d57c2a0b"
readonly TELEMT_LEGACY_VERSION="3.5.5"
readonly TELEMT_LEGACY_BINARY_SHA256_AMD64="cd34e20e9f5c4457579479e321eba2e65496712f0e794df6acb5b63d2ee01dcd"
readonly TELEMT_LEGACY_BINARY_SHA256_ARM64="5f2031447df06a393af7e271a72c5553ea83bff9611d24c441b492236101f857"
readonly TELEMT_SHA256_ARM64="f0d3d06d029f2b21dc66a44069bcc7bd20d32fcb34b5867c5250cee647785de6"
readonly TELEMT_CORE_VERSION_AMD64="3.5.5+tdh.balanced2"
readonly TELEMT_CORE_ARCHIVE_SHA256_AMD64="b4fec3efd10288a9f211ad958337fa314a15486e72fd5c50ab870f22a19737e5"
readonly TELEMT_CORE_BINARY_SHA256_AMD64="5dc5d9b5ac43a0f12a402159e127b3b69e18ccb355c99b0322e73d870d907100"
readonly TELEMT_CORE_URL_AMD64="https://github.com/bababoyi6/me/releases/download/telemt-core-3.5.5-tdh.balanced2/telemt-3.5.5-tdh.balanced2-x86_64-linux-gnu.tar.gz"
readonly TELEMT_AUDIT2_VERSION="3.5.5+tdh.audit2"
readonly TELEMT_AUDIT2_ARCHIVE_SHA256="c205183e479716f015378e4e2241103ae8d488e06f6ef65946be0bd94624f356"
readonly TELEMT_AUDIT2_BINARY_SHA256="2e5f864461be842dbca2ee8638f3b68327f56d5f1666ea0f2622a491b2ba75d1"
readonly TELEMT_AUDIT2_URL="https://github.com/bababoyi6/me/releases/download/telemt-core-3.5.5-tdh.audit2/telemt-3.5.5-tdh.audit2-x86_64-linux-gnu.tar.gz"
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
transaction_is_owned() {
  [[ ${TX_ACTIVE:-0} == 1 && ${TX_OWNER_PID:-} == "$BASHPID" ]]
}
die() {
  printf '%s[失败]%s %s\n' "$C_RED" "$C_RESET" "$*" >&2
  if transaction_is_owned && declare -F rollback_transaction >/dev/null; then
    rollback_transaction
  fi
  exit 1
}
title() { printf '\n%s%s%s\n' "$C_BOLD" "$*" "$C_RESET"; }

on_error() {
  local rc=$1 line=$2
  if transaction_is_owned && declare -F rollback_transaction >/dev/null; then
    rollback_transaction
  fi
  printf '%s[失败]%s 脚本在第 %s 行停止（退出码 %s）。没有通过校验的配置不会启用。\n' \
    "$C_RED" "$C_RESET" "$line" "$rc" >&2
}
trap 'on_error $? $LINENO' ERR

on_exit() {
  local rc=$1
  trap - EXIT
  if transaction_is_owned && declare -F rollback_transaction >/dev/null; then
    # Do not leave a half-installed node on explicit exit or handled signals.
    trap '' HUP INT TERM
    rollback_transaction
    (( rc != 0 )) || rc=1
  fi
  exit "$rc"
}
trap 'on_exit $?' EXIT
on_signal() {
  local rc=$1
  trap '' HUP INT TERM
  if [[ -n ${PANEL_ACTION_PID:-} ]]; then
    kill -TERM "$PANEL_ACTION_PID" 2>/dev/null || true
    wait "$PANEL_ACTION_PID" 2>/dev/null || true
  fi
  exit "$rc"
}
trap 'on_signal 130' INT
trap 'on_signal 143' TERM
trap 'on_signal 129' HUP

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
TX_ACTIVE=0 TX_KIND="" TX_OWNER_PID=""
PANEL_ACTION_PID=""

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
  printf 'Telemt Dual-Hop %s\n' "$TDH_VERSION"
  cat <<'EOF'

用法：
  sudo bash telemt-dual-hop.sh              打开中文菜单
  a                                         安装后打开控制面板（自动使用 sudo）
  a status                                  快速查看状态
  a update                                  安全更新管理脚本（可简写为 a u）
  a update-rollback                         恢复上次 a update 前的管理脚本
  a core-upgrade --maintenance               后端固定版本核心升级（需先排空）
  a core-rollback --maintenance              后端核心回滚（配置变更时拒绝）
  a backend-drain 1|2                       入口排空后端的新连接
  a backend-resume 1|2                      入口恢复后端接收新连接
  a health-refresh --maintenance            安装本管理器内置的健康代理
  a health-rollback --maintenance           回退上一次健康代理更新
  a connect-refresh --maintenance           备份并应用有界 DC 连接重试
  a connect-rollback --maintenance          恢复上一次 DC 连接配置
  a recover                                 后端隧道与服务恢复
  a perf [2-30]                             只读脱敏性能采样（默认 5 秒）
  a queue status                            查看后端实验队列（默认关闭）
  a queue enable 180 200 --maintenance       后端发送预算/实测路径Mbps（示例，勿直接照抄）
  a queue disable --maintenance              撤销本管理器创建的队列与启动钩子
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
  local error_file rc deadline update_retries=0
  local -a options=(-o DPkg::Lock::Timeout=30 -o Acquire::Retries=2
    -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30)
  if [[ ${1:-} == update ]]; then options+=(-o APT::Update::Error-Mode=any); fi
  error_file=$(mktemp)
  deadline=$((SECONDS + 600))

  while true; do
    if LC_ALL=C DEBIAN_FRONTEND=noninteractive apt-get "${options[@]}" "$@" 2>"$error_file"; then
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
    if [[ ${1:-} == update ]] && (( update_retries < 2 && SECONDS < deadline )) && \
       grep -Eqi 'Hash Sum mismatch|File has unexpected size|Mirror sync in progress|Temporary failure resolving|Could not resolve|Could not connect|Connection timed out|Connection reset by peer' "$error_file"; then
      update_retries=$((update_retries + 1))
      warn "Ubuntu 软件源同步或连接暂时异常；5 秒后重新获取并校验索引（重试 ${update_retries}/2）。"
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

lock_file_is_safe() {
  local owner permissions links metadata
  [[ -f $TDH_LOCK && ! -L $TDH_LOCK ]] || return 1
  metadata=$(stat -c '%u %a %h' -- "$TDH_LOCK" 2>/dev/null) || return 1
  read -r owner permissions links <<<"$metadata"
  [[ $owner == "$EUID" && $links == 1 && $permissions =~ ^[0-7]{1,4}$ ]] || return 1
  (( (8#$permissions & 0022) == 0 ))
}

acquire_lock() {
  local directory owner permissions temporary identity descriptor
  directory=$(dirname "$TDH_LOCK")
  mkdir -p "$directory"
  read -r owner permissions < <(stat -Lc '%u %a' -- "$directory")
  [[ $owner == "$EUID" && $permissions =~ ^[0-7]{1,4}$ ]] || die "管理锁目录权限异常，已停止以保护现有文件。"
  (( (8#$permissions & 0022) == 0 || (8#$permissions & 01000) != 0 )) || \
    die "管理锁目录权限异常，已停止以保护现有文件。"
  if [[ ! -e $TDH_LOCK && ! -L $TDH_LOCK ]]; then
    temporary=$(mktemp "${TDH_LOCK}.XXXXXX")
    if ! ln -T -- "$temporary" "$TDH_LOCK" 2>/dev/null; then
      rm -f -- "$temporary"
      die "管理锁已被其他操作占用或发生变化，请稍后重试。"
    fi
    rm -f -- "$temporary"
  fi
  lock_file_is_safe || die "管理锁文件权限或类型异常，已停止以保护现有文件。"
  # Keep the legacy inode for mutual exclusion, without truncating its contents.
  exec 9<>"$TDH_LOCK"
  identity=$(stat -c '%d:%i:%u:%a:%h' -- "$TDH_LOCK")
  descriptor=$(stat -Lc '%d:%i:%u:%a:%h' -- "/proc/$$/fd/9")
  if [[ $identity != "$descriptor" ]] || ! lock_file_is_safe; then
    die "管理锁发生变化，请稍后重试。"
  fi
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
  install -d -o root -g root -m 0755 "$(dirname "$TDH_SHORTCUT")" || return 1
  tmp=$(mktemp "${TDH_SHORTCUT}.update.XXXXXX") || return 1
  if ! {
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
  } >"$tmp"; then
    rm -f "$tmp"
    return 1
  fi
  if ! chmod 0755 "$tmp" || ! mv -fT "$tmp" "$TDH_SHORTCUT"; then
    rm -f "$tmp"
    return 1
  fi
}

remove_shortcut() {
  if shortcut_is_managed; then
    rm -f "$TDH_SHORTCUT"
  elif [[ -e $TDH_SHORTCUT || -L $TDH_SHORTCUT ]]; then
    warn "${TDH_SHORTCUT} 已不再由本脚本管理，卸载时予以保留。"
  fi
}

install_self() {
  local self=${BASH_SOURCE[0]} self_real manager_real installed_version entrypoints_changed=0
  if [[ -f $self && -r $self ]]; then
    [[ ! -L $TDH_MANAGER && ( ! -e $TDH_MANAGER || -f $TDH_MANAGER ) ]] || die "管理器文件类型异常；已保留原文件。"
    if [[ ( -e $TDH_SHORTCUT || -L $TDH_SHORTCUT ) ]] && ! shortcut_is_managed; then
      die "系统中已存在非本脚本创建的 a 命令（${TDH_SHORTCUT}）；为避免覆盖，请先自行改名或移除。"
    fi
    self_real=$(readlink -f -- "$self")
    manager_real=$(readlink -f -- "$TDH_MANAGER" 2>/dev/null || printf '%s\n' "$TDH_MANAGER")
    if [[ $self_real != "$manager_real" ]]; then
      if [[ -f $TDH_STATE && -f $TDH_MANAGER ]]; then
        installed_version=$(extract_release_value "$TDH_MANAGER" TDH_VERSION)
        [[ $installed_version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "现有管理器版本无效；已保留原文件。"
        [[ $(extract_release_value "$TDH_MANAGER" TDH_CONFIG_SCHEMA) == "$TDH_CONFIG_SCHEMA" && \
           $(extract_release_value "$TDH_MANAGER" TDH_PROTOCOL_VERSION) == "$TDH_PROTOCOL_VERSION" ]] || \
          die "安装命令与现有配置协议不兼容；已保留原管理器与服务，请查看升级说明。"
        dpkg --compare-versions "$TDH_VERSION" ge "$installed_version" || die "安装命令取得了旧版本；已拒绝覆盖较新的管理器。"
      fi
      if ! bash -n "$self" || ! timeout 60 bash "$self" --self-test >/dev/null; then
        die "安装脚本未通过检查；现有管理器与服务保持原样。"
      fi
      activate_manager "$self"
      entrypoints_changed=1
    else
      install_shortcut
    fi
    if (( entrypoints_changed == 1 )); then
      ok "管理入口已就绪；以后直接输入 a 即可打开控制面板。"
    fi
  else
    warn "当前通过不可复制的输入流运行，无法安装 a 快捷命令；请使用 README 中先下载再运行的安装方式。"
  fi
}

activate_manager() {
  local candidate=$1 staged backup="" prior_backup="" previous="${TDH_MANAGER}.previous"
  queue_manager_guard "$candidate"
  if [[ -L $TDH_MANAGER || ( -e $TDH_MANAGER && ! -f $TDH_MANAGER ) || \
        -L $previous || ( -e $previous && ! -f $previous ) ]]; then
    die "管理器或备份文件类型异常；已停止替换。"
  fi
  staged=$(mktemp "${TDH_MANAGER}.update.XXXXXX")
  if ! install -m 0755 "$candidate" "$staged"; then
    rm -f "$staged"
    die "无法准备新管理器；当前版本未作任何修改。"
  fi
  # Unique adjacent files and rename keep replacement atomic on this filesystem.
  if [[ -f $TDH_MANAGER ]] && ! cmp -s "$staged" "$TDH_MANAGER"; then
    backup=$(mktemp "${TDH_MANAGER}.previous.XXXXXX")
    if ! cp -p "$TDH_MANAGER" "$backup"; then
      rm -f "$staged" "$backup"
      die "无法保存旧管理器；更新已取消。"
    fi
  fi
  if ! install_shortcut; then
    rm -f "$staged"
    [[ -z $backup ]] || rm -f "$backup"
    die "无法准备快捷入口；当前管理器保持原样。"
  fi
  if [[ -n $backup ]]; then
    if [[ -f $previous ]]; then
      prior_backup=$(mktemp "${TDH_MANAGER}.restore.XXXXXX")
      if ! cp -p "$previous" "$prior_backup"; then
        rm -f "$staged" "$backup" "$prior_backup"
        die "无法保护已有回退备份；更新已取消。"
      fi
    fi
    if ! mv -fT "$backup" "$previous"; then
      rm -f "$staged" "$backup"
      [[ -z $prior_backup ]] || rm -f "$prior_backup"
      die "无法保存旧管理器；更新已取消。"
    fi
  fi
  if ! mv -fT "$staged" "$TDH_MANAGER"; then
    rm -f "$staged"
    if [[ -n $prior_backup ]]; then
      mv -fT "$prior_backup" "$previous" || warn "原回退备份仍保存在 ${prior_backup}，请保留此文件。"
    elif [[ -n $backup ]]; then
      rm -f "$previous"
    fi
    die "无法替换管理器；当前版本未作任何修改。"
  fi
  [[ -z $prior_backup ]] || rm -f "$prior_backup"
}

extract_release_value() {
  local file=$1 name=$2
  awk -F'"' -v prefix="readonly ${name}=\"" 'index($0, prefix) == 1 { print $2; exit }' "$file"
}

update_manager() {
  local download_tmp new_version new_schema new_protocol checksum
  load_state || die "尚未安装，不能使用快捷更新。"
  if [[ ( -e $TDH_SHORTCUT || -L $TDH_SHORTCUT ) ]] && ! shortcut_is_managed; then
    die "系统中已存在非本脚本创建的 a 命令（${TDH_SHORTCUT}）；更新已取消。"
  fi

  download_tmp=$(mktemp)
  info "正在从 GitHub 获取最新管理脚本……"
  # Match bootstrap protections, including responses without Content-Length.
  if ! (ulimit -f 1024 || exit 1
    curl -q -fL --retry 3 --retry-delay 2 --connect-timeout 10 --max-time 120 --max-filesize 1048576 \
      --proto '=https' --proto-redir '=https' --tlsv1.2 "$TDH_UPDATE_URL" -o "$download_tmp"); then
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
  if ! timeout 60 bash "$download_tmp" --self-test >/dev/null; then
    rm -f "$download_tmp"
    die "新脚本未通过内置自检；当前版本未作任何修改。"
  fi

  checksum=$(sha256sum "$download_tmp" | awk '{print $1}')
  activate_manager "$download_tmp"
  rm -f "$download_tmp"
  if [[ $new_version == "$TDH_VERSION" ]]; then
    ok "已重新安装最新版 ${new_version}（SHA256: ${checksum}）。"
  else
    ok "管理脚本已从 ${TDH_VERSION} 更新到 ${new_version}（SHA256: ${checksum}）。"
  fi
  info "升级完成，无需重新部署。现有服务、IP、端口、Secret、链接和主备配置均保留。"
  info "本次为兼容性与只读诊断升级，没有经生产验证的大幅提速，未自动修改传输参数。"
  info "退出当前面板后再次输入 a 即可使用新版本；诊断为按需功能，不是升级步骤。"
}

rollback_manager() {
  local previous="${TDH_MANAGER}.previous" tmp
  [[ ! -L $TDH_MANAGER && ( ! -e $TDH_MANAGER || -f $TDH_MANAGER ) ]] || \
    die "管理器文件类型异常；已保留当前文件和回退备份。"
  [[ -f $previous && ! -L $previous ]] || die "没有上次 a update 的管理器备份。"
  queue_manager_guard "$previous"
  [[ $(extract_release_value "$previous" TDH_CONFIG_SCHEMA) == "$TDH_CONFIG_SCHEMA" && \
     $(extract_release_value "$previous" TDH_PROTOCOL_VERSION) == "$TDH_PROTOCOL_VERSION" ]] || die "备份协议不兼容，拒绝回退。"
  if ! bash -n "$previous" || ! timeout 60 bash "$previous" --self-test >/dev/null; then
    die "备份自检失败。"
  fi
  tmp=$(mktemp "${TDH_MANAGER}.rollback.XXXXXX")
  if ! install -m 0755 "$previous" "$tmp"; then
    rm -f -- "$tmp"
    die "无法准备回退管理器；当前文件和回退备份保持原样。"
  fi
  if ! mv -fT -- "$tmp" "$TDH_MANAGER"; then
    rm -f -- "$tmp"
    die "无法替换管理器；当前文件和回退备份保持原样。"
  fi
  ok "管理器已恢复；服务和集群配置未改动，退出面板后重新输入 a。"
}

init_dirs() {
  local backend_base_mode=0750
  # Routine state saves must not silently migrate an existing legacy node.
  # Its 0710 -> 0750 change belongs to the explicit core maintenance transaction.
  if [[ ( $TDH_ROLE == backend1 || $TDH_ROLE == backend2 ) && -d $TDH_BASE && ! -L $TDH_BASE ]] && \
     [[ $(stat -c '%a' -- "$TDH_BASE") == 710 ]]; then
    backend_base_mode=0710
  fi
  install -d -o root -g root -m 0700 "$TDH_BASE" "$TDH_KEY_DIR"
  # New Telemt opens every configuration ancestor with O_RDONLY|O_DIRECTORY.
  # The dedicated group needs read/search; state and WG keys remain root-only.
  if [[ $TDH_ROLE == backend1 || $TDH_ROLE == backend2 ]]; then
    install -d -o root -g root -m 0750 "$TDH_CONFIG_DIR" "$TDH_WORK_ROOT"
    if getent group telemt >/dev/null 2>&1; then
      chown root:telemt "$TDH_BASE" "$TDH_CONFIG_DIR" "$TDH_WORK_ROOT"
      chmod "$backend_base_mode" "$TDH_BASE"
      chmod 0750 "$TDH_CONFIG_DIR" "$TDH_WORK_ROOT"
    fi
  fi
}

assert_fresh_install_paths() {
  local path root=${TDH_BASE%/*} unit
  local -a reserved=("$TDH_BASE" "$TDH_WORK_ROOT" "$(dirname "$TDH_BIN")" "$TDH_MANAGER" "$TDH_SHORTCUT"
    "${root}/sysctl.d/90-telemt-dual-hop.conf" "${root}/modules-load.d/90-telemt-dual-hop.conf")
  for unit in telemt-dual-hop-haproxy telemt-dual-hop-telemt telemt-dual-hop-health-agent telemt-a telemt-b; do
    reserved+=("${root}/systemd/system/${unit}.service")
  done
  for unit in tdh1 tdh2 tdh-b1 tdh-b2; do reserved+=("${root}/wireguard/${unit}.conf"); done
  for path in "${reserved[@]}"; do
    [[ ! -e $path && ! -L $path ]] || \
      die "新安装路径已存在：${path}。已保留原内容；请先确认其来源，不会自动覆盖或清理。"
  done
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
  # Once cleanup starts, finish it even if the terminal disconnects again.
  trap '' HUP INT TERM
  TX_ACTIVE=0
  TX_OWNER_PID=""
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
  [[ -f $TDH_STATE && ! -L $TDH_STATE ]] || return 1
  local directory owner permissions
  directory=$(dirname "$TDH_STATE")
  while [[ $directory != / ]]; do
    [[ -d $directory && ! -L $directory ]] || die "状态目录不得包含符号链接。"
    read -r owner permissions < <(stat -c '%u %a' -- "$directory")
    [[ $owner == 0 && $permissions =~ ^[0-7]{1,4}$ ]] || die "状态目录所有者异常。"
    (( (8#$permissions & 0022) == 0 || (8#$permissions & 01000) != 0 )) || die "状态目录不得允许其他用户写入。"
    directory=$(dirname "$directory")
  done
  [[ $(stat -c '%u' "$TDH_STATE") -eq 0 ]] || die "状态文件所有者异常。"
  [[ $(stat -c '%h' "$TDH_STATE") == 1 ]] || die "状态文件不得有额外硬链接。"
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
  state_is_current || die "当前管理器不支持此配置协议。已保留现有服务与连接信息，请使用兼容版本或按发布说明迁移；不要直接重新初始化。"
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

# A core restart interrupts this backend's existing TCP sessions. Upgrade one
# backend at a time after checking the peer and draining from the entry node.
manage_core() {
  local action=$1 acknowledgement=${2:-} tmp="" asset expected binary_sha url directory result=0
  [[ $acknowledgement == --maintenance ]] || die "请先确认另一个后端正常并在入口排空本后端，按升级文档添加 --maintenance；现有会话可能断开。"
  load_state || die "未安装节点。"
  require_current_state
  [[ $TDH_ROLE == backend1 || $TDH_ROLE == backend2 ]] || die "核心事务只能在后端运行。"
  if [[ $action == upgrade || $action == *-connect ]]; then
    telemt_binary_is_expected || die "现有核心身份未知；拒绝自动维护。"
  fi
  install -d -m 0700 "${TDH_BASE}/core-upgrade"
  local args=("$action" --binary "$TDH_BIN" --directory "${TDH_BASE}/core-upgrade"
    --config "${TDH_CONFIG_DIR}/telemt.toml" --work "${TDH_WORK_ROOT}/telemt"
    --token-file "${TDH_CONFIG_DIR}/api.token" --bind "$LOCAL_WG_IP" --protect "$TDH_STATE"
    --protect "$TDH_CONFIG_DIR" --protect "$TDH_KEY_DIR"
    --protect "/etc/wireguard/${WG_INTERFACE}.conf"
    --protect /etc/systemd/system/telemt-dual-hop-telemt.service
    --protect /etc/systemd/system/telemt-dual-hop-health-agent.service)
  for directory in /etc/systemd/system/telemt-dual-hop-{telemt,health-agent}.service.d \
      /run/systemd/system/telemt-dual-hop-{telemt,health-agent}.service.d \
      "$(dirname "$TDH_BIN")/core-source-notice"; do
    [[ ! -e $directory && ! -L $directory ]] || args+=(--protect "$directory")
  done
  if [[ $action == upgrade ]]; then
    case $(uname -m) in
      x86_64|amd64) asset=telemt-x86_64-linux-gnu.tar.gz expected=$TELEMT_SHA256_AMD64 binary_sha=$TELEMT_BINARY_SHA256_AMD64 ;;
      aarch64|arm64) asset=telemt-aarch64-linux-gnu.tar.gz expected=$TELEMT_SHA256_ARM64 binary_sha=$TELEMT_BINARY_SHA256_ARM64 ;;
      *) die "不支持当前架构。" ;;
    esac
    url="https://github.com/telemt/telemt/releases/download/${TELEMT_VERSION}/${asset}"
    info "目标 Telemt ${TELEMT_VERSION}，提交 ${TELEMT_COMMIT}；保留配置与链接。"
    tmp=$(mktemp -d "${TDH_BASE}/core-fetch.XXXXXX")
    if ! curl -q -fL --retry 2 --connect-timeout 10 --max-time 120 --max-filesize 67108864 \
        --proto '=https' --proto-redir '=https' --tlsv1.2 "$url" -o "${tmp}/core.tar.gz"; then
      rm -rf -- "$tmp"
      die "核心下载失败；现有服务未改动。"
    fi
    args+=(--archive "${tmp}/core.tar.gz" --archive-sha "$expected" --binary-sha "$binary_sha" --version "$TELEMT_VERSION")
  fi
  python3 - "${args[@]}" <<'TDH_CORE_UPGRADE_PY' || result=$?
# BEGIN GENERATED CORE UPGRADE
HEALTH_AGENT_SOURCE = '#!/usr/bin/env python3\nimport concurrent.futures, json, os, selectors, socket, threading, time, urllib.error, urllib.request\n\nBIND = os.environ["TDH_BIND_IP"]\nwith open(os.environ["TDH_API_TOKEN_FILE"], encoding="ascii") as token_file:\n    API_TOKEN = token_file.read().strip()\nAGENT_PORT = int(os.environ["TDH_AGENT_PORT"])\nAPI_PORT = int(os.environ["TDH_API_PORT"])\nTELEMT_PORT = int(os.environ.get("TDH_TELEMT_PORT", "24431"))\nDCS = tuple(value for value in os.environ["TDH_DC_IPV4"].split(",") if value)\nDC_PORT = int(os.environ["TDH_DC_PORT"])\nAPI_READY = False\nDC_READY = False\nAPI_CHECKED = None\nDC_CHECKED = None\nAPI_MAX_AGE = 5\nDC_MAX_AGE = 25\n# Progress deadlines cover the complete check, not each socket read. A peer\n# sending a byte before every urllib timeout can otherwise strand a live thread.\nAPI_PROGRESS_TIMEOUT = 15\nDC_PROGRESS_TIMEOUT = 45\nREADY_LOCK = threading.Lock()\nsel = selectors.DefaultSelector()\nsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\nsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\nsock.bind((BIND, AGENT_PORT)); sock.listen(64); sock.setblocking(False)\nsel.register(sock, selectors.EVENT_READ)\n\ndef healthy(http, api_port):\n    request = urllib.request.Request(\n        f"http://127.0.0.1:{api_port}/v1/health/ready",\n        headers={"Authorization": f"Bearer {API_TOKEN}"},\n    )\n    try:\n        with http.open(request, timeout=2) as response:\n            raw = response.read(16385)\n            if len(raw) > 16384:\n                return False\n            payload = json.loads(raw)\n        return (response.status == 200 and isinstance(payload, dict) and payload.get("ok") is True\n                and isinstance(payload.get("data"), dict) and payload["data"].get("ready") is True)\n    except urllib.error.HTTPError as error:\n        error.close()\n        return False\n    except (OSError, ValueError, urllib.error.URLError):\n        return False\n\nclass NoRedirect(urllib.request.HTTPRedirectHandler):\n    def redirect_request(self, req, fp, code, msg, headers, newurl):\n        # A local readiness endpoint must not forward its bearer token elsewhere.\n        return None\n\n\nclass HealthGate:\n    """Consecutive samples plus a down-state cooldown; stale results expire separately."""\n    def __init__(self, fall, rise, cooldown):\n        self.fall, self.rise, self.cooldown = fall, rise, cooldown\n        self.up = False\n        self.good = self.bad = 0\n        self.retry_after = 0\n\n    def sample(self, good, now):\n        if good:\n            self.bad = 0\n            self.good += 1\n            if self.good >= self.rise and now >= self.retry_after:\n                self.up = True\n        else:\n            self.good = 0\n            self.bad += 1\n            if self.up and self.bad >= self.fall:\n                self.up = False\n                self.retry_after = now + self.cooldown\n        return self.up\n\n\ndef dc_quorum(results):\n    # One impaired DC must not evict a backend serving the other DCs.\n    # This TCP reachability vote is NOT an MTProto or per-DC protocol guarantee.\n    results = tuple(results)\n    return bool(results) and sum(value is True for value in results) >= len(results) // 2 + 1\n\n\ndef listener_alive():\n    try:\n        with socket.create_connection((BIND, TELEMT_PORT), timeout=1):\n            return True\n    except OSError:\n        return False\n\n\ndef monitor_api():\n    global API_READY, API_CHECKED\n    # Never inherit HTTP(S)_PROXY for the loopback API or expose its bearer token.\n    http = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())\n    gate = HealthGate(fall=3, rise=2, cooldown=10)\n    while True:\n        current = healthy(http, API_PORT) and listener_alive()\n        with READY_LOCK:\n            API_CHECKED = time.monotonic()\n            API_READY = gate.sample(current, API_CHECKED)\n        time.sleep(1)\n\ndef probe_dc(address):\n    try:\n        with socket.create_connection((address, DC_PORT), timeout=2):\n            return True\n    except OSError:\n        return False\n\ndef monitor_dcs():\n    global DC_READY, DC_CHECKED\n    gate = HealthGate(fall=2, rise=3, cooldown=30)\n    while True:\n        if DCS:\n            with concurrent.futures.ThreadPoolExecutor(max_workers=len(DCS)) as executor:\n                current = dc_quorum(executor.map(probe_dc, DCS))\n        else:\n            current = False\n        with READY_LOCK:\n            DC_CHECKED = time.monotonic()\n            DC_READY = gate.sample(current, DC_CHECKED)\n        time.sleep(10)\n\ndef ready(now=None):\n    # A stalled/crashed monitor must never leave a permanently healthy flag.\n    with READY_LOCK:\n        # Read the clock after locking so a monitor cannot publish a newer timestamp.\n        if now is None:\n            now = time.monotonic()\n        return (API_READY and DC_READY and API_CHECKED is not None and DC_CHECKED is not None\n                and 0 <= now - API_CHECKED <= API_MAX_AGE and 0 <= now - DC_CHECKED <= DC_MAX_AGE)\n\n\ndef monitors_progressing(now, started):\n    with READY_LOCK:\n        # As with ready(), sample after locking: a freshly published timestamp\n        # must not look as though it came from the future.\n        if now is None:\n            now = time.monotonic()\n        return all(0 <= now - (checked if checked is not None else started) <= limit\n                   for checked, limit in ((API_CHECKED, API_PROGRESS_TIMEOUT),\n                                          (DC_CHECKED, DC_PROGRESS_TIMEOUT)))\n\n\nstarted = time.monotonic()\nmonitors = [threading.Thread(target=target, daemon=True) for target in (monitor_api, monitor_dcs)]\nfor monitor in monitors:\n    monitor.start()\n\nwhile True:\n    if any(not monitor.is_alive() for monitor in monitors):\n        # Let the existing systemd restart policy replace a failed monitor thread.\n        raise SystemExit("health monitor stopped")\n    if not monitors_progressing(None, started):\n        # Staleness already closes admission. Recover the stuck worker through\n        # the existing service restart policy; ordinary unhealthy votes do not\n        # trigger restarts, and no check is silently declared healthy.\n        raise SystemExit("health monitor progress deadline")\n    for key, _ in sel.select(timeout=1):\n        listener = key.fileobj\n        try:\n            conn, _ = listener.accept()\n        except (BlockingIOError, OSError):\n            continue\n        with conn:\n            try:\n                conn.settimeout(1)\n                current = ready()\n                conn.sendall(b"up\\n" if current else b"down\\n")\n            except OSError:\n                pass\n'

"""Validate a pinned core archive and stage a fresh install; never update a node.

This source is embedded verbatim in the single-file Bash installer. All archive
bytes and members are verified before any executable is run or installed.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import stat
import subprocess
import sys
import tarfile
import tempfile


class InstallError(RuntimeError):
    pass


AUDIT_FILES = {'telemt','TELEMT-LICENSE.txt','NOTICE.txt','source.patch','build-provenance.json','manifest.json'}


def checksum(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024*1024), b''):
            value.update(block)
    return value.hexdigest()


def durable_file(path, content, mode):
    with Path(path).open('xb') as output:
        output.write(content)
        output.flush()
        os.fchmod(output.fileno(), mode)
        os.fsync(output.fileno())


def archive_payload(path, expected_archive, version, expected_binary='', audited=False):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 64*1024**2:
        raise InstallError('invalid archive type or size')
    if checksum(path) != expected_archive:
        raise InstallError('archive checksum mismatch')
    files, names, total = {}, set(), 0
    with tarfile.open(path, mode='r|gz') as archive:
        for number, member in enumerate(archive, 1):
            name = PurePosixPath(member.name)
            if (number > 32 or name.is_absolute() or '..' in name.parts or not name.parts
                    or str(name) in names or (not member.isfile() and not member.isdir())):
                raise InstallError('unsafe or duplicate archive member')
            names.add(str(name))
            if member.isdir():
                if audited:
                    raise InstallError('unexpected directory in audited archive')
                continue
            if not 0 < member.size <= 64*1024**2:
                raise InstallError('invalid member size')
            total += member.size
            if total > 64*1024**2:
                raise InstallError('archive payload exceeds limit')
            if audited and str(name) not in AUDIT_FILES:
                raise InstallError('unexpected audited archive member')
            content = archive.extractfile(member).read(member.size+1)
            if len(content) != member.size:
                raise InstallError('incomplete archive member')
            files[str(name)] = content
    binaries = [content for name, content in files.items() if PurePosixPath(name).name == 'telemt']
    if len(binaries) != 1:
        raise InstallError('archive must contain one telemt binary')
    binary = binaries[0]
    if expected_binary and hashlib.sha256(binary).hexdigest() != expected_binary:
        raise InstallError('binary checksum mismatch')
    if audited:
        if set(files) != AUDIT_FILES:
            raise InstallError('incomplete audited archive')
        if len(binary) < 64 or binary[:6] != b'\x7fELF\x02\x01' or binary[18:20] != b'\x3e\x00':
            raise InstallError('x86_64 ELF required')
        manifest = json.loads(files['manifest.json'])
        if (manifest.get('schema') != 1 or manifest.get('version') != version
                or manifest.get('architecture') != 'x86_64' or manifest.get('os') != 'linux'
                or manifest.get('libc') != 'gnu' or manifest.get('minimum_glibc') != '2.35'
                or manifest.get('unofficial') is not True
                or set(manifest.get('files', {})) != AUDIT_FILES-{'manifest.json'}):
            raise InstallError('audited manifest identity mismatch')
        for name, metadata in manifest['files'].items():
            if (metadata.get('sha256') != hashlib.sha256(files[name]).hexdigest()
                    or metadata.get('bytes') != len(files[name])
                    or metadata.get('mode') != ('0755' if name == 'telemt' else '0644')):
                raise InstallError('audited manifest member mismatch')
    return binary, {name:files[name] for name in AUDIT_FILES-{'telemt'} if name in files}


def fresh_install(archive, target, expected_archive, version, expected_binary='', audited=False):
    target = Path(target)
    parent = target.parent
    if not target.is_absolute() or target.resolve() != target or target.exists() or target.is_symlink():
        raise InstallError('fresh install refuses an existing core or symlink')
    parent_info = parent.lstat()
    if not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid != os.geteuid() or parent_info.st_mode & 0o022:
        raise InstallError('unsafe installation directory')
    binary, documents = archive_payload(archive, expected_archive, version, expected_binary, audited)
    identity = pwd.getpwnam('telemt')
    if identity.pw_uid == 0 or identity.pw_gid == 0:
        raise InstallError('unprivileged telemt identity required')
    with tempfile.TemporaryDirectory(prefix='.telemt-install-', dir=parent) as temporary:
        stage = Path(temporary)
        candidate = stage/'telemt'
        durable_file(candidate, binary, 0o755)
        stage.chmod(0o755)
        result = subprocess.run([str(candidate),'--version'], user=identity.pw_uid, group=identity.pw_gid,
                                extra_groups=[], cwd='/', capture_output=True, timeout=5, check=True)
        if result.stdout.strip() != ('telemt '+version).encode() or len(result.stdout) > 256:
            raise InstallError('candidate runtime version mismatch')
        if audited:
            legal = stage/'core-source-notice'
            legal.mkdir(mode=0o755)
            for name, content in documents.items():
                document = legal/name
                durable_file(document, content, 0o644)
            destination = parent/'core-source-notice'
            if destination.exists() or destination.is_symlink():
                raise InstallError('refusing to replace existing core notice')
            os.rename(legal, destination)
        # Exclusive link cannot clobber a concurrent or pre-existing executable.
        os.link(candidate, target)
        directory = os.open(parent, os.O_RDONLY|os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    return version

"""Durable local core replacement engine; deliberately not a public upgrade CLI.

The caller must hold an entry-side maintenance permit and drain this backend.
This module never downloads a release, edits configuration or coordinates peers.
Service hooks are explicit so the exact transaction can be tested without systemd.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile


class CoreError(RuntimeError):
    pass


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def safe_directory(path):
    path = Path(path)
    info = path.lstat()
    if (not path.is_absolute() or path.resolve() != path or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid() or info.st_mode & 0o022):
        raise CoreError("unsafe transaction directory")


def safe_file(path):
    info = Path(path).lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_nlink != 1 or info.st_mode & 0o022):
        raise CoreError("unsafe transaction file")


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_copy(source, target, mode, expected_sha256=None, owner=None):
    target = Path(target)
    safe_directory(target.parent)
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=target.name + ".stage-", dir=target.parent)
        with os.fdopen(fd, "wb") as output, Path(source).open("rb") as incoming:
            shutil.copyfileobj(incoming, output, 1024 * 1024)
            output.flush()
            if owner is not None:
                os.fchown(output.fileno(), *owner)
            os.fchmod(output.fileno(), mode)
            os.fsync(output.fileno())
        if expected_sha256 is not None and digest(temporary) != expected_sha256:
            raise CoreError("source changed while staging core")
        os.replace(temporary, target)
        temporary = None
        sync_directory(target.parent)
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


class CoreTransaction:
    """One backend only. Hooks implement stop/start, bounded ready and version."""

    def __init__(self, binary, directory, service, *, file_mode=0o755, file_owner=None):
        self.binary = Path(binary)
        self.directory = Path(directory)
        self.service = service
        if file_mode not in (0o640, 0o755) or (file_mode == 0o640 and file_owner is None):
            raise CoreError('explicit config ownership required')
        self.file_mode, self.file_owner = file_mode, file_owner
        self.target_metadata = ({'mode': file_mode, 'uid': file_owner[0], 'gid': file_owner[1]}
                                if file_owner is not None else None)
        safe_directory(self.binary.parent)
        safe_directory(self.directory)
        if self.directory.stat().st_mode & 0o077:
            raise CoreError("transaction storage must be private")
        self.journal = self.directory / "transaction.json"

    @contextmanager
    def locked(self):
        lock = self.directory / "lock"
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            safe_file(lock)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)

    def save(self, value):
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(prefix="journal-", dir=self.directory)
            with os.fdopen(fd, "w", encoding="ascii") as output:
                json.dump(value, output, sort_keys=True)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.journal)
            temporary = None
            sync_directory(self.directory)
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    def load(self):
        safe_file(self.journal)
        if self.journal.stat().st_size > 4096:
            raise CoreError("oversized transaction journal")
        value = json.loads(self.journal.read_text(encoding="ascii"))
        self.validate(value)
        return value

    def validate(self, value, allow_prior=True):
        if not isinstance(value, dict) or value.get("schema") != 1 or value.get("binary") != str(self.binary):
            raise CoreError("transaction journal belongs to another target")
        if value.get('target_metadata') != self.target_metadata:
            raise CoreError('transaction target permissions changed')
        for key in ("old_sha256", "new_sha256"):
            text = value.get(key, "")
            if len(text) != 64 or any(c not in "0123456789abcdef" for c in text):
                raise CoreError("invalid journal digest")
        if value.get("phase") not in ("prepared", "switching", "committed", "recovered"):
            raise CoreError("invalid journal phase")
        if value.get("backup") != "core.previous-" + value["old_sha256"]:
            raise CoreError("invalid backup identity")
        snapshot = value.get("snapshot")
        if snapshot is not None and (not isinstance(snapshot, str) or len(snapshot) != 17
                                     or not snapshot.startswith('snapshot-')
                                     or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789_' for c in snapshot[9:])):
            raise CoreError("invalid protected snapshot identity")
        if value.get("prior") is not None:
            if not allow_prior:
                raise CoreError("nested transaction history")
            self.validate(value["prior"], False)
            if value["prior"]["phase"] != "committed" or value["prior"]["new_sha256"] != value["old_sha256"]:
                raise CoreError("previous journal does not match this baseline")

    def restore(self, value):
        backup = self.directory / value["backup"]
        safe_file(backup)
        safe_file(self.binary)
        self.validate_target_metadata()
        if digest(backup) != value["old_sha256"]:
            raise CoreError("previous core checksum mismatch")
        if digest(self.binary) not in (value["old_sha256"], value["new_sha256"]):
            raise CoreError("core changed outside this transaction; refusing overwrite")
        self.service.stop()
        atomic_copy(backup, self.binary, self.file_mode, value["old_sha256"], self.file_owner)
        self.service.start()
        if not self.service.ready():
            raise CoreError("previous core did not regain readiness")
        value["phase"] = "recovered"
        self.save(value)

    def activate(self, candidate, expected_sha256, expected_version, current_sha256, snapshot=None):
        candidate = Path(candidate)
        with self.locked():
            safe_directory(candidate.parent)
            safe_file(self.binary)
            self.validate_target_metadata()
            safe_file(candidate)
            if candidate.stat().st_size > 64 * 1024**2:
                raise CoreError("candidate exceeds size limit")
            if digest(candidate) != expected_sha256:
                raise CoreError("candidate checksum mismatch")
            if self.service.version(candidate) != expected_version:
                raise CoreError("candidate version mismatch")
            if digest(self.binary) != current_sha256:
                raise CoreError("running core differs from the approved baseline")
            if self.journal.is_symlink():
                raise CoreError("unsafe transaction journal")
            prior = None
            if self.journal.exists():
                previous = self.load()
                if previous["phase"] not in ("committed", "recovered"):
                    raise CoreError("unfinished transaction; recover it before upgrading")
                if previous["phase"] == "committed" and previous["new_sha256"] == current_sha256:
                    prior = {k: previous[k] for k in ("schema", "binary", "old_sha256", "new_sha256", "backup", "phase")}
                    if 'snapshot' in previous:
                        prior['snapshot'] = previous['snapshot']
                    if 'target_metadata' in previous:
                        prior['target_metadata'] = previous['target_metadata']
            if expected_sha256 == current_sha256:
                return "unchanged"
            backup = self.directory / ("core.previous-" + current_sha256)
            if backup.exists() or backup.is_symlink():
                safe_file(backup)
            atomic_copy(self.binary, backup, 0o600, current_sha256)
            if digest(backup) != current_sha256:
                raise CoreError("baseline changed while saving backup")
            value = {"schema": 1, "binary": str(self.binary), "old_sha256": current_sha256,
                     "new_sha256": expected_sha256, "backup": backup.name, "phase": "prepared", "prior": prior}
            if snapshot is not None:
                value['snapshot'] = snapshot
            if self.target_metadata is not None:
                value['target_metadata'] = self.target_metadata
            self.validate(value)
            self.save(value)
            try:
                self.service.stop()
                value["phase"] = "switching"
                self.save(value)
                atomic_copy(candidate, self.binary, self.file_mode, expected_sha256, self.file_owner)
                if digest(self.binary) != expected_sha256:
                    raise CoreError("activated core checksum mismatch")
                self.service.start()
                if not self.service.ready():
                    raise CoreError("candidate did not become ready")
                value["phase"] = "committed"
                self.save(value)
            except BaseException:
                self.restore(value)
                if prior is not None:
                    self.save(prior)
                raise
            return "committed"

    def validate_target_metadata(self):
        if self.target_metadata is not None:
            info = self.binary.stat()
            if (stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid) != (self.file_mode, *self.file_owner):
                raise CoreError('config permissions changed outside transaction')

    def rollback(self):
        with self.locked():
            value = self.load()
            if value["phase"] == "recovered" and digest(self.binary) == value["old_sha256"]:
                return "unchanged"
            pending = value["phase"] in ("prepared", "switching")
            self.restore(value)
            if pending and value.get("prior") is not None:
                self.save(value["prior"])
            return "recovered"

"""Pinned backend-only upgrade adapter for CoreTransaction.

Core replacement preserves the managed schema-5 configuration byte-for-byte.
A separate explicit connect-policy transaction edits three managed defaults only.
The caller holds the manager lock and explicitly acknowledges a maintenance restart.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time



class ConfigAccessMigration:
    """One reversible 0710 -> 0750 parent-mode migration, with an immutable record.

    No ownership, file bytes or child permissions change. The manager holds its
    node lock. A crash before/after chmod is resumed from the same private record.
    """
    def __init__(self, base, directory, binary, group):
        self.base, self.binary = Path(base), Path(binary)
        self.record = Path(directory)/'config-access.json'
        self.group = group

    def validate(self):
        safe_directory(self.base)
        safe_directory(self.record.parent)
        if self.record.parent.stat().st_mode & 0o077:
            raise CoreError('config access record directory must be private')
        info = self.base.stat()
        if (info.st_gid != self.group or self.group == 0 or
                stat.S_IMODE(info.st_mode) not in (0o710, 0o750)):
            raise CoreError('custom config parent permissions; review required')
        if self.record.exists() or self.record.is_symlink():
            safe_file(self.record)
            if self.record.stat().st_mode & 0o077 or self.record.stat().st_size > 4096:
                raise CoreError('unsafe config access record')
            value = json.loads(self.record.read_text())
            if (value.get('schema') != 1 or value.get('path') != str(self.base) or
                    value.get('uid') != info.st_uid or value.get('gid') != self.group or
                    value.get('before') != 0o710 or value.get('after') != 0o750 or
                    not re.fullmatch('[a-f0-9]{64}', value.get('legacy_binary_sha256', ''))):
                raise CoreError('invalid config access record')
            return value
        return None

    def set_mode(self, mode):
        descriptor = os.open(self.base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(descriptor)
            if (info.st_ino, info.st_dev) != (self.base.stat().st_ino, self.base.stat().st_dev):
                raise CoreError('config directory changed during migration')
            os.fchmod(descriptor, mode)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def prepare(self):
        value = self.validate()
        if stat.S_IMODE(self.base.stat().st_mode) == 0o750:
            return False
        if value is None:
            value = {'schema': 1, 'path': str(self.base), 'uid': self.base.stat().st_uid,
                     'gid': self.group, 'before': 0o710, 'after': 0o750,
                     'legacy_binary_sha256': digest(self.binary)}
            with tempfile.TemporaryDirectory(prefix='.config-access-', dir=self.record.parent) as name:
                staged = Path(name)/'record'
                durable_file(staged, (json.dumps(value, sort_keys=True)+'\n').encode(), 0o600)
                os.link(staged, self.record)
            sync_directory(self.record.parent)
        elif digest(self.binary) != value['legacy_binary_sha256']:
            raise CoreError('config access baseline changed; review required')
        self.set_mode(0o750)
        return True

    def restore(self):
        value = self.validate()
        # Never restrict a newer core restored from a prior committed transaction.
        if value is not None and digest(self.binary) == value['legacy_binary_sha256']:
            self.set_mode(0o710)
            return True
        return False


class BackendService:
    def __init__(self, binary, config, work, token_file, api_port=19091,
                 unit='telemt-dual-hop-telemt.service', user='telemt', migrate_access=False):
        self.binary, self.config, self.work = Path(binary), Path(config), Path(work)
        self.token_file, self.api_port = Path(token_file), api_port
        self.unit = unit
        self.migrate_access = migrate_access
        identity = pwd.getpwnam(user)
        if identity.pw_uid == 0 or identity.pw_gid == 0:
            raise CoreError('unprivileged telemt identity required')
        self.identity = dict(user=identity.pw_uid, group=identity.pw_gid, extra_groups=[])

    def version(self, binary):
        result = subprocess.run([str(binary), '--version'], capture_output=True,
                                timeout=5, check=True, cwd='/', **self.identity)
        return result.stdout.decode('ascii').strip()

    def validate_candidate(self, binary):
        if not self.running_binary_matches():
            raise CoreError('service process does not run the expected current core')
        # Upstream's bounded healthcheck parses the old strict config without
        # launching a new listener or generation, then probes the current API.
        result = subprocess.run([str(binary), 'healthcheck', '--mode', 'ready', str(self.config)],
                                cwd=self.work, capture_output=True, timeout=12, **self.identity)
        if result.returncode:
            raise CoreError('candidate rejects old config or current backend is not ready')

    def running_binary_matches(self):
        result = subprocess.run(['systemctl', 'show', self.unit,
                                 '--property=MainPID', '--value'], capture_output=True, text=True, timeout=5, check=True)
        pid = result.stdout.strip()
        if not pid.isdecimal() or int(pid) <= 0:
            return False
        try:
            return digest(Path('/proc')/pid/'exe') == digest(self.binary)
        except OSError:
            return False

    def stop(self):
        subprocess.run(['systemctl', 'stop', self.unit],
                       check=True, capture_output=True, timeout=40)

    def start(self):
        subprocess.run(['systemctl', 'start', self.unit],
                       check=True, capture_output=True, timeout=40)

    def ready(self):
        # Control-plane readiness + actual process image; protocol is tested separately.
        import http.client
        token = self.token_file.read_text(encoding='ascii').strip()
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            connection = http.client.HTTPConnection('127.0.0.1', self.api_port, timeout=2)
            try:
                connection.request('GET', '/v1/health/ready', headers={'Authorization': 'Bearer '+token})
                response = connection.getresponse()
                raw = response.read(16385)
                data = json.loads(raw) if len(raw) <= 16384 else None
                if (response.status == 200 and isinstance(data, dict) and data.get('ok') is True
                        and isinstance(data.get('data'), dict) and data['data'].get('ready') is True
                        and self.running_binary_matches()):
                    return True
            except (OSError, ValueError):
                pass
            finally:
                connection.close()
            time.sleep(1)
        return False


class HealthService:
    def __init__(self, bind):
        import ipaddress
        self.bind = str(ipaddress.IPv4Address(bind))
    def version(self, path):
        import ast
        ast.parse(Path(path).read_text(encoding='utf-8'))
        return digest(path)
    def stop(self):
        subprocess.run(['systemctl', 'stop', 'telemt-dual-hop-health-agent.service'],
                       check=True, capture_output=True, timeout=20)
    def start(self):
        subprocess.run(['systemctl', 'start', 'telemt-dual-hop-health-agent.service'],
                       check=True, capture_output=True, timeout=20)
    def ready(self):
        import socket
        deadline = time.monotonic()+45
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((self.bind, 19101), timeout=2) as connection:
                    connection.settimeout(2)
                    if connection.recv(16).strip() == b'up':
                        return True
            except OSError:
                pass
            time.sleep(1)
        return False


def refresh_health(base, bind, rollback_only=False, *, target=None, service=None):
    directory = Path(base)/'health-upgrade'
    if not directory.exists():
        directory.mkdir(mode=0o700)
    target = Path(target or '/usr/local/lib/telemt-dual-hop/health_agent.py')
    transaction = CoreTransaction(target, directory, service or HealthService(bind))
    if rollback_only:
        return transaction.rollback()
    source = globals().get('HEALTH_AGENT_SOURCE')
    if source is None:
        source = Path(__file__).with_name('health_agent.py').read_text(encoding='utf-8')
    with tempfile.TemporaryDirectory(prefix='.health-candidate-', dir=target.parent) as temp:
        candidate = Path(temp)/'health_agent.py'
        durable_file(candidate, source.encode('utf-8'), 0o755)
        expected = digest(candidate)
        return transaction.activate(candidate, expected, expected, digest(target))


MAX_CONNECT_CONFIG_BYTES = 1024 * 1024
MAX_CONNECT_POLICY_BACKUPS = 8


def bounded_connect_config(content):
    """Edit three managed defaults only; custom values/layouts fail closed."""
    if len(content) > MAX_CONNECT_CONFIG_BYTES:
        raise CoreError('connect config exceeds 1MiB bound')
    text = content.decode('utf8')
    if any(marker in text for marker in ('\x00', '\ufeff', '\"\"\"', "'''")):
        raise CoreError('custom TOML layout; review required')
    lines = text.splitlines(keepends=True)
    headers = [i for i,line in enumerate(lines) if re.fullmatch(r'\[general\][ \t]*(?:#[^\r\n]*)?\r?\n?', line)]
    if len(headers) != 1:
        raise CoreError('one managed general table required')
    start = headers[0]+1
    end = next((i for i in range(start,len(lines)) if lines[i].lstrip().startswith('[')),len(lines))
    defaults = {'tg_connect':10, 'upstream_connect_retry_attempts':2, 'upstream_connect_budget_ms':3000}
    desired = {'tg_connect':4, 'upstream_connect_retry_attempts':2, 'upstream_connect_budget_ms':9000}
    seen = set()
    for i in range(start,end):
        for key in desired:
            if not re.match(r'\s*'+key+r'\s*=', lines[i]): continue
            match = re.fullmatch(r'(\s*'+key+r'\s*=\s*)([0-9]+)([ \t]*(?:#[^\r\n]*)?)(\r?\n)?', lines[i])
            if key in seen or match is None or int(match[2]) not in (defaults[key],desired[key]):
                raise CoreError('custom connect settings; review required')
            seen.add(key)
            lines[i] = match[1]+str(desired[key])+match[3]+(match[4] or '')
    newline = '\r\n' if lines[start-1].endswith('\r\n') else '\n'
    if not lines[start-1].endswith('\n'): lines[start-1] += newline
    lines[start:start] = [f'{key} = {value}{newline}' for key,value in desired.items() if key not in seen]
    return ''.join(lines).encode('utf8')


class ConnectPolicyService:
    def __init__(self, backend): self.backend = backend
    def version(self, path): return digest(path)
    def stop(self): return self.backend.stop()
    def start(self): return self.backend.start()
    def ready(self): return self.backend.ready()


def refresh_connect_policy(base, service, rollback_only=False):
    target = service.config
    safe_directory(Path(base))
    safe_file(target)
    info = target.stat()
    if stat.S_IMODE(info.st_mode) != 0o640 or info.st_gid != service.identity['group']:
        raise CoreError('custom config ownership or mode; review required')
    if not rollback_only and info.st_size > MAX_CONNECT_CONFIG_BYTES:
        raise CoreError('connect config exceeds 1MiB bound')
    directory = Path(base)/'connect-policy'
    if not directory.exists(): directory.mkdir(mode=0o700)
    transaction = CoreTransaction(target, directory, ConnectPolicyService(service),
                                  file_mode=0o640, file_owner=(info.st_uid,info.st_gid))
    if rollback_only: return transaction.rollback()
    with target.open('rb') as source:
        original = source.read(MAX_CONNECT_CONFIG_BYTES + 1)
    baseline = hashlib.sha256(original).hexdigest()
    content = bounded_connect_config(original)
    expected = hashlib.sha256(content).hexdigest()
    if transaction.journal.exists() and transaction.load()['phase'] not in ('committed','recovered'):
        raise CoreError('unfinished connect transaction; run connect-rollback first')
    if digest(target) != baseline:
        raise CoreError('config changed while preparing connect policy')
    if expected == baseline:
        if not service.ready(): raise CoreError('backend is not ready')
        return 'unchanged'
    if (len(list(directory.glob('core.previous-*'))) >= MAX_CONNECT_POLICY_BACKUPS
            and not (directory/('core.previous-'+baseline)).exists()):
        raise CoreError('connect backup limit reached; privately archive old backups before maintenance')
    with tempfile.TemporaryDirectory(prefix='.connect-candidate-',dir=target.parent) as name:
        stage=Path(name)
        os.chown(stage,info.st_uid,info.st_gid); stage.chmod(0o750)
        candidate=stage/'telemt.toml'
        durable_file(candidate,content,0o640)
        os.chown(candidate,info.st_uid,info.st_gid)
        # Parse using the installed core before stopping the current service.
        result=subprocess.run([str(service.binary),'healthcheck','--mode','ready',str(candidate)],
            cwd=service.work,capture_output=True,timeout=12,**service.identity)
        if result.returncode or not service.running_binary_matches():
            raise CoreError('connect candidate validation or running core identity failed')
        return transaction.activate(candidate,expected,expected,baseline)


def protected_files(roots):
    files = {}
    for root in map(Path, roots):
        if root.is_symlink() or root.resolve() != root:
            raise CoreError('protected path contains a symlink')
        candidates = [root] if root.is_file() else sorted(root.rglob('*')) if root.is_dir() else []
        if not root.exists():
            raise CoreError('required protected path is missing')
        if not root.is_file() and not root.is_dir():
            raise CoreError('protected root must be a regular file or directory')
        if root.is_dir():
            safe_directory(root)
        for path in candidates:
            if path.is_symlink():
                raise CoreError('protected tree contains a symlink')
            if path.is_dir():
                safe_directory(path)
                continue
            safe_file(path)
            if path.stat().st_size > 16*1024**2 or len(files) >= 128:
                raise CoreError('protected state exceeds backup bounds')
            files[str(path)] = {'sha256': digest(path), 'mode': stat.S_IMODE(path.stat().st_mode)}
    if sum(Path(name).stat().st_size for name in files) > 32*1024**2:
        raise CoreError('protected backup exceeds total byte budget')
    return files


def save_snapshot(directory, roots):
    """Only hashes and fixed paths in the manifest; all copied secrets stay 0600."""
    files = protected_files(roots)
    folder = Path(tempfile.mkdtemp(prefix='snapshot-', dir=directory))
    try:
        for index, (name, metadata) in enumerate(sorted(files.items())):
            metadata['copy'] = str(index)
            atomic_copy(name, folder/str(index), 0o600, metadata['sha256'])
        durable_file(folder/'manifest.json', (json.dumps(files, sort_keys=True)+'\n').encode(), 0o600)
        sync_directory(folder)
        sync_directory(directory)
        return folder
    except BaseException:
        shutil.rmtree(folder)
        raise


def unchanged_snapshot(folder, roots):
    safe_directory(folder)
    safe_file(Path(folder)/'manifest.json')
    files = json.loads((Path(folder)/'manifest.json').read_text())
    current = protected_files(roots)
    expected = {name: {k: row[k] for k in ('sha256', 'mode')} for name, row in files.items()}
    if current != expected:
        raise CoreError('protected configuration changed; manual review required before rollback')
    for row in files.values():
        if not re.fullmatch(r'[0-9]{1,3}', row['copy']):
            raise CoreError('invalid snapshot member')
        source = Path(folder)/row['copy']
        safe_file(source)
        if digest(source) != row['sha256']:
            raise CoreError('protected backup checksum mismatch')


def preserve_audited_archive(directory, archive, archive_sha, binary_sha):
    """Keep exact source/license bytes without changing rollback-protected files."""
    if not re.fullmatch(r'[a-f0-9]{64}', binary_sha):
        raise CoreError('invalid audited core identity')
    directory=Path(directory)
    safe_directory(directory)
    if directory.stat().st_mode & 0o077:
        raise CoreError('audited archive storage must be private')
    target=directory/('audited-core-'+binary_sha+'.tar.gz')
    if target.exists() or target.is_symlink():
        safe_file(target)
        if stat.S_IMODE(target.stat().st_mode)!=0o600 or digest(target)!=archive_sha:
            raise CoreError('audited archive cache changed; review required')
    else:
        if len(list(directory.glob('audited-core-*.tar.gz')))>=8:
            raise CoreError('audited archive retention limit reached; archive old notices first')
        atomic_copy(archive,target,0o600,archive_sha)
    return target


def upgrade(binary, directory, archive, archive_sha, binary_sha, version, roots, service, *, audited=False):
    safe_directory(directory)
    if Path(directory).stat().st_mode & 0o077:
        raise CoreError('transaction storage must be private')
    safe_file(binary)
    tx = CoreTransaction(binary, directory, service)
    if tx.journal.exists() or tx.journal.is_symlink():
        previous = tx.load()
        if previous['phase'] in ('prepared', 'switching'):
            raise CoreError('unfinished transaction; run core-rollback first')
    old = digest(binary)
    if old == binary_sha:
        if not service.ready():
            raise CoreError('installed target core is not ready; diagnose or recover it first')
        if audited:
            # An already-installed candidate may predate source retention.
            # Attach or revalidate its exact archive without restarting it.
            archive_payload(archive,archive_sha,version,binary_sha,True)
            preserve_audited_archive(directory,archive,archive_sha,binary_sha)
        return 'unchanged'
    if len(list(Path(directory).glob('snapshot-*'))) >= 8:
        raise CoreError('snapshot retention limit reached; archive old backups before upgrading')
    payload, _ = archive_payload(archive, archive_sha, version, binary_sha, audited)
    if audited:
        preserve_audited_archive(directory,archive,archive_sha,binary_sha)
    # Stage on an executable root-owned filesystem, readable by telemt only
    # through the temporary 0755 directory; no credentials are staged here.
    with tempfile.TemporaryDirectory(prefix='.core-candidate-', dir=Path(binary).parent) as temp:
        stage = Path(temp)
        candidate = stage/'telemt'
        durable_file(candidate, payload, 0o755)
        stage.chmod(0o755)
        snapshot = save_snapshot(directory, roots)
        access = (ConfigAccessMigration(service.config.parent.parent, directory, binary,
                                       service.identity['group']) if getattr(service, 'migrate_access', False) else None)
        migrated = False
        try:
            if access is not None:
                if stat.S_IMODE(access.base.stat().st_mode) == 0o710:
                    backup = Path(directory)/('core.previous-'+old)
                    if backup.exists() or backup.is_symlink():
                        safe_file(backup)
                    # Config/key/unit snapshot and original binary are durable
                    # before the first permissions change, even before preflight.
                    atomic_copy(binary, backup, 0o600, old)
                migrated = access.prepare()
            service.validate_candidate(candidate)
            # Bind the snapshot to the exact durable journal, including recovery.
            result = tx.activate(candidate, binary_sha, 'telemt '+version, old, snapshot=snapshot.name)
            unchanged_snapshot(snapshot, roots)
            return result
        except BaseException:
            if migrated:
                access.restore()
            # A preflight failure must not consume the finite snapshot budget.
            # Keep any snapshot referenced by the durable recovery journal.
            if not tx.journal.exists() or tx.load().get('snapshot') != snapshot.name:
                shutil.rmtree(snapshot)
            raise


def rollback(binary, directory, roots, service):
    transaction = CoreTransaction(binary, directory, service)
    access = (ConfigAccessMigration(service.config.parent.parent, directory, binary,
                                   service.identity['group']) if getattr(service, 'migrate_access', False) else None)
    if not transaction.journal.exists() and access is not None and access.record.exists():
        if not access.restore():
            raise CoreError('config access recovery baseline changed; review required')
        return 'access-restored'
    value = transaction.load()
    name = value.get('snapshot', '')
    if not re.fullmatch(r'snapshot-[a-z0-9_]{8}', name):
        raise CoreError('missing or invalid transaction snapshot')
    unchanged_snapshot(Path(directory)/name, roots)
    result = transaction.rollback()
    if access is not None:
        access.restore()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('upgrade', 'rollback', 'refresh-health', 'rollback-health', 'refresh-connect', 'rollback-connect'))
    parser.add_argument('--binary', required=True)
    parser.add_argument('--directory', required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--work', required=True)
    parser.add_argument('--token-file', required=True)
    parser.add_argument('--protect', action='append', required=True)
    parser.add_argument('--archive')
    parser.add_argument('--archive-sha')
    parser.add_argument('--binary-sha')
    parser.add_argument('--version')
    parser.add_argument('--audited',action='store_true')
    parser.add_argument('--bind')
    args = parser.parse_args()
    if args.audited and args.action!='upgrade':
        parser.error('audited package validation is only available for upgrade')
    if os.geteuid() != 0:
        raise CoreError('root required')
    if args.action in ('refresh-health', 'rollback-health'):
        result = refresh_health(Path(args.directory).parent, args.bind, args.action == 'rollback-health')
        print('健康代理事务结果：'+result)
        return
    service = BackendService(args.binary, args.config, args.work, args.token_file, migrate_access=True)
    if args.action in ('refresh-connect','rollback-connect'):
        print('连接策略事务结果：'+refresh_connect_policy(Path(args.directory).parent,service,args.action=='rollback-connect'))
        return
    if args.action == 'upgrade':
        result = upgrade(args.binary, args.directory, args.archive, args.archive_sha,
                         args.binary_sha, args.version, args.protect, service, audited=args.audited)
    else:
        result = rollback(args.binary, args.directory, args.protect, service)
    print('核心事务结果：'+result)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('核心事务失败：'+(str(error) if isinstance(error, (CoreError, InstallError)) else type(error).__name__), file=sys.stderr)
        raise SystemExit(1)
# END GENERATED CORE UPGRADE
TDH_CORE_UPGRADE_PY
  [[ -z $tmp ]] || rm -rf -- "$tmp"
  (( result == 0 )) || die "核心事务未完成；请保留 core-upgrade 备份并检查状态，必要时执行 core-rollback。"
}

backend_maintenance() {
  local action=$1 index=$2
  [[ $index == 1 || $index == 2 ]] || die "后端编号只能为 1 或 2。"
  load_state || die "未安装节点。"
  require_current_state
  [[ $TDH_ROLE == entry ]] || die "此命令只能在入口运行。"
  python3 - "$action" "$index" <<'TDH_ENTRY_MAINTENANCE_PY'
# BEGIN GENERATED ENTRY MAINTENANCE
"""Fixed HAProxy runtime operations; no shell, untrusted commands or peer logins."""
import csv
import io
import socket
import sys


def exchange(command):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(3)
        connection.connect('/run/telemt-dual-hop/haproxy.sock')
        connection.sendall((command+'\n').encode('ascii'))
        connection.shutdown(socket.SHUT_WR)
        output = bytearray()
        while True:
            part = connection.recv(16384)
            if not part:
                return output.decode('ascii')
            output += part
            if len(output) > 262144:
                raise RuntimeError('HAProxy reply exceeds limit')


def maintain(action, index, request=exchange):
    if action not in ('drain', 'resume') or index not in ('1', '2'):
        raise ValueError('fixed action and backend index required')
    if action == 'drain':
        rows = list(csv.DictReader(io.StringIO(request('show stat').lstrip('# '))))
        peer = 'vps'+('2' if index == '1' else '1')
        for backend in ('telemt_a', 'telemt_b'):
            matches = [row for row in rows if row.get('pxname') == backend and row.get('svname') == peer]
            if len(matches) != 1 or matches[0].get('status') != 'UP':
                raise RuntimeError('peer is not UP on both lines; refusing drain')
    state = 'drain' if action == 'drain' else 'ready'
    for backend in ('telemt_a', 'telemt_b'):
        reply = request(f'set server {backend}/vps{index} state {state}')
        if reply.strip():
            raise RuntimeError('HAProxy rejected state change; inspect both lines')
    return state


if __name__ == '__main__':
    try:
        if len(sys.argv) != 3:
            raise ValueError('exact arguments required')
        print('后端维护状态：'+maintain(sys.argv[1], sys.argv[2]))
    except Exception as error:
        print('入口维护失败：'+type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
# END GENERATED ENTRY MAINTENANCE
TDH_ENTRY_MAINTENANCE_PY
}

recover_backend() {
  load_state || die "未安装节点。"
  require_current_state
  [[ $TDH_ROLE == backend1 || $TDH_ROLE == backend2 ]] || die "此命令仅用于后端。"
  systemctl start "wg-quick@${WG_INTERFACE}.service"
  systemctl restart telemt-dual-hop-telemt.service telemt-dual-hop-health-agent.service
  diagnose
}

queue_is_configured() {
  [[ -e ${TDH_BASE}/queue/policy.json || -L ${TDH_BASE}/queue || \
     -e ${TDH_BASE}/queue/runtime.json || -L ${TDH_BASE}/queue/runtime.json || \
     -e ${TDH_BASE}/queue/stopping || -L ${TDH_BASE}/queue/stopping || -L ${TDH_BASE}/queue/policy.json || \
     -e /etc/systemd/system/wg-quick@tdh-b1.service.d/90-telemt-queue.conf || \
     -L /etc/systemd/system/wg-quick@tdh-b1.service.d/90-telemt-queue.conf || \
     -e /etc/systemd/system/wg-quick@tdh-b2.service.d/90-telemt-queue.conf || \
     -L /etc/systemd/system/wg-quick@tdh-b2.service.d/90-telemt-queue.conf ]]
}

queue_manager_guard() {
  if queue_is_configured && ! grep -Fqx '# TDH_QUEUE_PROTOCOL=1' "$1"; then
    die "实验队列尚未撤销，目标管理器不支持它；请先 a queue disable --maintenance，再更新或回退。"
  fi
}

manage_queue() {
  load_state || die "未安装节点。"
  require_current_state
  [[ $TDH_ROLE == backend1 || $TDH_ROLE == backend2 ]] || die "队列控制目前仅支持后端单隧道；不自动切分入口两条隧道的共享带宽。"
  [[ ( $TDH_ROLE == backend1 && $WG_INTERFACE == tdh-b1 ) || \
     ( $TDH_ROLE == backend2 && $WG_INTERFACE == tdh-b2 ) ]] || die "后端接口与角色不符。"
  python3 - "$TDH_BASE" "$WG_INTERFACE" "$TDH_MANAGER" "$@" <<'TDH_QUEUE_PY'
# BEGIN GENERATED QUEUE CONTROL
"""Opt-in backend WG egress shaping; no daemon, downloads or global networking.

The manager holds its ordinary lock for operator actions. WG PostStart/PostStop
uses a separate bounded lock, so restarting WG never deadlocks on the manager.
Failure of the optional hook must not fail the tunnel or Telemt dependency.
"""
import argparse
from contextlib import contextmanager
from decimal import Decimal
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile


class QueueError(RuntimeError):
    pass


def run(*args):
    try:
        return subprocess.run(args, check=True, capture_output=True, text=True, timeout=10).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise QueueError(f'{Path(args[0]).name}操作失败；已保留恢复记录') from exc


def safe_directory(path):
    path=Path(path)
    info=path.lstat()
    if path.resolve()!=path or not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o022:
        raise QueueError('目录权限或类型异常')


def read_file(path, private=True, limit=65536):
    safe_directory(path.parent)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1 or
            info.st_mode & (0o077 if private else 0o022) or info.st_size>limit):
            raise QueueError('文件权限或类型异常')
        return stream.read(limit+1)


def durable(path, data, mode=0o600):
    safe_directory(path.parent)
    exists=path.exists() or path.is_symlink()
    if exists:read_file(path,mode==0o600)
    fd,name=tempfile.mkstemp(prefix='.queue-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data);os.fchmod(stream.fileno(),mode);stream.flush();os.fsync(stream.fileno())
        if exists:os.replace(name,path)
        else:os.link(name,path)
        sync(path.parent)
    finally:Path(name).unlink(missing_ok=True)


def sync(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)


def encoded(value):return (json.dumps(value,sort_keys=True)+'\n').encode()
def digest(value):return hashlib.sha256(encoded(value)).hexdigest()


def policy(interface,rate,path_mbps):
    if interface not in ('tdh-b1','tdh-b2'):
        raise QueueError('只支持后端单隧道；入口共享带宽尚未开放，不能给两条隧道各分配整机带宽')
    if any(type(v) is not int for v in (rate,path_mbps)) or not 1<=rate<=900 or not rate<path_mbps<=1000 or rate*10>path_mbps*9:
        raise QueueError('请输入1–1000Mbps内的实测路径容量，发送预算须至少预留10%，最大900Mbps')
    return {'schema':1,'interface':interface,'rate_mbps':rate,'path_mbps':path_mbps}


class KernelQueue:
    ROOT='7a00:'
    LEAF='7a01:'

    def __init__(self,interface):self.interface=interface

    def link(self):
        links=json.loads(run('ip','-d','-j','link','show'))
        rows=[v for v in links if v['ifname']==self.interface]
        if not rows:return None
        row=rows[0]
        if row.get('linkinfo',{}).get('info_kind')!='wireguard':raise QueueError('目标不是WireGuard接口')
        return {'boot':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'netns':os.stat('/proc/self/ns/net').st_ino,'ifindex':row['ifindex'],'txqlen':row.get('txqlen',0)}

    def shape(self):
        # Do not request -j/-s together: Ubuntu22.04 HTB prints legacy xstats.
        qdisc=run('tc','-d','qdisc','show','dev',self.interface)
        classes=run('tc','-d','class','show','dev',self.interface)
        headers=[line.strip() for line in qdisc.splitlines() if line.startswith('qdisc ')]
        if not headers:raise QueueError('缺少队列观察，拒绝修改')
        htb=[line.strip() for line in classes.splitlines() if line.startswith('class htb ')]
        other=[line for line in classes.splitlines() if line.startswith('class ') and not line.startswith(('class htb ','class fq_codel '))]
        if other:raise QueueError('发现未知分类，拒绝修改')
        # Live fq_codel flow classes and direct-packet counters are statistics,
        # not configuration. Everything else remains part of the fingerprint.
        headers=[re.sub(r' direct_packets_stat \d+| refcnt \d+','',line) for line in headers]
        return {'qdisc':headers,'class':htb}

    def fresh(self):
        link=self.link()
        if link is None:return False
        shape=self.shape()
        return len(shape['qdisc'])==1 and shape['qdisc'][0].startswith('qdisc noqueue 0: root') and not shape['class']

    def owned(self,shape,rate,complete=True):
        kinds=[re.match(r'qdisc (\S+) (\S+) ',s).groups() for s in shape['qdisc']]
        expected=[('htb',self.ROOT),('fq_codel',self.LEAF)]
        if not kinds or any(v not in expected for v in kinds) or len(kinds)!=len(set(kinds)):
            raise QueueError('队列不属于本事务，拒绝覆盖或删除')
        if ('htb',self.ROOT) not in kinds or (complete and set(kinds)!=set(expected)):
            raise QueueError('队列结构不完整')
        if any(' root' not in s for s in shape['qdisc'] if s.startswith('qdisc htb ')):
            raise QueueError('根队列结构变化')
        if any(f' parent {self.ROOT}10 ' not in s+' ' for s in shape['qdisc'] if s.startswith('qdisc fq_codel ')):
            raise QueueError('叶队列结构变化')
        if len(shape['class'])>(1 if not complete else 1) or (complete and len(shape['class'])!=1):
            raise QueueError('HTB分类发生变化')
        for line in shape['class']:
            if not line.startswith(f'class htb {self.ROOT}10 root'):
                raise QueueError('HTB分类归属异常')
            for name in ('rate','ceil'):
                found=re.search(r'\b'+name+r' (\d+(?:\.\d+)?)([KMG]?)bit\b',line)
                if not found or Decimal(found[1])*{'':1,'K':1000,'M':1000000,'G':1000000000}[found[2]]!=rate*1000000:
                    raise QueueError('队列带宽发生漂移，拒绝覆盖')
        if run('tc','filter','show','dev',self.interface,'parent',self.ROOT):
            raise QueueError('发现自定义过滤器，拒绝覆盖')

    def create(self,rate):
        run('ip','link','set','dev',self.interface,'txqueuelen','1000')
        run('tc','qdisc','add','dev',self.interface,'root','handle',self.ROOT,'htb','default','10')
        run('tc','class','add','dev',self.interface,'parent',self.ROOT,'classid',self.ROOT+'10',
            'htb','rate',f'{rate}mbit','ceil',f'{rate}mbit','quantum','1514')
        run('tc','qdisc','add','dev',self.interface,'parent',self.ROOT+'10','handle',self.LEAF,'fq_codel')

    def restore(self,rate,baseline,expected=None):
        link=self.link()
        if link is None:return
        if expected is not None and link['txqlen']!=1000:raise QueueError('接口队列长度发生漂移')
        shape=self.shape()
        if len(shape['qdisc'])==1 and shape['qdisc'][0].startswith('qdisc noqueue 0: root') and not shape['class']:
            if expected is not None or link['txqlen'] not in (0,1000):raise QueueError('原队列已经变化，保留现场')
            run('ip','link','set','dev',self.interface,'txqueuelen',str(baseline))
            return
        self.owned(shape,rate,complete=expected is not None)
        if expected is not None and digest(shape)!=expected:raise QueueError('队列配置发生漂移，保留现场')
        run('tc','qdisc','del','dev',self.interface,'root')
        run('ip','link','set','dev',self.interface,'txqueuelen',str(baseline))
        if not self.fresh() or self.link()['txqlen']!=baseline:raise QueueError('队列恢复未完成，保留记录')


class QueueControl:
    def __init__(self,directory,interface,manager,dropin=None):
        self.directory=Path(directory);self.interface=interface;self.manager=Path(manager)
        self.dropin=Path(dropin or f'/etc/systemd/system/wg-quick@{interface}.service.d/90-telemt-queue.conf')
        self.config=self.directory/'policy.json';self.journal=self.directory/'runtime.json'
        self.stopping=self.directory/'stopping'
        self.kernel=KernelQueue(interface)

    @contextmanager
    def locked(self,create=False):
        safe_directory(self.directory.parent)
        if create:self.directory.mkdir(mode=0o700,exist_ok=True)
        safe_directory(self.directory)
        if self.directory.stat().st_mode & 0o077:raise QueueError('队列记录目录必须为0700')
        fd=os.open(self.directory/'lock',os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK|(os.O_CREAT if create else 0),0o600)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1 or info.st_mode & 0o077:
                raise QueueError('队列锁不安全')
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise QueueError('队列操作正在进行，请稍后重试') from None
            yield
        finally:os.close(fd)

    def desired(self):
        value=json.loads(read_file(self.config))
        if type(value) is not dict or type(value.get('schema')) is not int or set(value)!={'schema','interface','rate_mbps','path_mbps'} or value!=policy(self.interface,value.get('rate_mbps'),value.get('path_mbps')):
            raise QueueError('队列策略内容无效')
        return value

    def record(self,value):durable(self.journal,encoded(value))

    def prior(self,value):
        if not self.journal.exists() and not self.journal.is_symlink():return None
        record=json.loads(read_file(self.journal))
        if (type(record) is not dict or type(record.get('schema')) is not int or record.get('schema')!=1 or record.get('policy')!=digest(value) or
            type(record.get('baseline_txqlen')) is not int or not 0<=record['baseline_txqlen']<=10000 or record.get('phase') not in ('applying','active') or
            type(record.get('epoch')) is not dict):raise QueueError('恢复记录与策略不一致')
        epoch=record['epoch']
        if (set(epoch)!={'boot','netns','ifindex'} or not isinstance(epoch['boot'],str) or
            not re.fullmatch('[a-f0-9-]{36}',epoch['boot']) or
            any(type(epoch[k]) is not int or epoch[k]<=0 for k in ('netns','ifindex'))):raise QueueError('接口身份记录无效')
        if record['phase']=='active' and not re.fullmatch('[a-f0-9]{64}',record.get('fingerprint','')):
            raise QueueError('恢复记录缺少队列指纹')
        return record

    def apply(self,value):
        link=self.kernel.link()
        if link is None:raise QueueError('WireGuard接口未启动；未改变服务')
        epoch={k:link[k] for k in ('boot','netns','ifindex')}
        record=self.prior(value)
        if record and record['epoch']==epoch:
            shape=self.kernel.shape()
            if record['phase']=='active':
                self.kernel.owned(shape,value['rate_mbps'])
                if digest(shape)!=record['fingerprint'] or link['txqlen']!=1000:raise QueueError('队列配置发生漂移')
                return 'unchanged'
            self.kernel.restore(value['rate_mbps'],record['baseline_txqlen'])
        if not self.kernel.fresh():raise QueueError('接口已有自定义队列；拒绝接管')
        baseline=self.kernel.link()['txqlen']
        if type(baseline) is not int or not 0<=baseline<=10000:raise QueueError('原接口队列长度超出安全恢复范围')
        record={'schema':1,'policy':digest(value),'baseline_txqlen':baseline,'epoch':epoch,'phase':'applying'}
        self.record(record)
        try:
            self.kernel.create(value['rate_mbps'])
            shape=self.kernel.shape();self.kernel.owned(shape,value['rate_mbps'])
            record.update(phase='active',fingerprint=digest(shape));self.record(record)
        except BaseException:
            self.kernel.restore(value['rate_mbps'],baseline)
            self.journal.unlink(missing_ok=True);sync(self.directory)
            raise
        return 'applied'

    def remove(self,value):
        record=self.prior(value)
        link=self.kernel.link()
        if record and link is not None:
            epoch={k:link[k] for k in ('boot','netns','ifindex')}
            if record['epoch']==epoch:
                self.kernel.restore(value['rate_mbps'],record['baseline_txqlen'],record.get('fingerprint'))
            elif not self.kernel.fresh():raise QueueError('接口身份变化且有自定义队列，保留现场')
        elif link is not None and not self.kernel.fresh():
            raise QueueError('缺少归属记录，拒绝删除队列')
        self.journal.unlink(missing_ok=True);sync(self.directory)

    def hook(self):
        if not re.fullmatch(r'/[A-Za-z0-9_./-]+',str(self.manager)):raise QueueError('管理器路径不安全')
        return ('# Managed by Telemt Dual-Hop queue protocol 1\n[Service]\n'
            f'ExecStartPost=-{self.manager} --queue-service apply\n'
            f'ExecStopPost=-{self.manager} --queue-service remove\n').encode()

    def check_hook(self):
        if self.dropin.exists() or self.dropin.is_symlink():
            if read_file(self.dropin,False)!=self.hook():raise QueueError('队列启动配置发生漂移，拒绝覆盖')

    def enable(self,rate,path_mbps):
        value=policy(self.interface,rate,path_mbps)
        if b'# TDH_QUEUE_PROTOCOL=1\n' not in read_file(self.manager,False,1048576):raise QueueError('已安装管理器不支持队列控制，请先更新管理器')
        with self.locked(True):
            if self.stopping.exists() or self.stopping.is_symlink():raise QueueError('上次撤销未完成，请先运行queue disable --maintenance')
            self.check_hook()
            new=not self.config.exists() and not self.config.is_symlink()
            if self.config.exists() or self.config.is_symlink():
                if self.desired()!=value:raise QueueError('已有不同策略，请先撤销后重新启用')
            elif not self.kernel.fresh():raise QueueError('接口未就绪或已有自定义队列；未接管')
            if not self.dropin.parent.exists():
                safe_directory(self.dropin.parent.parent);self.dropin.parent.mkdir(mode=0o755)
            safe_directory(self.dropin.parent)
            try:
                durable(self.config,encoded(value));durable(self.dropin,self.hook(),0o644)
                run('systemctl','daemon-reload')
                return self.apply(value)
            except Exception:
                # Ordinary failures undo a fresh enable after kernel rollback.
                # SIGKILL leaves the durable intent/journal for safe resumption.
                if new and not self.journal.exists() and not self.journal.is_symlink():
                    self.check_hook()
                    if self.dropin.exists():self.dropin.unlink();sync(self.dropin.parent)
                    self.config.unlink(missing_ok=True);sync(self.directory)
                    run('systemctl','daemon-reload')
                raise

    def disable(self):
        if not self.directory.exists() and not self.directory.is_symlink():
            if self.dropin.exists() or self.dropin.is_symlink():raise QueueError('缺少策略归属信息，保留启动配置')
            return 'disabled'
        with self.locked(True):
            self.check_hook()
            durable(self.stopping,b'1\n')
            if self.config.exists() or self.config.is_symlink():self.remove(self.desired())
            elif self.journal.exists() or self.journal.is_symlink():raise QueueError('缺少策略，保留恢复记录')
            elif self.kernel.link() is not None and not self.kernel.fresh():raise QueueError('缺少策略归属，拒绝清理当前队列')
            if self.dropin.exists():self.dropin.unlink();sync(self.dropin.parent)
            run('systemctl','daemon-reload')
            self.config.unlink(missing_ok=True);sync(self.directory)
            self.stopping.unlink();sync(self.directory)
        return 'disabled'

    def service(self,action):
        if not self.config.exists() and not self.config.is_symlink():return 'disabled'
        with self.locked(True):
            self.check_hook();value=self.desired()
            if action=='apply' and (self.stopping.exists() or self.stopping.is_symlink()):
                if read_file(self.stopping)!=b'1\n':raise QueueError('撤销记录无效')
                return 'removal-pending'
            return self.apply(value) if action=='apply' else self.remove(value)

    def status(self):
        if not self.directory.exists() and not self.directory.is_symlink():return {'enabled':False}
        with self.locked():
            if not self.config.exists() and not self.config.is_symlink():
                if self.journal.exists() or self.dropin.exists():raise QueueError('缺少策略，需检查恢复记录')
                return {'enabled':False}
            value=self.desired();self.check_hook();record=self.prior(value);link=self.kernel.link()
            active=False
            if record and link and record['phase']=='active':
                active=(record['epoch']=={k:link[k] for k in ('boot','netns','ifindex')} and
                    link['txqlen']==1000 and digest(self.kernel.shape())==record['fingerprint'])
            return dict(value,enabled=True,active=active,boot_hook=self.dropin.exists(),removal_pending=self.stopping.exists())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base');parser.add_argument('interface');parser.add_argument('manager')
    parser.add_argument('action',choices=('enable','disable','status','apply','remove'))
    parser.add_argument('rate',nargs='?',type=int);parser.add_argument('path_mbps',nargs='?',type=int)
    parser.add_argument('--maintenance',action='store_true')
    args=parser.parse_args();os.umask(0o077)
    if os.geteuid()!=0:raise QueueError('需要root权限')
    if args.action in ('enable','disable') and not args.maintenance:raise QueueError('请先排空本后端并添加--maintenance')
    if args.action!='enable' and (args.rate is not None or args.path_mbps is not None):raise QueueError('参数数量无效')
    if args.interface not in ('tdh-b1','tdh-b2'):raise QueueError('只支持后端单隧道，入口共享整形尚未开放')
    control=QueueControl(Path(args.base)/'queue',args.interface,args.manager)
    result=(control.enable(args.rate,args.path_mbps) if args.action=='enable' else
            control.disable() if args.action=='disable' else control.status() if args.action=='status' else control.service(args.action))
    print(json.dumps({'queue':result},ensure_ascii=False))


if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('队列操作失败：'+(str(exc) if isinstance(exc,QueueError) else type(exc).__name__)+'；代理服务不会因此重启。',file=sys.stderr)
        raise SystemExit(1)
# END GENERATED QUEUE CONTROL
TDH_QUEUE_PY
}

queue_panel() {
  local action rate path_capacity
  manage_queue status
  printf '\n实验功能默认关闭，仅控制本后端向入口的发送队列；不会重启代理。\n'
  printf '请先在入口排空本后端，并确认另一节点正常。不要用端口标称带宽代替实测路径容量。\n'
  printf '1) 启用  2) 撤销  0) 返回\n'
  read -r -p '请选择 [0]: ' action </dev/tty || return 0
  case ${action:-0} in
    1)
      read -r -p '本后端到入口的已测路径容量（整数Mbps，无默认）: ' path_capacity </dev/tty || return 0
      read -r -p '留给该隧道的发送预算（整数Mbps，须预留至少10%）: ' rate </dev/tty || return 0
      manage_queue enable "$rate" "$path_capacity" --maintenance ;;
    2) manage_queue disable --maintenance ;;
    0) ;;
    *) warn "无效选择。" ;;
  esac
}

select_amd64_core() {
  local version expected binary_sha url
  case ${TDH_NEW_INSTALL_CORE:-official} in
    official)
      version=$TELEMT_VERSION expected=$TELEMT_SHA256_AMD64
      binary_sha=$TELEMT_BINARY_SHA256_AMD64
      url="https://github.com/telemt/telemt/releases/download/${version}/telemt-x86_64-linux-gnu.tar.gz" ;;
    balanced2)
      version=$TELEMT_CORE_VERSION_AMD64 expected=$TELEMT_CORE_ARCHIVE_SHA256_AMD64
      binary_sha=$TELEMT_CORE_BINARY_SHA256_AMD64 url=$TELEMT_CORE_URL_AMD64 ;;
    audit2)
      version=$TELEMT_AUDIT2_VERSION expected=$TELEMT_AUDIT2_ARCHIVE_SHA256
      binary_sha=$TELEMT_AUDIT2_BINARY_SHA256 url=$TELEMT_AUDIT2_URL ;;
    *) die "新安装核心选择无效；仅支持 official、balanced2 或 audit2。" ;;
  esac
  printf '%s\t%s\t%s\t%s\t%s\n' "${url##*/}" "$version" "$expected" "$binary_sha" "$url"
}

download_telemt() {
  local asset expected url tmp version binary_sha="" mode selected
  case $(uname -m) in
    x86_64|amd64)
      selected=$(select_amd64_core) || return 1
      IFS=$'\t' read -r asset version expected binary_sha url <<<"$selected"
      mode=audited
      [[ $version != "$TELEMT_VERSION" ]] || mode=official
      info "安装固定版本 Telemt ${version}（${mode}）。"
      ;;
    aarch64|arm64)
      [[ ${TDH_NEW_INSTALL_CORE:-official} == official ]] || die "自选核心仅支持 x86_64；ARM 使用官方核心。"
      asset="telemt-aarch64-linux-gnu.tar.gz" expected=$TELEMT_SHA256_ARM64
      url="https://github.com/telemt/telemt/releases/download/${TELEMT_VERSION}/${asset}"
      version=$TELEMT_VERSION mode=official binary_sha=$TELEMT_BINARY_SHA256_ARM64
      info "ARM 使用官方 Telemt ${version}；本轮提速核心仅验证了 x86_64。"
      ;;
    *) die "Telemt 不支持当前架构。" ;;
  esac
  [[ ! -e $TDH_BIN && ! -L $TDH_BIN ]] || die "已存在 Telemt 核心；新安装不会覆盖它。"
  tmp=$(mktemp -d "${TDH_BASE}/core-download.XXXXXX")
  if ! curl -q -fL --retry 3 --retry-all-errors --connect-timeout 10 --max-time 180 --max-filesize 67108864 \
      --proto '=https' --proto-redir '=https' --tlsv1.2 "$url" -o "${tmp}/${asset}"; then
    rm -rf -- "$tmp"
    die "核心下载失败；安装已停止，不会悄悄改用其他构建。"
  fi
  install -d -m 0755 "$(dirname "$TDH_BIN")"
  if ! python3 - "${tmp}/${asset}" "$TDH_BIN" "$expected" "$version" "$binary_sha" "$mode" <<'TDH_CORE_INSTALL_PY'
# BEGIN GENERATED CORE INSTALLER
"""Validate a pinned core archive and stage a fresh install; never update a node.

This source is embedded verbatim in the single-file Bash installer. All archive
bytes and members are verified before any executable is run or installed.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import stat
import subprocess
import sys
import tarfile
import tempfile


class InstallError(RuntimeError):
    pass


AUDIT_FILES = {'telemt','TELEMT-LICENSE.txt','NOTICE.txt','source.patch','build-provenance.json','manifest.json'}


def checksum(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024*1024), b''):
            value.update(block)
    return value.hexdigest()


def durable_file(path, content, mode):
    with Path(path).open('xb') as output:
        output.write(content)
        output.flush()
        os.fchmod(output.fileno(), mode)
        os.fsync(output.fileno())


def archive_payload(path, expected_archive, version, expected_binary='', audited=False):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 64*1024**2:
        raise InstallError('invalid archive type or size')
    if checksum(path) != expected_archive:
        raise InstallError('archive checksum mismatch')
    files, names, total = {}, set(), 0
    with tarfile.open(path, mode='r|gz') as archive:
        for number, member in enumerate(archive, 1):
            name = PurePosixPath(member.name)
            if (number > 32 or name.is_absolute() or '..' in name.parts or not name.parts
                    or str(name) in names or (not member.isfile() and not member.isdir())):
                raise InstallError('unsafe or duplicate archive member')
            names.add(str(name))
            if member.isdir():
                if audited:
                    raise InstallError('unexpected directory in audited archive')
                continue
            if not 0 < member.size <= 64*1024**2:
                raise InstallError('invalid member size')
            total += member.size
            if total > 64*1024**2:
                raise InstallError('archive payload exceeds limit')
            if audited and str(name) not in AUDIT_FILES:
                raise InstallError('unexpected audited archive member')
            content = archive.extractfile(member).read(member.size+1)
            if len(content) != member.size:
                raise InstallError('incomplete archive member')
            files[str(name)] = content
    binaries = [content for name, content in files.items() if PurePosixPath(name).name == 'telemt']
    if len(binaries) != 1:
        raise InstallError('archive must contain one telemt binary')
    binary = binaries[0]
    if expected_binary and hashlib.sha256(binary).hexdigest() != expected_binary:
        raise InstallError('binary checksum mismatch')
    if audited:
        if set(files) != AUDIT_FILES:
            raise InstallError('incomplete audited archive')
        if len(binary) < 64 or binary[:6] != b'\x7fELF\x02\x01' or binary[18:20] != b'\x3e\x00':
            raise InstallError('x86_64 ELF required')
        manifest = json.loads(files['manifest.json'])
        if (manifest.get('schema') != 1 or manifest.get('version') != version
                or manifest.get('architecture') != 'x86_64' or manifest.get('os') != 'linux'
                or manifest.get('libc') != 'gnu' or manifest.get('minimum_glibc') != '2.35'
                or manifest.get('unofficial') is not True
                or set(manifest.get('files', {})) != AUDIT_FILES-{'manifest.json'}):
            raise InstallError('audited manifest identity mismatch')
        for name, metadata in manifest['files'].items():
            if (metadata.get('sha256') != hashlib.sha256(files[name]).hexdigest()
                    or metadata.get('bytes') != len(files[name])
                    or metadata.get('mode') != ('0755' if name == 'telemt' else '0644')):
                raise InstallError('audited manifest member mismatch')
    return binary, {name:files[name] for name in AUDIT_FILES-{'telemt'} if name in files}


def fresh_install(archive, target, expected_archive, version, expected_binary='', audited=False):
    target = Path(target)
    parent = target.parent
    if not target.is_absolute() or target.resolve() != target or target.exists() or target.is_symlink():
        raise InstallError('fresh install refuses an existing core or symlink')
    parent_info = parent.lstat()
    if not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid != os.geteuid() or parent_info.st_mode & 0o022:
        raise InstallError('unsafe installation directory')
    binary, documents = archive_payload(archive, expected_archive, version, expected_binary, audited)
    identity = pwd.getpwnam('telemt')
    if identity.pw_uid == 0 or identity.pw_gid == 0:
        raise InstallError('unprivileged telemt identity required')
    with tempfile.TemporaryDirectory(prefix='.telemt-install-', dir=parent) as temporary:
        stage = Path(temporary)
        candidate = stage/'telemt'
        durable_file(candidate, binary, 0o755)
        stage.chmod(0o755)
        result = subprocess.run([str(candidate),'--version'], user=identity.pw_uid, group=identity.pw_gid,
                                extra_groups=[], cwd='/', capture_output=True, timeout=5, check=True)
        if result.stdout.strip() != ('telemt '+version).encode() or len(result.stdout) > 256:
            raise InstallError('candidate runtime version mismatch')
        if audited:
            legal = stage/'core-source-notice'
            legal.mkdir(mode=0o755)
            for name, content in documents.items():
                document = legal/name
                durable_file(document, content, 0o644)
            destination = parent/'core-source-notice'
            if destination.exists() or destination.is_symlink():
                raise InstallError('refusing to replace existing core notice')
            os.rename(legal, destination)
        # Exclusive link cannot clobber a concurrent or pre-existing executable.
        os.link(candidate, target)
        directory = os.open(parent, os.O_RDONLY|os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    return version


if __name__ == '__main__':
    try:
        if os.geteuid() != 0 or len(sys.argv) != 7:
            raise InstallError('root and exact install arguments required')
        archive, target, archive_sha, version, binary_sha, mode = sys.argv[1:]
        if mode not in ('official','audited'):
            raise InstallError('unknown archive mode')
        fresh_install(archive, target, archive_sha, version, binary_sha, mode == 'audited')
    except Exception as error:
        # No untrusted archive fields, paths, environment or service logs escape.
        print('核心安装验证失败：'+(str(error) if isinstance(error, InstallError) else type(error).__name__), file=sys.stderr)
        raise SystemExit(1)
# END GENERATED CORE INSTALLER
TDH_CORE_INSTALL_PY
  then
    rm -rf -- "$tmp"
    die "核心未通过完整性、架构、许可清单或运行校验；安装已取消。"
  fi
  rm -rf -- "$tmp"
  ok "Telemt ${version} 已验证并安装。"
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
tg_connect = 4
upstream_connect_retry_attempts = 2
upstream_connect_budget_ms = 9000
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
TELEMT_PORT = int(os.environ.get("TDH_TELEMT_PORT", "24431"))
DCS = tuple(value for value in os.environ["TDH_DC_IPV4"].split(",") if value)
DC_PORT = int(os.environ["TDH_DC_PORT"])
API_READY = False
DC_READY = False
API_CHECKED = None
DC_CHECKED = None
API_MAX_AGE = 5
DC_MAX_AGE = 25
# Progress deadlines cover the complete check, not each socket read. A peer
# sending a byte before every urllib timeout can otherwise strand a live thread.
API_PROGRESS_TIMEOUT = 15
DC_PROGRESS_TIMEOUT = 45
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
            raw = response.read(16385)
            if len(raw) > 16384:
                return False
            payload = json.loads(raw)
        return (response.status == 200 and isinstance(payload, dict) and payload.get("ok") is True
                and isinstance(payload.get("data"), dict) and payload["data"].get("ready") is True)
    except urllib.error.HTTPError as error:
        error.close()
        return False
    except (OSError, ValueError, urllib.error.URLError):
        return False

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A local readiness endpoint must not forward its bearer token elsewhere.
        return None


class HealthGate:
    """Consecutive samples plus a down-state cooldown; stale results expire separately."""
    def __init__(self, fall, rise, cooldown):
        self.fall, self.rise, self.cooldown = fall, rise, cooldown
        self.up = False
        self.good = self.bad = 0
        self.retry_after = 0

    def sample(self, good, now):
        if good:
            self.bad = 0
            self.good += 1
            if self.good >= self.rise and now >= self.retry_after:
                self.up = True
        else:
            self.good = 0
            self.bad += 1
            if self.up and self.bad >= self.fall:
                self.up = False
                self.retry_after = now + self.cooldown
        return self.up


def dc_quorum(results):
    # One impaired DC must not evict a backend serving the other DCs.
    # This TCP reachability vote is NOT an MTProto or per-DC protocol guarantee.
    results = tuple(results)
    return bool(results) and sum(value is True for value in results) >= len(results) // 2 + 1


def listener_alive():
    try:
        with socket.create_connection((BIND, TELEMT_PORT), timeout=1):
            return True
    except OSError:
        return False


def monitor_api():
    global API_READY, API_CHECKED
    # Never inherit HTTP(S)_PROXY for the loopback API or expose its bearer token.
    http = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    gate = HealthGate(fall=3, rise=2, cooldown=10)
    while True:
        current = healthy(http, API_PORT) and listener_alive()
        with READY_LOCK:
            API_CHECKED = time.monotonic()
            API_READY = gate.sample(current, API_CHECKED)
        time.sleep(1)

def probe_dc(address):
    try:
        with socket.create_connection((address, DC_PORT), timeout=2):
            return True
    except OSError:
        return False

def monitor_dcs():
    global DC_READY, DC_CHECKED
    gate = HealthGate(fall=2, rise=3, cooldown=30)
    while True:
        if DCS:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(DCS)) as executor:
                current = dc_quorum(executor.map(probe_dc, DCS))
        else:
            current = False
        with READY_LOCK:
            DC_CHECKED = time.monotonic()
            DC_READY = gate.sample(current, DC_CHECKED)
        time.sleep(10)

def ready(now=None):
    # A stalled/crashed monitor must never leave a permanently healthy flag.
    with READY_LOCK:
        # Read the clock after locking so a monitor cannot publish a newer timestamp.
        if now is None:
            now = time.monotonic()
        return (API_READY and DC_READY and API_CHECKED is not None and DC_CHECKED is not None
                and 0 <= now - API_CHECKED <= API_MAX_AGE and 0 <= now - DC_CHECKED <= DC_MAX_AGE)


def monitors_progressing(now, started):
    with READY_LOCK:
        # As with ready(), sample after locking: a freshly published timestamp
        # must not look as though it came from the future.
        if now is None:
            now = time.monotonic()
        return all(0 <= now - (checked if checked is not None else started) <= limit
                   for checked, limit in ((API_CHECKED, API_PROGRESS_TIMEOUT),
                                          (DC_CHECKED, DC_PROGRESS_TIMEOUT)))


started = time.monotonic()
monitors = [threading.Thread(target=target, daemon=True) for target in (monitor_api, monitor_dcs)]
for monitor in monitors:
    monitor.start()

while True:
    if any(not monitor.is_alive() for monitor in monitors):
        # Let the existing systemd restart policy replace a failed monitor thread.
        raise SystemExit("health monitor stopped")
    if not monitors_progressing(None, started):
        # Staleness already closes admission. Recover the stuck worker through
        # the existing service restart policy; ordinary unhealthy votes do not
        # trigger restarts, and no check is silently declared healthy.
        raise SystemExit("health monitor progress deadline")
    for key, _ in sel.select(timeout=1):
        listener = key.fileobj
        try:
            conn, _ = listener.accept()
        except (BlockingIOError, OSError):
            continue
        with conn:
            try:
                conn.settimeout(1)
                current = ready()
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

check_tuning_support() {
  modprobe sch_fq >/dev/null 2>&1 || \
    die "当前内核缺少 fq 支持（可能为裁剪内核或缺少匹配模块）；请确认内核功能后重试。"
  modprobe tcp_bbr >/dev/null 2>&1 || \
    die "当前内核缺少 BBR 支持（可能为裁剪内核或缺少匹配模块）；请确认内核功能后重试。"
  sysctl -n net.ipv4.tcp_available_congestion_control 2>/dev/null | grep -qw bbr || \
    die "当前内核未提供 BBR；安装已停止，请先确认内核功能。"
}

apply_tuning() {
  local somax syn_backlog
  check_tuning_support
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
  # Match the 4096 listener backlog; preserve any larger operator setting.
  # Hundreds of users on a single vCPU do not justify a 65k/32k queue floor.
  if (( somax < 4096 )); then somax=4096; fi
  if (( syn_backlog < 4096 )); then syn_backlog=4096; fi
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
  ok "BBR / 默认 qdisc=fq 已写入；实际接口队列和连接拥塞算法请用 a perf 核实。"
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
  local version expected checksum owner mode links directory
  [[ -f $TDH_BIN && -x $TDH_BIN && ! -L $TDH_BIN ]] || return 1
  read -r owner mode links < <(stat -c '%u %a %h' -- "$TDH_BIN")
  [[ $owner == "$EUID" && $links == 1 && $mode =~ ^[0-7]{1,4}$ ]] || return 1
  (( (8#$mode & 0022) == 0 )) || return 1
  directory=$(dirname "$TDH_BIN")
  while [[ $directory != / ]]; do
    [[ -d $directory && ! -L $directory ]] || return 1
    read -r owner mode < <(stat -c '%u %a' -- "$directory")
    [[ ( $owner == 0 || $owner == "$EUID" ) && $mode =~ ^[0-7]{1,4}$ ]] || return 1
    (( (8#$mode & 0022) == 0 || (8#$mode & 01000) != 0 )) || return 1
    directory=$(dirname "$directory")
  done
  # Authenticate existing bytes BEFORE executing even --version as root.
  checksum=$(sha256sum "$TDH_BIN" | awk '{print $1}')
  case $(uname -m):$checksum in
    x86_64:"$TELEMT_BINARY_SHA256_AMD64"|amd64:"$TELEMT_BINARY_SHA256_AMD64"|aarch64:"$TELEMT_BINARY_SHA256_ARM64"|arm64:"$TELEMT_BINARY_SHA256_ARM64") expected="telemt ${TELEMT_VERSION}" ;;
    x86_64:"$TELEMT_PREVIOUS_BINARY_SHA256_AMD64"|amd64:"$TELEMT_PREVIOUS_BINARY_SHA256_AMD64"|aarch64:"$TELEMT_PREVIOUS_BINARY_SHA256_ARM64"|arm64:"$TELEMT_PREVIOUS_BINARY_SHA256_ARM64") expected="telemt ${TELEMT_PREVIOUS_VERSION}" ;;
    x86_64:"$TELEMT_LEGACY_BINARY_SHA256_AMD64"|amd64:"$TELEMT_LEGACY_BINARY_SHA256_AMD64"|aarch64:"$TELEMT_LEGACY_BINARY_SHA256_ARM64"|arm64:"$TELEMT_LEGACY_BINARY_SHA256_ARM64") expected="telemt ${TELEMT_LEGACY_VERSION}" ;;
    x86_64:"$TELEMT_CORE_BINARY_SHA256_AMD64"|amd64:"$TELEMT_CORE_BINARY_SHA256_AMD64") expected="telemt ${TELEMT_CORE_VERSION_AMD64}" ;;
    x86_64:"$TELEMT_AUDIT2_BINARY_SHA256"|amd64:"$TELEMT_AUDIT2_BINARY_SHA256") expected="telemt ${TELEMT_AUDIT2_VERSION}" ;;
    *) return 1 ;;
  esac
  version=$(timeout 5 "$TDH_BIN" --version 2>/dev/null) || return 1
  [[ $version == "$expected" ]]
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
  local api_port=$1 username=$2 domain=$3 domain_hex escaped_token
  validate_port "$api_port" || return 1
  [[ $API_TOKEN != *[$'\r\n']* ]] || return 1
  escaped_token=${API_TOKEN//\\/\\\\}
  escaped_token=${escaped_token//\"/\\\"}
  domain_hex=$(printf '%s' "$domain" | od -An -tx1 | tr -d ' \n')
  # Loopback credentials must never inherit proxies, .curlrc or appear in argv.
  {
    if [[ -n $API_TOKEN ]]; then printf 'header = "Authorization: Bearer %s"\n' "$escaped_token"; fi
  } | curl -q --noproxy '*' --config - -fsS --connect-timeout 2 --max-time 5 \
      "http://127.0.0.1:${api_port}/v1/users" | \
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
  warn "Telemt 在等待期内未生成可用链接；请检查服务状态和本机诊断。"
  return 1
}

join_core_version_is_compatible() {
  [[ $1 == "$TELEMT_VERSION" || $1 == "$TELEMT_PREVIOUS_VERSION" ]]
}

install_backend() {
  local requested_role=$1 code decoded backend_priv backend_pub
  local -a fields
  [[ ! -f $TDH_STATE ]] || die "本机已经初始化；请运行管理菜单。"
  assert_fresh_install_paths
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
  join_core_version_is_compatible "${fields[14]}" || die "Join Code 要求的 Telemt 版本与脚本不兼容。"
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
  if command -v modprobe >/dev/null 2>&1; then check_tuning_support; fi
  install_packages backend
  check_tuning_support
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
  TX_ACTIVE=1 TX_KIND=$requested_role TX_OWNER_PID=$BASHPID
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
  assert_fresh_install_paths
  check_os
  if systemctl is-active --quiet haproxy.service 2>/dev/null; then
    die "检测到已有 haproxy.service 正在运行。为避免破坏现有业务，请先确认并停止它。"
  fi
  if command -v modprobe >/dev/null 2>&1; then check_tuning_support; fi
  install_packages entry
  systemctl disable --now haproxy.service >/dev/null 2>&1 || true
  check_tuning_support
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

  TX_ACTIVE=1 TX_KIND=entry TX_OWNER_PID=$BASHPID
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
  printf 'show stat\n' | timeout 3 socat - UNIX-CONNECT:/run/telemt-dual-hop/haproxy.sock 2>/dev/null | \
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
  check_tuning_support
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
    printf 'show stat\n' | timeout 3 socat - UNIX-CONNECT:/run/telemt-dual-hop/haproxy.sock 2>/dev/null | \
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
    if tuning_is_active; then ok "BBR / 默认队列 fq 已设置；a perf 可查看实际连接和接口"; else warn "BBR/fq 默认值异常"; ((failures+=1)); fi
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
    if tuning_is_active; then ok "BBR / 默认队列 fq 已设置；a perf 可查看实际连接和接口"; else warn "BBR/fq 默认值异常"; ((failures+=1)); fi
    if telemt_binary_is_expected; then ok "Telemt 二进制身份通过（a perf 可查看构建标识）"; else warn "Telemt 二进制身份异常"; ((failures+=1)); fi
    if health_agent_is_up "$LOCAL_WG_IP" "$TDH_AGENT_PORT"; then ok "Telemt API 就绪且 DC 多数可达（非协议级保证）"; else warn "Telemt API 或 DC 多数连通性异常"; ((failures+=1)); fi
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

# This path deliberately bypasses bootstrap installation, state sourcing and the
# manager lock. Only fixed read commands and allow-listed numeric fields escape.
performance_report() {
  local seconds=${1:-5}
  [[ $seconds =~ ^([2-9]|[12][0-9]|30)$ ]] || die "采样时长必须为 2-30 秒。"
  command -v python3 >/dev/null || die "缺少 python3；只读诊断不会自动安装软件。"
  command -v timeout >/dev/null || die "缺少 timeout。"
  timeout 55 python3 - "$seconds" "$TDH_STATE" <<'PY'
import csv
import datetime
import http.client
import io
import json
import os
import pathlib
import re
import signal
import socket
import statistics
import subprocess
import sys
import time

DEADLINE = time.monotonic() + 50

def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(1048576)
    except OSError:
        return ""

def command(*args):
    # Bound time AND output, and never emit command errors or raw output.
    try:
        remaining = DEADLINE - time.monotonic()
        if remaining < .05:
            return ""
        with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True) as p:
            import threading
            def stop():
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            timer = threading.Timer(min(2, remaining), stop)
            timer.start()
            try:
                data = p.stdout.read(1048576)
                if len(data) == 1048576:
                    stop()
                p.wait(timeout=1)
                return data.decode("utf-8", "replace") if p.returncode == 0 else ""
            finally:
                timer.cancel()
                if p.poll() is None:
                    stop()
                    p.wait()
    except (OSError, subprocess.SubprocessError):
        return ""

def integer(value):
    return int(value) if re.fullmatch(r"\d+", str(value)) else None

def telemt_version():
    value = command("/usr/local/lib/telemt-dual-hop/telemt", "--version").strip()
    match = re.fullmatch(r"telemt ([0-9]+\.[0-9]+\.[0-9]+(?:[-+][a-zA-Z0-9.+-]{1,48})?)", value)
    return match[1] if match else None

def numeric_fields(text, names):
    return {k: int(v) for k, v in re.findall(r"^([A-Za-z_]+):?\s+(\d+)", text, re.M) if k in names}

def counters():
    result = {}
    allowed = {"RetransSegs", "OutSegs", "InSegs", "ListenOverflows", "ListenDrops",
               "TCPTimeouts", "TCPLostRetransmit", "TCPMemoryPressures", "TCPBacklogDrop",
               "TCPRcvQDrop", "IpFragFails", "FragFails", "FragCreates", "ReasmFails",
               "InErrors", "RcvbufErrors", "SndbufErrors"}
    for path in ("/proc/net/snmp", "/proc/net/netstat"):
        lines = read(path).splitlines()
        for header, values in zip(lines[::2], lines[1::2]):
            prefix = header.split()[0].rstrip(":")
            for name, value in zip(header.split()[1:], values.split()[1:]):
                if name in allowed and integer(value) is not None:
                    result[prefix + "." + name] = int(value)
    result.update({"Vm." + k: v for k, v in numeric_fields(read("/proc/vmstat"),
                   {"oom_kill", "pgmajfault", "pswpin", "pswpout"}).items()})
    return result

def cpu():
    return {row[0]: list(map(int, row[1:9])) for line in read("/proc/stat").splitlines()
            if (row := line.split()) and re.fullmatch(r"cpu\d*", row[0])}

def cpu_usage(before, after):
    delta = [b - a for a, b in zip(before, after)]
    total = sum(delta)
    if len(delta) < 8 or total <= 0 or any(v < 0 for v in delta):
        return {"busy_percent": None, "steal_percent": None}
    return {"busy_percent": round(100 * (total - delta[3] - delta[4] - delta[7]) / total, 2),
            "steal_percent": round(100 * delta[7] / total, 2)}

def processes():
    result = {}
    for name in ("haproxy", "telemt", "health-agent"):
        unit = "telemt-dual-hop-" + ("health-agent" if name == "health-agent" else name) + ".service"
        props = dict(line.split("=", 1) for line in command("systemctl", "show", unit,
                     "--property=MainPID,ControlGroup,LimitNOFILE,MemoryCurrent,TasksCurrent").splitlines() if "=" in line)
        pid = integer(props.get("MainPID", ""))
        pids = {pid} if pid else set()
        group = props.get("ControlGroup", "")
        if group.startswith("/") and ".." not in group:
            pids.update(int(x) for x in read("/sys/fs/cgroup" + group + "/cgroup.procs").split() if x.isdigit())
        # Include HAProxy workers on cgroup v1 as well as v2.
        for parent in list(pids):
            pids.update(int(x) for x in read(f"/proc/{parent}/task/{parent}/children").split() if x.isdigit())
        ticks = rss = fds = 0
        for member in pids:
            fields = read(f"/proc/{member}/stat").rsplit(")", 1)[-1].split()
            if len(fields) >= 22:
                ticks += int(fields[11]) + int(fields[12])
                rss += int(fields[21]) * os.sysconf("SC_PAGE_SIZE")
            try:
                fds += len(os.listdir(f"/proc/{member}/fd"))
            except OSError:
                pass
        result[name] = {"running": bool(pid), "ticks": ticks, "rss_bytes": rss, "fds": fds,
                        "fd_limit": integer(props.get("LimitNOFILE", "")),
                        "tasks": integer(props.get("TasksCurrent", "")), "identity": sorted(pids), "at": time.monotonic()}
    return result

def interfaces(details=False):
    result = {}
    for path in sorted(pathlib.Path("/sys/class/net").glob("*")):
        if path.name == "lo":
            continue
        name = path.name
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", name):
            continue
        alias = name if name in ("tdh1", "tdh2", "tdh-b1", "tdh-b2") else f"nic{len(result)}"
        row = {"mtu": integer(read(path / "mtu").strip())}
        for field in ("rx_bytes", "tx_bytes", "rx_dropped", "tx_dropped", "rx_errors", "tx_errors"):
            row[field] = integer(read(path / "statistics" / field).strip())
        if details:
            try:
                queues = json.loads(command("tc", "-j", "-s", "qdisc", "show", "dev", name))
                row["qdisc"] = [{key: q[key] for key in ("kind", "drops", "overlimits", "backlog", "qlen")
                                 if key in q and (key == "kind" or isinstance(q[key], (int, float)))} for q in queues
                                if re.fullmatch(r"[a-z0-9_]+", str(q.get("kind", "")))]
            except (ValueError, TypeError):
                row["qdisc"] = None
            features = command("ethtool", "-k", name)
            row["offload"] = {k: v == "on" for k, v in re.findall(
                r"^(generic-receive-offload|generic-segmentation-offload|tcp-segmentation-offload|tx-udp-segmentation): (on|off)", features, re.M)} or None
        result[alias] = row
    return result

def sockets():
    groups = {}
    numeric = {
        "rtt_ms": r"\brtt:([\d.]+)", "cwnd_segments": r"\bcwnd:(\d+)",
        "recv_buffer_limit_bytes": r"\brb(\d+)", "send_buffer_limit_bytes": r"\btb(\d+)",
        "snd_wnd_bytes": r"\bsnd_wnd:(\d+)", "rcv_space_bytes": r"\brcv_space:(\d+)",
        "rwnd_limited_ms": r"\brwnd_limited:(\d+)ms", "sndbuf_limited_ms": r"\bsndbuf_limited:(\d+)ms",
    }
    # Address and process information stay in memory; only aggregates leave.
    text = command("ss", "-Htinmp", "state", "established")
    current = None
    for line in text.splitlines():
        if line and not line[0].isspace():
            fields = line.split()
            current = None
            if len(fields) < 4:
                continue
            local, remote = fields[2:4]
            if remote.startswith(("10.77.1.2:24431", "10.77.2.2:24431")):
                current = "entry-backend"
            elif local.startswith(("10.77.1.2:24431", "10.77.2.2:24431")):
                current = "backend-entry"
            elif local.endswith(":443") and '"haproxy"' in line:
                current = "user-entry"
            elif '"telemt"' in line and remote.endswith(":443"):
                current = "backend-upstream"
            if current:
                row = groups.setdefault(current, {"count": 0, "recv_q_bytes": 0, "send_q_bytes": 0,
                                                   "bbr": 0, **{field: [] for field in numeric}})
                row["count"] += 1
                row["recv_q_bytes"] += integer(fields[0]) or 0
                row["send_q_bytes"] += integer(fields[1]) or 0
        elif current:
            row = groups[current]
            row["bbr"] += int(bool(re.search(r"\bbbr\b", line)))
            for field, pattern in numeric.items():
                match = re.search(pattern, line)
                if match:
                    try:
                        value = float(match[1])
                    except ValueError:
                        continue
                    if 0 <= value < float("inf"):
                        row[field].append(value)
    for row in groups.values():
        for field in numeric:
            values = sorted(row.pop(field))
            row[field + "_median"] = statistics.median(values) if values else None
            row[field + "_p95"] = values[max(0, (95 * len(values) + 99) // 100 - 1)] if values else None
            row[field + "_min"] = values[0] if values else None
            row[field + "_max"] = values[-1] if values else None
    return groups

def haproxy():
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(2)
            s.connect("/run/telemt-dual-hop/haproxy.sock")
            s.sendall(b"show stat\n")
            data = bytearray()
            while len(data) < 262144:
                block = s.recv(8192)
                if not block:
                    break
                data.extend(block)
        rows = csv.DictReader(io.StringIO(data.decode().lstrip("# ")))
        return [{k: r[k] for k in ("pxname", "svname", "status") if k in r} |
                {k: integer(r.get(k, "")) for k in ("qcur", "qmax", "scur", "smax", "slim", "bin", "bout",
                 "econ", "eresp", "wretr", "wredis", "chkfail", "chkdown", "downtime", "qtime", "ctime", "rtime", "ttime")}
                for r in rows if r.get("pxname") in ("mtp_tls", "telemt_a", "telemt_b")
                and r.get("svname") in ("FRONTEND", "BACKEND", "vps1", "vps2")
                and re.fullmatch(r"[A-Z0-9 /()+_-]{1,40}|DOWN \(agent\)", r.get("status", ""))]
    except (OSError, ValueError, UnicodeError):
        return None

def telemt():
    # Use the protected token file, never argv/environment or user/config APIs.
    token = read(pathlib.Path(sys.argv[2]).parent / "telemt/api.token").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", token) or DEADLINE - time.monotonic() < 3:
        return None
    connection = http.client.HTTPConnection("127.0.0.1", 19091, timeout=2)
    try:
        connection.request("GET", "/v1/stats/zero/all", headers={"Authorization": "Bearer " + token})
        response = connection.getresponse()
        if response.status != 200:
            return None
        data = json.loads(response.read(262144)).get("data", {})
        allowed = {"core": ("connections_total", "connections_bad_total", "handshake_timeouts_total",
                            "accept_permit_timeout_total", "conntrack_pressure_active"),
                   "upstream": ("connect_attempt_total", "connect_success_total", "connect_fail_total",
                                "connect_failfast_hard_error_total", "connect_duration_success_bucket_le_100ms",
                                "connect_duration_success_bucket_101_500ms", "connect_duration_success_bucket_501_1000ms",
                                "connect_duration_success_bucket_gt_1000ms")}
        return {group: {key: data[group][key] for key in keys
                        if key in data.get(group, {}) and isinstance(data[group][key], (int, bool))}
                for group, keys in allowed.items()}
    except (OSError, ValueError, TypeError, AttributeError, http.client.HTTPException):
        return None
    finally:
        connection.close()

seconds = int(sys.argv[1])
role_match = re.search(r"^TDH_ROLE=(entry|backend1|backend2)$", read(sys.argv[2]), re.M)
role = role_match[1] if role_match else "unknown"
start = time.monotonic()
c0, p0, i0, n0 = cpu(), processes(), interfaces(), counters()
time.sleep(seconds)
c1, p1, i1, n1 = cpu(), processes(), interfaces(True), counters()
elapsed = time.monotonic() - start
for name, row in p1.items():
    previous = p0.get(name, {})
    ticks = row.pop("ticks")
    same_process = row.pop("identity") == previous.get("identity")
    duration = row.pop("at") - previous.get("at", start)
    row["sample_seconds"] = round(duration, 3)
    row["cpu_percent_one_core"] = round(100 * (ticks - previous["ticks"]) / os.sysconf("SC_CLK_TCK") / duration, 2) if same_process else None
for name, row in i1.items():
    for field in ("rx_bytes", "tx_bytes", "rx_dropped", "tx_dropped", "rx_errors", "tx_errors"):
        old, new = i0.get(name, {}).get(field), row[field]
        row[field + "_delta"] = new - old if old is not None and new is not None and new >= old else None
cpu_load = {name: cpu_usage(c0.get(name, values), values) for name, values in c1.items()}
sysctls = {}
for key in ("net.core.default_qdisc", "net.ipv4.tcp_congestion_control", "net.ipv4.tcp_available_congestion_control",
            "net.ipv4.tcp_rmem", "net.ipv4.tcp_wmem", "net.core.rmem_max", "net.core.wmem_max",
            "net.ipv4.tcp_mtu_probing", "net.core.somaxconn", "net.ipv4.tcp_max_syn_backlog"):
    value = read("/proc/sys/" + key.replace(".", "/")).strip()
    sysctls[key] = value if re.fullmatch(r"[a-z0-9_ \t]+", value) else None
wg = {}
for name in ("tdh1", "tdh2", "tdh-b1", "tdh-b2"):
    if pathlib.Path("/sys/class/net", name).exists():
        timestamps = [int(v) for v in re.findall(r"\t(\d+)\n", command("wg", "show", name, "latest-handshakes"))]
        wg[name] = {"handshake_age_seconds": int(time.time()) - max(timestamps) if timestamps and max(timestamps) else None}
versions = {"telemt": telemt_version()}
haproxy_version = re.search(r"HAProxy version ([0-9][a-zA-Z0-9.+_-]*)", command("haproxy", "-v"))
versions["haproxy"] = haproxy_version[1] if haproxy_version else None
report = {"说明": "只读本机采样；无主动测速、无上传；null 表示不可用。TCP RTT 为当前连接分布，不是时间序列 P95。",
          "schema": 1, "role": role, "kernel": os.uname().release, "sample_seconds": round(elapsed, 3),
          "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "versions": versions,
          "memory_kib": numeric_fields(read("/proc/meminfo"), {"MemTotal", "MemAvailable", "SwapFree", "SwapTotal"}),
          "cpu": cpu_load, "services": p1, "interfaces": i1, "tcp": sockets(), "wireguard": wg,
          "system_counters_delta": {k: v - n0[k] if k in n0 and v >= n0[k] else None for k, v in n1.items()},
          "vm_counters_total": {k: v for k, v in n1.items() if k.startswith("Vm.")},
          "sysctl": sysctls, "haproxy": haproxy(), "telemt": telemt(), "direct_buffer_pressure": None}
print(json.dumps(report, ensure_ascii=False, indent=2))
PY
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
  if queue_is_configured; then manage_queue disable --maintenance; fi
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
  PANEL_ACTION_PID=""
  (
    set -Eeuo pipefail
    trap 'on_error $? $LINENO' ERR
    trap 'on_exit $?' EXIT
    trap 'on_signal 130' INT
    trap 'on_signal 143' TERM
    trap 'on_signal 129' HUP
    "$@"
  ) <&0 &
  PANEL_ACTION_PID=$!
  wait "$PANEL_ACTION_PID"
  rc=$?
  PANEL_ACTION_PID=""
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
      printf '  8) 只读脱敏性能采样（5 秒）\n'
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
        8) run_panel_action performance_report ;;
        9) run_panel_action uninstall_all ;;
        0|q|Q) PANEL_EXIT_REQUESTED=1; return 0 ;;
        *) warn "无效选择：${PANEL_CHOICE}"; pause_panel ;;
      esac
    else
      printf '\n  1) 查看运行状态（默认）\n'
      printf '  2) 重新显示给入口使用的回执码\n'
      printf '  3) 一键诊断\n'
      printf '  4) 安全更新管理脚本\n'
      printf '  5) 只读脱敏性能采样（5 秒）\n'
      printf '  6) 后端实验队列（默认关闭，可撤销）\n'
      printf '  9) 卸载\n'
      printf '  0) 退出\n\n'
      read_panel_choice '请选择' 1 || { PANEL_EXIT_REQUESTED=1; return 0; }
      case $PANEL_CHOICE in
        1) run_panel_action show_status ;;
        2) run_panel_action backend_response_again ;;
        3) run_panel_action diagnose ;;
        4) run_panel_action update_manager ;;
        5) run_panel_action performance_report ;;
        6) run_panel_action queue_panel ;;
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
  if [[ ${1:-} == perf ]]; then
    (( $# <= 2 )) || die "用法：a perf [2-30]"
    performance_report "${2:-5}"
    return
  fi
  if [[ ${1:-} == --queue-service ]]; then
    if (( $# != 2 )) || [[ $2 != apply && $2 != remove ]]; then
      die "内部队列钩子参数无效。"
    fi
    # This is called by wg-quick while an operator may hold the manager lock.
    # The Python controller has its own flock. A hook failure is ignored by
    # systemd's '-' prefix so optional tuning cannot fail the tunnel itself.
    manage_queue "$2"
    return
  fi
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
    update-rollback) rollback_manager ;;
    core-upgrade|core-rollback)
      (( $# == 2 )) || die "用法：a $1 --maintenance"
      manage_core "${1#core-}" "$2" ;;
    backend-drain|backend-resume)
      (( $# == 2 )) || die "用法：a $1 1|2"
      backend_maintenance "${1#backend-}" "$2" ;;
    health-refresh|health-rollback)
      (( $# == 2 )) || die "用法：a $1 --maintenance"
      if [[ $1 == health-refresh ]]; then manage_core refresh-health "$2"; else manage_core rollback-health "$2"; fi ;;
    connect-refresh|connect-rollback)
      (( $# == 2 )) || die "用法：a $1 --maintenance"
      if [[ $1 == connect-refresh ]]; then manage_core refresh-connect "$2"; else manage_core rollback-connect "$2"; fi ;;
    recover) recover_backend ;;
    queue) shift; manage_queue "$@" ;;
    uninstall) uninstall_all ;;
    menu|panel) control_panel ;;
    "") control_panel ;;
    *) usage; die "未知命令：$1" ;;
  esac
}

if [[ ${TDH_SOURCE_ONLY:-0} != 1 ]]; then
  main "$@"
fi
