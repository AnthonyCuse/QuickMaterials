# -*- coding: utf-8 -*-
"""
Material Converter (name-preserving, texture rewire, conversion log w/ optional auto-delete)

- Converts between: lambert, blinn, phong, standardSurface, aiStandardSurface, surfaceShader
- Renames source -> <name>_old, creates new target with the *original* name
- Rewires SGs to the new material
- Reconnects incoming textures/utility nodes to mapped attributes where possible
- Copies values for mapped attributes; unmapped attributes reset to defaults
- Shows a conversion log; supports optional auto-deletion of *_old materials and Maya's built-in undo
- Handles normal textures, opacity/transparency with reverse nodes, and proper emission/incandescence conversion

Drop anywhere on PYTHONPATH (e.g., your QuickMaterials folder) and run:
    import material_converter as mc
    mc.show()

Tested in Maya 2024/2025.
"""

import maya.cmds as cmds
import traceback

# --- Qt compatibility for Maya 2024 (PySide2) & Maya 2025 (PySide6) ---
try:
    # Maya 2025+
    from PySide6 import QtCore, QtWidgets, QtGui, QtUiTools
    from shiboken6 import wrapInstance
    PYSIDE6 = True
except Exception:
    from PySide2 import QtCore, QtWidgets, QtGui, QtUiTools
    from shiboken2 import wrapInstance
    PYSIDE6 = False

import maya.OpenMayaUI as omui


# --------------------------------------------------------------------------------------
#                   ATTRIBUTE MAPPING (pairwise + via standardSurface)
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
        "outColor",  # its primary display input
        "outMatteOpacity"
    ],
}

# --- math helpers for highlight width ↔ roughness ---
import math
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
        r,g,b = rgb
        return max(0.0, min(1.0, float((r+g+b)/3.0)))
    except Exception:
        return 0.0


# --------------------------------------------------------------------------------------
#                               CORE UTILITIES
# --------------------------------------------------------------------------------------

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
        else:
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
            # best effort: ignore unsupported types
            pass
    except Exception:
        pass

def _incoming_src(plug):
    """Return (srcNode, srcPlug) that drives the given plug (if any), else (None, None)."""
    try:
        conn = cmds.listConnections(plug, s=True, d=False, p=True) or []
        if not conn:
            return (None, None)
        src_full = conn[0]  # e.g., file1.outColor
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
        
        # Try fallback connections for common attribute type mismatches
        if src_attr == "outColor" and dst_attr in ["transmission", "opacity", "specular", "metalness"]:
            # Color to float - try outAlpha
            print(f"[DEBUG] _connect_if_possible: Trying fallback outAlpha for float attribute")
            try:
                cmds.connectAttr(f"{src_node}.outAlpha", f"{dst_node}.{dst_attr}", f=True)
                print(f"[DEBUG] _connect_if_possible: Fallback connection successful!")
                return True
            except Exception as e2:
                print(f"[DEBUG] _connect_if_possible: Fallback connection failed: {e2}")
        elif src_attr == "outAlpha" and dst_attr in ["transparency", "baseColor", "specularColor", "emissionColor"]:
            # Float to color - try outColor
            print(f"[DEBUG] _connect_if_possible: Trying fallback outColor for color attribute")
            try:
                cmds.connectAttr(f"{src_node}.outColor", f"{dst_node}.{dst_attr}", f=True)
                print(f"[DEBUG] _connect_if_possible: Fallback connection successful!")
                return True
            except Exception as e2:
                print(f"[DEBUG] _connect_if_possible: Fallback connection failed: {e2}")
        
        return False

def _safe_shading_node(node_type, name_hint):
    
    base = name_hint
    # let Maya make unique if needed
    return cmds.shadingNode(node_type, asShader=True, name=base)

def _sgs_of_material(material):
    return cmds.listConnections(material, type="shadingEngine") or []

def _has_reverse_node(plug):
    """Check if a plug is connected through a reverse node and return reverse node info."""
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
    """Check if a plug is connected to a normal/bump texture and return info."""
    try:
        conn = cmds.listConnections(plug, s=True, d=False, p=True) or []
        if not conn:
            return None
        src_full = conn[0]
        node, attr = src_full.split(".", 1)
        node_type = cmds.nodeType(node)
        
        # Check for normal/bump textures
        if node_type in ["file", "aiImage"]:
            # Check if it's connected to a normal/bump node
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
            # Direct normal/bump node connection
            return {
                "texture_node": None,
                "normal_node": node,
                "normal_type": node_type
            }
        return None
    except Exception:
        return None


# --------------------------------------------------------------------------------------
#                        CONVERSION IMPLEMENTATION
# --------------------------------------------------------------------------------------

def _legacy_to_standard(src):
    """Convert lambert/blinn/phong/surfaceShader -> standardSurface (new node name based on src)."""
    src_type = cmds.nodeType(src)
    dst = _safe_shading_node("standardSurface", f"{src}_standardSurface_TMP")
    
    # baseColor
    col = _get_val(src, "color", (0,0,0))
    if col: _set_val(dst, "baseColor", col)
    
    # base
    diff = _get_val(src, "diffuse", 0.8) if src_type in ("lambert","blinn","phong") else 1.0
    _set_val(dst, "base", diff)
    
    # emissionColor + emission weight (only if incandescence has no incoming connections)
    inc_plug = f"{src}.incandescence"
    inc_connections = cmds.listConnections(inc_plug, s=True, d=False, p=True) or []
    
    if not inc_connections:
        # No incoming connections - set values directly
        inc = _get_val(src, "incandescence", (0,0,0))
        if inc and _avg_rgb(inc) > 0.001:
            # Calculate emission weight based on incandescence intensity
            # Use the maximum RGB component as the emission weight
            emission_weight = max(inc[0], inc[1], inc[2])
            
            # Normalize the incandescence color by dividing by the weight
            # This prevents double-boosting when the weight is applied
            if emission_weight > 0.001:
                normalized_color = tuple(c / emission_weight for c in inc)
                _set_val(dst, "emissionColor", normalized_color)
                _set_val(dst, "emission", emission_weight)
                print(f"[DEBUG] Set emissionColor: {normalized_color}, emission weight: {emission_weight}")
            else:
                # Very low intensity - treat as no emission
                _set_val(dst, "emissionColor", (0,0,0))
                _set_val(dst, "emission", 0.0)
                print(f"[DEBUG] Set emission to default - very low incandescence intensity")
        else:
            # No incandescence - ensure emission is at default
            _set_val(dst, "emissionColor", (0,0,0))
            _set_val(dst, "emission", 0.0)
            print(f"[DEBUG] Set emission to default (0,0,0) - no incandescence")
    else:
        # Has incoming connections - will be handled in rewire phase
        # But also set emission weight to 1.0 to enable emission
        _set_val(dst, "emission", 1.0)
        print(f"[DEBUG] Emission has incoming connections - will handle in rewire phase")
        print(f"[DEBUG] Set emission weight to 1.0 to enable emission")

    # spec color + weight & roughness
    if src_type in ("lambert","blinn","phong"):
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

    # transparency -> transmission (only if no incoming connections)
    tr_plug = f"{src}.transparency"
    tr_connections = cmds.listConnections(tr_plug, s=True, d=False, p=True) or []
    
    if not tr_connections:
        # No incoming connections - set value directly
        tr = _get_val(src, "transparency", None)
        if tr:
            # Convert transparency to transmission (direct mapping)
            _set_val(dst, "transmission", _avg_rgb(tr))
            _set_val(dst, "transmissionColor", (1,1,1))
            print(f"[DEBUG] Converted transparency to transmission: {tr} -> {_avg_rgb(tr)}")
    else:
        # Has incoming connections - will be handled in rewire phase
        print(f"[DEBUG] Transparency has incoming connections - will handle in rewire phase")

    # surfaceShader case: drive baseColor from outColor if connected/value exists
    if src_type == "surfaceShader":
        outc = _get_val(src, "outColor", None)
        if outc: _set_val(dst, "baseColor", outc)
        # opacity-ish mapping:
        matte = _get_val(src, "outMatteOpacity", None)
        if matte and isinstance(matte, (list, tuple)):
            # 1=opaque in stdSurface, assume outMatteOpacity similar
            _set_val(dst, "opacity", matte)

    return dst


