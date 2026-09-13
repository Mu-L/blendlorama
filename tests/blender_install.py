"""Run after extension install-file -e to verify the packaged module loads."""

import importlib

module = importlib.import_module("bl_ext.user_default.pixelorama_sync")
assert module.ImageManager.INSTANCE is not None
status = module.server.get_server_status()
assert status["address"] == "ws://127.0.0.1:8765"
assert not status["running"]
print("PASS: packaged extension imported and enabled")
