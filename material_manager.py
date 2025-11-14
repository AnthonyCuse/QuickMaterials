"""
Material Manager
---------------------------------

Unified tool for tidying messy scenes:
- Converts materials while preserving names (built-in conversion core).
- Merges near-identical materials using tolerance-based comparison.
- Renames materials with flexible token-driven patterns.
- Shares a single readable log with optional detail level.
"""

import collections
import math
import os
import re
import traceback
import colorsys

import maya.cmds as cmds

try:
    # Maya 2025+
    from PySide6 import QtCore, QtWidgets
    from shiboken6 import wrapInstance
    PYSIDE6 = True
except Exception:
    from PySide2 import QtCore, QtWidgets
    from shiboken2 import wrapInstance
    PYSIDE6 = False

import maya.OpenMayaUI as omui


# --------------------------------------------------------------------------------------
#                    MATERIAL CONVERTER CORE (INTEGRATED)
# --------------------------------------------------------------------------------------

# Minimal "logical" bridge attributes so we can convert legacy<->legacy through standardSurface.
# We do direct maps for standardSurface <-> legacy, and legacy <-> legacy goes via standardSurface.
# (This keeps things predictable and simpler to maintain.)
#
# ---- legacy → standardSurface (safe subset) ----
LEGACY_TO_STD = {
    "color":              ("baseColor", "color"),
    "diffuse":            ("base",      "float"),
    "incandescence":      ("emissionColor", "color"),
    "ambientColor":       (None, "color"),  # no clean analog; skip
    "transparency":       ("__SPECIAL__transparency", "color"),  # handled specially (opacity/transmission)
    # spec
    "specularColor":      ("specularColor", "color"),
    "reflectivity":       ("specular", "float"),
    # highlight shape (approx)
    "eccentricity":       ("__SPECIAL__blinn_ecc_to_rough", "float"),
    "specularRollOff":    (None, "float"),
    "cosinePower":        ("__SPECIAL__phong_power_to_rough", "float"),
    # other legacies commonly unused in PBR land: ignore or default
}

# ---- standardSurface → legacy (lambert/blinn/phong) ----
STD_TO_LEGACY_COMMON = {
    "baseColor":          ("color", "color"),
    "base":               ("diffuse", "float"),
    "emissionColor":      ("incandescence", "color"),
    # spec handled below per-target, but specularColor -> specularColor is common
    "specularColor":      ("specularColor", "color"),
    # opacity/transmission handled specially -> transparency
}
# Per-target spec/roughness behavior
STD_TO_LEGACY_TARGET = {
    "lambert": {
        "specular":       (None, "float"),  # lambert has no specular; will force reflectivity 0
        "specularRoughness": (None, "float"),
    },
    "blinn": {
        "specular":       ("reflectivity", "float"),
        "specularRoughness": ("__SPECIAL__rough_to_blinn_ecc", "float"),
    },
    "phong": {
        "specular":       ("reflectivity", "float"),
        "specularRoughness": ("__SPECIAL__rough_to_phong_power", "float"),
    }
}

# Useful attribute presence by type (subset we care about copying/connecting)
TYPE_ATTRS = {
    "lambert": [
        "color","diffuse","ambientColor","incandescence","transparency","normalCamera"
    ],
    "blinn": [
        "color","diffuse","ambientColor","incandescence","transparency","normalCamera",
        "specularColor","reflectivity","specularRollOff","eccentricity","reflectedColor"
    ],
    "phong": [
        "color","diffuse","ambientColor","incandescence","transparency","normalCamera",
        "specularColor","reflectivity","cosinePower","reflectedColor"
    ],
    "standardSurface": [
        "base","baseColor",
        "specular","specularColor","specularRoughness",
        "metalness",
        "emission","emissionColor",
        "transmission","transmissionColor",
        "opacity","normalCamera",
    ],
    "aiStandardSurface": [
        "base","baseColor",
        "specular","specularColor","specularRoughness",
        "metalness",
        "emission","emissionColor",
        "transmission","transmissionColor",
        "opacity","normalCamera",
    ],
    "surfaceShader": [
        "outColor",
        "outMatteOpacity"
    ],
}


def _phong_power_to_roughness(n):
    try:
        n = max(0.001, float(n))
        return max(0.0, min(1.0, math.sqrt(2.0 / (n + 2.0))))
    except Exception:
        return 0.5


def _roughness_to_phong_power(rough):
    try:
        r = max(0.001, float(rough))
        return max(2.0, min(200.0, (2.0 / (r*r)) - 2.0))
    except Exception:
        return 30.0


def _blinn_ecc_to_roughness(e):
    try:
        return max(0.0, min(1.0, float(e)))
    except Exception:
        return 0.5


def _roughness_to_blinn_ecc(r):
    try:
        return max(0.0, min(1.0, float(r)))
    except Exception:
        return 0.3


def _avg_rgb(rgb):
    try:
        r, g, b = rgb
        return max(0.0, min(1.0, float((r + g + b) / 3.0)))
    except Exception:
        return 0.0


def _exists_attr(node, attr):
    try:
        return cmds.attributeQuery(attr, node=node, exists=True)
    except Exception:
        return False


def _is_color_attr(node, attr):
    try:
        return bool(cmds.attributeQuery(attr, node=node, numberOfChildren=True))
    except Exception:
        return False


def _get_val(node, attr, default=None):
    plug = f"{node}.{attr}"
    try:
        if _is_color_attr(node, attr):
            v = cmds.getAttr(plug)[0]
            return v
        return cmds.getAttr(plug)
    except Exception:
        return default


def _set_val(node, attr, value):
    plug = f"{node}.{attr}"
    try:
        if isinstance(value, (tuple, list)) and len(value) == 3:
            cmds.setAttr(plug, value[0], value[1], value[2], type="double3")
        elif isinstance(value, (int, float, bool)):
            cmds.setAttr(plug, float(value))
        else:
            pass
    except Exception:
        pass


def _list_connections_safe(node, attr, **kwargs):
    if not _exists_attr(node, attr):
        return []
    plug = f"{node}.{attr}"
    try:
        return cmds.listConnections(plug, **kwargs) or []
    except Exception:
        return []


def _incoming_src(plug):
    try:
        conn = cmds.listConnections(plug, s=True, d=False, p=True) or []
        if not conn:
            return (None, None)
        src_full = conn[0]
        node, attr = src_full.split(".", 1)
        return (node, attr)
    except Exception:
        return (None, None)


def _connect_if_possible(src_node, src_attr, dst_node, dst_attr):
    if not (src_node and src_attr and dst_node and dst_attr):
        print(f"[DEBUG] _connect_if_possible: Missing parameters - src_node={src_node}, src_attr={src_attr}, dst_node={dst_node}, dst_attr={dst_attr}")
        return False
    if not _exists_attr(dst_node, dst_attr):
        print(f"[DEBUG] _connect_if_possible: Destination attribute doesn't exist - {dst_node}.{dst_attr}")
        return False
    try:
        print(f"[DEBUG] _connect_if_possible: Attempting connection {src_node}.{src_attr} -> {dst_node}.{dst_attr}")
        cmds.connectAttr(f"{src_node}.{src_attr}", f"{dst_node}.{dst_attr}", f=True)
        print(f"[DEBUG] _connect_if_possible: Connection successful!")
        return True
    except Exception as e:
        print(f"[DEBUG] _connect_if_possible: Connection failed: {e}")
        if src_attr == "outColor" and dst_attr in ["transmission", "opacity", "specular", "metalness"]:
            try:
                cmds.connectAttr(f"{src_node}.outAlpha", f"{dst_node}.{dst_attr}", f=True)
                print(f"[DEBUG] _connect_if_possible: Fallback connection successful!")
                return True
            except Exception as e2:
                print(f"[DEBUG] _connect_if_possible: Fallback connection failed: {e2}")
        elif src_attr == "outAlpha" and dst_attr in ["transparency", "baseColor", "specularColor", "emissionColor"]:
            try:
                cmds.connectAttr(f"{src_node}.outColor", f"{dst_node}.{dst_attr}", f=True)
                print(f"[DEBUG] _connect_if_possible: Fallback connection successful!")
                return True
            except Exception as e2:
                print(f"[DEBUG] _connect_if_possible: Fallback connection failed: {e2}")
        return False


def _safe_shading_node(node_type, name_hint):
    base = name_hint
    return cmds.shadingNode(node_type, asShader=True, name=base)


def _sgs_of_material(material):
    return cmds.listConnections(material, type="shadingEngine") or []


def _is_referenced(node):
    """Check if a node is referenced."""
    try:
        return cmds.referenceQuery(node, isNodeReferenced=True)
    except Exception:
        return False


def _has_reverse_node(plug):
    try:
        conn = cmds.listConnections(plug, s=True, d=False, p=True) or []
        if not conn:
            return None
        src_full = conn[0]
        node, attr = src_full.split(".", 1)
        if cmds.nodeType(node) == "reverse":
            return {
                "reverse_node": node,
                "reverse_input": attr,
                "has_reverse": True
            }
        return None
    except Exception:
        return None


def _get_normal_texture_info(plug):
    try:
        conn = cmds.listConnections(plug, s=True, d=False, p=True) or []
        if not conn:
            return None
        src_full = conn[0]
        node, attr = src_full.split(".", 1)
        node_type = cmds.nodeType(node)
        if node_type in ["file", "aiImage"]:
            normal_conns = cmds.listConnections(f"{node}.outColor", s=False, d=True, p=True) or []
            for normal_conn in normal_conns:
                normal_node = normal_conn.split(".")[0]
                if cmds.nodeType(normal_node) in ["aiNormalMap", "bump2d", "normalMap"]:
                    return {
                        "texture_node": node,
                        "normal_node": normal_node,
                        "normal_type": cmds.nodeType(normal_node)
                    }
        elif node_type in ["aiNormalMap", "bump2d", "normalMap"]:
            return {
                "texture_node": None,
                "normal_node": node,
                "normal_type": node_type
            }
        return None
    except Exception:
        return None


