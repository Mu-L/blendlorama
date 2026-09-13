"""Per-project independent layer images and a composited original-image preview."""

import hashlib
import os

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    IntVectorProperty,
    PointerProperty,
    StringProperty,
)


# Blend modes understood by both sides. Kept intentionally small; unmapped
# modes degrade to "normal". The string values match the on-wire protocol.
BLEND_MODES = (
    ("normal", "Normal", ""),
    ("erase", "Erase", ""),
    ("darken", "Darken", ""),
    ("lighten", "Lighten", ""),
    ("multiply", "Multiply", ""),
    ("screen", "Screen", ""),
    ("overlay", "Overlay", ""),
)


def layer_image_name(image: str, layer_id: str, image_id: str = "") -> str:
    """Legacy internal name used by releases before human-readable datablocks."""
    return "pix_" + hashlib.sha256(f"{image_id or image}\0{layer_id}".encode()).hexdigest()[:48]


def layer_image_display_name(image: str, layer_name: str) -> str:
    """Put the layer first because Blender truncates the end of image names."""
    return f"PX | {layer_name or 'Layer'} | {image}"


def find_layer_image(image: str, layer_id: str, image_id: str = ""):
    """Resolve by persistent IDs; names are display-only and may change."""
    for candidate in bpy.data.images:
        if not candidate.get("blendlorama_layer"):
            continue
        if candidate.get("blendlorama_layer_id", "") != layer_id:
            continue
        candidate_image_id = candidate.get("blendlorama_image_id", "")
        if image_id and candidate_image_id == image_id:
            return candidate
        if not image_id and candidate.get("blendlorama_image_name", "") == image:
            return candidate
    # Migrate datablocks created by the hashed-name implementation.
    return bpy.data.images.get(layer_image_name(image, layer_id, image_id)) or bpy.data.images.get(
        layer_image_name(image, layer_id)
    )


def preview_image_name(image: str) -> str:
    return f"pix_{image}__preview"


def _role_changed(self, context):
    from . import layer_material
    if context.scene:
        layer_material.refresh(context.scene, self.image_name)


class PixeloramaLayer(bpy.types.PropertyGroup):
    """One layer in a scene's stack for a given Pixelorama image collection."""

    layer_id: StringProperty(name="Layer ID")  # type: ignore[name-defined]
    image_id: StringProperty(name="Blender Image ID")  # type: ignore[name-defined]
    image_name: StringProperty(name="Project Image")
    frame: IntProperty(default=0)
    role: EnumProperty(items=(("color", "Color", ""), ("emission", "Emission", "")), default="color", update=_role_changed)
    name: StringProperty(name="Name")  # type: ignore[name-defined]
    opacity: FloatProperty(name="Opacity", default=1.0, min=0.0, max=1.0)  # type: ignore[name-defined]
    visible: BoolProperty(name="Visible", default=True)  # type: ignore[name-defined]
    blend_mode: EnumProperty(items=BLEND_MODES, default="normal")  # type: ignore[name-defined]
    order_index: IntProperty(name="Order Index")  # type: ignore[name-defined]
    source: EnumProperty(  # type: ignore[name-defined]
        items=(
            ("blender", "Blender", ""),
            ("pixelorama", "Pixelorama", ""),
        ),
        default="pixelorama",
    )
    image_size: IntVectorProperty(name="Size", size=2, default=(0, 0))  # type: ignore[name-defined]
    revision: IntProperty(name="Revision", default=0, min=0)  # type: ignore[name-defined]


class LayerStackState(bpy.types.PropertyGroup):
    """Per-scene collection of layer stacks keyed by image name.

    Mirrors the Blender side of a Pixelorama project. ``active_image`` is the
    image collection currently bound to the incoming sync stream; ``active_layer``
    is the index the user paints on.
    """

    active_image: StringProperty(name="Active Image")  # type: ignore[name-defined]
    active_layer: IntProperty(name="Active Layer", default=-1)  # type: ignore[name-defined]


# ---------------------------------------------------------------------------
# Composite preview blending
# ---------------------------------------------------------------------------

