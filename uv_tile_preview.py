"""Regenerate Viewport 2.0 (OGS) UV tile preview textures.

Mirrors Maya's VP2 action that rebuilds UV tile preview images so tiled / UDIM
file textures display correctly in the viewport.
"""
import maya.cmds as cmds
import maya.mel as mel


def regenerate_all_uv_tile_previews(reload_file_textures=True):
    """Regenerate all OGS UV tile preview textures.

    Tries the VP2 MEL proc first, then ``cmds.ogs(regenerateUVTilePreview="")``
    from the Maya docs. Optionally reloads GPU file textures.

    Args:
        reload_file_textures: If True, also runs ``ogs -reloadTextures``.

    Returns:
        True when at least one regeneration path succeeded.

    Raises:
        RuntimeError: If both the MEL proc and ogs flag fail.
    """
    mel_ok = False
    try:
        mel.eval("generateAllUvTilePreviews;")
        mel_ok = True
    except Exception:
        pass

    ogs_ok = False
    try:
        cmds.ogs(regenerateUVTilePreview="")
        ogs_ok = True
    except Exception:
        pass

    if not mel_ok and not ogs_ok:
        raise RuntimeError(
            "Could not regenerate UV tile previews "
            "(generateAllUvTilePreviews and ogs regenerateUVTilePreview both failed)."
        )

    if reload_file_textures:
        try:
            cmds.ogs(reloadTextures=True)
        except Exception:
            pass

    return True
