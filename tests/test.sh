#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SCRIPT="${ROOT}/telemt-dual-hop.sh"
TMP_ROOT=$(mktemp -d)
STUB_PID=""
AGENT_PID=""
TELEMT_PID=""
cleanup() {
  if [[ -n $TELEMT_PID ]]; then kill "$TELEMT_PID" 2>/dev/null || true; fi
  if [[ -n $AGENT_PID ]]; then kill "$AGENT_PID" 2>/dev/null || true; fi
  if [[ -n $STUB_PID ]]; then kill "$STUB_PID" 2>/dev/null || true; fi
  rm -rf "$TMP_ROOT"
}
trap cleanup EXIT

bash -n "$SCRIPT"
bash "$SCRIPT" --self-test

TDH_SOURCE_ONLY=1 \
TDH_BASE="${TMP_ROOT}/etc" \
TDH_STATE="${TMP_ROOT}/etc/state.env" \
TDH_KEY_DIR="${TMP_ROOT}/etc/keys" \
TDH_CONFIG_DIR="${TMP_ROOT}/etc/telemt" \
TDH_WORK_ROOT="${TMP_ROOT}/work" \
TDH_BIN="${TMP_ROOT}/bin/telemt" \
TDH_MANAGER="${TMP_ROOT}/bin/manager" \
TDH_HAPROXY_CFG="${TMP_ROOT}/etc/haproxy.cfg" \
bash -Eeuo pipefail -s "$SCRIPT" <<'BASH'
source "$1"

mkdir -p "$TDH_KEY_DIR"
VALID_KEY='AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='
printf '%s\n' "$VALID_KEY" >"${TDH_KEY_DIR}/entry-b1.pub"
printf '%s\n' "$VALID_KEY" >"${TDH_KEY_DIR}/entry-b1.psk"
printf '%s\n' "$VALID_KEY" >"${TDH_KEY_DIR}/entry-b2.pub"
printf '%s\n' "$VALID_KEY" >"${TDH_KEY_DIR}/entry-b2.psk"

TDH_ROLE=entry
CONFIG_SCHEMA=$TDH_CONFIG_SCHEMA
INSTALLED_VERSION=$TDH_VERSION
CLUSTER_ID=00112233445566778899aabb
ENTRY_PUBLIC_IP=203.0.113.10
PORT_A=443 PORT_B=443
SECRET_A=00112233445566778899aabbccddeeff
SECRET_B=ffeeddccbbaa99887766554433221100
JOIN_CREATED=1700000000 JOIN_EXPIRES=4102444800
ENROLL_TOKEN_B1=$(printf 'ab%.0s' {1..32})
ENROLL_TOKEN_B2=$(printf 'cd%.0s' {1..32})
code=$(generate_join_code backend1)
[[ $code == TDH4.* ]]
decoded=$(decode_join_code "$code")
grep -qx backend1 <<<"$decoded"
grep -qx 203.0.113.10 <<<"$decoded"
grep -qx apple.com <<<"$decoded"
grep -qx gs.apple.com <<<"$decoded"
last_char=${code: -1}
if [[ $last_char == A ]]; then replacement=B; else replacement=A; fi
tampered="${code::-1}${replacement}"
if decode_join_code "$tampered" >/dev/null 2>&1; then
  echo "篡改后的 Join Code 被错误接受" >&2
  exit 1
fi
if decode_join_code "TDH3.${code#TDH4.}" >/dev/null 2>&1; then
  echo "旧版 Join Code 被错误接受" >&2
  exit 1
fi
if decode_join_code "TDH4.$(printf 'A%.0s' {1..17000})" >/dev/null 2>&1; then
  echo "超长 Join Code 被错误接受" >&2
  exit 1
fi
valid_expiry=$JOIN_EXPIRES
JOIN_EXPIRES=1600000000
expired=$(generate_join_code backend1)
JOIN_EXPIRES=$valid_expiry
if decode_join_code "$expired" >/dev/null 2>&1; then
  echo "过期 Join Code 被错误接受" >&2
  exit 1
fi

