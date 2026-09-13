# Blenderlorama

<img src="./logo.png" width="50%" alt="Blender-Aseprite Bridge Logo">

[简体中文](README.zh-CN.md) | [English](README.md)

A real-time synchronization tool that connects Blender and Pixelorama for seamless pixel art workflow. This project consists of two components: a Blender addon and a Pixelorama extension that communicate via WebSocket to sync UV maps, textures, and pixel art data in real-time.

## Overview

Blender Pixel Sync enables artists to work with pixel art textures in Blender while having real-time synchronization with Pixelorama. The tool provides pixel-perfect UV unwrapping, texture management, and live updates between both applications.

## Features

### Blender Addon (`blender-part/`)

- **WebSocket Server**: Real-time communication server for data synchronization
- **Pixel-Perfect UV Unwrapping**: Specialized UV unwrapping algorithms for pixel art
- **Texture Management**: Automatic texture detection, loading, and management
- **UV Export/Import**: Export UV layouts to Pixelorama and import back with modifications
- **Image State Monitoring**: Real-time monitoring of image changes in Blender
- **World Grid Tools**: Specialized grid setup for pixel art workflow
- **Checker Texture Generation**: Create checker patterns for texture testing

### Pixelorama Extension (`blender-lorama/`)

- **WebSocket Client**: Connects to Blender's WebSocket server
- **UV Overlay**: Visual overlay showing Blender UV layouts in Pixelorama
- **Texture Export**: Export modified textures back to Blender
- **Sync Panel**: User interface for managing synchronization settings
- **Real-time Updates**: Live updates when textures change in Blender

## Layer sync (first version)

Select the original Blender texture in Pixelorama's panel. Pixel layers from the
current frame become separate Blender images; their composite updates the original
image so existing material links continue to work. Hidden layers are retained.

- Manage layer creation, deletion, names, order, visibility and opacity in Pixelorama.
- Selecting a layer in Pixelorama automatically switches Blender's Texture Paint
  canvas to the same layer. Blender displays it as `PX | Layer name | Blender image`.
  You can also use `Paint` in `3D View > N > PixeloramaSync > Layers` to switch it
  manually. Edits, including transparent pixels, return to the matching Pixelorama
  layer/frame. Do not paint the composite preview directly.
- Set layer roles to `Emission` and click `Apply Unlit Layer Material` to assign a new
  generated material to the active slot (the previous material datablock is retained).
  The generated material is always unlit and preserves the composite alpha. Emission
  layers additionally boost their colour using alpha, opacity and visibility, allowing
  HDR bloom to distinguish them from ordinary colour layers. Generated nodes are rebuilt when structure changes.
  For custom node edits, duplicate the material and remove its `blendlorama_image`
  custom property first.
- `Export Layer PNGs + Manifest` writes original RGBA layer PNGs and `layers.json`
  with names, order, roles, visibility, opacity, blend modes and frame indices into
  a project-name-derived subdirectory. Re-export overwrites matching files. Engine
  shaders can consume each texture independently; apply manifest opacity separately.

Supported: ordinary pixel layers, Normal, Erase, Darken, Lighten, Multiply, Screen,
Overlay. Unsupported modes warn and preview as Normal. Group compositing, clipping,
layer effects, tile and 3D layers are not implemented; group children are flattened
with inherited visibility. Only the current frame is mirrored, not a whole animation.
Layer IDs, project IDs and Blender image bindings persist through Pixelorama native
metadata in `.pxo`, including rename, reorder and reopen. Linking does not rename the
Pixelorama project. First linking marks the project dirty: save the `.pxo` and save
Blender roles in `.blend`. Reconnect to the same Blender texture to retain roles.
Existing IDs are preserved; missing, malformed or duplicated IDs are repaired.
Both applications must share local filesystem paths. Normal strokes send only the
current layer; structural changes send a full layer snapshot. Messages carry protocol,
project/image/layer IDs, revisions and content hashes. Temporary files are removed on
ACK and their pending count is bounded. Failed incremental writes request a full resync.
Avoid simultaneous edits to the same layer; locked Pixelorama layers reject Blender writes.
Cross-application conflict merging is not implemented;
detached Blender images are retained to preserve other material references.

Validation:

