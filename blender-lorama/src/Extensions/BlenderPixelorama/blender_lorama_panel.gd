extends PanelContainer

const PROTOCOL_VERSION := 1

var extensions_api

@onready var status_label = $MarginContainer/VBoxContainer/StatusLabel
@onready var connect_button = $MarginContainer/VBoxContainer/HBoxContainer/ConnectButton
@onready var disconnect_button = $MarginContainer/VBoxContainer/HBoxContainer/DisconnectButton
@onready var websocket_client: WebSocketClient = $WebSocketClient
@onready var uv_overlay_enable_button: CheckButton = $MarginContainer/VBoxContainer/EnableContainer/SwitchUvOverlay
@onready var texture_exporter: TextureExporter = $TextureExporter
@onready var image_options: OptionButton = $MarginContainer/VBoxContainer/ImageListContainer/ImageOptions

var current_image_id := ""
var current_image_name := ""
var uv_overlay: UVOverlay
var last_layer_state := ""
var poll_elapsed := 0.0
var pixel_debounce: Timer


func _ready() -> void:
	extensions_api = get_node_or_null("/root/ExtensionsApi")
	uv_overlay = UVOverlay.new()
	var canvas = extensions_api.general.get_canvas() if extensions_api else null
	if canvas:
		canvas.previews.add_child(uv_overlay)
	uv_overlay_enable_button.set_pressed_no_signal(true)
	pixel_debounce = Timer.new()
	pixel_debounce.one_shot = true
	pixel_debounce.wait_time = 0.12
	add_child(pixel_debounce)
	pixel_debounce.timeout.connect(_send_current_layer)

	if extensions_api and extensions_api.signals:
		extensions_api.signals.current_cel_signal(_on_texture_changed, "texture_changed")
		extensions_api.signals.signal_project_switched(_on_project_changed)
		extensions_api.signals.signal_cel_switched(_on_cel_switched)

	connect_button.pressed.connect(on_connect_button_pressed)
	disconnect_button.pressed.connect(on_disconnect_button_pressed)
	websocket_client.connected_to_server.connect(_on_server_connected)
	websocket_client.connection_closed.connect(_on_server_closed)
	websocket_client.connection_failed.connect(_on_connection_failed)
	websocket_client.message_received.connect(_on_receive_message)
	uv_overlay_enable_button.toggled.connect(_on_overlay_toggle)
	image_options.item_selected.connect(_on_blender_image_selected)
	if image_options.item_count == 0:
		image_options.add_item("Select target texture")
	_restore_project_binding()


func _exit_tree() -> void:
	if extensions_api and extensions_api.signals:
		extensions_api.signals.current_cel_signal(_on_texture_changed, "texture_changed", true)
		extensions_api.signals.signal_project_switched(_on_project_changed, true)
		extensions_api.signals.signal_cel_switched(_on_cel_switched, true)
	websocket_client.close()
	if is_instance_valid(uv_overlay):
		uv_overlay.queue_free()


func _on_server_connected() -> void:
	status_label.text = "Blender: Connected"
	last_layer_state = ""


func _on_server_closed() -> void:
	status_label.text = "Blender: Reconnecting..." if websocket_client.auto_reconnect else "Blender: Disconnected"


func _on_connection_failed() -> void:
	status_label.text = "Blender: Connection Failed"


func _on_overlay_toggle(enabled: bool) -> void:
	uv_overlay.set_enabled(enabled)


func _on_blender_image_selected(index: int) -> void:
	if index <= 0:
		current_image_id = ""
		current_image_name = ""
		return
	current_image_name = image_options.get_item_text(index)
	current_image_id = String(image_options.get_item_metadata(index))
	var project = extensions_api.project.current_project
	texture_exporter.bind_project(project, current_image_id, current_image_name)
	last_layer_state = ""
	_send_full_sync()


func _on_project_changed() -> void:
	last_layer_state = ""
	pixel_debounce.stop()
	_restore_project_binding()


func _on_cel_switched() -> void:
	call_deferred("_send_active_layer")


func _restore_project_binding() -> void:
	if not extensions_api:
		return
	var project = extensions_api.project.current_project
	current_image_id = texture_exporter.bound_image_id(project)
	current_image_name = texture_exporter.bound_image_name(project)
	var index := _get_image_list_index_by_id(current_image_id)
	image_options.select(maxi(index, 0))


