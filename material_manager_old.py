# -------------  material_converter.py  -----------------
import maya.cmds as cmds
from PySide2 import QtWidgets, QtCore, QtGui
import maya.OpenMayaUI as omui
from shiboken2 import wrapInstance
import math
import colorsys
import collections


_ATTR_MAP = {
    # legacy self-maps
    "color":               "color",
    "transparency":        "transparency",
    "ambientColor":        "ambientColor",
    "incandescence":       "incandescence",
    "diffuse":             "diffuse",
    "glowIntensity":       "glowIntensity",
    "translucence":        "translucence",
    "translucenceFocus":   "translucenceFocus",
    "translucenceDepth":   "translucenceDepth",
    "matteOpacity":        "matteOpacity",
    "materialAlphaGain":   "materialAlphaGain",
    "shadowAttenuation":   "shadowAttenuation",
    "surfaceThickness":    "surfaceThickness",

    # legacy specular (blinn/phong)
    ("specularColor", "blinn"): "specularColor",
    ("specularColor", "phong"): "specularColor",
    ("reflectedColor","blinn"): "reflectedColor",
    ("reflectedColor","phong"): "reflectedColor",
    ("reflectivity",  "blinn"): "reflectivity",
    ("reflectivity",  "phong"): "reflectivity",

    # legacy highlight width cross-mapping
    ("eccentricity",    "phong"): "cosinePower",
    ("specularRollOff", "phong"): "cosinePower",
    ("cosinePower",     "blinn"): "eccentricity",

    # ------ legacy → standardSurface (selected, safe subset) ------
    ("color",          "standardSurface"): "baseColor",
    ("diffuse",        "standardSurface"): "base",
    ("incandescence",  "standardSurface"): "emissionColor",
    ("specularColor",  "standardSurface"): "specularColor",
    ("reflectivity",   "standardSurface"): "specular",  # weight
    # transparency handled specially (→ opacity & transmission)
}

def _avg_rgb(rgb_tuple):
    try:
        r,g,b = rgb_tuple
        return max(0.0, min(1.0, float((r+g+b)/3.0)))
    except Exception:
        return 0.0

def _phong_power_to_roughness(n):
    try:
        n = max(0.001, float(n))
        import math
        return max(0.0, min(1.0, math.sqrt(2.0 / (n + 2.0))))
    except Exception:
        return 0.5

def _blinn_ecc_to_roughness(ecc):
    try:
        ecc = float(ecc)
        return max(0.0, min(1.0, ecc))
    except Exception:
        return 0.5


# -------- core plugs we actually care about ----------
_ATTRS_COMMON = [
    "diffuse", "color", "transparency",
    "ambientColor", "incandescence",
    "translucence", "translucenceFocus", "translucenceDepth",
]

_ATTRS_BLINN  = _ATTRS_COMMON + [
    "specularColor", "reflectivity", "reflectedColor",
    "reflectionSpecularity", "eccentricity", "specularRollOff",
]
_ATTRS_PHONG  = _ATTRS_COMMON + [
    "specularColor", "reflectivity", "reflectedColor",
    "reflectionSpecularity", "cosinePower",
]
_ATTRS_LAMBERT = _ATTRS_COMMON


# Core numeric/color plugs for standardSurface we’ll compare/print
_ATTRS_STANDARD = [
    "base", "baseColor",
    "specular", "specularColor", "specularRoughness",
    "metalness",
    "transmission", "transmissionColor",
    "emission", "emissionColor",
    "opacity",
]

_WHITELIST = {
    "blinn"          : set(_ATTRS_BLINN),
    "phong"          : set(_ATTRS_PHONG),
    "lambert"        : set(_ATTRS_LAMBERT),
    "standardSurface": set(_ATTRS_STANDARD),
}


