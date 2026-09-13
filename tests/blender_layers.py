"""Run: blender -b --factory-startup --python-exit-code 1 --python tests/blender_layers.py"""
import importlib.util
import hashlib
import sys
import tempfile
import threading
from pathlib import Path

import bpy
import numpy as np

root = Path(__file__).resolve().parents[1]
# Standalone source testing (installed extensions get wheels via Blender).
for wheel in (root / 'blender-part/wheels').glob('*.whl'):
    sys.path.append(str(wheel))
spec = importlib.util.spec_from_file_location('blendlorama_test', root / 'blender-part/__init__.py', submodule_search_locations=[str(root / 'blender-part')])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()
from blendlorama_test import layer_model as lm, layer_watch as lw
from blendlorama_test import blender_integration as bi
scene = bpy.context.scene
folder = Path(tempfile.mkdtemp())

def png(name, color):
    img = bpy.data.images.new(name, 4, 4, alpha=True)
    img.pixels[:] = list(color) * 16
    path = str(folder / (name + '.png'))
    lw.save_png(img, path)
    bpy.data.images.remove(img)
    return path

red, blue = png('red', (1, 0, 0, 1)), png('blue', (0, 0, 1, .5))
CANVAS_ID = hashlib.md5(b'Canvas').hexdigest()
OTHER_ID = hashlib.md5(b'Other').hexdigest()
def packet(image='Canvas', visible=True, image_id=None, active_layer_id='a'):
    return dict(type='SYNC_LAYERS', protocol_version=1, image=image,
                image_id=image_id or hashlib.md5(image.encode()).hexdigest(),
                active_layer_id=active_layer_id, revision=1, w=4, h=4, frame=2, layers=[
        dict(id='a', name='Base', index=0, file_path=red),
        dict(id='b', name='Glow', index=1, file_path=blue, visible=visible)])

