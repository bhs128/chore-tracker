"""
Tests for the Chore Tracker sync server.

Covers:
  - REST API (GET/PUT /data, GET /version, OPTIONS, 404, 413, bad Content-Length)
  - WebSocket lifecycle (connect, put, broadcast, ack)
  - Abrupt client disconnect (the original crash scenario)
  - Logging output verification
  - Static file serving
  - Atomic write safety
  - Concurrent multi-client interactions
  - Simulated multi-device user workflows

Run:
    pytest server/test_server.py -v
"""

import asyncio
import json
import logging
import os
import socket
import struct
import tempfile
import time

import pytest
import pytest_asyncio
import websockets
from websockets.asyncio.client import connect as ws_connect

# ── Import server module ──────────────────────────────────────
import importlib
import sys

# Ensure the server package is importable
sys.path.insert(0, os.path.dirname(__file__))
import server as srv


# ── Helpers ───────────────────────────────────────────────────

def _free_port():
    """Find a free TCP port."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _http_request(port, method, path, body=b"", headers=None):
    """Send a raw HTTP/1.1 request and return (status_code, headers_dict, body_bytes)."""
    headers = headers or {}

    reader, writer = await asyncio.open_connection("127.0.0.1", port)

    req_lines = [f"{method} {path} HTTP/1.1", f"Host: 127.0.0.1:{port}"]
    if body:
        req_lines.append(f"Content-Length: {len(body)}")
    for k, v in headers.items():
        req_lines.append(f"{k}: {v}")
    req_lines.append("")
    req_lines.append("")
    writer.write("\r\n".join(req_lines).encode() + body)
    await writer.drain()

    # Read full response (server sends Connection: close, so read until EOF)
    data = await asyncio.wait_for(reader.read(), timeout=5)
    writer.close()

    # Parse status
    header_end = data.index(b"\r\n\r\n")
    header_block = data[:header_end].decode()
    resp_body = data[header_end + 4:]
    status_line = header_block.split("\r\n")[0]
    status_code = int(status_line.split(" ", 2)[1])

    resp_headers = {}
    for line in header_block.split("\r\n")[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            resp_headers[k.strip().lower()] = v.strip()

    return status_code, resp_headers, resp_body


# ── Fixtures ──────────────────────────────────────────────────

@pytest_asyncio.fixture
async def server(tmp_path):
    """Start the REST + WS servers on ephemeral ports with a temp data file."""
    rest_port = _free_port()
    ws_port = _free_port()

    data_path = str(tmp_path / "test-data.json")
    static_dir = str(tmp_path / "static")
    os.makedirs(static_dir, exist_ok=True)
    # Create a minimal index.html for static serving tests
    with open(os.path.join(static_dir, "index.html"), "w") as f:
        f.write("<html><body>test</body></html>")

    # Patch module-level state
    srv.data_file = data_path
    srv.static_root = static_dir
    srv.CLIENTS.clear()

    loop = asyncio.get_event_loop()
    rest_server = await loop.create_server(srv.RESTProtocol, "127.0.0.1", rest_port)
    ws_server = await srv.ws_serve(srv.ws_handler, "127.0.0.1", ws_port)

    yield {
        "rest_port": rest_port,
        "ws_port": ws_port,
        "data_path": data_path,
        "static_dir": static_dir,
    }

    # Teardown
    rest_server.close()
    await rest_server.wait_closed()
    ws_server.close()
    await ws_server.wait_closed()
    srv.CLIENTS.clear()


# ── REST API Tests ────────────────────────────────────────────

class TestRESTAPI:
    """Tests for the HTTP REST endpoints."""

    @pytest.mark.asyncio
    async def test_get_data_empty(self, server):
        """GET /data returns '{}' when no data file exists."""
        status, headers, body = await _http_request(server["rest_port"], "GET", "/data")
        assert status == 200
        assert json.loads(body) == {}

    @pytest.mark.asyncio
    async def test_put_and_get_data(self, server):
        """PUT /data stores JSON; GET /data retrieves it with a _version stamp."""
        payload = json.dumps({"rooms": [{"name": "Kitchen"}]}).encode()
        status, _, body = await _http_request(
            server["rest_port"], "PUT", "/data", body=payload,
            headers={"Content-Type": "application/json"},
        )
        assert status == 200
        resp = json.loads(body)
        assert "version" in resp

        # Verify persisted data
        status, _, body = await _http_request(server["rest_port"], "GET", "/data")
        assert status == 200
        data = json.loads(body)
        assert data["rooms"][0]["name"] == "Kitchen"
        assert "_version" in data

    @pytest.mark.asyncio
    async def test_put_invalid_json(self, server):
        """PUT /data with invalid JSON returns 400."""
        status, _, body = await _http_request(
            server["rest_port"], "PUT", "/data", body=b"not json{{{",
            headers={"Content-Type": "application/json"},
        )
        assert status == 400

    @pytest.mark.asyncio
    async def test_options_cors(self, server):
        """OPTIONS request returns CORS headers with 204."""
        status, headers, _ = await _http_request(server["rest_port"], "OPTIONS", "/data")
        assert status == 204
        assert headers.get("access-control-allow-origin") == "*"

    @pytest.mark.asyncio
    async def test_get_version(self, server):
        """GET /version returns a JSON object with a version field."""
        status, _, body = await _http_request(server["rest_port"], "GET", "/version")
        assert status == 200
        data = json.loads(body)
        assert "version" in data

    @pytest.mark.asyncio
    async def test_404_unknown_path(self, server):
        """Unknown paths return 404."""
        status, _, _ = await _http_request(server["rest_port"], "GET", "/nonexistent")
        assert status == 404

    @pytest.mark.asyncio
    async def test_static_index(self, server):
        """GET / serves index.html from static root."""
        status, headers, body = await _http_request(server["rest_port"], "GET", "/")
        assert status == 200
        assert b"<html>" in body
        assert headers.get("content-type") == "text/html"

    @pytest.mark.asyncio
    async def test_invalid_content_length(self, server):
        """Malformed Content-Length returns 400."""
        reader, writer = await asyncio.open_connection("127.0.0.1", server["rest_port"])
        raw = (
            b"PUT /data HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Length: abc\r\n"
            b"\r\n"
        )
        writer.write(raw)
        await writer.drain()
        data = await asyncio.wait_for(reader.read(), timeout=5)
        writer.close()
        assert b"400 Bad Request" in data


# ── WebSocket Tests ───────────────────────────────────────────

class TestWebSocket:
    """Tests for the WebSocket sync protocol."""

    @pytest.mark.asyncio
    async def test_ws_connect_and_put(self, server):
        """A WS client can send a 'put' action and receive an 'ack'."""
        uri = f"ws://127.0.0.1:{server['ws_port']}"
        async with ws_connect(uri) as ws:
            msg = {"action": "put", "data": {"rooms": [{"name": "Bathroom"}]}}
            await ws.send(json.dumps(msg))
            resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert resp["type"] == "ack"
            assert "version" in resp

        # Verify data was written to disk
        with open(server["data_path"]) as f:
            data = json.loads(f.read())
        assert data["rooms"][0]["name"] == "Bathroom"

    @pytest.mark.asyncio
    async def test_ws_broadcast_to_other_clients(self, server):
        """When one client puts data, other connected clients get a broadcast."""
        uri = f"ws://127.0.0.1:{server['ws_port']}"
        async with ws_connect(uri) as client1, ws_connect(uri) as client2:
            # client1 sends data
            msg = {"action": "put", "data": {"task": "clean"}}
            await client1.send(json.dumps(msg))

            # client1 gets ack
            ack = json.loads(await asyncio.wait_for(client1.recv(), timeout=5))
            assert ack["type"] == "ack"

            # client2 gets broadcast
            broadcast = json.loads(await asyncio.wait_for(client2.recv(), timeout=5))
            assert broadcast["type"] == "data-changed"
            assert broadcast["version"] == ack["version"]

    @pytest.mark.asyncio
    async def test_ws_invalid_json(self, server):
        """Sending invalid JSON over WS returns an error message."""
        uri = f"ws://127.0.0.1:{server['ws_port']}"
        async with ws_connect(uri) as ws:
            await ws.send("not valid json{{{")
            resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert resp["type"] == "error"
            assert "Invalid payload" in resp["message"]

    @pytest.mark.asyncio
    async def test_ws_client_cleanup_on_disconnect(self, server):
        """After a client disconnects normally, it's removed from CLIENTS."""
        uri = f"ws://127.0.0.1:{server['ws_port']}"
        async with ws_connect(uri) as ws:
            await asyncio.sleep(0.1)
            assert len(srv.CLIENTS) == 1

        # Give the server a moment to process the close
        await asyncio.sleep(0.2)
        assert len(srv.CLIENTS) == 0


