"""Pixel-grid UV layout tools for low-resolution textures."""

from __future__ import annotations

from collections import defaultdict
from math import ceil, cos, floor, pi, sin, sqrt
from typing import Iterable, Tuple

import bmesh
import bpy
from mathutils import Matrix, Vector


def _usable_image(image):
    return image if image is not None and image.size[0] > 0 and image.size[1] > 0 else None


def _target_for_layer_image(image):
    """Resolve a paint-layer datablock back to its composite target image."""
    if image is not None and image.get("blendlorama_layer"):
        return _usable_image(bpy.data.images.get(image.get("blendlorama_image_name", "")))
    return _usable_image(image)


def resolve_target_image(context, obj=None):
    """Find the texture whose real dimensions define the pixel grid."""
    scene = getattr(context, "scene", None)
    state = getattr(scene, "pixelorama_layer_state", None)
    image = _usable_image(bpy.data.images.get(getattr(state, "active_image", "")))
    if image:
        return image

    paint = getattr(getattr(scene, "tool_settings", None), "image_paint", None)
    image = _target_for_layer_image(getattr(paint, "canvas", None))
    if image:
        return image

    obj = obj or getattr(context, "active_object", None)
    mat = getattr(obj, "active_material", None)
    tree = getattr(mat, "node_tree", None)
    if tree:
        active = getattr(tree.nodes, "active", None)
        image = _target_for_layer_image(getattr(active, "image", None))
        if image:
            return image
        for node in tree.nodes:
            if node.type == "TEX_IMAGE":
                image = _target_for_layer_image(node.image)
                if image:
                    return image

    from .image_manager import ImageManager
    manager = ImageManager.INSTANCE
    return _target_for_layer_image(manager.get_image() if manager else None)


def _polygon_area(points: Iterable[Tuple[float, float]]) -> float:
    points = list(points)
    if len(points) < 3:
        return 0.0
    twice_area = 0.0
    for index, point in enumerate(points):
        next_point = points[(index + 1) % len(points)]
        twice_area += point[0] * next_point[1] - point[1] * next_point[0]
    return abs(twice_area) * 0.5


def _face_world_area(face, matrix_world: Matrix) -> float:
    """Measure a face after object scale, including non-uniform scale."""
    points = [matrix_world @ loop.vert.co for loop in face.loops]
    if len(points) < 3:
        return 0.0
    origin = points[0]
    return sum(
        ((points[index] - origin).cross(points[index + 1] - origin)).length * 0.5
        for index in range(1, len(points) - 1)
    )


def _face_pixel_area(face, uv_layer, width: int, height: int) -> float:
    return _polygon_area(
        (loop[uv_layer].uv.x * width, loop[uv_layer].uv.y * height)
        for loop in face.loops
    )


def _save_uvs(faces, uv_layer):
    return [(loop, loop[uv_layer].uv.copy()) for face in faces for loop in face.loops]


def _restore_uvs(saved, uv_layer):
    for loop, uv in saved:
        loop[uv_layer].uv = uv


class UVFace:
    """A face wrapper retained for callers that inspect UV bounds."""

    def __init__(self, face, uv_layer):
        self.face = face
        self.uv_layer = uv_layer
        self.min = Vector((0.0, 0.0))
        self.max = Vector((0.0, 0.0))
        self.center = Vector((0.0, 0.0))
        self.calc_info()

    def calc_info(self):
        coords = [loop[self.uv_layer].uv for loop in self.face.loops]
        if not coords:
            return
        self.min = Vector((min(uv.x for uv in coords), min(uv.y for uv in coords)))
        self.max = Vector((max(uv.x for uv in coords), max(uv.y for uv in coords)))
        self.center = sum((uv.copy() for uv in coords), Vector((0.0, 0.0))) / len(coords)


