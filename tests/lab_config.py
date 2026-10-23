"""Generate production-template configurations with synthetic loopback identities."""
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def generate_telemt(config_dir, work_dir, listen_port, api_port, bind_ip="127.0.0.1", entry_ip="127.0.0.1"):
    config_dir, work_dir = Path(config_dir), Path(work_dir)
    config_dir.mkdir(mode=0o700, exist_ok=True)
    work_dir.mkdir(mode=0o700, exist_ok=True)
    env = dict(os.environ, TDH_SOURCE_ONLY="1", TDH_CONFIG_DIR=str(config_dir), TDH_WORK_ROOT=str(work_dir))
    generated = subprocess.run(["bash", "-c", '''
set -Eeuo pipefail
source "$1"
install() {
  local args=()
  while (( $# )); do case $1 in -o|-g) shift 2;; *) args+=("$1"); shift;; esac; done
  command install "${args[@]}"
}
chown() { :; }
BACKEND_MAX_CONNECTIONS=2048 BACKEND_BUFFER_BUDGET_BYTES=268435456
ENTRY_PUBLIC_IP=203.0.113.10 ENTRY_WG_IP="$5"
API_TOKEN=synthetic-telemt-probe-token
SECRET_A=10101010101010101010101010101010 SECRET_B=20202020202020202020202020202020
write_telemt_config "$4" "$2" "$3" 443
''', "probe", str(ROOT / "telemt-dual-hop.sh"), str(listen_port), str(api_port), bind_ip, entry_ip],
                               env=env, capture_output=True, timeout=10)
    if generated.returncode:
        raise RuntimeError("configuration generation failed; details withheld")
    return config_dir / "telemt.toml"
