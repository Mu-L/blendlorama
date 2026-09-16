"""Topology-aware, integer-pixel UV edits for stylized low-poly assets."""

from __future__ import annotations

from collections import defaultdict, deque
from math import floor, log, sqrt

import bmesh
import bpy
from mathutils import Matrix, Vector

from .unwrap_tools import (
    UnwrapTools, _polygon_area, _restore_uvs, _save_uvs,
    resolve_target_image,
)


def _uv_selected(loop, uv_layer):
    # BMLoopUV.select was replaced by BMLoop.uv_select_vert in Blender 5.
    if hasattr(loop, "uv_select_vert"):
        return loop.uv_select_vert
    return loop[uv_layer].select


def _edge_selected(loop, uv_layer):
    if hasattr(loop, "uv_select_edge"):
        return loop.uv_select_edge
    return loop[uv_layer].select_edge


def _selection(bm, uv_layer, context):
    faces = list(bm.faces)
    if context.area and context.area.type == "IMAGE_EDITOR" and not context.scene.tool_settings.use_uv_select_sync:
        return [face for face in faces if face.select and all(_uv_selected(loop, uv_layer) for loop in face.loops)]
    return [face for face in faces if face.select]


def _edge_faces(bm, uv_layer, context):
    if context.area and context.area.type == "IMAGE_EDITOR" and not context.scene.tool_settings.use_uv_select_sync:
        seeds = [face for face in bm.faces if face.select and
                 any(_edge_selected(loop, uv_layer) for loop in face.loops)]
        if not seeds:
            return []
        visible = {face for face in bm.faces if face.select}
        found = set(seeds)
        queue = deque(seeds)
        while queue:
            face = queue.popleft()
            for loop in face.loops:
                neighbor = _edge_neighbor(face, loop, visible, uv_layer)
                if neighbor and neighbor[0] not in found:
                    found.add(neighbor[0])
                    queue.append(neighbor[0])
        return list(found)
    return [face for face in bm.faces if face.select]


def _edit_data(context, selection_kind="FACES"):
    obj = context.active_object
    if obj is None or obj.type != "MESH" or context.mode != "EDIT_MESH":
        raise ValueError("Select a mesh in Edit Mode")
    if len(context.objects_in_mode_unique_data) > 1:
        raise ValueError("Edit one mesh at a time with Pixelorama UV tools")
    image = resolve_target_image(context, obj)
    if image is None:
        raise ValueError("Select or link a Target Texture first")
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    bm.verts.ensure_lookup_table()
    uv_layer = bm.loops.layers.uv.verify()
    selected = (_edge_faces(bm, uv_layer, context) if selection_kind == "EDGES"
                else _selection(bm, uv_layer, context))
    if not selected:
        raise ValueError("Select complete UV faces or one UV edge chain first")
    return obj, bm, uv_layer, selected, (int(image.size[0]), int(image.size[1]))


def _signed_area(face, uv_layer, size):
    w, h = size
    points = [(loop[uv_layer].uv.x * w, loop[uv_layer].uv.y * h) for loop in face.loops]
    return sum(points[i][0] * points[(i + 1) % len(points)][1]
               - points[i][1] * points[(i + 1) % len(points)][0]
               for i in range(len(points))) * 0.5


def _stretch(face, uv_layer, size, matrix_world=Matrix.Identity(4)):
    """Largest surface pixel anisotropy across fan triangles of one face."""
    w, h = size
    loops = list(face.loops)
    max_ratio = 1.0
    for i in range(1, len(loops) - 1):
        tri = (loops[0], loops[i], loops[i + 1])
        p = [matrix_world @ loop.vert.co for loop in tri]
        q = [Vector((loop[uv_layer].uv.x * w, loop[uv_layer].uv.y * h)) for loop in tri]
        a, b = q[1] - q[0], q[2] - q[0]
        det = a.x * b.y - a.y * b.x
        if abs(det) < 1e-6:
            return float("inf")
        da, db = p[1] - p[0], p[2] - p[0]
        du = (da * b.y - db * a.y) / det
        dv = (db * a.x - da * b.x) / det
        aa, bb, dd = du.dot(du), du.dot(dv), dv.dot(dv)
        trace = aa + dd
        disc = sqrt(max(0.0, (aa - dd) ** 2 + 4 * bb ** 2))
        low, high = (trace - disc) * 0.5, (trace + disc) * 0.5
        if low <= 1e-12:
            return float("inf")
        max_ratio = max(max_ratio, sqrt(high / low))
    return max_ratio