# First sync must select an actual paint canvas, not just a UI index.
original = bpy.data.images.new('Canvas', 4, 4, alpha=True)
mat = bpy.data.materials.new('Original material')
mat.use_nodes = True
node = mat.node_tree.nodes.new('ShaderNodeTexImage')
node.image = original
bpy.context.object.active_material = mat
scene.tool_settings.image_paint.mode = 'MATERIAL'
lm.apply_sync_layers(scene, packet())
assert scene.tool_settings.image_paint.mode == 'IMAGE'
assert scene.tool_settings.image_paint.canvas.name == 'PX | Base | Canvas'
assert len(scene.pixelorama_layers) == 2
np.testing.assert_allclose(bpy.data.images['Canvas'].pixels[:4], [.5, 0, .5, 1], atol=.01)
lm.apply_sync_layers(scene, packet(visible=False))
assert len(scene.pixelorama_layers) == 2
np.testing.assert_allclose(bpy.data.images['Canvas'].pixels[:4], [1, 0, 0, 1], atol=.01)
identity = lm.find_layer_image('Canvas', 'b', CANVAS_ID).as_pointer()
msg = packet(); msg['layers'][1].update(name='Renamed', index=-1)
lm.apply_sync_layers(scene, msg)
assert lm.find_layer_image('Canvas', 'b', CANVAS_ID).as_pointer() == identity
assert lm.find_layer_image('Canvas', 'b', CANVAS_ID).name == 'PX | Renamed | Canvas'
lm.apply_sync_layers(scene, packet('Other'))
assert len(scene.pixelorama_layers) == 4
lm.apply_sync_layers(scene, packet())
lm.apply_active_layer(scene, dict(image_id=CANVAS_ID, id='b', frame=2))
assert scene.tool_settings.image_paint.canvas.name == 'PX | Glow | Canvas'
scene.pixelorama_layers[1].role = 'emission'
bpy.ops.pixelorama_layer.apply_material()
generated_nodes = bpy.context.object.active_material.node_tree.nodes
assert bpy.context.object.active_material['blendlorama_material_version'] == 2
assert generated_nodes.get('Principled BSDF') is None
assert generated_nodes.get('Emission').inputs['Color'].is_linked
assert generated_nodes.get('Transparent BSDF') is not None
assert generated_nodes.get('Mix Shader').inputs[0].is_linked
custom = bpy.context.object.active_material.node_tree.nodes.new('ShaderNodeRGB')
custom.name = 'User Custom Node'
custom_pointer = custom.as_pointer()
scene.pixelorama_layers[1].role = 'color'
assert bpy.context.object.active_material.node_tree.nodes.get('User Custom Node').as_pointer() == custom_pointer
scene.pixelorama_layers[1].role = 'emission'
# Persist Blender roles too, reopen, then reconcile the same persistent IDs.
bpy.ops.wm.save_as_mainfile(filepath=str(folder / 'identity.blend'))
bpy.ops.wm.open_mainfile(filepath=str(folder / 'identity.blend'))
scene = bpy.context.scene
retained = lm.find_layer_image('Canvas', 'b', CANVAS_ID).as_pointer()
lm.apply_sync_layers(scene, packet())
assert scene.pixelorama_layers[1].role == 'emission'
assert lm.find_layer_image('Canvas', 'b', CANVAS_ID).as_pointer() == retained
# Blender image rename keeps the UUID binding and the existing per-layer datablocks.
bpy.data.images['Canvas'].name = 'CanvasRenamed'
lm.apply_sync_layers(scene, packet('CanvasRenamed', image_id=CANVAS_ID, active_layer_id='b'))
assert len(scene.pixelorama_layers) == 4
assert lm.find_layer_image('CanvasRenamed', 'b', CANVAS_ID).as_pointer() == retained
assert lm.find_layer_image('CanvasRenamed', 'b', CANVAS_ID).name == 'PX | Glow | CanvasRenamed'
assert sum(layer.image_name == 'CanvasRenamed' for layer in scene.pixelorama_layers) == 2
# Receiving must not echo; an edit outside the first scanline must send exactly once.
sent = []
lw.get_server_status = lambda: {'running': True, 'clients_count': 1}
lw.send_message = lambda message: (sent.append(message) or True)
watch = lw.LayerWatch.instance
watch.check_for_changes(); assert not sent
img = scene.tool_settings.image_paint.canvas
pixels = list(img.pixels); pixels[-4:] = [0, 1, 0, 1]; img.pixels[:] = pixels
watch.check_for_changes(); assert len(sent) == 1 and sent[0]['id'] == 'b' and sent[0]['frame'] == 2
watch.check_for_changes(); assert len(sent) == 1
lm.apply_sync_layers(scene, packet()); watch.check_for_changes(); assert len(sent) == 1
assert bpy.ops.pixelorama_layer.export(directory=str(folder)) == {'FINISHED'}
assert list(folder.glob('*/layers.json'))
# Transparent-background blend must not darken source RGB.
for mode in ('normal', 'multiply', 'screen', 'darken', 'lighten', 'overlay'):
    out = lm._blend_channel(np.zeros((1, 1, 4)), np.array([[[.8, .3, .1, .5]]]), mode, 1)
    np.testing.assert_allclose(out, [[[.8, .3, .1, .5]]])
msg = packet(); msg['layers'] = []
lm.apply_sync_layers(scene, msg)
assert len(scene.pixelorama_layers) == 2
assert lm.find_layer_image('Other', 'a', OTHER_ID)
# Network callbacks from a worker thread enqueue only; bpy changes happen when
# the main-thread timer drains the event.
queued = packet('Queued')
worker = threading.Thread(target=bi.on_message_received, args=('test-client', queued))
worker.start(); worker.join()
assert bpy.data.images.get('Queued') is None
bi.process_pending_events()
assert bpy.data.images.get('Queued') is not None
addon.unregister()
assert not hasattr(bpy.types.Scene, 'pixelorama_layers')
addon.register(); addon.unregister()
print('PASS: layer lifecycle, isolation, blending, paint target, echo suppression, export and registration')
