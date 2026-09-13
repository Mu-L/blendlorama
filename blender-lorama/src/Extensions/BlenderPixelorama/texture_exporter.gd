class_name TextureExporter
extends Node

# Blender<->Pixelorama blend mode mapping. Pixelorama stores its blend mode as
# an int; we translate to the string used on the wire protocol (and accepted by
# Blender's PixeloramaLayer blend_mode enum).
# Pixelorama 1.x BaseLayer.BlendModes. Unsupported modes are explicitly reported.
const BLEND_TO_WIRE := {
    0: "normal", 1: "erase", 2: "darken", 3: "multiply",
    6: "lighten", 7: "screen", 10: "overlay",
}
const PROTOCOL_VERSION := 1
const PROJECT_ID_META := "blendlorama_project_id"
const IMAGE_ID_META := "blendlorama_image_id"
const IMAGE_NAME_META := "blendlorama_image_name"
var export_revision := 0
var _identity_project: WeakRef
var _id_owners := {}
var _pending_files := {}
var _remote_revisions := {}

var extensions_api
var export_temp_dir: String
var export_temp_dir_relative: String
var remote_applying := false   # guard against echo when Blender pushes pixels in


func _ready() -> void:
	extensions_api = get_node_or_null("/root/ExtensionsApi")
	export_temp_dir = "user://tmp/blenderlorama_realtime/" + Crypto.new().generate_random_bytes(8).hex_encode()
	_ensure_export_directory()
	if extensions_api:
		pass


func _ensure_export_directory():
	var dir = DirAccess.open("user://")
	if dir:
		dir.make_dir_recursive(export_temp_dir)


# Multi-layer export: one PNG per pixel cel (including hidden layers) + a SYNC_LAYERS envelope.
func export_layers(image_name: String, image_id := ""):
	if not extensions_api:
		return null
	var project = extensions_api.project.current_project
	_ensure_layer_ids(project)
	var project_id := ensure_project_id(project)
	var frame = project.current_frame
	var current_frame = project.frames[frame]

	export_revision += 1
	var layers_meta := []
	for i in range(project.layers.size()):
		var layer = project.layers[i]
		if current_frame.cels.size() <= i:
			continue
		var cel = current_frame.cels[i]
		if cel.get_class_name() != "PixelCel":
			continue
		var cel_image = cel.get_image()
		if not cel_image:
			continue
		var lid = _stable_layer_id(layer, i)
		var png_name = "%s__%s__%d.png" % [project_id.substr(0, 12), lid, export_revision]
		var png_path = export_temp_dir.path_join(png_name)
		if cel_image.save_png(png_path) == OK:
			_track_temp(export_revision, png_path)
			layers_meta.append({
				"id": lid,
				"name": layer.name if layer.get("name") else ("Layer %d" % i),
				"index": i,
				"opacity": float(layer.opacity) * float(cel.opacity),
				"visible": layer.is_visible_in_hierarchy(),
				"blend_mode": _blend_to_wire(layer.blend_mode),
				"file_path": ProjectSettings.globalize_path(png_path),
				"revision": export_revision,
				"content_hash": _image_hash(cel_image),
			})
		else:
			push_error("[Blendlorama] per-layer png save failed: %d" % i)
			return null

	var sync_info := {
		"type": "SYNC_LAYERS",
		"protocol_version": PROTOCOL_VERSION,
		"project_id": project_id,
		"image": image_name,
		"image_id": image_id,
		"revision": export_revision,
		"frame": frame,
		"active_layer_id": _active_layer_id(project),
		"w": int(project.size.x),
		"h": int(project.size.y),
		"layers": layers_meta,
	}
	return sync_info


func active_layer_message(image_name: String, image_id: String):
	if not extensions_api:
		return null
	var project = extensions_api.project.current_project
	var layer_id := _active_layer_id(project)
	if layer_id.is_empty():
		return null
	return {
		"type": "ACTIVE_LAYER",
		"protocol_version": PROTOCOL_VERSION,
		"project_id": ensure_project_id(project),
		"image": image_name,
		"image_id": image_id,
		"id": layer_id,
		"frame": int(project.current_frame),
	}