def _blend_channel(dst, src, mode, opacity):
    """Per-pixel blend rgba float arrays (length w*h*4). Mirrors Pixelorama's
    ``_blend_layer_onto_image`` semantics but vectorised with numpy."""
    import numpy as np

    if dst.shape[0] == 0 or src.shape[0] == 0:
        return dst

    src_a = np.clip(src[..., 3] * opacity, 0, 1)
    dst_a = dst[..., 3].copy()
    if mode == "erase":
        dst[..., 3] = np.clip(dst_a - src_a, 0, 1)
        return dst
    cb, cs = dst[..., :3].copy(), src[..., :3]
    blend = {
        "normal": lambda: cs,
        "multiply": lambda: cb * cs,
        "screen": lambda: 1 - (1 - cb) * (1 - cs),
        "darken": lambda: np.minimum(cb, cs),
        "lighten": lambda: np.maximum(cb, cs),
        "overlay": lambda: np.where(cb <= .5, 2 * cb * cs, 1 - 2 * (1 - cb) * (1 - cs)),
    }.get(mode, lambda: cs)()
    out_a = src_a + dst_a * (1 - src_a)
    rgb = ((1 - src_a)[..., None] * dst_a[..., None] * cb
           + src_a[..., None] * ((1 - dst_a)[..., None] * cs + dst_a[..., None] * blend))
    dst[..., :3] = np.divide(rgb, out_a[..., None], out=np.zeros_like(rgb), where=out_a[..., None] > 0)
    dst[..., 3] = out_a
    return dst


def _image_to_array(blender_image) -> "object":  # numpy array (h,w,4) float32
    import numpy as np

    w, h = blender_image.size
    if w == 0 or h == 0:
        return np.zeros((0, 0, 4), dtype=np.float32)
    arr = np.empty(w * h * 4, dtype=np.float32)
    blender_image.pixels.foreach_get(arr)
    return arr.reshape(h, w, 4)


def _set_image_pixels(blender_image, arr) -> None:
    import numpy as np

    h, w = arr.shape[:2]
    if tuple(blender_image.size) != (w, h):
        blender_image.scale(w, h)
    flat = np.ascontiguousarray(arr).reshape(-1)
    blender_image.pixels.foreach_set(flat)


def recompute_preview(scene, image: str, width: int, height: int) -> "bpy.types.Image | None":
    """Recomposite the preview datablock from all visible layers for ``image``."""
    import numpy as np

    if width <= 0 or height <= 0:
        return None

    stack = _stack_for_image(scene, image)
    layers = sorted(stack, key=lambda l: l.order_index)
    composite = np.zeros((height, width, 4), dtype=np.float32)

    for layer in layers:
        if not layer.visible:
            continue
        lb = find_layer_image(image, layer.layer_id, layer.image_id)
        if lb is None:
            continue
        larr = _image_to_array(lb)
        if larr.shape != composite.shape:
            continue
        composite = _blend_channel(composite, larr, layer.blend_mode, layer.opacity)

    # Keep existing material links to the original image alive.
    pname = image
    preview = bpy.data.images.get(pname)
    if preview is None:
        preview = bpy.data.images.new(pname, width, height, alpha=True)
    if tuple(preview.size) != (width, height):
        preview.scale(width, height)
    _set_image_pixels(preview, composite)
    preview.use_fake_user = True
    preview["blendlorama_preview"] = True
    preview.pack()
    preview.alpha_mode = "STRAIGHT"
    preview.update()
    preview.update_tag()
    return preview


def _stack_for_image(scene, image: str) -> list[PixeloramaLayer]:
    return [layer for layer in scene.pixelorama_layers if layer.image_name == image]


# ---------------------------------------------------------------------------
# Incoming message applicators (run on main thread)
# ---------------------------------------------------------------------------