func _on_receive_message(raw: String) -> void:
	var message = JSON.parse_string(raw)
	if not message is Dictionary or not message.has("type"):
		return
	if int(message.get("protocol_version", PROTOCOL_VERSION)) != PROTOCOL_VERSION:
		status_label.text = "Blender: Protocol mismatch"
		return
	match String(message.type):
		"GET_UV_OVERLAY":
			_handle_uv_data(message)
		"GET_IMAGES":
			_handle_blender_images(message)
		"SYNC_LAYER":
			_handle_sync_layer(message)
		"ACK":
			texture_exporter.acknowledge(message)
			if not bool(message.get("success", true)) and message.get("message_type", "") == "SYNC_LAYER":
				call_deferred("_send_full_sync")


func _handle_uv_data(uv_data: Dictionary) -> void:
	uv_overlay.clear_uv_overlay()
	uv_overlay.set_uv_data(uv_data)


func _handle_blender_images(image_list: Dictionary) -> void:
	image_options.clear()
	image_options.add_item("Select target texture")
	for image in image_list.get("data", []):
		image_options.add_item(String(image.get("name", "Unnamed")))
		image_options.set_item_metadata(image_options.item_count - 1, String(image.get("id", "")))
	var selected := _get_image_list_index_by_id(current_image_id)
	image_options.select(maxi(selected, 0))
	if selected > 0:
		current_image_name = image_options.get_item_text(selected)
		texture_exporter.bind_project(extensions_api.project.current_project, current_image_id, current_image_name)
		_send_full_sync()


func _on_texture_changed() -> void:
	if texture_exporter.remote_applying or current_image_id.is_empty():
		return
	pixel_debounce.start()


func _send_full_sync() -> void:
	if not _can_sync():
		return
	pixel_debounce.stop()
	var message = texture_exporter.export_layers(current_image_name, current_image_id)
	if message:
		_send(message)
		last_layer_state = texture_exporter.state_signature(extensions_api.project.current_project)


func _send_current_layer() -> void:
	if not _can_sync() or texture_exporter.remote_applying:
		return
	var message = texture_exporter.export_current_layer(current_image_name, current_image_id)
	if message:
		_send(message)


func _send_active_layer() -> void:
	if not _can_sync() or texture_exporter.remote_applying:
		return
	var message = texture_exporter.active_layer_message(current_image_name, current_image_id)
	if message:
		_send(message)


func _can_sync() -> bool:
	if not extensions_api or websocket_client.socket.get_ready_state() != WebSocketPeer.STATE_OPEN:
		return false
	var project = extensions_api.project.current_project
	return not current_image_id.is_empty() and texture_exporter.bound_image_id(project) == current_image_id


func _send(message: Dictionary) -> void:
	var result := websocket_client.send(JSON.stringify(message))
	if result != OK:
		push_warning("Blendlorama send failed: %d" % result)


func _handle_sync_layer(message: Dictionary) -> void:
	var project = extensions_api.project.current_project
	var success := texture_exporter.apply_sync_layer(message, project)
	_send({
		"type": "ACK",
		"protocol_version": PROTOCOL_VERSION,
		"message_type": "SYNC_LAYER",
		"project_id": texture_exporter.ensure_project_id(project),
		"image_id": message.get("image_id", ""),
		"id": message.get("id", ""),
		"revision": int(message.get("revision", 0)),
		"success": success,
	})


func on_connect_button_pressed() -> void:
	websocket_client.reconnect_attempts = 0
	websocket_client.connect_to_url("ws://127.0.0.1:8765", true)


func on_disconnect_button_pressed() -> void:
	websocket_client.close()


func _get_image_list_index_by_id(image_id: String) -> int:
	if image_id.is_empty():
		return -1
	for index in range(1, image_options.item_count):
		if String(image_options.get_item_metadata(index)) == image_id:
			return index
	return -1


func _process(delta: float) -> void:
	poll_elapsed += delta
	if poll_elapsed < 0.2 or not _can_sync() or texture_exporter.remote_applying:
		return
	poll_elapsed = 0.0
	var project = extensions_api.project.current_project
	var signature := texture_exporter.state_signature(project)
	if signature != last_layer_state:
		_send_full_sync()