# ── Crash / Abrupt Disconnect Tests ──────────────────────────

class TestAbruptDisconnect:
    """
    Tests for the exact crash scenario from the bug report:
    a client's TCP connection resets mid-WebSocket frame,
    causing ConnectionClosedError with no close frame.
    The server must NOT crash — it should log and continue.
    """

    @pytest.mark.asyncio
    async def test_tcp_reset_does_not_crash_server(self, server, caplog):
        """
        Simulate the original crash: open a WS connection, then kill the TCP
        socket abruptly (RST, no close frame). The server should log an info
        message and keep running.
        """
        uri = f"ws://127.0.0.1:{server['ws_port']}"

        # Connect, then destroy the underlying TCP socket with RST
        ws = await ws_connect(uri)
        await asyncio.sleep(0.1)
        assert len(srv.CLIENTS) == 1

        # Force a TCP RST by setting SO_LINGER to (on, 0) and closing
        raw_sock = ws.transport.get_extra_info("socket")
        if raw_sock is not None:
            raw_sock.setsockopt(
                socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
            )
        # Close without WebSocket close handshake
        ws.transport.close()

        # Wait for the server to process the disconnect
        await asyncio.sleep(0.5)

        # Server must still be running — CLIENTS should be empty, not crashed
        assert len(srv.CLIENTS) == 0

        # Verify the server still accepts new connections
        async with ws_connect(uri) as ws2:
            msg = {"action": "put", "data": {"test": "still alive"}}
            await ws2.send(json.dumps(msg))
            resp = json.loads(await asyncio.wait_for(ws2.recv(), timeout=5))
            assert resp["type"] == "ack"

    @pytest.mark.asyncio
    async def test_abrupt_disconnect_is_logged(self, server, caplog):
        """The abrupt disconnect produces a log message (not silent)."""
        uri = f"ws://127.0.0.1:{server['ws_port']}"

        with caplog.at_level(logging.INFO, logger="chore-server"):
            ws = await ws_connect(uri)
            await asyncio.sleep(0.1)

            raw_sock = ws.transport.get_extra_info("socket")
            if raw_sock is not None:
                raw_sock.setsockopt(
                    socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
                )
            ws.transport.close()
            await asyncio.sleep(0.5)

        # Check that a disconnect was logged
        disconnect_logged = any(
            "disconnected" in r.message.lower() for r in caplog.records
        )
        assert disconnect_logged, (
            f"Expected a 'disconnected' log message, got: "
            f"{[r.message for r in caplog.records]}"
        )

    @pytest.mark.asyncio
    async def test_multiple_abrupt_disconnects(self, server):
        """Multiple clients disconnecting abruptly don't crash the server."""
        uri = f"ws://127.0.0.1:{server['ws_port']}"

        connections = []
        for _ in range(5):
            ws = await ws_connect(uri)
            connections.append(ws)
        await asyncio.sleep(0.1)
        assert len(srv.CLIENTS) == 5

        # Kill them all abruptly
        for ws in connections:
            raw_sock = ws.transport.get_extra_info("socket")
            if raw_sock is not None:
                raw_sock.setsockopt(
                    socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
                )
            ws.transport.close()

        await asyncio.sleep(1.0)
        assert len(srv.CLIENTS) == 0

        # Server still functional
        async with ws_connect(uri) as ws_new:
            msg = {"action": "put", "data": {"survived": True}}
            await ws_new.send(json.dumps(msg))
            resp = json.loads(await asyncio.wait_for(ws_new.recv(), timeout=5))
            assert resp["type"] == "ack"


