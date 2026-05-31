bl_info = {
    "name": "Vertex Data Tools",
    "author": "ChatGPT",
    "version": (1, 9, 2),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > Tool",
    "description": "Tools for managing vertex color and vertex group workflows",
    "category": "Mesh",
}

import bpy
from bpy.props import FloatProperty, StringProperty, PointerProperty
from bpy.types import Operator, Panel, PropertyGroup


VALID_COLOR_TYPES = {'BYTE_COLOR', 'FLOAT_COLOR'}
VALID_COLOR_DOMAINS = {'POINT', 'CORNER'}


# -----------------------------------------------------------------------------
# Common selection helpers
# -----------------------------------------------------------------------------

def collect_selection(mesh, use_vert, use_edge, use_face):
    selected_vertices = set()
    selected_edges = set()
    selected_faces = set()
    selected_loops = set()

    if use_vert:
        for v in mesh.vertices:
            if v.select:
                selected_vertices.add(v.index)

    if use_edge:
        for e in mesh.edges:
            if e.select:
                selected_edges.add(e.index)
                selected_vertices.update(e.vertices)

    if use_face:
        for poly in mesh.polygons:
            if poly.select:
                selected_faces.add(poly.index)
                selected_vertices.update(poly.vertices)
                selected_loops.update(poly.loop_indices)

    return selected_vertices, selected_edges, selected_faces, selected_loops


def get_context_targets(context):
    """
    Returns:
        original_mode, active_obj, target_objects, use_vert, use_edge, use_face, apply_all

    Object Mode:
        Applies to whole selected mesh objects.

    Edit Mode:
        Applies only to selected mesh elements.
    """

    original_mode = context.mode
    active_obj = context.view_layer.objects.active

    if original_mode == 'EDIT_MESH':
        target_objects = list(context.objects_in_mode_unique_data)
        use_vert, use_edge, use_face = context.tool_settings.mesh_select_mode[:]
        apply_all = False

        if not (use_vert or use_edge or use_face):
            use_vert = use_edge = use_face = True

        # Flush edit-mode selection to mesh data.
        bpy.ops.object.mode_set(mode='OBJECT')

    else:
        target_objects = [
            obj for obj in context.selected_objects
            if obj.type == 'MESH'
        ]
        use_vert = use_edge = use_face = True
        apply_all = True

    return original_mode, active_obj, target_objects, use_vert, use_edge, use_face, apply_all


def restore_mode_if_needed(context, original_mode, active_obj):
    if original_mode == 'EDIT_MESH':
        context.view_layer.objects.active = active_obj
        bpy.ops.object.mode_set(mode='EDIT')


# -----------------------------------------------------------------------------
# Add-on properties
# -----------------------------------------------------------------------------

class VDT_Properties(PropertyGroup):
    vertex_group_name: StringProperty(
        name="Vertex Group Name",
        default="head",
        description="Vertex group name to create or edit",
    )


# -----------------------------------------------------------------------------
# Vertex Color helpers
# -----------------------------------------------------------------------------

def is_real_color_attribute(attr):
    """
    custom_normal 같은 일반 Attribute가 아니라,
    진짜 Color Attribute인지 확인.
    """

    if attr is None:
        return False

    if getattr(attr, "domain", None) not in VALID_COLOR_DOMAINS:
        return False

    if getattr(attr, "data_type", None) not in VALID_COLOR_TYPES:
        return False

    # BYTE_COLOR / FLOAT_COLOR라면 보통 data element에 .color가 있어야 함
    try:
        if len(attr.data) > 0 and not hasattr(attr.data[0], "color"):
            return False
    except Exception:
        return False

    return True


def unique_attribute_name(mesh, base_name="Color"):
    name = base_name
    index = 1

    while mesh.attributes.get(name) is not None:
        name = f"{base_name}.{index:03d}"
        index += 1

    return name


