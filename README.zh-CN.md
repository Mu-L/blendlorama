# Blenderlorama

<img src="./logo.png" width="50%" alt="Blender-Aseprite Bridge Logo">

[简体中文](README.zh-CN.md) | [English](README.md)

## 概述

Blender Pixel Sync 是一个实时同步工具，连接 Blender 和 Pixelorama，为像素艺术工作流提供无缝衔接。该项目包含两个组件：Blender 插件和 Pixelorama 扩展，通过 WebSocket 进行实时数据同步。

## 主要功能

### Blender 插件 (`blender-part/`)

- **WebSocket 服务器**：用于数据同步的实时通信服务器
- **像素完美 UV 展开**：专门为像素艺术设计的 UV 展开算法
- **纹理管理**：自动纹理检测、加载和管理
- **UV 导出/导入**：将 UV 布局导出到 Pixelorama 并导入修改后的版本
- **图像状态监控**：实时监控 Blender 中的图像变化
- **世界网格工具**：专为像素艺术工作流设计的网格设置
- **棋盘纹理生成**：创建用于纹理测试的棋盘图案

### Pixelorama 扩展 (`blender-lorama/`)

- **WebSocket 客户端**：连接到 Blender 的 WebSocket 服务器
- **UV 叠加层**：在 Pixelorama 中显示 Blender UV 布局的视觉叠加
- **纹理导出**：将修改后的纹理导出回 Blender
- **同步面板**：管理同步设置的用户界面
- **实时更新**：当 Blender 中纹理发生变化时进行实时更新

## 图层同步（第一版）

在 Pixelorama 面板选择 Blender 原始贴图后，当前帧的像素图层会同步成独立的
Blender Image，并合成回原始贴图，已有材质连接可继续显示结果。

1. 在 Pixelorama 新建、删除、重命名、排序图层，或修改可见性和透明度；
   插件会自动更新 Blender。隐藏图层仍保留独立像素。
2. 在 Pixelorama 切换当前图层时，Blender 会自动把 Texture Paint 画布切换到同一图层；
   顶部图片名显示为 `PX | 图层名 | Blender贴图名`。也可在 Blender 的
   `3D 视图 > N > PixeloramaSync > Layers` 点击 `Paint` 手动切换。
   修改会回传到 Pixelorama 对应图层和帧，透明像素也能覆盖原像素。
   请勿直接绘制合成预览。
3. 按需将图层用途设为 `Emission`，点击 `Apply Layer Material`。
   此按钮给当前物体的活动材质槽分配新材质，保留原材质数据；生成材质使用
   合成图作为底色，并把 Emission 图层按 alpha、透明度和可见性叠加到发光输入。
   这些图层仍参与底色合成。生成材质由插件管理，结构变化会重建节点；
   需要自行编辑节点时请先复制材质并移除 `blendlorama_image` 自定义属性。
4. 点击 `Export Layer PNGs + Manifest` 选择目录，得到每层原始 RGBA PNG 和
   `layers.json`（名称、顺序、用途、可见性、透明度、混合模式、帧号）。
   输出放在按贴图名生成的子目录，同一目录再次导出会覆盖同 ID 的文件。
   游戏引擎可按用途把图层连接到 Base Color、Emission 或自定义 shader。
   PNG 不烘焙图层透明度；引擎需使用清单中的 opacity。

### 当前边界

- 图层结构由 Pixelorama 管理；Blender 负责逐层绘画、用途标记和导出。
- 同步 Pixelorama 当前帧，并非导出整段动画；同一图层不同帧共用 Blender 图片。
- 支持普通像素图层及 Normal、Erase、Darken、Lighten、Multiply、Screen、Overlay。
  其他混合模式会提示并使用 Normal 预览；图层组的独立合成、裁剪蒙版、图层效果、
  瓦片和 3D 图层未实现。组内像素层按平面图层处理，继承组可见性。
- 图层 ID、项目 ID 和 Blender 图片绑定使用 Pixelorama 原生 metadata 随 `.pxo` 保存，
  改名、调序和重开后保持不变；插件不会再为了绑定而修改 Pixelorama 项目名。
  首次链接会将项目标记为未保存，请保存 `.pxo`；Blender 的 Emission 用途需保存到 `.blend`。
  重开后选择同一 Blender 贴图即可继续配对，无需重新指定用途。旧 ID 保留；重复或损坏的 ID
  会自动修复。正常复制图层获得新 ID。此行为已对照 `../Pixelorama` 源码验证。
- 同步依赖本机临时 PNG 路径。两边应在同一台机器上运行；不要同时绘制同一层。
  普通笔触只发送当前图层，结构变化才全量同步；临时文件在 ACK 后删除并限制待确认数量。
  已删除图层的 Blender 图片仍保留，避免破坏其他材质引用。
- Pixelorama 的锁定图层不接收 Blender 写回。协议带版本、项目/图片/图层 ID、修订号和内容哈希；
  单层同步失败会触发全量重同步，但尚未实现跨两端同时编辑的冲突合并。

