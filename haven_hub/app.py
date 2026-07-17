#!/usr/bin/env python3
"""Haven Hub — de veilige thuishaven voor meerdere Home Assistant-instanties.

Twee soorten instanties:
  - direct:  {"url": "...", "verify_ssl": false?}  + token in tokens/<naam>
  - agent:   {"agent_id": "..."}                   — de remote HA draait de
             "HA Fleet Agent" add-on die ZELF uitbelt naar deze hub (reverse
             tunnel over WebSocket). Geen VPN/port-forward bij familie nodig;
             tokens blijven op hun eigen box (agent gebruikt SUPERVISOR_TOKEN).

Endpoints:
  /                — web-UI (alleen via HA-ingress of loopback)
  /api/...         — REST voor de UI (zelfde restrictie)
  /agent/ws        — agent-tunnel (publiek te exposen via bijv. Cloudflared;
                     auth met per-agent sleutel, sha256-gehasht opgeslagen)

Data: $HAFLEET_DATA of /data (add-on) of ~/.config/ha-fleet (dev)
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

VERSION = "2.0.0"

# Add-on-opties (/data/options.json); env-vars winnen voor lokaal ontwikkelen
_OPTS = {}
if Path("/data/options.json").is_file():
    try:
        _OPTS = json.loads(Path("/data/options.json").read_text())
    except (OSError, json.JSONDecodeError):
        _OPTS = {}

if os.environ.get("HAVEN_DATA") or os.environ.get("HAFLEET_DATA"):
    DATA_DIR = Path(os.environ.get("HAVEN_DATA")
                    or os.environ["HAFLEET_DATA"]).expanduser()
elif Path("/data").is_dir() and Path("/config").is_dir():
    DATA_DIR = Path("/config")       # add-on met addon_config-mapping (= /addon_configs/<slug>)
elif Path("/data").is_dir():
    DATA_DIR = Path("/data")
else:
    DATA_DIR = Path.home() / ".config" / "ha-fleet"
CONFIG_FILE = DATA_DIR / "instances.json"
AGENTS_FILE = DATA_DIR / "agents.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
AUDIT_FILE = DATA_DIR / "audit.jsonl"
ALERTS_FILE = DATA_DIR / "alerts.json"
TOKENS_DIR = DATA_DIR / "tokens"
STATIC_DIR = Path(__file__).parent / "static"
PORT = int(os.environ.get("PORT", "8099"))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL") or _OPTS.get("poll_interval") or 300)
PUBLIC_HUB_URL = (os.environ.get("PUBLIC_HUB_URL") or _OPTS.get("public_hub_url")
                  or "wss://fleet.kvn.frl/agent/ws")

ALLOWED_CLIENTS = {"127.0.0.1", "::1"}
# Alleen de HA ingress-proxy mag bij de UI/API. Bewust GEEN hele /24 (dan zou
# een add-on op het hassio-net — bijv. de cloudflared-container die publiek
# fleet.kvn.frl doorstuurt — ook bij de API kunnen). Override via env indien nodig.
ALLOWED_CLIENTS |= set(filter(None, os.environ.get("ALLOWED_INGRESS_IPS", "172.30.32.2").split(",")))
MAX_FRAME = 8 * 1024 * 1024            # 8 MB — ruim voor /api/states, dicht tegen DoS
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

TIMEOUT = 12
_lock = threading.Lock()
_audit_lock = threading.Lock()   # eigen lock: audit-I/O blokkeert cache/agents niet
_cache = {}        # naam -> laatste health-samenvatting
_jobs = {}         # id -> {status, label, result, error, started}
_agents = {}       # agent_id -> AgentConn (live verbindingen)
_alerts = {}       # (instance, type) -> alert-dict (actieve alerts in geheugen)
_auth_fails = {}   # agent_id -> [timestamps] (rate-limit mislukte agent-auth)

DEFAULT_SETTINGS = {
    "alerts_enabled": True,
    "notify_service": "",
    "alert_instance": "thuis",
    "backup_day": -1,
    "backup_hour": 3,
    "thresholds": {"dead_pct": 15, "backup_max_days": 8, "offline_min": 30},
}


# ---------- opslag ----------

def _load(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text())


def _save(path, obj):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")
    os.chmod(path, 0o600)


def load_config():
    return _load(CONFIG_FILE, {"instances": {}})


def save_config(cfg):
    _save(CONFIG_FILE, cfg)


def load_agents():
    return _load(AGENTS_FILE, {})


def save_agents(agents):
    _save(AGENTS_FILE, agents)


def load_settings():
    s = _load(SETTINGS_FILE, {})
    merged = json.loads(json.dumps(DEFAULT_SETTINGS))    # deep copy
    merged.update(s)
    merged["thresholds"] = {**DEFAULT_SETTINGS["thresholds"], **s.get("thresholds", {})}
    return merged


def save_settings(settings):
    _save(SETTINGS_FILE, settings)


def load_alerts_state():
    return _load(ALERTS_FILE, {"active": {}, "last_sent": {}, "last_backup": None})


def save_alerts_state(state):
    _save(ALERTS_FILE, state)


def append_audit(action, instance="", detail="", ok=True):
    """Schrijft 1 regel naar het audit-log; ruimt op boven de 5000 regels."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    entry = {"ts": datetime.now(timezone.utc).isoformat(), "action": action,
              "instance": instance, "detail": detail, "ok": bool(ok)}
    with _audit_lock:
        with AUDIT_FILE.open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        try:
            lines = AUDIT_FILE.read_text().splitlines()
        except FileNotFoundError:
            lines = []
        if len(lines) > 5000:
            lines = lines[len(lines) // 2:]
            AUDIT_FILE.write_text("\n".join(lines) + "\n")


def load_audit(limit=200):
    if not AUDIT_FILE.exists():
        return []
    lines = AUDIT_FILE.read_text().splitlines()
    events = []
    for line in lines:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    events.reverse()
    return events[:limit]


def check_auth_rate_limit(agent_id):
    """True als agent_id nog een auth-poging mag wagen (max 5 per 60s)."""
    now = time.time()
    with _lock:
        attempts = [t for t in _auth_fails.get(agent_id[:64], []) if now - t < 60]
        _auth_fails[agent_id[:64]] = attempts
        return len(attempts) < 5


def record_auth_failure(agent_id):
    now = time.time()
    with _lock:
        # lege/verlopen tellers opruimen zodat willekeurige agent_id's het
        # geheugen niet gestaag laten groeien (publiek pad zonder IP-check)
        for aid in [a for a, ts in _auth_fails.items()
                    if not ts or now - ts[-1] > 120]:
            del _auth_fails[aid]
        if len(_auth_fails) > 500:
            _auth_fails.clear()
        _auth_fails.setdefault(agent_id[:64], []).append(now)


def read_token(inst, name):
    path = Path(inst.get("token_file") or (TOKENS_DIR / name)).expanduser()
    if not path.exists():
        raise RuntimeError(f"geen token voor '{name}'")
    return path.read_text().strip()


def write_token(name, token):
    TOKENS_DIR.mkdir(parents=True, exist_ok=True)
    path = TOKENS_DIR / name
    path.write_text(token + "\n")
    os.chmod(path, 0o600)


# ---------- WebSocket frame-codec (gedeeld client/server, stdlib) ----------

class FrameIO:
    """Tekstframes over een socket. Client → server maskeert; server niet."""

    def __init__(self, sock, mask_out):
        self.sock = sock
        self.mask_out = mask_out
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
        maskbit = 0x80 if self.mask_out else 0
        if n < 126:
            head += bytes([maskbit | n])
        elif n < 65536:
            head += bytes([maskbit | 126]) + n.to_bytes(2, "big")
        else:
            head += bytes([maskbit | 127]) + n.to_bytes(8, "big")
        if self.mask_out:
            mask = secrets.token_bytes(4)
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            head += mask
        with self.wlock:
            self.sock.sendall(head + payload)

    def send_json(self, obj):
        self.send_frame(0x1, json.dumps(obj).encode())

    def recv_json(self):
        msg = b""
        while True:
            b1, b2 = self._recv_exact(2)
            fin, opcode = b1 & 0x80, b1 & 0x0F
            masked = b2 & 0x80
            n = b2 & 0x7F
            if n == 126:
                n = int.from_bytes(self._recv_exact(2), "big")
            elif n == 127:
                n = int.from_bytes(self._recv_exact(8), "big")
            # framelengte begrenzen: een kwaadwillende peer op /agent/ws mag de
            # hub niet via een enorme lengte het geheugen laten uitputten
            if n > MAX_FRAME or len(msg) + n > MAX_FRAME:
                raise ConnectionError("frame te groot")
            mask = self._recv_exact(4) if masked else None
            payload = self._recv_exact(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x9:                      # ping → pong
                self.send_frame(0xA, payload)
                continue
            if opcode == 0xA:                      # pong → negeren
                continue
            if opcode == 0x8:
                raise ConnectionError("peer sloot de verbinding")
            msg += payload
            if fin:
                return json.loads(msg)

    def close(self):
        try:
            self.send_frame(0x8)
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


def ws_connect(url, verify_ssl=True, timeout=TIMEOUT, path=None, headers=None):
    """Uitgaande WebSocket-verbinding (client)."""
    u = urlparse(url)
    tls = u.scheme in ("https", "wss")
    port = u.port or (443 if tls else 80)
    sock = socket.create_connection((u.hostname, port), timeout=timeout)
    if tls:
        ctx = ssl.create_default_context()
        if not verify_ssl:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        sock = ctx.wrap_socket(sock, server_hostname=u.hostname)
    key = base64.b64encode(secrets.token_bytes(16)).decode()
    extra = "".join(f"{k}: {v}\r\n" for k, v in (headers or {}).items())
    sock.sendall((
        f"GET {path or u.path or '/'} HTTP/1.1\r\nHost: {u.hostname}:{port}\r\n"
        "Upgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n{extra}\r\n"
    ).encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("websocket-handshake afgebroken")
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    if b" 101" not in head.split(b"\r\n", 1)[0]:
        raise ConnectionError(f"websocket geweigerd: {head.split(b'\r\n', 1)[0].decode()}")
    io = FrameIO(sock, mask_out=True)
    io.buf = rest
    return io


# ---------- transport: direct (REST/WS) of via agent-tunnel ----------

def _rest(inst, name, path, method="GET", payload=None, raw=False, timeout=TIMEOUT):
    url = inst["url"].rstrip("/") + path
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {read_token(inst, name)}")
    req.add_header("Content-Type", "application/json")
    data = json.dumps(payload).encode() if payload is not None else None
    ctx = None
    if inst["url"].startswith("https") and not inst.get("verify_ssl", True):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, data=data, timeout=timeout, context=ctx) as r:
        body = r.read()
    if raw:
        return body.decode(errors="replace")
    return json.loads(body) if body else None


def _ws_cmd(inst, name, payload, timeout=60):
    """Willekeurig HA-WebSocket-commando → result of None."""
    io = None
    try:
        io = ws_connect(inst["url"], inst.get("verify_ssl", True),
                        timeout=timeout, path="/api/websocket")
        io.recv_json()
        io.send_json({"type": "auth", "access_token": read_token(inst, name)})
        if io.recv_json().get("type") != "auth_ok":
            return None
        io.send_json({"id": 1, **payload})
        while True:
            r = io.recv_json()
            if r.get("id") == 1:
                return r.get("result") if r.get("success") else None
    except Exception:
        return None
    finally:
        if io:
            io.close()


def _ws_supervisor(inst, name, endpoint, method="get", data=None, timeout=60):
    """Supervisor API via de HA-WebSocket (REST-proxy /api/hassio geeft 401)."""
    msg = {"type": "supervisor/api", "endpoint": endpoint,
           "method": method, "timeout": timeout}
    if data:
        msg["data"] = data
    return _ws_cmd(inst, name, msg, timeout)


def fetch_log_lines(inst, name):
    """Error-log als regels. /api/error_log bestaat niet meer op HA 2026.x."""
    if inst.get("agent_id"):
        r = agent_request(inst["agent_id"], "supervisor", "GET", "/core/logs", timeout=30)
        if r["status"] < 400:
            return r["body"].strip().splitlines()[-150:]
        raise RuntimeError(f"logs ophalen mislukt (HTTP {r['status']})")
    entries = _ws_cmd(inst, name, {"type": "system_log/list"}, timeout=20)
    if entries is None:
        raise RuntimeError("logs ophalen mislukt (system_log/list)")
    lines = []
    for e in sorted(entries, key=lambda e: e.get("timestamp", 0)):
        ts = datetime.fromtimestamp(e.get("timestamp", 0), tz=timezone.utc).strftime("%m-%d %H:%M:%S")
        msg = " | ".join(e.get("message") or [])
        cnt = f" (x{e['count']})" if e.get("count", 1) > 1 else ""
        lines.append(f"{ts} {e.get('level', '?'):<7} [{e.get('name', '')}] {msg}{cnt}")
    return lines[-150:]


class AgentConn:
    def __init__(self, agent_id, io):
        self.agent_id = agent_id
        self.io = io
        self.pending = {}
        self.meta = {}
        self.connected_at = datetime.now(timezone.utc).isoformat()

    def request(self, kind, method, path, body=None, timeout=30):
        rid = uuid.uuid4().hex
        ev = threading.Event()
        slot = {"event": ev, "resp": None}
        self.pending[rid] = slot
        try:
            self.io.send_json({"id": rid, "kind": kind, "method": method,
                               "path": path, "body": body, "timeout": timeout})
            if not ev.wait(timeout + 10):
                raise RuntimeError("agent antwoordde niet (timeout)")
            return slot["resp"]
        finally:
            self.pending.pop(rid, None)


def agent_request(agent_id, kind, method, path, body=None, timeout=30):
    conn = _agents.get(agent_id)
    if not conn:
        raise RuntimeError(f"agent '{agent_id}' is niet verbonden")
    return conn.request(kind, method, path, body, timeout)


def core_req(inst, name, path, method="GET", payload=None, raw=False, timeout=TIMEOUT):
    """Core REST API — direct of door de agent-tunnel."""
    if inst.get("agent_id"):
        r = agent_request(inst["agent_id"], "core", method, path, payload, timeout)
        if r["status"] >= 400:
            raise RuntimeError(f"HTTP {r['status']} van agent-instantie")
        return r["body"] if raw else (json.loads(r["body"]) if r["body"] else None)
    return _rest(inst, name, path, method, payload, raw, timeout)


def try_core(inst, name, path, **kw):
    try:
        return core_req(inst, name, path, **kw)
    except Exception:
        return None


def sup_req(inst, name, endpoint, method="get", data=None, timeout=60):
    """Supervisor API → dict of None. Agent praat direct met http://supervisor."""
    if inst.get("agent_id"):
        try:
            r = agent_request(inst["agent_id"], "supervisor", method.upper(),
                              endpoint, data, timeout)
            if r["status"] >= 400:
                return None
            return (json.loads(r["body"]) if r["body"] else {}).get("data")
        except Exception:
            return None
    return _ws_supervisor(inst, name, endpoint, method, data, timeout)


# ---------- health ----------

def collect_health(inst, name):
    aid = inst.get("agent_id")
    out = {"name": name, "note": inst.get("note", ""), "ssh": inst.get("ssh", ""),
           "online": False, "fetched_at": datetime.now(timezone.utc).isoformat(),
           "url": inst.get("url", f"agent: {aid}"),
           "kind": "agent" if aid else "direct"}
    if aid:
        conn = _agents.get(aid)
        out["agent_connected"] = bool(conn)
        if conn:
            out["agent_since"] = conn.connected_at
            if conn.meta.get("version"):
                out["agent_version"] = conn.meta["version"]
        else:
            out["error"] = "agent niet verbonden"
            return out
    else:
        try:
            read_token(inst, name)
        except RuntimeError:
            out["error"] = "geen token"
            return out

    info = try_core(inst, name, "/api/config", timeout=10)
    if not info:
        out["error"] = "onbereikbaar"
        return out
    out.update(online=True, location=info.get("location_name"),
               version=info.get("version"), state=info.get("state"))

    for key, ep in (("core", "/core/info"), ("os", "/os/info"), ("supervisor", "/supervisor/info")):
        d = sup_req(inst, name, ep, timeout=15)
        if d:
            out[key] = {"version": d.get("version"), "latest": d.get("version_latest"),
                        "update": bool(d.get("update_available"))}

    host = sup_req(inst, name, "/host/info", timeout=15)
    if host:
        free = host.get("disk_free")
        total = host.get("disk_total")
        try:
            free_gb = round(float(free), 1)
            total_gb = round(float(total), 1)
            used_pct = round(100 * (total_gb - free_gb) / total_gb, 1) if total_gb else None
            out["host"] = {"disk_free": free_gb, "disk_total": total_gb, "disk_pct": used_pct}
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    resolution = sup_req(inst, name, "/resolution/info", timeout=15)
    if resolution is not None:
        issues = []
        for u in resolution.get("unhealthy", []) or []:
            issues.append(f"unhealthy: {u}")
        for u in resolution.get("unsupported", []) or []:
            issues.append(f"unsupported: {u}")
        out["healthy"] = not resolution.get("unhealthy")
        out["issues"] = issues

    addons = (sup_req(inst, name, "/addons", timeout=20) or {}).get("addons")
    if addons is not None:
        out["addons"] = {
            "total": len(addons),
            "running": sum(1 for a in addons if a.get("state") == "started"),
            "updates": [{"name": a["name"], "slug": a["slug"],
                         "version": a.get("version"), "latest": a.get("version_latest")}
                        for a in addons if a.get("update_available")],
        }

    states = try_core(inst, name, "/api/states", timeout=25) or []
    if states:
        dead = [s["entity_id"] for s in states if s.get("state") in ("unavailable", "unknown")]
        by_dom = {}
        for e in dead:
            dom = e.split(".")[0]
            by_dom[dom] = by_dom.get(dom, 0) + 1
        out["entities"] = {"total": len(states), "dead": len(dead),
                           "pct": round(100 * len(dead) / len(states), 1),
                           "worst": sorted(by_dom.items(), key=lambda kv: -kv[1])[:4]}

    bk = (sup_req(inst, name, "/backups", timeout=20) or {}).get("backups")
    if bk is not None:
        newest = max(bk, key=lambda b: b.get("date", ""), default=None)
        out["backup"] = {"count": len(bk),
                         "newest_date": newest and newest.get("date"),
                         "newest_name": newest and newest.get("name")}
    return out


def refresh_instance(name):
    cfg = load_config()
    inst = cfg["instances"].get(name)
    if not inst:
        return None
    health = collect_health(inst, name)
    with _lock:
        _cache[name] = health
    return health


def send_notification(settings, message, title="Haven"):
    """Stuurt notify via de alert_instance-instantie. Fouten worden stil genegeerd."""
    try:
        cfg = load_config()
        inst = cfg["instances"].get(settings.get("alert_instance"))
        if not inst:
            return
        service = (settings.get("notify_service") or "").strip()
        if service.startswith("notify."):
            service = service[len("notify."):]
        if not service:
            return
        core_req(inst, settings["alert_instance"], f"/api/services/notify/{service}",
                "POST", {"title": title, "message": message}, timeout=15)
    except Exception:
        pass


def _issue_alert(state, settings, instance, atype, message):
    """Registreert een actieve alert; stuurt notificatie met 24h cooldown per (instance,type)."""
    key = f"{instance}|{atype}"
    now = datetime.now(timezone.utc)
    alert = {"instance": instance, "type": atype, "message": message, "since": now.isoformat()}
    with _lock:
        existing = _alerts.get(key)
        if existing:
            alert["since"] = existing["since"]      # sinds-tijd blijft staan zolang alert actief is
        _alerts[key] = alert
        state["active"][key] = alert

    last_sent = state.get("last_sent", {}).get(key)
    should_send = True
    if last_sent:
        try:
            should_send = (now - datetime.fromisoformat(last_sent)).total_seconds() > 86400
        except ValueError:
            should_send = True
    if should_send and settings.get("alerts_enabled") and settings.get("notify_service"):
        send_notification(settings, message)
        state.setdefault("last_sent", {})[key] = now.isoformat()


def _clear_alert(state, instance, atype):
    key = f"{instance}|{atype}"
    with _lock:
        _alerts.pop(key, None)
        state["active"].pop(key, None)


_offline_since = {}     # naam -> iso-tijdstip sinds wanneer offline (in-memory)


def run_alerts_engine(settings, state):
    thresholds = settings.get("thresholds", DEFAULT_SETTINGS["thresholds"])
    cfg = load_config()
    now = datetime.now(timezone.utc)
    for name in sorted(cfg["instances"]):
        with _lock:
            health = _cache.get(name)
        if not health:
            continue

        # offline langer dan offline_min minuten
        if not health.get("online"):
            with _lock:
                since = _offline_since.setdefault(name, now.isoformat())
            try:
                minutes = (now - datetime.fromisoformat(since)).total_seconds() / 60
            except ValueError:
                minutes = 0
            if minutes > thresholds.get("offline_min", 30):
                _issue_alert(state, settings, name, "offline",
                             f"{name} is al {int(minutes)} minuten offline")
            else:
                _clear_alert(state, name, "offline")
        else:
            with _lock:
                _offline_since.pop(name, None)
            _clear_alert(state, name, "offline")

        # backup ouder dan backup_max_days
        backup = health.get("backup") or {}
        newest = backup.get("newest_date")
        if newest:
            try:
                age_days = (now - datetime.fromisoformat(newest.replace("Z", "+00:00"))).days
                if age_days > thresholds.get("backup_max_days", 8):
                    _issue_alert(state, settings, name, "backup_stale",
                                 f"{name}: laatste backup is {age_days} dagen oud")
                else:
                    _clear_alert(state, name, "backup_stale")
            except ValueError:
                pass

        # core-update beschikbaar
        if (health.get("core") or {}).get("update"):
            _issue_alert(state, settings, name, "core_update",
                         f"{name}: core-update beschikbaar")
        else:
            _clear_alert(state, name, "core_update")

        # entities.pct > dead_pct
        pct = (health.get("entities") or {}).get("pct")
        if pct is not None and pct > thresholds.get("dead_pct", 15):
            _issue_alert(state, settings, name, "dead_entities",
                         f"{name}: {pct}% entiteiten unavailable/unknown")
        else:
            _clear_alert(state, name, "dead_entities")

        # host.disk_pct > 90
        disk_pct = (health.get("host") or {}).get("disk_pct")
        if disk_pct is not None and disk_pct > 90:
            _issue_alert(state, settings, name, "disk_full",
                         f"{name}: schijf {disk_pct}% vol")
        else:
            _clear_alert(state, name, "disk_full")

        # healthy == false
        if health.get("healthy") is False:
            issues = "; ".join(health.get("issues") or [])
            _issue_alert(state, settings, name, "unhealthy",
                         f"{name}: ongezond ({issues})" if issues else f"{name}: ongezond")
        else:
            _clear_alert(state, name, "unhealthy")

    save_alerts_state(state)


def run_backup_scheduler(settings, state):
    """Wekelijkse volledige backup op alle instanties, hooguit 1x per week."""
    day = settings.get("backup_day", -1)
    hour = settings.get("backup_hour", 3)
    if day is None or day < 0:
        return
    now = datetime.now(timezone.utc)
    # op de juiste dag, op/na het geplande uur (niet een exacte uur-match — dan
    # zou een gemiste poll of down-tijd de hele week overslaan); de wekelijkse
    # cooldown hieronder zorgt dat het maar 1x gebeurt
    if now.weekday() != day or now.hour < hour:
        return
    last = state.get("last_backup")
    if last:
        try:
            if (now - datetime.fromisoformat(last)).total_seconds() < 6 * 86400:
                return
        except ValueError:
            pass
    cfg = load_config()
    for name, inst in sorted(cfg["instances"].items()):
        try:
            do_action(name, inst, "backup", {})
        except Exception:
            pass
    state["last_backup"] = now.isoformat()
    save_alerts_state(state)


def poller():
    # parallel per instantie: één trage/onbereikbare instantie houdt de
    # ronde (en daarmee de alerts van de rest) niet meer op
    pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="poll")
    def safe_refresh(name):
        try:
            refresh_instance(name)
        except Exception:
            pass
    while True:
        list(pool.map(safe_refresh, sorted(load_config()["instances"])))
        try:
            settings = load_settings()
            state = load_alerts_state()
            state.setdefault("active", {})
            state.setdefault("last_sent", {})
            run_alerts_engine(settings, state)
            run_backup_scheduler(settings, state)
        except Exception:
            pass
        time.sleep(POLL_INTERVAL)


# ---------- jobs ----------

def start_job(label, fn):
    jid = uuid.uuid4().hex[:12]
    with _lock:
        # oude afgeronde jobs opruimen zodat de dict niet onbegrensd groeit
        if len(_jobs) > 100:
            done = sorted((j for j in _jobs.values() if j["status"] != "running"),
                          key=lambda j: j["started"])
            for j in done[:len(_jobs) - 100]:
                _jobs.pop(j["id"], None)
        _jobs[jid] = {"id": jid, "label": label, "status": "running",
                      "started": datetime.now(timezone.utc).isoformat()}

    def run():
        try:
            result = fn()
            with _lock:
                _jobs[jid].update(status="done", result=result)
        except Exception as e:
            with _lock:
                _jobs[jid].update(status="error", error=str(e))

    threading.Thread(target=run, daemon=True).start()
    return jid


def do_action(name, inst, action, body):
    if action == "backup":
        def run():
            label = f"haven {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            try:
                res = sup_req(inst, name, "/backups/new/full", "post", {"name": label}, timeout=3600)
                if res is None:
                    raise RuntimeError("backup mislukt (geen supervisor of timeout)")
                refresh_instance(name)
                append_audit("backup", name, label, ok=True)
                return {"slug": res.get("slug")}
            except Exception as e:
                append_audit("backup", name, str(e), ok=False)
                raise
        return start_job(f"Backup {name}", run)

    if action == "core_update":
        def run():
            try:
                if sup_req(inst, name, "/core/update", "post", timeout=3600) is None:
                    raise RuntimeError("update mislukt of geen supervisor")
                time.sleep(20)
                refresh_instance(name)
                append_audit("core_update", name, "", ok=True)
                return {}
            except Exception as e:
                append_audit("core_update", name, str(e), ok=False)
                raise
        return start_job(f"Core-update {name}", run)

    if action == "addon_update":
        slug = body.get("slug") or ""
        if not slug.replace("_", "").replace("-", "").isalnum():
            raise RuntimeError("ongeldige add-on slug")
        def run():
            try:
                if sup_req(inst, name, f"/addons/{slug}/update", "post", timeout=1800) is None:
                    raise RuntimeError(f"update van {slug} mislukt")
                refresh_instance(name)
                append_audit("addon_update", name, slug, ok=True)
                return {}
            except Exception as e:
                append_audit("addon_update", name, f"{slug}: {e}", ok=False)
                raise
        return start_job(f"Add-on update {slug} op {name}", run)

    if action == "restart":
        def run():
            try:
                core_req(inst, name, "/api/services/homeassistant/restart", "POST", {})
                time.sleep(30)
                refresh_instance(name)
                append_audit("restart", name, "", ok=True)
                return {}
            except Exception as e:
                append_audit("restart", name, str(e), ok=False)
                raise
        return start_job(f"Herstart {name}", run)

    if action == "reload_entry":
        entry_id = body.get("entry_id") or ""
        # entry_ids zijn alfanumeriek (ULID/hex) — voorkomt path-traversal in het Core-pad
        if not entry_id or not entry_id.isalnum():
            raise RuntimeError("ongeldig entry_id")
        def run():
            try:
                core_req(inst, name, f"/api/config/config_entries/entry/{entry_id}/reload",
                        "POST", {})
                refresh_instance(name)
                append_audit("reload_entry", name, entry_id, ok=True)
                return {}
            except Exception as e:
                append_audit("reload_entry", name, f"{entry_id}: {e}", ok=False)
                raise
        return start_job(f"Herlaad entry {entry_id} op {name}", run)

    raise RuntimeError(f"onbekende actie '{action}'")


# ---------- HTTP handler ----------

def json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False).encode()