def _face_problem(face, uv_layer, size, matrix_world, limit):
    w, h = size
    points = [(loop[uv_layer].uv.x * w, loop[uv_layer].uv.y * h) for loop in face.loops]
    stretch = _stretch(face, uv_layer, size, matrix_world)
    if (_polygon_area(points) < 0.5 or stretch == float("inf") or
            any(x < -1e-4 or y < -1e-4 or x > w + 1e-4 or y > h + 1e-4
                for x, y in points)):
        return "collapsed"
    if stretch > limit:
        return "stretched"
    if any(abs(v - round(v)) > 1e-4 for point in points for v in point):
        return "off_grid"
    return ""


def _edge_neighbor(face, loop, included, uv_layer):
    """Continue only through a truly welded UV edge and an unmarked seam."""
    if loop.edge.seam or not loop.edge.is_manifold:
        return None
    other = next((f for f in loop.edge.link_faces if f is not face and f in included), None)
    if other is None:
        return None
    current = {loop.vert: loop[uv_layer].uv,
               loop.link_loop_next.vert: loop.link_loop_next[uv_layer].uv}
    for opposite in other.loops:
        if opposite.edge is loop.edge:
            if all((((opposite if opposite.vert is vert else opposite.link_loop_next)[uv_layer].uv)
                    - uv).length < 1e-5 for vert, uv in current.items()):
                return other, opposite
    return None


def _quad_components(faces, uv_layer):
    included = set(faces)
    remaining = set(faces)
    components = []
    while remaining:
        root = min(remaining, key=lambda f: f.index)
        queue = deque([root])
        component = []
        remaining.remove(root)
        while queue:
            face = queue.popleft()
            component.append(face)
            for loop in face.loops:
                neighbor = _edge_neighbor(face, loop, included, uv_layer)
                if neighbor and neighbor[0] in remaining:
                    remaining.remove(neighbor[0])
                    queue.append(neighbor[0])
        components.append(component)
    return components


def _require_complete_islands(bm, selected, uv_layer):
    """Packing/moving a partial UV island would introduce an accidental seam."""
    chosen = set(selected)
    all_faces = set(bm.faces)
    for face in selected:
        for loop in face.loops:
            neighbor = _edge_neighbor(face, loop, all_faces, uv_layer)
            if neighbor and neighbor[0] not in chosen:
                raise ValueError("Select the whole UV island before straightening or packing; partial moves create seams")