# ----------------------------------------------------------------------------
# ----------  COLOUR NAME GUESS  (simple but good enough)  -------------------
# ----------------------------------------------------------------------------
_COLOR_RANGES = [
    ("red",     (0,   15)),
    ("orange",  (15,  45)),
    ("yellow",  (45,  70)),
    ("green",   (70, 160)),
    ("cyan",    (160, 200)),
    ("blue",    (200, 255)),
    ("purple",  (255, 320)),
    ("pink",    (320, 345)),
    ("red",     (345, 360)),   # wrap slice
]
# ----- hue-to-name helper (360° wraps to 0°) -----
def _color_name(rgb):
    """
    Simplified color naming:
      - Dark / light adjectives only
      - Rich hue bins (18 slices around the wheel)
      - Grayscale handled separately
    """
    r, g, b = rgb
    h, l, s = colorsys.rgb_to_hls(r, g, b)

    # Grayscale if nearly no saturation
    if s < 0.08 or (max(r, g, b) - min(r, g, b)) < 0.05:
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
    hue_bins = [
        ("red", 350, 360), ("red", 0, 10), ("red-orange", 10, 30),
        ("orange", 30, 50), ("yellow-orange", 50, 70), ("yellow", 70, 90),
        ("yellow-green", 90, 110), ("lime", 110, 130), ("green", 130, 150),
        ("teal", 150, 170), ("cyan", 170, 190), ("sky", 190, 210),
        ("blue", 210, 230), ("indigo", 230, 250), ("violet", 250, 270),
        ("magenta", 270, 290), ("fuchsia", 290, 310), ("pink", 310, 350),
    ]
    base = "color"
    for name, lo, hi in hue_bins:
        if lo <= hue < hi:
            base = name
            break

    # Only dark/light adjectives
    if l <= 0.25:
        brightness = "dark"
    elif l >= 0.75:
        brightness = "light"
    else:
        brightness = ""

    return (brightness + " " + base).strip()


def _roughness_to_phong_power(rough):
    """
    Invert roughness ≈ sqrt(2/(n+2))  →  n ≈ (2 / rough^2) - 2
    Clamped into a sensible Phong range.
    """
    try:
        r = max(0.001, float(rough))
        import math
        n = (2.0 / (r*r)) - 2.0
        return max(2.0, min(200.0, n))
    except Exception:
        return 30.0

def _roughness_to_blinn_ecc(rough):
    """
    Blinn 'eccentricity' ~ highlight width; use roughness directly as heuristic.
    """
    try:
        r = float(rough)
        return max(0.0, min(1.0, r))
    except Exception:
        return 0.3

def _get_material_color_rgb(material):
    """
    Return an (r,g,b) in 0..1 for the material's *display* color attribute.
    - standardSurface → baseColor
    - lambert/blinn/phong → color
    Returns None if not found.
    """
    mtype = cmds.nodeType(material)
    attr = "baseColor" if (mtype == "standardSurface" and cmds.attributeQuery("baseColor", node=material, exists=True)) else "color"
    plug = f"{material}.{attr}"
    try:
        if cmds.attributeQuery(attr, node=material, numberOfChildren=True):
            return cmds.getAttr(plug)[0]  # (r,g,b)
        val = cmds.getAttr(plug)
        if isinstance(val, (tuple, list)) and len(val) == 3:
            return tuple(val)
    except Exception:
        pass
    return None


def _gather_materials_from_selection():
    """Return a unique sorted list of shader nodes on the current selection."""
    mats, sel = set(), cmds.ls(sl=True, l=True) or []
    for x in sel:
        for sh in cmds.listRelatives(x, s=True, f=True) or []:
            for sg in cmds.listConnections(sh, type="shadingEngine") or []:
                mats.update(cmds.ls(cmds.listConnections(sg + ".surfaceShader") or [],
                                    materials=True))
    return sorted(mats)


def list_material_attributes(material):
    """
    Return a dict of {attribute_name: value} for keyable attrs on the material.
    Values are queried safely; array & compound attrs are skipped for brevity.
    """
    attr_dict = {}
    attrs = cmds.listAttr(material, keyable=True) or []
    for attr in attrs:
        plug = f"{material}.{attr}"
        try:
            # Skip multi/compound attributes (keeps output readable)
            if cmds.attributeQuery(attr, node=material, multi=True) or cmds.attributeQuery(attr, node=material, numberOfChildren=True):
                continue
            attr_dict[attr] = cmds.getAttr(plug)
        except RuntimeError:
            # locked/unreadable attrs get ignored
            continue
    return attr_dict