def key_hash(key):
    return hashlib.sha256(key.encode()).hexdigest()


class Handler(BaseHTTPRequestHandler):
    server_version = f"haven-hub/{VERSION}"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _allowed(self):
        return self.client_address[0] in ALLOWED_CLIENTS

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json_bytes(obj))

    def _err(self, msg, code=400):
        self._json({"error": msg}, code)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n)) if n else {}

    def _route(self):
        p = urlparse(self.path)
        return [seg for seg in p.path.split("/") if seg], parse_qs(p.query)

    # -- agent-tunnel (publiek pad; eigen auth, geen IP-check) --
    def _agent_ws(self):
        aid = self.headers.get("X-Agent-Id", "")
        key = self.headers.get("X-Agent-Key", "")
        if not check_auth_rate_limit(aid):
            return self._err("te veel mislukte auth-pogingen, probeer later opnieuw", 429)
        rec = load_agents().get(aid)
        if not rec or not hmac.compare_digest(key_hash(key), rec["key_hash"]):
            record_auth_failure(aid)
            append_audit("auth_failed", aid, f"vanaf {self.client_address[0]}", ok=False)
            return self._err("verboden", 403)
        if self.headers.get("Upgrade", "").lower() != "websocket":
            return self._err("websocket vereist", 400)
        wskey = self.headers.get("Sec-WebSocket-Key", "")
        accept = base64.b64encode(
            hashlib.sha1((wskey + WS_GUID).encode()).digest()).decode()
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.close_connection = True

        self.connection.settimeout(90)             # agent pingt elke 30s
        io = FrameIO(self.connection, mask_out=False)
        conn = AgentConn(aid, io)
        with _lock:                                 # swap onder lock (snelle reconnects)
            old = _agents.get(aid)
            _agents[aid] = conn
        if old:
            old.io.close()                          # oude sluiten NA het vrijgeven van de lock
        print(f"agent '{aid}' verbonden vanaf {self.client_address[0]}", flush=True)
        append_audit("agent_connected", aid, f"vanaf {self.client_address[0]}", ok=True)
        try:
            while True:
                msg = io.recv_json()
                if msg.get("type") == "hello":
                    conn.meta = msg.get("meta", {})
                    continue
                slot = conn.pending.get(msg.get("id"))
                if slot:
                    slot["resp"] = msg
                    slot["event"].set()
        except Exception:
            pass
        finally:
            with _lock:
                if _agents.get(aid) is conn:   # niet de nieuwe verbinding wegvegen
                    del _agents[aid]
            print(f"agent '{aid}' verbroken", flush=True)
            append_audit("agent_disconnected", aid, "", ok=True)

    # -- GET --
    def do_GET(self):
        parts, q = self._route()
        if parts[:2] == ["agent", "ws"]:
            return self._agent_ws()
        if parts == ["healthz"]:
            # watchdog/monitoring: bewust zonder IP-check, lekt niets
            return self._json({"ok": True, "version": VERSION})
        if not self._allowed():
            return self._err("verboden", 403)
        try:
            if not parts or parts[0] != "api":
                return self._static(parts)
            if parts[1] == "instances" and len(parts) == 2:
                cfg = load_config()
                with _lock:
                    items = []
                    for n in sorted(cfg["instances"]):
                        inst = cfg["instances"][n]
                        h = dict(_cache.get(n) or {
                            "name": n, "note": inst.get("note", ""), "online": False,
                            "url": inst.get("url", f"agent: {inst.get('agent_id')}"),
                            "kind": "agent" if inst.get("agent_id") else "direct",
                            "error": "nog niet opgehaald"})
                        if inst.get("agent_id"):
                            h["agent_connected"] = inst["agent_id"] in _agents
                            # cache zegt offline maar agent hangt er weer aan → ververs op de achtergrond
                            if h["agent_connected"] and not h.get("online"):
                                threading.Thread(target=refresh_instance, args=(n,), daemon=True).start()
                        items.append(h)
                return self._json({"instances": items})
            if parts[1] == "instances" and len(parts) >= 4:
                name = parts[2]
                cfg = load_config()
                inst = cfg["instances"].get(name)
                if not inst:
                    return self._err("onbekende instantie", 404)
                sub = parts[3]
                if sub == "health":
                    return self._json(refresh_instance(name))
                if sub == "logs":
                    return self._json({"lines": fetch_log_lines(inst, name)})
                if sub == "backups":
                    res = sup_req(inst, name, "/backups", timeout=20)
                    return self._json({"backups": (res or {}).get("backups", [])})
                if sub == "addons":
                    res = sup_req(inst, name, "/addons", timeout=20)
                    return self._json({"addons": (res or {}).get("addons", [])})
                if sub == "states":
                    flt = (q.get("filter") or [""])[0].lower()
                    states = core_req(inst, name, "/api/states", timeout=25)
                    rows = [{"entity_id": s["entity_id"], "state": s["state"],
                             "name": s.get("attributes", {}).get("friendly_name", "")}
                            for s in states
                            if not flt or flt in s["entity_id"].lower()
                            or flt in s.get("attributes", {}).get("friendly_name", "").lower()]
                    rows.sort(key=lambda r: r["entity_id"])
                    return self._json({"total": len(rows), "states": rows[:400]})
                if sub == "entries":
                    entries = core_req(inst, name, "/api/config/config_entries/entry", timeout=20) or []
                    rows = [{"entry_id": e.get("entry_id"), "title": e.get("title"),
                             "domain": e.get("domain"), "state": e.get("state")}
                            for e in entries]
                    rows.sort(key=lambda r: (r["domain"] or "", r["title"] or ""))
                    return self._json({"entries": rows})
                return self._err("onbekend pad", 404)
            if parts[1] == "overview" and len(parts) == 2:
                cfg = load_config()
                with _lock:
                    cached = [_cache.get(n) for n in cfg["instances"]]
                total = len(cfg["instances"])
                online = sum(1 for h in cached if h and h.get("online"))
                updates = 0
                for h in cached:
                    if not h:
                        continue
                    if (h.get("core") or {}).get("update"):
                        updates += 1
                    updates += len((h.get("addons") or {}).get("updates", []))
                with _lock:
                    alert_count = len(_alerts)
                return self._json({"total": total, "online": online,
                                    "updates": updates, "alerts": alert_count})
            if parts[1] == "alerts" and len(parts) == 2:
                with _lock:
                    items = sorted(_alerts.values(), key=lambda a: a.get("since", ""), reverse=True)
                return self._json({"alerts": items})
            if parts[1] == "audit" and len(parts) == 2:
                return self._json({"events": load_audit(200)})
            if parts[1] == "settings" and len(parts) == 2:
                return self._json(load_settings())
            if parts[1] == "agents" and len(parts) == 2:
                agents = load_agents()
                return self._json({"agents": [
                    {"id": aid, "note": rec.get("note", ""), "created": rec.get("created"),
                     "connected": aid in _agents,
                     "since": _agents[aid].connected_at if aid in _agents else None,
                     "meta": _agents[aid].meta if aid in _agents else {}}
                    for aid, rec in sorted(agents.items())]})
            if parts[1] == "jobs":
                with _lock:
                    if len(parts) == 3:
                        job = _jobs.get(parts[2])
                        return self._json(job) if job else self._err("onbekende job", 404)
                    return self._json({"jobs": sorted(_jobs.values(), key=lambda j: j["started"],
                                                      reverse=True)[:20]})
            return self._err("onbekend pad", 404)
        except RuntimeError as e:
            return self._err(str(e))
        except urllib.error.HTTPError as e:
            return self._err(f"HTTP {e.code} van instantie", 502)
        except Exception as e:
            return self._err(f"{type(e).__name__}: {e}", 500)

    # -- POST --
    def do_POST(self):
        if not self._allowed():
            return self._err("verboden", 403)
        parts, _ = self._route()
        try:
            body = self._body()
            if parts[:2] == ["api", "instances"] and len(parts) == 2:
                return self._add_direct(body)
            if parts[:2] == ["api", "agents"] and len(parts) == 2:
                return self._add_agent(body)
            if parts[:2] == ["api", "instances"] and len(parts) == 4 and parts[3] == "action":
                name = parts[2]
                cfg = load_config()
                inst = cfg["instances"].get(name)
                if not inst:
                    return self._err("onbekende instantie", 404)
                jid = do_action(name, inst, body.get("type"), body)
                return self._json({"job": jid}, 202)
            if parts[:2] == ["api", "settings"] and len(parts) == 2:
                return self._save_settings(body)
            if parts[:2] == ["api", "agents"] and len(parts) == 4 and parts[3] == "rotate":
                return self._rotate_agent(parts[2])
            return self._err("onbekend pad", 404)
        except RuntimeError as e:
            return self._err(str(e))
        except Exception as e:
            return self._err(f"{type(e).__name__}: {e}", 500)

    def _add_direct(self, body):
        name = (body.get("name") or "").strip().lower()
        url = (body.get("url") or "").strip().rstrip("/")
        token = (body.get("token") or "").strip()
        if not name.replace("-", "").replace("_", "").isalnum():
            return self._err("naam: alleen letters/cijfers/-/_")
        if not url.startswith(("http://", "https://")):
            return self._err("url moet met http(s):// beginnen")
        if not token:
            return self._err("token is verplicht")
        cfg = load_config()
        inst = {"url": url}
        if body.get("insecure"):
            inst["verify_ssl"] = False
        if body.get("note"):
            inst["note"] = body["note"]
        if body.get("ssh"):
            inst["ssh"] = body["ssh"]
        write_token(name, token)
        probe = try_core(inst, name, "/api/config", timeout=10)
        if not probe and not body.get("force"):
            return self._err("instantie niet bereikbaar met dit token — "
                             "controleer url/token (of stuur force)")
        cfg["instances"][name] = inst
        save_config(cfg)
        append_audit("instance_added", name, "direct", ok=True)
        return self._json(refresh_instance(name), 201)

    def _add_agent(self, body):
        name = (body.get("name") or "").strip().lower()
        if not name.replace("-", "").replace("_", "").isalnum():
            return self._err("naam: alleen letters/cijfers/-/_")
        cfg = load_config()
        agents = load_agents()
        if name in cfg["instances"] or name in agents:
            return self._err(f"'{name}' bestaat al")
        key = secrets.token_urlsafe(24)
        agents[name] = {"key_hash": key_hash(key), "note": body.get("note", ""),
                        "created": datetime.now(timezone.utc).isoformat()}
        save_agents(agents)
        inst = {"agent_id": name}
        if body.get("note"):
            inst["note"] = body["note"]
        cfg["instances"][name] = inst
        save_config(cfg)
        append_audit("instance_added", name, "agent", ok=True)
        return self._json({
            "agent_id": name, "key": key, "hub_url": PUBLIC_HUB_URL,
            "addon_options": {"hub_url": PUBLIC_HUB_URL, "agent_id": name, "agent_key": key},
        }, 201)

    def _save_settings(self, body):
        settings = json.loads(json.dumps(DEFAULT_SETTINGS))    # deep copy
        for k in ("alerts_enabled", "notify_service", "alert_instance", "backup_day", "backup_hour"):
            if k in body:
                settings[k] = body[k]
        if "thresholds" in body and isinstance(body["thresholds"], dict):
            settings["thresholds"] = {**DEFAULT_SETTINGS["thresholds"], **body["thresholds"]}
        save_settings(settings)
        return self._json({"ok": True})

    def _rotate_agent(self, agent_id):
        agents = load_agents()
        if agent_id not in agents:
            return self._err("onbekende agent", 404)
        key = secrets.token_urlsafe(24)
        agents[agent_id]["key_hash"] = key_hash(key)
        save_agents(agents)
        conn = _agents.get(agent_id)
        if conn:
            conn.io.close()
        append_audit("key_rotated", agent_id, "", ok=True)
        return self._json({
            "agent_id": agent_id, "key": key, "hub_url": PUBLIC_HUB_URL,
            "addon_options": {"hub_url": PUBLIC_HUB_URL, "agent_id": agent_id, "agent_key": key},
        })

    # -- DELETE --
    def do_DELETE(self):
        if not self._allowed():
            return self._err("verboden", 403)
        parts, _ = self._route()
        if parts[:2] == ["api", "instances"] and len(parts) == 3:
            name = parts[2]
            cfg = load_config()
            if name not in cfg["instances"]:
                return self._err("onbekende instantie", 404)
            inst = cfg["instances"].pop(name)
            save_config(cfg)
            aid = inst.get("agent_id")
            if aid:
                agents = load_agents()
                agents.pop(aid, None)
                save_agents(agents)
                conn = _agents.get(aid)
                if conn:
                    conn.io.close()
            tok = TOKENS_DIR / name
            if tok.exists():
                tok.unlink()
            with _lock:
                _cache.pop(name, None)
            append_audit("instance_removed", name, "", ok=True)
            return self._json({"ok": True})
        return self._err("onbekend pad", 404)

    # -- static --
    def _static(self, parts):
        fname = parts[-1] if parts else "index.html"
        if fname not in ("index.html", "app.js", "style.css"):
            fname = "index.html"                   # SPA-fallback (ingress-subpaden)
        path = STATIC_DIR / fname
        if not path.exists():
            return self._err("niet gevonden", 404)
        ctype = {"html": "text/html; charset=utf-8", "js": "text/javascript; charset=utf-8",
                 "css": "text/css; charset=utf-8"}[fname.rsplit(".", 1)[1]]
        self._send(200, path.read_bytes(), ctype)


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _lock:
        _alerts.update(load_alerts_state().get("active", {}))
    threading.Thread(target=poller, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.daemon_threads = True
    print(f"haven-hub luistert op :{PORT} — data in {DATA_DIR}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