def _load_layer_pixels(image: str, layer_id: str, layer_name: str, file_path: str,
                       width: int, height: int, image_id: str = ""):
    """Ensure a per-layer image datablock exists and load pixels from file_path.

    Always loads into a fresh temp then copies pixels to preserve the datablock
    name identity (mirrors ImageManager's pattern).
    """
    target = find_layer_image(image, layer_id, image_id)

    if not os.path.exists(file_path):
        raise FileNotFoundError(file_path)

    temp = bpy.data.images.load(file_path)
    if target is None:
        target = temp
        needs_remove = False
    else:
        if target.type in ["RENDER_RESULT", "COMPOSITING", "MULTILAYER"]:
            bpy.data.images.remove(temp)
            raise ValueError(f"cannot replace protected image type {target.type}")
        if target.size != temp.size:
            target.scale(temp.size[0], temp.size[1])
        target.pixels = list(temp.pixels[:])
        needs_remove = True

    target.use_fake_user = True
    target["blendlorama_layer"] = True
    target["blendlorama_layer_id"] = layer_id
    target["blendlorama_image_id"] = image_id
    target["blendlorama_image_name"] = image
    target["blendlorama_layer_name"] = layer_name
    target.name = layer_image_display_name(image, layer_name)
    target.pack()
    if target.channels == 4:
        target.alpha_mode = "STRAIGHT"
    target.update()
    target.update_tag()

    if needs_remove:
        bpy.data.images.remove(temp)
    from .layer_watch import LayerWatch
    if LayerWatch.instance:
        LayerWatch.instance.mark_received(target)
    return target


def apply_sync_layers(scene, message) -> None:
    """Apply an incoming ``SYNC_LAYERS`` payload: reconcile the stack and repaint."""
    image = message.get("image")
    image_id = message.get("image_id", "")
    width = message.get("w", 0) or 0
    height = message.get("h", 0) or 0
    layers = message.get("layers", [])
    if not image:
        print("[Layer] SYNC_LAYERS missing image")
        return

    if int(width) <= 0 or int(height) <= 0:
        return
    if image_id:
        from .blender_integration import find_image
        bound = find_image(image_id, image)
        if bound is not None:
            image = bound.name
    stack = scene.pixelorama_layers
    state = scene.pixelorama_layer_state
    requested_active_id = message.get("active_layer_id", "")
    active_id = requested_active_id or (
        stack[state.active_layer].layer_id if 0 <= state.active_layer < len(stack) else ""
    )
    state.active_image = image
    for entry in stack:
        if image_id and entry.image_id == image_id:
            entry.image_name = image
    incoming_ids = {l.get("id") for l in layers if l.get("id")}

    # Remove layers no longer present
    for i in range(len(stack) - 1, -1, -1):
        if stack[i].image_name == image and stack[i].layer_id not in incoming_ids:
            # Retain detached images: they may still be used by other materials.
            stack.remove(i)

    seen = {l.layer_id for l in stack if l.image_name == image}
    for l in layers:
        lid = l.get("id")
        if not lid:
            continue
        if lid not in seen:
            entry = stack.add()
            entry.layer_id = lid
            entry.image_name = image
            seen.add(lid)
        else:
            entry = next(e for e in stack if e.layer_id == lid and e.image_name == image)
        entry.name = l.get("name", lid)
        entry.image_id = image_id
        entry.order_index = l.get("index", 0)
        entry.opacity = float(l.get("opacity", 1.0))
        entry.visible = bool(l.get("visible", True))
        mode = l.get("blend_mode", "normal")
        entry.blend_mode = mode if mode in {m[0] for m in BLEND_MODES} else "normal"
        entry.image_size = (int(width), int(height))
        entry.frame = int(message.get("frame", 0))
        entry.source = "pixelorama"
        revision = int(l.get("revision", message.get("revision", 0)))
        fp = l.get("file_path")
        if fp and revision >= entry.revision:
            _load_layer_pixels(image, lid, entry.name, fp, width, height, image_id)
            entry.revision = revision

    state.active_layer = next((i for i, layer in enumerate(stack)
                               if layer.image_name == image and layer.layer_id == active_id),
                              next((i for i, layer in enumerate(stack) if layer.image_name == image), -1))
    recompute_preview(scene, image, int(width), int(height))
    preview = bpy.data.images.get(image)
    if preview is not None and image_id:
        preview["blendlorama_id"] = image_id
    from . import layer_material
    layer_material.refresh(scene, image)
    paint = scene.tool_settings.image_paint
    canvas = paint.canvas
    # On first link, replace the composite paint target with the selected layer
    # when the active material already uses this project's original texture.
    obj = bpy.context.object
    material = obj.active_material if obj else None
    paints_composite = (
        paint.mode == "IMAGE" and canvas is not None and canvas.name == image
    ) or (
        paint.mode == "MATERIAL" and material is not None
        and any(img is not None and img.name == image for img in material.texture_paint_images)
    )
    if requested_active_id or paints_composite or (canvas and canvas.get("blendlorama_layer")):
        if state.active_layer >= 0:
            bpy.ops.pixelorama_layer.set_active(index=state.active_layer)
        else:
            scene.tool_settings.image_paint.canvas = None


