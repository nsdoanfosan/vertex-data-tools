bl_info = {
    "name": "Vertex Data Tools",
    "author": "PARK",
    "version": (1, 14, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > Tool",
    "description": "Tools for managing vertex color and vertex group workflows",
    "category": "Mesh",
}

import bpy
import bmesh
from bpy.props import BoolProperty, FloatProperty, StringProperty, PointerProperty
from bpy.types import Operator, Panel, PropertyGroup
from mathutils.kdtree import KDTree


VALID_COLOR_TYPES = {'BYTE_COLOR', 'FLOAT_COLOR'}
VALID_COLOR_DOMAINS = {'POINT', 'CORNER'}
DEFORMATION_EPSILON = 1e-6
PROXIMITY_SAMPLE_COUNT = 8


# -----------------------------------------------------------------------------
# Common target helpers
# -----------------------------------------------------------------------------

def get_target_objects(context):
    """
    Object Mode: all selected mesh objects (applies to whole mesh).
    Edit Mode: meshes currently in edit mode (applies to selected elements).
    """

    if context.mode == 'EDIT_MESH':
        return [
            obj for obj in context.objects_in_mode_unique_data
            if obj.type == 'MESH'
        ]

    return [obj for obj in context.selected_objects if obj.type == 'MESH']


# -----------------------------------------------------------------------------
# Add-on properties
# -----------------------------------------------------------------------------

class VDT_Properties(PropertyGroup):
    vertex_group_name: StringProperty(
        name="Vertex Group Name",
        default="head",
        description="Vertex group name to create or edit",
    )
    overwrite_shape_keys: BoolProperty(
        name="Overwrite Existing",
        default=False,
        description="Replace clothing shape keys with matching character shape key names",
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
    (Object Mode 전용. Edit Mode에서는 ensure_bmesh_color_layer 사용)
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


def get_bmesh_color_layer(bm, domain, data_type, name):
    """
    Color Attribute의 도메인/타입에 맞는 bmesh 레이어를 이름으로 찾는다.
    """

    if domain == 'POINT':
        collection = (
            bm.verts.layers.float_color
            if data_type == 'FLOAT_COLOR'
            else bm.verts.layers.color
        )
    else:  # CORNER
        collection = (
            bm.loops.layers.float_color
            if data_type == 'FLOAT_COLOR'
            else bm.loops.layers.color
        )

    return collection.get(name)


def ensure_bmesh_color_layer(bm, mesh):
    """
    Edit Mode용. 활성 Color Attribute에 대응하는 bmesh 레이어를 돌려준다.
    없으면 BYTE_COLOR / CORNER 레이어를 새로 만든다.

    Returns:
        (layer, domain, created_name)
        created_name: 새로 만들었으면 이름, 아니면 None
    """

    attr = get_active_real_color_attribute(mesh)

    if attr is not None:
        layer = get_bmesh_color_layer(bm, attr.domain, attr.data_type, attr.name)
        if layer is not None:
            return layer, attr.domain, None

    name = unique_attribute_name(mesh, "Color")
    layer = bm.loops.layers.color.new(name)
    return layer, 'CORNER', name


def apply_color_object_mode(mesh, color):
    """
    Object Mode: 전체 mesh의 Color Attribute에 값 적용.
    """

    attr = get_or_create_color_attribute(mesh)

    if attr is None or not is_real_color_attribute(attr):
        return None

    for i in range(len(attr.data)):
        attr.data[i].color = color

    mesh.update()
    return len(attr.data)


def apply_color_edit_mode(obj, color):
    """
    Edit Mode: bmesh로 선택된 요소에만 값 적용 (모드 전환 없음).
    POINT 도메인이면 선택된 vertex, CORNER 도메인이면 선택 모드에 맞는 loop.
    """

    mesh = obj.data
    bm = bmesh.from_edit_mesh(mesh)

    layer, domain, created_name = ensure_bmesh_color_layer(bm, mesh)

    if layer is None:
        return None

    count = 0
    modes = bm.select_mode

    if domain == 'POINT':
        for vert in bm.verts:
            if vert.select:
                vert[layer] = color
                count += 1
    else:  # CORNER
        for face in bm.faces:
            for loop in face.loops:
                if (
                    ('FACE' in modes and face.select)
                    or ('EDGE' in modes and loop.edge.select)
                    or ('VERT' in modes and loop.vert.select)
                ):
                    loop[layer] = color
                    count += 1

    bmesh.update_edit_mesh(mesh)

    if created_name:
        try:
            set_active_color_attribute(mesh, mesh.color_attributes.get(created_name))
        except Exception:
            pass

    return count


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
        target_objects = get_target_objects(context)

        if not target_objects:
            self.report({'ERROR'}, "No mesh objects found.")
            return {'CANCELLED'}

        color = (self.value, self.value, self.value, 1.0)
        edit_mode = context.mode == 'EDIT_MESH'

        total_count = 0
        skipped = []

        for obj in target_objects:
            if edit_mode:
                count = apply_color_edit_mode(obj, color)
            else:
                count = apply_color_object_mode(obj.data, color)

            if count is None:
                skipped.append(f"{obj.name}: could not create or find real color attribute")
                continue

            total_count += count

        if skipped:
            self.report({'WARNING'}, "Skipped: " + " | ".join(skipped))
        else:
            scope = "selected mesh elements" if edit_mode else "whole selected objects"
            self.report(
                {'INFO'},
                f"Set vertex color to {self.value} on {total_count} elements ({scope})."
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


def apply_group_object_mode(obj, group_name, assign):
    """
    Object Mode: 전체 vertex를 그룹에 추가/제거.
    실패하면 에러 문자열을 돌려준다.
    """

    indices = [v.index for v in obj.data.vertices]

    if not indices:
        return 0

    group = get_or_create_vertex_group(obj, group_name)

    try:
        if assign:
            group.add(indices, 1.0, 'REPLACE')
        else:
            # weight 0을 저장하는 대신 그룹에서 제거하는 게 가장 안전함.
            group.remove(indices)
    except RuntimeError as error:
        return str(error)

    return len(indices)


def apply_group_edit_mode(obj, group_name, assign):
    """
    Edit Mode: bmesh deform 레이어로 선택된 vertex만 처리 (모드 전환 없음).
    VertexGroup.add/remove는 Edit Mode에서 막혀 있으므로 deform 레이어를 직접 쓴다.
    """

    mesh = obj.data
    group = get_or_create_vertex_group(obj, group_name)

    bm = bmesh.from_edit_mesh(mesh)
    deform = bm.verts.layers.deform.verify()
    group_index = group.index

    count = 0

    for vert in bm.verts:
        if not vert.select:
            continue

        if assign:
            vert[deform][group_index] = 1.0
        elif group_index in vert[deform]:
            del vert[deform][group_index]

        count += 1

    bmesh.update_edit_mesh(mesh)
    return count


class MESH_OT_vdt_set_vertex_group_value(Operator):
    bl_idname = "mesh.vdt_set_vertex_group_value"
    bl_label = "Set Vertex Group Value"
    bl_options = {'REGISTER', 'UNDO'}

    assign: BoolProperty(
        name="Assign",
        default=True,
        description=(
            "Add selected vertices to the group at weight 1.0; "
            "disable to remove them (equivalent to weight 0)"
        ),
    )

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == 'MESH'

    def execute(self, context):
        group_name = context.scene.vdt_props.vertex_group_name.strip()

        if not group_name:
            self.report({'ERROR'}, "Vertex group name is empty.")
            return {'CANCELLED'}

        target_objects = get_target_objects(context)

        if not target_objects:
            self.report({'ERROR'}, "No mesh objects found.")
            return {'CANCELLED'}

        edit_mode = context.mode == 'EDIT_MESH'

        total_count = 0
        skipped = []

        for obj in target_objects:
            if edit_mode:
                result = apply_group_edit_mode(obj, group_name, self.assign)
            else:
                result = apply_group_object_mode(obj, group_name, self.assign)

            if isinstance(result, str):
                skipped.append(f"{obj.name}: {result}")
                continue

            total_count += result

        if skipped:
            self.report({'WARNING'}, "Skipped: " + " | ".join(skipped))
        else:
            action = "Added to" if self.assign else "Removed from"
            scope = "selected mesh elements" if edit_mode else "whole selected objects"
            self.report(
                {'INFO'},
                f"{action} vertex group '{group_name}' for {total_count} vertices ({scope})."
            )

        return {'FINISHED'}


# -----------------------------------------------------------------------------
# Shape Key Transfer
# -----------------------------------------------------------------------------

def get_key_blocks(obj):
    shape_keys = obj.data.shape_keys
    return shape_keys.key_blocks if shape_keys else None


def capture_key_values(key_blocks):
    if not key_blocks:
        return {}
    return {key.name: key.value for key in key_blocks}


def restore_key_values(key_blocks, values):
    if not key_blocks:
        return
    for name, value in values.items():
        key = key_blocks.get(name)
        if key:
            key.value = value


def reset_key_values(key_blocks):
    if not key_blocks:
        return
    for key in key_blocks:
        key.value = 0.0


def create_proximity_bindings(character, clothes):
    source_vertices = character.data.vertices
    if not source_vertices:
        raise RuntimeError(f'"{character.name}" does not contain any vertices.')

    tree = KDTree(len(source_vertices))
    for vertex in source_vertices:
        tree.insert(vertex.co, vertex.index)
    tree.balance()

    clothes_to_character = character.matrix_world.inverted() @ clothes.matrix_world
    sample_count = min(PROXIMITY_SAMPLE_COUNT, len(source_vertices))
    bindings = []

    for vertex in clothes.data.vertices:
        character_point = clothes_to_character @ vertex.co
        nearest = tree.find_n(character_point, sample_count)
        if not nearest:
            raise RuntimeError(
                f'Could not find nearby vertices on "{character.name}".'
            )

        if nearest[0][2] <= DEFORMATION_EPSILON:
            bindings.append(((nearest[0][1],), (1.0,)))
            continue

        weights = [1.0 / max(distance, DEFORMATION_EPSILON) ** 2 for _, _, distance in nearest]
        weight_total = sum(weights)
        bindings.append(
            (
                tuple(index for _, index, _ in nearest),
                tuple(weight / weight_total for weight in weights),
            )
        )

    return bindings


def add_proximity_shape_key(character, clothes, source_key, bindings):
    key_blocks = get_key_blocks(clothes)
    new_key = clothes.shape_key_add(name=source_key.name, from_mix=False)
    new_key.value = 0.0
    source_reference = source_key.relative_key
    clothes_reference = clothes.data.shape_keys.reference_key

    if source_reference != character.data.shape_keys.reference_key:
        matching_reference = key_blocks.get(source_reference.name)
        if matching_reference:
            clothes_reference = matching_reference
            new_key.relative_key = matching_reference

    character_to_clothes = clothes.matrix_world.inverted() @ character.matrix_world
    rotation = character_to_clothes.to_3x3()

    for point, reference_point, binding in zip(
        new_key.data, clothes_reference.data, bindings
    ):
        indices, weights = binding
        deformation = sum(
            (
                (source_key.data[index].co - source_reference.data[index].co)
                * weight
                for index, weight in zip(indices, weights)
            ),
            start=source_key.data[indices[0]].co * 0.0,
        )
        point.co = reference_point.co + rotation @ deformation

    return new_key


def shape_key_has_deformation(key):
    return any(
        (point.co - reference_point.co).length > DEFORMATION_EPSILON
        for point, reference_point in zip(key.data, key.relative_key.data)
    )


class OBJECT_OT_vdt_transfer_surface_shape_keys(Operator):
    bl_idname = "object.vdt_transfer_surface_shape_keys"
    bl_label = "Transfer Proximity Shape Keys"
    bl_description = (
        "Transfer character shape keys to the active mesh using smooth "
        "proximity-weighted deformation"
    )
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        if context.mode != 'OBJECT':
            self.report({'ERROR'}, "Switch to Object Mode before transferring shape keys.")
            return {'CANCELLED'}

        selected = list(context.selected_objects)
        clothes = context.active_object

        if len(selected) != 2 or not clothes or clothes not in selected:
            self.report(
                {'ERROR'},
                "Select exactly two objects and make the clothing mesh active."
            )
            return {'CANCELLED'}

        character = next(obj for obj in selected if obj != clothes)
        if clothes.type != 'MESH' or character.type != 'MESH':
            self.report({'ERROR'}, "Both selected objects must be mesh objects.")
            return {'CANCELLED'}

        character_keys = get_key_blocks(character)
        if not character_keys or len(character_keys) <= 1:
            self.report({'ERROR'}, f'"{character.name}" has no transferable shape keys.')
            return {'CANCELLED'}

        source_names = [
            key.name
            for key in character_keys
            if key != character.data.shape_keys.reference_key
        ]

        if get_key_blocks(clothes) is None:
            clothes.shape_key_add(name="Basis")

        clothes_keys = get_key_blocks(clothes)
        conflicts = [name for name in source_names if clothes_keys.get(name)]
        if conflicts and not context.scene.vdt_props.overwrite_shape_keys:
            self.report(
                {'ERROR'},
                "Matching clothing shape keys already exist: " + ", ".join(conflicts)
            )
            return {'CANCELLED'}

        source_values = capture_key_values(character_keys)
        clothes_values = capture_key_values(clothes_keys)
        backups = []
        created_keys = []

        try:
            reset_key_values(character_keys)
            reset_key_values(clothes_keys)

            for index, name in enumerate(conflicts):
                key = clothes_keys[name]
                backup_name = f"__vdt_shape_key_backup_{index}__"
                while clothes_keys.get(backup_name):
                    backup_name += "_"
                key.name = backup_name
                backups.append((key, name))

            bindings = create_proximity_bindings(character, clothes)
            for source_name in source_names:
                source_key = character_keys.get(source_name)
                created_keys.append(
                    add_proximity_shape_key(character, clothes, source_key, bindings)
                )

            if not any(shape_key_has_deformation(key) for key in created_keys):
                raise RuntimeError("No clothing vertex changes were generated.")

            for key, _original_name in reversed(backups):
                clothes.shape_key_remove(key)

        except Exception as error:
            for key in reversed(created_keys):
                if key.name in clothes_keys:
                    clothes.shape_key_remove(key)

            for key, original_name in backups:
                key.name = original_name

            restore_key_values(clothes_keys, clothes_values)
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}

        finally:
            restore_key_values(character_keys, source_values)

        no_deformation = [
            key.name for key in created_keys if not shape_key_has_deformation(key)
        ]
        if no_deformation:
            self.report(
                {'WARNING'},
                "Transferred, but these keys produced no clothing changes: "
                + ", ".join(no_deformation)
            )
        else:
            self.report(
                {'INFO'},
                f'Transferred {len(created_keys)} shape keys to "{clothes.name}".'
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
        op.assign = False
        op = row.operator("mesh.vdt_set_vertex_group_value", text="1")
        op.assign = True

        box.label(text="0 removes selected vertices from the group")

        layout.separator()

        # Shape Key Transfer section
        box = layout.box()
        box.label(text="Shape Key Transfer")
        box.label(text="Select character and active clothing")
        box.prop(props, "overwrite_shape_keys")
        box.operator("object.vdt_transfer_surface_shape_keys", icon='SHAPEKEY_DATA')


classes = (
    VDT_Properties,
    MESH_OT_vdt_set_active_vertex_color_value,
    MESH_OT_vdt_set_vertex_group_value,
    OBJECT_OT_vdt_transfer_surface_shape_keys,
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
