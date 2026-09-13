"""Image updates. Callers must run on Blender's main thread."""

from __future__ import annotations

import os
import threading

import bpy


class ImageManager:
    INSTANCE = None

    def __init__(self) -> None:
        self.IMAGE_NAME = None
        self._main_thread_id = threading.get_ident()
        ImageManager.INSTANCE = self

    def _assert_main_thread(self) -> None:
        if threading.get_ident() != self._main_thread_id:
            raise RuntimeError("Blender image data may only be changed on the main thread")

    def get_image(self, name=None):
        key = name if name is not None else self.IMAGE_NAME
        return bpy.data.images.get(key) if key else None

    def get_image_from_name(self, name):
        return bpy.data.images.get(name)

    def set_image_name(self, name: str | None):
        self.IMAGE_NAME = name

    def get_image_size(self, name=None):
        image = self.get_image(name)
        return image.size if image else None

    def load_or_create_image(self, image_name, file_path, project_size=None):
        self._assert_main_thread()
        if not os.path.isfile(file_path):
            raise FileNotFoundError(file_path)
        temp = bpy.data.images.load(file_path, check_existing=False)
        target = bpy.data.images.get(image_name)
        if target is not None and target is not temp:
            if target.type in {"RENDER_RESULT", "COMPOSITING", "MULTILAYER"}:
                bpy.data.images.remove(temp)
                raise ValueError(f"cannot replace protected image type {target.type}")
            if tuple(target.size) != tuple(temp.size):
                target.scale(temp.size[0], temp.size[1])
            target.pixels.foreach_set(list(temp.pixels))
            bpy.data.images.remove(temp)
        else:
            target = temp
            target.name = image_name
        target.use_fake_user = True
        target.pack()
        if target.channels == 4:
            target.alpha_mode = "STRAIGHT"
        target.update()
        target.update_tag()
        self.IMAGE_NAME = target.name
        return target

    @classmethod
    def reset(cls) -> None:
        cls.INSTANCE = None