def _legacy_to_standard(src):
    src_type = cmds.nodeType(src)
    dst = _safe_shading_node("standardSurface", f"{src}_standardSurface_TMP")
    col = _get_val(src, "color", (0, 0, 0))
    if col:
        _set_val(dst, "baseColor", col)
    diff = _get_val(src, "diffuse", 0.8) if src_type in ("lambert", "blinn", "phong") else 1.0
    _set_val(dst, "base", diff)
    if _exists_attr(src, "incandescence"):
        inc_connections = _list_connections_safe(src, "incandescence", s=True, d=False, p=True)
        if not inc_connections:
            inc = _get_val(src, "incandescence", (0, 0, 0))
            if inc and _avg_rgb(inc) > 0.001:
                emission_weight = max(inc[0], inc[1], inc[2])
                if emission_weight > 0.001:
                    normalized_color = tuple(c / emission_weight for c in inc)
                    _set_val(dst, "emissionColor", normalized_color)
                    _set_val(dst, "emission", emission_weight)
                else:
                    _set_val(dst, "emissionColor", (0, 0, 0))
                    _set_val(dst, "emission", 0.0)
            else:
                _set_val(dst, "emissionColor", (0, 0, 0))
                _set_val(dst, "emission", 0.0)
        else:
            _set_val(dst, "emission", 1.0)
    if src_type in ("lambert", "blinn", "phong"):
        sc = _get_val(src, "specularColor", None)
        if sc:
            _set_val(dst, "specularColor", sc)
        spec_w = _get_val(src, "reflectivity", None)
        if spec_w is None and sc:
            spec_w = _avg_rgb(sc)
        _set_val(dst, "specular", spec_w if spec_w is not None else 1.0)
        if src_type == "blinn":
            rough = _blinn_ecc_to_roughness(_get_val(src, "eccentricity", 0.3))
            _set_val(dst, "specularRoughness", rough)
        elif src_type == "phong":
            rough = _phong_power_to_roughness(_get_val(src, "cosinePower", 30.0))
            _set_val(dst, "specularRoughness", rough)
    if _exists_attr(src, "transparency"):
        tr_connections = _list_connections_safe(src, "transparency", s=True, d=False, p=True)
        if not tr_connections:
            tr = _get_val(src, "transparency", None)
            if tr:
                _set_val(dst, "transmission", _avg_rgb(tr))
                _set_val(dst, "transmissionColor", (1, 1, 1))
    if src_type == "surfaceShader":
        outc = _get_val(src, "outColor", None)
        if outc:
            _set_val(dst, "baseColor", outc)
        matte = _get_val(src, "outMatteOpacity", None)
        if matte and isinstance(matte, (list, tuple)):
            _set_val(dst, "opacity", matte)
    return dst


def _standard_to_legacy(src_std, target_type):
    dst = _safe_shading_node(target_type, f"{src_std}_{target_type}_TMP")
    for std_attr, (legacy_attr, _) in STD_TO_LEGACY_COMMON.items():
        if legacy_attr and _exists_attr(dst, legacy_attr) and _exists_attr(src_std, std_attr):
            _set_val(dst, legacy_attr, _get_val(src_std, std_attr))
    tmap = STD_TO_LEGACY_TARGET.get(target_type, {})
    for std_attr, (legacy_attr, _) in tmap.items():
        if legacy_attr is None:
            continue
        if std_attr == "specularRoughness":
            rough = _get_val(src_std, "specularRoughness", 0.5)
            if target_type == "blinn":
                _set_val(dst, "eccentricity", _roughness_to_blinn_ecc(rough))
            elif target_type == "phong":
                _set_val(dst, "cosinePower", _roughness_to_phong_power(rough))
        else:
            if _exists_attr(src_std, std_attr) and _exists_attr(dst, legacy_attr):
                _set_val(dst, legacy_attr, _get_val(src_std, std_attr))
    if target_type == "lambert":
        if _exists_attr(dst, "diffuse") and _get_val(dst, "diffuse", None) is None:
            _set_val(dst, "diffuse", 0.8)
        if _exists_attr(dst, "reflectivity"):
            _set_val(dst, "reflectivity", 0.0)
    emission_color_connected = _incoming_src(f"{src_std}.emissionColor")[0] is not None
    emission_weight_connected = _incoming_src(f"{src_std}.emission")[0] is not None
    if not (emission_color_connected or emission_weight_connected):
        emission_color = _get_val(src_std, "emissionColor", (0, 0, 0))
        emission_weight = _get_val(src_std, "emission", 0.0)
        if emission_color and emission_weight > 0.001:
            scaled_emission = tuple(min(1.0, c * emission_weight) for c in emission_color)
            if _exists_attr(dst, "incandescence"):
                _set_val(dst, "incandescence", scaled_emission)
        else:
            if _exists_attr(dst, "incandescence"):
                _set_val(dst, "incandescence", (0, 0, 0))
    opac = _get_val(src_std, "opacity", None)
    trans_w = float(_get_val(src_std, "transmission", 0.0) or 0.0)
    if opac:
        inv = tuple(max(0.0, min(1.0, 1.0 - c)) for c in opac)
        inv_boosted = tuple(max(c, trans_w) for c in inv)
        if _exists_attr(dst, "transparency"):
            _set_val(dst, "transparency", inv_boosted)
    if target_type == "surfaceShader":
        col = _get_val(src_std, "baseColor", (0, 0, 0))
        emis = _get_val(src_std, "emissionColor", (0, 0, 0))
        mix = (min(1.0, col[0] + emis[0]), min(1.0, col[1] + emis[1]), min(1.0, col[2] + emis[2]))
        if _exists_attr(dst, "outColor"):
            _set_val(dst, "outColor", mix)
        if _exists_attr(dst, "outMatteOpacity"):
            op = _get_val(src_std, "opacity", (1, 1, 1))
            _set_val(dst, "outMatteOpacity", op)
    return dst


def _aiStandardSurface_to_standardSurface(src_ai):
    dst = _safe_shading_node("standardSurface", f"{src_ai}_standardSurface_TMP")
    ai_attrs = TYPE_ATTRS.get("aiStandardSurface", [])
    std_attrs = TYPE_ATTRS.get("standardSurface", [])
    for attr in ai_attrs:
        if attr in std_attrs and _exists_attr(src_ai, attr) and _exists_attr(dst, attr):
            _set_val(dst, attr, _get_val(src_ai, attr))
    return dst


def _standardSurface_to_aiStandardSurface(src_std):
    dst = _safe_shading_node("aiStandardSurface", f"{src_std}_aiStandardSurface_TMP")
    std_attrs = TYPE_ATTRS.get("standardSurface", [])
    ai_attrs = TYPE_ATTRS.get("aiStandardSurface", [])
    for attr in std_attrs:
        if attr in ai_attrs and _exists_attr(src_std, attr) and _exists_attr(dst, attr):
            _set_val(dst, attr, _get_val(src_std, attr))
    return dst


def _legacy_to_legacy_via_standard(src, target_type):
    mid = _legacy_to_standard(src)
    dst = _standard_to_legacy(mid, target_type)
    cmds.delete(mid)
    return dst


def _attribute_map_for_pair(src_type, dst_type):
    mapping = {}
    if dst_type == "standardSurface":
        for a, (std, _t) in LEGACY_TO_STD.items():
            if std:
                mapping[a] = std
        mapping["__SPECIAL__legacy_to_std"] = True
        return mapping
    if src_type == "standardSurface":
        for std, (leg, _t) in STD_TO_LEGACY_COMMON.items():
            if leg:
                mapping[std] = leg
        tmap = STD_TO_LEGACY_TARGET.get(dst_type, {})
        for std, (leg, _t) in tmap.items():
            if leg:
                mapping[std] = leg
        mapping["__SPECIAL__std_to_legacy"] = True
        mapping["__TARGET__"] = dst_type
        return mapping
    if src_type == "aiStandardSurface" and dst_type == "standardSurface":
        mapping["__AI_TO_STD__"] = True
        return mapping
    if src_type == "standardSurface" and dst_type == "aiStandardSurface":
        mapping["__STD_TO_AI__"] = True
        return mapping
    mapping["__VIA_STD__"] = True
    return mapping


def _handle_normal_texture_rewire(src_mat, dst_mat, src_attr, dst_attr, src_node, src_out):
    try:
        normal_info = _get_normal_texture_info(f"{src_mat}.{src_attr}")
        if not normal_info:
            return
        src_type = cmds.nodeType(src_mat)
        dst_type = cmds.nodeType(dst_mat)
        if dst_type in ["standardSurface", "aiStandardSurface"]:
            target_normal_type = "aiNormalMap"
        else:
            target_normal_type = "normalMap"
        if normal_info["normal_type"] == "bump2d":
            new_normal = cmds.shadingNode(target_normal_type, asUtility=True,
                                          name=f"{dst_mat}_{target_normal_type}")
            if normal_info["texture_node"]:
                _connect_if_possible(normal_info["texture_node"], "outColor", new_normal, "input")
            else:
                bump_input_node, bump_input_attr = _incoming_src(f"{normal_info['normal_node']}.bumpValue")
                if bump_input_node and bump_input_attr:
                    _connect_if_possible(bump_input_node, bump_input_attr, new_normal, "input")
            _connect_if_possible(new_normal, "outNormal", dst_mat, dst_attr)
        elif normal_info["normal_type"] == target_normal_type:
            _connect_if_possible(normal_info["normal_node"], "outNormal", dst_mat, dst_attr)
        else:
            new_normal = cmds.shadingNode(target_normal_type, asUtility=True,
                                          name=f"{dst_mat}_{target_normal_type}")
            if normal_info["texture_node"]:
                _connect_if_possible(normal_info["texture_node"], "outColor", new_normal, "input")
            else:
                _connect_if_possible(normal_info["normal_node"], "input", new_normal, "input")
            _connect_if_possible(new_normal, "outNormal", dst_mat, dst_attr)
    except Exception as e:
        print(f"[DEBUG] Error in normal texture rewire: {e}")
        _connect_if_possible(src_node, src_out, dst_mat, dst_attr)


def _handle_legacy_transparency_rewire(src_mat, dst_mat, src_attr, dst_attr, src_node, src_out, transparency_to_opacity=True):
    try:
        if transparency_to_opacity and dst_attr == "opacity":
            reverse_node = cmds.shadingNode("reverse", asUtility=True, name=f"{dst_mat}_reverse")
            _connect_if_possible(src_node, src_out, reverse_node, "input")
            _connect_if_possible(reverse_node, "output", dst_mat, dst_attr)
        elif not transparency_to_opacity and dst_attr == "transmission":
            _connect_if_possible(src_node, src_out, dst_mat, dst_attr)
        else:
            _connect_if_possible(src_node, src_out, dst_mat, dst_attr)
    except Exception as e:
        print(f"[DEBUG] Error in legacy transparency rewire: {e}")
        _connect_if_possible(src_node, src_out, dst_mat, dst_attr)