def _standard_to_legacy(src_std, target_type):
    """Convert standardSurface -> lambert/blinn/phong/surfaceShader."""
    dst = _safe_shading_node(target_type, f"{src_std}_{target_type}_TMP")

    # Common
    for std_attr, (legacy_attr, _) in STD_TO_LEGACY_COMMON.items():
        if legacy_attr and _exists_attr(dst, legacy_attr) and _exists_attr(src_std, std_attr):
            _set_val(dst, legacy_attr, _get_val(src_std, std_attr))

    # Spec/roughness per target
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

    # Lambert has no specular: force matte
    if target_type == "lambert":
        if _exists_attr(dst, "diffuse") and _get_val(dst, "diffuse", None) is None:
            _set_val(dst, "diffuse", 0.8)
        if _exists_attr(dst, "reflectivity"):
            _set_val(dst, "reflectivity", 0.0)

    # Handle emission -> incandescence conversion properly
    # Check if emission attributes have incoming connections (textures)
    emission_color_connected = _incoming_src(f"{src_std}.emissionColor")[0] is not None
    emission_weight_connected = _incoming_src(f"{src_std}.emission")[0] is not None
    
    if emission_color_connected or emission_weight_connected:
        # If there are incoming connections, we'll handle them in the rewire phase
        # Don't set any default values here - let the texture connections handle it
        print(f"[DEBUG] Emission has incoming connections - will handle in rewire phase")
    else:
        # No incoming connections - use the actual values
        emission_color = _get_val(src_std, "emissionColor", (0,0,0))
        emission_weight = _get_val(src_std, "emission", 0.0)
        
        # Only set incandescence if there's meaningful emission
        if emission_color and emission_weight > 0.001:
            # Scale emission color by emission weight
            scaled_emission = tuple(min(1.0, c * emission_weight) for c in emission_color)
            if _exists_attr(dst, "incandescence"):
                _set_val(dst, "incandescence", scaled_emission)
                print(f"[DEBUG] Set incandescence from emission: {scaled_emission}")
        else:
            # No meaningful emission - ensure incandescence is at default (0,0,0)
            if _exists_attr(dst, "incandescence"):
                _set_val(dst, "incandescence", (0,0,0))
                print(f"[DEBUG] Set incandescence to default (0,0,0) - no emission")

    # Transparency from opacity + transmission
    opac = _get_val(src_std, "opacity", None)
    trans_w = float(_get_val(src_std, "transmission", 0.0) or 0.0)
    if opac:
        inv = tuple(max(0.0, min(1.0, 1.0 - c)) for c in opac)
        inv_boosted = tuple(max(c, trans_w) for c in inv)
        if _exists_attr(dst, "transparency"):
            _set_val(dst, "transparency", inv_boosted)

    # surfaceShader case: just feed outColor
    if target_type == "surfaceShader":
        # approximate beauty via baseColor/emission
        col = _get_val(src_std, "baseColor", (0,0,0))
        emis = _get_val(src_std, "emissionColor", (0,0,0))
        mix = (min(1.0, col[0]+emis[0]), min(1.0, col[1]+emis[1]), min(1.0, col[2]+emis[2]))
        if _exists_attr(dst, "outColor"):
            _set_val(dst, "outColor", mix)
        if _exists_attr(dst, "outMatteOpacity"):
            op = _get_val(src_std, "opacity", (1,1,1))
            _set_val(dst, "outMatteOpacity", op)

    return dst


def _aiStandardSurface_to_standardSurface(src_ai):
    """Convert aiStandardSurface -> standardSurface (retain all connections and values)."""
    dst = _safe_shading_node("standardSurface", f"{src_ai}_standardSurface_TMP")
    
    # Copy all common attributes
    ai_attrs = TYPE_ATTRS.get("aiStandardSurface", [])
    std_attrs = TYPE_ATTRS.get("standardSurface", [])
    
    for attr in ai_attrs:
        if attr in std_attrs and _exists_attr(src_ai, attr) and _exists_attr(dst, attr):
            _set_val(dst, attr, _get_val(src_ai, attr))
    
    return dst


def _standardSurface_to_aiStandardSurface(src_std):
    """Convert standardSurface -> aiStandardSurface (retain all connections and values)."""
    dst = _safe_shading_node("aiStandardSurface", f"{src_std}_aiStandardSurface_TMP")
    
    # Copy all common attributes
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
    """
    Produce a lightweight mapping dict {srcAttr: dstAttr or special-tag}
    used both for value-copy and connection rewiring.

    For legacy<->legacy we go through standardSurface logic (special tags handled inline).
    """
    mapping = {}

    if dst_type == "standardSurface":
        # legacy -> std
        for a, (std, _t) in LEGACY_TO_STD.items():
            if std:
                mapping[a] = std
        mapping["__SPECIAL__legacy_to_std"] = True
        return mapping

    if src_type == "standardSurface":
        # std -> legacy
        for std, (leg, _t) in STD_TO_LEGACY_COMMON.items():
            if leg:
                mapping[std] = leg
        # per-target extras
        tmap = STD_TO_LEGACY_TARGET.get(dst_type, {})
        for std, (leg, _t) in tmap.items():
            if leg:
                mapping[std] = leg
        mapping["__SPECIAL__std_to_legacy"] = True
        mapping["__TARGET__"] = dst_type
        return mapping

    # aiStandardSurface <-> standardSurface (direct mapping)
    if src_type == "aiStandardSurface" and dst_type == "standardSurface":
        mapping["__AI_TO_STD__"] = True
        return mapping
    
    if src_type == "standardSurface" and dst_type == "aiStandardSurface":
        mapping["__STD_TO_AI__"] = True
        return mapping

    # legacy -> legacy (via std). We'll still try a direct-ish rewire using bridge keys:
    mapping["__VIA_STD__"] = True
    return mapping


