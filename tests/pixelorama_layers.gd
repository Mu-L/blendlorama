extends SceneTree

const Exporter = preload("res://src/Extensions/BlenderPixelorama/texture_exporter.gd")

class Layer extends RefCounted:
	var name := "Layer"
	var visible := true
	var opacity := 1.0
	var blend_mode := 0
	var locked := false
	func is_visible_in_hierarchy():
		return visible

class Cel extends RefCounted:
	var opacity := 1.0
	var image := Image.create(4, 4, false, Image.FORMAT_RGBA8)
	func get_class_name():
		return "PixelCel"
	func get_image():
		return image

class Frame extends RefCounted:
	var cels := [Cel.new(), Cel.new()]

class Project extends RefCounted:
	var has_changed := false
	var name := "Canvas"
	var size := Vector2i(4, 4)
	var current_frame := 0
	var current_layer := 0
	var layers := [Layer.new(), Layer.new()]
	var frames := [Frame.new()]

class ProjectApi extends RefCounted:
	var current_project := Project.new()
	var updates := 0
	func set_pixelcel_image(image, frame, layer):
		current_project.frames[frame].cels[layer].image = image
		updates += 1

class DrawingAlgosApi extends RefCounted:
	var project
	var calls := []
	func _init(value):
		project = value
	func resize_canvas(width, height, offset_x, offset_y):
		calls.append([width, height, offset_x, offset_y])
		project.size = Vector2i(width, height)

class GeneralApi extends RefCounted:
	var drawing_algos
	func _init(project):
		drawing_algos = DrawingAlgosApi.new(project)
	func get_drawing_algos():
		return drawing_algos

class Api extends RefCounted:
	var project := ProjectApi.new()
	var general := GeneralApi.new(project.current_project)

func _initialize():
	_test_target_size_matching()
	var exporter = Exporter.new()
	exporter.extensions_api = Api.new()
	exporter.export_temp_dir = "user://blendlorama_test"
	DirAccess.make_dir_recursive_absolute(exporter.export_temp_dir)
	var api = exporter.extensions_api.project
	var project = api.current_project
	project.layers[1].visible = false
	project.frames[0].cels[0].image.fill(Color.RED)
	exporter.bind_project(project, "blender-image-id", "Canvas")
	var message = exporter.export_layers("Canvas", "blender-image-id")
	assert(message.layers.size() == 2, "Hidden layer must survive export")
	assert(not message.layers[1].visible)
	assert(message.active_layer_id == message.layers[0].id)
	project.current_layer = 1
	assert(exporter.active_layer_message("Canvas", "blender-image-id").id == message.layers[1].id)
	project.current_layer = 0
	var id = message.layers[0].id
	project.layers[0].name = "Renamed"
	project.layers.reverse()
	project.frames[0].cels.reverse()
	var next = exporter.export_layers("Canvas", "blender-image-id")
	assert(next.layers[1].id == id, "Identity survives rename/reorder")
	var incoming = {"image": "Canvas", "image_id": "blender-image-id", "id": id, "frame": 0, "revision": 1, "file_path": message.layers[1].file_path}
	exporter.apply_sync_layer(incoming, project)
	assert(api.updates == 1)
	assert(project.frames[0].cels[1].image.get_pixel(0, 0).a == 0, "Transparent paint replaces old pixels")
	assert(project.layers[1].name == "Renamed", "Pixels cannot overwrite newer metadata")
	incoming.id = "deleted"
	exporter.apply_sync_layer(incoming, project)
	assert(api.updates == 1, "Deleted ID cannot fall back to another layer")
	incoming.id = id
	incoming.frame = 99
	exporter.apply_sync_layer(incoming, project)
	assert(api.updates == 1)
	assert(not exporter.remote_applying)
	_test_persistence(exporter)
	exporter.free()
	print("PASS: hidden layers, stable identity, reordered receive, transparent replacement, stale packet rejection")
	quit()


func _test_target_size_matching():
	var exporter = Exporter.new()
	exporter.extensions_api = Api.new()
	var project = exporter.extensions_api.project.current_project
	assert(exporter.match_project_size(Vector2i(8, 6)))
	assert(project.size == Vector2i(8, 6))
	assert(exporter.extensions_api.general.drawing_algos.calls == [[8, 6, 2, 1]])
	assert(not exporter.remote_applying)
	assert(exporter.match_project_size(Vector2i(8, 6)))
	assert(exporter.extensions_api.general.drawing_algos.calls.size() == 1)
	assert(not exporter.match_project_size(Vector2i.ZERO))
	exporter.free()
	print("PASS: Target Texture size becomes Pixelorama canvas size without resampling")