def _handle_standard_opacity_to_legacy_transparency_rewire(src_mat, dst_mat, src_attr, dst_attr, src_node, src_out, transparency_to_opacity=True):
    try:
        if transparency_to_opacity:
            reverse_node = cmds.shadingNode("reverse", asUtility=True, name=f"{dst_mat}_reverse")
            _connect_if_possible(src_node, src_out, reverse_node, "input")
            _connect_if_possible(reverse_node, "output", dst_mat, dst_attr)
        else:
            _connect_if_possible(src_node, src_out, dst_mat, dst_attr)
    except Exception as e:
        print(f"[DEBUG] Error in standard opacity -> legacy transparency rewire: {e}")
        _connect_if_possible(src_node, src_out, dst_mat, dst_attr)


def _handle_opacity_transparency_rewire(src_mat, dst_mat, src_attr, dst_attr, src_node, src_out, transparency_to_opacity=True):
    try:
        reverse_info = _has_reverse_node(f"{src_mat}.{src_attr}")
        src_has_reverse = reverse_info is not None
        needs_reverse = False
        if transparency_to_opacity:
            if src_attr == "transparency" and dst_attr == "opacity":
                needs_reverse = True
            elif src_attr == "opacity" and dst_attr == "transparency":
                needs_reverse = True
        if needs_reverse and not src_has_reverse:
            reverse_node = cmds.shadingNode("reverse", asUtility=True, name=f"{dst_mat}_reverse")
            _connect_if_possible(src_node, src_out, reverse_node, "input")
            _connect_if_possible(reverse_node, "output", dst_mat, dst_attr)
        elif not needs_reverse and src_has_reverse:
            reverse_input_node, reverse_input_attr = _incoming_src(f"{reverse_info['reverse_node']}.input")
            if reverse_input_node and reverse_input_attr:
                _connect_if_possible(reverse_input_node, reverse_input_attr, dst_mat, dst_attr)
            else:
                _connect_if_possible(src_node, src_out, dst_mat, dst_attr)
        else:
            _connect_if_possible(src_node, src_out, dst_mat, dst_attr)
    except Exception as e:
        print(f"[DEBUG] Error in opacity/transparency rewire: {e}")
        _connect_if_possible(src_node, src_out, dst_mat, dst_attr)


def _rewire_inputs(src_mat, dst_mat, mapping_hint):
    src_type = cmds.nodeType(src_mat)
    dst_type = cmds.nodeType(dst_mat)
    src_attrs = TYPE_ATTRS.get(src_type, cmds.listAttr(src_mat, k=True) or [])
    dst_attrs = set(TYPE_ATTRS.get(dst_type, cmds.listAttr(dst_mat, k=True) or []))

    def best_dst_for(src_attr):
        if mapping_hint:
            if mapping_hint.get("__VIA_STD__"):
                bridge = {
                    "color": "color",
                    "diffuse": "diffuse",
                    "incandescence": "incandescence",
                    "transparency": "transparency",
                    "specularColor": "specularColor",
                    "reflectivity": "reflectivity",
                    "eccentricity": "specularRoughness",
                    "cosinePower": "specularRoughness",
                }
                guess = bridge.get(src_attr)
                if guess and guess in dst_attrs:
                    return guess
            elif mapping_hint.get("__SPECIAL__legacy_to_std"):
                guess = LEGACY_TO_STD.get(src_attr, (None, None))[0]
                if guess and guess in dst_attrs:
                    return guess
            elif mapping_hint.get("__SPECIAL__std_to_legacy"):
                for std_attr, leg in list(STD_TO_LEGACY_COMMON.items()) + list(STD_TO_LEGACY_TARGET.get(mapping_hint.get("__TARGET__", ""), {}).items()):
                    leg_attr = leg[0]
                    if std_attr == src_attr and leg_attr in dst_attrs:
                        return leg_attr
            elif mapping_hint.get("__AI_TO_STD__") or mapping_hint.get("__STD_TO_AI__"):
                if src_attr in dst_attrs:
                    return src_attr
        if src_attr in dst_attrs:
            return src_attr
        heur = {
            "baseColor": "color",
            "emissionColor": "incandescence",
            "transparency": "transmission",
            "transmission": "transparency",
            "transmissionColor": "transparency",
        }
        if src_attr in heur and heur[src_attr] in dst_attrs:
            return heur[src_attr]
        return None

    for a in src_attrs:
        plug = f"{src_mat}.{a}"
        src_node, src_out = _incoming_src(plug)
        if not src_node:
            continue
        dst_attr = best_dst_for(a)
        if not dst_attr:
            continue
        if a == "transparency" and dst_attr == "opacity":
            _handle_legacy_transparency_rewire(src_mat, dst_mat, a, dst_attr, src_node, src_out)
        elif a == "transparency" and dst_attr == "transmission":
            _handle_legacy_transparency_rewire(src_mat, dst_mat, a, dst_attr, src_node, src_out, transparency_to_opacity=False)
        elif a == "opacity" and dst_attr == "transparency":
            _handle_standard_opacity_to_legacy_transparency_rewire(src_mat, dst_mat, a, dst_attr, src_node, src_out)
        elif a in ("transparency", "opacity") or dst_attr in ("transparency", "opacity"):
            _handle_opacity_transparency_rewire(src_mat, dst_mat, a, dst_attr, src_node, src_out)
        elif a == "normalCamera" and dst_attr == "normalCamera":
            _handle_normal_texture_rewire(src_mat, dst_mat, a, dst_attr, src_node, src_out)
        else:
            _connect_if_possible(src_node, src_out, dst_mat, dst_attr)


def _reconnect_sgs(src_old, dst_new):
    old_sgs = _sgs_of_material(src_old)
    old_sg_name = f"{src_old}_SG"
    old_sg = _unique_name(old_sg_name)
    old_sg_node = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=old_sg)
    try:
        if cmds.objExists(f"{src_old}.outColor") and cmds.objExists(f"{old_sg_node}.surfaceShader"):
            cmds.connectAttr(f"{src_old}.outColor", f"{old_sg_node}.surfaceShader", f=True)
    except Exception:
        pass
    for sg in old_sgs:
        try:
            if cmds.objExists(f"{dst_new}.outColor") and cmds.objExists(f"{sg}.surfaceShader"):
                cmds.connectAttr(f"{dst_new}.outColor", f"{sg}.surfaceShader", f=True)
        except Exception:
            pass


def _unique_name(base):
    if not cmds.objExists(base):
        return base
    i = 1
    while cmds.objExists(f"{base}_{i}"):
        i += 1
    return f"{base}_{i}"


def convert_material_preserve_name(src_mat, target_type, rewire_textures=True):
    if not cmds.objExists(src_mat) or cmds.nodeType(src_mat) not in TYPE_ATTRS:
        raise RuntimeError(f"Not a supported shader node: {src_mat}")

    src_type = cmds.nodeType(src_mat)
    if src_type == target_type:
        return (src_mat, src_mat, src_mat, src_mat)

    orig_name = src_mat
    old_name = _unique_name(f"{orig_name}_old")

    if target_type == "standardSurface":
        if src_type == "aiStandardSurface":
            dst_tmp = _aiStandardSurface_to_standardSurface(src_mat)
        elif src_type != "standardSurface":
            dst_tmp = _legacy_to_standard(src_mat)
        else:
            return (orig_name, orig_name, src_mat, src_mat)
        dst_node = dst_tmp
    elif target_type == "aiStandardSurface":
        if src_type == "standardSurface":
            dst_node = _standardSurface_to_aiStandardSurface(src_mat)
        else:
            mid = _legacy_to_standard(src_mat)
            dst_node = _standardSurface_to_aiStandardSurface(mid)
            cmds.delete(mid)
    elif src_type == "standardSurface" and target_type in ("lambert", "blinn", "phong", "surfaceShader"):
        dst_node = _standard_to_legacy(src_mat, target_type)
    elif src_type == "aiStandardSurface" and target_type in ("lambert", "blinn", "phong", "surfaceShader"):
        mid = _aiStandardSurface_to_standardSurface(src_mat)
        dst_node = _standard_to_legacy(mid, target_type)
        cmds.delete(mid)
    else:
        dst_node = _legacy_to_legacy_via_standard(src_mat, target_type)

    mapping_hint = _attribute_map_for_pair(src_type, target_type)

    src_old = cmds.rename(src_mat, old_name)
    new_name = _unique_name(orig_name) if cmds.objExists(orig_name) else orig_name
    dst_new = cmds.rename(dst_node, new_name)

    if rewire_textures:
        try:
            _rewire_inputs(src_old, dst_new, mapping_hint)
        except Exception:
            pass

    _reconnect_sgs(src_old, dst_new)
    return (old_name, new_name, src_old, dst_new)


