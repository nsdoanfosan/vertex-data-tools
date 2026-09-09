"""Run in Blender --background --factory-startup; never saves preferences."""
import pathlib
import sys
import bpy
import addon_utils
from mathutils import Matrix

sys.path.append(str(pathlib.Path(__file__).resolve().parents[2]))
# The installed junction exposes the package under its Blender module name.
addon_utils.enable('vertex_data_tools', default_set=False, persistent=False)
from vertex_data_tools import evaluated_shape_keys as bake

mesh = bpy.data.meshes.new('Character')
mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
character = bpy.data.objects.new('Character', mesh)
bpy.context.scene.collection.objects.link(character)
character.shape_key_add(name='Basis')
move = character.shape_key_add(name='Move')
move.data[0].co.z = 0.25
move.value = 0.0
move.keyframe_insert('value', frame=1)
move.value = 1.0
move.keyframe_insert('value', frame=10)
bpy.context.scene.frame_set(1)
move.value = 0.37
character.data.update()
bpy.context.view_layer.update()
animation = mesh.shape_keys.animation_data
old_action, old_slot = animation.action, animation.action_slot


def target_copy(name):
    data = bpy.data.meshes.new(name)
    data.from_pydata([v.co for v in mesh.vertices], [], [(0, 1, 2)])
    target = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(target)
    target.matrix_world = Matrix.Translation((0.2, 0.3, 0.4)) @ Matrix.Scale(0.01, 4)
    return target


target = target_copy('ExportCopy')
bake.bake_evaluated_shape_keys(character, [(character, [target])])
keys = target.data.shape_keys.key_blocks
delta = target.matrix_world.to_3x3() @ (keys['Move'].data[0].co - keys['Basis'].data[0].co)
assert abs(delta.z - 0.25) < 1e-6
assert abs(move.value - 0.37) < 1e-6
assert animation.action == old_action and animation.action_slot == old_slot

# A changing topology must leave the temporary target and source intact.
target2 = target_copy('RollbackCopy')
old_data = target2.data
sample = bake._sample
calls = 0


def changed_topology(source, targets):
    global calls
    calls += 1
    values = sample(source, targets)
    if calls == 2:
        identity, _, coords = values[0]
        values[0] = (identity, (999, ()), coords)
    return values


bake._sample = changed_topology
try:
    bake.bake_evaluated_shape_keys(character, [(character, [target2])])
except RuntimeError as error:
    assert 'topology' in str(error)
else:
    raise AssertionError('Topology mismatch was accepted')
finally:
    bake._sample = sample
assert target2.data == old_data and not target2.data.shape_keys
assert abs(move.value - 0.37) < 1e-6
assert animation.action == old_action and animation.action_slot == old_slot
assert bake.BAKED_PROPERTY not in target2
print('EVALUATED_SHAPE_KEYS_SMOKE_PASSED')
