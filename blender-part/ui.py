import bpy

from .server import get_server_status


class WS_PT_ServerPanel(bpy.types.Panel):
    bl_label = "Sync Server"
    bl_category = "PixeloramaSync"  # Right sidebar tab name
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"

    def draw(self, context):
        layout = self.layout
        if layout:
            # Server status section
            box = layout.box()
            box.label(text="SyncServer Status", icon="SETTINGS")

            status = get_server_status()
            if status["running"]:
                status_text = f"Status: Running"
                status_icon = "PLAY"
            else:
                status_text = "Status: Stopped"
                status_icon = "PAUSE"

            box.label(text=status_text, icon=status_icon)
            if status["running"]:
                box.label(text=status["address"], icon="URL")
                box.label(text=f"Clients: {status['clients_count']}")
            elif status["error"]:
                box.label(text=status["error"], icon="ERROR")
            if not bpy.app.online_access:
                box.label(text="Enable Online Access in Preferences > System", icon="ERROR")
            # Control buttons
            layout.separator()
            layout.operator("server.start", text="Start Server")
            layout.operator("server.stop", text="Stop Server")


class WS_PT_UVToolsPanel(bpy.types.Panel):
    bl_label = "UV Tools"
    bl_category = "PixeloramaSync"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"

    def draw(self, context):
        layout = self.layout
        if layout:
            scene = context.scene
            box = layout.box()
            box.label(text="UV Unwrapping", icon="GROUP_UVS")

            from .unwrap_tools import resolve_target_image
            target = resolve_target_image(context)
            if target:
                box.label(text=f"Target: {target.name}", icon="IMAGE_DATA")
                box.label(text=f"Size: {target.size[0]} x {target.size[1]}")
            else:
                box.label(text="No Target Texture", icon="ERROR")

            layout.separator()
            layout.prop(scene, "pixel_uv_density", text="Density")
            layout.prop(scene, "pixel_uv_padding", text="Padding")
            layout.prop(scene, "pixel_uv_snap", text="Snap")
            unwrap = layout.operator("uv.unwrap_pixel_perfect", text="Pixel Perfect Unwrap")
            unwrap.target_density = scene.pixel_uv_density
            unwrap.padding = scene.pixel_uv_padding
            unwrap.snap_to_pixels = scene.pixel_uv_snap

            layout.separator()
            layout.prop(scene, "pixel_grid_face_size", text="Face Size")
            grid = layout.operator("uv.unwrap_to_grid", text="Face Grid Layout")
            grid.grid_size = scene.pixel_grid_face_size
            grid.padding = scene.pixel_uv_padding

            # Info text
            layout.separator()
            box = layout.box()
            box.label(text="Select faces in Edit Mode", icon="INFO")
            box.label(text="before using UV tools.")


class WS_PT_TextureToolsPanel(bpy.types.Panel):
    bl_label = "Texture Tools"
    bl_category = "PixeloramaSync"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"

    def draw(self, context):
        layout = self.layout
        if layout:
            box = layout.box()
            box.label(text="Create Checker Texture", icon="IMAGE_DATA")

            # Texture size slider
            row = layout.row()
            row.prop(context.scene, "pixel_checker_texture_size", text="Size")

            # Create button
            layout.operator(
                "texture.create_checker_texture", text="Create & Apply Checker"
            )

            # Info text
            layout.separator()
            box = layout.box()
            box.label(text="Select object to apply texture", icon="INFO")


class WS_PT_WorldGridPanel(bpy.types.Panel):
    bl_label = "World Grid"
    bl_category = "PixeloramaSync"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"

    def draw(self, context):
        layout = self.layout
        if layout:
            box = layout.box()
            box.label(text="World Grid Settings", icon="GRID")

            # Subdivisions slider
            row = layout.row()
            row.prop(context.scene, "world_grid_subdivisions", text="Subdivisions")

            # Setup button
            layout.operator("world.setup_grid", text="Setup World Grid")

            # Info text
            layout.separator()
            box = layout.box()
            box.label(text="Sets grid scale to 0.0625", icon="INFO")
            box.label(text="and units to NONE")


class WS_PT_LayerPanel(bpy.types.Panel):
    bl_label = "Layers"
    bl_category = "PixeloramaSync"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        state = scene.pixelorama_layer_state
        stack = scene.pixelorama_layers

        row = layout.row(align=True)
        active = state.active_image or "(none)"
        row.label(text=f"Image: {active}")
        row.operator("pixelorama_layer.recompute", text="", icon="FILE_REFRESH")

        if len(stack) == 0:
            box = layout.box()
            box.label(text="No layers yet", icon="INFO")
            box.label(text="Connect and receive layers from Pixelorama")
            return

        rows = layout.column(align=True)
        for i, layer in sorted(enumerate(stack), key=lambda item: -item[1].order_index):
            if layer.image_name != state.active_image:
                continue
            base = rows.row(align=True)
            icon = "RESTRICT_VIEW_OFF" if layer.visible else "RESTRICT_VIEW_ON"
            base.label(text="", icon=icon)
            base.label(text=layer.name or layer.layer_id[:8])
            from .layer_model import find_layer_image
            paint = scene.tool_settings.image_paint
            target = find_layer_image(layer.image_name, layer.layer_id, layer.image_id)
            painting = paint.mode == "IMAGE" and target is not None and paint.canvas == target
            # Always clickable, including the initially selected / only layer.
            base.operator("pixelorama_layer.set_active", text="Paint", icon="BRUSH_DATA",
                          depress=painting).index = i
            base.label(text=f"{layer.opacity:.0%} {layer.blend_mode}")
            base.prop(layer, "role", text="")

        layout.operator("pixelorama_layer.apply_material")
        layout.prop(scene, "pixel_export_bleed", text="Export Bleed")
        layout.operator("pixelorama_layer.export")
        layout.label(text="Manage layer structure in Pixelorama", icon="INFO")


# Keep the old panel name for backward compatibility
WS_PT_Panel = WS_PT_ServerPanel