def convert_material(src_mat, target_type):
    if cmds.nodeType(src_mat) == target_type:
        return src_mat

    s_type = cmds.nodeType(src_mat)

    # special cases
    if target_type == "standardSurface":
        return _convert_to_standard_surface(src_mat)

    if s_type == "standardSurface" and target_type in ("lambert", "blinn", "phong"):
        return _convert_from_standard_surface(src_mat, target_type)

    dst_mat = cmds.shadingNode(target_type, asShader=True,
                               name=f"{src_mat}_{target_type}")


    # copy like-for-like ...
    for attr in cmds.listAttr(src_mat) or []:
        plug_src = f"{src_mat}.{attr}"
        try:
            if cmds.attributeQuery(attr, node=src_mat, multi=True): continue
            if not cmds.attributeQuery(attr, node=src_mat, storable=True): continue
            if cmds.connectionInfo(plug_src, isDestination=True): continue
        except RuntimeError:
            continue

        dst_attr = _ATTR_MAP.get((attr, target_type), _ATTR_MAP.get(attr))
        if not dst_attr: continue
        if not cmds.attributeQuery(dst_attr, node=dst_mat, exists=True): continue

        plug_dst = f"{dst_mat}.{dst_attr}"
        try:
            if cmds.attributeQuery(attr, node=src_mat, numberOfChildren=True):
                r,g,b = cmds.getAttr(plug_src)[0]
                cmds.setAttr(plug_dst, r, g, b, type="double3")
            else:
                val = cmds.getAttr(plug_src)
                cmds.setAttr(plug_dst, val)
        except Exception:
            continue

    _convert_specular(src_mat, dst_mat)

    for sg in cmds.listConnections(src_mat, type="shadingEngine") or []:
        try:
            cmds.connectAttr(f"{dst_mat}.outColor", f"{sg}.surfaceShader", f=True)
        except Exception:
            pass

    return dst_mat

def _convert_from_standard_surface(src_mat, target_type):
    """
    Create a legacy lambert/blinn/phong and map standardSurface attributes:
      baseColor → color
      base      → diffuse
      emission/emissionColor → incandescence (+ enable if non-black)
      specular/specularColor/specularRoughness → reflectivity/specularColor/(eccentricity|cosinePower)
      opacity/transmission → transparency (inverse of opacity, boosted by transmission)
    """
    dst = cmds.shadingNode(target_type, asShader=True,
                           name=f"{src_mat}_{target_type}")

    def get_col(attr, default=(0.0, 0.0, 0.0)):
        try:
            if cmds.attributeQuery(attr, node=src_mat, exists=True):
                v = cmds.getAttr(f"{src_mat}.{attr}")
                return v[0] if isinstance(v, (list, tuple)) else default
        except Exception: pass
        return default

    def get_flt(attr, default=0.0):
        try:
            if cmds.attributeQuery(attr, node=src_mat, exists=True):
                return float(cmds.getAttr(f"{src_mat}.{attr}"))
        except Exception: pass
        return default

    def set_col(node, attr, col):
        try:
            if cmds.attributeQuery(attr, node=node, exists=True):
                r,g,b = col
                cmds.setAttr(f"{node}.{attr}", r, g, b, type="double3")
        except Exception: pass

    def set_flt(node, attr, val):
        try:
            if cmds.attributeQuery(attr, node=node, exists=True):
                cmds.setAttr(f"{node}.{attr}", float(val))
        except Exception: pass

    # ---- base/baseColor
    base_color = get_col("baseColor", (0,0,0))
    base_w     = get_flt("base", 0.8)
    # legacy color is straight baseColor
    set_col(dst, "color", base_color)
    # legacy diffuse controls overall diffuse contribution
    set_flt(dst, "diffuse", base_w)

    # ---- emission
    emis_col = get_col("emissionColor", (0,0,0))
    emis_w   = get_flt("emission", 0.0)
    # push into incandescence (color only; Maya's incandescence has no scalar)
    if emis_w > 0.001:
        # scale color by weight to keep energy roughly similar
        scaled = tuple(max(0.0, min(1.0, c * emis_w)) for c in emis_col)
        set_col(dst, "incandescence", scaled)

    # ---- specular
    spec_w   = get_flt("specular", 0.0)                # 0..1
    spec_col = get_col("specularColor", (spec_w,)*3)   # if no color, tint by weight
    rough    = get_flt("specularRoughness", 0.5)

    # common legacy plugs
    set_col(dst, "specularColor", spec_col)

    if target_type == "lambert":
        # lambert has no specular; mimic with very low reflectivity so it stays matte
        set_flt(dst, "reflectivity", 0.0)

    elif target_type == "blinn":
        set_flt(dst, "reflectivity", spec_w)
        set_flt(dst, "specularRollOff", spec_w)                  # pragmatic pairing
        set_flt(dst, "eccentricity", _roughness_to_blinn_ecc(rough))

    elif target_type == "phong":
        set_flt(dst, "reflectivity", spec_w)
        set_flt(dst, "cosinePower", _roughness_to_phong_power(rough))

    # ---- transparency (legacy expects 0=opaque, 1=transparent)
    # standardSurface: opacity (RGB, 1=opaque), transmission (0..1 for refraction)
    opac = get_col("opacity", (1.0, 1.0, 1.0))
    trans_w = get_flt("transmission", 0.0)
    # invert opacity; then ensure at least transmission level (so glass stays glassy)
    inv = tuple(max(0.0, min(1.0, 1.0 - c)) for c in opac)
    inv_boosted = tuple(max(c, trans_w) for c in inv)
    set_col(dst, "transparency", inv_boosted)

    # ---- re-wire SGs to the new shader
    for sg in cmds.listConnections(src_mat, type="shadingEngine") or []:
        try:
            cmds.connectAttr(f"{dst}.outColor", f"{sg}.surfaceShader", f=True)
        except Exception:
            pass

    return dst