domain_a_hex=$(printf '%s' apple.com | od -An -tx1 | tr -d ' \n')
domain_b_hex=$(printf '%s' gs.apple.com | od -An -tx1 | tr -d ' \n')
RESPONSE_LINK_A="tg://proxy?server=${ENTRY_PUBLIC_IP}&port=443&secret=ee${SECRET_A}${domain_a_hex}"
RESPONSE_LINK_B="tg://proxy?server=${ENTRY_PUBLIC_IP}&port=443&secret=ee${SECRET_B}${domain_b_hex}"
API_TOKEN=$(printf 'ef%.0s' {1..32})
TDH_ROLE=backend1 BACKEND_PUBLIC_IP=198.51.100.20 BACKEND_WG_PORT=51821 ENROLL_TOKEN=$ENROLL_TOKEN_B1
BACKEND_MAX_CONNECTIONS=4000
response=$(generate_response_code "$VALID_KEY")
[[ $response == TDHR4.* ]]
decoded_response=$(decode_response_code "$response" "$ENROLL_TOKEN_B1" "$ENROLL_TOKEN_B2")
grep -qx backend1 <<<"$decoded_response"
grep -qx 198.51.100.20 <<<"$decoded_response"
grep -qx 4000 <<<"$decoded_response"
grep -qx "$TDH_VERSION" <<<"$decoded_response"
grep -qx "$TDH_CONFIG_SCHEMA" <<<"$decoded_response"
last_char=${response: -1}
if [[ $last_char == A ]]; then replacement=B; else replacement=A; fi
if decode_response_code "${response::-1}${replacement}" "$ENROLL_TOKEN_B1" "$ENROLL_TOKEN_B2" >/dev/null 2>&1; then
  echo "篡改后的回执码被错误接受" >&2
  exit 1
fi
if decode_response_code "TDHR3.${response#TDHR4.}" "$ENROLL_TOKEN_B1" "$ENROLL_TOKEN_B2" >/dev/null 2>&1; then
  echo "旧版回执码被错误接受" >&2
  exit 1
fi
if decode_response_code "TDHR4.$(printf 'A%.0s' {1..17000})" "$ENROLL_TOKEN_B1" "$ENROLL_TOKEN_B2" >/dev/null 2>&1; then
  echo "超长回执码被错误接受" >&2
  exit 1
fi
if decode_response_code "$response" "$(printf '00%.0s' {1..32})" "$ENROLL_TOKEN_B2" >/dev/null 2>&1; then
  echo "错误登记令牌被回执验证接受" >&2
  exit 1
fi
TDH_RESPONSE="$response" python3 - <<'PY'
import base64, json, os
data = os.environ["TDH_RESPONSE"].split(".", 1)[1]
outer = json.loads(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)))
if "enrollment_token" in outer["body"]:
    raise SystemExit("回执正文泄露登记令牌")
PY

normalized=$(normalize_and_validate_link "$RESPONSE_LINK_A" 443 "$SECRET_A" apple.com)
[[ $normalized == "https://t.me/proxy?server=203.0.113.10&port=443&secret=ee${SECRET_A}${domain_a_hex}" ]]
normalize_and_validate_link "$RESPONSE_LINK_B" 443 "$SECRET_B" gs.apple.com >/dev/null
if normalize_and_validate_link "$RESPONSE_LINK_A" 8443 "$SECRET_A" apple.com >/dev/null 2>&1; then
  echo "错误端口链接被错误接受" >&2
  exit 1
fi
if normalize_and_validate_link "$RESPONSE_LINK_B" 443 "$SECRET_B" apple.com >/dev/null 2>&1; then
  echo "错误 SNI 链接被错误接受" >&2
  exit 1
fi
if normalize_and_validate_link "${RESPONSE_LINK_A}&unexpected=1" 443 "$SECRET_A" apple.com >/dev/null 2>&1; then
  echo "含未知参数的链接被错误接受" >&2
  exit 1
fi
if normalize_and_validate_link "${RESPONSE_LINK_A}#unexpected" 443 "$SECRET_A" apple.com >/dev/null 2>&1; then
  echo "含 fragment 的链接被错误接受" >&2
  exit 1
fi
if normalize_and_validate_link "${RESPONSE_LINK_A/tg:\/\/proxy/tg:\/\/proxy\/unexpected}" 443 "$SECRET_A" apple.com >/dev/null 2>&1; then
  echo "含错误路径的 tg 链接被错误接受" >&2
  exit 1
fi