def set_active_color_attribute(mesh, attr):
    """
    생성/선택한 Color Attribute를 활성화.
    """

    if attr is None:
        return

    color_attrs = getattr(mesh, "color_attributes", None)

    if color_attrs is None:
        return

    try:
        for i, color_attr in enumerate(color_attrs):
            if color_attr.name == attr.name:
                color_attrs.active_color_index = i

                if hasattr(color_attrs, "render_color_index"):
                    color_attrs.render_color_index = i

                break
    except Exception:
        pass


def get_active_real_color_attribute(mesh):
    """
    현재 활성화된 Color Attribute를 가져온다.
    단, custom_normal 같은 일반 Attribute는 제외.
    """

    color_attrs = getattr(mesh, "color_attributes", None)

    if color_attrs is None:
        return None

    candidates = [
        getattr(color_attrs, "active_color", None),
        getattr(color_attrs, "active", None),
    ]

    for attr in candidates:
        if is_real_color_attribute(attr):
            return attr

    # 활성 Attribute가 이상하면, 전체 Color Attributes 중 진짜 컬러만 찾기
    for attr in color_attrs:
        if is_real_color_attribute(attr):
            set_active_color_attribute(mesh, attr)
            return attr

    return None


def get_or_create_color_attribute(mesh):
    """
    진짜 Color Attribute가 있으면 사용.
    없으면 Color라는 이름으로 BYTE_COLOR / CORNER 생성.
    """

    attr = get_active_real_color_attribute(mesh)

    if attr is not None:
        return attr

    name = unique_attribute_name(mesh, "Color")

    try:
        attr = mesh.color_attributes.new(
            name=name,
            type='BYTE_COLOR',
            domain='CORNER'
        )
    except Exception:
        try:
            attr = mesh.attributes.new(
                name=name,
                type='BYTE_COLOR',
                domain='CORNER'
            )
        except Exception:
            return None

    if not is_real_color_attribute(attr):
        return None

    set_active_color_attribute(mesh, attr)
    return attr


def apply_color_to_corner_domain(
    mesh,
    color_attr,
    color,
    use_vert,
    use_edge,
    use_face,
    apply_all=False
):
    """
    CORNER 도메인 Color Attribute에 값 적용.
    Object Mode에서는 전체 loop에 적용.
    Edit Mode에서는 선택된 vertex / edge / face에 연결된 loop에 적용.
    """

    # Object Mode에서 오브젝트만 선택한 경우: 전체 적용
    if apply_all:
        for i in range(len(color_attr.data)):
            color_attr.data[i].color = color

        return len(color_attr.data)

    selected_loops = set()

    selected_vertices, selected_edges, selected_faces, face_loops = collect_selection(
        mesh,
        use_vert,
        use_edge,
        use_face
    )

    # Face 선택이면 선택된 face 전체 corner에 적용
    if use_face:
        selected_loops.update(face_loops)

    # Edge 선택이면 해당 edge가 포함된 face corner에 적용
    if use_edge:
        selected_edge_keys = set()

        for edge_index in selected_edges:
            e = mesh.edges[edge_index]
            selected_edge_keys.add(tuple(sorted(e.vertices)))

        for poly in mesh.polygons:
            poly_vertices = list(poly.vertices)
            poly_loops = list(poly.loop_indices)
            count = len(poly_vertices)

            for i in range(count):
                v_current = poly_vertices[i]
                v_next = poly_vertices[(i + 1) % count]

                loop_current = poly_loops[i]
                loop_next = poly_loops[(i + 1) % count]

                edge_key = tuple(sorted((v_current, v_next)))

                if edge_key in selected_edge_keys:
                    selected_loops.add(loop_current)
                    selected_loops.add(loop_next)

    # Vertex 선택이면 해당 vertex가 쓰인 모든 corner에 적용
    if use_vert:
        for poly in mesh.polygons:
            for loop_index in poly.loop_indices:
                vertex_index = mesh.loops[loop_index].vertex_index

                if vertex_index in selected_vertices:
                    selected_loops.add(loop_index)

    for loop_index in selected_loops:
        color_attr.data[loop_index].color = color

    return len(selected_loops)