def _grid_map(component, uv_layer, size, direction="AUTO"):
    """Embed a rectangular quad net in integer row/column *indices* first."""
    if any(len(face.loops) != 4 for face in component):
        raise ValueError("Smart Straighten needs quad faces; isolate triangles with a seam")
    root = min(component, key=lambda f: f.index)
    loops = list(root.loops)
    w, h = size
    lengths = [((loops[(i + 1) % 4][uv_layer].uv - loops[i][uv_layer].uv)
                * Vector((w, h))).length for i in range(4)]
    start = max(range(4), key=lambda i: lengths[i])
    sign = 1 if _signed_area(root, uv_layer, size) >= 0 else -1
    vertical = direction == "V" or (direction == "AUTO" and
        abs((loops[(start + 1) % 4][uv_layer].uv.y - loops[start][uv_layer].uv.y) * h) >
        abs((loops[(start + 1) % 4][uv_layer].uv.x - loops[start][uv_layer].uv.x) * w))
    axis = (0, 1) if vertical else (1, 0)
    # Choose the sign of the first edge to keep a familiar UV orientation.
    direction = loops[(start + 1) % 4][uv_layer].uv - loops[start][uv_layer].uv
    if axis == (1, 0) and direction.x < 0 or axis == (0, 1) and direction.y < 0:
        axis = (-axis[0], -axis[1])
    turn = (-axis[1] * sign, axis[0] * sign)
    root_coords = [None] * 4
    for idx, value in ((start, (0, 0)), ((start + 1) % 4, axis),
                       ((start + 2) % 4, (axis[0] + turn[0], axis[1] + turn[1])),
                       ((start + 3) % 4, turn)):
        root_coords[idx] = value
    mapping = {root: root_coords}
    included = set(component)
    queue = deque([root])
    while queue:
        face = queue.popleft()
        for loop in face.loops:
            neighbor = _edge_neighbor(face, loop, included, uv_layer)
            if not neighbor:
                continue
            other, opposite = neighbor
            local = list(face.loops).index(loop)
            remote = list(other.loops).index(opposite)
            a = mapping[face][local]
            b = mapping[face][(local + 1) % 4]
            # Opposite face traverses the shared edge backwards.
            ca = b if opposite.vert is loop.link_loop_next.vert else a
            cb = a if opposite.vert is loop.link_loop_next.vert else b
            d = (cb[0] - ca[0], cb[1] - ca[1])
            t = (-d[1] * sign, d[0] * sign)
            expected = [None] * 4
            for idx, value in ((remote, ca), ((remote + 1) % 4, cb),
                               ((remote + 2) % 4, (cb[0] + t[0], cb[1] + t[1])),
                               ((remote + 3) % 4, (ca[0] + t[0], ca[1] + t[1]))):
                expected[idx] = value
            if other in mapping:
                if mapping[other] != expected:
                    raise ValueError("Quad patch cannot form a consistent rectangular grid; add a seam")
            else:
                mapping[other] = expected
                queue.append(other)
    if len(mapping) != len(component):
        raise ValueError("Quad patch contains a disconnected face")
    occupied = set()
    for coords in mapping.values():
        center = (sum(x for x, _ in coords) / 4, sum(y for _, y in coords) / 4)
        if center in occupied:
            raise ValueError("Quad rows overlap; add a seam or straighten smaller patches")
        occupied.add(center)
    return mapping


def _column_lengths(mapping, matrix_world):
    xs = sorted({x for coords in mapping.values() for x, _ in coords})
    ys = sorted({y for coords in mapping.values() for _, y in coords})
    if any(b - a != 1 for axis in (xs, ys) for a, b in zip(axis, axis[1:])):
        raise ValueError("Quad patch has discontinuous rows or columns")
    horizontal, vertical = defaultdict(list), defaultdict(list)
    for face, coords in mapping.items():
        loops = list(face.loops)
        for i in range(4):
            p, q = coords[i], coords[(i + 1) % 4]
            length = ((matrix_world @ loops[i].vert.co)
                      - (matrix_world @ loops[(i + 1) % 4].vert.co)).length
            if p[1] == q[1] and abs(p[0] - q[0]) == 1:
                horizontal[min(p[0], q[0])].append(length)
            elif p[0] == q[0] and abs(p[1] - q[1]) == 1:
                vertical[min(p[1], q[1])].append(length)
    if any(not horizontal[x] for x in xs[:-1]) or any(not vertical[y] for y in ys[:-1]):
        raise ValueError("Quad patch contains an invalid row or column")
    return xs, ys, [sum(horizontal[x]) / len(horizontal[x]) for x in xs[:-1]], [sum(vertical[y]) / len(vertical[y]) for y in ys[:-1]]


def _integer_segments(lengths, density, max_size):
    ideal = [value * density for value in lengths]
    target = round(sum(ideal))
    if target < len(lengths):
        raise ValueError("Target density gives less than one pixel per row or column")
    if target > max_size:
        raise ValueError(f"Patch needs {target} pixels but Target Texture has only {max_size}; lower density or split the patch")
    result = [max(1, floor(v)) for v in ideal]
    while sum(result) < target:
        i = max(range(len(result)), key=lambda n: ideal[n] - result[n])
        result[i] += 1
    while sum(result) > target:
        candidates = [i for i in range(len(result)) if result[i] > 1]
        if not candidates:
            raise ValueError("Not enough pixels for this quad patch")
        i = min(candidates, key=lambda n: ideal[n] - result[n])
        result[i] -= 1
    return result


def _place_grid(mapping, xs, ys, widths, heights, uv_layer, size, origin):
    w, h = size
    x_values, y_values = {xs[0]: origin[0]}, {ys[0]: origin[1]}
    for x, step in zip(xs, widths):
        x_values[x + 1] = x_values[x] + step
    for y, step in zip(ys, heights):
        y_values[y + 1] = y_values[y] + step
    for face, coords in mapping.items():
        for loop, (x, y) in zip(face.loops, coords):
            loop[uv_layer].uv = (x_values[x] / w, y_values[y] / h)


