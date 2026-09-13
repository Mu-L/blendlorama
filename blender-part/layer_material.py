"""Generated layer preview materials; user-authored materials are never rebuilt."""
import bpy
from . import layer_model

MATERIAL_SCHEMA_VERSION = 2


def rebuild(mat, scene, name):
    preview = bpy.data.images.get(name)
    if preview is None:
        return
    signature = repr((MATERIAL_SCHEMA_VERSION,
                      [(l.layer_id, l.name, l.visible, l.opacity, l.role)
                       for l in layer_model._stack_for_image(scene, name)]))
    if mat.get("blendlorama_signature") == signature:
        return
    mat["blendlorama_signature"] = signature
    mat["blendlorama_material_version"] = MATERIAL_SCHEMA_VERSION
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

    output = managed("ShaderNodeOutputMaterial")
    links = mat.node_tree.links
    tex = managed("ShaderNodeTexImage")
    tex.image, tex.interpolation = preview, "Closest"
    tex.location = (-600, 200)
    surface_color = tex.outputs["Color"]
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
        add = managed("ShaderNodeVectorMath")
        add.operation = "ADD"
        links.new(surface_color, add.inputs[0])
        links.new(emission, add.inputs[1])
        surface_color = add.outputs[0]

    # Pixel-art materials are intentionally unlit: the texture is the final
    # colour and must not change with scene lights. Emission-role layers are
    # added above the composite so HDR bloom can still distinguish them.
    unlit = managed("ShaderNodeEmission")
    transparent = managed("ShaderNodeBsdfTransparent")
    alpha_mix = managed("ShaderNodeMixShader")
    links.new(surface_color, unlit.inputs["Color"])
    unlit.inputs["Strength"].default_value = 1.0
    links.new(tex.outputs["Alpha"], alpha_mix.inputs[0])
    links.new(transparent.outputs["BSDF"], alpha_mix.inputs[1])
    links.new(unlit.outputs["Emission"], alpha_mix.inputs[2])
    links.new(alpha_mix.outputs["Shader"], output.inputs["Surface"])

    # Eevee needs a transparent render method for the Transparent BSDF to be
    # visible. Blender 4.5+ uses surface_render_method; keep the older fallback
    # for source compatibility with earlier Blender installations.
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "DITHERED"
    elif hasattr(mat, "blend_method"):
        mat.blend_method = "HASHED"


def refresh(scene, name):
    for mat in bpy.data.materials:
        if mat.get("blendlorama_image") == name and mat.use_nodes:
            rebuild(mat, scene, name)