def _rewire_inputs(src_mat, dst_mat, mapping_hint):
    """
    For every attribute on the *source* that has an incoming connection,
    try to connect that same upstream to an equivalent attribute on the *dest*
    based on mapping_hint (or name-similar fallback).
    """
    src_type = cmds.nodeType(src_mat)
    dst_type = cmds.nodeType(dst_mat)

    # Candidate attributes to inspect for incoming connections:
    src_attrs = TYPE_ATTRS.get(src_type, cmds.listAttr(src_mat, k=True) or [])
    dst_attrs = set(TYPE_ATTRS.get(dst_type, cmds.listAttr(dst_mat, k=True) or []))
    print(f"[DEBUG] Source type: {src_type}, attributes: {src_attrs}")
    print(f"[DEBUG] Destination type: {dst_type}, attributes: {list(dst_attrs)}")

    def best_dst_for(src_attr):
        # 1) mapping hint
        if mapping_hint:
            if mapping_hint.get("__VIA_STD__"):
                # We can't know exact pairwise map; try common-sense guesses
                bridge = {
                    "color": "color",
                    "diffuse": "diffuse",
                    "incandescence": "incandescence",
                    "transparency": "transparency",
                    "specularColor": "specularColor",
                    "reflectivity": "reflectivity",
                    "eccentricity": "specularRoughness",  # rough proxy
                    "cosinePower": "specularRoughness",   # rough proxy
                }
                guess = bridge.get(src_attr)
                if guess and guess in dst_attrs:
                    return guess
            elif mapping_hint.get("__SPECIAL__legacy_to_std"):
                guess = LEGACY_TO_STD.get(src_attr, (None, None))[0]
                if guess and guess in dst_attrs:
                    return guess
            elif mapping_hint.get("__SPECIAL__std_to_legacy"):
                # reverse table lookup
                for std_attr, leg in list(STD_TO_LEGACY_COMMON.items()) + list(STD_TO_LEGACY_TARGET.get(mapping_hint.get("__TARGET__",""), {}).items()):
                    leg_attr = leg[0]
                    if std_attr == src_attr and leg_attr in dst_attrs:
                        return leg_attr
            elif mapping_hint.get("__AI_TO_STD__") or mapping_hint.get("__STD_TO_AI__"):
                # Direct attribute mapping for aiStandardSurface <-> standardSurface
                if src_attr in dst_attrs:
                    return src_attr

        # 2) Same-name fallback
        if src_attr in dst_attrs:
            return src_attr

        # 3) Heuristics (common aliases)
        heur = {
            "baseColor": "color",
            "emissionColor": "incandescence",
            "transparency": "transmission",  # Legacy transparency -> standardSurface transmission
            "transmission": "transparency",  # StandardSurface transmission -> legacy transparency
            "transmissionColor": "transparency",  # StandardSurface transmissionColor -> legacy transparency
        }
        if src_attr in heur and heur[src_attr] in dst_attrs:
            print(f"[DEBUG] Heuristic mapping: {src_attr} -> {heur[src_attr]}")
            return heur[src_attr]

        print(f"[DEBUG] No mapping found for {src_attr} in {dst_attrs}")
        return None

    for a in src_attrs:
        plug = f"{src_mat}.{a}"
        src_node, src_out = _incoming_src(plug)
        if not src_node:
            print(f"[DEBUG] No incoming connection for {plug}")
            continue
        print(f"[DEBUG] Processing attribute: {a} (incoming from {src_node}.{src_out})")
        dst_attr = best_dst_for(a)
        if not dst_attr:
            print(f"[DEBUG] No destination attribute found for {a}")
            continue
        
        print(f"[DEBUG] Rewiring connection: {src_node}.{src_out} -> {dst_mat}.{dst_attr}")
        
        # Special handling for legacy transparency -> standardSurface transmission
        if a == "transparency" and dst_attr == "transmission":
            print(f"[DEBUG] Detected legacy transparency -> standardSurface transmission")
            # Use outAlpha for transmission (float attribute) - fallback will handle if outColor fails
            _connect_if_possible(src_node, "outAlpha", dst_mat, dst_attr)
        # Special handling for standardSurface transmission -> legacy transparency
        elif a == "transmission" and dst_attr == "transparency":
            print(f"[DEBUG] Detected standardSurface transmission -> legacy transparency")
            # Use outColor for transparency (color attribute) - fallback will handle if outAlpha fails
            _connect_if_possible(src_node, "outColor", dst_mat, dst_attr)
        # Special handling for standardSurface transmissionColor -> legacy transparency
        elif a == "transmissionColor" and dst_attr == "transparency":
            print(f"[DEBUG] Detected standardSurface transmissionColor -> legacy transparency")
            # Direct connection for transmissionColor -> transparency (both are color)
            _connect_if_possible(src_node, src_out, dst_mat, dst_attr)
        # Special handling for emission -> incandescence
        elif a == "emissionColor" and dst_attr == "incandescence":
            print(f"[DEBUG] Detected emission->incandescence connection: {a} -> {dst_attr}")
            _connect_if_possible(src_node, src_out, dst_mat, dst_attr)
        elif a == "emission" and dst_attr == "incandescence":
            print(f"[DEBUG] Detected emission weight->incandescence connection: {a} -> {dst_attr}")
            # For emission weight, we need to multiply the texture by the weight
            # This is complex, so for now just connect directly and let user adjust
            _connect_if_possible(src_node, src_out, dst_mat, dst_attr)
        else:
            # Direct connection for all other cases
            _connect_if_possible(src_node, src_out, dst_mat, dst_attr)
        
        # Handle normal textures
        if a == "normalCamera" and dst_attr == "normalCamera":
            _handle_normal_texture_rewire(src_mat, dst_mat, a, dst_attr, src_node, src_out)


def _handle_legacy_transparency_rewire(src_mat, dst_mat, src_attr, dst_attr, src_node, src_out, transparency_to_opacity=True):
    """Handle legacy transparency texture connections to standardSurface opacity/transmission."""
    try:
        print(f"[DEBUG] Legacy Transparency Rewire: {src_mat}.{src_attr} -> {dst_mat}.{dst_attr}")
        
        if transparency_to_opacity and dst_attr == "opacity":
            # Legacy transparency -> standardSurface opacity (needs reverse node)
            print(f"[DEBUG] Adding reverse node for transparency->opacity conversion")
            reverse_node = cmds.shadingNode("reverse", asUtility=True, name=f"{dst_mat}_reverse")
            _connect_if_possible(src_node, src_out, reverse_node, "input")
            _connect_if_possible(reverse_node, "output", dst_mat, dst_attr)
            print(f"[DEBUG] Created reverse node: {reverse_node}")
            
        elif not transparency_to_opacity and dst_attr == "transmission":
            # Legacy transparency -> standardSurface transmission (direct connection)
            print(f"[DEBUG] Direct connection for transparency->transmission")
            _connect_if_possible(src_node, src_out, dst_mat, dst_attr)
            
        else:
            # Fallback - direct connection
            print(f"[DEBUG] Fallback direct connection")
            _connect_if_possible(src_node, src_out, dst_mat, dst_attr)
            
        print(f"[DEBUG] Legacy transparency rewire completed")
        
    except Exception as e:
        print(f"[DEBUG] Error in legacy transparency rewire: {e}")
        # Fallback to direct connection
        _connect_if_possible(src_node, src_out, dst_mat, dst_attr)


def _handle_standard_opacity_to_legacy_transparency_rewire(src_mat, dst_mat, src_attr, dst_attr, src_node, src_out, transparency_to_opacity=True):
    """Handle standardSurface opacity texture connections to legacy transparency."""
    try:
        print(f"[DEBUG] Standard Opacity -> Legacy Transparency Rewire: {src_mat}.{src_attr} -> {dst_mat}.{dst_attr}")
        
        if transparency_to_opacity:
            # standardSurface opacity -> legacy transparency (needs reverse node)
            print(f"[DEBUG] Adding reverse node for opacity->transparency conversion")
            reverse_node = cmds.shadingNode("reverse", asUtility=True, name=f"{dst_mat}_reverse")
            _connect_if_possible(src_node, src_out, reverse_node, "input")
            _connect_if_possible(reverse_node, "output", dst_mat, dst_attr)
            print(f"[DEBUG] Created reverse node: {reverse_node}")
        else:
            # If transparency_to_opacity is False, we shouldn't be converting opacity to transparency
            # This case shouldn't happen, but handle it gracefully
            print(f"[DEBUG] Warning: opacity->transparency conversion with transparency_to_opacity=False")
            _connect_if_possible(src_node, src_out, dst_mat, dst_attr)
            
        print(f"[DEBUG] Standard opacity -> legacy transparency rewire completed")
        
    except Exception as e:
        print(f"[DEBUG] Error in standard opacity -> legacy transparency rewire: {e}")
        # Fallback to direct connection
        _connect_if_possible(src_node, src_out, dst_mat, dst_attr)


