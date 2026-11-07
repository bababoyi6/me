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


monitors = [threading.Thread(target=target, daemon=True) for target in (monitor_api, monitor_dcs)]
for monitor in monitors:
    monitor.start()

while True:
    if any(not monitor.is_alive() for monitor in monitors):
        # Let the existing systemd restart policy replace a failed monitor thread.
        raise SystemExit("health monitor stopped")
    for key, _ in sel.select(timeout=30):
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