# ── Atomic Write Tests ────────────────────────────────────────

class TestAtomicWrite:
    """Tests for data file integrity."""

    @pytest.mark.asyncio
    async def test_write_is_atomic(self, server):
        """After a PUT, the data file contains valid JSON (no partial writes)."""
        payload = json.dumps({"big": "x" * 10000}).encode()
        status, _, _ = await _http_request(
            server["rest_port"], "PUT", "/data", body=payload,
            headers={"Content-Type": "application/json"},
        )
        assert status == 200

        with open(server["data_path"]) as f:
            data = json.loads(f.read())  # must not raise
        assert data["big"] == "x" * 10000

    @pytest.mark.asyncio
    async def test_version_increases(self, server):
        """Each PUT gets a newer _version timestamp."""
        versions = []
        for i in range(3):
            payload = json.dumps({"seq": i}).encode()
            status, _, body = await _http_request(
                server["rest_port"], "PUT", "/data", body=payload,
                headers={"Content-Type": "application/json"},
            )
            assert status == 200
            versions.append(json.loads(body)["version"])
            time.sleep(0.01)  # ensure timestamps differ

        assert versions == sorted(versions), "Versions should be monotonically increasing"


# ── Logging Tests ─────────────────────────────────────────────

class TestLogging:
    """Verify that server actions produce appropriate log output."""

    @pytest.mark.asyncio
    async def test_broadcast_logs_stale_client(self, server, caplog):
        """
        If a client in CLIENTS is already dead, broadcast() should remove it
        and log the removal.
        """
        # Create a mock "dead" websocket
        class DeadSocket:
            async def send(self, msg):
                raise ConnectionError("gone")

        dead = DeadSocket()
        srv.CLIENTS.add(dead)

        with caplog.at_level(logging.INFO, logger="chore-server"):
            await srv.broadcast(version=12345)

        assert dead not in srv.CLIENTS
        stale_logged = any("stale" in r.message.lower() for r in caplog.records)
        assert stale_logged, (
            f"Expected 'stale' log message, got: {[r.message for r in caplog.records]}"
        )