class UVIsland:
    """A connected UV island."""

    def __init__(self, faces, mesh, uv_layer):
        self.mesh = mesh
        self.uv_faces = [UVFace(face, uv_layer) for face in faces]
        self.uv_layer = uv_layer
        self.update_min_max()

    @property
    def faces(self):
        return [item.face for item in self.uv_faces]

    @property
    def loops(self):
        return [loop for face in self.faces for loop in face.loops]

    def get_faces(self):
        return iter(self.faces)

    def update_min_max(self):
        for face in self.uv_faces:
            face.calc_info()
        coords = [loop[self.uv_layer].uv for loop in self.loops]
        if not coords:
            self.min = self.max = self.average_uv = Vector((0.0, 0.0))
            self.num_uv = 0
            return
        self.min = Vector((min(uv.x for uv in coords), min(uv.y for uv in coords)))
        self.max = Vector((max(uv.x for uv in coords), max(uv.y for uv in coords)))
        self.average_uv = sum((uv.copy() for uv in coords), Vector((0.0, 0.0))) / len(coords)
        self.num_uv = len(coords)

    def pixel_bounds(self, width: int, height: int):
        points = [(loop[self.uv_layer].uv.x * width,
                   loop[self.uv_layer].uv.y * height) for loop in self.loops]
        epsilon = 1e-4
        return (
            floor(min(point[0] for point in points) + epsilon),
            floor(min(point[1] for point in points) + epsilon),
            ceil(max(point[0] for point in points) - epsilon),
            ceil(max(point[1] for point in points) - epsilon),
        )


def _rect_intersects(first, second):
    ax, ay, aw, ah = first
    bx, by, bw, bh = second
    return ax < bx + bw and ax + aw > bx and ay < by + bh and ay + ah > by


def _prune_rectangles(rectangles):
    unique = list(dict.fromkeys(rect for rect in rectangles if rect[2] > 0 and rect[3] > 0))
    result = []
    for index, rect in enumerate(unique):
        x, y, width, height = rect
        contained = False
        for other_index, other in enumerate(unique):
            if index == other_index:
                continue
            ox, oy, other_width, other_height = other
            if (x >= ox and y >= oy and x + width <= ox + other_width
                    and y + height <= oy + other_height):
                contained = True
                break
        if not contained:
            result.append(rect)
    return result


def _subtract_rectangle(free_rectangles, used):
    """MaxRects-style split that also accepts pre-existing occupied regions."""
    result = []
    ux, uy, used_width, used_height = used
    used_right, used_top = ux + used_width, uy + used_height
    for free in free_rectangles:
        if not _rect_intersects(free, used):
            result.append(free)
            continue
        fx, fy, free_width, free_height = free
        free_right, free_top = fx + free_width, fy + free_height
        if ux > fx:
            result.append((fx, fy, ux - fx, free_height))
        if used_right < free_right:
            result.append((used_right, fy, free_right - used_right, free_height))
        if uy > fy:
            result.append((fx, fy, free_width, uy - fy))
        if used_top < free_top:
            result.append((fx, used_top, free_width, free_top - used_top))
    return _prune_rectangles(result)