def _smart_component(component, uv_layer, size, matrix_world, density, limit, padding, direction):
    mapping = _grid_map(component, uv_layer, size, direction)
    xs, ys, x_lengths, y_lengths = _column_lengths(mapping, matrix_world)
    w, h = size
    widths = _integer_segments(x_lengths, density, w - 2 * padding)
    heights = _integer_segments(y_lengths, density, h - 2 * padding)
    original = _save_uvs(component, uv_layer)
    old_min = (min(loop[uv_layer].uv.x * w for face in component for loop in face.loops),
               min(loop[uv_layer].uv.y * h for face in component for loop in face.loops))
    origin = (max(0, min(w - sum(widths), round(old_min[0]))),
              max(0, min(h - sum(heights), round(old_min[1]))))

    def evaluate(a, b):
        _place_grid(mapping, xs, ys, a, b, uv_layer, size, origin)
        ratios = [_stretch(face, uv_layer, size, matrix_world) for face in component]
        if any(r == float("inf") for r in ratios):
            return float("inf"), float("inf")
        # Worst-face penalty prevents one thin trapezoid face carrying all distortion.
        value = sum(log(r) ** 2 for r in ratios) + len(ratios) * log(max(ratios)) ** 2
        value += 0.15 * sum(log(max(1e-6, v / (l * density))) ** 2
                            for v, l in zip(a + b, x_lengths + y_lengths))
        return value, max(ratios)

    best, ratio = evaluate(widths, heights)
    # Local integer search balances stretch across rows without giving up density.
    for _ in range(3):
        improved = False
        for segments, other, limit_size, horizontal in ((widths, heights, w - 2 * padding, True),
                                                         (heights, widths, h - 2 * padding, False)):
            for i in range(len(segments)):
                old = segments[i]
                chosen = old
                for candidate in range(max(1, old - 2), old + 3):
                    if sum(segments) - old + candidate > limit_size:
                        continue
                    segments[i] = candidate
                    score, stretch = evaluate(widths, heights)
                    if score < best - 1e-8:
                        best, ratio, chosen = score, stretch, candidate
                segments[i] = chosen
                improved |= chosen != old
        if not improved:
            break
    _place_grid(mapping, xs, ys, widths, heights, uv_layer, size, origin)
    if ratio > limit:
        _restore_uvs(original, uv_layer)
        raise ValueError(f"Face stretch {ratio:.2f}x exceeds {limit:.2f}x; add a seam, split the patch, or raise Max Stretch")
    if any(_signed_area(face, uv_layer, size) * _signed_area_saved(face, original, size) <= 0
           for face in component):
        _restore_uvs(original, uv_layer)
        raise ValueError("Straightening would flip a UV face")
    return ratio


def _signed_area_saved(face, saved, size):
    saved_by_loop = {loop: uv for loop, uv in saved}
    w, h = size
    points = [(saved_by_loop[loop].x * w, saved_by_loop[loop].y * h) for loop in face.loops]
    return sum(points[i][0] * points[(i + 1) % len(points)][1]
               - points[i][1] * points[(i + 1) % len(points)][0]
               for i in range(len(points))) * 0.5


def _snap_component(component, uv_layer, size):
    w, h = size
    saved = _save_uvs(component, uv_layer)
    # A welded vertex gets one integer corner, including across neighboring faces.
    groups = defaultdict(list)
    for face in component:
        for loop in face.loops:
            uv = loop[uv_layer].uv
            groups[(loop.vert.index, round(uv.x, 5), round(uv.y, 5))].append(loop)
    for loops in groups.values():
        if any(loop[uv_layer].pin_uv for loop in loops):
            if any(abs(v - round(v)) > 1e-4 for v in (loops[0][uv_layer].uv.x * w,
                                                       loops[0][uv_layer].uv.y * h)):
                raise ValueError("Pinned UV is off-grid; unpin it or move it to a pixel corner")
            continue
        original = loops[0][uv_layer].uv
        target = (round(original.x * w) / w, round(original.y * h) / h)
        for loop in loops:
            loop[uv_layer].uv = target
    for face in component:
        if _polygon_area((loop[uv_layer].uv.x * w, loop[uv_layer].uv.y * h)
                         for loop in face.loops) < 0.5 or _signed_area(face, uv_layer, size) * _signed_area_saved(face, saved, size) <= 0:
            _restore_uvs(saved, uv_layer)
            raise ValueError(f"Face {face.index} would collapse or flip on the pixel grid; give it more pixels")