def _handle_opacity_transparency_rewire(src_mat, dst_mat, src_attr, dst_attr, src_node, src_out, transparency_to_opacity=True):
    """Handle opacity/transparency conversion with reverse node logic and debug logging."""
    try:
        print(f"[DEBUG] Opacity/Transparency Rewire: {src_mat}.{src_attr} -> {dst_mat}.{dst_attr}")
        
        # Check if source has reverse node
        reverse_info = _has_reverse_node(f"{src_mat}.{src_attr}")
        src_has_reverse = reverse_info is not None
        
        if src_has_reverse:
            print(f"[DEBUG] Source has reverse node: {reverse_info['reverse_node']}")
        
        # Determine if we need reverse logic based on attribute types and user setting
        # For legacy -> standardSurface: if transparency_to_opacity is True, convert transparency to opacity
        # For standardSurface -> legacy: if transparency_to_opacity is True, convert opacity to transparency
        needs_reverse = False
        if transparency_to_opacity:
            # User wants transparency to convert to opacity
            if src_attr == "transparency" and dst_attr == "opacity":
                needs_reverse = True  # transparency -> opacity needs reverse
            elif src_attr == "opacity" and dst_attr == "transparency":
                needs_reverse = True  # opacity -> transparency needs reverse
        else:
            # User wants transparency to convert to transmission (no reverse needed)
            # Direct connections for same attribute types
            pass
        
        print(f"[DEBUG] Needs reverse: {needs_reverse} (transparency_to_opacity: {transparency_to_opacity})")
        
        if needs_reverse and not src_has_reverse:
            # Need to add reverse node for opacity->transparency or transparency->opacity
            print(f"[DEBUG] Adding reverse node for {src_attr}->{dst_attr} conversion")
            reverse_node = cmds.shadingNode("reverse", asUtility=True, name=f"{dst_mat}_reverse")
            _connect_if_possible(src_node, src_out, reverse_node, "input")
            _connect_if_possible(reverse_node, "output", dst_mat, dst_attr)
            print(f"[DEBUG] Created reverse node: {reverse_node}")
            
        elif not needs_reverse and src_has_reverse:
            # Need to remove reverse node - connect directly (opacity->opacity or transparency->transparency)
            print(f"[DEBUG] Removing reverse node for {src_attr}->{dst_attr} conversion")
            # Connect the input of the reverse node directly to destination
            reverse_input_node, reverse_input_attr = _incoming_src(f"{reverse_info['reverse_node']}.input")
            if reverse_input_node and reverse_input_attr:
                _connect_if_possible(reverse_input_node, reverse_input_attr, dst_mat, dst_attr)
                print(f"[DEBUG] Connected {reverse_input_node}.{reverse_input_attr} -> {dst_mat}.{dst_attr}")
            else:
                # Fallback to direct connection
                _connect_if_possible(src_node, src_out, dst_mat, dst_attr)
                
        else:
            # No change needed - same attribute type (opacity->opacity or transparency->transparency)
            print(f"[DEBUG] Direct connection for {src_attr}->{dst_attr}")
            _connect_if_possible(src_node, src_out, dst_mat, dst_attr)
            
        print(f"[DEBUG] Opacity/Transparency rewire completed")
        
    except Exception as e:
        print(f"[DEBUG] Error in opacity/transparency rewire: {e}")
        # Fallback to direct connection
        _connect_if_possible(src_node, src_out, dst_mat, dst_attr)


def _handle_normal_texture_rewire(src_mat, dst_mat, src_attr, dst_attr, src_node, src_out):
    """Handle normal/bump texture reconnection with appropriate normal nodes."""
    try:
        normal_info = _get_normal_texture_info(f"{src_mat}.{src_attr}")
        if not normal_info:
            return
        
        src_type = cmds.nodeType(src_mat)
        dst_type = cmds.nodeType(dst_mat)
        
        # Determine appropriate normal node type for destination
        if dst_type in ["standardSurface", "aiStandardSurface"]:
            target_normal_type = "aiNormalMap"
        else:
            target_normal_type = "normalMap"
        
        # Handle different source normal node types
        if normal_info["normal_type"] == "bump2d":
            # Convert bump2d to appropriate normal node
            new_normal = cmds.shadingNode(target_normal_type, asUtility=True, 
                                        name=f"{dst_mat}_{target_normal_type}")
            
            # Connect texture to new normal node
            if normal_info["texture_node"]:
                _connect_if_possible(normal_info["texture_node"], "outColor", 
                                   new_normal, "input")
            else:
                # Connect bump2d input to new normal node
                bump_input_node, bump_input_attr = _incoming_src(f"{normal_info['normal_node']}.bumpValue")
                if bump_input_node and bump_input_attr:
                    _connect_if_possible(bump_input_node, bump_input_attr, 
                                       new_normal, "input")
            
            # Connect new normal node to destination
            _connect_if_possible(new_normal, "outNormal", dst_mat, dst_attr)
            
        elif normal_info["normal_type"] == target_normal_type:
            # Same node type - connect directly
            if normal_info["normal_type"] == "aiNormalMap":
                _connect_if_possible(normal_info["normal_node"], "outNormal", dst_mat, dst_attr)
            else:  # normalMap
                _connect_if_possible(normal_info["normal_node"], "outNormal", dst_mat, dst_attr)
        else:
            # Different node types - create new normal node
            new_normal = cmds.shadingNode(target_normal_type, asUtility=True, 
                                        name=f"{dst_mat}_{target_normal_type}")
            
            # Connect texture to new normal node
            if normal_info["texture_node"]:
                _connect_if_possible(normal_info["texture_node"], "outColor", 
                                   new_normal, "input")
            else:
                # Direct normal node connection
                _connect_if_possible(normal_info["normal_node"], "input", 
                                   new_normal, "input")
            
            # Connect new normal node to destination
            _connect_if_possible(new_normal, "outNormal", dst_mat, dst_attr)
            
    except Exception as e:
        print(f"[DEBUG] Error in normal texture rewire: {e}")
        # Fallback to direct connection
        _connect_if_possible(src_node, src_out, dst_mat, dst_attr)


def _reconnect_sgs(src_old, dst_new):
    """Connect all SGs that used to be driven by src_old to dst_new, and create new SG for old material."""
    old_sgs = _sgs_of_material(src_old)
    
    # Create a new shading group for the old material
    old_sg_name = f"{src_old}_SG"
    old_sg = _unique_name(old_sg_name)
    old_sg_node = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=old_sg)
    
    # Connect the old material to its new shading group
    try:
        if cmds.objExists(f"{src_old}.outColor") and cmds.objExists(f"{old_sg_node}.surfaceShader"):
            cmds.connectAttr(f"{src_old}.outColor", f"{old_sg_node}.surfaceShader", f=True)
    except Exception:
        pass
    
    # Connect all original SGs to the new material
    for sg in old_sgs:
        try:
            if cmds.objExists(f"{dst_new}.outColor") and cmds.objExists(f"{sg}.surfaceShader"):
                cmds.connectAttr(f"{dst_new}.outColor", f"{sg}.surfaceShader", f=True)
        except Exception:
            pass

def _unique_name(base):
    """Return 'base' if free, otherwise base_1, base_2, ... (first free)."""
    if not cmds.objExists(base):
        return base
    i = 1
    while cmds.objExists(f"{base}_{i}"):
        i += 1
    return f"{base}_{i}"



