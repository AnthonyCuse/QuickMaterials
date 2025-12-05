"""
Material Converter
---------------------------------

Focused tool for converting materials while preserving names and connections.
"""

import collections
import math
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
    background-color: #666666;
    border: 1px solid #666666;
    border-radius: 6px;
    padding: 3px 6px;
    margin: 2px;
}

QPushButton:hover {
    background-color: #777777;
    border: 1px solid #777777;
}

QPushButton:pressed {
    background-color: #555555;
    border: 1px solid #555555;
}

QPushButton:disabled {
    color: #bbbbbb;
    background-color: #555555;
    border: 1px solid #666666;
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
        connected_node = connections[0]
        return f"{attr}: {connected_node}"
    val = _attr_val(node, attr)
    if val is None:
        return None
    return f"{attr}: {val}"


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
        self.setWindowTitle("Material Converter")
        self.setMinimumWidth(500)
        self.setMinimumHeight(635)

        self._ops_pairs = []
        self._last_preview = None

        self._build_ui()
        self._connect_signals()

        self.setStyleSheet(FALLBACK_STYLESHEET)
        self._settings = QtCore.QSettings("QuickMaterials", "MaterialManager")
        self._load_settings()

    # ---------------- UI ----------------

    def _build_ui(self):
        title = QtWidgets.QLabel("Material Converter")
        title.setAlignment(QtCore.Qt.AlignCenter)
        title.setStyleSheet("font-weight:600; font-size:16px; padding:2px;")

        def make_separator(height=1):
            line = QtWidgets.QFrame()
            line.setFrameShape(QtWidgets.QFrame.HLine)
            line.setFrameShadow(QtWidgets.QFrame.Plain)
            line.setLineWidth(1)
            line.setFixedHeight(height)
            line.setStyleSheet("background-color:#333333; border:none;")
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
        self.preview_convert_btn.setStyleSheet("font-style: italic; color: #f0f0f0;")
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

        self.log = QtWidgets.QTextEdit()
        self.log.setReadOnly(True)
        self.log.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

        foot = QtWidgets.QHBoxLayout()
        foot.setContentsMargins(0, 0, 0, 0)
        foot.setSpacing(4)
        self.clear_log_btn = QtWidgets.QPushButton("Clear Log")
        self.clear_log_btn.setToolTip("Clear the log output.")
        self.close_btn = QtWidgets.QPushButton("Close")
        self.close_btn.setToolTip("Close the Material Converter.")
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
            "QFrame#convertFrame {"
            " background-color:#3a3a3a;"
            " border: 3px solid #444444;"
            " border-radius: 10px;"
            " padding: 5px;"
            " margin: 3px;"
            " color: #ffffff;"
            "}"
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

        buttons_container = QtWidgets.QWidget()
        buttons_container.setLayout(foot)
        buttons_container.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

        left_panel = QtWidgets.QVBoxLayout()
        left_panel.setContentsMargins(0, 0, 0, 0)
        left_panel.setSpacing(10)
        left_panel.addLayout(left_panel_top)
        left_panel.addWidget(convert_frame)
        left_panel.addLayout(details_row)
        left_panel.addWidget(self.log, 1)
        left_panel.addWidget(buttons_container)

        left_panel_container = QtWidgets.QWidget()
        left_container_layout = QtWidgets.QVBoxLayout(left_panel_container)
        left_container_layout.setContentsMargins(0, 0, 0, 0)
        left_container_layout.setSpacing(6)
        left_container_layout.addLayout(left_panel)
        left_panel_container.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding)
        left_panel_container.setMinimumWidth(320)

        main_layout = QtWidgets.QVBoxLayout()
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(8)
        main_layout.addWidget(left_panel_container)

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
        self.clear_log_btn.clicked.connect(self._clear_log)
        self.close_btn.clicked.connect(self.close)
        self.show_details_cb.toggled.connect(self._refresh_log_display)

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

    def _save_settings(self):
        settings = self._settings
        settings.setValue("processMode", self.process_combo.currentText())
        settings.setValue("targetType", self.target_combo.currentText())
        settings.setValue("rewireTextures", self.rewire_cb.isChecked())
        settings.setValue("autoDeleteOld", self.auto_delete_cb.isChecked())
        settings.setValue("showDetails", self.show_details_cb.isChecked())
        settings.sync()

    # ---------------- utility ----------------

    def closeEvent(self, event):
        try:
            self._save_settings()
        finally:
            super().closeEvent(event)

    def _append_log(self, text):
        self.log.append(text)

    def _clear_log(self):
        self.log.clear()
        self._last_preview = None

    def _set_preview_state(self, kind, data, summary=None, preview_only=None):
        # Store preview_only state if provided, otherwise infer from data
        if preview_only is None:
            preview_only = data.get("preview_only", True)
        self._last_preview = {"type": kind, "data": data, "summary": summary, "preview_only": preview_only}

    def _refresh_log_display(self):
        if not self._last_preview:
            return
        scrollbar = self.log.verticalScrollBar()
        previous_value = scrollbar.value()
        kind = self._last_preview["type"]
        data = self._last_preview["data"]
        preview_only = self._last_preview.get("preview_only", True)
        self.log.clear()
        if kind == "list":
            self._log_material_list(data)
        elif kind == "convert":
            self._log_conversion_preview(data, preview_only=preview_only)
        summary = self._last_preview.get("summary")
        if summary:
            if isinstance(summary, str):
                summary = [summary]
            if summary:
                self._append_log("<div style='height:2px;'></div>")
                for line in summary:
                    # Check if it's an undo message (should be white and italic)
                    if "Use Maya's undo" in line:
                        self._append_log(f"<i><font color='#ffffff'>{line}</font></i>")
                    else:
                        self._append_log(f"<font color='#00ff00'>{line}</font>")
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
        self._append_log("<div style='height:2px;'></div>")
        for line in summary_lines:
            if "Use Maya's undo" in line:
                self._append_log(f"<i><font color='#ffffff'>{line}</font></i>")
            else:
                self._append_log(f"<font color='#00ff00'>{line}</font>")
        self._set_preview_state("convert", preview, summary_lines, preview_only=False)
        self._save_settings()
        
        # Refresh Quick Materials UI to update shader type display
        try:
            import quick_materials
            if quick_materials.quick_materials_ui_instance:
                quick_materials.quick_materials_ui_instance.populate_materials_scroll_area()
        except Exception:
            pass

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


# --------------------------------------------------------------------------------------
#                               ENTRY POINT
# --------------------------------------------------------------------------------------

def show():
    dlg = MaterialManagerDialog(parent=_maya_main_window())
    dlg.resize(380, 360)
    dlg.show()
    return dlg