def apply_active_layer(scene, message) -> None:
    """Follow Pixelorama's active layer without retransmitting its pixels."""
    image_id = message.get("image_id", "")
    layer_id = message.get("id", "")
    frame = int(message.get("frame", -1))
    index = next((index for index, layer in enumerate(scene.pixelorama_layers)
                  if layer.layer_id == layer_id and
                  ((image_id and layer.image_id == image_id) or
                   layer.image_name == message.get("image", ""))), -1)
    if index < 0:
        return
    layer = scene.pixelorama_layers[index]
    # A frame change is followed by SYNC_LAYERS; keep the old canvas until then.
    if frame >= 0 and layer.frame != frame:
        return
    bpy.ops.pixelorama_layer.set_active(index=index)


def apply_sync_layer(scene, message) -> None:
    """Apply one changed Pixelorama cel without rebuilding the entire stack."""
    image = message.get("image", "")
    image_id = message.get("image_id", "")
    layer_id = message.get("id", "")
    entry = next((layer for layer in scene.pixelorama_layers
                  if layer.layer_id == layer_id and
                  ((image_id and layer.image_id == image_id) or layer.image_name == image)), None)
    if entry is None:
        raise ValueError("unknown layer; request a full layer sync")
    image = entry.image_name
    revision = int(message.get("revision", 0))
    if revision and revision <= entry.revision:
        return
    width = int(message.get("w", entry.image_size[0]))
    height = int(message.get("h", entry.image_size[1]))
    file_path = message.get("file_path", "")
    if not file_path:
        raise ValueError("SYNC_LAYER requires file_path")
    _load_layer_pixels(image, layer_id, entry.name, file_path, width, height, entry.image_id)
    entry.frame = int(message.get("frame", entry.frame))
    entry.image_size = (width, height)
    entry.revision = revision
    entry.source = "pixelorama"
    recompute_preview(scene, image, width, height)
    from . import layer_material
    layer_material.refresh(scene, image)


def apply_layer_state(scene, message) -> None:
    message = dict(message)
    stack = _stack_for_image(scene, message.get("image", ""))
    if stack:
        message.setdefault("w", stack[0].image_size[0])
        message.setdefault("h", stack[0].image_size[1])
        message.setdefault("frame", stack[0].frame)
    apply_sync_layers(scene, message)


def set_layer_lock(scene, message) -> None:
    """``LOCK_LAYER`` / ``UNLOCK_LAYER`` direction-lock bookkeeping."""
    image = message.get("image")
    lid = message.get("id")
    owner = message.get("owner")  # None => unlock
    if not image or not lid:
        return
    entry = next((e for e in scene.pixelorama_layers if e.layer_id == lid and e.image_name == image), None)
    if entry is None:
        return
    entry.source = owner if owner in ("blender", "pixelorama") else "pixelorama"


# registration of PropertyGroups is done in __init__.py
register_props = [
    PixeloramaLayer,
    LayerStackState,
]


def register():
    for cls in register_props:
        bpy.utils.register_class(cls)
    bpy.types.Scene.pixelorama_layers = CollectionProperty(type=PixeloramaLayer)
    bpy.types.Scene.pixelorama_layer_state = PointerProperty(type=LayerStackState)


def unregister():
    if hasattr(bpy.types.Scene, "pixelorama_layers"):
        del bpy.types.Scene.pixelorama_layers
    if hasattr(bpy.types.Scene, "pixelorama_layer_state"):
        del bpy.types.Scene.pixelorama_layer_state
    for cls in reversed(register_props):
        bpy.utils.unregister_class(cls)