# ── Simulated User Interaction Tests ─────────────────────────

class _SimulatedDevice:
    """
    Simulates a client device (phone/tablet) running the chore tracker app.
    Mirrors the real client behavior from index.html:
    - syncPull()  →  GET /data
    - syncPush()  →  PUT /data
    - WebSocket   →  listen for data-changed broadcasts, then re-pull
    """

    def __init__(self, rest_port, ws_port, device_name="Device"):
        self.rest_port = rest_port
        self.ws_port = ws_port
        self.name = device_name
        self.data = {}
        self._ws = None
        self._notifications = asyncio.Queue()

    async def sync_pull(self):
        """GET /data — like the client's syncPull()."""
        status, _, body = await _http_request(self.rest_port, "GET", "/data")
        assert status == 200, f"{self.name}: GET /data returned {status}"
        remote = json.loads(body)
        if remote and remote.get("rooms"):
            # Preserve local-only fields (like the real client does)
            selected_user = self.data.get("selectedUser")
            self.data = remote
            if selected_user:
                self.data["selectedUser"] = selected_user
        elif not self.data:
            self.data = remote
        return self.data

    async def sync_push(self):
        """PUT /data — like the client's syncPush()."""
        # Strip local-only fields before sending (matches real client)
        payload = json.loads(json.dumps(self.data))
        payload.pop("selectedUser", None)
        status, _, body = await _http_request(
            self.rest_port, "PUT", "/data",
            body=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        assert status == 200, f"{self.name}: PUT /data returned {status}"
        resp = json.loads(body)
        self.data["_version"] = resp["version"]
        return resp["version"]

    async def connect_ws(self):
        """Open a WebSocket — like connectWebSocket() in the client."""
        uri = f"ws://127.0.0.1:{self.ws_port}"
        self._ws = await ws_connect(uri)

        async def _listener():
            try:
                async for raw in self._ws:
                    msg = json.loads(raw)
                    if msg.get("type") == "data-changed":
                        await self._notifications.put(msg)
            except websockets.exceptions.ConnectionClosed:
                pass

        self._listener_task = asyncio.create_task(_listener())

    async def wait_for_notification(self, timeout=5, ignore_version=None):
        """Wait for a data-changed WS notification (like ws.onmessage).
        If ignore_version is set, skip notifications matching that version
        (mimics the real client's `msg.version !== DATA._version` check)."""
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError()
            msg = await asyncio.wait_for(self._notifications.get(), timeout=remaining)
            if ignore_version is not None and msg.get("version") == ignore_version:
                continue  # skip own broadcast
            return msg

    async def close(self):
        """Disconnect cleanly."""
        if self._ws:
            await self._ws.close()
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass


class TestUserInteraction:
    """
    End-to-end tests simulating real user workflows across multiple devices.
    Each test mimics the actual client behavior from index.html.
    """

    @pytest.mark.asyncio
    async def test_new_user_first_load(self, server):
        """
        Scenario: A user opens the app for the first time on a new device.
        Expected: GET /data returns empty, device can initialize data.
        """
        phone = _SimulatedDevice(server["rest_port"], server["ws_port"], "Phone")

        # First load — pull returns empty
        data = await phone.sync_pull()
        assert data == {} or data == {"_version": data.get("_version")}

        # User sets up their first room
        phone.data = {
            "rooms": [{"id": "r1", "name": "Kitchen", "cleanDays": 7, "tasks": [
                {"id": "t1", "label": "Wipe counters", "cleanDays": 7},
            ]}],
            "users": [{"id": "u1", "name": "Alice"}],
            "settings": {"pastDays": 3, "futureDays": 10},
        }
        version = await phone.sync_push()
        assert isinstance(version, int) and version > 0

        # Verify data persisted correctly
        data = await phone.sync_pull()
        assert data["rooms"][0]["name"] == "Kitchen"
        assert data["users"][0]["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_two_devices_sync_via_rest(self, server):
        """
        Scenario: User has a phone and a tablet. Phone saves data, tablet pulls it.
        """
        phone = _SimulatedDevice(server["rest_port"], server["ws_port"], "Phone")
        tablet = _SimulatedDevice(server["rest_port"], server["ws_port"], "Tablet")

        # Phone creates data
        phone.data = {
            "rooms": [{"id": "r1", "name": "Bathroom", "cleanDays": 5, "tasks": []}],
            "users": [{"id": "u1", "name": "Bob"}],
            "settings": {},
        }
        await phone.sync_push()

        # Tablet pulls — sees the same data
        await tablet.sync_pull()
        assert tablet.data["rooms"][0]["name"] == "Bathroom"
        assert tablet.data["users"][0]["name"] == "Bob"

    @pytest.mark.asyncio
    async def test_realtime_sync_via_websocket(self, server):
        """
        Scenario: Two devices connected over WebSocket. Phone saves a change,
        tablet gets notified in real-time and re-pulls.
        (This is the core real-time sync flow from the app.)
        """
        phone = _SimulatedDevice(server["rest_port"], server["ws_port"], "Phone")
        tablet = _SimulatedDevice(server["rest_port"], server["ws_port"], "Tablet")

        # Both devices connect WS
        await phone.connect_ws()
        await tablet.connect_ws()
        await asyncio.sleep(0.1)

        # Phone pushes initial data
        phone.data = {
            "rooms": [{"id": "r1", "name": "Living Room", "cleanDays": 3, "tasks": []}],
            "users": [],
            "settings": {},
        }
        version1 = await phone.sync_push()

        # Tablet receives notification
        notif = await tablet.wait_for_notification()
        assert notif["type"] == "data-changed"
        assert notif["version"] == version1

        # Tablet re-pulls (like the real ws.onmessage handler does)
        await tablet.sync_pull()
        assert tablet.data["rooms"][0]["name"] == "Living Room"

        # Now tablet makes a change
        tablet.data["rooms"][0]["name"] = "Family Room"
        version2 = await tablet.sync_push()

        # Phone receives notification and re-pulls
        # (ignore version1 broadcast that phone may still have queued from its own push)
        notif = await phone.wait_for_notification(ignore_version=version1)
        assert notif["version"] == version2
        await phone.sync_pull()
        assert phone.data["rooms"][0]["name"] == "Family Room"

        await phone.close()
        await tablet.close()

    @pytest.mark.asyncio
    async def test_mark_task_done(self, server):
        """
        Scenario: User marks a chore as done on their phone, all devices see it.
        """
        phone = _SimulatedDevice(server["rest_port"], server["ws_port"], "Phone")
        tablet = _SimulatedDevice(server["rest_port"], server["ws_port"], "Tablet")

        await phone.connect_ws()
        await tablet.connect_ws()
        await asyncio.sleep(0.1)

        # Initial state: one room with one uncompleted task
        phone.data = {
            "rooms": [{
                "id": "r1", "name": "Kitchen", "cleanDays": 7,
                "tasks": [{"id": "t1", "label": "Wash dishes", "cleanDays": 7}],
            }],
            "users": [{"id": "u1", "name": "Alice"}],
            "history": [],
            "settings": {},
        }
        await phone.sync_push()
        await tablet.wait_for_notification()
        await tablet.sync_pull()

        # Alice marks "Wash dishes" as done on phone
        phone.data["history"].append({
            "taskId": "t1",
            "roomId": "r1",
            "userId": "u1",
            "date": "2026-03-17",
        })
        version = await phone.sync_push()

        # Tablet gets notified, re-pulls, and sees the history entry
        notif = await tablet.wait_for_notification()
        assert notif["version"] == version
        await tablet.sync_pull()
        assert len(tablet.data["history"]) == 1
        assert tablet.data["history"][0]["taskId"] == "t1"
        assert tablet.data["history"][0]["userId"] == "u1"

        await phone.close()
        await tablet.close()

    @pytest.mark.asyncio
    async def test_add_room_and_tasks(self, server):
        """
        Scenario: User adds a new room with tasks on one device,
        another device sees the full room after sync.
        """
        phone = _SimulatedDevice(server["rest_port"], server["ws_port"], "Phone")
        tablet = _SimulatedDevice(server["rest_port"], server["ws_port"], "Tablet")

        await phone.connect_ws()
        await tablet.connect_ws()
        await asyncio.sleep(0.1)

        phone.data = {"rooms": [], "users": [], "settings": {}}
        await phone.sync_push()
        await tablet.wait_for_notification()

        # Add a room with multiple tasks
        phone.data["rooms"].append({
            "id": "r2", "name": "Main Bathroom", "cleanDays": 5,
            "tasks": [
                {"id": "t1", "label": "Scrub toilet", "cleanDays": 7},
                {"id": "t2", "label": "Clean mirror", "cleanDays": 5},
                {"id": "t3", "label": "Mop floor", "cleanDays": 7},
            ],
        })
        await phone.sync_push()

        notif = await tablet.wait_for_notification()
        await tablet.sync_pull()

        assert len(tablet.data["rooms"]) == 1
        room = tablet.data["rooms"][0]
        assert room["name"] == "Main Bathroom"
        assert len(room["tasks"]) == 3
        task_labels = {t["label"] for t in room["tasks"]}
        assert task_labels == {"Scrub toilet", "Clean mirror", "Mop floor"}

        await phone.close()
        await tablet.close()

    @pytest.mark.asyncio
    async def test_local_only_fields_not_synced(self, server):
        """
        Scenario: selectedUser is per-device and must not be overwritten by sync.
        (The real client strips selectedUser before PUT and preserves it on pull.)
        """
        phone = _SimulatedDevice(server["rest_port"], server["ws_port"], "Phone")
        tablet = _SimulatedDevice(server["rest_port"], server["ws_port"], "Tablet")

        phone.data = {
            "rooms": [{"id": "r1", "name": "Kitchen", "cleanDays": 7, "tasks": []}],
            "users": [{"id": "u1", "name": "Alice"}, {"id": "u2", "name": "Bob"}],
            "selectedUser": "u1",  # local-only
            "settings": {},
        }
        await phone.sync_push()

        # selectedUser should NOT appear in the server data
        status, _, body = await _http_request(server["rest_port"], "GET", "/data")
        server_data = json.loads(body)
        assert "selectedUser" not in server_data

        # Tablet has its own selectedUser
        tablet.data["selectedUser"] = "u2"
        await tablet.sync_pull()
        # After pull, tablet should still have its own selectedUser
        assert tablet.data.get("selectedUser") == "u2"

    @pytest.mark.asyncio
    async def test_rapid_saves_from_multiple_devices(self, server):
        """
        Scenario: Three devices save changes in rapid succession.
        All data should be persisted without corruption.
        """
        devices = [
            _SimulatedDevice(server["rest_port"], server["ws_port"], f"Device-{i}")
            for i in range(3)
        ]

        # Initialize
        devices[0].data = {"rooms": [], "users": [], "settings": {}, "counter": 0}
        await devices[0].sync_push()

        # Each device pulls, modifies, and pushes rapidly
        versions = []
        for i, dev in enumerate(devices):
            await dev.sync_pull()
            dev.data["counter"] = i + 1
            dev.data[f"device_{i}_was_here"] = True
            v = await dev.sync_push()
            versions.append(v)

        # Versions should be monotonically increasing
        assert versions == sorted(versions)

        # Final state on server should be from the last writer
        final = _SimulatedDevice(server["rest_port"], server["ws_port"], "Final")
        await final.sync_pull()
        assert final.data["counter"] == 3
        assert final.data["device_2_was_here"] is True

    @pytest.mark.asyncio
    async def test_device_reconnects_after_server_data_change(self, server):
        """
        Scenario: A device goes offline, data changes on server via another device,
        device comes back and re-pulls to catch up.
        """
        phone = _SimulatedDevice(server["rest_port"], server["ws_port"], "Phone")
        tablet = _SimulatedDevice(server["rest_port"], server["ws_port"], "Tablet")

        # Phone sets initial data
        phone.data = {
            "rooms": [{"id": "r1", "name": "Bedroom", "cleanDays": 7, "tasks": []}],
            "users": [],
            "settings": {},
        }
        await phone.sync_push()
        await tablet.sync_pull()
        assert tablet.data["rooms"][0]["name"] == "Bedroom"

        # Phone "goes offline" (no WS), tablet makes changes
        tablet.data["rooms"][0]["name"] = "Master Bedroom"
        tablet.data["rooms"].append({
            "id": "r2", "name": "Guest Room", "cleanDays": 14, "tasks": []
        })
        await tablet.sync_push()

        # Phone "comes back online" — re-pulls and sees all changes
        await phone.sync_pull()
        assert len(phone.data["rooms"]) == 2
        assert phone.data["rooms"][0]["name"] == "Master Bedroom"
        assert phone.data["rooms"][1]["name"] == "Guest Room"

    @pytest.mark.asyncio
    async def test_ws_ignores_own_broadcast(self, server):
        """
        Scenario: After a device pushes via REST, it receives the new _version
        in the PUT response. When the WS broadcast arrives, the client should
        recognize its own version and skip re-pulling. We verify the version
        matches so the client's comparison logic (msg.version !== DATA._version)
        would correctly skip.
        """
        phone = _SimulatedDevice(server["rest_port"], server["ws_port"], "Phone")
        tablet = _SimulatedDevice(server["rest_port"], server["ws_port"], "Tablet")

        await phone.connect_ws()
        await tablet.connect_ws()
        await asyncio.sleep(0.1)

        phone.data = {"rooms": [], "users": [], "settings": {}}
        push_version = await phone.sync_push()

        # The phone's local _version should match what the server broadcast
        # So in real client code, (msg.version !== DATA._version) would be false
        assert phone.data["_version"] == push_version

        # But the tablet gets the notification with that same version
        notif = await tablet.wait_for_notification()
        assert notif["version"] == push_version

        await phone.close()
        await tablet.close()