### 开发验证

```bash
blender -b --factory-startup --python-exit-code 1 --python tests/blender_layers.py
godot --headless --path blender-lorama --script "$PWD/tests/pixelorama_layers.gd"
```

Blender 测试使用实际 bpy 数据和材质节点；Godot 测试使用模拟的 Pixelorama 项目 API，
覆盖协议和图层身份，并调用 `../Pixelorama` 实际 metadata 序列化函数测试 ZIP/JSON 保存重读；
Blender 测试还覆盖 `.blend` 保存重开后的用途保留。这些测试不能替代两个应用中完整的绘画、撤销和动画切帧验收。
Pixelorama API 接口依据[官方源码](https://github.com/Orama-Interactive/Pixelorama/blob/v1.2.2/src/Autoload/ExtensionsApi.gd)，
混合模式编号依据[BaseLayer](https://github.com/Orama-Interactive/Pixelorama/blob/v1.2.2/src/Classes/Layers/BaseLayer.gd)。

## 自动构建与发布

GitHub Actions 的 `Build and Release` 工作流会在推送 `v*` 标签后自动构建并发布
GitHub Release，上传 Blender 插件 ZIP、可直接安装的 `BlenderPixelorama.pck`、
Pixelorama 源码 ZIP 和 `SHA256SUMS.txt`。

提交并推送代码后，例如发布 0.2.0：

```bash
git tag v0.2.0
git push origin v0.2.0
```

标签必须采用 `v主版本.次版本.修订号`，可带 `-rc.1` 等预发布后缀。
构建时会将标签版本写入构建工作区的两端扩展信息，不回写仓库。
含预发布后缀的标签会发布为 Prerelease。任一构建、导出或校验步骤失败都不会进入发布步骤。

也可在 Actions 页面手动运行：选择普通分支只生成下载产物；选择已有 `v*` 标签则
构建并发布该标签。使用仓库内置 `GITHUB_TOKEN`，无需额外配置个人 Token；仓库或组织
策略必须允许工作流的 `contents: write` 权限。当前分支运行结果不会自动成为正式版本。

本地完整构建需要 Python 3.11+、Blender 4.5+ 和 Godot（CI 固定 Godot 4.7.2）：

```bash
python build.py --all --blender /path/to/blender
# Godot 不在 PATH 时：
python build.py --all --godot /path/to/godot
```

`--all` / `--pck` 现在会真正导出 PCK；脚本以源码方式打包，避免 GDScript 字节码版本耦合。
`--release-version 0.2.0` 会修改本地版本文件，只应在准备发布或临时构建工作区使用。

## 安装

### Blender 插件安装

插件以 Blender 4.5+ 的标准扩展格式打包，由 Blender 官方
`--command extension build` 生成。WebSocket 服务使用 Python 标准库，包内不含 wheel，
运行时无需 `pip` 或下载依赖。

1. 下载包含 `blender_manifest.toml` 与 `__init__.py` 的插件 `.zip`
2. 在 Blender 中转到 `编辑 > 偏好设置 > 扩展`
3. 点击右上角下拉箭头，选择"从磁盘安装..."
4. 选择 zip 文件
5. 启用"Pixelorama Sync"扩展
6. 在 `偏好设置 > 系统` 启用 Online Access；扩展只监听本机 `127.0.0.1:8765`

同一标准 ZIP 可用于 Blender 支持的 Windows、macOS 和 Linux 平台，不含平台相关二进制依赖。

### Pixelorama 扩展安装

1. 下载 `BlenderPixelorama.pck` 文件
2. 打开 Pixelorama
3. 转到 `首选项 > 扩展 > 安装扩展`
4. 选择预打包的 `.pck` 文件
5. "BlenderPixelorama" 扩展将自动安装

## 使用方法

### 设置工作流

1. **启动 Blender 服务器**：

   - 在 Blender 中打开图像编辑器
   - 转到"Pixelorama Sync"面板（图像编辑器 > UI 面板 > Pixelorama Sync）
   - 点击"启动服务器"开始 WebSocket 服务器

2. **从 Pixelorama 连接**：

   - 在 Pixelorama 中，Blender Pixel Sync 面板将作为新标签页出现
   - 扩展将自动尝试连接到 Blender
   - 连接状态将显示在面板中

3. **准备模型**：
   - 在 Blender 中创建或导入 3D 模型
   - 应用材质和 UV 展开
   - 使用像素完美展开工具获得最佳效果

### 处理纹理

1. **导出 UV 布局**：

   - 在 Blender 中选择对象
   - 使用 UV 工具将布局导出到 Pixelorama
   - UV 布局将作为叠加层出现在 Pixelorama 中

2. **创建/编辑纹理**：

   - 在 Pixelorama 中设计像素艺术纹理
   - 使用 UV 叠加层作为精确放置的指南
   - 网格设置确保像素完美对齐

3. **同步更改**：
   - Pixelorama 中的更改可以导出回 Blender
   - Blender 将自动更新纹理
   - 实时同步保持两个应用程序同步

### 推荐工作流

1. **模型设置**：

   - 在 Blender 中创建低多边形模型
   - 使用"设置世界网格"工具进行适当的像素艺术缩放
   - 根据像素密度要求设置网格细分

2. **UV 展开**：

   - 使用"像素完美展开"获得干净的、像素对齐的 UV
   - 或使用"展开到网格"进行基于网格的 UV 布局
   - 在 UV 编辑器中检查 UV 的正确对齐

3. **纹理创建**：
   - 将 UV 布局导出到 Pixelorama
   - 遵循 UV 指南创建像素艺术纹理
4. **最终集成**：
   - 将纹理导出回 Blender
   - 应用到模型并在 3D 视图中测试
   - 根据需要在任一应用程序中进行调整

## Blender 面板 UI

Blender 插件提供多个面板：

### 服务器面板

- **启动/停止服务器**：控制 WebSocket 服务器
- **连接状态**：显示连接的客户端和服务器状态
- **端口信息**：显示服务器连接详情

### UV 工具面板

- **像素完美展开**：像素完美精度的 UV 展开
- **展开到网格**：创建基于网格的 UV 布局
- **导出 UV**：将 UV 数据发送到 Pixelorama

### 纹理工具面板

- **检查纹理**：验证纹理尺寸和格式
- **创建棋盘**：生成棋盘图案纹理
- **重新加载纹理**：从磁盘刷新纹理

### 世界网格面板

- **设置世界网格**：为像素艺术配置 Blender 的网格
- **网格细分**：调整网格密度
- **缩放设置**：为像素工作设置适当的缩放

## 兼容性

### Blender

- **版本**：Blender 4.5.0 及更高版本
- **平台**：Windows、macOS、Linux

### Pixelorama

- **版本**：支持 Pixelorama API 版本 8
- **平台**：Windows、macOS、Linux

## 技术详情

### 通信协议

- **WebSocket**：实时双向通信
- **JSON 消息格式**：结构化数据交换
- **事件驱动**：更改时自动更新

### 支持的功能

- **图像格式**：PNG、JPG、BMP 和其他 Blender 支持的格式
- **UV 坐标**：完整的 UV 贴图同步

## 依赖项

### Blender 插件依赖项

- 无第三方 Python 包；WebSocket 服务由 Python 标准库实现
- Blender 4.5.0 或更高版本
- NumPy（包含在 Blender 中）

### Pixelorama 扩展依赖项

- Pixelorama 1.2.2（扩展 API 9）
- Godot 引擎（Pixelorama 运行时）

## 文件结构

```
blender-pixel-sync/
├── blender-part/              # Blender 插件 / 扩展
│   ├── blender_manifest.toml  # 扩展清单（id、权限、构建路径）
│   ├── __init__.py             # 插件注册
│   ├── server.py               # WebSocket 服务器
│   ├── operators.py            # Blender 操作符
│   ├── blender_integration.py  # Blender 集成逻辑
│   ├── uv_extractor.py         # UV 提取和处理
│   ├── image_manager.py        # 图像和纹理管理
│   ├── texture_processor.py    # 纹理处理工具
│   ├── unwrap_tools.py         # UV 展开算法
│   ├── ui.py                   # 用户界面面板
│   └── watch.py                # 文件监控和更改检测
└── blender-lorama/           # Pixelorama 扩展
    └── src/
        └── Extensions/
            └── BlenderPixelorama/
                ├── extension.json      # 扩展元数据
                ├── Main.gd            # 主扩展脚本
                ├── Main.tscn          # 主场景
                ├── BlenderLoramaPanel.tscn # UI 面板
                ├── WebSocketClient.gd # WebSocket 客户端
                ├── uv_overlay.gd      # UV 叠加功能
                ├── blender_lorama_panel.gd # 面板逻辑
                └── texture_exporter.gd # 纹理导出
```

## 贡献

欢迎贡献！请随时提交拉取请求、报告错误或建议功能。

### 开发设置

1. 克隆仓库
2. Blender 开发：使用 Blender 的脚本环境
3. Pixelorama 开发：使用带 Pixelorama 源码的 Godot 引擎
4. 使用两个运行中的应用程序测试更改

## 许可证

本项目采用 MIT 许可证 - 请参阅 LICENSE 文件了解详情。

## 致谢

- **原作者**：Heisenshark
- **重构者**：Assistant
- **Pixelorama 扩展**：yuchenyang1994
- **UV 展开算法**：基于 Nutti 的 Magic-UV

## 支持

如需问题、疑问或支持：

1. 查看 GitHub 问题页面
2. 查看文档了解常见解决方案
3. 报告问题时提供 Blender 和 Pixelorama 版本信息

## 版本历史

### v0.1.0

- 初始版本
- 基本 WebSocket 通信
- UV 导出/导入功能
- 像素完美展开工具
- 纹理同步
- 世界网格设置工具
