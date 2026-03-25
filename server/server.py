#!/usr/bin/env python3
"""
Chore Tracker — Sync Server (REST + WebSocket)

A lightweight data-sync server that enables multiple devices to share the same
chore-tracker state.  Serves three purposes:

  1. REST API   — GET /data  and  PUT /data  for reading/writing the full JSON blob
  2. WebSocket  — real-time push notifications when data changes (port+1)
  3. Static     — serves index.html, manifest, SW, fonts, and icons so clients
                  only need to visit http://<host>:<port>

Dependencies:  pip install websockets  (or: apt install python3-websockets)

Usage:
  python3 server.py [--port PORT] [--data PATH] [--static DIR]

Options:
  --port PORT    Port for REST API & static files (default: 8780).
                 WebSocket listens on PORT+1 (default: 8781).
  --data PATH    Path to the JSON data file (default: chore-data.json).
  --static DIR   Directory to serve static files from.
                 Default: auto-detected parent of server/ (where index.html lives).
                 Set to empty string ('') to disable static serving.

Examples:
  python3 server/server.py                        # defaults: port 8780, auto-detect static root
  python3 server/server.py --port 80              # serve on port 80 (needs root), WS on 81
  python3 server/server.py --data /tmp/data.json  # custom data file location
  python3 server/server.py --static ''            # disable static file serving
"""

import argparse
import asyncio
import json
import logging
import mimetypes
import os
import subprocess
import tempfile
import time
from http import HTTPStatus
from pathlib import Path
from urllib.parse import unquote, urlparse

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)
log = logging.getLogger("chore-server")

# Suppress noisy websockets library logs (e.g. "server listening on ...")
logging.getLogger("websockets").setLevel(logging.WARNING)

try:
    import websockets
    try:
        # websockets >= 13.0 (new asyncio API)
        from websockets.asyncio.server import serve as ws_serve
    except (ImportError, AttributeError):
        # websockets 10.x–12.x (legacy API)
        ws_serve = websockets.serve  # type: ignore[attr-defined]
except ImportError:
    print("Missing dependency. Install it with:\n  pip install websockets\nor:\n  apt install python3-websockets")
    raise SystemExit(1)

# ── Defaults ──────────────────────────────────────────────────
DEFAULT_PORT = 8780
DEFAULT_DATA_FILE = "chore-data.json"


def _get_git_version() -> str:
    """Return short git hash, or '' if not in a repo."""
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root, capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


_git_version = _get_git_version()

# ── State ─────────────────────────────────────────────────────
CLIENTS: set = set()
data_file: str = DEFAULT_DATA_FILE
static_root: str = ""  # set in main(); parent of server/ directory

# ── MIME helpers ──────────────────────────────────────────────
MIME_OVERRIDES = {
    ".html": "text/html",
    ".js":   "application/javascript",
    ".json": "application/json",
    ".css":  "text/css",
    ".woff2": "font/woff2",
    ".png":  "image/png",
    ".svg":  "image/svg+xml",
    ".ico":  "image/x-icon",
    ".webmanifest": "application/manifest+json",
}


def guess_mime(filepath: str) -> str:
    ext = os.path.splitext(filepath)[1].lower()
    if ext in MIME_OVERRIDES:
        return MIME_OVERRIDES[ext]
    mt, _ = mimetypes.guess_type(filepath)
    return mt or "application/octet-stream"


# ── Persistence helpers ───────────────────────────────────────
MAX_CHANGELOG_ENTRIES = 200
changelog_file: str = ""  # set in __main__ based on data_file


def _ensure_changelog_file():
    """Derive changelog_file from data_file if not yet set."""
    global changelog_file
    if not changelog_file:
        base, ext = os.path.splitext(data_file)
        changelog_file = f"{base}-changelog{ext}"


def read_data() -> str:
    """Return the raw JSON string from disk (or '{}' if missing)."""
    try:
        with open(data_file, "r") as f:
            return f.read()
    except FileNotFoundError:
        return "{}"
    except OSError:
        log.exception("Failed to read data file %s", data_file)
        return "{}"


