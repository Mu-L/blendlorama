import bpy

from .blender_integration import clear_pending_events, process_pending_events, setup_blender_integration
from .image_manager import ImageManager
from .watch import UvWatch, ImagesStateWatch
from .operators import (
    LAYER_OT_apply_material,
    LAYER_OT_export,
    LAYER_OT_recompute_preview,
    LAYER_OT_set_active,
    SERVER_OT_start,
    SERVER_OT_stop,
    WORLD_OT_setup_grid,
)
from .server import set_callbacks, stop_server
from .texture_processor import (
    TEXTURE_OT_check_texture,
    TEXTURE_OT_create_checker_texture,
)
from .ui import (
    WS_PT_LayerPanel,
    WS_PT_ServerPanel,
    WS_PT_TextureToolsPanel,
    WS_PT_UVToolsPanel,
    WS_PT_WorldGridPanel,
)
from .unwrap_tools import UV_OT_unwrap_pixel_perfect, UV_OT_unwrap_to_grid
from .layer_watch import LayerWatch
from . import layer_model

classes = (
    SERVER_OT_start,
    SERVER_OT_stop,
    WORLD_OT_setup_grid,
    LAYER_OT_set_active,
    LAYER_OT_apply_material,
    LAYER_OT_export,
    LAYER_OT_recompute_preview,
    WS_PT_ServerPanel,
    WS_PT_UVToolsPanel,
    WS_PT_TextureToolsPanel,
    WS_PT_WorldGridPanel,
    WS_PT_LayerPanel,
    UV_OT_unwrap_pixel_perfect,
    UV_OT_unwrap_to_grid,
    TEXTURE_OT_check_texture,
    TEXTURE_OT_create_checker_texture,
)


def register_scene_properties():
    bpy.types.Scene.pixel_checker_texture_size = bpy.props.IntProperty(
        name="Checker Texture Size",
        default=64,
        min=8,
        max=2048,
    )
    bpy.types.Scene.world_grid_subdivisions = bpy.props.IntProperty(
        name="Grid Subdivisions",
        default=16,
        min=1,
        max=64,
    )


def unregister_scene_properties():
    if hasattr(bpy.types.Scene, "pixel_checker_texture_size"):
        del bpy.types.Scene.pixel_checker_texture_size
    if hasattr(bpy.types.Scene, "world_grid_subdivisions"):
        del bpy.types.Scene.world_grid_subdivisions


def register():
    register_scene_properties()
    layer_model.register()
    setup_blender_integration()
    ImageManager()
    UvWatch()
    ImagesStateWatch()
    LayerWatch()

    if not bpy.app.timers.is_registered(UvWatch.instance.check_for_changes):
        bpy.app.timers.register(
            UvWatch.instance.check_for_changes, first_interval=0.5, persistent=True
        )

    if not bpy.app.timers.is_registered(ImagesStateWatch.instance.check_for_changes):
        bpy.app.timers.register(
            ImagesStateWatch.instance.check_for_changes,
            first_interval=0.5,
            persistent=True,
        )

    if not bpy.app.timers.is_registered(LayerWatch.instance.check_for_changes):
        bpy.app.timers.register(
            LayerWatch.instance.check_for_changes,
            first_interval=0.4,
            persistent=True,
        )

    if not bpy.app.timers.is_registered(process_pending_events):
        bpy.app.timers.register(
            process_pending_events, first_interval=0.05, persistent=True
        )

    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    stop_server()

    if bpy.app.timers.is_registered(process_pending_events):
        bpy.app.timers.unregister(process_pending_events)

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

    if UvWatch.instance and bpy.app.timers.is_registered(UvWatch.instance.check_for_changes):
        bpy.app.timers.unregister(UvWatch.instance.check_for_changes)
    if ImagesStateWatch.instance and bpy.app.timers.is_registered(ImagesStateWatch.instance.check_for_changes):
        bpy.app.timers.unregister(ImagesStateWatch.instance.check_for_changes)
    if LayerWatch.instance and bpy.app.timers.is_registered(LayerWatch.instance.check_for_changes):
        bpy.app.timers.unregister(LayerWatch.instance.check_for_changes)

    import shutil
    if LayerWatch.instance:
        shutil.rmtree(LayerWatch.instance._tempdir, ignore_errors=True)
    LayerWatch.instance = None
    UvWatch.instance = None
    ImagesStateWatch.instance = None
    ImageManager.reset()
    clear_pending_events()
    set_callbacks()
    layer_model.unregister()
    unregister_scene_properties()


if __name__ == "__main__":
    register()
