# -*- coding: utf-8 -*-
"""
Material Converter (name-preserving, texture rewire, conversion log w/ Delete Old / Undo / OK)

- Converts between: lambert, blinn, phong, standardSurface, surfaceShader
- Renames source -> <name>_old, creates new target with the *original* name
- Rewires SGs to the new material
- Reconnects incoming textures/utility nodes to mapped attributes where possible
- Copies values for mapped attributes; unmapped attributes reset to defaults
- Shows a conversion log; offers "Delete Old", "Undo", "OK"

Drop anywhere on PYTHONPATH (e.g., your QuickMaterials folder) and run:
    import material_converter_plus as mcp
    mcp.show()

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
        "color","diffuse","ambientColor","incandescence","transparency"
    ],
    "blinn": [
        "color","diffuse","ambientColor","incandescence","transparency",
        "specularColor","reflectivity","specularRollOff","eccentricity","reflectedColor"
    ],
    "phong": [
        "color","diffuse","ambientColor","incandescence","transparency",
        "specularColor","reflectivity","cosinePower","reflectedColor"
    ],
    "standardSurface": [
        "base","baseColor",
        "specular","specularColor","specularRoughness",
        "metalness",
        "emission","emissionColor",
        "transmission","transmissionColor",
        "opacity",
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
        return False
    if not _exists_attr(dst_node, dst_attr):
        return False
    try:
        cmds.connectAttr(f"{src_node}.{src_attr}", f"{dst_node}.{dst_attr}", f=True)
        return True
    except Exception:
        return False

def _safe_shading_node(node_type, name_hint):
    base = name_hint
    # let Maya make unique if needed
    return cmds.shadingNode(node_type, asShader=True, name=base)

def _sgs_of_material(material):
    return cmds.listConnections(material, type="shadingEngine") or []


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
    # emissionColor + emission approx
    inc = _get_val(src, "incandescence", (0,0,0))
    if inc: _set_val(dst, "emissionColor", inc); _set_val(dst, "emission", 1.0 if _avg_rgb(inc) > 0.001 else 0.0)

    # spec color + weight & roughness
    if src_type in ("lambert","blinn","phong"):
        sc = _get_val(src, "specularColor", None)
        if sc:
            _set_val(dst, "specularColor", sc)
        spec_w = _get_val(src, "reflectivity", None)
        if spec_w is None and sc:
            spec_w = _avg_rgb(sc)
        _set_val(dst, "specular", spec_w if spec_w is not None else 0.0)

        if src_type == "blinn":
            rough = _blinn_ecc_to_roughness(_get_val(src, "eccentricity", 0.3))
            _set_val(dst, "specularRoughness", rough)
        elif src_type == "phong":
            rough = _phong_power_to_roughness(_get_val(src, "cosinePower", 30.0))
            _set_val(dst, "specularRoughness", rough)

    # transparency -> opacity + transmission
    tr = _get_val(src, "transparency", None)
    if tr:
        opacity = tuple(max(0.0, min(1.0, 1.0 - c)) for c in tr)
        _set_val(dst, "opacity", opacity)
        _set_val(dst, "transmission", _avg_rgb(tr))
        _set_val(dst, "transmissionColor", (1,1,1))

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

        # 2) Same-name fallback
        if src_attr in dst_attrs:
            return src_attr

        # 3) Heuristics (common aliases)
        heur = {
            "baseColor": "color",
            "emissionColor": "incandescence",
            "opacity": "transparency",
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
        _connect_if_possible(src_node, src_out, dst_mat, dst_attr)


def _reconnect_sgs(src_old, dst_new):
    """Connect all SGs that used to be driven by src_old to dst_new."""
    for sg in _sgs_of_material(src_old):
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
        dst_tmp = _legacy_to_standard(src_mat) if src_type != "standardSurface" else None
        if dst_tmp is None:
            # already stdSurface; return early
            return (orig_name, orig_name, src_mat, src_mat)
        dst_node = dst_tmp
    elif src_type == "standardSurface" and target_type in ("lambert","blinn","phong","surfaceShader"):
        dst_node = _standard_to_legacy(src_mat, target_type)
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

# Provide graceful styling: uses caller’s style if they import it, else a compact fallback
FALLBACK_STYLESHEET = """
QDialog{
    background:#3a3a3a;           /* slightly lighter than before */
    color:#ffffff;
    font:12px "Segoe UI";
}

