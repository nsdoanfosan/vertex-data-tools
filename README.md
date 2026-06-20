# Vertex Data Tools

Blender add-on for three common real-time art tasks:

- assigning stepped grayscale vertex-color values;
- adding or removing vertices from a named vertex group;
- transferring character shape keys to a nearby mesh by proximity.

The panel is in **3D Viewport > Sidebar (`N`) > Tool > Vertex Data Tools**.

## Installation

1. Download or clone this repository.
2. Zip the repository folder with `__init__.py` at the zip root.
3. In Blender, open **Edit > Preferences > Add-ons > Install from Disk**.
4. Select the zip and enable **Vertex Data Tools**.

Blender 4.0 or newer is required.

## Object Mode and Edit Mode

The vertex-color and vertex-group tools use the current mode as their scope:

- **Object Mode** changes the whole mesh of every selected mesh object.
- **Edit Mode** changes selected vertices. Selected edges and faces work
  because their vertices are selected by Blender.

The top of the panel shows the current target scope.

## Vertex Color

Use this section to assign grayscale values commonly used as masks in Unreal or
other real-time shaders.

Steps:

1. Select one or more mesh objects.
2. To initialize the whole object, stay in Object Mode. To paint a region,
   enter Edit Mode and select the target geometry.
3. Press `0.0`, `0.2`, `0.4`, `0.6`, `0.8`, or `1.0`.

The tool writes:

```text
(value, value, value, 1.0)
```

It uses the active real Color Attribute when possible. Valid attributes are
`BYTE_COLOR` or `FLOAT_COLOR` on the `POINT` or `CORNER` domain. If none exists,
the tool creates a color attribute named `Color`. Non-color attributes such as
custom normals are ignored.

Example mask workflow:

1. In Object Mode, press `0.0` to clear the whole object.
2. Enter Edit Mode and select the desired faces.
3. Press a higher value for the selected area.

## Vertex Group

Use this section for binary membership: weight `1` or not in the group.

Steps:

1. Enter the target group name. The default is `head`.
2. Select mesh objects or enter Edit Mode and select geometry.
3. Press `1` to add the vertices with weight `1.0`.
4. Press `0` to remove the vertices from the group.

The group is created automatically if it does not exist. `0` removes
membership rather than storing an explicit zero weight.

## Shape Key Transfer

Use this to copy deformations from a character surface to nearby meshes such as
eyebrows, eyelashes, clothing, or accessories.

Steps:

1. Switch to **Object Mode**.
2. Select exactly two mesh objects:
   - the character/source mesh with shape keys;
   - the receiving mesh.
3. Make the receiving mesh the **active object** last.
4. Optionally enable **Overwrite Existing**.
5. Press **Transfer Proximity Shape Keys**.

The tool samples up to eight nearby source vertices for each receiving vertex,
blends their deformation by inverse-distance weighting, and creates one
receiving shape key for every non-Basis source key.

Important behavior:

- the source must have at least one non-Basis shape key;
- the receiver gets a `Basis` key automatically when needed;
- existing keys with matching names stop the operation unless **Overwrite
  Existing** is enabled;
- matching relative shape-key relationships are reused when the corresponding
  receiver key exists;
- current source and receiver shape-key values are restored after processing;
- no cage or temporary modifier is created.

This is a proximity transfer, not a surface-wrap solver. Check the result when
the receiver is far from the source, crosses to another body region, or has very
different topology.

## Developer Map

The complete add-on currently lives in:

```text
vertex-data-tools/
|-- __init__.py
`-- README.md
```

Important code areas in `__init__.py`:

- mode-dependent targeting: `get_target_objects`;
- color-attribute validation and writes: `is_real_color_attribute`,
  `apply_color_object_mode`, `apply_color_edit_mode`;
- vertex-group writes: `apply_group_object_mode`, `apply_group_edit_mode`;
- proximity binding and shape-key creation: `create_proximity_bindings`,
  `add_proximity_shape_key`;
- operators: `MESH_OT_vdt_set_active_vertex_color_value`,
  `MESH_OT_vdt_set_vertex_group_value`,
  `OBJECT_OT_vdt_transfer_surface_shape_keys`;
- UI and settings: `VDT_Properties`,
  `VIEW3D_PT_vertex_data_tools_panel`.

The main transfer tuning constants are `PROXIMITY_SAMPLE_COUNT` and
`DEFORMATION_EPSILON`. Preserve the operator's rollback path when changing the
shape-key workflow so a partial failure does not leave temporary or half-created
keys behind.
