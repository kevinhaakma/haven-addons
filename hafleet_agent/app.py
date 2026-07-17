#!/usr/bin/env python3
"""HA Fleet Agent — draait als add-on op de HA van een familielid/vriend.

Belt ZELF uit (reverse tunnel over WebSocket, meestal wss:// via Cloudflare)
naar de HA Fleet Hub van de beheerder. Geen VPN of port-forward nodig; er
verlaat geen token dit systeem — de agent gebruikt het lokale
SUPERVISOR_TOKEN om Core- en Supervisor-API's aan te roepen.

Config (add-on opties in /data/options.json, of env voor lokaal testen):
  hub_url    wss://fleet.example.nl/agent/ws
  agent_id   naam die de beheerder uitgaf
  agent_key  geheime sleutel die de beheerder uitgaf
"""

import base64
import json
import os
import posixpath
import re
import secrets
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

VERSION = "1.3.0"

OPTIONS = {}
if os.path.exists("/data/options.json"):
    OPTIONS = json.load(open("/data/options.json"))

HUB_URL = os.environ.get("HUB_URL") or OPTIONS.get("hub_url", "")
AGENT_ID = os.environ.get("AGENT_ID") or OPTIONS.get("agent_id", "")
AGENT_KEY = os.environ.get("AGENT_KEY") or OPTIONS.get("agent_key", "")
TOKEN = os.environ.get("TOKEN") or os.environ.get("SUPERVISOR_TOKEN", "")
CORE_BASE = os.environ.get("CORE_BASE", "http://supervisor/core")
SUP_BASE = os.environ.get("SUP_BASE", "http://supervisor")
INSECURE = os.environ.get("INSECURE") == "1"
BACKUP_KEY_FILE = os.environ.get("BACKUP_KEY_FILE", "/data/backup_key")
PING_INTERVAL = 30
MAX_FRAME = 8 * 1024 * 1024            # 8 MB — begrens inkomende frames van de hub
MAX_WORKERS = 8                        # begrensde pool i.p.v. thread-per-bericht


# ---------- WebSocket-client (stdlib) ----------