func _test_persistence(exporter):
	# Execute the actual dependency-free metadata codec from the user's checkout.
	# The surrounding Project/GUI is mocked; this is not a full UI integration test.
	var source_path = ProjectSettings.globalize_path("res://../../Pixelorama/src/Classes/Project.gd")
	if not FileAccess.file_exists(source_path):
		push_error("Persistence test requires ../Pixelorama source checkout")
		quit(1)
		return
	var source = FileAccess.get_file_as_string(source_path)
	assert(source.contains('layer_data[-1]["metadata"] = _serialize_metadata(layer)'))
	assert(source.contains('_deserialize_metadata(layer, dict.layers[layer_i])'))
	var start = source.find("func _serialize_metadata(")
	var end = source.find("\n\n##", start)
	assert(start >= 0 and end > start)
	var codec_script = GDScript.new()
	codec_script.source_code = "extends RefCounted\n" + source.substr(start, end - start)
	assert(codec_script.reload() == OK)
	var codec = codec_script.new()
	var project = Project.new()
	# Migration: an existing ID must remain unchanged.
	var legacy_id = "0123456789abcdef0123456789abcdef"
	project.layers[0].set_meta("blendlorama_id", legacy_id)
	project.layers[0].set_meta("another_extension", "preserve me")
	exporter.bind_project(project, "persistent-image-id", "RenamedTexture")
	var project_id: String = exporter.ensure_project_id(project)
	exporter._ensure_layer_ids(project)
	assert(project.has_changed)
	assert(project.layers[0].get_meta("blendlorama_id") == legacy_id)
	project.has_changed = false
	exporter._ensure_layer_ids(project)
	assert(not project.has_changed, "No repeated dirty flag for existing IDs")
	var data := {"metadata": codec._serialize_metadata(project), "layers": []}
	for layer in project.layers:
		data.layers.append({"metadata": codec._serialize_metadata(layer)})
	# Use the same JSON-in-ZIP representation as OpenSave.save_pxo_file.
	var path = "user://blendlorama_identity_test.pxo"
	var writer = ZIPPacker.new()
	assert(writer.open(path) == OK)
	writer.start_file("data.json")
	writer.write_file(JSON.stringify(data).to_utf8_buffer())
	writer.close_file()
	writer.close()
	var reader = ZIPReader.new()
	assert(reader.open(path) == OK)
	var loaded = JSON.parse_string(reader.read_file("data.json").get_string_from_utf8())
	reader.close()
	DirAccess.remove_absolute(path)
	var reopened = Project.new()
	codec._deserialize_metadata(reopened, loaded)
	for i in range(reopened.layers.size()):
		codec._deserialize_metadata(reopened.layers[i], loaded.layers[i])
	var fresh_exporter = Exporter.new()
	fresh_exporter._ensure_layer_ids(reopened)
	assert(not reopened.has_changed)
	assert(fresh_exporter.ensure_project_id(reopened) == project_id)
	assert(fresh_exporter.bound_image_id(reopened) == "persistent-image-id")
	assert(fresh_exporter.bound_image_name(reopened) == "RenamedTexture")
	assert(reopened.layers[0].get_meta("blendlorama_id") == legacy_id)
	assert(reopened.layers[0].get_meta("another_extension") == "preserve me")
	assert(reopened.layers[1].get_meta("blendlorama_id") == project.layers[1].get_meta("blendlorama_id"))
	# A copied ID cannot steal the original even if inserted before it.
	var copy = Layer.new()
	codec._deserialize_metadata(copy, loaded.layers[0])
	reopened.layers.push_front(copy)
	fresh_exporter._ensure_layer_ids(reopened)
	assert(copy.get_meta("blendlorama_id") != legacy_id)
	assert(reopened.layers[1].get_meta("blendlorama_id") == legacy_id)
	assert(reopened.has_changed)
	# Malformed metadata is repaired instead of becoming a file path or shared ID.
	copy.set_meta("blendlorama_id", 42)
	fresh_exporter._ensure_layer_ids(reopened)
	assert(copy.get_meta("blendlorama_id") is String)
	fresh_exporter.free()
	print("PASS: Pixelorama source metadata codec, ZIP/JSON roundtrip, old ID migration, dirty flag, duplicate repair")
