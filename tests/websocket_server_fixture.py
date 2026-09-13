"""Standalone server fixture used by the Godot interoperability test."""

import importlib.util
import signal
import sys
import threading
from pathlib import Path


spec = importlib.util.spec_from_file_location("blendlorama_server_fixture", Path(__file__).resolve().parents[1] / "blender-part/server.py")
server = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = server
spec.loader.exec_module(server)
server.PORT = 18765

stopped = threading.Event()
signal.signal(signal.SIGTERM, lambda *_args: stopped.set())
started, error = server.start_server()
if not started:
    raise SystemExit(error)
print("READY", flush=True)
stopped.wait(30)
server.stop_server()
