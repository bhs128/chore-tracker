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
import mimetypes
import os
import subprocess
import time
from http import HTTPStatus
from pathlib import Path
from urllib.parse import unquote, urlparse

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
def read_data() -> str:
    """Return the raw JSON string from disk (or '{}' if missing)."""
    if os.path.exists(data_file):
        with open(data_file, "r") as f:
            return f.read()
    return "{}"


def write_data(body: str) -> int:
    """Write JSON to disk, stamping a _version field. Returns the new version."""
    try:
        obj = json.loads(body)
    except json.JSONDecodeError:
        raise ValueError("Invalid JSON")
    obj["_version"] = int(time.time() * 1000)
    with open(data_file, "w") as f:
        json.dump(obj, f)
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


# ── HTTP Server (REST API) ────────────────────────────────────
CORS_HEADERS = (
    ("Access-Control-Allow-Origin", "*"),
    ("Access-Control-Allow-Methods", "GET, PUT, OPTIONS"),
    ("Access-Control-Allow-Headers", "Content-Type"),
)


class RESTProtocol(asyncio.Protocol):
    """Minimal HTTP/1.1 protocol handler for the REST API."""

    def connection_made(self, transport):
        self.transport = transport
        self._buf = b""

    def data_received(self, data):
        self._buf += data
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

        content_length = int(headers.get("content-length", 0))
        body = self._buf[body_start : body_start + content_length]

        # Wait for full body
        if len(body) < content_length:
            return

        # Route
        asyncio.ensure_future(self._handle(method, path, body))

    async def _handle(self, method, path, body):
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
                try:
                    version = write_data(body.decode("utf-8"))
                except ValueError as exc:
                    self._respond(400, str(exc), extra_headers=CORS_HEADERS)
                    return
                await broadcast(version)
                resp_body = json.dumps({"version": version})
                self._respond(200, resp_body, content_type="application/json", extra_headers=CORS_HEADERS)
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
        reason = {200: "OK", 204: "No Content", 400: "Bad Request", 404: "Not Found"}.get(status, "OK")
        lines = [f"HTTP/1.1 {status} {reason}"]
        lines.append(f"Content-Type: {content_type}")
        lines.append(f"Content-Length: {len(body)}")
        for k, v in extra_headers:
            lines.append(f"{k}: {v}")
        lines.append("Connection: close")
        lines.append("")
        lines.append("")
        self.transport.write("\r\n".join(lines).encode("utf-8") + body)
        self.transport.close()


# ── WebSocket handler ─────────────────────────────────────────
async def ws_handler(websocket, path=None):
    """Handle a single WebSocket connection."""
    CLIENTS.add(websocket)
    try:
        async for message in websocket:
            try:
                msg = json.loads(message)
                if msg.get("action") == "put" and "data" in msg:
                    version = write_data(json.dumps(msg["data"]))
                    await broadcast(version, sender=websocket)
                    await websocket.send(
                        json.dumps({"type": "ack", "version": version})
                    )
            except (json.JSONDecodeError, ValueError):
                await websocket.send(
                    json.dumps({"type": "error", "message": "Invalid payload"})
                )
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
    print(f"Chore Tracker sync server running")
    print(f"  REST:      http://0.0.0.0:{port}/data")
    print(f"  WebSocket: ws://0.0.0.0:{ws_port}")
    print(f"  Data file: {os.path.abspath(data_file)}")
    if static_root:
        print(f"  Static:    http://0.0.0.0:{port}/  → {os.path.abspath(static_root)}")
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

    # Auto-detect static root: parent of the directory containing this script
    if args.static is None:
        candidate = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if os.path.isfile(os.path.join(candidate, "index.html")):
            static_root = candidate
    elif args.static:
        static_root = args.static

    asyncio.run(main(args.port))