def apply_color_to_point_domain(
    mesh,
    color_attr,
    color,
    use_vert,
    use_edge,
    use_face,
    apply_all=False
):
    """
    POINT 도메인 Color Attribute에 값 적용.
    Object Mode에서는 전체 vertex에 적용.
    Edit Mode에서는 선택된 vertex / edge / face에 연결된 vertex에 적용.
    """

    # Object Mode에서 오브젝트만 선택한 경우: 전체 적용
    if apply_all:
        for i in range(len(color_attr.data)):
            color_attr.data[i].color = color

        return len(color_attr.data)

    selected_vertices, selected_edges, selected_faces, selected_loops = collect_selection(
        mesh,
        use_vert,
        use_edge,
        use_face
    )

    for vertex_index in selected_vertices:
        color_attr.data[vertex_index].color = color

    return len(selected_vertices)


class MESH_OT_vdt_set_active_vertex_color_value(Operator):
    bl_idname = "mesh.vdt_set_active_vertex_color_value"
    bl_label = "Set Vertex Color Value"
    bl_options = {'REGISTER', 'UNDO'}

    value: FloatProperty(
        name="Value",
        default=1.0,
        min=0.0,
        max=1.0,
    )

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == 'MESH'

    def execute(self, context):
        original_mode, active_obj, target_objects, use_vert, use_edge, use_face, apply_all = get_context_targets(context)

        if not target_objects:
            restore_mode_if_needed(context, original_mode, active_obj)
            self.report({'ERROR'}, "No mesh objects found.")
            return {'CANCELLED'}

        color = (self.value, self.value, self.value, 1.0)

        total_count = 0
        skipped = []

        try:
            for obj in target_objects:
                mesh = obj.data

                color_attr = get_or_create_color_attribute(mesh)

                if color_attr is None:
                    skipped.append(f"{obj.name}: could not create or find real color attribute")
                    continue

                if not is_real_color_attribute(color_attr):
                    skipped.append(f"{obj.name}: active attribute is not real vertex color")
                    continue

                domain = color_attr.domain

                if domain == 'CORNER':
                    count = apply_color_to_corner_domain(
                        mesh,
                        color_attr,
                        color,
                        use_vert,
                        use_edge,
                        use_face,
                        apply_all=apply_all
                    )

                elif domain == 'POINT':
                    count = apply_color_to_point_domain(
                        mesh,
                        color_attr,
                        color,
                        use_vert,
                        use_edge,
                        use_face,
                        apply_all=apply_all
                    )

                else:
                    skipped.append(f"{obj.name}: unsupported color domain {domain}")
                    continue

                total_count += count
                mesh.update()

        finally:
            restore_mode_if_needed(context, original_mode, active_obj)

        if skipped:
            self.report(
                {'WARNING'},
                "Skipped: " + " | ".join(skipped)
            )
        else:
            if apply_all:
                self.report(
                    {'INFO'},
                    f"Set whole selected object vertex color to {self.value} on {total_count} elements."
                )
            else:
                self.report(
                    {'INFO'},
                    f"Set selected mesh element vertex color to {self.value} on {total_count} elements."
                )

        return {'FINISHED'}


# -----------------------------------------------------------------------------
# Vertex Group helpers/operators
# -----------------------------------------------------------------------------

def get_or_create_vertex_group(obj, group_name):
    group = obj.vertex_groups.get(group_name)

    if group is None:
        group = obj.vertex_groups.new(name=group_name)

    return group


def collect_vertex_group_indices(mesh, use_vert, use_edge, use_face, apply_all=False):
    if apply_all:
        return [v.index for v in mesh.vertices]

    selected_vertices, selected_edges, selected_faces, selected_loops = collect_selection(
        mesh,
        use_vert,
        use_edge,
        use_face
    )

    return sorted(selected_vertices)