def _convert_to_standard_surface(src_mat):
    """Create standardSurface and map legacy attributes sensibly."""
    dst = cmds.shadingNode("standardSurface", asShader=True,
                           name=f"{src_mat}_standardSurface")

    def set_if_exists(attr, value, is_color=False):
        if cmds.attributeQuery(attr, node=dst, exists=True):
            try:
                if is_color:
                    r,g,b = value
                    cmds.setAttr(f"{dst}.{attr}", r, g, b, type="double3")
                else:
                    cmds.setAttr(f"{dst}.{attr}", float(value))
            except Exception:
                pass

    # base / baseColor
    try:
        col = cmds.getAttr(f"{src_mat}.color")[0] if cmds.attributeQuery("color", node=src_mat, exists=True) else (0,0,0)
    except Exception:
        col = (0,0,0)
    set_if_exists("baseColor", col, is_color=True)

    try:
        diff = cmds.getAttr(f"{src_mat}.diffuse") if cmds.attributeQuery("diffuse", node=src_mat, exists=True) else 0.8
    except Exception:
        diff = 0.8
    set_if_exists("base", diff)

    # emission from incandescence
    try:
        inc = cmds.getAttr(f"{src_mat}.incandescence")[0] if cmds.attributeQuery("incandescence", node=src_mat, exists=True) else (0,0,0)
    except Exception:
        inc = (0,0,0)
    set_if_exists("emissionColor", inc, is_color=True)
    set_if_exists("emission", 1.0 if _avg_rgb(inc) > 0.001 else 0.0)

    # specular weight & color
    spec_w = None
    if cmds.attributeQuery("reflectivity", node=src_mat, exists=True):
        try:
            spec_w = float(cmds.getAttr(f"{src_mat}.reflectivity"))
        except Exception:
            spec_w = None
    if spec_w is None and cmds.attributeQuery("specularColor", node=src_mat, exists=True):
        try:
            sc = cmds.getAttr(f"{src_mat}.specularColor")[0]
            spec_w = _avg_rgb(sc)
            set_if_exists("specularColor", sc, is_color=True)
        except Exception:
            spec_w = 0.0
    set_if_exists("specular", spec_w if spec_w is not None else 0.0)

    # roughness from legacy highlight controls
    s_type = cmds.nodeType(src_mat)
    rough = None
    try:
        if s_type == "blinn" and cmds.attributeQuery("eccentricity", node=src_mat, exists=True):
            rough = _blinn_ecc_to_roughness(cmds.getAttr(f"{src_mat}.eccentricity"))
        elif s_type == "phong" and cmds.attributeQuery("cosinePower", node=src_mat, exists=True):
            rough = _phong_power_to_roughness(cmds.getAttr(f"{src_mat}.cosinePower"))
    except Exception:
        pass
    if rough is not None:
        set_if_exists("specularRoughness", rough)

    # transparency → opacity + transmission
    if cmds.attributeQuery("transparency", node=src_mat, exists=True):
        try:
            tr = cmds.getAttr(f"{src_mat}.transparency")[0]  # (r,g,b) 0=opaque,1=transparent
            opacity = tuple(max(0.0, min(1.0, 1.0 - c)) for c in tr)
            set_if_exists("opacity", opacity, is_color=True)

            tr_w = _avg_rgb(tr)
            set_if_exists("transmission", tr_w)
            set_if_exists("transmissionColor", (1.0, 1.0, 1.0), is_color=True)
        except Exception:
            pass

    # re-wire SGs
    for sg in cmds.listConnections(src_mat, type="shadingEngine") or []:
        try:
            cmds.connectAttr(f"{dst}.outColor", f"{sg}.surfaceShader", f=True)
        except Exception:
            pass

    return dst