class _UVOperator(bpy.types.Operator):
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "EDIT_MESH" and context.active_object and context.active_object.type == "MESH"

    def _run(self, context, operation, selection_kind="FACES"):
        try:
            obj, bm, uv_layer, selected, size = _edit_data(context, selection_kind)
            saved = _save_uvs(selected, uv_layer)
            try:
                message = operation(context, obj, bm, uv_layer, selected, size)
                UnwrapTools.validate(selected, uv_layer, size)
            except Exception:
                _restore_uvs(saved, uv_layer)
                raise
            bmesh.update_edit_mesh(obj.data)
            self.report({"INFO"}, message)
            return {"FINISHED"}
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class UV_OT_smart_straighten(_UVOperator):
    bl_idname = "uv.pixel_smart_straighten"
    bl_label = "Smart Straighten + Pixel Align"
    bl_description = "Straighten quad patches by rows and columns, allocate integer pixels, and check face stretch"

    density: bpy.props.FloatProperty(name="Density", default=32.0, min=1.0, max=1024.0)
    max_stretch: bpy.props.FloatProperty(name="Max Stretch", default=2.5, min=1.0, max=20.0)
    padding: bpy.props.IntProperty(name="Padding", default=2, min=0, max=32)
    direction: bpy.props.EnumProperty(name="Patch Direction",
        items=(("AUTO", "Auto", "Use the current longest UV edge"),
               ("U", "Horizontal", "Place that edge along U"),
               ("V", "Vertical", "Place that edge along V")), default="AUTO")

    def execute(self, context):
        def operation(context, obj, bm, uv_layer, selected, size):
            _require_complete_islands(bm, selected, uv_layer)
            components = _quad_components(selected, uv_layer)
            if any(loop[uv_layer].pin_uv for face in selected for loop in face.loops):
                raise ValueError("Pinned UVs constrain this patch; unpin before Smart Straighten")
            ratios = []
            for component in components:
                ratios.append(_smart_component(component, uv_layer, size, obj.matrix_world,
                                               self.density, self.max_stretch, self.padding, self.direction))
            return f"Straightened {len(components)} quad patches; max pixel stretch {max(ratios):.2f}x"
        return self._run(context, operation)


class UV_OT_pixel_align(_UVOperator):
    bl_idname = "uv.pixel_align_selected"
    bl_label = "Align UVs to Pixels"
    bl_description = "Snap welded UV corners together; reject face collapses and pinned off-grid points"

    def execute(self, context):
        def operation(context, obj, bm, uv_layer, selected, size):
            for component in _quad_components(selected, uv_layer):
                _snap_component(component, uv_layer, size)
            return f"Aligned {len(selected)} faces to {size[0]} x {size[1]} pixel grid"
        return self._run(context, operation)


class UV_OT_pixel_pack(_UVOperator):
    bl_idname = "uv.pixel_pack_selected"
    bl_label = "Pack UV Islands on Pixels"
    bl_description = "Pack selected UV islands with integer translations, right-angle rotations and pixel padding"

    padding: bpy.props.IntProperty(name="Padding", default=2, min=0, max=32)

    def execute(self, context):
        def operation(context, obj, bm, uv_layer, selected, size):
            _require_complete_islands(bm, selected, uv_layer)
            islands = UnwrapTools.get_islands_for_faces(bm, selected, uv_layer)
            if any(loop[uv_layer].pin_uv for island in islands for loop in island.loops):
                raise ValueError("Pinned UV islands cannot be packed; unpin or leave them unselected")
            for island in islands:
                if any(abs(v - round(v)) > 1e-4 for loop in island.loops
                       for v in (loop[uv_layer].uv.x * size[0], loop[uv_layer].uv.y * size[1])):
                    raise ValueError("Align selected islands to pixels before packing")
            occupied = [face for face in bm.faces if face not in selected]
            if not UnwrapTools._pack_islands(islands, uv_layer, *size, self.padding, occupied):
                raise ValueError("Islands do not fit at their current pixel sizes; reduce padding, split an island or enlarge Target Texture")
            return f"Packed {len(islands)} pixel-aligned islands with {self.padding}px padding"
        return self._run(context, operation)