class FrameIO:
    def __init__(self, sock):
        self.sock = sock
        self.buf = b""
        self.wlock = threading.Lock()

    def _recv_exact(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("verbinding gesloten")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send_frame(self, opcode, payload=b""):
        head = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + n.to_bytes(2, "big")
        else:
            head += bytes([0x80 | 127]) + n.to_bytes(8, "big")
        mask = secrets.token_bytes(4)
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self.wlock:
            self.sock.sendall(head + mask + payload)

    def send_json(self, obj):
        self.send_frame(0x1, json.dumps(obj).encode())

    def recv_json(self):
        msg = b""
        while True:
            b1, b2 = self._recv_exact(2)
            fin, opcode = b1 & 0x80, b1 & 0x0F
            n = b2 & 0x7F
            if n == 126:
                n = int.from_bytes(self._recv_exact(2), "big")
            elif n == 127:
                n = int.from_bytes(self._recv_exact(8), "big")
            if n > MAX_FRAME or len(msg) + n > MAX_FRAME:
                raise ConnectionError("frame te groot")
            payload = self._recv_exact(n)          # server maskeert niet
            if opcode == 0x9:
                self.send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x8:
                raise ConnectionError("hub sloot de verbinding")
            msg += payload
            if fin:
                return json.loads(msg)


def connect():
    u = urlparse(HUB_URL)
    tls = u.scheme in ("wss", "https")
    port = u.port or (443 if tls else 80)
    sock = socket.create_connection((u.hostname, port), timeout=20)
    if tls:
        ctx = ssl.create_default_context()
        if INSECURE:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        sock = ctx.wrap_socket(sock, server_hostname=u.hostname)
    key = base64.b64encode(secrets.token_bytes(16)).decode()
    sock.sendall((
        f"GET {u.path or '/'} HTTP/1.1\r\nHost: {u.hostname}:{port}\r\n"
        "Upgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
        f"X-Agent-Id: {AGENT_ID}\r\nX-Agent-Key: {AGENT_KEY}\r\n\r\n"
    ).encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("handshake afgebroken")
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    status = head.split(b"\r\n", 1)[0].decode()
    if " 101" not in status:
        raise ConnectionError(f"hub weigerde: {status}")
    io = FrameIO(sock)
    io.buf = rest
    return io


# ---------- end-to-end versleutelde backups ----------
#
# Backups die via de hub worden aangevraagd, versleutelt de agent met een
# wachtwoord dat ALLEEN op deze box bestaat (/data/backup_key). De hub ziet
# het wachtwoord nooit — alleen de eigenaar van deze machine kan zijn eigen
# backups openen/herstellen.

_backup_key_cache = None


def _notify_owner(key):
    """Persistente notificatie in de lokale HA-UI met het backup-wachtwoord.

    Zonder dit wachtwoord is een backup niet te herstellen als deze machine
    wegvalt — de eigenaar moet het dus buiten deze box bewaren."""
    try:
        req = urllib.request.Request(
            CORE_BASE.rstrip("/") + "/api/services/persistent_notification/create",
            method="POST")
        req.add_header("Authorization", f"Bearer {TOKEN}")
        req.add_header("Content-Type", "application/json")
        body = {
            "title": "HA Fleet Agent: backup-wachtwoord",
            "message": (
                "Backups via HA Fleet worden end-to-end versleuteld met dit "
                f"wachtwoord:\n\n**`{key}`**\n\nBewaar het op een veilige plek "
                "búiten deze machine (wachtwoordmanager). Zonder dit wachtwoord "
                "is een backup niet te herstellen als deze machine wegvalt. "
                "Het wachtwoord staat ook in `/data/backup_key` van de add-on."),
            "notification_id": "hafleet_backup_key",
        }
        urllib.request.urlopen(req, data=json.dumps(body).encode(), timeout=10).close()
    except Exception as e:
        print(f"kon backup-wachtwoord-notificatie niet plaatsen ({type(e).__name__}: {e})",
              flush=True)


def backup_key():
    global _backup_key_cache
    if _backup_key_cache:
        return _backup_key_cache
    if os.environ.get("BACKUP_KEY"):
        _backup_key_cache = os.environ["BACKUP_KEY"]
        return _backup_key_cache
    try:
        _backup_key_cache = open(BACKUP_KEY_FILE).read().strip()
    except FileNotFoundError:
        _backup_key_cache = secrets.token_urlsafe(24)
        try:
            fd = os.open(BACKUP_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(_backup_key_cache + "\n")
        except FileNotFoundError:      # geen /data (lokaal dev) — alleen in geheugen
            print("waarschuwing: /data ontbreekt, backup-wachtwoord is niet persistent",
                  flush=True)
            return _backup_key_cache
        print("nieuw backup-wachtwoord aangemaakt in /data/backup_key — "
              "zie de notificatie in de HA-UI en bewaar het veilig", flush=True)
        _notify_owner(_backup_key_cache)
    return _backup_key_cache


# ---------- lokale API-calls ----------

# Vaste allowlist voor kind "supervisor": exacte paden of prefixen die zijn
# toegestaan. /addons/<slug>/update wordt apart gecontroleerd (slug-patroon)
# om regex-injectie via het pad te voorkomen.
SUPERVISOR_EXACT_PATHS = {
    "/core/info", "/os/info", "/supervisor/info", "/host/info",
    "/resolution/info", "/core/logs", "/addons", "/backups",
    "/backups/new/full", "/core/update",
}
SLUG_RE = re.compile(r"^[a-z0-9_-]+$")

# Allowlist voor kind "core", vernauwd tot precies wat de hub gebruikt —
# per pad ook de toegestane method, zodat een gecompromitteerde hub niet
# alsnog willekeurige service-calls of config-writes kan doen.
CORE_GET_PATHS = {
    "/api/config", "/api/states", "/api/config/config_entries/entry",
}
CORE_POST_PATHS = {
    "/api/services/homeassistant/restart",
}
CORE_POST_RE = (
    re.compile(r"^/api/services/notify/[a-z0-9_]+$"),
    re.compile(r"^/api/config/config_entries/entry/[A-Za-z0-9]+/reload$"),
)


def _core_allowed(method, path):
    if method == "GET":
        return path in CORE_GET_PATHS
    if method == "POST":
        return path in CORE_POST_PATHS or any(rx.match(path) for rx in CORE_POST_RE)
    return False


def _supervisor_path_allowed(path):
    if path in SUPERVISOR_EXACT_PATHS:
        return True
    parts = path.split("/")
    # verwacht: ["", "addons", "<slug>", "update"]
    if len(parts) == 4 and parts[0] == "" and parts[1] == "addons" and parts[3] == "update":
        return bool(SLUG_RE.match(parts[2]))
    return False


def local_request(kind, method, path, body, timeout):
    # pad normaliseren VOOR de allowlist-checks: '/api/../x' mag de
    # prefix-check niet passeren en bij de upstream alsnog elders uitkomen
    path = posixpath.normpath(path)
    if ".." in path.split("/") or "?" in path or "#" in path:
        return 403, "pad niet toegestaan (normalisatie)"
    if kind == "core":
        if not _core_allowed(method.upper(), path):
            return 403, f"pad niet toegestaan voor kind 'core': {method.upper()} {path}"
        url = CORE_BASE.rstrip("/") + path
    elif kind == "supervisor":
        if not SUP_BASE:
            return 501, "supervisor niet beschikbaar"
        if not _supervisor_path_allowed(path):
            return 403, f"pad niet toegestaan voor kind 'supervisor': {path}"
        if path == "/backups/new/full":
            # e2e: altijd het EIGEN wachtwoord — ook als de hub er een meestuurt
            body = {**(body or {}), "password": backup_key()}
        url = SUP_BASE.rstrip("/") + path
    else:
        return 400, f"onbekende kind '{kind}'"
    req = urllib.request.Request(url, method=method.upper())
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Content-Type", "application/json")
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:500]
    except Exception as e:
        return 502, f"{type(e).__name__}: {e}"


def handle(io, msg):
    rid = msg.get("id")
    status, body = local_request(msg.get("kind"), msg.get("method", "GET"),
                                 msg.get("path", "/"), msg.get("body"),
                                 min(int(msg.get("timeout", 30)), 3600))
    try:
        io.send_json({"id": rid, "status": status, "body": body})
    except Exception:
        pass


def pinger(io):
    while True:
        time.sleep(PING_INTERVAL)
        try:
            io.send_frame(0x9, b"ka")
        except Exception:
            return


def main():
    if not (HUB_URL and AGENT_ID and AGENT_KEY):
        print("hub_url / agent_id / agent_key niet geconfigureerd — "
              "vul de add-on opties in", flush=True)
        time.sleep(300)
        return
    backup_key()      # zorg dat het e2e-backup-wachtwoord bij de eerste start
                      # al bestaat én de eigenaar de notificatie krijgt
    # begrensde worker-pool: een kwaadwillende/kapotte hub kan niet ongelimiteerd
    # threads laten spawnen door berichten te spammen
    pool = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    backoff = 5
    while True:
        try:
            print(f"verbinden met {HUB_URL} als '{AGENT_ID}'…", flush=True)
            io = connect()
            print("verbonden met de hub", flush=True)
            backoff = 5
            io.send_json({"type": "hello", "meta": {
                "agent_id": AGENT_ID,
                "hostname": socket.gethostname(),
                "version": VERSION,
            }})
            threading.Thread(target=pinger, args=(io,), daemon=True).start()
            while True:
                msg = io.recv_json()
                pool.submit(handle, io, msg)
        except Exception as e:
            print(f"verbinding weg ({type(e).__name__}: {e}) — "
                  f"opnieuw over {backoff}s", flush=True)
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)


if __name__ == "__main__":
    main()