# --------------------------------------------------------------------------------------
#                           PUBLIC: CONVERT + NAME PRESERVE
# --------------------------------------------------------------------------------------

def convert_material_preserve_name(src_mat, target_type, rewire_textures=True):
    """
    Convert a single material node to target_type:
      - Rename original to <name>_old
      - Create new shader of target_type named exactly <name>
      - Rewire SGs to new shader
      - Rewire incoming textures/utility nodes to target attributes (best-effort)
      - Copy values where sensible; defaults for the rest
    Returns: (old_name, new_name, old_node, new_node)
    """
    if not cmds.objExists(src_mat) or cmds.nodeType(src_mat) not in TYPE_ATTRS:
        raise RuntimeError(f"Not a supported shader node: {src_mat}")

    src_type = cmds.nodeType(src_mat)
    if src_type == target_type:
        return (src_mat, src_mat, src_mat, src_mat)

    orig_name = src_mat
    # Prepare final names
    old_name = _unique_name(f"{orig_name}_old")

    # Create destination (temp name) based on strategy
    if target_type == "standardSurface":
        if src_type == "aiStandardSurface":
            dst_tmp = _aiStandardSurface_to_standardSurface(src_mat)
        elif src_type != "standardSurface":
            dst_tmp = _legacy_to_standard(src_mat)
        else:
            # already stdSurface; return early
            return (orig_name, orig_name, src_mat, src_mat)
        dst_node = dst_tmp
    elif target_type == "aiStandardSurface":
        if src_type == "standardSurface":
            dst_node = _standardSurface_to_aiStandardSurface(src_mat)
        else:
            # Convert via standardSurface
            mid = _legacy_to_standard(src_mat)
            dst_node = _standardSurface_to_aiStandardSurface(mid)
            cmds.delete(mid)
    elif src_type == "standardSurface" and target_type in ("lambert","blinn","phong","surfaceShader"):
        dst_node = _standard_to_legacy(src_mat, target_type)
    elif src_type == "aiStandardSurface" and target_type in ("lambert","blinn","phong","surfaceShader"):
        # Convert via standardSurface
        mid = _aiStandardSurface_to_standardSurface(src_mat)
        dst_node = _standard_to_legacy(mid, target_type)
        cmds.delete(mid)
    else:
        # legacy -> legacy
        dst_node = _legacy_to_legacy_via_standard(src_mat, target_type)

    # Figure mapping hint for rewire
    mapping_hint = _attribute_map_for_pair(src_type, target_type)

    # Rename original -> _old
    src_old = cmds.rename(src_mat, old_name)

    # Rename new to take the original name (may need increment if clash)
    new_name = _unique_name(orig_name) if cmds.objExists(orig_name) else orig_name

    dst_new = cmds.rename(dst_node, new_name)

    # Value cleanup for unmapped (defaults): We don't have to explicitly zero;
    # Maya creates with defaults already. We've set mapped values above.

    # Rewire incoming textures (best-effort)
    if rewire_textures:
        try:
            _rewire_inputs(src_old, dst_new, mapping_hint)
        except Exception:
            # Non-fatal
            pass

    # Reconnect shading groups to the new shader
    _reconnect_sgs(src_old, dst_new)

    return (old_name, new_name, src_old, dst_new)


# --------------------------------------------------------------------------------------
#                                   UI
# --------------------------------------------------------------------------------------

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


def _maya_main_window():
    ptr = omui.MQtUtil.mainWindow()
    return wrapInstance(int(ptr), QtWidgets.QWidget)