# Complete standalone stylesheet - no external dependencies
FALLBACK_STYLESHEET = """
/* ---- Base font ---- */
* {
    font-family: 'Segoe UI';
    font-size: 12px;
}

/* ---------------------------------------------
   Main Dialog
   --------------------------------------------- */
QDialog {
    background-color: #333333;
    color: #ffffff;
    font-family: 'Segoe UI';
    font-size: 12px;
}

/* ---------------------------------------------
   Labels
   --------------------------------------------- */
QLabel {
    color: #ffffff;
    font-family: 'Segoe UI';
    font-size: 12px;
}

/* ---------------------------------------------
   Buttons
   --------------------------------------------- */
QPushButton {
    font-family: 'Segoe UI';
    font-size: 12px;
    color: #ffffff;
    background-color: #444444;
    border: 1px solid #666666;
    border-radius: 6px;
    padding: 3px 6px;
    margin: 2px;
}

QPushButton:hover {
    background-color: #555555;
    border: 1px solid #777777;
}

QPushButton:pressed {
    background-color: #333333;
    border: 1px solid #555555;
}

QPushButton:disabled {
    color: #888888;
    background-color: #3a3a3a;
    border: 1px solid #555555;
}

/* ---------------------------------------------
   Text Areas
   --------------------------------------------- */
QTextEdit {
    font-family: 'Segoe UI';
    font-size: 12px;
    color: #ffffff;
    background-color: #1e1e1e;
    border: 1px solid #666666;
    border-radius: 6px;
    padding: 8px;
}

QTextEdit:focus {
    border: 1px solid #00f7c8;
}

/* ---------------------------------------------
   Line Edit
   --------------------------------------------- */
QLineEdit {
    font-family: 'Segoe UI';
    font-size: 12px;
    color: #ffffff;
    background-color: #333333;
    border: 1px solid #666666;
    border-radius: 6px;
    padding: 4px 8px;
}

QLineEdit:focus {
    border: 1px solid #00f7c8;
}

QLineEdit:disabled {
    color: #888888;
    background-color: #3a3a3a;
    border: 1px solid #555555;
}

/* ---------------------------------------------
   Checkboxes
   --------------------------------------------- */
QCheckBox {
    font-family: 'Segoe UI';
    font-size: 11px;
    color: #dddddd;
    border: none;
    border-radius: 6px;
    padding: 2px 6px;
    margin: 1px 0;
}

QCheckBox:checked {
    color: #00f7c8;
}

QCheckBox::indicator {
    width: 12px;
    height: 12px;
    border: 1px solid #444444;
    border-radius: 3px;
}

QCheckBox::indicator:checked {
    background-color: #ffffff;
    border: 1px solid #2b2b2b;
}

QCheckBox::indicator:unchecked {
    background-color: #2b2b2b;
    border: 1px solid #444444;
}

QCheckBox::indicator:checked:hover,
QCheckBox::indicator:unchecked:hover {
    border: 1px solid #ffffff;
}

QCheckBox::indicator:checked:pressed,
QCheckBox::indicator:unchecked:pressed {
    background-color: #ffffff;
    border: 1px solid #ffffff;
}

QCheckBox:disabled {
    color: #666666;
    background-color: #3a3a3a;
    border-radius: 6px;
    padding: 2px 6px;
}

/* ---------------------------------------------
   Scrollbars
   --------------------------------------------- */
QScrollBar:vertical {
    background-color: #2b2b2b;
    width: 12px;
    border-radius: 6px;
}

QScrollBar::handle:vertical {
    background-color: #555555;
    border-radius: 6px;
    min-height: 20px;
}

QScrollBar::handle:vertical:hover {
    background-color: #666666;
}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {
    height: 0px;
}

QScrollBar:horizontal {
    background-color: #2b2b2b;
    height: 12px;
    border-radius: 6px;
}

QScrollBar::handle:horizontal {
    background-color: #555555;
    border-radius: 6px;
    min-width: 20px;
}

QScrollBar::handle:horizontal:hover {
    background-color: #666666;
}

QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {
    width: 0px;
}

/* ---------------------------------------------
   Tooltips
   --------------------------------------------- */
QToolTip {
    font-family: 'Segoe UI';
    font-size: 11px;
    color: #ffffff;
    background-color: #1e1e1e;
    border: 1px solid #666666;
    border-radius: 4px;
    padding: 4px 8px;
}
"""

PROCESS_OPTIONS = [
    ("Selected Meshes", "Selected Meshes"),
    ("Selected Materials", "Selected Materials"),
    ("All Materials", "All Materials"),
]

DEFAULT_MATERIALS = {"lambert1", "standardSurface1", "particleCloud1"}

MERGE_DETAIL_ATTRS = [
    "color",
    "baseColor",
    "specularRoughness",
    "metalness",
    "emission",
    "emissionColor",
    "transmission",
    "transmissionColor",
    "opacity",
]


def _coerce_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("1", "true", "yes", "on")


def _maya_main_window():
    ptr = omui.MQtUtil.mainWindow()
    return wrapInstance(int(ptr), QtWidgets.QWidget)


def _gather_materials(process_key):
    """Collect materials based on the chosen processing scope."""
    if process_key == "Selected Meshes":
        sel = cmds.ls(sl=True, l=True) or []
        if not sel:
            return [], "materials on Selected Meshes"
        mats = set()
        for node in sel:
            for shape in cmds.listRelatives(node, s=True, f=True) or []:
                for sg in cmds.listConnections(shape, type="shadingEngine") or []:
                    mats.update(cmds.ls(cmds.listConnections(sg + ".surfaceShader") or [],
                                        materials=True))
        mats = sorted(set(mats) - DEFAULT_MATERIALS)
        return mats, "materials on Selected Meshes"

    if process_key == "Selected Materials":
        sel = cmds.ls(sl=True, materials=True) or []
        return sorted(set(sel) - DEFAULT_MATERIALS), "Selected Materials"

    # All materials
    mats = cmds.ls(materials=True) or []
    return sorted(set(mats) - DEFAULT_MATERIALS), "All Materials"


def _analyze_material_inputs(material, target_type):
    """Mirror of analyzer from material_converter for preview summaries."""
    convertible = []
    non_convertible = []

    src_type = cmds.nodeType(material)
    src_attrs = TYPE_ATTRS.get(src_type, [])

    for attr in src_attrs:
        connections = cmds.listConnections(f"{material}.{attr}", s=True, d=False) or []
        if not connections:
            continue

        dst_attr = _get_best_dst_for_attr(attr, target_type)
        node = connections[0]
        node_type = cmds.nodeType(node)
        if dst_attr:
            convertible.append(f"{attr} ({node_type}) → {dst_attr}")
        else:
            non_convertible.append(f"{attr} ({node_type})")

    return convertible, non_convertible


def _get_best_dst_for_attr(src_attr, dst_type):
    heuristics = {
        "color": "baseColor",
        "diffuse": "base",
        "incandescence": "emissionColor",
        "transparency": "transmission",
        "specularColor": "specularColor",
        "reflectivity": "specular",
        "eccentricity": "specularRoughness",
        "cosinePower": "specularRoughness",
        "normalCamera": "normalCamera",

        "baseColor": "color",
        "base": "diffuse",
        "emissionColor": "incandescence",
        "transmission": "transparency",
        "specular": "reflectivity",
        "specularRoughness": "eccentricity",
        "opacity": "transparency",
        "transmissionColor": "transparency",
    }

    mapped = heuristics.get(src_attr)
    if mapped:
        dst_attrs = TYPE_ATTRS.get(dst_type, [])
        if mapped in dst_attrs:
            return mapped

    dst_attrs = TYPE_ATTRS.get(dst_type, [])
    if src_attr in dst_attrs:
        return src_attr
    return None


def _numeric_attrs(node):
    keep = TYPE_ATTRS.get(cmds.nodeType(node), [])
    return [a for a in keep if cmds.attributeQuery(a, node=node, exists=True)]


def _attr_val(node, attr):
    try:
        if cmds.attributeQuery(attr, node=node, numberOfChildren=True):
            v = cmds.getAttr(f"{node}.{attr}")[0]
            return tuple(round(float(x), 3) for x in v)
        v = cmds.getAttr(f"{node}.{attr}")
        if isinstance(v, (bool, int)):
            return v
        if isinstance(v, float):
            return round(float(v), 3)
    except Exception:
        pass
    return None


def _format_attr_entry(node, attr):
    connections = cmds.listConnections(f"{node}.{attr}", s=True, d=False) or []
    if connections:
        node_type = cmds.nodeType(connections[0])
        return f"{attr} <- ({node_type})"
    val = _attr_val(node, attr)
    if val is None:
        return None
    return f"{attr}: {val}"


def _normalized_attr_value(material, attr):
    val = _attr_val(material, attr)
    if attr == "opacity" and isinstance(val, (tuple, list)):
        try:
            return round(sum(val) / len(val), 3)
        except Exception:
            return None
    return val


def _format_scalar(value):
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        text = f"{value:.3f}"
        if "." in text:
            stripped = text.rstrip("0")
            if stripped.endswith("."):
                stripped = stripped + "0"
            return stripped
        return text
    return str(value)


def _format_value_html(attr, value):
    if value is None:
        return "-"
    if isinstance(value, (bool, int, float)):
        text = _format_scalar(value)
        return text
    if isinstance(value, tuple):
        components = []
        for comp in value:
            components.append(_format_scalar(float(comp)))
        text = ", ".join(components)
        if attr.lower() in {"basecolor", "emissioncolor", "transmissioncolor", "color"}:
            hex_color = _rgb_to_hex(value)
            return f"<span style='color:{hex_color};'>{text}</span>"
        return text
    return str(value)


def _within_tol(v1, v2, tol):
    if v1 is None or v2 is None:
        return False
    if isinstance(v1, (tuple, list)):
        return all(_within_tol(a, b, tol) for a, b in zip(v1, v2))
    if v1 == 0:
        return abs(v2) <= tol
    return abs(v1 - v2) / max(abs(v1), 1e-6) <= tol


def _value_delta(v1, v2):
    if v1 is None or v2 is None:
        return None
    if isinstance(v1, (tuple, list)):
        diffs = [abs(a - b) for a, b in zip(v1, v2)]
        return round(max(diffs), 3)
    return round(abs(v1 - v2), 3)


def _get_material_color_rgb(material):
    node_type = cmds.nodeType(material)
    attr = "baseColor" if node_type in ("standardSurface", "aiStandardSurface") else "color"
    plug = f"{material}.{attr}"
    try:
        if cmds.attributeQuery(attr, node=material, numberOfChildren=True):
            return cmds.getAttr(plug)[0]
        val = cmds.getAttr(plug)
        if isinstance(val, (tuple, list)) and len(val) == 3:
            return val
    except Exception:
        pass
    return None


def _color_name(rgb):
    if not rgb:
        return "unknown"
    r, g, b = rgb
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    if s < 0.08 or (max(rgb) - min(rgb)) < 0.05:
        if l <= 0.15:
            return "black"
        if l <= 0.35:
            return "dark gray"
        if l <= 0.65:
            return "gray"
        if l <= 0.85:
            return "light gray"
        return "white"

    hue = (h * 360.0) % 360.0
    bins = [
        ("red", 350, 360), ("red", 0, 15), ("orange", 15, 45),
        ("yellow", 45, 70), ("green", 70, 160), ("cyan", 160, 200),
        ("blue", 200, 260), ("purple", 260, 320), ("pink", 320, 350),
    ]
    color = "color"
    for name, lo, hi in bins:
        if lo <= hue < hi:
            color = name
            break
    brightness = ""
    if l <= 0.25:
        brightness = "dark "
    elif l >= 0.75:
        brightness = "light "
    return f"{brightness}{color}".strip()


