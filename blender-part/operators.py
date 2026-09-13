import bpy

from . import server


class WORLD_OT_setup_grid(bpy.types.Operator):
    bl_idname = "world.setup_grid"
    bl_label = "Setup World Grid"
    bl_description = "Set up world grid with scale 0.0625 and subdivisions"

    @classmethod
    def poll(cls, context):
        return True

    def execute(self, context):
        # Get subdivisions from scene property
        subdivisions = context.scene.world_grid_subdivisions

        # Set viewport grid
        for space in context.area.spaces:
            if space.type == "VIEW_3D":
                space.overlay.grid_scale = 0.0625
                space.overlay.grid_subdivisions = subdivisions
                break

        # Set scene units to None (metric)
        context.scene.unit_settings.system = "NONE"
        context.scene.unit_settings.scale_length = 0.0625

        self.report(
            {"INFO"}, f"World grid set up: scale=0.0625, subdivisions={subdivisions}"
        )
        return {"FINISHED"}


class SERVER_OT_start(bpy.types.Operator):
    bl_idname = "server.start"
    bl_label = "Start Server"

    @classmethod
    def poll(cls, context):
        return True

    def execute(self, context):
        if not bpy.app.online_access:
            self.report(
                {"ERROR"},
                "Online Access is disabled in Blender Preferences > System",
            )
            return {"CANCELLED"}
        started, error = server.start_server()
        if not started:
            self.report({"ERROR"}, f"Server could not start: {error}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Server started at ws://{server.HOST}:{server.PORT}")
        return {"FINISHED"}


class SERVER_OT_stop(bpy.types.Operator):
    bl_idname = "server.stop"
    bl_label = "Stop Server"

    @classmethod
    def poll(cls, context):
        # Ensure availability in correct context
        return True  # or your condition check

    def execute(self, context):
        self.report({"INFO"}, "Server stopped")
        server.stop_server()
        return {"FINISHED"}


class LAYER_OT_set_active(bpy.types.Operator):
    bl_idname = "pixelorama_layer.set_active"
    bl_label = "Set Active Layer"
    bl_description = "Set the active layer for painting and editing"
    bl_options = {"INTERNAL"}

    index: bpy.props.IntProperty()  # type: ignore[assignment]

    @classmethod
    def poll(cls, context):
        return hasattr(context.scene, "pixelorama_layers")

    def execute(self, context):
        stack = context.scene.pixelorama_layers
        if 0 <= self.index < len(stack):
            from . import layer_model
            layer = stack[self.index]
            state = context.scene.pixelorama_layer_state
            state.active_layer = self.index
            state.active_image = layer.image_name
            image = layer_model.find_layer_image(layer.image_name, layer.layer_id, layer.image_id)
            if image is None:
                self.report({"ERROR"}, "The layer image is missing; sync from Pixelorama again")
                return {"CANCELLED"}
            context.scene.tool_settings.image_paint.mode = "IMAGE"
            context.scene.tool_settings.image_paint.canvas = image
            for screen in bpy.data.screens:
                for area in screen.areas:
                    if area.type == "IMAGE_EDITOR":
                        area.spaces.active.image = image
            obj = context.object
            if obj and obj.active_material and obj.active_material.use_nodes:
                nodes = obj.active_material.node_tree.nodes
                for node in nodes:
                    node.select = False
                for node in nodes:
                    if node.type == "TEX_IMAGE" and node.image == image:
                        node.select = True
                        nodes.active = node
                        break
        return {"FINISHED"}


class LAYER_OT_recompute_preview(bpy.types.Operator):
    bl_idname = "pixelorama_layer.recompute"
    bl_label = "Recompute Preview"
    bl_description = "Recomposite the layer preview manually"

    def execute(self, context):
        from . import layer_model

        image = context.scene.pixelorama_layer_state.active_image
        w = h = 0
        layers = layer_model._stack_for_image(context.scene, image)
        if layers:
            w, h = layers[0].image_size
        layer_model.recompute_preview(context.scene, image, w, h)
        return {"FINISHED"}


class LAYER_OT_apply_material(bpy.types.Operator):
    bl_idname = "pixelorama_layer.apply_material"
    bl_label = "Apply Unlit Layer Material"
    bl_description = "Create an unlit pixel-art preview material on the active mesh"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == "MESH" and bool(context.scene.pixelorama_layer_state.active_image)

    def execute(self, context):
        from . import layer_model
        name = context.scene.pixelorama_layer_state.active_image
        preview = bpy.data.images.get(name)
        if preview is None:
            return {"CANCELLED"}
        mat = bpy.data.materials.new(f"Pixelorama {name}")
        mat.use_nodes = True
        mat["blendlorama_image"] = name
        from . import layer_material
        layer_material.rebuild(mat, context.scene, name)
        obj = context.object
        if obj.active_material is None:
            obj.data.materials.append(mat)
        else:
            obj.active_material = mat
        return {"FINISHED"}


class LAYER_OT_export(bpy.types.Operator):
    bl_idname = "pixelorama_layer.export"
    bl_label = "Export Layer PNGs + Manifest"
    bl_description = "Export independent RGBA textures and layer metadata for game engines"

    directory: bpy.props.StringProperty(subtype="DIR_PATH")

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        import json
        import hashlib
        from pathlib import Path
        from . import layer_model
        from .layer_watch import save_png
        name = context.scene.pixelorama_layer_state.active_image
        layers = layer_model._stack_for_image(context.scene, name)
        if not layers or not self.directory:
            return {"CANCELLED"}
        folder = Path(bpy.path.abspath(self.directory)) / hashlib.sha256(name.encode()).hexdigest()[:12]
        folder.mkdir(parents=True, exist_ok=True)
        manifest = {"version": 1, "image": name, "order": "bottom_to_top", "layers": []}
        try:
            for layer in sorted(layers, key=lambda item: item.order_index):
                img = layer_model.find_layer_image(name, layer.layer_id, layer.image_id)
                if img is None:
                    raise ValueError(f"Missing image for {layer.name}")
                filename = hashlib.sha256(layer.layer_id.encode()).hexdigest()[:16] + ".png"
                save_png(img, str(folder / filename))
                manifest["layers"].append({"id": layer.layer_id, "name": layer.name,
                    "file": filename, "role": layer.role, "visible": layer.visible,
                    "opacity": layer.opacity, "blend_mode": layer.blend_mode,
                    "frame": layer.frame, "size": list(img.size)})
            (folder / "layers.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Exported {len(layers)} layers to {folder}")
        return {"FINISHED"}