class MaterialConverterDialog(QtWidgets.QDialog):
    WINDOW_OBJECT = "MaterialConverterPlusWindow"

    def __init__(self, parent=None):
        # kill old
        old = omui.MQtUtil.findControl(self.WINDOW_OBJECT)
        if old:
            try:
                QtWidgets.QWidget.find(old).close()
            except Exception:
                pass

        super().__init__(parent or _maya_main_window())
        self.setObjectName(self.WINDOW_OBJECT)
        self.setWindowTitle("Material Converter")
        self.setMinimumWidth(350)
        self.setMinimumHeight(300)

        # State
        self._ops_log = []            # list of strings to print
        self._ops_pairs = []          # [(old_name, new_name, old_node, new_node), ...]
        self._undo_open = False

        # --- Widgets ---
        title = QtWidgets.QLabel("Material Converter")
        title.setAlignment(QtCore.Qt.AlignCenter)
        title.setStyleSheet("font-weight:600; font-size:16px; padding:2px;")

        # Top row with convert to and reconnect textures
        row1 = QtWidgets.QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(6)
        self.target_combo = QtWidgets.QComboBox()
        self.target_combo.addItems(["lambert", "blinn", "phong", "standardSurface", "aiStandardSurface", "surfaceShader"])
        self.rewire_cb = QtWidgets.QCheckBox("Reconnect textures")
        self.rewire_cb.setChecked(True)
        row1.addWidget(QtWidgets.QLabel("Convert to:"))
        row1.addWidget(self.target_combo, 1)
        row1.addWidget(self.rewire_cb)
        
        # Material processing mode checkboxes (stacked vertically)
        row2 = QtWidgets.QVBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(1)
        self.process_mode_group = QtWidgets.QButtonGroup()
        
        self.convert_meshes_cb = QtWidgets.QCheckBox("Convert Materials on Selected Meshes")
        self.convert_meshes_cb.setChecked(True)
        self.process_mode_group.addButton(self.convert_meshes_cb, 0)
        
        self.convert_selected_cb = QtWidgets.QCheckBox("Convert Selected Materials")
        self.process_mode_group.addButton(self.convert_selected_cb, 1)
        
        self.convert_all_cb = QtWidgets.QCheckBox("Convert All Materials")
        self.process_mode_group.addButton(self.convert_all_cb, 2)
        
        row2.addWidget(self.convert_meshes_cb)
        row2.addWidget(self.convert_selected_cb)
        row2.addWidget(self.convert_all_cb)
        
        # Preview conversion button and show details checkbox
        preview_row = QtWidgets.QHBoxLayout()
        preview_row.setContentsMargins(0, 0, 0, 0)
        preview_row.setSpacing(4)
        self.list_btn = QtWidgets.QPushButton("Preview Conversion")
        self.list_btn.setFixedHeight(22)
        self.show_details_cb = QtWidgets.QCheckBox("Show Details")
        self.show_details_cb.setChecked(True)  # Default to showing details
        preview_row.addWidget(self.list_btn)
        preview_row.addWidget(self.show_details_cb)
        row2.addLayout(preview_row)

        convert_layout = QtWidgets.QVBoxLayout()
        convert_layout.setContentsMargins(0, 0, 0, 0)
        convert_layout.setSpacing(4)
        self.convert_btn = QtWidgets.QPushButton("Convert Selected Materials")
        self.convert_btn.setMinimumHeight(34)
        self.convert_btn.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.auto_delete_cb = QtWidgets.QCheckBox("Delete old materials after conversion")
        convert_layout.addWidget(self.convert_btn)
        convert_layout.addWidget(self.auto_delete_cb)

        self.log = QtWidgets.QTextEdit()
        self.log.setReadOnly(True)

        # Footer buttons
        foot = QtWidgets.QHBoxLayout()
        foot.setContentsMargins(0, 0, 0, 0)
        foot.setSpacing(4)
        self.clear_log_btn = QtWidgets.QPushButton("Clear Log")
        self.close_btn = QtWidgets.QPushButton("Close")
        foot.addWidget(self.clear_log_btn)
        foot.addStretch(1)
        foot.addWidget(self.close_btn)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)
        lay.addWidget(title)
        lay.addLayout(row1)
        lay.addLayout(row2)
        lay.addLayout(convert_layout)
        lay.addWidget(self.log, 1)
        lay.addLayout(foot)

        # Styling - Always use standalone stylesheet, ignore external stylesheets
        self.setStyleSheet(FALLBACK_STYLESHEET)

        # Signals
        self.list_btn.clicked.connect(self._list_mats)
        self.convert_btn.clicked.connect(self._convert_clicked)
        self.clear_log_btn.clicked.connect(self._clear_log)
        self.close_btn.clicked.connect(self.accept)
        self.show_details_cb.toggled.connect(self._refresh_log_display)

    # ---------------- actions ----------------

    def _append_log(self, text):
        self._ops_log.append(text)
        self.log.append(text)

    def _clear_log(self):
        """Clear the log display."""
        self.log.clear()
        self._ops_log[:] = []

    def _refresh_log_display(self):
        """Refresh the log display when show details checkbox is toggled."""
        # Simply regenerate the current preview if we have materials to show
        if hasattr(self, '_last_preview_materials') and self._last_preview_materials:
            self._list_mats()

    def _analyze_material_inputs(self, material, target_type):
        """Analyze specific inputs that can be converted vs can't be converted."""
        convertible_inputs = []
        non_convertible_inputs = []
        
        # Get source material type and its attributes
        src_type = cmds.nodeType(material)
        src_attrs = TYPE_ATTRS.get(src_type, [])
        
        # Check each attribute for incoming connections
        for attr in src_attrs:
            connections = cmds.listConnections(f"{material}.{attr}", s=True, d=False) or []
            if connections:
                # Get the connected node name and type
                connected_node = connections[0]
                node_type = cmds.nodeType(connected_node)
                
                # Check if this attribute can be converted to target type
                dst_attr = self._get_best_dst_for_attr(attr, target_type)
                if dst_attr:
                    convertible_inputs.append(f"{attr} ({node_type}) → {dst_attr}")
                else:
                    non_convertible_inputs.append(f"{attr} ({node_type})")
        
        return convertible_inputs, non_convertible_inputs

    def _get_best_dst_for_attr(self, src_attr, dst_type):
        """Get the best destination attribute for a source attribute when converting to dst_type."""
        # Simple heuristic mapping for common conversions
        heuristics = {
            # Legacy to Standard Surface
            "color": "baseColor",
            "diffuse": "base",
            "incandescence": "emissionColor", 
            "transparency": "transmission",
            "specularColor": "specularColor",
            "reflectivity": "specular",
            "eccentricity": "specularRoughness",
            "cosinePower": "specularRoughness",
            "normalCamera": "normalCamera",
            
            # Standard Surface to Legacy
            "baseColor": "color",
            "base": "diffuse",
            "emissionColor": "incandescence",
            "transmission": "transparency",
            "specularColor": "specularColor", 
            "specular": "reflectivity",
            "specularRoughness": "eccentricity",
            "normalCamera": "normalCamera",
            
            # Cross-legacy mappings
            "opacity": "transparency",
            "transmissionColor": "transparency",
        }
        
        # Check if we have a direct mapping
        if src_attr in heuristics:
            mapped_attr = heuristics[src_attr]
            # Verify the destination type has this attribute
            dst_attrs = TYPE_ATTRS.get(dst_type, [])
            if mapped_attr in dst_attrs:
                return mapped_attr
        
        # Check if the attribute exists directly in the destination type
        dst_attrs = TYPE_ATTRS.get(dst_type, [])
        if src_attr in dst_attrs:
            return src_attr
            
        return None

    def _list_mats(self):
        """Preview what conversions will be made based on selected processing mode."""
        self.log.clear()
        
        # Default materials to exclude
        default_materials = {"lambert1", "standardSurface1", "particleCloud1"}
        target = self.target_combo.currentText()
        
        # Determine processing mode
        if self.convert_meshes_cb.isChecked():
            # Preview materials on selected meshes
            sel = cmds.ls(sl=True, l=True) or []
            if not sel:
                self._append_log("No meshes selected.")
                return
            mats = set()
            for x in sel:
                for shp in cmds.listRelatives(x, s=True, f=True) or []:
                    for sg in cmds.listConnections(shp, type="shadingEngine") or []:
                        mats.update(cmds.ls(cmds.listConnections(sg + ".surfaceShader") or [], materials=True))
            mats = sorted(mats)
            mode_text = "Materials on selected meshes"
            
        elif self.convert_selected_cb.isChecked():
            # Preview selected materials directly
            sel = cmds.ls(sl=True, materials=True) or []
            if not sel:
                self._append_log("No materials selected.")
                return
            mats = sorted(sel)
            mode_text = "Selected materials"
            
        else:  # convert_all_cb.isChecked()
            # Preview all materials in scene
            mats = sorted(cmds.ls(materials=True) or [])
            mode_text = "All materials in scene"
        
        # Filter out default materials
        mats = [m for m in mats if m not in default_materials]
        
        if not mats:
            self._append_log(f"No materials found ({mode_text.lower()}).")
            return
        
        # Store for potential regeneration when checkbox is toggled
        self._last_preview_materials = mats
        self._last_preview_target = target
        self._last_preview_mode_text = mode_text
        
        # Group materials by source type for preview
        conversion_groups = {}
        for m in mats:
            src_type = cmds.nodeType(m)
            if src_type not in conversion_groups:
                conversion_groups[src_type] = []
            conversion_groups[src_type].append(m)
        
        self._append_log(f"<b>Preview: Converting {mode_text.lower()} to {target}</b>")
        self._append_log("")
        
        # Show preview conversions
        for src_type, materials in conversion_groups.items():
            self._append_log(f"<b>{src_type} → {target}</b>")
            for m in materials:
                # Analyze input connections
                convertible_inputs, non_convertible_inputs = self._analyze_material_inputs(m, target)
                
                # Generate cleaner preview format
                old_name = f"{m}_old"
                new_name = m  # The new material will have the original name
                
                # Determine color based on conversion status
                if len(convertible_inputs) > 0 and len(non_convertible_inputs) == 0:
                    # All inputs can be converted - GREEN
                    color = "#00ff00"  # Green
                elif len(convertible_inputs) == 0 and len(non_convertible_inputs) > 0:
                    # No inputs can be converted - RED
                    color = "#ff0000"  # Red
                elif len(convertible_inputs) > 0 and len(non_convertible_inputs) > 0:
                    # Mixed - some can, some can't - YELLOW
                    color = "#ffff00"  # Yellow
                else:
                    # No inputs at all - default cyan
                    color = "#00f7c8"  # Cyan
                
                # Build the conversion info string
                conversion_info = f"{old_name} → <b><font color='{color}'>{new_name}</font></b>"
                self._append_log(f"  {conversion_info}")
                
                # Show detailed input information only if checkbox is checked
                if self.show_details_cb.isChecked():
                    if convertible_inputs:
                        self._append_log(f"    <font color='#00ff00'>✓ Convertible inputs:</font>")
                        for input_info in convertible_inputs:
                            self._append_log(f"      • {input_info}")
                    
                    if non_convertible_inputs:
                        self._append_log(f"    <font color='#ff6666'>✗ Non-convertible inputs:</font>")
                        for input_info in non_convertible_inputs:
                            self._append_log(f"      • {input_info}")
                    
                    if not convertible_inputs and not non_convertible_inputs:
                        self._append_log(f"    <font color='#888888'>No texture/connection inputs</font>")
                else:
                    # Show summary counts when details are hidden
                    if convertible_inputs or non_convertible_inputs:
                        stats_parts = []
                        if convertible_inputs:
                            stats_parts.append(f"<font color='#ffffff'>({len(convertible_inputs)} input{'s' if len(convertible_inputs) != 1 else ''} can be converted)</font>")
                        if non_convertible_inputs:
                            stats_parts.append(f"<font color='#ffffff'>({len(non_convertible_inputs)} input{'s' if len(non_convertible_inputs) != 1 else ''} can't be converted)</font>")
                        self._append_log(f"  {' '.join(stats_parts)}")
                    # Don't show "No texture/connection inputs" when details are hidden
                
                # Add small spacer between materials
                self._append_log("<div style='height: 4px;'></div>")
            
            self._append_log("")  # Empty line between groups
        
        self._append_log("<i>This is a preview. Click 'Convert Selected Materials' to perform the actual conversion.</i>")

    def _convert_clicked(self):
        target = self.target_combo.currentText()
        rewire = self.rewire_cb.isChecked()
        auto_delete = self.auto_delete_cb.isChecked()
        deleted_materials = 0
        deleted_sgs = 0
        
        # Determine processing mode
        if self.convert_meshes_cb.isChecked():
            # Convert materials on selected meshes
            sel = cmds.ls(sl=True, l=True) or []
            if not sel:
                self._append_log("No meshes selected.")
                return
            mats = set()
            for x in sel:
                for shp in cmds.listRelatives(x, s=True, f=True) or []:
                    for sg in cmds.listConnections(shp, type="shadingEngine") or []:
                        mats.update(cmds.ls(cmds.listConnections(sg + ".surfaceShader") or [], materials=True))
            mats = sorted(mats)
            mode_text = "materials on selected meshes"
            
        elif self.convert_selected_cb.isChecked():
            # Convert selected materials directly
            sel = cmds.ls(sl=True, materials=True) or []
            if not sel:
                self._append_log("No materials selected.")
                return
            mats = sorted(sel)
            mode_text = "selected materials"
            
        else:  # convert_all_cb.isChecked()
            # Convert all materials in scene
            mats = sorted(cmds.ls(materials=True) or [])
            if not mats:
                self._append_log("No materials found in scene.")
                return
            mode_text = "all materials"

        # Filter out default materials
        default_materials = {"lambert1", "standardSurface1", "particleCloud1"}
        mats = [m for m in mats if m not in default_materials]
        
        if not mats:
            self._append_log(f"No materials found ({mode_text}).")
            return

        self._ops_pairs[:] = []
        self.log.clear()
        
        # Group materials by source type for better organization
        conversion_groups = {}
        for m in mats:
            src_type = cmds.nodeType(m)
            if src_type not in conversion_groups:
                conversion_groups[src_type] = []
            conversion_groups[src_type].append(m)
        
        # Log conversion groups
        cmds.undoInfo(openChunk=True)
        self._undo_open = True
        try:
            for src_type, materials in conversion_groups.items():
                self._append_log(f"<b>{src_type} → {target}</b>")
                for m in materials:
                    try:
                        old_name, new_name, old_node, new_node = convert_material_preserve_name(m, target, rewire_textures=rewire)
                        self._ops_pairs.append((old_name, new_name, old_node, new_node))
                        
                        # Analyze input connections for color coding
                        convertible_inputs, non_convertible_inputs = self._analyze_material_inputs(m, target)
                        
                        # Determine color based on conversion status
                        if len(convertible_inputs) > 0 and len(non_convertible_inputs) == 0:
                            # All inputs can be converted - GREEN
                            color = "#00ff00"  # Green
                        elif len(convertible_inputs) == 0 and len(non_convertible_inputs) > 0:
                            # No inputs can be converted - RED
                            color = "#ff0000"  # Red
                        elif len(convertible_inputs) > 0 and len(non_convertible_inputs) > 0:
                            # Mixed - some can, some can't - YELLOW
                            color = "#ffff00"  # Yellow
                        else:
                            # No inputs at all - default cyan
                            color = "#00f7c8"  # Cyan
                        
                        # Use cleaner format: old_name → new_name with color coding
                        self._append_log(f"  {old_name} → <b><font color='{color}'>{new_name}</font></b>")
                        
                        # Show detailed input information for actual conversion
                        if self.show_details_cb.isChecked():
                            if convertible_inputs:
                                self._append_log(f"    <font color='#00ff00'>✓ Converted inputs:</font>")
                                for input_info in convertible_inputs:
                                    self._append_log(f"      • {input_info}")
                            
                            if non_convertible_inputs:
                                self._append_log(f"    <font color='#ff6666'>✗ Lost inputs:</font>")
                                for input_info in non_convertible_inputs:
                                    self._append_log(f"      • {input_info}")
                            
                            if not convertible_inputs and not non_convertible_inputs:
                                self._append_log(f"    <font color='#888888'>No texture/connection inputs</font>")
                        else:
                            # Show summary counts when details are hidden
                            if convertible_inputs or non_convertible_inputs:
                                stats_parts = []
                                if convertible_inputs:
                                    stats_parts.append(f"<font color='#ffffff'>({len(convertible_inputs)} input{'s' if len(convertible_inputs) != 1 else ''} converted)</font>")
                                if non_convertible_inputs:
                                    stats_parts.append(f"<font color='#ffffff'>({len(non_convertible_inputs)} input{'s' if len(non_convertible_inputs) != 1 else ''} lost)</font>")
                                self._append_log(f"  {' '.join(stats_parts)}")
                            # Don't show "No texture/connection inputs" when details are hidden
                        
                        # Add small spacer between materials
                        self._append_log("<div style='height: 4px;'></div>")
                            
                    except Exception as e:
                        self._append_log(f"  <font color='#ff7777'>Failed: {m} → {target} :: {e}</font>")
                        self._append_log(f"  <pre>{traceback.format_exc()}</pre>")
                self._append_log("")  # Empty line between groups
            
            if auto_delete:
                deleted_materials, deleted_sgs = self._delete_old_materials()
        finally:
            cmds.undoInfo(closeChunk=True)
            self._undo_open = False

        # Summary + footer hint
        self._append_log("<br><b>Done.</b>")
        if auto_delete:
            if deleted_sgs > 0:
                self._append_log(f"Deleted {deleted_materials} old material(s) and {deleted_sgs} old shading group(s).")
            else:
                self._append_log(f"Deleted {deleted_materials} old material(s).")
        self._append_log("Enable 'Delete old materials after conversion' to remove *_old materials automatically. Use Maya's undo to revert the entire conversion.")

    def _delete_old_materials(self):
        """Delete *_old nodes and their shading groups created during this session's conversion."""
        deleted_materials = 0
        deleted_sgs = 0
        
        for (old_name, _new_name, old_node, _new_node) in self._ops_pairs:
            if cmds.objExists(old_node):
                try:
                    # Find and delete the old material's shading group
                    old_sg_name = f"{old_node}_SG"
                    if cmds.objExists(old_sg_name):
                        try:
                            cmds.delete(old_sg_name)
                            deleted_sgs += 1
                        except Exception:
                            pass
                    
                    # Detach any SG surfaceShader if still connected (rare)
                    for sg in _sgs_of_material(old_node):
                        try:
                            src = cmds.listConnections(sg + ".surfaceShader", s=True, d=False, p=True) or []
                            if src and src[0].split(".")[0] == old_node:
                                cmds.disconnectAttr(f"{old_node}.outColor", f"{sg}.surfaceShader")
                        except Exception:
                            pass
                    
                    # Delete the old material
                    cmds.delete(old_node)
                    deleted_materials += 1
                except Exception:
                    pass
        
        return deleted_materials, deleted_sgs

    # Ensure UI closes cleanly
    def closeEvent(self, ev):
        try:
            if self._undo_open:
                cmds.undoInfo(closeChunk=True)
                self._undo_open = False
        except Exception:
            pass
        super().closeEvent(ev)


