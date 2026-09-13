import bpy

from .image_manager import ImageManager
from .server import get_server_status, send_message
from .uv_extractor import get_fast_hash, getUvOverlay


class UvWatch:
    last_hash = None
    instance = None

    def __init__(self) -> None:
        UvWatch.instance = self

    def check_for_changes(self):
        try:
            status = get_server_status()
            if not status["running"] or status["clients_count"] == 0:
                return 0.5
            new_hash = get_fast_hash()
            if (
                new_hash != self.last_hash
                and status["running"]
                and status["clients_count"] > 0
            ):
                send_message(
                    {
                        "type": "GET_UV_OVERLAY",
                        "protocol_version": 1,
                        "data": getUvOverlay(),
                        "noshow": True,
                        "requestId": -1,
                    }
                )
                self.last_hash = new_hash
        except Exception as exc:
            print(f"[Blendlorama] UV watcher: {exc}")
        return 0.5


class ImagesStateWatch:
    instance = None
    last_hash = None

    def __init__(self) -> None:
        ImagesStateWatch.instance = self

    def check_for_changes(self):
        status = get_server_status()
        if not status["running"] or status["clients_count"] == 0:
            return 0.5
        data = set()
        for image in bpy.data.images:
            # Skip special image types that shouldn't be monitored
            if image.get("blendlorama_layer") or image.type in ["RENDER_RESULT", "COMPOSITING", "MULTILAYER"]:
                continue

            data.add(
                frozenset(
                    {
                        image.name,
                        bpy.path.abspath(image.filepath) if image.filepath else "",
                        image.size[0],
                        image.size[1],
                        ImageManager.INSTANCE.IMAGE_NAME == image.name,
                    }
                )
            )

        new_hash = hash(frozenset(data))

        if (
            new_hash != self.last_hash
            and status["running"]
            and status["clients_count"] > 0
        ):
            self.last_hash = new_hash
            from .blender_integration import get_images
            get_images()
        return 0.5
