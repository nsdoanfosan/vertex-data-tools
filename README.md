# Vertex Data Tools

A small Blender add-on collection for managing vertex-related data used in real-time art workflows.

## Feature Set

- Vertex Color value assignment
- Vertex Group binary assignment
- Proximity-weighted shape key transfer

## Vertex Color Features

- Adds a `Vertex Data Tools` panel to the 3D View sidebar `Tool` tab.
- Buttons for `0.0`, `0.2`, `0.4`, `0.6`, `0.8`, `1.0`.
- Uses the active real Color Attribute if available.
- Creates a `Color` attribute automatically when no valid Color Attribute exists.
- Ignores non-color attributes such as `custom_normal`.
- Object Mode:
  - Applies the selected value to the whole selected mesh object.
- Edit Mode:
  - Applies the selected value only to selected vertices, edges, or faces.
- Writes grayscale color as:
  - `(value, value, value, 1.0)`

## Vertex Group Features

- Text field for the target vertex group name.
- Default vertex group name:
  - `head`
- Buttons:
  - `0`
  - `1`
- If the vertex group does not exist, it is created automatically.
- Object Mode:
  - Applies to all vertices in the selected mesh objects.
- Edit Mode:
  - Applies only to selected vertices, edges, or faces.
- `1` adds selected vertices to the group with weight `1.0`.
- `0` removes selected vertices from the group, which is equivalent to weight `0` for most Blender and game-engine workflows.

## Shape Key Transfer Features

- Select a source surface mesh with shape keys and the active target mesh.
- Samples nearby source vertices and smoothly blends their deformation.
- Bakes each source shape key onto the active target mesh.
- Does not require a cage or a temporary deform modifier.
- Intended for meshes outside the source surface, such as eyebrows, eyelashes, and clothing.

## Install in Blender

This repository is deployed live with a Windows junction into Blender's
`scripts/addons` directory, so editing the source updates the add-on directly.
Restart or reload Blender to pick up changes, then:

1. Open the 3D View sidebar with `N`.
2. Use the `Tool` tab.

To install on another machine instead, zip the folder and use
`Edit > Preferences > Add-ons > Install...`, then enable `Vertex Data Tools`.

## Git usage

Recommended structure:

```text
vertex_data_tools/
├── __init__.py
├── README.md
└── .gitignore
```

## Notes

For Unreal Engine mask workflows, a common vertex color workflow is:

1. Object Mode: set the whole object to `0.0`.
2. Edit Mode: select specific faces/edges/vertices.
3. Assign `0.2`, `0.4`, `0.6`, `0.8`, or `1.0` as needed.

For vertex groups, a common workflow is:

1. Enter the group name, for example `head`.
2. Object Mode: press `0` to clear the group from the whole object, if needed.
3. Edit Mode: select the target vertices/faces.
4. Press `1` to add them to the group.
