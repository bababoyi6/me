#!/usr/bin/env bash
# Historical API helper, used only with synthetic credentials in loopback tests.
# shellcheck disable=SC2154
query_telemt_link() {
  local api_port=$1 username=$2 domain=$3 domain_hex
  local -a auth=()
  if [[ -n $API_TOKEN ]]; then auth=(-H "Authorization: Bearer ${API_TOKEN}"); fi
  domain_hex=$(printf '%s' "$domain" | od -An -tx1 | tr -d ' \n')
  curl -fsS --connect-timeout 2 --max-time 5 "${auth[@]}" "http://127.0.0.1:${api_port}/v1/users" | \
    jq -er --arg username "$username" --arg suffix "$domain_hex" \
      'first(.data[] | select(.username == $username) | .links.tls[]? | select(ascii_downcase | endswith($suffix)))'
}
