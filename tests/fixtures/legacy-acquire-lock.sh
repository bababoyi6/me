#!/usr/bin/env bash
# Original acquire_lock body from c4e2111; temporary-file regression only.
acquire_lock() {
  mkdir -p "$(dirname "$TDH_LOCK")"
  exec 9>"$TDH_LOCK"
  flock -n 9 || die "另一个 Telemt Dual-Hop 操作正在运行。"
}