class UnwrapTools:
    """Texture-aware UV density, snapping, packing and validation."""

    @staticmethod
    def get_islands_from_obj(obj, only_selected=True):
        if obj.data.is_editmode:
            mesh = bmesh.from_edit_mesh(obj.data)
        else:
            mesh = bmesh.new()
            mesh.from_mesh(obj.data)
        return UnwrapTools.get_islands_from_mesh(mesh, only_selected)

    @staticmethod
    def get_islands_from_mesh(mesh, only_selected=True):
        if not mesh.loops.layers.uv:
            return []
        uv_layer = mesh.loops.layers.uv.verify()
        faces = [face for face in mesh.faces if face.select or not only_selected]
        return UnwrapTools.get_islands_for_faces(mesh, faces, uv_layer)

    @staticmethod
    def get_islands_for_faces(mesh, faces, uv_layer):
        mesh.faces.ensure_lookup_table()
        face_to_points = defaultdict(set)
        point_to_faces = defaultdict(set)
        for face in faces:
            for loop in face.loops:
                point = (loop[uv_layer].uv.to_tuple(6), loop.vert.index)
                face_to_points[face.index].add(point)
                point_to_faces[point].add(face.index)

        remaining = set(face_to_points)
        components = []
        while remaining:
            pending = [next(iter(remaining))]
            component = []
            while pending:
                face_index = pending.pop()
                if face_index not in remaining:
                    continue
                remaining.remove(face_index)
                component.append(face_index)
                for point in face_to_points[face_index]:
                    pending.extend(point_to_faces[point])
            components.append(component)
        return [UVIsland([mesh.faces[index] for index in component], mesh, uv_layer)
                for component in components]

    @staticmethod
    def texel_density(faces, uv_layer, image_size, matrix_world=Matrix.Identity(4)):
        width, height = image_size
        world_area = sum(_face_world_area(face, matrix_world) for face in faces)
        pixel_area = sum(_face_pixel_area(face, uv_layer, width, height) for face in faces)
        if world_area <= 1e-12 or pixel_area <= 1e-12:
            return 0.0
        return sqrt(pixel_area / world_area)

    @staticmethod
    def _correct_island_aspect(island, width, height):
        """Make Blender's normalized-square unwrap isotropic in texture pixels."""
        center = island.average_uv.copy()
        unit = min(width, height)
        center_px = Vector((center.x * width, center.y * height))
        for loop in island.loops:
            uv = loop[island.uv_layer].uv
            pixel = center_px + (uv - center) * unit
            uv.x, uv.y = pixel.x / width, pixel.y / height
        island.update_min_max()

    @staticmethod
    def _scale_island(island, scale, width, height):
        loops = island.loops
        points = [Vector((loop[island.uv_layer].uv.x * width,
                          loop[island.uv_layer].uv.y * height)) for loop in loops]
        center = sum(points, Vector((0.0, 0.0))) / len(points)
        for loop, point in zip(loops, points):
            point = center + (point - center) * scale
            loop[island.uv_layer].uv = (point.x / width, point.y / height)
        island.update_min_max()

    @staticmethod
    def _snap_island(island, width, height):
        proposed = {}
        for loop in island.loops:
            uv = loop[island.uv_layer].uv
            proposed[id(loop)] = (round(uv.x * width), round(uv.y * height))
        safe = all(
            _polygon_area(proposed[id(loop)] for loop in face.loops) >= 0.25
            for face in island.faces
        )
        if safe:
            for loop in island.loops:
                x, y = proposed[id(loop)]
                loop[island.uv_layer].uv = (x / width, y / height)
            island.update_min_max()
            return True

        # Very small faces would collapse when every vertex is rounded. Move the
        # whole island by one sub-pixel offset and preserve its shape instead.
        first = island.loops[0][island.uv_layer].uv
        anchor = Vector((first.x * width, first.y * height))
        delta = Vector((round(anchor.x) - anchor.x, round(anchor.y) - anchor.y))
        for loop in island.loops:
            uv = loop[island.uv_layer].uv
            uv.x += delta.x / width
            uv.y += delta.y / height
        island.update_min_max()
        return False

    @staticmethod
    def _occupied_rectangles(faces, uv_layer, width, height, padding):
        rectangles = []
        for face in faces:
            points = [(loop[uv_layer].uv.x * width, loop[uv_layer].uv.y * height)
                      for loop in face.loops]
            xmin = max(0, floor(min(point[0] for point in points)) - padding)
            ymin = max(0, floor(min(point[1] for point in points)) - padding)
            xmax = min(width, ceil(max(point[0] for point in points)) + padding)
            ymax = min(height, ceil(max(point[1] for point in points)) + padding)
            if xmax > xmin and ymax > ymin:
                rectangles.append((xmin, ymin, xmax - xmin, ymax - ymin))
        return rectangles

    @staticmethod
    def _pack_islands(islands, uv_layer, width, height, padding, occupied_faces=()):
        free = [(0, 0, width, height)]
        for occupied in UnwrapTools._occupied_rectangles(
                occupied_faces, uv_layer, width, height, padding):
            free = _subtract_rectangle(free, occupied)

        specs = []
        for island in islands:
            xmin, ymin, xmax, ymax = island.pixel_bounds(width, height)
            content_width = max(1, xmax - xmin)
            content_height = max(1, ymax - ymin)
            specs.append((island, xmin, ymin, content_width, content_height))
        specs.sort(key=lambda item: (max(item[3], item[4]), item[3] * item[4]), reverse=True)

        for island, xmin, ymin, content_width, content_height in specs:
            candidates = []
            for free_rect in free:
                fx, fy, free_width, free_height = free_rect
                orientations = [(False, content_width, content_height)]
                if content_width != content_height:
                    orientations.append((True, content_height, content_width))
                for rotated, packed_width, packed_height in orientations:
                    used_width = packed_width + 2 * padding
                    used_height = packed_height + 2 * padding
                    if used_width <= free_width and used_height <= free_height:
                        score = (min(free_width - used_width, free_height - used_height),
                                 free_width * free_height - used_width * used_height, fy, fx)
                        candidates.append((score, free_rect, rotated, used_width, used_height))
            if not candidates:
                return False
            _, free_rect, rotated, used_width, used_height = min(
                candidates, key=lambda item: item[0])
            place_x, place_y = free_rect[0], free_rect[1]
            loops = island.loops
            old_points = [(loop[uv_layer].uv.x * width, loop[uv_layer].uv.y * height)
                          for loop in loops]
            for loop, point in zip(loops, old_points):
                local_x, local_y = point[0] - xmin, point[1] - ymin
                if rotated:
                    local_x, local_y = local_y, content_width - local_x
                loop[uv_layer].uv = (
                    (place_x + padding + local_x) / width,
                    (place_y + padding + local_y) / height,
                )
            island.update_min_max()
            free = _subtract_rectangle(free, (place_x, place_y, used_width, used_height))
        return True

    @staticmethod
    def validate(faces, uv_layer, image_size):
        width, height = image_size
        for face in faces:
            if _face_pixel_area(face, uv_layer, width, height) < 0.01:
                raise ValueError(f"Face {face.index} collapsed below one hundredth of a pixel")
            for loop in face.loops:
                uv = loop[uv_layer].uv
                if uv.x < -1e-6 or uv.y < -1e-6 or uv.x > 1.0 + 1e-6 or uv.y > 1.0 + 1e-6:
                    raise ValueError(f"Face {face.index} lies outside the target texture")

    @staticmethod
    def pixel_perfect_layout(bm, faces, uv_layer, image_size, target_density,
                             matrix_world=Matrix.Identity(4), padding=2, snap=True):
        width, height = image_size
        islands = UnwrapTools.get_islands_for_faces(bm, faces, uv_layer)
        if not islands:
            raise ValueError("No UV islands were created")
        for island in islands:
            UnwrapTools._correct_island_aspect(island, width, height)

        baseline = _save_uvs(faces, uv_layer)
        base_density = {
            id(island): UnwrapTools.texel_density(
                island.faces, uv_layer, image_size, matrix_world)
            for island in islands
        }
        if any(value <= 0 for value in base_density.values()):
            raise ValueError("The selection contains a zero-area face or UV island")

        occupied_faces = [face for face in bm.faces if face not in faces]
        attempt_density = float(target_density)
        minimum_density = min(1.0, attempt_density)
        for _attempt in range(40):
            _restore_uvs(baseline, uv_layer)
            fallback_islands = 0
            for island in islands:
                island.update_min_max()
                UnwrapTools._scale_island(
                    island, attempt_density / base_density[id(island)], width, height)
                if snap and not UnwrapTools._snap_island(island, width, height):
                    fallback_islands += 1
            if UnwrapTools._pack_islands(
                    islands, uv_layer, width, height, padding, occupied_faces):
                UnwrapTools.validate(faces, uv_layer, image_size)
                achieved = UnwrapTools.texel_density(
                    faces, uv_layer, image_size, matrix_world)
                return {
                    "requested_density": target_density,
                    "fitted_density": attempt_density,
                    "achieved_density": achieved,
                    "islands": len(islands),
                    "snap_fallback_islands": fallback_islands,
                }
            attempt_density *= 0.9
            if attempt_density < minimum_density:
                break
        _restore_uvs(baseline, uv_layer)
        raise ValueError(
            f"UV islands do not fit in {width}x{height} with {padding}px padding")


