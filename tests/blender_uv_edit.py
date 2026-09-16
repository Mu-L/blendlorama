"""Run with: blender -b --factory-startup --python-exit-code 1 --python tests/blender_uv_edit.py"""
import importlib.util
import sys
from pathlib import Path
import bmesh
import bpy

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('pixel_uv_test', root / 'blender-part/__init__.py', submodule_search_locations=[str(root / 'blender-part')])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()
from pixel_uv_test.uv_edit_tools import _stretch, _selection, _edge_faces, _face_problem
from pixel_uv_test.unwrap_tools import UnwrapTools

target = bpy.data.images.new('LegAtlas', 96, 64, alpha=True)
bpy.context.scene.pixelorama_layer_state.active_image = target.name
mesh = bpy.data.meshes.new('Tapered quad strip')
model = bmesh.new()
rows = ((-1.0, -0.25, 0.25, 1.0), (-0.8, -0.2, 0.2, 0.8), (-0.55, -0.14, 0.14, 0.55))
verts = [[model.verts.new((x, float(row), 0.0)) for x in xs] for row, xs in enumerate(rows)]
for row in range(2):
    for col in range(3):
        model.faces.new((verts[row][col], verts[row][col + 1], verts[row + 1][col + 1], verts[row + 1][col]))
model.faces.ensure_lookup_table()
uv_layer = model.loops.layers.uv.verify()
for face in model.faces:
    face.select = True
    for loop in face.loops:
        x, y = loop.vert.co.x, loop.vert.co.y
        loop[uv_layer].uv = (0.36 + 0.13 * x + 0.011 * y, 0.14 + 0.14 * y + 0.015 * x)
model.to_mesh(mesh)
model.free()
obj = bpy.data.objects.new('Tapered Leg', mesh)
bpy.context.collection.objects.link(obj)
bpy.ops.object.select_all(action='DESELECT')
bpy.context.view_layer.objects.active = obj
obj.select_set(True)
bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.mesh.select_all(action='SELECT')
bm = bmesh.from_edit_mesh(mesh)
bm.faces.ensure_lookup_table()
uv_layer = bm.loops.layers.uv.verify()
size = (96, 64)

def coordinates():
    return [(loop[uv_layer].uv.x, loop[uv_layer].uv.y) for face in bm.faces for loop in face.loops]

def select_uv_vert(loop, selected):
    if hasattr(loop, 'uv_select_vert_set'):
        loop.uv_select_vert_set(selected)
    else:
        loop[uv_layer].select = selected

def select_uv_edge(loop, selected):
    if hasattr(loop, 'uv_select_edge_set'):
        loop.uv_select_edge_set(selected)
    else:
        loop[uv_layer].select_edge = selected

def expect_rejection(call, message):
    before = coordinates()
    try:
        assert call() == {'CANCELLED'}
    except RuntimeError as exc:
        assert message in str(exc), exc
    assert coordinates() == before

assert bpy.ops.uv.pixel_smart_straighten(density=8.0, max_stretch=2.5, padding=2) == {'FINISHED'}
UnwrapTools.validate(list(bm.faces), uv_layer, size)
for face in bm.faces:
    pixels = [(round(loop[uv_layer].uv.x * 96), round(loop[uv_layer].uv.y * 64)) for loop in face.loops]
    assert len({x for x, y in pixels}) == 2, pixels
    assert len({y for x, y in pixels}) == 2, pixels
    assert all(abs(loop[uv_layer].uv.x * 96 - round(loop[uv_layer].uv.x * 96)) < 1e-4 for loop in face.loops)
assert max(_stretch(face, uv_layer, size, obj.matrix_world) for face in bm.faces) <= 2.5
straight = coordinates()
expect_rejection(lambda: bpy.ops.uv.pixel_smart_straighten(density=8.0, max_stretch=1.0, padding=2), 'stretch')
assert coordinates() == straight
expect_rejection(lambda: bpy.ops.uv.pixel_smart_straighten(density=80.0, max_stretch=20.0, padding=2), 'Target Texture')
for face in bm.faces:
    for loop in face.loops:
        loop[uv_layer].uv.x += 0.21 / 96
        loop[uv_layer].uv.y += 0.18 / 64