[[ $(calculate_connection_limit 768 1) == 1536 ]]
[[ $(calculate_connection_limit 1024 1) == 2048 ]]
[[ $(calculate_connection_limit 4096 1) == 4000 ]]
[[ $(calculate_connection_limit 4096 4) == 8192 ]]
[[ $(calculate_connection_limit 65536 64) == 30000 ]]
[[ $(calculate_direct_buffer_budget 768) == 201326592 ]]
[[ $(calculate_direct_buffer_budget 1024) == 268435456 ]]
[[ $(calculate_direct_buffer_budget 2048) == 536870912 ]]
[[ $(calculate_direct_buffer_budget 65536) == 1073741824 ]]
validate_direct_buffer_budget 268435456
ENTRY_MAX_CONNECTIONS=2000 B1_MAX_CONNECTIONS=4000 B2_MAX_CONNECTIONS=2000
[[ $(configured_cluster_connection_limit) == 2000 ]]
if calculate_connection_limit invalid 1 >/dev/null 2>&1 || \
   calculate_connection_limit 1024 invalid >/dev/null 2>&1 || \
   calculate_connection_limit 512 1 >/dev/null 2>&1; then
  echo "无效或过低的 CPU/RAM 容量被错误接受" >&2
  exit 1
fi

validate_public_ipv4 8.8.8.8
if validate_public_ipv4 10.0.0.1 || validate_public_ipv4 203.0.113.10; then
  echo "非公网 IPv4 被错误接受" >&2
  exit 1
fi
all_apple_owned_ipv4 17.0.0.1 17.255.255.254
if all_apple_owned_ipv4 17.0.0.1 101.72.204.55; then
  echo "中国 CDN 地址被错误识别为 Apple 自有 17/8" >&2
  exit 1
fi
TDH_TEST_ROUTE_JSON='[]' TDH_TEST_ADDRESS_JSON='[]' \
  tunnel_network_is_available 198.18.0.0/30 tdh-test
TDH_TEST_ROUTE_JSON='[{"dst":"198.18.0.0/30","dev":"tdh-test"}]' TDH_TEST_ADDRESS_JSON='[]' \
  tunnel_network_is_available 198.18.0.0/30 tdh-test
if TDH_TEST_ROUTE_JSON='[{"dst":"198.18.0.0/24","dev":"eth0"}]' TDH_TEST_ADDRESS_JSON='[]' \
  tunnel_network_is_available 198.18.0.0/30 tdh-test 2>/dev/null; then
  echo "重叠路由被错误接受" >&2
  exit 1
fi
if TDH_TEST_ROUTE_JSON='[]' \
  TDH_TEST_ADDRESS_JSON='[{"ifname":"eth0","addr_info":[{"family":"inet","local":"198.18.0.2","prefixlen":24}]}]' \
  tunnel_network_is_available 198.18.0.0/30 tdh-test 2>/dev/null; then
  echo "重叠接口地址被错误接受" >&2
  exit 1
fi

TDH_ROLE=entry
save_state
[[ $(stat -c '%a' "$TDH_STATE") == 600 ]]
load_state
[[ $CLUSTER_ID == 00112233445566778899aabb ]]
[[ $API_TOKEN == $(printf 'ef%.0s' {1..32}) ]]
state_is_current
BASH

if grep -Eq 'releases/latest|haproxy:latest' "$SCRIPT"; then
  echo "发现浮动 latest 下载" >&2
  exit 1
fi