class MESH_OT_vdt_set_vertex_group_value(Operator):
    bl_idname = "mesh.vdt_set_vertex_group_value"
    bl_label = "Set Vertex Group Value"
    bl_options = {'REGISTER', 'UNDO'}

    value: FloatProperty(
        name="Value",
        default=1.0,
        min=0.0,
        max=1.0,
    )

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == 'MESH'

    def execute(self, context):
        props = context.scene.vdt_props
        group_name = props.vertex_group_name.strip()

        if not group_name:
            self.report({'ERROR'}, "Vertex group name is empty.")
            return {'CANCELLED'}

        original_mode, active_obj, target_objects, use_vert, use_edge, use_face, apply_all = get_context_targets(context)

        if not target_objects:
            restore_mode_if_needed(context, original_mode, active_obj)
            self.report({'ERROR'}, "No mesh objects found.")
            return {'CANCELLED'}

        total_count = 0
        skipped = []

        try:
            for obj in target_objects:
                mesh = obj.data
                indices = collect_vertex_group_indices(
                    mesh,
                    use_vert,
                    use_edge,
                    use_face,
                    apply_all=apply_all
                )

                if not indices:
                    continue

                group = get_or_create_vertex_group(obj, group_name)

                try:
                    if self.value <= 0.0:
                        # Vertex Group에서 0은 저장하지 않는 방식이 가장 안전함.
                        # 제거된 vertex는 weight 0과 동일하게 동작함.
                        group.remove(indices)
                    else:
                        group.add(indices, 1.0, 'REPLACE')
                except RuntimeError as e:
                    skipped.append(f"{obj.name}: {e}")
                    continue

                total_count += len(indices)

        finally:
            restore_mode_if_needed(context, original_mode, active_obj)

        if skipped:
            self.report({'WARNING'}, "Skipped: " + " | ".join(skipped))
        else:
            action = "Removed from" if self.value <= 0.0 else "Added to"
            target = "whole selected objects" if apply_all else "selected mesh elements"
            self.report(
                {'INFO'},
                f"{action} vertex group '{group_name}' for {total_count} vertices on {target}."
            )

        return {'FINISHED'}


# -----------------------------------------------------------------------------
# UI
# -----------------------------------------------------------------------------

class VIEW3D_PT_vertex_data_tools_panel(Panel):
    bl_label = "Vertex Data Tools"
    bl_idname = "VIEW3D_PT_vertex_data_tools_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Tool"

    def draw(self, context):
        layout = self.layout
        obj = context.object
        props = context.scene.vdt_props

        if context.mode == 'EDIT_MESH':
            layout.label(text="Target: Selected Mesh Elements")
        else:
            layout.label(text="Target: Whole Selected Objects")

        layout.separator()

        # Vertex Color section
        box = layout.box()
        box.label(text="Vertex Color")

        if obj and obj.type == 'MESH':
            color_attr = get_active_real_color_attribute(obj.data)

            if color_attr:
                box.label(text=f"Active: {color_attr.name}")
                box.label(text=f"Type: {color_attr.data_type}")
                box.label(text=f"Domain: {color_attr.domain}")
            else:
                box.label(text="No real Color Attribute")
                box.label(text='Will create "Color"')
        else:
            box.label(text="No mesh object selected")

        box.label(text="Set Value:")

        row = box.row(align=True)
        for value in [0.0, 0.2, 0.4]:
            op = row.operator(
                "mesh.vdt_set_active_vertex_color_value",
                text=str(value)
            )
            op.value = value

        row = box.row(align=True)
        for value in [0.6, 0.8, 1.0]:
            op = row.operator(
                "mesh.vdt_set_active_vertex_color_value",
                text=str(value)
            )
            op.value = value

        layout.separator()

        # Vertex Group section
        box = layout.box()
        box.label(text="Vertex Group")
        box.prop(props, "vertex_group_name", text="Name")
        box.label(text="Set Weight:")

        row = box.row(align=True)
        op = row.operator("mesh.vdt_set_vertex_group_value", text="0")
        op.value = 0.0
        op = row.operator("mesh.vdt_set_vertex_group_value", text="1")
        op.value = 1.0

        box.label(text="0 removes selected vertices from the group")


classes = (
    VDT_Properties,
    MESH_OT_vdt_set_active_vertex_color_value,
    MESH_OT_vdt_set_vertex_group_value,
    VIEW3D_PT_vertex_data_tools_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.vdt_props = PointerProperty(type=VDT_Properties)


def unregister():
    del bpy.types.Scene.vdt_props

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