def _convert_specular(src, dst):
    """Bidirectional Phong⇌Blinn highlight-shape conversion."""
    s_type, d_type = cmds.nodeType(src), cmds.nodeType(dst)

    def clamp(v, lo, hi): return max(lo, min(hi, v))

    if s_type == "blinn" and d_type == "phong":
        ecc  = cmds.getAttr(f"{src}.eccentricity")
        roll = cmds.getAttr(f"{src}.specularRollOff")
        cos  = clamp(((1.0 - ecc) * (0.4 + roll)) * 120.0, 2.0, 200.0)
        cmds.setAttr(f"{dst}.cosinePower", cos)

    elif s_type == "phong" and d_type == "blinn":
        cos  = cmds.getAttr(f"{src}.cosinePower")
        ecc  = clamp(1.0 - (cos / 120.0),  0.0, 0.999)
        roll = clamp(cos / 170.0,          0.0, 1.0)
        cmds.setAttr(f"{dst}.eccentricity",    ecc)
        cmds.setAttr(f"{dst}.specularRollOff", roll)


def _numeric_attrs(node):
    """Return only the whitelisted numeric/colour plugs for this shader."""
    keep = _WHITELIST.get(cmds.nodeType(node), set())
    return [a for a in keep if cmds.attributeQuery(a, node=node, exists=True)]

def _attr_val(node, attr):
    """Return numeric (rounded to 2 dp) or tuple. Non-numeric → None."""
    try:
        if cmds.attributeQuery(attr, node=node, numberOfChildren=True):
            v = cmds.getAttr(f"{node}.{attr}")[0]
            return tuple(round(x, 2) for x in v)
        v = cmds.getAttr(f"{node}.{attr}")
        if isinstance(v, (bool, int)):
            return v
        if isinstance(v, float):
            return round(v, 2)
    except Exception:
        pass
    return None


def _within_tol(v1, v2, tol):
    """Compare two numeric scalars/tuples; None values never match."""
    if v1 is None or v2 is None:
        return False
    if isinstance(v1, (tuple, list)):
        return all(_within_tol(a, b, tol) for a, b in zip(v1, v2))
    denom = max(abs(v1), 1e-6)
    return abs(v1 - v2) / denom <= tol




from PySide2 import QtWidgets, QtCore
import maya.OpenMayaUI as omui
from shiboken2 import wrapInstance


