"""Exercise the dependency-free WebSocket server with a raw RFC 6455 client."""

import importlib.util
import json
import os
import socket
import struct
import sys
import threading
import time
import unittest
from pathlib import Path


spec = importlib.util.spec_from_file_location("blendlorama_server", Path(__file__).resolve().parents[1] / "blender-part/server.py")
server = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = server
spec.loader.exec_module(server)


def masked_text(value: dict) -> bytes:
    payload = json.dumps(value).encode()
    mask = os.urandom(4)
    length = len(payload)
    head = bytes((0x81, 0x80 | length)) if length < 126 else bytes((0x81, 0x80 | 126)) + struct.pack("!H", length)
    return head + mask + bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))


def read_frame(sock: socket.socket) -> dict:
    first, second = sock.recv(2)
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", sock.recv(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", sock.recv(8))[0]
    payload = bytearray()
    while len(payload) < length:
        payload.extend(sock.recv(length - len(payload)))
    return json.loads(payload.decode())


class ServerTests(unittest.TestCase):
    def tearDown(self):
        server.stop_server()

    def test_handshake_receive_and_broadcast(self):
        probe = socket.socket()
        probe.bind((server.HOST, 0))
        server.PORT = probe.getsockname()[1]
        probe.close()
        messages = []
        event = threading.Event()
        server.set_callbacks(on_message=lambda _client, message: (messages.append(message), event.set()))
        started, error = server.start_server()
        self.assertTrue(started, error)
        self.assertEqual(server.get_server_status()["address"], f"ws://127.0.0.1:{server.PORT}")
        client = socket.create_connection((server.HOST, server.PORT), timeout=2)
        client.sendall(
            b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            b"Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n"
        )
        response = bytearray()
        while not response.endswith(b"\r\n\r\n"):
            response.extend(client.recv(1))
        header = bytes(response)
        self.assertIn(b"101 Switching Protocols", header)
        welcome = read_frame(client)
        self.assertEqual(welcome["type"], "WELCOME")
        client.sendall(masked_text({"type": "PING", "protocol_version": 1}))
        self.assertTrue(event.wait(2))
        self.assertEqual(messages[0]["type"], "PING")
        self.assertTrue(server.send_message({"type": "PONG", "protocol_version": 1}))
        self.assertEqual(read_frame(client)["type"], "PONG")
        client.close()


if __name__ == "__main__":
    unittest.main()