def read_data_obj() -> dict:
    """Return the parsed JSON object from disk."""
    raw = read_data()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _read_changelog() -> list:
    _ensure_changelog_file()
    if os.path.exists(changelog_file):
        try:
            with open(changelog_file, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _write_changelog(entries: list):
    _ensure_changelog_file()
    with open(changelog_file, "w") as f:
        json.dump(entries[-MAX_CHANGELOG_ENTRIES:], f)


def _compute_entries_diff(old_entries: dict, new_entries: dict) -> list:
    """Compute a list of changes between two entries dicts.

    Returns a list of {date, room, task, action, user} dicts describing
    what changed (added, removed, or changed user).
    """
    changes = []
    all_dates = set(list(old_entries.keys()) + list(new_entries.keys()))
    for date in sorted(all_dates):
        old_rooms = old_entries.get(date, {})
        new_rooms = new_entries.get(date, {})
        all_rooms = set(list(old_rooms.keys()) + list(new_rooms.keys()))
        for room_id in all_rooms:
            old_tasks = old_rooms.get(room_id, {}).get("tasks", {})
            new_tasks = new_rooms.get(room_id, {}).get("tasks", {})
            all_tasks = set(list(old_tasks.keys()) + list(new_tasks.keys()))
            for task_id in all_tasks:
                old_t = old_tasks.get(task_id)
                new_t = new_tasks.get(task_id)
                if old_t is None and new_t is not None:
                    changes.append({
                        "date": date, "room": room_id, "task": task_id,
                        "type": "checked", "user": new_t.get("user", "?"),
                    })
                elif old_t is not None and new_t is None:
                    changes.append({
                        "date": date, "room": room_id, "task": task_id,
                        "type": "unchecked", "user": old_t.get("user", "?"),
                    })
                elif old_t and new_t:
                    if old_t.get("cleaned") != new_t.get("cleaned"):
                        act = "checked" if new_t.get("cleaned") else "unchecked"
                        changes.append({
                            "date": date, "room": room_id, "task": task_id,
                            "type": act, "user": new_t.get("user", "?"),
                        })
                    elif old_t.get("user") != new_t.get("user"):
                        changes.append({
                            "date": date, "room": room_id, "task": task_id,
                            "type": "reassigned",
                            "from_user": old_t.get("user", "?"),
                            "user": new_t.get("user", "?"),
                        })
    return changes


def _compute_structure_diff(old_obj: dict, new_obj: dict) -> list:
    """Detect room/task/user/settings additions and removals."""
    changes = []
    old_rooms = {r.get("id", r.get("name", "?")): r.get("name", "?") for r in old_obj.get("rooms", [])}
    new_rooms = {r.get("id", r.get("name", "?")): r.get("name", "?") for r in new_obj.get("rooms", [])}
    for rid in set(new_rooms) - set(old_rooms):
        changes.append({"type": "room_added", "name": new_rooms[rid]})
    for rid in set(old_rooms) - set(new_rooms):
        changes.append({"type": "room_removed", "name": old_rooms[rid]})

    old_users = {(u["id"] if isinstance(u, dict) else u) for u in old_obj.get("users", [])}
    new_users = {(u["id"] if isinstance(u, dict) else u) for u in new_obj.get("users", [])}
    _user_name = lambda uid: next((u.get("name", uid) if isinstance(u, dict) else u
                                   for u in new_obj.get("users", []) + old_obj.get("users", [])
                                   if (u.get("id") if isinstance(u, dict) else u) == uid), uid)
    for u in new_users - old_users:
        changes.append({"type": "user_added", "name": _user_name(u)})
    for u in old_users - new_users:
        changes.append({"type": "user_removed", "name": _user_name(u)})
    return changes


class VersionConflict(Exception):
    """Raised when a client's base_version doesn't match the server's current version."""
    def __init__(self, server_version: int, server_data: dict):
        self.server_version = server_version
        self.server_data = server_data


def write_data(body: str, client_id: str = "", client_label: str = "",
               base_version: int = None) -> int:
    """Atomically write JSON to disk, stamping a _version field. Returns the new version.

    If base_version is provided and doesn't match the current server version,
    raises VersionConflict so the caller can return 409.
    """
    try:
        obj = json.loads(body)
    except json.JSONDecodeError:
        raise ValueError("Invalid JSON")

    current = read_data_obj()
    current_version = current.get("_version", 0)

    # Version guard: reject stale writes
    if base_version is not None and current_version != 0 and base_version != current_version:
        raise VersionConflict(current_version, current)

    # Compute diff for changelog
    entry_changes = _compute_entries_diff(
        current.get("entries", {}), obj.get("entries", {})
    )
    struct_changes = _compute_structure_diff(current, obj)

    obj["_version"] = int(time.time() * 1000)
    # Write to a temp file then atomically rename to avoid corruption
    dir_name = os.path.dirname(os.path.abspath(data_file)) or "."
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f)
        os.replace(tmp_path, data_file)
    except BaseException:
        # Clean up the temp file if anything goes wrong
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    # Record changelog entry (only if something actually changed)
    if entry_changes or struct_changes:
        cl = _read_changelog()
        cl.append({
            "ts": obj["_version"],
            "client_id": client_id,
            "client_label": client_label,
            "base_version": base_version or current_version,
            "new_version": obj["_version"],
            "entry_changes": entry_changes,
            "struct_changes": struct_changes,
        })
        _write_changelog(cl)

    return obj["_version"]