class UV_OT_unwrap_pixel_perfect(bpy.types.Operator):
    """Unwrap, density-scale, pixel-snap and pack selected faces."""

    bl_idname = "uv.unwrap_pixel_perfect"
    bl_label = "Pixel Perfect Unwrap"
    bl_options = {"REGISTER", "UNDO"}

    target_density: bpy.props.FloatProperty(
        name="Target Density", description="Maximum pixels per world-space unit",
        default=32.0, min=1.0, max=1024.0)
    padding: bpy.props.IntProperty(
        name="Padding", description="Empty pixels around every packed UV island",
        default=2, min=0, max=32)
    snap_to_pixels: bpy.props.BoolProperty(
        name="Snap to Pixels", description="Snap safe UV vertices to pixel corners",
        default=True)

    @classmethod
    def poll(cls, context):
        return context.mode == "EDIT_MESH" and context.active_object and context.active_object.type == "MESH"

    def execute(self, context):
        obj = context.active_object
        image = resolve_target_image(context, obj)
        if image is None:
            self.report({"ERROR"}, "No Target Texture found; link or select a Blender image first")
            return {"CANCELLED"}
        image_size = (int(image.size[0]), int(image.size[1]))
        bm = bmesh.from_edit_mesh(obj.data)
        uv_layer = bm.loops.layers.uv.verify()
        selected_faces = [face for face in bm.faces if face.select]
        if not selected_faces:
            self.report({"ERROR"}, "No faces selected")
            return {"CANCELLED"}
        original = _save_uvs(selected_faces, uv_layer)
        try:
            has_seams = any(edge.seam for face in selected_faces for edge in face.edges)
            if has_seams:
                bpy.ops.uv.unwrap(method="ANGLE_BASED", fill_holes=True, correct_aspect=False,
                                  margin_method="SCALED", margin=0.001)
            else:
                bpy.ops.uv.smart_project(
                    angle_limit=1.15192, margin_method="SCALED", rotate_method="AXIS_ALIGNED",
                    island_margin=0.001, area_weight=0.0, correct_aspect=False,
                    scale_to_bounds=False)
            bm = bmesh.from_edit_mesh(obj.data)
            uv_layer = bm.loops.layers.uv.verify()
            selected_faces = [face for face in bm.faces if face.select]
            stats = UnwrapTools.pixel_perfect_layout(
                bm, selected_faces, uv_layer, image_size, self.target_density,
                obj.matrix_world, self.padding, self.snap_to_pixels)
        except Exception as exc:
            _restore_uvs(original, uv_layer)
            bmesh.update_edit_mesh(obj.data)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        bmesh.update_edit_mesh(obj.data)
        message = (f"{stats['islands']} islands on {image_size[0]}x{image_size[1]}; "
                   f"density {stats['achieved_density']:.1f} PPU")
        if stats["fitted_density"] < stats["requested_density"] * 0.99:
            message += f" (auto-fit from {stats['requested_density']:.1f})"
        if stats["snap_fallback_islands"]:
            message += f"; preserved {stats['snap_fallback_islands']} tiny islands"
        self.report({"INFO"}, message)
        return {"FINISHED"}