grep -q 'proxy_protocol = true' "$SCRIPT"
grep -q 'proxy_protocol_trusted_cidrs' "$SCRIPT"
grep -q 'readonly TDH_VERSION="1.2.1"' "$SCRIPT"
grep -q 'readonly TDH_APPLE_INC_ROOT_SHA256="b0b1730ecbc7ff4505142c49f1295e6eda6bcaed7e2c68c5be91b5a11001f024"' "$SCRIPT"
grep -q 'AppleIncRootCertificate.cer' "$SCRIPT"
grep -q 'verify_apple_private_root_tls' "$SCRIPT"
grep -q -- '-CAfile "$pem"' "$SCRIPT"
grep -q 'readonly TDH_CONFIG_SCHEMA="5"' "$SCRIPT"
grep -q 'use_middle_proxy = false' "$SCRIPT"
grep -q 'direct_relay_buffer_budget_max_bytes = ${BACKEND_BUFFER_BUDGET_BYTES}' "$SCRIPT"
grep -q 'tls_domain = "${TDH_DOMAIN_A}"' "$SCRIPT"
grep -q 'tls_domains = \["${TDH_DOMAIN_B}"\]' "$SCRIPT"
grep -q 'mask_port = 443' "$SCRIPT"
grep -q 'mask_dynamic = true' "$SCRIPT"
grep -q 'unknown_sni_action = "mask"' "$SCRIPT"
grep -q '^line_a = "${SECRET_A}"$' "$SCRIPT"
grep -q '^line_b = "${SECRET_B}"$' "$SCRIPT"
grep -q '^type = "direct"$' "$SCRIPT"
# shellcheck disable=SC2016
grep -q 'auth_header = "Bearer ${API_TOKEN}"' "$SCRIPT"
grep -q 'read_only = true' "$SCRIPT"
grep -q 'config_strict = true' "$SCRIPT"
grep -q 'fast_mode = true' "$SCRIPT"
grep -q 'beobachten = false' "$SCRIPT"
grep -q 'inline_conntrack_control = false' "$SCRIPT"
grep -q '/v1/health/ready' "$SCRIPT"
grep -q '149.154.175.50,149.154.167.51,149.154.175.100,149.154.167.91,149.154.171.5' "$SCRIPT"
grep -q 'all(executor.map(probe_dc, DCS))' "$SCRIPT"
grep -q 'telemt-dual-hop-telemt.service' "$SCRIPT"
grep -q 'StartLimitIntervalSec=0' "$SCRIPT"
grep -q 'Restart=always' "$SCRIPT"
grep -q '^sch_fq$' "$SCRIPT"
grep -q '^tcp_bbr$' "$SCRIPT"
grep -q 'tuning_is_active' "$SCRIPT"
grep -q 'wireguard_handshake_is_fresh' "$SCRIPT"
grep -q 'send-proxy-v2' "$SCRIPT"
grep -q 'tunnel_network_is_available 10.77.1.0/30 tdh1' "$SCRIPT"
grep -q 'tunnel_network_is_available 10.77.2.0/30 tdh2' "$SCRIPT"
grep -q 'Wants=network-online.target wg-quick@tdh1.service wg-quick@tdh2.service' "$SCRIPT"
if grep -q 'Requires=wg-quick@tdh1.service wg-quick@tdh2.service' "$SCRIPT"; then
  echo "HAProxy 仍被两个隧道硬绑定" >&2
  exit 1
fi
# The unprivileged service must be able to traverse both parent directories.
# shellcheck disable=SC2016
grep -q 'chown root:telemt "$TDH_BASE" "$TDH_CONFIG_DIR" "$TDH_WORK_ROOT"' "$SCRIPT"
# shellcheck disable=SC2016
grep -q 'chmod 0710 "$TDH_BASE"' "$SCRIPT"
# shellcheck disable=SC2016
grep -q 'chown root:telemt "$tmp"' "$SCRIPT"
# shellcheck disable=SC2016
grep -q 'write_private_file "${TDH_CONFIG_DIR}/api.token" 0640' "$SCRIPT"
# A rejected generated config must never overwrite the last valid HAProxy config.
# shellcheck disable=SC2016
grep -q 'tmp=$(mktemp "${TDH_BASE}/haproxy.cfg.XXXXXX")' "$SCRIPT"
# shellcheck disable=SC2016
grep -q 'haproxy -c -f "$tmp"' "$SCRIPT"
# shellcheck disable=SC2016
grep -q 'server vps1 10.77.1.2:${TDH_TELEMT_PORT} check port ${TDH_AGENT_PORT}.*backup' "$SCRIPT"
# shellcheck disable=SC2016
grep -q 'server vps2 10.77.2.2:${TDH_TELEMT_PORT} check port ${TDH_AGENT_PORT}.*backup' "$SCRIPT"
if grep -q 'server vps1 10.77.1.1:' "$SCRIPT"; then
  echo "HAProxy 错把入口隧道地址当作 VPS1 后端" >&2
  exit 1
fi