def _rgb_to_hex(rgb):
    if not rgb:
        return "#ffffff"
    try:
        r, g, b = rgb
    except Exception:
        return "#ffffff"
    r = max(0, min(255, int(round(float(r) * 255.0))))
    g = max(0, min(255, int(round(float(g) * 255.0))))
    b = max(0, min(255, int(round(float(b) * 255.0))))
    return f"#{r:02x}{g:02x}{b:02x}"


def _project_name():
    try:
        project_root = cmds.workspace(q=True, rd=True) or ""
        return os.path.basename(os.path.normpath(project_root)) if project_root else "project"
    except Exception:
        return "project"


def _scene_name():
    try:
        scene_path = cmds.file(q=True, sn=True) or ""
        return os.path.splitext(os.path.basename(scene_path))[0] if scene_path else "untitled"
    except Exception:
        return "untitled"


def _token_map(material):
    color_rgb = _get_material_color_rgb(material)
    color_token = _color_name(color_rgb) if color_rgb else "color"
    color_attr = "baseColor" if _exists_attr(material, "baseColor") else "color" if _exists_attr(material, "color") else None
    if not color_attr and _exists_attr(material, "outColor"):
        color_attr = "outColor"
    if color_attr:
        color_connections = cmds.listConnections(f"{material}.{color_attr}", s=True, d=False) or []
        if color_connections:
            color_token = "texture"
    shader_token = cmds.nodeType(material)
    tokens = {
        "(name)": material,
        "(color)": color_token,
        "(shader)": shader_token,
        "(project)": _project_name(),
        "(scene)": _scene_name(),
    }
    # legacy aliases
    tokens["(current)"] = tokens["(name)"]
    tokens["(mat_type)"] = tokens["(shader)"]
    return tokens


def _format_color_display(material, color_token):
    if not color_token:
        return "color"
    if isinstance(color_token, str):
        return color_token
    return str(color_token)


_TOKEN_PATTERN_CACHE = {}


def _apply_tokens(text, tokens):
    if text is None:
        return ""
    tokens_lower = {k.lower(): v for k, v in tokens.items()}
    pattern_key = tuple(sorted(tokens_lower.keys()))
    pattern = _TOKEN_PATTERN_CACHE.get(pattern_key)
    if pattern is None:
        pattern = re.compile("|".join(re.escape(k) for k in tokens_lower.keys()), re.IGNORECASE)
        _TOKEN_PATTERN_CACHE[pattern_key] = pattern

    def _replace(match):
        key = match.group(0).lower()
        return tokens_lower.get(key, match.group(0))

    return pattern.sub(_replace, text)


def _build_name(prefix, core, suffix, tokens):
    core_text = core.strip()
    prefix_text = _apply_tokens(prefix, tokens).strip()
    suffix_text = _apply_tokens(suffix, tokens).strip()
    if core_text:
        core_resolved = _apply_tokens(core_text, tokens).strip()
    else:
        core_resolved = tokens.get("(name)", "")
    parts = [p for p in [prefix_text, core_resolved, suffix_text] if p]
    candidate = "_".join(parts) if parts else tokens.get("(name)", "material")
    return candidate


def _unique_rename(base_name, used):
    candidate = base_name
    index = 1
    while candidate in used or cmds.objExists(candidate):
        candidate = f"{base_name}_{index}"
        index += 1
    used.add(candidate)
    return candidate


# --------------------------------------------------------------------------------------
#                                   DIALOG
# --------------------------------------------------------------------------------------