assert _face_problem(bm.faces[0], uv_layer, size, obj.matrix_world, 2.5) == 'off_grid'
assert bpy.ops.uv.pixel_align_selected() == {'FINISHED'}
assert all(abs(loop[uv_layer].uv.x * 96 - round(loop[uv_layer].uv.x * 96)) < 1e-4 for face in bm.faces for loop in face.loops)
pinned = bm.faces[0].loops[0]
pinned[uv_layer].pin_uv = True
pinned[uv_layer].uv.x += 0.4 / 96
pre_pin = coordinates()
expect_rejection(lambda: bpy.ops.uv.pixel_align_selected(), 'Pinned UV')
assert coordinates() == pre_pin
pinned[uv_layer].pin_uv = False
pinned[uv_layer].uv.x -= 0.4 / 96
bpy.context.scene.tool_settings.use_uv_select_sync = True
for edge in bm.edges:
    edge.select = all(abs(v.co.y) < 1e-6 for v in edge.verts)
for face in bm.faces:
    for loop in face.loops:
        if abs(loop.vert.co.y) < 1e-6 and loop.vert.co.x < 0:
            loop[uv_layer].uv.y += 0.3 / 64
assert bpy.ops.uv.pixel_straighten_edge(axis='U') == {'FINISHED'}
chain_y = {round(loop[uv_layer].uv.y * 64, 4) for face in bm.faces for loop in face.loops if abs(loop.vert.co.y) < 1e-6}
assert len(chain_y) == 1, chain_y
assert abs(next(iter(chain_y)) - round(next(iter(chain_y)))) < 1e-4
UnwrapTools.validate(list(bm.faces), uv_layer, size)
assert bpy.ops.uv.pixel_align_selected() == {'FINISHED'}
old_bounds = [island.pixel_bounds(*size) for island in UnwrapTools.get_islands_for_faces(bm, list(bm.faces), uv_layer)]
assert bpy.ops.uv.pixel_pack_selected(padding=2) == {'FINISHED'}
new_bounds = [island.pixel_bounds(*size) for island in UnwrapTools.get_islands_for_faces(bm, list(bm.faces), uv_layer)]
assert sorted((c-a,d-b) for a,b,c,d in old_bounds) == sorted((c-a,d-b) for a,b,c,d in new_bounds)

# Moving only five faces of a welded six-face UV island must not split its boundary.
bm.faces[0].select = False
expect_rejection(lambda: bpy.ops.uv.pixel_pack_selected(padding=2), 'whole UV island')
expect_rejection(lambda: bpy.ops.uv.pixel_smart_straighten(density=8.0, max_stretch=2.5, padding=2), 'whole UV island')
bm.faces[0].select = True

# UV editor with selection sync disabled must use only explicit UV selections.
area = next(area for area in bpy.context.screen.areas if area.type == 'VIEW_3D')
area.type = 'IMAGE_EDITOR'
area.ui_type = 'UV'
bpy.context.scene.tool_settings.use_uv_select_sync = False
for face in bm.faces:
    for loop in face.loops:
        select_uv_vert(loop, False)
        select_uv_edge(loop, False)
for loop in bm.faces[0].loops:
    select_uv_vert(loop, True)
with bpy.context.temp_override(area=area):
    assert _selection(bm, uv_layer, bpy.context) == [bm.faces[0]]
for loop in bm.faces[0].loops:
    select_uv_vert(loop, False)
with bpy.context.temp_override(area=area):
    assert _selection(bm, uv_layer, bpy.context) == []
for face in bm.faces:
    for loop in face.loops:
        if loop.edge.select:
            select_uv_edge(loop, True)
with bpy.context.temp_override(area=area):
    assert len(_edge_faces(bm, uv_layer, bpy.context)) == len(bm.faces)
for face in bm.faces:
    for loop in face.loops:
        select_uv_vert(loop, True)
with bpy.context.temp_override(area=area):
    assert bpy.ops.uv.pixel_smart_straighten(density=8.0, max_stretch=2.5,
                                             padding=2, direction='V') == {'FINISHED'}
assert all(abs(loop[uv_layer].uv.x * 96 - round(loop[uv_layer].uv.x * 96)) < 1e-4
           for face in bm.faces for loop in face.loops)
print('PASS: tapered quad grid, integer UVs, stretch rollback, pinned UV, edge chain, size-preserving pack')