mockbin="${TMP_ROOT}/mockbin"
mkdir -p "$mockbin"
cat >"${mockbin}/chown" <<'SH'
#!/usr/bin/env sh
exit 0
SH
cat >"${mockbin}/haproxy" <<'SH'
#!/usr/bin/env sh
exit 0
SH
chmod +x "${mockbin}/chown" "${mockbin}/haproxy"
PATH="${mockbin}:$PATH" \
TDH_SOURCE_ONLY=1 \
TDH_BASE="${TMP_ROOT}/haproxy-etc" \
TDH_STATE="${TMP_ROOT}/haproxy-etc/state.env" \
TDH_KEY_DIR="${TMP_ROOT}/haproxy-etc/keys" \
TDH_CONFIG_DIR="${TMP_ROOT}/haproxy-etc/telemt" \
TDH_WORK_ROOT="${TMP_ROOT}/haproxy-work" \
TDH_BIN="${TMP_ROOT}/bin/telemt" \
TDH_MANAGER="${TMP_ROOT}/bin/manager" \
TDH_HAPROXY_CFG="${TMP_ROOT}/haproxy-etc/haproxy.cfg" \
bash -Eeuo pipefail -s "$SCRIPT" <<'BASH'
source "$1"
mkdir -p "$TDH_BASE"
PORT_A=443 PORT_B=443
ENTRY_MAX_CONNECTIONS=2000 B1_MAX_CONNECTIONS=4000 B2_MAX_CONNECTIONS=2000
write_haproxy_config
BASH

haproxy_cfg="${TMP_ROOT}/haproxy-etc/haproxy.cfg"
[[ $(grep -c 'bind 0.0.0.0:443' "$haproxy_cfg") == 1 ]]
grep -q '^    maxconn 2000$' "$haproxy_cfg"
if grep -q ':8443' "$haproxy_cfg"; then
  echo "双 SNI 配置仍然监听 8443" >&2
  exit 1
fi
grep -q 'tcp-request inspect-delay 3s' "$haproxy_cfg"
grep -q '^    log stdout format raw local0 warning$' "$haproxy_cfg"
grep -q '^    zero-warning$' "$haproxy_cfg"
grep -q '^    option dontlog-normal$' "$haproxy_cfg"
if grep -q 'option tcplog' "$haproxy_cfg"; then
  echo "HAProxy 仍记录每条正常 TCP 会话" >&2
  exit 1
fi
grep -q 'tcp-request content accept if { req.ssl_hello_type 1 }' "$haproxy_cfg"
grep -q 'acl sni_line_a req.ssl_sni -i apple.com' "$haproxy_cfg"
grep -q 'acl sni_line_b req.ssl_sni -i gs.apple.com' "$haproxy_cfg"
grep -q 'use_backend telemt_a if sni_line_a' "$haproxy_cfg"
grep -q 'use_backend telemt_b if sni_line_b' "$haproxy_cfg"
[[ $(grep -Ec '^    server vps[12] 10\.77\.[12]\.2:24431 ' "$haproxy_cfg") == 4 ]]
if grep -Eq '10\.77\.[12]\.2:(24432|19102)' "$haproxy_cfg"; then
  echo "HAProxy 仍引用已删除的第二实例端口" >&2
  exit 1
fi
grep -q 'server apple_a apple.com:443 .* backup ' "$haproxy_cfg"
grep -q 'server apple_b gs.apple.com:443 .* backup ' "$haproxy_cfg"
grep -q 'default_backend tls_fallback_unknown' "$haproxy_cfg"
grep -q 'server apple_unknown apple.com:443 ' "$haproxy_cfg"
if grep -E 'server apple_(a|b|unknown).*send-proxy' "$haproxy_cfg"; then
  echo "Apple 回落错误携带 PROXY protocol" >&2
  exit 1
fi
real_haproxy=${TDH_REAL_HAPROXY:-/usr/sbin/haproxy}
if [[ -x $real_haproxy ]]; then
  parser_cfg="${TMP_ROOT}/haproxy-parser.cfg"
  cp "$haproxy_cfg" "$parser_cfg"
  if ! getent passwd haproxy >/dev/null || ! getent group haproxy >/dev/null; then
    sed -i -e 's/^    user haproxy$/    user nobody/' \
      -e 's/^    group haproxy$/    group nogroup/' "$parser_cfg"
  fi
  sed -i -e 's/^    nameserver local .*$/    nameserver local 127.0.0.1:53/' \
    -e 's/ apple.com:443 / 127.0.0.1:443 /' \
    -e 's/ gs.apple.com:443 / 127.0.0.1:443 /' "$parser_cfg"
  "$real_haproxy" -c -f "$parser_cfg"
fi