class MaterialManagerDialog(QtWidgets.QDialog):
    WINDOW_OBJECT = "MaterialManagerWindow"

    def __init__(self, parent=None):
        old = omui.MQtUtil.findControl(self.WINDOW_OBJECT)
        if old:
            try:
                QtWidgets.QWidget.find(old).close()
            except Exception:
                pass

        super().__init__(parent or _maya_main_window())
        self.setObjectName(self.WINDOW_OBJECT)
        self.setWindowTitle("Material Manager")
        self.setMinimumWidth(500)
        self.setMinimumHeight(540)

        self._ops_pairs = []
        self._last_preview = None

        self._build_ui()
        self._connect_signals()

        self.setStyleSheet(FALLBACK_STYLESHEET)
        self._settings = QtCore.QSettings("QuickMaterials", "MaterialManager")
        self._load_settings()

    # ---------------- UI ----------------

    def _build_ui(self):
        title = QtWidgets.QLabel("Material Manager")
        title.setAlignment(QtCore.Qt.AlignCenter)
        title.setStyleSheet("font-weight:600; font-size:16px; padding:2px;")

        def make_separator(height=1):
            line = QtWidgets.QFrame()
            line.setFrameShape(QtWidgets.QFrame.HLine)
            line.setFrameShadow(QtWidgets.QFrame.Plain)
            line.setLineWidth(1)
            line.setFixedHeight(height)
            line.setStyleSheet("background-color:#454545; border:none;")
            return line

        process_row = QtWidgets.QHBoxLayout()
        process_row.setContentsMargins(0, 0, 0, 0)
        process_row.setSpacing(6)
        process_row.addWidget(QtWidgets.QLabel("Process:"))
        self.process_combo = QtWidgets.QComboBox()
        self.process_combo.addItems([label for label, _ in PROCESS_OPTIONS])
        self.process_combo.setMinimumWidth(180)
        self.process_combo.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.process_combo.setToolTip("Choose which materials the manager operates on.")
        process_row.addWidget(self.process_combo)
        process_row.addStretch(1)

        list_row = QtWidgets.QHBoxLayout()
        list_row.setContentsMargins(0, 0, 0, 0)
        list_row.setSpacing(6)
        self.list_btn = QtWidgets.QPushButton("List Materials")
        self.list_btn.setFixedHeight(24)
        self.list_btn.setToolTip("Preview the materials included by the current process scope.")
        self.list_btn.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        list_row.addWidget(self.list_btn, 1)

        convert_title = QtWidgets.QLabel("Convert Materials")
        convert_title.setStyleSheet("font-weight:600; margin:0px; color:#ffffff;")

        convert_top = QtWidgets.QHBoxLayout()
        convert_top.setContentsMargins(0, 0, 0, 0)
        convert_top.setSpacing(6)
        convert_top.addWidget(QtWidgets.QLabel("Target:"))
        self.target_combo = QtWidgets.QComboBox()
        self.target_combo.addItems(["lambert", "blinn", "phong", "standardSurface", "aiStandardSurface", "surfaceShader"])
        self.target_combo.setMinimumWidth(160)
        self.target_combo.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.target_combo.setToolTip("Select the target material type for conversion.")
        convert_top.addWidget(self.target_combo)
        convert_top.addStretch(1)

        self.rewire_cb = QtWidgets.QCheckBox("Reconnect textures")
        self.rewire_cb.setChecked(True)
        self.rewire_cb.setToolTip("Attempt to reconnect textures and utility nodes during conversion.")
        convert_rewire_row = QtWidgets.QHBoxLayout()
        convert_rewire_row.setContentsMargins(0, 0, 0, 0)
        convert_rewire_row.setSpacing(6)
        convert_rewire_row.addWidget(self.rewire_cb)
        convert_rewire_row.addStretch(1)

        convert_action_row = QtWidgets.QHBoxLayout()
        convert_action_row.setContentsMargins(0, 0, 0, 0)
        convert_action_row.setSpacing(6)
        self.preview_convert_btn = QtWidgets.QPushButton("Preview Conversion")
        self.preview_convert_btn.setFixedHeight(28)
        self.preview_convert_btn.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self.preview_convert_btn.setToolTip("Summarize conversion results without changing the scene.")
        self.preview_convert_btn.setStyleSheet("font-style: italic; color: #d0d0d0;")
        self.convert_btn = QtWidgets.QPushButton("Convert Materials")
        self.convert_btn.setFixedHeight(28)
        self.convert_btn.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.convert_btn.setToolTip("Execute the conversion using the current options.")
        self.convert_btn.setStyleSheet("color: #00f7c8; font-weight:600;")
        convert_action_row.addWidget(self.preview_convert_btn)
        convert_action_row.addWidget(self.convert_btn)
        self.auto_delete_cb = QtWidgets.QCheckBox("Delete old materials after conversion")
        self.auto_delete_cb.setToolTip("Remove *_old backups automatically after conversion completes.")
        convert_options_row = QtWidgets.QHBoxLayout()
        convert_options_row.setContentsMargins(0, 0, 0, 0)
        convert_options_row.setSpacing(6)
        convert_options_row.addWidget(self.auto_delete_cb)
        convert_options_row.addStretch(1)

        merge_title = QtWidgets.QLabel("Merge Materials")
        merge_title.setStyleSheet("font-weight:600; margin:0px; color:#ffffff;")

        merge_preview_row = QtWidgets.QHBoxLayout()
        merge_preview_row.setContentsMargins(0, 0, 0, 0)
        merge_preview_row.setSpacing(6)
        merge_preview_row.addWidget(QtWidgets.QLabel("Tolerance:"))
        self.tol_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.tol_slider.setRange(0, 100)
        self.tol_slider.setValue(10)
        self.tol_slider.setFixedHeight(16)
        self.tol_slider.setToolTip("Set the tolerance percentage when comparing materials for merging.")
        merge_preview_row.addWidget(self.tol_slider, 1)
        self.tol_label = QtWidgets.QLabel("Tol 10%")
        self.tol_label.setToolTip("Displays the current merge tolerance percentage.")
        merge_preview_row.addWidget(self.tol_label)
        self._update_tol_label(self.tol_slider.value())

        merge_action_row = QtWidgets.QHBoxLayout()
        merge_action_row.setContentsMargins(0, 0, 0, 0)
        merge_action_row.setSpacing(6)
        self.preview_merge_btn = QtWidgets.QPushButton("Preview Merge")
        self.preview_merge_btn.setFixedHeight(28)
        self.preview_merge_btn.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self.preview_merge_btn.setToolTip("Identify merge groups at the current tolerance without changing the scene.")
        self.preview_merge_btn.setStyleSheet("font-style: italic; color: #d0d0d0;")
        self.merge_btn = QtWidgets.QPushButton("Merge Materials")
        self.merge_btn.setFixedHeight(28)
        self.merge_btn.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.merge_btn.setToolTip("Merge materials that fall within the tolerance threshold.")
        self.merge_btn.setStyleSheet("color: #00f7c8; font-weight:600;")
        merge_action_row.addWidget(self.preview_merge_btn)
        merge_action_row.addWidget(self.merge_btn)

        rename_title = QtWidgets.QLabel("Rename Materials")
        rename_title.setStyleSheet("font-weight:600; margin:0px; color:#ffffff;")

        rename_row = QtWidgets.QHBoxLayout()
        rename_row.setContentsMargins(0, 0, 0, 0)
        rename_row.setSpacing(6)
        self.prefix_edit = QtWidgets.QLineEdit()
        self.prefix_edit.setPlaceholderText("Prefix")
        self.prefix_edit.setToolTip("Optional prefix; supports tokens like (name), (color), (shader), (project), (scene).")
        self.suffix_edit = QtWidgets.QLineEdit()
        self.suffix_edit.setPlaceholderText("Suffix")
        self.suffix_edit.setToolTip("Optional suffix; supports tokens like (name), (color), (shader), (project), (scene).")
        rename_row.addWidget(self.prefix_edit, 1)
        rename_row.addWidget(self.suffix_edit, 1)

        self.main_edit = QtWidgets.QLineEdit()
        self.main_edit.setPlaceholderText("Main pattern e.g. (name)_(shader)")
        self.main_edit.setToolTip("Define the core naming pattern. Tokens: (name), (color), (shader), (project), (scene).")
        rename_main_row = QtWidgets.QHBoxLayout()
        rename_main_row.setContentsMargins(0, 0, 0, 0)
        rename_main_row.setSpacing(6)
        rename_main_row.addWidget(self.main_edit, 1)

        rename_btn_row = QtWidgets.QHBoxLayout()
        rename_btn_row.setContentsMargins(0, 0, 0, 0)
        rename_btn_row.setSpacing(6)
        self.preview_rename_btn = QtWidgets.QPushButton("Preview Rename")
        self.preview_rename_btn.setFixedHeight(28)
        self.preview_rename_btn.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self.preview_rename_btn.setToolTip("Preview the new names before applying them.")
        self.preview_rename_btn.setStyleSheet("font-style: italic; color: #d0d0d0;")
        rename_btn_row.addWidget(self.preview_rename_btn)
        self.rename_btn = QtWidgets.QPushButton("Rename Materials")
        self.rename_btn.setFixedHeight(28)
        self.rename_btn.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.rename_btn.setToolTip("Apply the naming pattern to the selected materials.")
        self.rename_btn.setStyleSheet("color: #00f7c8; font-weight:600;")
        rename_btn_row.addWidget(self.rename_btn)

        self.log = QtWidgets.QTextEdit()
        self.log.setReadOnly(True)

        foot = QtWidgets.QHBoxLayout()
        foot.setContentsMargins(0, 0, 0, 0)
        foot.setSpacing(4)
        self.clear_log_btn = QtWidgets.QPushButton("Clear Log")
        self.clear_log_btn.setToolTip("Clear the log output.")
        self.close_btn = QtWidgets.QPushButton("Close")
        self.close_btn.setToolTip("Close the Material Manager.")
        foot.addWidget(self.clear_log_btn)
        foot.addStretch(1)
        foot.addWidget(self.close_btn)

        self.show_details_cb = QtWidgets.QCheckBox("Show Details")
        self.show_details_cb.setChecked(True)
        self.show_details_cb.setToolTip("Toggle detailed attribute information in previews.")
        details_row = QtWidgets.QHBoxLayout()
        details_row.setContentsMargins(0, 0, 0, 0)
        details_row.setSpacing(6)
        details_row.addWidget(self.show_details_cb)
        details_row.addStretch(1)

        left_panel_top = QtWidgets.QVBoxLayout()
        left_panel_top.setContentsMargins(0, 0, 0, 0)
        left_panel_top.setSpacing(6)
        left_panel_top.addLayout(process_row)
        left_panel_top.addLayout(list_row)

        convert_frame = QtWidgets.QFrame()
        convert_frame.setFrameShape(QtWidgets.QFrame.NoFrame)
        convert_frame.setObjectName("convertFrame")
        convert_frame.setStyleSheet(
            "QFrame#convertFrame { background-color: #333333; border-radius: 6px; border: 1px solid #444444; }"
        )
        convert_group = QtWidgets.QVBoxLayout(convert_frame)
        convert_group.setContentsMargins(10, 8, 10, 10)
        convert_group.setSpacing(6)
        convert_group.addWidget(convert_title)
        convert_group.addWidget(make_separator())
        convert_group.addLayout(convert_top)
        convert_group.addLayout(convert_rewire_row)
        convert_group.addLayout(convert_action_row)
        convert_group.addLayout(convert_options_row)

        merge_frame = QtWidgets.QFrame()
        merge_frame.setFrameShape(QtWidgets.QFrame.NoFrame)
        merge_frame.setObjectName("mergeFrame")
        merge_frame.setStyleSheet(
            "QFrame#mergeFrame { background-color: #333333; border-radius: 6px; border: 1px solid #444444; }"
        )
        merge_group = QtWidgets.QVBoxLayout(merge_frame)
        merge_group.setContentsMargins(10, 8, 10, 10)
        merge_group.setSpacing(6)
        merge_group.addWidget(merge_title)
        merge_group.addWidget(make_separator())
        merge_group.addLayout(merge_preview_row)
        merge_group.addLayout(merge_action_row)

        rename_frame = QtWidgets.QFrame()
        rename_frame.setFrameShape(QtWidgets.QFrame.NoFrame)
        rename_frame.setObjectName("renameFrame")
        rename_frame.setStyleSheet(
            "QFrame#renameFrame { background-color: #333333; border-radius: 6px; border: 1px solid #444444; }"
        )
        rename_group = QtWidgets.QVBoxLayout(rename_frame)
        rename_group.setContentsMargins(10, 8, 10, 10)
        rename_group.setSpacing(6)
        rename_group.addWidget(rename_title)
        rename_group.addWidget(make_separator())
        rename_group.addLayout(rename_row)
        rename_group.addLayout(rename_main_row)
        rename_group.addLayout(rename_btn_row)

        left_panel_center = QtWidgets.QVBoxLayout()
        left_panel_center.setContentsMargins(0, 0, 0, 0)
        left_panel_center.setSpacing(10)
        left_panel_center.addWidget(convert_frame)
        left_panel_center.addWidget(merge_frame)
        left_panel_center.addStretch(1)
        left_panel_center.addWidget(rename_frame)

        left_panel = QtWidgets.QVBoxLayout()
        left_panel.setContentsMargins(0, 0, 0, 0)
        left_panel.setSpacing(6)
        left_panel.addLayout(left_panel_top)
        left_panel.addLayout(left_panel_center)
        left_panel.addStretch(1)

        buttons_container = QtWidgets.QWidget()
        buttons_container.setLayout(foot)
        buttons_container.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)

        left_panel_container = QtWidgets.QWidget()
        left_container_layout = QtWidgets.QVBoxLayout(left_panel_container)
        left_container_layout.setContentsMargins(0, 0, 0, 0)
        left_container_layout.setSpacing(6)
        left_container_layout.addLayout(left_panel)
        left_panel_container.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding)
        left_panel_container.setMinimumWidth(260)
        left_panel_container.setMaximumWidth(300)

        main_layout = QtWidgets.QHBoxLayout()
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(8)
        main_layout.addWidget(left_panel_container)

        log_column = QtWidgets.QVBoxLayout()
        log_column.setContentsMargins(0, 0, 0, 0)
        log_column.setSpacing(6)
        log_column.addLayout(details_row)
        log_column.addWidget(self.log, 1)
        log_column.addWidget(buttons_container)
        main_layout.addLayout(log_column, 1)

        outer_frame = QtWidgets.QFrame()
        outer_frame.setObjectName("outerFrame")
        outer_frame.setStyleSheet("QFrame#outerFrame { border: 1px solid #444444; border-radius: 8px; background-color: #333333; }")
        outer_layout = QtWidgets.QVBoxLayout(outer_frame)
        outer_layout.setContentsMargins(6, 6, 6, 6)
        outer_layout.setSpacing(6)
        outer_layout.addLayout(main_layout)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)
        lay.addWidget(title)
        lay.addWidget(outer_frame)

    def _connect_signals(self):
        self.list_btn.clicked.connect(self._list_materials)
        self.preview_convert_btn.clicked.connect(self._preview_conversion)
        self.convert_btn.clicked.connect(self._convert_materials)
        self.preview_merge_btn.clicked.connect(self._preview_merge)
        self.merge_btn.clicked.connect(self._merge_materials)
        self.preview_rename_btn.clicked.connect(self._preview_rename)
        self.rename_btn.clicked.connect(self._rename_materials)
        self.clear_log_btn.clicked.connect(self._clear_log)
        self.close_btn.clicked.connect(self.close)
        self.show_details_cb.toggled.connect(self._refresh_log_display)
        self.tol_slider.valueChanged.connect(self._update_tol_label)

    def _load_settings(self):
        settings = self._settings
        process = settings.value("processMode")
        if process:
            idx = self.process_combo.findText(process)
            if idx != -1:
                self.process_combo.setCurrentIndex(idx)
        target = settings.value("targetType")
        if target:
            idx = self.target_combo.findText(target)
            if idx != -1:
                self.target_combo.setCurrentIndex(idx)
        self.rewire_cb.setChecked(_coerce_bool(settings.value("rewireTextures"), True))
        self.auto_delete_cb.setChecked(_coerce_bool(settings.value("autoDeleteOld"), False))
        self.show_details_cb.setChecked(_coerce_bool(settings.value("showDetails"), True))
        tol_value = settings.value("mergeTolerance")
        if tol_value is not None:
            try:
                self.tol_slider.setValue(int(tol_value))
            except Exception:
                pass
        self._update_tol_label(self.tol_slider.value())
        prefix = settings.value("renamePrefix")
        if prefix is not None:
            self.prefix_edit.setText(prefix)
        main_pattern = settings.value("renameMain")
        if main_pattern is not None:
            self.main_edit.setText(main_pattern)
        suffix = settings.value("renameSuffix")
        if suffix is not None:
            self.suffix_edit.setText(suffix)

    def _save_settings(self):
        settings = self._settings
        settings.setValue("processMode", self.process_combo.currentText())
        settings.setValue("targetType", self.target_combo.currentText())
        settings.setValue("rewireTextures", self.rewire_cb.isChecked())
        settings.setValue("autoDeleteOld", self.auto_delete_cb.isChecked())
        settings.setValue("showDetails", self.show_details_cb.isChecked())
        settings.setValue("mergeTolerance", self.tol_slider.value())
        settings.setValue("renamePrefix", self.prefix_edit.text())
        settings.setValue("renameMain", self.main_edit.text())
        settings.setValue("renameSuffix", self.suffix_edit.text())
        settings.sync()

    # ---------------- utility ----------------

    def closeEvent(self, event):
        try:
            self._save_settings()
        finally:
            super().closeEvent(event)

    def _update_tol_label(self, value):
        self.tol_label.setText(f"Tol {value}%")

    def _append_log(self, text):
        self.log.append(text)

    def _clear_log(self):
        self.log.clear()
        self._last_preview = None

    def _set_preview_state(self, kind, data, summary=None):
        self._last_preview = {"type": kind, "data": data, "summary": summary}

    def _refresh_log_display(self):
        if not self._last_preview:
            return
        scrollbar = self.log.verticalScrollBar()
        previous_value = scrollbar.value()
        kind = self._last_preview["type"]
        data = self._last_preview["data"]
        self.log.clear()
        if kind == "list":
            self._log_material_list(data)
        elif kind == "convert":
            self._log_conversion_preview(data)
        elif kind == "merge":
            self._log_merge_preview(data)
        elif kind == "rename":
            self._log_rename_preview(data, preview_only=data.get("preview_only", True))
        summary = self._last_preview.get("summary")
        if summary:
            if isinstance(summary, str):
                summary = [summary]
            if summary:
                self._append_log("")
                for line in summary:
                    self._append_log(line)
        QtCore.QTimer.singleShot(0, lambda: scrollbar.setValue(min(previous_value, scrollbar.maximum())))

    def _current_process_key(self):
        index = self.process_combo.currentIndex()
        return PROCESS_OPTIONS[index][0]

    # ---------------- list materials ----------------

    def _list_materials(self):
        mats, mode_text = _gather_materials(self._current_process_key())
        data = {"materials": mats, "mode": mode_text}
        self.log.clear()
        self._log_material_list(data)
        self._set_preview_state("list", data)

    def _log_material_list(self, data):
        mats = data["materials"]
        mode = data["mode"]
        if not mats:
            self._append_log(f"No materials found ({mode}).")
            return
        self._append_log(f"<b>Materials ({mode}): {len(mats)}</b>")
        detailed = self.show_details_cb.isChecked()
        for mat in mats:
            node_type = cmds.nodeType(mat)
            self._append_log(f"• <b><font color='#00f7c8'>{mat}</font></b> ({node_type})")
            if detailed:
                attrs = _numeric_attrs(mat)
                for attr in attrs:
                    entry = _format_attr_entry(mat, attr)
                    if entry is not None:
                        self._append_log(f"    <font color='#bbbbbb'>{entry}</font>")
            self._append_log("<div style='height:2px;'></div>")

    # ---------------- conversion ----------------

    def _collect_conversion_preview(self):
        mats, mode_text = _gather_materials(self._current_process_key())
        target = self.target_combo.currentText()
        rewire = self.rewire_cb.isChecked()
        groups = collections.defaultdict(list)
        for mat in mats:
            groups[cmds.nodeType(mat)].append(mat)
        preview = {
            "mode": mode_text,
            "target": target,
            "rewire": rewire,
            "groups": [],
        }
        errors = {}
        for src_type, materials in sorted(groups.items()):
            entry = {"src_type": src_type, "materials": []}
            for mat in sorted(materials):
                if _is_referenced(mat):
                    errors[mat] = "Can't convert referenced materials"
                convertible, non_convertible = _analyze_material_inputs(mat, target)
                status = "green" if convertible and not non_convertible else \
                         "red" if not convertible and non_convertible else \
                         "yellow" if convertible and non_convertible else "cyan"
                entry["materials"].append({
                    "name": mat,
                    "old_name": f"{mat}_old",
                    "new_name": mat,
                    "convertible": convertible,
                    "non_convertible": non_convertible,
                    "status": status,
                })
            preview["groups"].append(entry)
        preview["count"] = len(mats)
        preview["errors"] = errors
        return preview

    def _log_conversion_preview(self, data, preview_only=True):
        self.log.clear()
        if data["count"] == 0:
            self._append_log(f"No materials found ({data['mode']}).")
            return
        
        action = "Preview: Converting" if preview_only else "Converting"
        header_color = "#ff8800" if preview_only else "#00ff00"  # Orange for preview, green for execution
        self._append_log(f"<b><font color='{header_color}'>{action} {data['mode']} to {data['target']}</font></b>")
        self._append_log("")
        detailed = self.show_details_cb.isChecked()
        errors = data.get("errors", {})
        conversion_results = data.get("conversion_results", {})  # Map of mat_name -> (old_name, new_name)
        
        for group in data["groups"]:
            src_type = group['src_type']
            target_type = data['target']
            self._append_log(f"<b><font color='#ffffff'>{src_type} → {target_type}</font></b>")
            for mat in group["materials"]:
                mat_name = mat["name"]
                # Use actual conversion results if available, otherwise use preview data
                if mat_name in conversion_results:
                    old_name, new_name = conversion_results[mat_name]
                else:
                    old_name = mat['old_name']
                    new_name = mat['new_name']
                
                if preview_only:
                    color = {
                        "green": "#00ff00",
                        "red": "#ff0000",
                        "yellow": "#ffff00",
                        "cyan": "#00f7c8",
                    }[mat["status"]]
                else:
                    # When executing, make the new name green
                    color = "#00ff00"
                self._append_log(f"  {old_name} → <b><font color='{color}'>{new_name}</font></b>")
                if mat_name in errors:
                    self._append_log(f"    <font color='#ff5555'>{errors[mat_name]}</font>")
                if detailed:
                    if mat["convertible"]:
                        self._append_log(f"    <font color='#00ff00'>✓ Converted inputs:</font>")
                        for item in mat["convertible"]:
                            self._append_log(f"      • {item}")
                    if mat["non_convertible"]:
                        self._append_log(f"    <font color='#ff6666'>✗ Lost inputs:</font>")
                        for item in mat["non_convertible"]:
                            self._append_log(f"      • {item}")
                    if not mat["convertible"] and not mat["non_convertible"]:
                        self._append_log(f"    <font color='#888888'>No texture/connection inputs</font>")
                else:
                    stats = []
                    if mat["convertible"]:
                        stats.append(f"<font color='#ffffff'>({len(mat['convertible'])} converted)</font>")
                    if mat["non_convertible"]:
                        stats.append(f"<font color='#ffffff'>({len(mat['non_convertible'])} lost)</font>")
                    if stats:
                        self._append_log("  " + " ".join(stats))
                self._append_log("<div style='height:4px;'></div>")
            self._append_log("")
        if preview_only:
            self._append_log("<i>Click 'Convert Materials' to perform the conversion.</i>")

    def _preview_conversion(self):
        preview = self._collect_conversion_preview()
        self._log_conversion_preview(preview, preview_only=True)
        self._set_preview_state("convert", preview)

    def _convert_materials(self):
        preview = self._collect_conversion_preview()
        if preview["count"] == 0:
            self._log_conversion_preview(preview)
            self._set_preview_state("convert", preview, None)
            return

        target = preview["target"]
        rewire = self.rewire_cb.isChecked()
        preview["rewire"] = rewire
        auto_delete = self.auto_delete_cb.isChecked()

        self._ops_pairs[:] = []
        deleted_materials = deleted_sgs = 0
        success_count = 0

        errors = {}
        conversion_results = {}  # Map of original mat_name -> (old_name, new_name)
        cmds.undoInfo(openChunk=True)
        try:
            for group in preview["groups"]:
                for mat_info in group["materials"]:
                    mat = mat_info["name"]
                    if _is_referenced(mat):
                        errors[mat] = "Can't convert referenced materials"
                        continue
                    try:
                        old_name, new_name, old_node, new_node = convert_material_preserve_name(mat, target, rewire_textures=rewire)
                        self._ops_pairs.append((old_name, new_name, old_node, new_node))
                        conversion_results[mat] = (old_name, new_name)
                        success_count += 1
                    except Exception as exc:
                        if _is_referenced(mat):
                            errors[mat] = "Can't convert referenced materials"
                        else:
                            errors[mat] = f"Failed: {exc}"
            if auto_delete:
                deleted_materials, deleted_sgs = self._delete_old_materials()
        finally:
            cmds.undoInfo(closeChunk=True)

        # Use the original preview data, not a new collection (which would show already-converted materials)
        preview["errors"] = errors
        preview["conversion_results"] = conversion_results
        self._log_conversion_preview(preview, preview_only=False)

        summary_lines = [f"{success_count} material(s) converted to {target}!"]
        if auto_delete:
            if deleted_sgs:
                summary_lines.append(f"Deleted {deleted_materials} old material(s) and {deleted_sgs} shading group(s).")
            elif deleted_materials:
                summary_lines.append(f"Deleted {deleted_materials} old material(s).")
            else:
                summary_lines.append("No *_old materials to delete.")
        summary_lines.append("Use Maya's undo to revert the entire conversion.")
        self._append_log("")
        for line in summary_lines:
            self._append_log(f"<font color='#00ff00'>{line}</font>")
        self._set_preview_state("convert", preview, summary_lines)
        self._save_settings()

    def _delete_old_materials(self):
        deleted_materials = 0
        deleted_sgs = 0
        for (old_name, _new_name, old_node, _new_node) in self._ops_pairs:
            if cmds.objExists(old_node):
                try:
                    sg_name = f"{old_node}_SG"
                    if cmds.objExists(sg_name):
                        try:
                            cmds.delete(sg_name)
                            deleted_sgs += 1
                        except Exception:
                            pass
                    for sg in _sgs_of_material(old_node):
                        try:
                            src = cmds.listConnections(sg + ".surfaceShader", s=True, d=False, p=True) or []
                            if src and src[0].split(".")[0] == old_node:
                                cmds.disconnectAttr(f"{old_node}.outColor", f"{sg}.surfaceShader")
                        except Exception:
                            pass
                    cmds.delete(old_node)
                    deleted_materials += 1
                except Exception:
                    pass
        return deleted_materials, deleted_sgs

    # ---------------- merge ----------------

    def _collect_merge_preview(self):
        mats, mode_text = _gather_materials(self._current_process_key())
        tol = self.tol_slider.value() / 100.0
        groups = []
        visited = set()
        for mat in mats:
            if mat in visited:
                continue
            node_type = cmds.nodeType(mat)
            attrs = _numeric_attrs(mat)
            duplicates = []
            details = {}
            for other in mats:
                if other == mat or other in visited:
                    continue
                if cmds.nodeType(other) != node_type:
                    continue
                within = True
                attr_details = []
                for attr in attrs:
                    vala = _attr_val(mat, attr)
                    valb = _attr_val(other, attr)
                    delta = _value_delta(vala, valb)
                    ok = _within_tol(vala, valb, tol)
                    attr_details.append({
                        "attr": attr,
                        "base": vala,
                        "value": valb,
                        "within": ok,
                        "delta": delta,
                    })
                    if not ok:
                        within = False
                if within:
                    duplicates.append(other)
                    details[other] = attr_details
                    visited.add(other)
            groups.append({
                "representative": mat,
                "type": node_type,
                "duplicates": duplicates,
                "attr_details": details,
                "attrs": attrs,
            })
            visited.add(mat)
        errors = {}
        for group in groups:
            rep = group["representative"]
            if _is_referenced(rep):
                errors[rep] = "Can't merge referenced materials"
            for dup in group["duplicates"]:
                if _is_referenced(dup):
                    errors[dup] = "Can't merge referenced materials"
        return {
            "mode": mode_text,
            "tolerance": tol,
            "groups": groups,
            "count": len(mats),
            "errors": errors,
        }

    def _log_merge_preview(self, data, preview_only=True):
        self.log.clear()
        tol_pct = int(data["tolerance"] * 100)
        if data["count"] == 0:
            self._append_log(f"No materials found ({data['mode']}).")
            return
        
        action = "Preview: Merge" if preview_only else "Merging"
        header_color = "#ff8800" if preview_only else "#00ff00"  # Orange for preview, green for execution
        self._append_log(f"<b><font color='{header_color}'>{action} {data['mode']} (tolerance {tol_pct}%)</font></b>")
        self._append_log("")
        detailed = self.show_details_cb.isChecked()
        errors = data.get("errors", {})
        for group in data["groups"]:
            rep = group["representative"]
            dupes = group["duplicates"]
            tag = f" ({len(dupes)} merging)" if dupes else " (no matches)"
            rep_color = "#00ff00" if not preview_only else "#00f7c8"  # Green when executing, cyan in preview
            self._append_log(f"<b><font color='{rep_color}'>{rep}</font></b> ({group['type']}){tag}")
            if rep in errors:
                self._append_log(f"    <font color='#ff5555'>{errors[rep]}</font>")
            if dupes:
                self._append_log("  <font color='#ffffff'>Merges:</font>")
                for dup in dupes:
                    self._append_log(f"    • {dup}")
                    if dup in errors:
                        self._append_log(f"      <font color='#ff5555'>{errors[dup]}</font>")
                if detailed:
                    materials = [rep] + dupes
                    for attr in MERGE_DETAIL_ATTRS:
                        values = [_normalized_attr_value(mat, attr) for mat in materials]
                        if all(v is None for v in values):
                            continue
                        formatted_values = []
                        for idx, val in enumerate(values):
                            value_html = _format_value_html(attr, val)
                            if idx == 0:
                                formatted_values.append(value_html)
                            else:
                                formatted_values.append(f"({value_html})")
                        self._append_log(f"    <font color='#bbbbbb'>{attr}:</font> {' '.join(formatted_values)}")
                self._append_log("")
            else:
                # Only show "No merge candidates" in preview, not after execution
                if preview_only:
                    self._append_log("  <font color='#888888'>No merge candidates</font>")
                    self._append_log("")
        if preview_only:
            self._append_log("<i>Click 'Merge Materials' to apply merges.</i>")

    def _preview_merge(self):
        preview = self._collect_merge_preview()
        self._log_merge_preview(preview, preview_only=True)
        self._set_preview_state("merge", preview)

    def _merge_materials(self):
        mats, mode_text = _gather_materials(self._current_process_key())
        if not mats:
            self.log.clear()
            self._append_log(f"No materials found ({mode_text}).")
            return

        preview = self._collect_merge_preview()
        if preview["count"] == 0:
            self._log_merge_preview(preview)
            self._set_preview_state("merge", preview, None)
            return

        merged_count = 0
        base_groups = 0
        errors = {}

        cmds.undoInfo(openChunk=True)
        try:
            for group in preview["groups"]:
                rep = group["representative"]
                dupes = group["duplicates"]
                if not dupes:
                    continue
                if _is_referenced(rep):
                    errors[rep] = "Can't merge referenced materials"
                    for dup in dupes:
                        if _is_referenced(dup):
                            errors[dup] = "Can't merge referenced materials"
                    continue
                base_groups += 1
                rep_sgs = cmds.listConnections(rep, type="shadingEngine") or []
                if rep_sgs:
                    rep_sg = rep_sgs[0]
                else:
                    rep_sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True,
                                       name=f"{rep}_SG")
                    cmds.connectAttr(f"{rep}.outColor", f"{rep_sg}.surfaceShader", f=True)
                for dup in dupes:
                    if _is_referenced(dup):
                        errors[dup] = "Can't merge referenced materials"
                        continue
                    for sg in cmds.listConnections(dup, type="shadingEngine") or []:
                        members = cmds.sets(sg, q=True) or []
                        if members:
                            cmds.sets(members, e=True, forceElement=rep_sg)
                        try:
                            cmds.delete(sg)
                        except Exception:
                            pass
                    try:
                        cmds.delete(dup)
                        merged_count += 1
                    except Exception:
                        if _is_referenced(dup):
                            errors[dup] = "Can't merge referenced materials"
                        else:
                            errors[dup] = f"Failed to merge into {rep}"
        finally:
            cmds.undoInfo(closeChunk=True)

        # Use the original preview data, not a new collection (which would show already-merged materials)
        preview["errors"] = errors
        self._log_merge_preview(preview, preview_only=False)

        summary_lines = []
        if merged_count:
            summary_lines.append(f"Merged {merged_count} material(s) into {base_groups} base material(s)!")
        else:
            summary_lines.append("No materials required merging.")
        summary_lines.append("Use Maya's undo to restore merged materials if needed.")
        self._append_log("")
        for line in summary_lines:
            self._append_log(f"<font color='#00ff00'>{line}</font>")
        self._set_preview_state("merge", preview, summary_lines)
        self._save_settings()

    # ---------------- rename ----------------

    def _collect_rename_plan(self):
        mats, mode_text = _gather_materials(self._current_process_key())
        prefix = self.prefix_edit.text()
        main = self.main_edit.text()
        suffix = self.suffix_edit.text()

        plan = []
        used_names = set()
        errors = {}
        for mat in mats:
            if _is_referenced(mat):
                errors[mat] = "Can't rename referenced materials"
            tokens = _token_map(mat)
            candidate = _build_name(prefix, main, suffix, tokens)
            final_name = _unique_rename(candidate, used_names)
            color_display = _format_color_display(mat, tokens.get("(color)"))
            shader_token = tokens.get("(shader)")
            shader_display = shader_token if shader_token is not None else ""
            plan.append({
                "material": mat,
                "candidate": candidate,
                "final": final_name,
                "color_display": color_display,
                "shader_display": shader_display,
            })
        return {
            "mode": mode_text,
            "plan": plan,
            "count": len(mats),
            "preview_only": True,
            "errors": errors,
        }

    def _log_rename_preview(self, data, preview_only=True):
        self.log.clear()
        if data["count"] == 0:
            self._append_log(f"No materials found ({data['mode']}).")
            return

        action = "Preview" if preview_only else "Renamed"
        header_color = "#ff8800" if preview_only else "#00ff00"  # Orange for preview, green for execution
        self._append_log(f"<b><font color='{header_color}'>{action}: {data['mode']}</font></b>")
        detailed = self.show_details_cb.isChecked()
        errors = data.get("errors", {})
        for entry in data["plan"]:
            mat = entry["material"]
            final_name = entry["final"]
            self._append_log(f"• {mat} → <b><font color='#00f7c8'>{final_name}</font></b>")
            if mat in errors:
                self._append_log(f"    <font color='#ff5555'>{errors[mat]}</font>")
            if detailed:
                color_display = entry.get("color_display")
                shader_display = entry.get("shader_display")
                if color_display:
                    self._append_log(f"    <font color='#bbbbbb'>(color) =</font> {color_display}")
                if shader_display:
                    self._append_log(f"    <font color='#bbbbbb'>(shader) =</font> {shader_display}")
            self._append_log("")
        if preview_only:
            self._append_log("Click 'Rename Materials' to apply the new names.")

    def _preview_rename(self):
        plan = self._collect_rename_plan()
        self._log_rename_preview(plan, preview_only=True)
        self._set_preview_state("rename", plan, None)

    def _rename_materials(self):
        plan = self._collect_rename_plan()
        if plan["count"] == 0:
            self.log.clear()
            self._append_log(f"No materials found ({plan['mode']}).")
            self._set_preview_state("rename", plan, None)
            return

        rename_count = 0
        errors = {}
        cmds.undoInfo(openChunk=True)
        try:
            for entry in plan["plan"]:
                old_name = entry["material"]
                final_name = entry["final"]
                if old_name == final_name:
                    continue
                if _is_referenced(old_name):
                    errors[old_name] = "Can't rename referenced materials"
                    continue
                try:
                    result_name = cmds.rename(old_name, final_name)
                    entry["final"] = result_name
                    rename_count += 1
                except RuntimeError as exc:
                    if _is_referenced(old_name):
                        errors[old_name] = "Can't rename referenced materials"
                    else:
                        errors[old_name] = f"Failed to rename: {exc}"
        finally:
            cmds.undoInfo(closeChunk=True)

        plan["preview_only"] = False
        plan["errors"] = errors
        self._log_rename_preview(plan, preview_only=False)
        summary_lines = [
            f"{rename_count} material(s) renamed!" if rename_count else "No materials required renaming.",
            "Use Maya's undo to restore original names if needed.",
        ]
        self._append_log("")
        for line in summary_lines:
            self._append_log(f"<font color='#00ff00'>{line}</font>")
        self._set_preview_state("rename", plan, summary_lines)
        self._save_settings()


# --------------------------------------------------------------------------------------
#                               ENTRY POINT
# --------------------------------------------------------------------------------------

def show():
    dlg = MaterialManagerDialog(parent=_maya_main_window())
    dlg.resize(380, 360)
    dlg.show()
    return dlg