class MaterialConverterUI(QtWidgets.QDialog):
    WINDOW_NAME = "materialConverterWindow"

    def __init__(self, parent=None):
        # kill old window
        if cmds.window(self.WINDOW_NAME, exists=True):
            cmds.deleteUI(self.WINDOW_NAME)
        main = wrapInstance(int(omui.MQtUtil.mainWindow()), QtWidgets.QWidget)
        super().__init__(main if parent is None else parent)
        self.setObjectName(self.WINDOW_NAME)
        self.setWindowTitle("Material Converter")
        self.setMinimumWidth(520)

        # ==========   WIDGETS   ==========
        title = QtWidgets.QLabel("Material Converter")
        title.setAlignment(QtCore.Qt.AlignCenter)
        title.setStyleSheet("font: bold 14px;")

        hint = QtWidgets.QLabel("select objects to perform actions")
        hint.setAlignment(QtCore.Qt.AlignCenter)
        hint.setStyleSheet("color:#888; font:10px;")

        self.list_btn   = QtWidgets.QPushButton("List Materials")

        self.convert_btn = QtWidgets.QPushButton("Convert Materials")
        self.type_combo = QtWidgets.QComboBox()
        self.type_combo.addItems(["Lambert", "Blinn", "Phong", "Standard Surface (Maya)"])

        self.match_btn  = QtWidgets.QPushButton("List Matching Materials")
        self.merge_btn  = QtWidgets.QPushButton("Merge Matching Materials")
        self.tol_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.tol_slider.setRange(0, 50); self.tol_slider.setValue(10)
        self.tol_label  = QtWidgets.QLabel("Tol 10%")


        self.rename_btn   = QtWidgets.QPushButton("Rename Materials")
        self.prefix_edit  = QtWidgets.QLineEdit(); self.prefix_edit.setPlaceholderText("prefix")
        self.suffix_edit  = QtWidgets.QLineEdit(); self.suffix_edit.setPlaceholderText("suffix")
        self.cb_type  = QtWidgets.QCheckBox("type");  self.cb_type.setChecked(True)
        self.cb_color = QtWidgets.QCheckBox("color"); self.cb_color.setChecked(True)
        self.cb_spec  = QtWidgets.QCheckBox("spec");  self.cb_spec.setChecked(True)
        self.cb_trans = QtWidgets.QCheckBox("glass"); self.cb_trans.setChecked(True)

        self.cb_spec.setToolTip("adds 'shiny' to material name when specular > 30 %")
        self.cb_trans.setToolTip("adds 'glass' to material name when transparency > 30 %")

        self.cleanup_btn = QtWidgets.QPushButton("Remove Duplicate Shading Groups")

        self.output = QtWidgets.QTextEdit(); self.output.setReadOnly(True)

        # ==========   LAYOUT   ==========
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(title); lay.addWidget(hint); lay.addWidget(self.list_btn)

        convert_row = QtWidgets.QHBoxLayout()
        convert_row.addWidget(self.convert_btn); convert_row.addWidget(self.type_combo)
        lay.addLayout(convert_row)

        match_row = QtWidgets.QHBoxLayout()
        match_row.addWidget(self.match_btn)
        match_row.addWidget(self.tol_slider); match_row.addWidget(self.tol_label)
        match_row.addWidget(self.merge_btn)
        lay.addLayout(match_row)

        rename_row1 = QtWidgets.QHBoxLayout()
        rename_row1.addWidget(self.rename_btn)
        rename_row1.addWidget(self.prefix_edit)
        rename_row1.addWidget(self.suffix_edit)
        for cb in (self.cb_type,self.cb_color,self.cb_spec,self.cb_trans):
            rename_row1.addWidget(cb)
        rename_row1.addStretch()
        lay.addLayout(rename_row1)

        lay.addWidget(self.cleanup_btn)  # put it under the rename widgets

        lay.addWidget(self.output, 1)

        # ==========   SIGNALS   ==========
        self.list_btn.clicked.connect(self.list_materials)
        self.convert_btn.clicked.connect(self.convert_materials)
        self.match_btn.clicked.connect(self.list_matching)
        self.merge_btn.clicked.connect(self.merge_matching)
        self.rename_btn.clicked.connect(self.rename_materials)
        self.cleanup_btn.clicked.connect(self.remove_duplicate_sgs)
        self.tol_slider.valueChanged.connect(lambda v:self.tol_label.setText(f"Tol {v}%"))

        # ==========   STYLE   ==========
        self.setStyleSheet("""
            QDialog{background:#2c2c2c; color:#ddd; font:11px "Segoe UI";}
            QPushButton{background:#444; border:1px solid #666; padding:4px 8px;}
            QPushButton:hover{background:#555;}
            QLineEdit{background:#383838; border:1px solid #555; padding:2px;}
            QTextEdit{background:#1b1b1b; border:1px solid #555;}
            QSlider::groove:horizontal{height:4px; background:#555;}
            QSlider::handle:horizontal{width:8px; background:#aaa; margin:-4px 0;}
            QCheckBox{spacing:4px;}
        """)

        self.resize(640, 500); self.show()

    # --------------------------------------------------
    # -----------  BUTTON CALLBACKS  -------------------
    # --------------------------------------------------
    def list_materials(self):
        """Simple dump of shaders on the selection."""
        mats = _gather_materials_from_selection()
        self.output.clear()
        if not mats:
            self.output.setPlainText("No materials on selection.")
            return
        for m in mats:
            self.output.append(f"<b>{m}  ({cmds.nodeType(m)})</b>")
            for a in _numeric_attrs(m):
                self.output.append(f"{a:<18}: {_attr_val(m,a)}")
            self.output.append("")

    def convert_materials(self):
        target_map = {
            "Lambert": "lambert",
            "Blinn": "blinn",
            "Phong": "phong",
            "Standard Surface (Maya)": "standardSurface",
        }
        target = target_map[self.type_combo.currentText()]
        mats = _gather_materials_from_selection()
        cmds.undoInfo(openChunk=True)
        try:
            if not mats: return
            for m in mats:
                new = convert_material(m, target)
                print(f"{m} -> {new}")
        finally:
            cmds.undoInfo(closeChunk=True)
        self.list_materials()

    # -- matching list (uses earlier matching function) --
    def list_matching(self):
        tol=self.tol_slider.value()/100.0
        mats=_gather_materials_from_selection()
        if not mats:
            cmds.warning("No materials on selection."); return

        rainbow=["red","orange","yellow","green","cyan","blue","violet"]
        visited=set(); groups=[]
        for m in mats:
            if m in visited: continue
            attrs=_numeric_attrs(m); dup=[]
            for n in mats:
                if n==m or n in visited or cmds.nodeType(n)!=cmds.nodeType(m): continue
                if all(_within_tol(_attr_val(m,a),_attr_val(n,a),tol) for a in attrs):
                    dup.append(n); visited.add(n)
            groups.append((m,dup)); visited.add(m)

        self.output.clear()
        for rep,dupes in groups:
            tag=f" ({len(dupes)} matching)" if dupes else ""
            self.output.append(f"<b>{rep} ({cmds.nodeType(rep)}){tag}</b>")
            for a in _numeric_attrs(rep):
                line = f"{a:<18}: {_attr_val(rep, a)}"
                for idx,d in enumerate(dupes):
                    col=rainbow[idx%len(rainbow)]
                    line+=f" <font color='{col}'>({_attr_val(d,a)})</font>"
                self.output.append(line)
            self.output.append("")

    # -- merge matching --
    def merge_matching(self):
        """Merge duplicate shaders *and* fold all their SGs into one clean SG."""
        tol = self.tol_slider.value() / 100.0
        mats = _gather_materials_from_selection()
        if not mats:
            cmds.warning("No materials on selection.");
            return

        cmds.undoInfo(openChunk=True)
        try:

            visited, groups = set(), []
            for m in mats:
                if m in visited: continue
                attrs = _numeric_attrs(m)
                dupes = []
                for n in mats:
                    if n == m or n in visited or cmds.nodeType(n) != cmds.nodeType(m):
                        continue
                    if all(_within_tol(_attr_val(m, a), _attr_val(n, a), tol) for a in attrs):
                        dupes.append(n);
                        visited.add(n)
                groups.append((m, dupes));
                visited.add(m)

            for rep, dup_list in groups:
                # choose / create a single representative SG for the rep shader
                rep_sgs = cmds.listConnections(rep, type="shadingEngine") or []
                if rep_sgs:
                    rep_sg = rep_sgs[0]
                else:
                    rep_sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True,
                                       name=f"{rep}_SG")
                    cmds.connectAttr(f"{rep}.outColor", f"{rep_sg}.surfaceShader", f=True)

                # move all geometry from duplicate SGs into rep_sg, then delete dupes + SGs
                for d in dup_list:
                    for sg in cmds.listConnections(d, type="shadingEngine") or []:
                        members = cmds.sets(sg, q=True) or []
                        if members:
                            cmds.sets(members, e=True, forceElement=rep_sg)
                        cmds.delete(sg)  # remove empty SG
                    cmds.delete(d)  # remove duplicate shader
                    print(f"Merged {d} → {rep} (using SG {rep_sg})")
        finally:
            cmds.undoInfo(closeChunk=True)

        self.list_materials()  # refresh panel

    # -- rename materials  --------------------------------------------------
    def rename_materials(self):
        mats = _gather_materials_from_selection()
        if not mats:
            return

        inc_type = self.cb_type.isChecked()
        inc_color = self.cb_color.isChecked()
        inc_spec = self.cb_spec.isChecked()
        inc_trans = self.cb_trans.isChecked()
        prefix = self.prefix_edit.text().strip()
        suffix = self.suffix_edit.text().strip()

        name_counts = collections.Counter()
        cmds.undoInfo(openChunk=True)
        try:
            for m in mats:
                bits = []
                if prefix:
                    bits.append(prefix)
                if inc_type:
                    bits.append(cmds.nodeType(m))
                if inc_color:
                    rgb = _get_material_color_rgb(m)
                    if rgb:
                        bits.append(_color_name(rgb))

                if inc_spec:
                    spec = _attr_val(m, "specularColor") or (0, 0, 0)
                    if sum(spec) / 3.0 > 0.30:
                        bits.append("shiny")
                if inc_trans:
                    trans = _attr_val(m, "transparency") or (0, 0, 0)
                    if sum(trans) / 3.0 > 0.30:
                        bits.append("glass")
                if suffix:
                    bits.append(suffix)

                base = "_".join(bits).replace("__", "_")
                name_counts[base] += 1
                final = base if name_counts[base] == 1 else f"{base}_{name_counts[base] - 1}"

                new_shader = cmds.rename(m, final)

                # ---------- rename connected shadingEngine(s) ----------
                for sg in cmds.listConnections(new_shader, type="shadingEngine") or []:
                    try:
                        cmds.rename(sg, f"{final}_SG")
                    except RuntimeError:
                        # name clash – append count
                        unique = cmds.rename(sg, cmds.incrementName(f"{final}_SG"))
                print(f"{m}  →  {final}   (+ shadingEngine renamed)")
        finally:
            cmds.undoInfo(closeChunk=True)

        self.list_materials()


    def remove_duplicate_sgs(self):
        """Consolidate duplicate SGs on the current selection with verbose output."""
        sel = cmds.ls(sl=True, l=True) or []
        if not sel:
            cmds.warning("Nothing selected.")
            return

        print("\n--- Scanning selection for shading groups ---")
        sg_set = set()
        for obj in sel:
            for shp in cmds.listRelatives(obj, s=True, f=True) or []:
                sg_set.update(cmds.listConnections(shp, type="shadingEngine") or [])

        if not sg_set:
            print("No shading groups found on the selected objects.")
            return

        by_shader = {}
        for sg in sorted(sg_set):
            # source=True because shader -> SG (destination plug)
            shader = cmds.listConnections(sg + ".surfaceShader", s=True, d=False) or []
            shader = shader[0] if shader else "<none>"
            members = cmds.sets(sg, q=True) or []
            conn = cmds.connectionInfo(sg + ".surfaceShader", sfd=True)
            print(f"  {sg:<32}  ->  {shader:<20}  ({len(members):>3} members)  |  {conn}")
            by_shader.setdefault(shader, []).append(sg)

        cmds.undoInfo(openChunk=True)
        try:
            for shader, sg_list in by_shader.items():
                if shader == "<none>" or len(sg_list) < 2:
                    continue
                keep_sg, *dupes = sg_list
                print(f"\nKeeping SG {keep_sg} for shader {shader}")
                for dup in dupes:
                    members = cmds.sets(dup, q=True) or []
                    if members:
                        cmds.sets(members, e=True, forceElement=keep_sg)
                        print(f"  Moved {len(members)} members from {dup} → {keep_sg}")
                    else:
                        print(f"  {dup} was empty")
                    cmds.delete(dup)
                    print(f"  Deleted {dup}")
        finally:
            cmds.undoInfo(closeChunk=True)

        print("\n--- Done. Duplicate shading groups removed. ---\n")



# -------------------------------------------------------
#  Exported helper to show the UI
# -------------------------------------------------------
def show():
    MaterialConverterUI()