if [[ -n ${TDH_REAL_TELEMT:-} ]]; then
  [[ -x $TDH_REAL_TELEMT ]]
  [[ $("$TDH_REAL_TELEMT" --version) == "telemt 3.5.5" ]]
  read -r TELEMT_BIND TELEMT_API < <(python3 - <<'PY'
import socket
sockets = []
for _ in range(2):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sockets.append(sock)
print(*(sock.getsockname()[1] for sock in sockets))
for sock in sockets:
    sock.close()
PY
  )
  PATH="${mockbin}:$PATH" \
  TDH_SOURCE_ONLY=1 \
  TDH_BASE="${TMP_ROOT}/telemt-etc" \
  TDH_STATE="${TMP_ROOT}/telemt-etc/state.env" \
  TDH_KEY_DIR="${TMP_ROOT}/telemt-etc/keys" \
  TDH_CONFIG_DIR="${TMP_ROOT}/telemt-etc/config" \
  TDH_WORK_ROOT="${TMP_ROOT}/telemt-work" \
  TDH_BIN="$TDH_REAL_TELEMT" \
  TDH_MANAGER="${TMP_ROOT}/bin/manager" \
  TDH_HAPROXY_CFG="${TMP_ROOT}/telemt-etc/haproxy.cfg" \
  TELEMT_BIND="$TELEMT_BIND" TELEMT_API="$TELEMT_API" \
  bash -Eeuo pipefail -s "$SCRIPT" <<'BASH'
source "$1"
install() {
  if [[ ${1:-} == -d ]]; then
    shift
    while (( $# > 0 )); do
      case $1 in
        -o|-g|-m) shift 2 ;;
        *) mkdir -p "$1"; shift ;;
      esac
    done
  else
    command install "$@"
  fi
}
mkdir -p "$TDH_BASE" "$TDH_CONFIG_DIR" "$TDH_WORK_ROOT"
ENTRY_PUBLIC_IP=203.0.113.10 ENTRY_WG_IP=127.0.0.1
API_TOKEN=$(printf 'ef%.0s' {1..32})
BACKEND_MAX_CONNECTIONS=4000
BACKEND_BUFFER_BUDGET_BYTES=536870912
SECRET_A=00112233445566778899aabbccddeeff
SECRET_B=ffeeddccbbaa99887766554433221100
write_telemt_config 127.0.0.1 "$TELEMT_BIND" "$TELEMT_API" 443
BASH
  config="${TMP_ROOT}/telemt-etc/config/telemt.toml"
  [[ $(find "${TMP_ROOT}/telemt-etc/config" -maxdepth 1 -name '*.toml' | wc -l) == 1 ]]
  grep -q '^config_strict = true$' "$config"
  grep -q '^fast_mode = true$' "$config"
  grep -q '^use_middle_proxy = false$' "$config"
  grep -q '^beobachten = false$' "$config"
  grep -q '^max_connections = 4000$' "$config"
  grep -q '^direct_relay_buffer_budget_max_bytes = 536870912$' "$config"
  grep -q '^listen_backlog = 4096$' "$config"
  grep -q '^inline_conntrack_control = false$' "$config"
  grep -q '^tls_domain = "apple.com"$' "$config"
  grep -q '^tls_domains = \["gs.apple.com"\]$' "$config"
  grep -q '^unknown_sni_action = "mask"$' "$config"
  grep -q '^type = "direct"$' "$config"
  telemt_log="${TMP_ROOT}/telemt.log"
  (
    cd "${TMP_ROOT}/telemt-work/telemt"
    exec "$TDH_REAL_TELEMT" "$config"
  ) >"$telemt_log" 2>&1 &
  TELEMT_PID=$!
  api_json="${TMP_ROOT}/telemt-users.json"
  api_ready=0
  for _ in {1..80}; do
    if curl --noproxy '*' -fsS --connect-timeout 1 --max-time 2 \
      -H "Authorization: Bearer $(printf 'ef%.0s' {1..32})" \
      "http://127.0.0.1:${TELEMT_API}/v1/users" >"$api_json" 2>/dev/null; then
      api_ready=1
      break
    fi
    kill -0 "$TELEMT_PID" 2>/dev/null || break
    sleep 0.25
  done
  if [[ $api_ready != 1 ]]; then
    cat "$telemt_log" >&2
    echo "Telemt 单实例未能启动本机 API" >&2
    exit 1
  fi
  [[ $(jq '.data | length' "$api_json") == 2 ]]
  for line in a b; do
    if [[ $line == a ]]; then
      expected_secret=00112233445566778899aabbccddeeff
      expected_domain=apple.com
    else
      expected_secret=ffeeddccbbaa99887766554433221100
      expected_domain=gs.apple.com
    fi
    domain_hex=$(printf '%s' "$expected_domain" | od -An -tx1 | tr -d ' \n')
    actual_link=$(jq -er --arg user "line_${line}" --arg suffix "$domain_hex" \
      'first(.data[] | select(.username == $user) | .links.tls[] | select(endswith($suffix)))' "$api_json")
    TDH_LINK=$actual_link TDH_EXPECT_SECRET=$expected_secret TDH_EXPECT_DOMAIN=$expected_domain python3 - <<'PY'
