"""Watch the current Blender paint target and send changed pixels."""

from __future__ import annotations

import hashlib
import os
import tempfile

import bpy
import numpy as np

from . import layer_model
from .server import get_server_status, send_message


class LayerWatch:
    instance = None

    def __init__(self):
        LayerWatch.instance = self
        self.last_signature = {}
        self.revisions = {}
        self.pending_files = {}
        self.dirty = set()
        self._tempdir = tempfile.mkdtemp(prefix="blendlorama_layer_")

    def _signature(self, image):
        pixels = np.empty(len(image.pixels), dtype=np.float32)
        image.pixels.foreach_get(pixels)
        digest = hashlib.blake2b(pixels.tobytes(), digest_size=16).hexdigest()
        return (tuple(image.size), digest)

    def mark_received(self, image):
        self.last_signature[image.name] = self._signature(image)
        self.dirty.discard(image.name)

    def check_for_changes(self):
        try:
            scene = bpy.context.scene
            if scene is None:
                return 0.15
            image = scene.tool_settings.image_paint.canvas
            if image is None or not image.get("blendlorama_layer"):
                return 0.15
            layer = next((item for item in scene.pixelorama_layers
                          if (candidate := layer_model.find_layer_image(
                              item.image_name, item.layer_id, item.image_id
                          )) is not None and candidate.as_pointer() == image.as_pointer()), None)
            if layer is None:
                return 0.15
            signature = self._signature(image)
            previous = self.last_signature.get(image.name)
            if previous is None:
                self.last_signature[image.name] = signature
                return 0.15
            status = get_server_status()
            if signature == previous:
                if image.name in self.dirty and status["running"] and status["clients_count"] > 0:
                    if self._export_and_send(layer, image, signature[1]):
                        self.dirty.discard(image.name)
                return 0.15
            layer_model.recompute_preview(scene, layer.image_name, *image.size)
            if status["running"] and status["clients_count"] > 0:
                if self._export_and_send(layer, image, signature[1]):
                    self.dirty.discard(image.name)
                else:
                    self.dirty.add(image.name)
            else:
                self.dirty.add(image.name)
            self.last_signature[image.name] = signature
        except Exception as exc:
            print(f"[LayerWatch] {exc}")
        return 0.15

    def _export_and_send(self, layer, image, content_hash: str) -> bool:
        key = (layer.image_name, layer.layer_id)
        revision = self.revisions.get(key, 0) + 1
        self.revisions[key] = revision
        fd, path = tempfile.mkstemp(prefix=f"r{revision}_", suffix=".png", dir=self._tempdir)
        os.close(fd)
        save_png(image, path)
        image.pack()
        message = {
            "type": "SYNC_LAYER",
            "protocol_version": 1,
            "image": layer.image_name,
            "image_id": layer.image_id,
            "id": layer.layer_id,
            "frame": layer.frame,
            "revision": revision,
            "content_hash": content_hash,
            "w": image.size[0],
            "h": image.size[1],
            "file_path": path,
        }
        if send_message(message):
            self.pending_files[(layer.layer_id, revision)] = path
            # Bound disk use if a peer vanishes before acknowledging packets.
            same_layer = sorted(k for k in self.pending_files if k[0] == layer.layer_id)
            for old_key in same_layer[:-8]:
                self._remove_pending(old_key)
            return True
        else:
            os.unlink(path)
            return False

    def acknowledge(self, message: dict) -> None:
        if message.get("message_type") != "SYNC_LAYER":
            return
        self._remove_pending((message.get("id", ""), int(message.get("revision", 0))))

    def _remove_pending(self, key) -> None:
        path = self.pending_files.pop(key, None)
        if path:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass


def _pixels_with_rgb_bleed(image, radius):
    """Copy neighbouring RGB into transparent pixels while preserving alpha."""
    width, height = image.size
    pixels = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    pixels = pixels.reshape((height, width, 4))
    original_alpha = pixels[..., 3].copy()
    filled = original_alpha > 0.0
    rgb = pixels[..., :3].copy()
    for _ in range(radius):
        sums = np.zeros_like(rgb)
        counts = np.zeros((height, width), dtype=np.float32)
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1),
                       (-1, -1), (-1, 1), (1, -1), (1, 1)):
            source_y = slice(max(0, -dy), min(height, height - dy))
            source_x = slice(max(0, -dx), min(width, width - dx))
            target_y = slice(max(0, dy), min(height, height + dy))
            target_x = slice(max(0, dx), min(width, width + dx))
            mask = filled[source_y, source_x]
            sums[target_y, target_x] += rgb[source_y, source_x] * mask[..., None]
            counts[target_y, target_x] += mask
        grow = (~filled) & (counts > 0)
        if not np.any(grow):
            break
        rgb[grow] = sums[grow] / counts[grow, None]
        filled[grow] = True
    pixels[..., :3] = rgb
    pixels[..., 3] = original_alpha
    return pixels.reshape(-1)


def save_png(image, path, bleed=0):
    if bleed > 0:
        temp = bpy.data.images.new(
            "Blendlorama Export Bleed", width=image.size[0], height=image.size[1], alpha=True)
        try:
            temp.alpha_mode = "STRAIGHT"
            temp.colorspace_settings.name = image.colorspace_settings.name
            temp.pixels.foreach_set(_pixels_with_rgb_bleed(image, int(bleed)))
            temp.update()
            save_png(temp, path)
        finally:
            bpy.data.images.remove(temp)
        return
    previous = image.filepath_raw, image.file_format
    try:
        image.filepath_raw = path
        image.file_format = "PNG"
        image.save()
    finally:
        image.filepath_raw, image.file_format = previous
