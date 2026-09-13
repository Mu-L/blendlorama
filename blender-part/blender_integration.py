"""Bridge server-thread events to Blender's main thread."""

from __future__ import annotations

import os
import queue
import threading
import uuid

import bpy

from . import server
from .image_manager import ImageManager


PROTOCOL_VERSION = 1
_events: "queue.Queue[tuple[str, object]]" = queue.Queue()
_main_thread_id = threading.get_ident()


def ensure_image_id(image) -> str:
    value = image.get("blendlorama_id", "")
    if not isinstance(value, str) or len(value) != 32:
        value = uuid.uuid4().hex
        image["blendlorama_id"] = value
    return value


def find_image(image_id: str = "", name: str = ""):
    if image_id:
        for image in bpy.data.images:
            if image.get("blendlorama_id", "") == image_id:
                return image
    return bpy.data.images.get(name) if name else None


def get_images() -> None:
    data = []
    for image in bpy.data.images:
        if image.get("blendlorama_layer") or image.type in {"RENDER_RESULT", "COMPOSITING", "MULTILAYER"}:
            continue
        data.append({
            "id": ensure_image_id(image),
            "name": image.name,
            "path": bpy.path.abspath(image.filepath) if image.filepath else "",
            "size": [image.size[0], image.size[1]],
            "type": image.type,
            "packed": image.packed_file is not None,
        })
    server.send_message({
        "type": "GET_IMAGES",
        "protocol_version": PROTOCOL_VERSION,
        "data": data,
        "requestId": -1,
    })


def on_client_connected(client_info: str) -> None:
    _events.put(("CONNECTED", client_info))


def on_client_disconnected(client_info: str) -> None:
    _events.put(("DISCONNECTED", client_info))


def on_message_received(client_info: str, message_data: dict) -> None:
    _events.put(("MESSAGE", (client_info, message_data)))


def _ack(message: dict, success: bool = True, error: str = "") -> None:
    response = {
        "type": "ACK",
        "protocol_version": PROTOCOL_VERSION,
        "message_type": message.get("type", ""),
        "revision": int(message.get("revision", 0)),
        "project_id": message.get("project_id", ""),
        "image_id": message.get("image_id", ""),
        "id": message.get("id", ""),
        "success": success,
    }
    if error:
        response["error"] = error
    server.send_message(response)


def handle_sync_texture(message_data: dict) -> None:
    image_name = message_data.get("image", "")
    file_path = message_data.get("file_path", "")
    if not image_name or not file_path:
        raise ValueError("SYNC_TEXTURE requires image and file_path")
    if not os.path.isfile(file_path):
        raise FileNotFoundError(file_path)
    manager = ImageManager.INSTANCE
    if manager is None:
        raise RuntimeError("ImageManager is not initialized")
    image = manager.load_or_create_image(image_name, file_path, message_data.get("project_size"))
    if image is None:
        raise RuntimeError("failed to load image")
    requested_id = message_data.get("image_id", "")
    if requested_id:
        image["blendlorama_id"] = requested_id
    else:
        ensure_image_id(image)
    server.send_message({
        "type": "SYNC_TEXTURE_RESPONSE",
        "protocol_version": PROTOCOL_VERSION,
        "success": True,
        "image_name": image.name,
        "image_id": image.get("blendlorama_id", ""),
        "size": list(image.size),
        "packed": image.packed_file is not None,
        "revision": int(message_data.get("revision", 0)),
    })


def _process_message(message: dict) -> None:
    version = int(message.get("protocol_version", PROTOCOL_VERSION))
    if version != PROTOCOL_VERSION:
        raise ValueError(f"unsupported protocol version {version}")
    msg_type = message.get("type")
    if msg_type == "GET_IMAGES":
        get_images()
    elif msg_type == "SYNC_TEXTURE":
        handle_sync_texture(message)
    elif msg_type == "SYNC_LAYERS":
        from . import layer_model
        layer_model.apply_sync_layers(bpy.context.scene, message)
        _ack(message)
    elif msg_type == "SYNC_LAYER":
        from . import layer_model
        layer_model.apply_sync_layer(bpy.context.scene, message)
        _ack(message)
    elif msg_type == "LAYER_STATE":
        from . import layer_model
        layer_model.apply_layer_state(bpy.context.scene, message)
        _ack(message)
    elif msg_type == "ACTIVE_LAYER":
        from . import layer_model
        layer_model.apply_active_layer(bpy.context.scene, message)
    elif msg_type in {"LOCK_LAYER", "UNLOCK_LAYER"}:
        from . import layer_model
        layer_model.set_layer_lock(bpy.context.scene, message)
    elif msg_type == "ACK":
        from .layer_watch import LayerWatch
        if LayerWatch.instance:
            LayerWatch.instance.acknowledge(message)
    elif msg_type != "WELCOME":
        raise ValueError(f"unknown message type {msg_type!r}")


def process_pending_events() -> float:
    """Persistent Blender timer; this is the only place callbacks touch bpy."""
    if threading.get_ident() != _main_thread_id:
        return 0.05
    for _ in range(64):
        try:
            event, payload = _events.get_nowait()
        except queue.Empty:
            break
        if event == "CONNECTED":
            print(f"[Blendlorama] client connected: {payload}")
            get_images()
            continue
        if event == "DISCONNECTED":
            print(f"[Blendlorama] client disconnected: {payload}")
            continue
        _client_info, message = payload
        try:
            _process_message(message)
        except Exception as exc:
            print(f"[Blendlorama] {message.get('type', 'message')} failed: {exc}")
            _ack(message, False, str(exc))
    return 0.05


def clear_pending_events() -> None:
    while True:
        try:
            _events.get_nowait()
        except queue.Empty:
            return


def setup_blender_integration() -> None:
    server.set_callbacks(
        on_connected=on_client_connected,
        on_disconnected=on_client_disconnected,
        on_message=on_message_received,
    )