import os
import urllib.parse
url = urllib.parse.urlparse(os.environ["TDH_LINK"])
params = urllib.parse.parse_qs(url.query, strict_parsing=True)
expected = "ee" + os.environ["TDH_EXPECT_SECRET"] + os.environ["TDH_EXPECT_DOMAIN"].encode().hex()
if params != {"server": ["203.0.113.10"], "port": ["443"], "secret": [expected]}:
    raise SystemExit("Telemt 单实例生成的双 SNI 链接不一致")
PY
  done
  health_json="${TMP_ROOT}/telemt-health.json"
  curl --noproxy '*' -sS --connect-timeout 1 --max-time 3 \
    -H "Authorization: Bearer $(printf 'ef%.0s' {1..32})" \
    "http://127.0.0.1:${TELEMT_API}/v1/health/ready" >"$health_json" || true
  jq -e '.ok == true and (.data.ready | type == "boolean")' "$health_json" >/dev/null
  kill "$TELEMT_PID"
  wait "$TELEMT_PID" 2>/dev/null || true
  TELEMT_PID=""
fi

health_agent_py="${TMP_ROOT}/health_agent.py"
awk '/health_agent.py <<'\''PY'\''/{inside=1; next} inside && $0=="PY"{exit} inside{print}' "$SCRIPT" >"$health_agent_py"
python3 -m py_compile "$health_agent_py"

read -r API_PORT AGENT_PORT < <(python3 - <<'PY'
import socket
sockets = []
for _ in range(2):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sockets.append(sock)
print(*(sock.getsockname()[1] for sock in sockets))
for sock in sockets:
    sock.close()
PY
)
printf '%s\n' "$(printf 'ef%.0s' {1..32})" >"${TMP_ROOT}/api.token"
cat >"${TMP_ROOT}/health_stub.py" <<'PY'
import json, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

token = "Bearer " + "ef" * 32

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/v1/health/ready" or self.headers.get("Authorization") != token:
            self.send_response(403); self.end_headers(); return
        body = json.dumps({"ok": True, "data": {"ready": True}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def log_message(self, *_):
        pass

ThreadingHTTPServer(("127.0.0.1", int(os.environ["API_PORT"])), Handler).serve_forever()
PY
API_PORT=$API_PORT python3 "${TMP_ROOT}/health_stub.py" &
STUB_PID=$!
HTTP_PROXY=http://127.0.0.1:1 HTTPS_PROXY=http://127.0.0.1:1 NO_PROXY='' \
TDH_BIND_IP=127.0.0.1 TDH_AGENT_PORT=$AGENT_PORT TDH_API_PORT=$API_PORT \
TDH_DC_IPV4=127.0.0.1 TDH_DC_PORT=$API_PORT \
TDH_API_TOKEN_FILE="${TMP_ROOT}/api.token" \
  python3 "$health_agent_py" &
AGENT_PID=$!
python3 - "$AGENT_PORT" <<'PY'
import socket, sys, time

def get_status():
    try:
        with socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=1) as conn:
            return conn.recv(16).decode().strip()
    except OSError:
        return ""

for _ in range(150):
    if get_status() == "up":
        break
    time.sleep(0.1)
else:
    raise SystemExit("健康代理没有把 ready API 报告为 up")

started = time.monotonic()
if get_status() != "up":
    raise SystemExit("健康代理缓存状态异常")
if time.monotonic() - started > 0.5:
    raise SystemExit("健康代理响应没有使用内存缓存")
PY
kill "$STUB_PID"
wait "$STUB_PID" 2>/dev/null || true
STUB_PID=""
python3 - "$AGENT_PORT" <<'PY'
import socket, sys, time

def get_status():
    try:
        with socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=1) as conn:
            return conn.recv(16).decode().strip()
    except OSError:
        return ""