class UV_OT_straighten_edge(_UVOperator):
    bl_idname = "uv.pixel_straighten_edge"
    bl_label = "Straighten UV Edge Chain"
    bl_description = "Straighten a selected UV edge chain on an integer pixel line, smoothing nearby vertices"

    axis: bpy.props.EnumProperty(name="Direction", items=(("AUTO", "Auto", ""),
                                                            ("U", "Horizontal", ""),
                                                            ("V", "Vertical", "")), default="AUTO")

    def execute(self, context):
        def operation(context, obj, bm, uv_layer, selected, size):
            w, h = size
            selected_set = set(selected)
            edge_loops = [loop for face in selected for loop in face.loops
                          if _edge_selected(loop, uv_layer) or
                          (context.scene.tool_settings.use_uv_select_sync and loop.edge.select)]
            if not edge_loops:
                raise ValueError("Select one open UV edge chain in the UV Editor")
            groups = defaultdict(list)
            key_of = {}
            for face in selected:
                for loop in face.loops:
                    uv = loop[uv_layer].uv
                    key = (loop.vert.index, round(uv.x, 5), round(uv.y, 5))
                    groups[key].append(loop)
                    key_of[loop] = key
            graph = defaultdict(set)
            for loop in edge_loops:
                a, b = key_of[loop], key_of[loop.link_loop_next]
                if a == b:
                    continue
                graph[a].add(b)
                graph[b].add(a)
            endpoints = [key for key, neighbors in graph.items() if len(neighbors) == 1]
            if len(endpoints) != 2 or any(len(n) > 2 for n in graph.values()):
                raise ValueError("Select a single open, unbranched UV edge chain")
            chain = [endpoints[0]]
            while chain[-1] != endpoints[1]:
                next_nodes = graph[chain[-1]] - set(chain)
                if len(next_nodes) != 1:
                    raise ValueError("Selected UV edges form disconnected chains")
                chain.append(next(iter(next_nodes)))
            if len(chain) != len(graph):
                raise ValueError("Select only one UV edge chain")
            uv_points = {key: groups[key][0][uv_layer].uv.copy() for key in groups}
            horizontal = self.axis == "U" or (self.axis == "AUTO" and
                abs((uv_points[chain[-1]].x - uv_points[chain[0]].x) * w) >=
                abs((uv_points[chain[-1]].y - uv_points[chain[0]].y) * h))
            perpendicular = 1 if horizontal else 0
            scale = h if horizontal else w
            fixed_pixel = round(sum(uv_points[key][perpendicular] * scale for key in chain) / len(chain))
            fixed = fixed_pixel / scale
            if any(loop[uv_layer].pin_uv and abs(uv_points[key][perpendicular] - fixed) > 1e-5
                   for key in chain for loop in groups[key]):
                raise ValueError("Pinned UV conflicts with the straightened edge line")
            displacement = {key: Vector((0.0, 0.0)) for key in groups}
            for key in chain:
                displacement[key][perpendicular] = fixed - uv_points[key][perpendicular]
            # Harmonic displacement across the selected patch; original along-chain spacing stays intact.
            adjacency = defaultdict(set)
            for face in selected:
                for loop in face.loops:
                    a, b = key_of[loop], key_of[loop.link_loop_next]
                    adjacency[a].add(b)
                    adjacency[b].add(a)
            chain_set = set(chain)
            pinned = {key for key, loops in groups.items() if any(loop[uv_layer].pin_uv for loop in loops)}
            boundary = {key_of[corner] for face in selected for corner in face.loops
                        if _edge_neighbor(face, corner, selected_set, uv_layer) is None}
            boundary.update(key_of[corner.link_loop_next] for face in selected for corner in face.loops
                            if _edge_neighbor(face, corner, selected_set, uv_layer) is None)
            free = set(groups) - chain_set - pinned - boundary
            for _ in range(100):
                updates = {key: sum((displacement[n] for n in adjacency[key]), Vector((0.0, 0.0))) / len(adjacency[key])
                           for key in free if adjacency[key]}
                displacement.update(updates)
            for key, loops in groups.items():
                target = uv_points[key] + displacement[key]
                for loop in loops:
                    loop[uv_layer].uv = target
            if any(_signed_area(face, uv_layer, size) * _signed_area_saved(face,
                    [(loop, uv_points[key_of[loop]]) for f in selected for loop in f.loops], size) <= 0
                   for face in selected):
                raise ValueError("Straightening this edge would flip a face; select a shorter chain")
            return f"Straightened {len(chain) - 1} edges on {'V' if horizontal else 'U'}={fixed_pixel}px"
        return self._run(context, operation, "EDGES")


