"""Generated layer preview materials; user-authored materials are never rebuilt."""
import bpy
from . import layer_model


def rebuild(mat, scene, name):
    preview = bpy.data.images.get(name)
    if preview is None:
        return
    signature = repr([(l.layer_id, l.name, l.visible, l.opacity, l.role)
                      for l in layer_model._stack_for_image(scene, name)])
    if mat.get("blendlorama_signature") == signature:
        return
    mat["blendlorama_signature"] = signature
    nodes = mat.node_tree.nodes
    if not mat.get("blendlorama_initialized"):
        # The operator creates a fresh material, so discard Blender's defaults once.
        nodes.clear()
        mat["blendlorama_initialized"] = True
    else:
        for node in list(nodes):
            if node.get("blendlorama_managed"):
                nodes.remove(node)

    def managed(node_type):
        node = nodes.new(node_type)
        node["blendlorama_managed"] = True
        return node

    shader = managed("ShaderNodeBsdfPrincipled")
    output = managed("ShaderNodeOutputMaterial")
    mat.node_tree.links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    links = mat.node_tree.links
    tex = managed("ShaderNodeTexImage")
    tex.image, tex.interpolation = preview, "Closest"
    tex.location = (-600, 200)
    links.new(tex.outputs["Color"], shader.inputs["Base Color"])
    links.new(tex.outputs["Alpha"], shader.inputs["Alpha"])
    # Independent paint targets stay in the material even when not part of emission.
    emission = None
    for i, layer in enumerate(layer_model._stack_for_image(scene, name)):
        node = managed("ShaderNodeTexImage")
        node.image = layer_model.find_layer_image(name, layer.layer_id, layer.image_id)
        node.label, node.interpolation = layer.name, "Closest"
        node.location = (-1000, -300 * i)
        if layer.role != "emission" or not layer.visible:
            continue
        mask = managed("ShaderNodeMath")
        mask.operation = "MULTIPLY"
        mask.inputs[1].default_value = layer.opacity
        links.new(node.outputs["Alpha"], mask.inputs[0])
        scale = managed("ShaderNodeVectorMath")
        scale.operation = "SCALE"
        links.new(node.outputs["Color"], scale.inputs[0])
        links.new(mask.outputs[0], scale.inputs[3])
        if emission is None:
            emission = scale.outputs[0]
        else:
            add = managed("ShaderNodeVectorMath")
            add.operation = "ADD"
            links.new(emission, add.inputs[0])
            links.new(scale.outputs[0], add.inputs[1])
            emission = add.outputs[0]
    if emission is not None:
        links.new(emission, shader.inputs["Emission Color"])
        shader.inputs["Emission Strength"].default_value = 1.0


def refresh(scene, name):
    for mat in bpy.data.materials:
        if mat.get("blendlorama_image") == name and mat.use_nodes:
            rebuild(mat, scene, name)