```bash
blender -b --factory-startup --python-exit-code 1 --python tests/blender_layers.py
godot --headless --path blender-lorama --script "$PWD/tests/pixelorama_layers.gd"
```

Blender tests use actual bpy images/materials. Godot tests use a mock Pixelorama
project API plus the actual metadata codec from `../Pixelorama` for ZIP/JSON
roundtrip tests. Blender tests also reopen a saved `.blend` and verify retained roles.
Interactive painting, undo and animation still require dual-app QA.

## Automated releases

The `Build and Release` GitHub Actions workflow publishes a Release when a `v*`
tag is pushed. Assets include the Blender addon ZIP, installable
`BlenderPixelorama.pck`, Pixelorama source ZIP and `SHA256SUMS.txt`.
After committing and pushing the code:

```bash
git tag v0.2.0
git push origin v0.2.0
```

Tags must use `vMAJOR.MINOR.PATCH`, optionally with a suffix such as `-rc.1`.
Package versions are stamped from the tag in the build workspace, without committing
back to the repository. Suffixed versions become prereleases. Publishing requires
all build/export/checksum steps to pass. Manual runs on branches only upload
artifacts; manual runs on existing version tags also publish a Release.
The built-in `GITHUB_TOKEN` is sufficient; repository/organization policy must allow
the release job's `contents: write` permission.

Local builds require Python 3.11+, Blender 4.5+ and Godot (CI uses Godot 4.7.2):

```bash
python build.py --all --blender /path/to/blender
python build.py --all --godot /path/to/godot
```

`--all` / `--pck` now export the actual PCK, with source GDScript instead of
version-coupled bytecode. `--release-version 0.2.0` modifies local version files;
use it only when preparing a release or in a disposable build workspace.

## Installation

### Blender Addon Installation

The Blender addon is a standard Blender 4.5+ Extension built with Blender's official
`--command extension build` command. Its WebSocket server uses Python's standard
library, so the package contains no wheels and needs no runtime `pip` or downloads.

1. Download the addon `.zip` containing `blender_manifest.toml` and `__init__.py`
2. In Blender, go to `Edit > Preferences > Extensions`
3. Click the drop-down arrow (top-right) and select "Install from Disk..."
4. Select the zip file
5. Enable the "Pixelorama Sync" extension
6. Enable Online Access in `Preferences > System`; the server only listens on
   local address `127.0.0.1:8765`

The same standard ZIP works on Blender-supported Windows, macOS and Linux systems;
it contains no platform-specific binary dependency.

### Pixelorama Extension Installation

1. Download the BlenderPixelorama .pck file​
2. Open Pixelorama
3. Go to `Preferences > Extensions > Install Extension`
4. Select the pre-packaged `.pck` file
4. The "BlenderPixelorama" extension will be installed automatically

## Usage

### Setting up the Workflow

1. **Start the Blender Server**:

   - In Blender, open the Image Editor
   - Go to the "Pixelorama Sync" panel (Image Editor > UI Panel > Pixelorama Sync)
   - Click "Start Server" to begin the WebSocket server

2. **Connect from Pixelorama**:

   - In Pixelorama, the Blender Pixel Sync panel will appear as a new tab
   - The extension will automatically attempt to connect to Blender
   - Connection status will be displayed in the panel

3. **Prepare Your Model**:
   - Create or import your 3D model in Blender
   - Apply materials and UV unwrapping
   - Use the pixel-perfect unwrapping tools for optimal results

### Working with Textures

1. **Export UV Layout**:

   - Select your object in Blender
   - Use the UV tools to export the layout to Pixelorama
   - The UV layout will appear as an overlay in Pixelorama

2. **Create/Edit Textures**:

   - Design your pixel art texture in Pixelorama
   - Use the UV overlay as a guide for precise placement
   - The grid setup ensures pixel-perfect alignment

3. **Sync Changes**:
   - Changes in Pixelorama can be exported back to Blender
   - Blender will automatically update the texture
   - Real-time synchronization keeps both applications in sync

### Recommended Workflow

1. **Model Setup**:

   - Create your low-poly model in Blender
   - Use the "Setup World Grid" tool for proper pixel art scaling
   - Set grid subdivisions based on your pixel density requirements

