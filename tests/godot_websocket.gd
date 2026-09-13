extends SceneTree


func _initialize() -> void:
	var peer := WebSocketPeer.new()
	assert(peer.connect_to_url("ws://127.0.0.1:18765") == OK)
	for _attempt in range(300):
		peer.poll()
		if peer.get_ready_state() == WebSocketPeer.STATE_OPEN and peer.get_available_packet_count() > 0:
			var message = JSON.parse_string(peer.get_packet().get_string_from_utf8())
			assert(message.type == "WELCOME")
			assert(message.protocol_version == 1)
			peer.close()
			print("PASS: Godot WebSocketPeer connected to dependency-free Blender server")
			quit()
			return
		await create_timer(0.01).timeout
	push_error("Godot could not complete the WebSocket handshake")
	quit(1)
