"""Bake an existing deformation stack, without re-binding vertices by proximity.

Procedural exporters opt a source in with object['vdt_evaluated_shape_keys'].
Only disposable mesh copies are accepted as targets. No preferences are changed.
"""

import bpy
import numpy as np

OPT_IN_PROPERTY = 'vdt_evaluated_shape_keys'
BAKED_PROPERTY = '_vdt_evaluated_shape_keys_baked'


def _topology(mesh):
    return (len(mesh.vertices), tuple(tuple(p.vertices) for p in mesh.polygons))


def _sample(source, targets):
    graph = bpy.context.evaluated_depsgraph_get()
    samples = []
    for instance in graph.object_instances:
        parent = instance.parent
        if instance.object.type != 'MESH':
            continue
        if instance.object.original != source and not (
            instance.is_instance and parent and parent.original == source
        ):
            continue
        mesh = instance.object.data
        if not mesh.vertices or not mesh.polygons:
            continue
        if len(samples) >= len(targets):
            raise RuntimeError(f'{source.name}: evaluated mesh part count changed')
        target = targets[len(samples)]
        matrix = target.matrix_world.inverted() @ instance.matrix_world
        coords = np.array([matrix @ v.co for v in mesh.vertices], dtype=np.float32)
        if not np.isfinite(coords).all():
            raise RuntimeError(f'{source.name}: non-finite evaluated vertex positions')
        identity = (instance.object.original.name_full, tuple(instance.persistent_id))
        samples.append((identity, _topology(mesh), coords))
    if len(samples) != len(targets):
        raise RuntimeError(f'{source.name}: evaluated mesh part count changed')
    return samples


def bake_evaluated_shape_keys(character, entries):
    """Sample all relative morphs once for several (source, temporary_parts) pairs.

    Atomic data replacement: source values/mutes and target data are restored on
    failure. Stable topology and instance identity are required for every key.
    The resulting keys use Basis-relative evaluated deltas, including modifiers.
    """
    keys = character.data.shape_keys
    if not keys or not keys.use_relative or len(keys.key_blocks) < 2:
        raise RuntimeError(f'{character.name}: relative source shape keys required')
    animation = keys.animation_data
    old_action = animation.action if animation else None
    old_slot = animation.action_slot if animation else None
    muted_animation = (
        [(item, item.mute) for item in list(animation.drivers) + list(animation.nla_tracks)]
        if animation else []
    )
    entries = list(entries)
    targets = [target for _, parts in entries for target in parts]
    if not targets or len(set(targets)) != len(targets):
        raise RuntimeError('Evaluated shape baking needs distinct temporary targets')
    if any(t.type != 'MESH' or t.data.shape_keys or t.library for t in targets):
        raise RuntimeError('Evaluated shape baking requires fresh local mesh copies')
    if any(source in targets or character in targets for source, _ in entries):
        raise RuntimeError('Evaluated shape baking must not replace source data')
    old_values = [(key, key.value, key.mute) for key in keys.key_blocks]
    show_only = character.show_only_shape_key
    old_data = {target: target.data for target in targets}
    copies = []
    success = False

    def update():
        character.data.update()
        character.update_tag(refresh={'DATA'})
        bpy.context.view_layer.update()

    try:
        if animation:
            animation.action = None
            for item, _ in muted_animation:
                item.mute = True
        character.show_only_shape_key = False
        for key, _, _ in old_values:
            key.value = 0.0
            key.mute = False
        update()
        # Animation detachment triggers its own graph evaluation. Reassert the
        # neutral values after that evaluation, before sampling Basis.
        for key, _, _ in old_values:
            key.value = 0.0
        update()
        neutral = [_sample(source, parts) for source, parts in entries]
        for (_, parts), samples in zip(entries, neutral):
            for target, (_, topology, coords) in zip(parts, samples):
                if _topology(target.data) != topology:
                    raise RuntimeError(f'{target.name}: export topology differs from neutral')
                mesh = target.data.copy()
                copies.append(mesh)
                target.data = mesh
                mesh.vertices.foreach_set('co', coords.ravel())
                mesh.update()
                basis = target.shape_key_add(name='Basis', from_mix=False)
                basis.data.foreach_set('co', coords.ravel())
        for source_key in keys.key_blocks:
            if source_key == keys.reference_key:
                continue
            source_key.value = 1.0
            update()
            for (source, parts), reference in zip(entries, neutral):
                samples = _sample(source, parts)
                for target, sample, base in zip(parts, samples, reference):
                    if sample[:2] != base[:2]:
                        raise RuntimeError(
                            f'{source.name}: topology/instance order changed at {source_key.name}'
                        )
                    key = target.shape_key_add(name=source_key.name, from_mix=False)
                    key.data.foreach_set('co', sample[2].ravel())
                    key.slider_min = source_key.slider_min
                    key.slider_max = source_key.slider_max
                    key.value = 0.0
            source_key.value = 0.0
        success = True
    finally:
        if animation:
            animation.action = old_action
            if old_action and old_slot:
                animation.action_slot = old_slot
            for item, mute in muted_animation:
                item.mute = mute
            # Reattaching an action schedules evaluation. Flush it before
            # restoring any manual slider edits that existed at entry.
            update()
        for key, value, mute in old_values:
            key.value = value
            key.mute = mute
        character.show_only_shape_key = show_only
        update()
        if not success:
            for target, mesh in old_data.items():
                target.data = mesh
            for mesh in copies:
                if mesh.users == 0:
                    bpy.data.meshes.remove(mesh)
    for target, mesh in old_data.items():
        target[BAKED_PROPERTY] = character.name_full
        target.data.update()
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    return {'sources': len(entries), 'parts': len(targets), 'keys': len(keys.key_blocks)}