/* Buttons */
QPushButton{
    color:#ffffff;
    background-color:#6d6d6d;
    border:1px solid #9a9a9a;     /* thin lighter frame */
    border-radius:8px;
    padding:4px 8px;
}
QPushButton:hover{ background-color:#7a7a7a; }
QPushButton:pressed{ background-color:#222222; }

/* Edits */
QLineEdit{
    color:#ffffff;
    background-color:#4a4a4a;
    border:1px solid #9a9a9a;     /* thin lighter frame */
    border-radius:8px;
    padding:3px 6px;
}

/* ComboBox (matches your spec) */
QComboBox {
    font-family: 'Segoe UI';
    font-size: 12px;
    color: #ffffff;
    background-color: #333333;
    border: 1px solid #9a9a9a;    /* thin lighter frame */
    border-radius: 6px;
    padding: 1px 2px;
    padding-left: 10px;
    margin: 2px 2px;
}
QComboBox:hover { background-color: #222222; }
QComboBox::drop-down {
    border-left: 0px solid #666666;
    width: 0px;
    background-color: #333333;
    border-radius: 2px;
    padding: 0px 0px;
}
QComboBox::down-arrow {
    image: url(:/arrow-down.png);
    width: 10px;
    height: 10px;
}
QComboBox::down-arrow:hover {
    image: url(:/arrow-down-hover.png);
}
QComboBox QAbstractItemView {
    font-family: 'Segoe UI';
    font-size: 14px;
    color: #ffffff;
    background-color: #262626;
    border: 1px solid #9a9a9a;    /* thin lighter frame */
    border-radius: 4px;
    selection-background-color: #5a5a5a;
    selection-color: #ffffff;
    padding: 2px 10px;
}
QComboBox::disabled {
    color: #cccccc;
    border: 1px solid #555555;
    background-color: #7a7a7a;
}

/* Text areas */
QTextEdit{
    background:#242424;
    border:1px solid #9a9a9a;     /* thin lighter frame */
    border-radius:8px;
}

/* Frames (lighter, subtle radius, thin frame) */
QFrame{
    background-color:#5f5f5f;     /* not as dark */
    border:1px solid #9a9a9a;     /* thin lighter frame */
    border-radius:10px;
    padding:0px;
    margin:0px;
}

/* Checkboxes */
QCheckBox{ color:#ffffff; }
"""


def _maya_main_window():
    ptr = omui.MQtUtil.mainWindow()
    return wrapInstance(int(ptr), QtWidgets.QWidget)

class MaterialConverterDialog(QtWidgets.QDialog):
    WINDOW_OBJECT = "MaterialConverterPlusWindow"

    def __init__(self, parent=None, app_stylesheet=None):
        # kill old
        old = omui.MQtUtil.findControl(self.WINDOW_OBJECT)
        if old:
            try:
                QtWidgets.QWidget.find(old).close()
            except Exception:
                pass

        super(MaterialConverterDialog, self).__init__(parent or _maya_main_window())
        self.setObjectName(self.WINDOW_OBJECT)
        self.setWindowTitle("Material Converter (Name-Preserving)")
        self.setMinimumWidth(560)

        # State
        self._ops_log = []            # list of strings to print
        self._ops_pairs = []          # [(old_name, new_name, old_node, new_node), ...]
        self._undo_open = False

        # --- Widgets ---
        title = QtWidgets.QLabel("Material Converter")
        title.setAlignment(QtCore.Qt.AlignCenter)
        title.setStyleSheet("font-weight:600; font-size:16px; padding:6px;")

        row1 = QtWidgets.QHBoxLayout()
        self.target_combo = QtWidgets.QComboBox()
        self.target_combo.addItems(["lambert", "blinn", "phong", "standardSurface", "surfaceShader"])
        self.rewire_cb = QtWidgets.QCheckBox("Reconnect textures")
        self.rewire_cb.setChecked(True)
        self.list_btn = QtWidgets.QPushButton("List Materials on Selection")
        row1.addWidget(QtWidgets.QLabel("Convert to:"))
        row1.addWidget(self.target_combo, 1)
        row1.addWidget(self.rewire_cb)
        row1.addStretch(1)
        row1.addWidget(self.list_btn)

        self.convert_btn = QtWidgets.QPushButton("Convert Selected Materials")
        self.convert_btn.setMinimumHeight(32)

        self.log = QtWidgets.QTextEdit()
        self.log.setReadOnly(True)

        # Footer buttons
        foot = QtWidgets.QHBoxLayout()
        self.delete_old_btn = QtWidgets.QPushButton("Delete Old")
        self.undo_btn = QtWidgets.QPushButton("Undo")
        self.ok_btn = QtWidgets.QPushButton("OK")
        foot.addStretch(1)
        foot.addWidget(self.delete_old_btn)
        foot.addWidget(self.undo_btn)
        foot.addWidget(self.ok_btn)

        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(title)
        lay.addLayout(row1)
        lay.addWidget(self.convert_btn)
        lay.addWidget(self.log, 1)
        lay.addLayout(foot)

        # Styling
        if app_stylesheet:
            self.setStyleSheet(app_stylesheet)
        else:
            self.setStyleSheet(FALLBACK_STYLESHEET)

        # Signals
        self.list_btn.clicked.connect(self._list_mats)
        self.convert_btn.clicked.connect(self._convert_clicked)
        self.delete_old_btn.clicked.connect(self._delete_old_clicked)
        self.undo_btn.clicked.connect(self._undo_clicked)
        self.ok_btn.clicked.connect(self.accept)

    # ---------------- actions ----------------

    def _append_log(self, text):
        self._ops_log.append(text)
        self.log.append(text)

    def _list_mats(self):
        self.log.clear()
        sel = cmds.ls(sl=True, l=True) or []
        if not sel:
            self._append_log("No selection.")
            return
        mats = set()
        for x in sel:
            for shp in cmds.listRelatives(x, s=True, f=True) or []:
                for sg in cmds.listConnections(shp, type="shadingEngine") or []:
                    mats.update(cmds.ls(cmds.listConnections(sg + ".surfaceShader") or [], materials=True))
        mats = sorted(mats)
        if not mats:
            self._append_log("No materials found on selection.")
            return
        self._append_log("<b>Materials on selection:</b>")
        for m in mats:
            self._append_log(f"  - {m} <i>({cmds.nodeType(m)})</i>")

    def _convert_clicked(self):
        target = self.target_combo.currentText()
        rewire = self.rewire_cb.isChecked()
        sel = cmds.ls(sl=True, l=True) or []

        # find unique shader nodes from selection (like your helper)
        mats = set()
        for x in sel:
            for shp in cmds.listRelatives(x, s=True, f=True) or []:
                for sg in cmds.listConnections(shp, type="shadingEngine") or []:
                    mats.update(cmds.ls(cmds.listConnections(sg + ".surfaceShader") or [], materials=True))
        mats = sorted(mats)

        if not mats:
            self._append_log("No materials found on selection.")
            return

        self._ops_pairs[:] = []
        self.log.clear()
        self._append_log(f"<b>Converting to:</b> {target}")
        cmds.undoInfo(openChunk=True)
        self._undo_open = True
        try:
            for m in mats:
                try:
                    old_name, new_name, old_node, new_node = convert_material_preserve_name(m, target, rewire_textures=rewire)
                    self._ops_pairs.append((old_name, new_name, old_node, new_node))
                    self._append_log(f"{m}  →  <b>{new_name}</b>  (old: {old_name})")
                except Exception as e:
                    self._append_log(f"<font color='#ff7777'>Failed: {m} → {target} :: {e}</font>")
                    self._append_log(f"<pre>{traceback.format_exc()}</pre>")
        finally:
            cmds.undoInfo(closeChunk=True)
            self._undo_open = False

        # Summary + footer hint
        self._append_log("<br><b>Done.</b>")
        self._append_log("Use <i>Delete Old</i> to remove *_old materials, <i>Undo</i> to revert, or <i>OK</i> to close.")

    def _delete_old_clicked(self):
        """Delete *_old nodes created during this session’s conversion."""
        deleted = 0
        for (old_name, _new_name, old_node, _new_node) in self._ops_pairs:
            if cmds.objExists(old_node):
                try:
                    # Detach any SG surfaceShader if still connected (rare)
                    for sg in _sgs_of_material(old_node):
                        try:
                            src = cmds.listConnections(sg + ".surfaceShader", s=True, d=False, p=True) or []
                            if src and src[0].split(".")[0] == old_node:
                                cmds.disconnectAttr(f"{old_node}.outColor", f"{sg}.surfaceShader")
                        except Exception:
                            pass
                    cmds.delete(old_node)
                    deleted += 1
                except Exception:
                    pass
        self._append_log(f"Deleted {deleted} old material(s).")

    def _undo_clicked(self):
        """Undo the entire conversion chunk (if still possible)."""
        try:
            cmds.undo()
            self._append_log("Undo successful.")
            # Clear local list because scene is rolled back
            self._ops_pairs[:] = []
        except Exception as e:
            self._append_log(f"Undo failed: {e}")

    # Ensure UI closes cleanly
    def closeEvent(self, ev):
        try:
            if self._undo_open:
                cmds.undoInfo(closeChunk=True)
                self._undo_open = False
        except Exception:
            pass
        super(MaterialConverterDialog, self).closeEvent(ev)


# --------------------------------------------------------------------------------------
#                               ENTRY POINT
# --------------------------------------------------------------------------------------

def show(apply_material_list_style=None):
    """
    Show the Material Converter dialog.

    Params:
        apply_material_list_style (str|None): pass your 'material_list_widget_style' string
                                              to enforce the same visual language globally.
    """
    # If user passed their stylesheet string, use it; else fallback
    app_styles = apply_material_list_style or FALLBACK_STYLESHEET
    dlg = MaterialConverterDialog(parent=_maya_main_window(), app_stylesheet=app_styles)
    dlg.resize(720, 540)
    dlg.show()
    return dlg