2. **UV Unwrapping**:

   - Use "Pixel Perfect Unwrap" for clean, pixel-aligned UVs
   - Or use "Unwrap to Grid" for grid-based UV layouts
   - Check UVs in the UV Editor for proper alignment

3. **Texture Creation**:
   - Export UV layout to Pixelorama
   - Create pixel art texture following the UV guides
4. **Final Integration**:
   - Export texture back to Blender
   - Apply to model and test in 3D view
   - Make adjustments as needed in either application

## Blender Panel UI

The Blender addon provides several panels:

### Server Panel

- **Start/Stop Server**: Control the WebSocket server
- **Connection Status**: Shows connected clients and server status
- **Port Information**: Display server connection details

### UV Tools Panel

- **Pixel Perfect Unwrap**: Unwrap UVs with pixel-perfect precision
- **Unwrap to Grid**: Create grid-based UV layouts
- **Export UV**: Send UV data to Pixelorama

### Texture Tools Panel

- **Check Texture**: Validate texture dimensions and format
- **Create Checker**: Generate checker pattern textures
- **Reload Textures**: Refresh textures from disk

### World Grid Panel

- **Setup World Grid**: Configure Blender's grid for pixel art
- **Grid Subdivisions**: Adjust grid density
- **Scale Settings**: Set appropriate scale for pixel work

## Compatibility

### Blender

- **Version**: Blender 4.5.0 and later
- **Platform**: Windows, macOS, Linux

### Pixelorama

- **Version**: Supports Pixelorama API version 8
- **Platform**: Windows, macOS, Linux

## Technical Details

### Communication Protocol

- **WebSocket**: Real-time bidirectional communication
- **JSON Message Format**: Structured data exchange
- **Event-Driven**: Automatic updates on changes

### Supported Features

- **Image Formats**: PNG, JPG, BMP, and other Blender-supported formats
- **UV Coordinates**: Full UV map synchronization

## Dependencies

### Blender Addon Dependencies

- No third-party Python packages; the WebSocket server uses the standard library
- Blender 4.5.0 or later
- NumPy (included with Blender)

### Pixelorama Extension Dependencies

- Pixelorama 1.2.2 (extension API 9)
- Godot Engine (Pixelorama runtime)

## File Structure

```
blender-pixel-sync/
├── blender-part/              # Blender addon / extension
│   ├── blender_manifest.toml  # Extension manifest (id, permissions, build paths)
│   ├── __init__.py             # Addon registration
│   ├── server.py               # WebSocket server
│   ├── operators.py            # Blender operators
│   ├── blender_integration.py  # Blender integration logic
│   ├── uv_extractor.py         # UV extraction and processing
│   ├── image_manager.py        # Image and texture management
│   ├── texture_processor.py    # Texture processing tools
│   ├── unwrap_tools.py         # UV unwrapping algorithms
│   ├── ui.py                   # User interface panels
│   └── watch.py                # File watching and change detection
└── blender-lorama/              # Pixelorama extension
    └── src/
        └── Extensions/
            └── BlenderPixelorama/
                ├── extension.json        # Extension metadata
                ├── Main.gd               # Main extension script
                ├── Main.tscn             # Main scene
                ├── BlenderLoramaPanel.tscn # UI panel
                ├── WebSocketClient.gd   # WebSocket client
                ├── uv_overlay.gd         # UV overlay functionality
                ├── blender_lorama_panel.gd # Panel logic
                └── texture_exporter.gd  # Texture export
```

## Contributing

Contributions are welcome! Please feel free to submit pull requests, report bugs, or suggest features.

### Development Setup

1. Clone the repository
2. For Blender development: Use Blender's scripting environment
3. For Pixelorama development: Use Godot Engine with Pixelorama source
4. Test changes with both applications running

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Credits

- **Original Author**: Heisenshark
- **Refactored by**: Assistant
- **Pixelorama Extension**: yuchenyang1994
- **UV Unwrapping Algorithm**: Based on Magic-UV by Nutti

## Support

For issues, questions, or support:

1. Check the GitHub issues page
2. Review the documentation for common solutions
3. Provide Blender and Pixelorama versions when reporting issues

## Version History

### v0.1.0

- Initial release
- Basic WebSocket communication
- UV export/import functionality
- Pixel-perfect unwrapping tools
- Texture synchronization
- World grid setup tools
