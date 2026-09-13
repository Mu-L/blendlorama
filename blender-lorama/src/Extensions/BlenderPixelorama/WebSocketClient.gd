class_name WebSocketClient
extends Node

@export var handshake_headers: PackedStringArray
@export var supported_protocols: PackedStringArray
var tls_options: TLSOptions = null

var socket := WebSocketPeer.new()
var last_state := WebSocketPeer.STATE_CLOSED
var reconnect_timer: Timer
var reconnect_attempts := 0
var max_reconnect_attempts := 5
var reconnect_delay := 2.0
var target_url := "ws://127.0.0.1:8765"
var auto_reconnect := false

signal connected_to_server
signal connection_closed
signal message_received(message: Variant)
signal connection_failed


func _ready() -> void:
	reconnect_timer = Timer.new()
	reconnect_timer.wait_time = reconnect_delay
	reconnect_timer.one_shot = true
	add_child(reconnect_timer)
	reconnect_timer.timeout.connect(_attempt_reconnect)


func connect_to_url(url: String, reconnect := true) -> int:
	target_url = url
	auto_reconnect = reconnect
	if socket.get_ready_state() != WebSocketPeer.STATE_CLOSED:
		socket.close()
	socket = WebSocketPeer.new()
	socket.supported_protocols = supported_protocols
	socket.handshake_headers = handshake_headers
	var err := socket.connect_to_url(target_url, tls_options)
	last_state = socket.get_ready_state()
	if err != OK:
		connection_failed.emit()
		_schedule_reconnect()
	return err


func _schedule_reconnect() -> void:
	if not auto_reconnect or reconnect_timer.time_left > 0:
		return
	if reconnect_attempts >= max_reconnect_attempts:
		connection_failed.emit()
		return
	reconnect_attempts += 1
	reconnect_timer.start()


func _attempt_reconnect() -> void:
	if auto_reconnect and socket.get_ready_state() == WebSocketPeer.STATE_CLOSED:
		connect_to_url(target_url, true)


func send(message) -> int:
	if socket.get_ready_state() != WebSocketPeer.STATE_OPEN:
		return ERR_UNCONFIGURED
	if typeof(message) == TYPE_STRING:
		return socket.send_text(message)
	return socket.send(var_to_bytes(message))


func get_message() -> Variant:
	if socket.get_available_packet_count() < 1:
		return null
	var packet := socket.get_packet()
	if socket.was_string_packet():
		return packet.get_string_from_utf8()
	return bytes_to_var(packet)


func close(code: int = 1000, reason: String = "") -> void:
	auto_reconnect = false
	reconnect_timer.stop()
	socket.close(code, reason)
	last_state = socket.get_ready_state()


func get_socket() -> WebSocketPeer:
	return socket


func poll() -> void:
	if socket.get_ready_state() != WebSocketPeer.STATE_CLOSED:
		socket.poll()
	var state := socket.get_ready_state()
	if last_state != state:
		last_state = state
		if state == WebSocketPeer.STATE_OPEN:
			reconnect_attempts = 0
			connected_to_server.emit()
		elif state == WebSocketPeer.STATE_CLOSED:
			connection_closed.emit()
			_schedule_reconnect()
	while socket.get_ready_state() == WebSocketPeer.STATE_OPEN and socket.get_available_packet_count():
		message_received.emit(get_message())


func _process(_delta: float) -> void:
	poll()
