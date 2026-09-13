"""Small dependency-free RFC 6455 server used by the Blender extension.

Only loopback clients are accepted. Callbacks run on the server thread and
therefore must only enqueue data for Blender's main thread.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import struct
import threading
from dataclasses import dataclass, field


HOST = "127.0.0.1"
PORT = 8765
MAX_MESSAGE_BYTES = 16 * 1024 * 1024
_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

connected_clients: set["_Client"] = set()
server_thread: threading.Thread | None = None
server_loop: asyncio.AbstractEventLoop | None = None
server_running = False
server_error = ""
_server: asyncio.Server | None = None
_startup_event: threading.Event | None = None
_state_lock = threading.Lock()

on_client_connected_callback = None
on_client_disconnected_callback = None
on_message_received_callback = None


@dataclass(eq=False)
class _Client:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    info: str
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    closed: bool = False

    async def send_text(self, text: str) -> None:
        async with self.send_lock:
            if self.closed:
                raise ConnectionError("client is closed")
            self.writer.write(_encode_frame(0x1, text.encode("utf-8")))
            await self.writer.drain()

    async def send_control(self, opcode: int, payload: bytes = b"") -> None:
        async with self.send_lock:
            if self.closed:
                return
            self.writer.write(_encode_frame(opcode, payload))
            await self.writer.drain()

    async def close(self, code: int = 1000, reason: str = "") -> None:
        if self.closed:
            return
        try:
            payload = struct.pack("!H", code) + reason.encode("utf-8")[:123]
            await self.send_control(0x8, payload)
        except (ConnectionError, OSError):
            pass
        self.closed = True
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except (ConnectionError, OSError):
            pass


def _encode_frame(opcode: int, payload: bytes) -> bytes:
    length = len(payload)
    head = bytes((0x80 | opcode,))
    if length < 126:
        return head + bytes((length,)) + payload
    if length <= 0xFFFF:
        return head + bytes((126,)) + struct.pack("!H", length) + payload
    return head + bytes((127,)) + struct.pack("!Q", length) + payload


async def _read_frame(reader: asyncio.StreamReader) -> tuple[bool, int, bytes]:
    first, second = await reader.readexactly(2)
    fin = bool(first & 0x80)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    if first & 0x70:
        raise ValueError("reserved WebSocket bits are not supported")
    if not masked:
        raise ValueError("client frames must be masked")
    if length == 126:
        length = struct.unpack("!H", await reader.readexactly(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", await reader.readexactly(8))[0]
    if length > MAX_MESSAGE_BYTES:
        raise ValueError("message is too large")
    mask = await reader.readexactly(4)
    payload = bytearray(await reader.readexactly(length))
    for index in range(length):
        payload[index] ^= mask[index & 3]
    return fin, opcode, bytes(payload)


async def _handshake(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> bool:
    try:
        request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        return False
    if len(request) > 16384:
        return False
    try:
        text = request.decode("latin-1")
        lines = text.split("\r\n")
        method, _path, _version = lines[0].split(" ", 2)
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip().lower()] = value.strip()
        key = headers["sec-websocket-key"]
    except (KeyError, ValueError, UnicodeDecodeError):
        return False
    if method != "GET" or headers.get("upgrade", "").lower() != "websocket":
        return False
    if "upgrade" not in headers.get("connection", "").lower():
        return False
    accept = base64.b64encode(hashlib.sha1((key + _GUID).encode("ascii")).digest()).decode("ascii")
    writer.write(
        (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
        ).encode("ascii")
    )
    await writer.drain()
    return True


async def _handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername")
    info = f"{peer[0]}:{peer[1]}" if peer else "local"
    client: _Client | None = None
    try:
        if not await _handshake(reader, writer):
            writer.close()
            await writer.wait_closed()
            return
        client = _Client(reader, writer, info)
        with _state_lock:
            connected_clients.add(client)
        await client.send_text(json.dumps({
            "type": "WELCOME",
            "protocol_version": 1,
            "message": "Connected to Blender Pixel Sync Server",
            "client_id": info,
        }))
        if on_client_connected_callback:
            on_client_connected_callback(info)

        fragments = bytearray()
        fragment_opcode = 0
        while True:
            fin, opcode, payload = await _read_frame(reader)
            if opcode == 0x8:
                break
            if opcode == 0x9:
                await client.send_control(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x2:
                await client.close(1003, "binary messages are not supported")
                return
            if opcode == 0x1:
                if fragment_opcode:
                    raise ValueError("new message before fragmented message completed")
                fragment_opcode = opcode
                fragments.extend(payload)
            elif opcode == 0x0 and fragment_opcode:
                fragments.extend(payload)
            else:
                raise ValueError("unsupported WebSocket opcode")
            if len(fragments) > MAX_MESSAGE_BYTES:
                raise ValueError("message is too large")
            if not fin:
                continue
            raw = bytes(fragments)
            fragments.clear()
            fragment_opcode = 0
            try:
                message = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                await client.send_text(json.dumps({"type": "ERROR", "error": "invalid JSON"}))
                continue
            if not isinstance(message, dict):
                await client.send_text(json.dumps({"type": "ERROR", "error": "message must be an object"}))
                continue
            if on_message_received_callback:
                on_message_received_callback(info, message)
    except (asyncio.IncompleteReadError, ConnectionError, OSError):
        pass
    except ValueError as exc:
        if client:
            await client.close(1002, str(exc))
    finally:
        if client:
            with _state_lock:
                connected_clients.discard(client)
            if on_client_disconnected_callback:
                on_client_disconnected_callback(info)
            await client.close()
        else:
            writer.close()


async def _serve() -> None:
    global _server, server_running, server_error
    try:
        _server = await asyncio.start_server(_handle_client, HOST, PORT)
        with _state_lock:
            server_running = True
            server_error = ""
    except OSError as exc:
        with _state_lock:
            server_running = False
            server_error = str(exc)
        return
    finally:
        if _startup_event:
            _startup_event.set()
    await _server.serve_forever()


def _run_server() -> None:
    global server_loop, _server, server_running
    loop = asyncio.new_event_loop()
    server_loop = loop
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_serve())
    except asyncio.CancelledError:
        pass
    finally:
        clients = list(connected_clients)
        if clients:
            loop.run_until_complete(asyncio.gather(*(client.close() for client in clients), return_exceptions=True))
        if _server:
            _server.close()
            loop.run_until_complete(_server.wait_closed())
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()
        with _state_lock:
            connected_clients.clear()
            server_running = False
        _server = None
        server_loop = None


def start_server() -> tuple[bool, str]:
    global server_thread, _startup_event, server_error
    with _state_lock:
        if server_running:
            return True, ""
        if server_thread and server_thread.is_alive():
            return False, "server is still starting"
        server_error = ""
        _startup_event = threading.Event()
        server_thread = threading.Thread(target=_run_server, name="Blendlorama WebSocket", daemon=True)
        server_thread.start()
    if not _startup_event.wait(timeout=3):
        return False, "server startup timed out"
    with _state_lock:
        return server_running, server_error


def stop_server() -> None:
    global server_thread
    loop = server_loop
    server = _server
    if loop and loop.is_running():
        def stop() -> None:
            if server:
                server.close()
            for task in asyncio.all_tasks(loop):
                task.cancel()
        loop.call_soon_threadsafe(stop)
    thread = server_thread
    if thread and thread.is_alive() and thread is not threading.current_thread():
        thread.join(timeout=5)
    server_thread = None


def get_server_status() -> dict:
    with _state_lock:
        return {
            "running": server_running,
            "clients_count": len(connected_clients),
            "loop_active": server_loop is not None,
            "address": f"ws://{HOST}:{PORT}",
            "error": server_error,
        }


def set_callbacks(on_connected=None, on_disconnected=None, on_message=None) -> None:
    global on_client_connected_callback, on_client_disconnected_callback, on_message_received_callback
    on_client_connected_callback = on_connected
    on_client_disconnected_callback = on_disconnected
    on_message_received_callback = on_message


async def _broadcast(message_data: str) -> None:
    clients = list(connected_clients)
    if not clients:
        return
    results = await asyncio.gather(*(client.send_text(message_data) for client in clients), return_exceptions=True)
    for client, result in zip(clients, results):
        if isinstance(result, Exception):
            with _state_lock:
                connected_clients.discard(client)
            await client.close()


def send_message(message: dict) -> bool:
    loop = server_loop
    if not loop or not loop.is_running() or not connected_clients:
        return False
    try:
        data = json.dumps(message, separators=(",", ":"), ensure_ascii=False)
        asyncio.run_coroutine_threadsafe(_broadcast(data), loop)
        return True
    except (TypeError, RuntimeError, ValueError) as exc:
        print(f"[Blendlorama] could not queue message: {exc}")
        return False
