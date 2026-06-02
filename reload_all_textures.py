"""Reload all file texture nodes from disk.

When artists overwrite image files on disk, Maya keeps the old pixels until each
file node is refreshed. Re-setting ``fileTextureName`` to its current path is the
standard way to pick up updated files scene-wide.
"""
import maya.cmds as cmds


def reload_all_file_textures(reload_viewport=True):
    """Reload every ``file`` node in the scene from disk.

    Args:
        reload_viewport: When True, also runs ``ogs -reloadTextures`` so Viewport 2.0
            picks up the updated images.

    Returns:
        dict with ``reloaded_count``, ``skipped_count``, and ``failed`` (list of
        ``(node_name, error_message)`` tuples).
    """
    texture_nodes = cmds.ls(type="file") or []
    reloaded_count = 0
    skipped_count = 0
    failed = []

    try:
        cmds.waitCursor(state=True)
    except Exception:
        pass

    try:
        for node in texture_nodes:
            try:
                if not cmds.attributeQuery("fileTextureName", node=node, exists=True):
                    skipped_count += 1
                    continue

                path = cmds.getAttr(f"{node}.fileTextureName")
                if not path:
                    skipped_count += 1
                    continue

                cmds.setAttr(f"{node}.fileTextureName", path, type="string")
                reloaded_count += 1
            except Exception as exc:
                failed.append((node, str(exc)))

        if reload_viewport:
            try:
                cmds.ogs(reloadTextures=True)
            except Exception:
                pass
    finally:
        try:
            cmds.waitCursor(state=False)
        except Exception:
            pass

    return {
        "reloaded_count": reloaded_count,
        "skipped_count": skipped_count,
        "failed": failed,
    }
