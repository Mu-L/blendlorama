"""Run: blender -b --factory-startup --python-exit-code 1 --python tests/blender_unwrap.py"""

import importlib.util
import sys
from pathlib import Path

import bmesh
import bpy


root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "blendlorama_uv_test",
    root / "blender-part/__init__.py",
    submodule_search_locations=[str(root / "blender-part")],
)
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()

from blendlorama_uv_test.unwrap_tools import UnwrapTools, resolve_target_image


target = bpy.data.images.new("CharacterAtlas", width=96, height=48, alpha=True)
bpy.context.scene.pixelorama_layer_state.active_image = target.name

bpy.ops.mesh.primitive_cube_add()
obj = bpy.context.object
obj.name = "ScaledCharacterPart"
obj.scale = (2.0, 0.75, 1.5)
material = bpy.data.materials.new("AtlasMaterial")
material.use_nodes = True
image_node = material.node_tree.nodes.new("ShaderNodeTexImage")
image_node.image = target
obj.data.materials.append(material)
assert resolve_target_image(bpy.context, obj).as_pointer() == target.as_pointer()

bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
result = bpy.ops.uv.unwrap_pixel_perfect(
    target_density=8.0, padding=2, snap_to_pixels=True)
assert result == {"FINISHED"}

bm = bmesh.from_edit_mesh(obj.data)
uv_layer = bm.loops.layers.uv.verify()
faces = list(bm.faces)
UnwrapTools.validate(faces, uv_layer, (96, 48))
density = UnwrapTools.texel_density(faces, uv_layer, (96, 48), obj.matrix_world)
assert 5.5 <= density <= 9.5, density
for face in faces:
    for loop in face.loops:
        uv = loop[uv_layer].uv
        assert abs(uv.x * 96 - round(uv.x * 96)) < 1e-4
        assert abs(uv.y * 48 - round(uv.y * 48)) < 1e-4

islands = UnwrapTools.get_islands_for_faces(bm, faces, uv_layer)
rectangles = []
for island in islands:
    xmin, ymin, xmax, ymax = island.pixel_bounds(96, 48)
    rectangles.append((xmin - 2, ymin - 2, xmax - xmin + 4, ymax - ymin + 4))
for index, first in enumerate(rectangles):
    for second in rectangles[index + 1:]:
        ax, ay, aw, ah = first
        bx, by, bw, bh = second
        assert not (ax < bx + bw and ax + aw > bx and ay < by + bh and ay + ah > by)

# The former placeholder now consumes Face Size and produces integer coordinates
# against the actual non-square target texture.
result = bpy.ops.uv.unwrap_to_grid(grid_size=4, padding=1)
assert result == {"FINISHED"}
for face in bm.faces:
    for loop in face.loops:
        uv = loop[uv_layer].uv
        assert abs(uv.x * 96 - round(uv.x * 96)) < 1e-4
        assert abs(uv.y * 48 - round(uv.y * 48)) < 1e-4
UnwrapTools.validate(faces, uv_layer, (96, 48))

print("PASS: target resolution, world-scale density, pixel snapping, padding, packing and face grid")