func export_current_layer(image_name: String, image_id := ""):
	if not extensions_api:
		return null
	var project = extensions_api.project.current_project
	_ensure_layer_ids(project)
	var index := int(project.current_layer)
	var frame := int(project.current_frame)
	if index < 0 or index >= project.layers.size() or frame < 0 or frame >= project.frames.size():
		return null
	if index >= project.frames[frame].cels.size():
		return null
	var cel = project.frames[frame].cels[index]
	if cel.get_class_name() != "PixelCel" or not cel.get_image():
		return null
	var image: Image = cel.get_image()
	export_revision += 1
	var lid := _stable_layer_id(project.layers[index], index)
	var path := export_temp_dir.path_join("%s__%s__%d.png" % [ensure_project_id(project).substr(0, 12), lid, export_revision])
	if image.save_png(path) != OK:
		return null
	_track_temp(export_revision, path)
	return {
		"type": "SYNC_LAYER",
		"protocol_version": PROTOCOL_VERSION,
		"project_id": ensure_project_id(project),
		"image": image_name,
		"image_id": image_id,
		"id": lid,
		"frame": frame,
		"revision": export_revision,
		"content_hash": _image_hash(image),
		"w": int(project.size.x),
		"h": int(project.size.y),
		"file_path": ProjectSettings.globalize_path(path),
	}


# Legacy single-blended-image export, kept for the old SYNC_TEXTURE path / panels
# that still expect the merged result (e.g. the texture tools panel).
func export(image_name):
	if not extensions_api:
		return
	var project = extensions_api.project.current_project
	var current_frame = project.frames[project.current_frame]
	var blended_image = _create_blended_image(project, current_frame)
	var export_path = export_temp_dir.path_join("%s_current_frame.png" % image_name)
	if blended_image.save_png(export_path) == OK:
		var sync_info = {
			"type": "SYNC_TEXTURE",
			"protocol_version": PROTOCOL_VERSION,
			"project_id": ensure_project_id(project),
			"image": image_name,
			"project_size": project.size,
			"file_path": ProjectSettings.globalize_path(export_path)
		}
		print("Texture exported successfully: ", image_name)
		return sync_info
	else:
		print("Failed to export texture: ", image_name)
	return null


# Receive a single layer pushed from Blender: apply pixels to the matching cel
# and set the remote_applying guard so the texture-changed signal does not echo.
func apply_sync_layer(msg, project) -> bool:
	if msg.get("image_id", "") != bound_image_id(project):
		return false
	_ensure_layer_ids(project)
	var target_index := -1
	for i in range(project.layers.size()):
		if _stable_layer_id(project.layers[i], i) == msg.get("id", ""):
			target_index = i
			break
	var frame := int(msg.get("frame", -1))
	if target_index < 0 or frame < 0 or frame >= project.frames.size():
		return false
	if project.layers[target_index].locked:
		return false
	var revision := int(msg.get("revision", 0))
	var revision_key := "%s:%s:%d" % [ensure_project_id(project), msg.get("id", ""), frame]
	if revision > 0 and revision <= int(_remote_revisions.get(revision_key, 0)):
		return true
	var cel = project.frames[frame].cels[target_index]
	if cel.get_class_name() != "PixelCel":
		return false
	var img := Image.new()
	if img.load_png_from_buffer(FileAccess.get_file_as_bytes(msg.get("file_path", ""))) != OK:
		return false
	if img.get_size() != Vector2i(project.size):
		return false
	remote_applying = true
	# Public project API updates the canvas, cel thumbnail and undo history.
	extensions_api.project.set_pixelcel_image(img, frame, target_index)
	remote_applying = false
	_remote_revisions[revision_key] = revision
	return true


func ensure_project_id(project) -> String:
	var value = project.get_meta(PROJECT_ID_META, "")
	if value is String and value.length() == 32 and value.is_valid_hex_number(false):
		return value
	value = Crypto.new().generate_random_bytes(16).hex_encode()
	project.set_meta(PROJECT_ID_META, value)
	if project.get("has_changed") != null:
		project.has_changed = true
	return value


func bind_project(project, image_id: String, image_name: String) -> void:
	var changed := bound_image_id(project) != image_id or bound_image_name(project) != image_name
	project.set_meta(IMAGE_ID_META, image_id)
	project.set_meta(IMAGE_NAME_META, image_name)
	ensure_project_id(project)
	if changed and project.get("has_changed") != null:
		project.has_changed = true


func bound_image_id(project) -> String:
	return String(project.get_meta(IMAGE_ID_META, ""))


func bound_image_name(project) -> String:
	return String(project.get_meta(IMAGE_NAME_META, ""))


func acknowledge(message) -> void:
	var revision := int(message.get("revision", 0))
	if revision <= 0:
		return
	for path in _pending_files.get(revision, []):
		DirAccess.remove_absolute(path)
	_pending_files.erase(revision)