class UV_OT_check_pixels(_UVOperator):
    bl_idname = "uv.pixel_check_selected"
    bl_label = "Check Pixel UVs"
    bl_description = "Report collapsed, off-grid or overstretched selected UV faces"

    def execute(self, context):
        try:
            obj, bm, uv_layer, selected, size = _edit_data(context)
            limit = context.scene.pixel_uv_max_stretch
            counts = defaultdict(int)
            for face in selected:
                counts[_face_problem(face, uv_layer, size, obj.matrix_world, limit)] += 1
            summary = ", ".join(f"{key}: {count}" for key, count in counts.items() if key)
            self.report({"WARNING"} if summary else {"INFO"}, summary or "Selected UVs are pixel aligned and within stretch limit")
            return {"FINISHED"}
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


UV_EDIT_CLASSES = (UV_OT_smart_straighten, UV_OT_straighten_edge,
                   UV_OT_pixel_align, UV_OT_pixel_pack, UV_OT_check_pixels)


_issue_draw_handle = None


def _draw_uv_issues():
    context = bpy.context
    scene = context.scene
    if not scene or not getattr(scene, "pixel_uv_show_issues", False):
        return
    if not context.area or context.area.type != "IMAGE_EDITOR" or context.space_data.ui_type != "UV":
        return
    obj = context.active_object
    if not obj or obj.type != "MESH" or not obj.data.is_editmode:
        return
    image = resolve_target_image(context, obj)
    if not image:
        return
    try:
        import gpu
        from gpu_extras.batch import batch_for_shader
        bm = bmesh.from_edit_mesh(obj.data)
        uv_layer = bm.loops.layers.uv.active
        if uv_layer is None:
            return
        size = (int(image.size[0]), int(image.size[1]))
        lines = defaultdict(list)
        view = context.region.view2d
        highlighted = _selection(bm, uv_layer, context) or _edge_faces(bm, uv_layer, context)
        for face in highlighted[:5000]:
            problem = _face_problem(face, uv_layer, size, obj.matrix_world,
                                    scene.pixel_uv_max_stretch)
            if not problem:
                continue
            coords = [view.view_to_region(loop[uv_layer].uv.x,
                                          loop[uv_layer].uv.y, clip=False)
                      for loop in face.loops]
            for a, b in zip(coords, coords[1:] + coords[:1]):
                lines[problem].extend((a, b))
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        colors = {"collapsed": (1.0, 0.1, 0.15, 0.9),
                  "off_grid": (1.0, 0.6, 0.1, 0.9),
                  "stretched": (0.9, 0.2, 1.0, 0.9)}
        gpu.state.blend_set("ALPHA")
        gpu.state.line_width_set(2.0)
        for problem, points in lines.items():
            batch = batch_for_shader(shader, "LINES", {"pos": points})
            shader.bind()
            shader.uniform_float("color", colors[problem])
            batch.draw(shader)
        gpu.state.line_width_set(1.0)
        gpu.state.blend_set("NONE")
    except Exception as exc:
        # Drawing must never interrupt UV editing; operators still report errors.
        print(f"[Pixelorama UV] issue overlay: {exc}")


def register_issue_overlay():
    global _issue_draw_handle
    if _issue_draw_handle is None:
        _issue_draw_handle = bpy.types.SpaceImageEditor.draw_handler_add(
            _draw_uv_issues, (), "WINDOW", "POST_PIXEL")


def unregister_issue_overlay():
    global _issue_draw_handle
    if _issue_draw_handle is not None:
        bpy.types.SpaceImageEditor.draw_handler_remove(_issue_draw_handle, "WINDOW")
        _issue_draw_handle = None