# --------------------------------------------------------------------------------------
#                               ENTRY POINT
# --------------------------------------------------------------------------------------

def show():
    """
    Show the Material Converter dialog with standalone styling.
    No external dependencies - completely self-contained.
    """
    dlg = MaterialConverterDialog(parent=_maya_main_window())
    dlg.resize(360, 240)  # Start as small as possible
    dlg.show()
    return dlg


def debug_opacity_transparency_conversion():
    """
    Debug function to test opacity/transparency conversion logic.
    Creates test materials with opacity/transparency connections and tests conversion.
    """
    print("=== DEBUG: Opacity/Transparency Conversion Test ===")
    
    # Create test materials
    test_lambert = cmds.shadingNode("lambert", asShader=True, name="test_lambert")
    test_std = cmds.shadingNode("standardSurface", asShader=True, name="test_std")
    
    # Create test file texture
    test_file = cmds.shadingNode("file", asTexture=True, name="test_file")
    
    print(f"Created test materials: {test_lambert}, {test_std}")
    print(f"Created test file: {test_file}")
    
    # Test 1: Direct opacity connection (no reverse)
    print("\n--- Test 1: Direct opacity connection ---")
    cmds.connectAttr(f"{test_file}.outColor", f"{test_std}.opacity", f=True)
    print(f"Connected {test_file}.outColor -> {test_std}.opacity")
    
    # Test conversion std -> lambert (should add reverse)
    print("Converting standardSurface -> lambert...")
    try:
        old_name, new_name, old_node, new_node = convert_material_preserve_name(test_std, "lambert", rewire_textures=True)
        print(f"Conversion result: {old_name} -> {new_name}")
    except Exception as e:
        print(f"Conversion failed: {e}")
    
    # Test 2: Opacity with reverse node
    print("\n--- Test 2: Opacity with reverse node ---")
    test_std2 = cmds.shadingNode("standardSurface", asShader=True, name="test_std2")
    test_file2 = cmds.shadingNode("file", asTexture=True, name="test_file2")
    reverse_node = cmds.shadingNode("reverse", asUtility=True, name="test_reverse")
    
    cmds.connectAttr(f"{test_file2}.outColor", f"{reverse_node}.input", f=True)
    cmds.connectAttr(f"{reverse_node}.output", f"{test_std2}.opacity", f=True)
    print(f"Connected {test_file2}.outColor -> {reverse_node}.input -> {test_std2}.opacity")
    
    # Test conversion std -> lambert (should remove reverse)
    print("Converting standardSurface with reverse -> lambert...")
    try:
        old_name, new_name, old_node, new_node = convert_material_preserve_name(test_std2, "lambert", rewire_textures=True)
        print(f"Conversion result: {old_name} -> {new_name}")
    except Exception as e:
        print(f"Conversion failed: {e}")
    
    # Test 3: Emission texture connection
    print("\n--- Test 3: Emission texture connection ---")
    test_std3 = cmds.shadingNode("standardSurface", asShader=True, name="test_std3")
    test_file3 = cmds.shadingNode("file", asTexture=True, name="test_file3")
    
    cmds.connectAttr(f"{test_file3}.outColor", f"{test_std3}.emissionColor", f=True)
    print(f"Connected {test_file3}.outColor -> {test_std3}.emissionColor")
    
    # Test conversion std -> lambert (should connect texture to incandescence)
    print("Converting standardSurface with emission texture -> lambert...")
    try:
        old_name, new_name, old_node, new_node = convert_material_preserve_name(test_std3, "lambert", rewire_textures=True)
        print(f"Conversion result: {old_name} -> {new_name}")
    except Exception as e:
        print(f"Conversion failed: {e}")
    
    # Test 4: Incandescence -> emission conversion
    print("\n--- Test 4: Incandescence -> emission conversion ---")
    test_lambert = cmds.shadingNode("lambert", asShader=True, name="test_lambert_inc")
    
    # Set incandescence to a bright red color
    cmds.setAttr(f"{test_lambert}.incandescence", 2.0, 0.5, 0.2, type="double3")
    print(f"Set {test_lambert}.incandescence to (2.0, 0.5, 0.2)")
    
    # Test conversion lambert -> standardSurface (should set emission color and weight)
    print("Converting lambert with incandescence -> standardSurface...")
    try:
        old_name, new_name, old_node, new_node = convert_material_preserve_name(test_lambert, "standardSurface", rewire_textures=True)
        print(f"Conversion result: {old_name} -> {new_name}")
    except Exception as e:
        print(f"Conversion failed: {e}")
    
    # Test 5: Legacy to legacy transparency (should NOT add reverse nodes)
    print("\n--- Test 5: Legacy to legacy transparency (no reverse) ---")
    test_lambert2 = cmds.shadingNode("lambert", asShader=True, name="test_lambert_trans")
    test_file4 = cmds.shadingNode("file", asTexture=True, name="test_file4")
    
    cmds.connectAttr(f"{test_file4}.outColor", f"{test_lambert2}.transparency", f=True)
    print(f"Connected {test_file4}.outColor -> {test_lambert2}.transparency")
    
    # Test conversion lambert -> blinn (both use transparency - should NOT add reverse)
    print("Converting lambert with transparency texture -> blinn...")
    try:
        old_name, new_name, old_node, new_node = convert_material_preserve_name(test_lambert2, "blinn", rewire_textures=True)
        print(f"Conversion result: {old_name} -> {new_name}")
    except Exception as e:
        print(f"Conversion failed: {e}")
    
    # Test 6: Transparency texture -> opacity/transmission
    print("\n--- Test 6: Transparency texture -> opacity/transmission ---")
    test_lambert3 = cmds.shadingNode("lambert", asShader=True, name="test_lambert_tex")
    test_file5 = cmds.shadingNode("file", asTexture=True, name="test_file5")
    
    cmds.connectAttr(f"{test_file5}.outColor", f"{test_lambert3}.transparency", f=True)
    print(f"Connected {test_file5}.outColor -> {test_lambert3}.transparency")
    
    # Test conversion lambert -> standardSurface (should connect texture to transmission directly)
    print("Converting lambert with transparency texture -> standardSurface...")
    try:
        old_name, new_name, old_node, new_node = convert_material_preserve_name(test_lambert3, "standardSurface", rewire_textures=True)
        print(f"Conversion result: {old_name} -> {new_name}")
    except Exception as e:
        print(f"Conversion failed: {e}")
    
    print("\n=== Debug test completed ===")
    print("Check the Maya Script Editor for detailed debug output.")
    print("Expected behavior:")
    print("- Test 1: Direct opacity -> should add reverse node for transparency")
    print("- Test 2: Opacity with reverse -> should remove reverse node")
    print("- Test 3: Emission texture -> should connect to incandescence")
    print("- Test 4: Incandescence (2.0,0.5,0.2) -> emissionColor (1.0,0.25,0.1) + emission weight 2.0")
    print("- Test 5: Lambert->Blinn transparency -> should NOT add reverse (same attribute type)")
    print("- Test 6: Lambert transparency texture -> standardSurface transmission (direct connection, no reverse)")