# ── Broadcast ─────────────────────────────────────────────────
async def broadcast(version: int, sender=None):
    """Notify every connected WS client (except the sender) that data changed."""
    msg = json.dumps({"type": "data-changed", "version": version})
    for ws in list(CLIENTS):
        if ws is sender:
            continue
        try:
            await ws.send(msg)
        except Exception:
            CLIENTS.discard(ws)
            log.info("Removed stale WebSocket client during broadcast")


# ── HTTP Server (REST API) ────────────────────────────────────
CORS_HEADERS = (
    ("Access-Control-Allow-Origin", "*"),
    ("Access-Control-Allow-Methods", "GET, PUT, POST, DELETE, OPTIONS"),
    ("Access-Control-Allow-Headers", "Content-Type, X-Client-Id, X-Client-Label, X-Base-Version"),
)


class RESTProtocol(asyncio.Protocol):
    """Minimal HTTP/1.1 protocol handler for the REST API."""

    # Maximum request size (headers + body): 1 MB
    MAX_REQUEST_SIZE = 1 * 1024 * 1024

    def connection_made(self, transport):
        self.transport = transport
        self._buf = b""

    def data_received(self, data):
        self._buf += data

        # Guard against excessively large requests
        if len(self._buf) > self.MAX_REQUEST_SIZE:
            log.warning("Request too large (%d bytes), dropping connection", len(self._buf))
            self._respond(413, "Request too large\n")
            return

        # Wait until we have full headers
        if b"\r\n\r\n" not in self._buf:
            return
        header_end = self._buf.index(b"\r\n\r\n")
        header_block = self._buf[:header_end].decode("utf-8", errors="replace")
        body_start = header_end + 4

        lines = header_block.split("\r\n")
        request_line = lines[0]
        parts = request_line.split(" ", 2)
        if len(parts) < 2:
            self._respond(400, "Bad request\n")
            return
        method, path = parts[0], parts[1]

        # Parse headers
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()

        try:
            content_length = int(headers.get("content-length", 0))
        except ValueError:
            self._respond(400, "Invalid Content-Length\n")
            return

        body = self._buf[body_start : body_start + content_length]

        # Wait for full body
        if len(body) < content_length:
            return

        # Route — schedule and log any unexpected errors
        task = asyncio.ensure_future(self._handle(method, path, body, headers))
        task.add_done_callback(self._handle_task_error)

    @staticmethod
    def _handle_task_error(task):
        """Log unhandled exceptions from request handler tasks."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.exception("Unhandled error in request handler", exc_info=exc)

    async def _handle(self, method, path, body, headers):
        if method == "OPTIONS":
            self._respond(204, "", extra_headers=CORS_HEADERS)
            return

        if path == "/version":
            if method == "GET":
                ver = _git_version or "unknown"
                body_str = json.dumps({"version": ver})
                self._respond(200, body_str, content_type="application/json", extra_headers=CORS_HEADERS)
                return

        if path == "/data":
            if method == "GET":
                data = read_data()
                self._respond(200, data, content_type="application/json", extra_headers=CORS_HEADERS)
                return
            if method == "PUT":
                client_id = headers.get("x-client-id", "")
                client_label = headers.get("x-client-label", "")
                base_version_str = headers.get("x-base-version", "")
                base_version = int(base_version_str) if base_version_str else None
                try:
                    version = write_data(
                        body.decode("utf-8"),
                        client_id=client_id,
                        client_label=client_label,
                        base_version=base_version,
                    )
                except VersionConflict as exc:
                    resp_body = json.dumps({
                        "error": "version_conflict",
                        "server_version": exc.server_version,
                        "server_data": exc.server_data,
                    })
                    self._respond(409, resp_body, content_type="application/json", extra_headers=CORS_HEADERS)
                    return
                except ValueError as exc:
                    self._respond(400, str(exc), extra_headers=CORS_HEADERS)
                    return
                await broadcast(version)
                resp_body = json.dumps({"version": version})
                self._respond(200, resp_body, content_type="application/json", extra_headers=CORS_HEADERS)
                return

        # ── Changelog endpoints ─────────────────────────────────
        if path == "/changelog":
            if method == "GET":
                cl = _read_changelog()
                self._respond(200, json.dumps(cl), content_type="application/json", extra_headers=CORS_HEADERS)
                return

        if path == "/changelog/rollback" and method == "POST":
            try:
                req = json.loads(body.decode("utf-8"))
                target_ts = int(req["ts"])
                client_id = headers.get("x-client-id", "")
                client_label = headers.get("x-client-label", "")
            except (json.JSONDecodeError, KeyError, ValueError):
                self._respond(400, "Invalid rollback request", extra_headers=CORS_HEADERS)
                return
            cl = _read_changelog()
            target_entry = None
            for entry in cl:
                if entry["ts"] == target_ts:
                    target_entry = entry
                    break
            if not target_entry:
                self._respond(404, "Changelog entry not found", extra_headers=CORS_HEADERS)
                return
            current = read_data_obj()
            entries = current.get("entries", {})
            for ch in target_entry.get("entry_changes", []):
                date, room, task = ch["date"], ch["room"], ch["task"]
                if ch["type"] == "checked":
                    if date in entries and room in entries[date]:
                        tasks = entries[date][room].get("tasks", {})
                        tasks.pop(task, None)
                        if not tasks:
                            del entries[date][room]
                        if not entries[date]:
                            del entries[date]
                elif ch["type"] == "unchecked":
                    entries.setdefault(date, {}).setdefault(room, {"tasks": {}})
                    entries[date][room]["tasks"][task] = {"cleaned": True, "user": ch.get("user", "?")}
            current["entries"] = entries
            version = write_data(
                json.dumps(current),
                client_id=client_id,
                client_label=f"rollback by {client_label or client_id}",
            )
            await broadcast(version)
            self._respond(200, json.dumps({"version": version}), content_type="application/json", extra_headers=CORS_HEADERS)
            return

        if path.startswith("/changelog/") and method == "DELETE":
            ts_str = path.split("/")[-1]
            try:
                target_ts = int(ts_str)
            except ValueError:
                self._respond(400, "Invalid timestamp", extra_headers=CORS_HEADERS)
                return
            cl = _read_changelog()
            new_cl = [e for e in cl if e["ts"] != target_ts]
            if len(new_cl) == len(cl):
                self._respond(404, "Changelog entry not found", extra_headers=CORS_HEADERS)
                return
            _write_changelog(new_cl)
            self._respond(200, json.dumps({"ok": True}), content_type="application/json", extra_headers=CORS_HEADERS)
            return

        # ── Static file fallback ────────────────────────────────
        if method == "GET" and static_root:
            # Map "/" → "index.html"
            rel = path.lstrip("/") or "index.html"
            rel = unquote(rel)
            # Resolve and ensure it stays inside static_root
            requested = Path(static_root, rel).resolve()
            root = Path(static_root).resolve()
            if str(requested).startswith(str(root)) and requested.is_file():
                try:
                    content = requested.read_bytes()
                    ct = guess_mime(str(requested))
                    self._respond(200, content, content_type=ct, extra_headers=CORS_HEADERS)
                    return
                except OSError:
                    pass

        self._respond(404, "Not found\n", extra_headers=CORS_HEADERS)

    def _respond(self, status, body, content_type="text/plain", extra_headers=()):
        if isinstance(body, str):
            body = body.encode("utf-8")
        reason = {200: "OK", 204: "No Content", 400: "Bad Request", 404: "Not Found", 409: "Conflict", 413: "Payload Too Large"}.get(status, "OK")
        lines = [f"HTTP/1.1 {status} {reason}"]
        lines.append(f"Content-Type: {content_type}")
        lines.append(f"Content-Length: {len(body)}")
        for k, v in extra_headers:
            lines.append(f"{k}: {v}")
        lines.append("Connection: close")
        lines.append("")
        lines.append("")
        try:
            self.transport.write("\r\n".join(lines).encode("utf-8") + body)
            self.transport.close()
        except Exception:
            log.debug("Client disconnected before response could be sent")


# ── WebSocket handler ─────────────────────────────────────────
async def ws_handler(websocket, path=None):
    """Handle a single WebSocket connection."""
    CLIENTS.add(websocket)
    try:
        async for message in websocket:
            try:
                msg = json.loads(message)
                if msg.get("action") == "put" and "data" in msg:
                    client_id = msg.get("client_id", "")
                    client_label = msg.get("client_label", "")
                    base_version = msg.get("base_version")
                    if base_version is not None:
                        base_version = int(base_version)
                    try:
                        version = write_data(
                            json.dumps(msg["data"]),
                            client_id=client_id,
                            client_label=client_label,
                            base_version=base_version,
                        )
                    except VersionConflict as exc:
                        await websocket.send(json.dumps({
                            "type": "version_conflict",
                            "server_version": exc.server_version,
                            "server_data": exc.server_data,
                        }))
                        continue
                    await broadcast(version, sender=websocket)
                    await websocket.send(
                        json.dumps({"type": "ack", "version": version})
                    )
            except (json.JSONDecodeError, ValueError):
                await websocket.send(
                    json.dumps({"type": "error", "message": "Invalid payload"})
                )
    except websockets.exceptions.ConnectionClosed as exc:
        log.info("WebSocket client disconnected: %s", exc)
    except Exception:
        log.exception("Unexpected error in WebSocket handler")
    finally:
        CLIENTS.discard(websocket)


# ── Main ──────────────────────────────────────────────────────
async def main(port: int):
    loop = asyncio.get_event_loop()

    # Start REST server
    rest_server = await loop.create_server(RESTProtocol, "0.0.0.0", port)

    # Start WebSocket server on port+1
    ws_port = port + 1
    ws_server = await ws_serve(ws_handler, "0.0.0.0", ws_port)
    log.info("Chore Tracker sync server running")
    log.info("  REST:      http://0.0.0.0:%d/data", port)
    log.info("  WebSocket: ws://0.0.0.0:%d", ws_port)
    log.info("  Data file: %s", os.path.abspath(data_file))
    log.info("  Changelog: %s", os.path.abspath(changelog_file))
    if static_root:
        log.info("  Static:    http://0.0.0.0:%d/  → %s", port, os.path.abspath(static_root))
    await asyncio.Future()  # run forever


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Chore Tracker sync server")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"Port to listen on (default: {DEFAULT_PORT})"
    )
    parser.add_argument(
        "--data", type=str, default=DEFAULT_DATA_FILE, help=f"Path to JSON data file (default: {DEFAULT_DATA_FILE})"
    )
    parser.add_argument(
        "--static", type=str, default=None,
        help="Directory to serve static files from (default: auto-detected parent of server/)."
             " Set to '' to disable."
    )
    args = parser.parse_args()
    data_file = args.data

    # Derive changelog file path from data file
    base, ext = os.path.splitext(data_file)
    changelog_file = f"{base}-changelog{ext}"

    # Auto-detect static root: parent of the directory containing this script
    if args.static is None:
        candidate = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if os.path.isfile(os.path.join(candidate, "index.html")):
            static_root = candidate
    elif args.static:
        static_root = args.static

    asyncio.run(main(args.port))