for _ in range(70):
    if get_status() == "down":
        break
    time.sleep(0.1)
else:
    raise SystemExit("Telemt API 停止后健康代理没有转为 down")
PY
kill "$AGENT_PID"
wait "$AGENT_PID" 2>/dev/null || true
AGENT_PID=""

# 即使 Telemt 本机 API 正常，只要任一固定 Telegram DC 不可达，后端也必须退出轮询。
read -r STRICT_AGENT_PORT < <(python3 - <<'PY'
import socket
sock = socket.socket()
sock.bind(("127.0.0.1", 0))
print(sock.getsockname()[1])
sock.close()
PY
)
API_PORT=$API_PORT python3 "${TMP_ROOT}/health_stub.py" &
STUB_PID=$!
HTTP_PROXY=http://127.0.0.1:1 HTTPS_PROXY=http://127.0.0.1:1 NO_PROXY='' \
TDH_BIND_IP=127.0.0.1 TDH_AGENT_PORT=$STRICT_AGENT_PORT TDH_API_PORT=$API_PORT \
TDH_DC_IPV4=127.0.0.1,192.0.2.1 TDH_DC_PORT=$API_PORT \
TDH_API_TOKEN_FILE="${TMP_ROOT}/api.token" \
  python3 "$health_agent_py" &
AGENT_PID=$!
python3 - "$STRICT_AGENT_PORT" <<'PY'
import socket, sys, time

def get_status():
    try:
        with socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=1) as conn:
            return conn.recv(16).decode().strip()
    except OSError:
        return ""

for _ in range(50):
    status = get_status()
    if status:
        break
    time.sleep(0.1)
else:
    raise SystemExit("严格 DC 健康代理没有开始响应")
if status != "down":
    raise SystemExit("任一 Telegram DC 不可达时健康代理仍错误报告为 up")
PY
kill "$AGENT_PID" "$STUB_PID"
wait "$AGENT_PID" 2>/dev/null || true
wait "$STUB_PID" 2>/dev/null || true
AGENT_PID=""
STUB_PID=""

startlimit_service_lines=$(awk '
  /^\[Service\]$/{service=1; next}
  /^\[/{service=0}
  service && /^StartLimit/{count++}
  END{print count+0}
' "$SCRIPT")
[[ $startlimit_service_lines == 0 ]]
[[ $(grep -c '^StartLimitIntervalSec=0$' "$SCRIPT") == 3 ]]
[[ $(grep -c '^Restart=always$' "$SCRIPT") == 3 ]]
if grep -q '^Restart=on-failure$' "$SCRIPT"; then
  echo "生产服务仍可能在正常退出后永久停止" >&2
  exit 1
fi

TDH_SOURCE_ONLY=1 \
TDH_BASE="${TMP_ROOT}/tuning-etc" \
TDH_STATE="${TMP_ROOT}/tuning-etc/state.env" \
TDH_KEY_DIR="${TMP_ROOT}/tuning-etc/keys" \
TDH_CONFIG_DIR="${TMP_ROOT}/tuning-etc/telemt" \
TDH_WORK_ROOT="${TMP_ROOT}/tuning-work" \
TDH_BIN="${TMP_ROOT}/bin/telemt" \
TDH_MANAGER="${TMP_ROOT}/bin/manager" \
TDH_HAPROXY_CFG="${TMP_ROOT}/tuning-etc/haproxy.cfg" \
bash -Eeuo pipefail -s "$SCRIPT" <<'BASH'
source "$1"
sysctl() {
  case ${*: -1} in
    net.core.default_qdisc) printf 'fq\n' ;;
    net.ipv4.tcp_congestion_control) printf 'bbr\n' ;;
    *) return 1 ;;
  esac
}
tuning_is_active
date() { printf '2000\n'; }
wg() { printf 'peer-key\t1900\n'; }
wireguard_handshake_is_fresh tdh-test 180
wg() { printf 'peer-key\t1700\n'; }
if wireguard_handshake_is_fresh tdh-test 180; then
  echo "过期 WireGuard 握手被错误接受" >&2
  exit 1
fi
BASH

if command -v shellcheck >/dev/null 2>&1; then
  shellcheck -x "$SCRIPT" "$ROOT/tests/test.sh"
fi

echo "全部测试通过"