func _track_temp(revision: int, path: String) -> void:
	if not _pending_files.has(revision):
		_pending_files[revision] = []
	_pending_files[revision].append(path)
	var revisions := _pending_files.keys()
	revisions.sort()
	for old_revision in revisions.slice(0, maxi(0, revisions.size() - 8)):
		for old_path in _pending_files[old_revision]:
			DirAccess.remove_absolute(old_path)
		_pending_files.erase(old_revision)


func _ensure_layer_ids(project) -> void:
	# Project.serialize()/deserialize() persist layer metadata in .pxo. Keep the
	# existing key for compatibility with previously synced projects.
	if not _identity_project or _identity_project.get_ref() != project:
		_identity_project = weakref(project)
		_id_owners.clear()
	var owners := {}
	# Prefer the known original if a copy carrying the same metadata was inserted
	# before it. Weak references do not keep deleted layers alive.
	for lid in _id_owners:
		var original = _id_owners[lid].get_ref()
		if original and project.layers.has(original) and is_same(original.get_meta("blendlorama_id", ""), lid):
			owners[lid] = original
	var changed := false
	for layer in project.layers:
		var raw = layer.get_meta("blendlorama_id", "")
		var lid: String = raw if raw is String else ""
		var valid := lid.length() == 32 and lid.is_valid_hex_number(false)
		if not valid or (owners.has(lid) and owners[lid] != layer):
			lid = Crypto.new().generate_random_bytes(16).hex_encode()
			while owners.has(lid):
				lid = Crypto.new().generate_random_bytes(16).hex_encode()
			layer.set_meta("blendlorama_id", lid)
			changed = true
		owners[lid] = layer
	_id_owners.clear()
	for lid in owners:
		_id_owners[lid] = weakref(owners[lid])
	# Without this, merely linking an already-saved project may not prompt the
	# user to save the newly assigned identities on closing Pixelorama.
	if changed and not project.has_changed:
		project.has_changed = true


func _stable_layer_id(layer, _index: int) -> String:
	return String(layer.get_meta("blendlorama_id"))


func _active_layer_id(project) -> String:
	_ensure_layer_ids(project)
	var index := int(project.current_layer)
	if index < 0 or index >= project.layers.size():
		return ""
	var frame := int(project.current_frame)
	if frame < 0 or frame >= project.frames.size() or index >= project.frames[frame].cels.size():
		return ""
	if project.frames[frame].cels[index].get_class_name() != "PixelCel":
		return ""
	return _stable_layer_id(project.layers[index], index)


func state_signature(project) -> String:
	_ensure_layer_ids(project)
	var state := [project.get_instance_id(), project.name, project.current_frame, project.size]
	for i in range(project.layers.size()):
		var layer = project.layers[i]
		var cel = project.frames[project.current_frame].cels[i]
		state.append([_stable_layer_id(layer, i), layer.name, layer.is_visible_in_hierarchy(),
			layer.opacity, layer.blend_mode, cel.opacity])
	return str(state)


func _blend_to_wire(blend) -> String:
	if blend == null:
		return "normal"
	var key := int(blend)
	if not BLEND_TO_WIRE.has(key):
		push_warning("Blendlorama: unsupported blend mode %d; preview uses Normal" % key)
	return BLEND_TO_WIRE.get(key, "normal")


func _image_hash(image: Image) -> String:
	var context := HashingContext.new()
	context.start(HashingContext.HASH_SHA256)
	context.update(image.get_data())
	return context.finish().hex_encode()


func _create_blended_image(project, frame) -> Image:
	var blended_image = project.new_empty_image()
	for i in range(project.layers.size()):
		var layer = project.layers[i]
		if layer.visible:
			if frame.cels.size() > i:
				var cel = frame.cels[i]
				var cel_image = cel.get_image()
				if cel_image:
					_blend_layer_onto_image(blended_image, cel_image, layer)
	return blended_image


func _blend_layer_onto_image(target, source, layer):
	var opacity = layer.opacity
	if opacity == null:
		opacity = 1.0
	for x in range(target.get_width()):
		for y in range(target.get_height()):
			var target_color = target.get_pixel(x, y)
			var source_color = source.get_pixel(x, y)
			if source_color.a > 0:
				var final_alpha = source_color.a * opacity
				var blended_color = target_color.lerp(source_color, final_alpha)
				target.set_pixel(x, y, blended_color)


# Hash functions for per-layer change detection removed - using signal approach.

func _exit_tree() -> void:
	# Only this exporter session owns this directory.
	var dir = DirAccess.open(export_temp_dir)
	if dir:
		for filename in dir.get_files():
			dir.remove(filename)
		DirAccess.remove_absolute(export_temp_dir)