class UV_OT_unwrap_to_grid(bpy.types.Operator):
    """Place every selected face in a fixed-size integer pixel cell."""

    bl_idname = "uv.unwrap_to_grid"
    bl_label = "Face Grid Layout"
    bl_options = {"REGISTER", "UNDO"}

    grid_size: bpy.props.IntProperty(
        name="Face Size", description="Pixel width and height allocated to each face",
        default=8, min=2, max=256)
    padding: bpy.props.IntProperty(
        name="Padding", description="Empty pixels around every face cell",
        default=1, min=0, max=32)

    @classmethod
    def poll(cls, context):
        return context.mode == "EDIT_MESH" and context.active_object and context.active_object.type == "MESH"

    def execute(self, context):
        obj = context.active_object
        image = resolve_target_image(context, obj)
        if image is None:
            self.report({"ERROR"}, "No Target Texture found; link or select a Blender image first")
            return {"CANCELLED"}
        width, height = int(image.size[0]), int(image.size[1])
        stride = self.grid_size + self.padding * 2
        columns, rows = width // stride, height // stride
        bm = bmesh.from_edit_mesh(obj.data)
        uv_layer = bm.loops.layers.uv.verify()
        faces = [face for face in bm.faces if face.select]
        if not faces:
            self.report({"ERROR"}, "No faces selected")
            return {"CANCELLED"}
        if columns < 1 or rows < 1 or len(faces) > columns * rows:
            self.report({"ERROR"},
                        f"{len(faces)} faces do not fit in {width}x{height} at {self.grid_size}px")
            return {"CANCELLED"}

        original = _save_uvs(faces, uv_layer)
        for index, face in enumerate(faces):
            cell_x = (index % columns) * stride + self.padding
            cell_y = (index // columns) * stride + self.padding
            right, top = cell_x + self.grid_size, cell_y + self.grid_size
            count = len(face.loops)
            if count == 3:
                points = [(cell_x, cell_y), (right, cell_y),
                          ((cell_x + right) // 2, top)]
            elif count == 4:
                points = [(cell_x, cell_y), (right, cell_y),
                          (right, top), (cell_x, top)]
            else:
                center_x, center_y = (cell_x + right) / 2, (cell_y + top) / 2
                radius = self.grid_size / 2
                points = [
                    (round(center_x + cos(-pi / 2 + 2 * pi * i / count) * radius),
                     round(center_y + sin(-pi / 2 + 2 * pi * i / count) * radius))
                    for i in range(count)
                ]
            for loop, point in zip(face.loops, points):
                loop[uv_layer].uv = (point[0] / width, point[1] / height)

        try:
            UnwrapTools.validate(faces, uv_layer, (width, height))
        except ValueError as exc:
            _restore_uvs(original, uv_layer)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        bmesh.update_edit_mesh(obj.data)
        self.report({"INFO"},
                    f"Placed {len(faces)} faces in {self.grid_size}px cells on {width}x{height}")
        return {"FINISHED"}
