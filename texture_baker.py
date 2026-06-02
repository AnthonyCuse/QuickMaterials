# texture_baker.py
# Maya 2024/2025 — PySide2/6 compatible
# UI to scan selected meshes, list materials, procedural nodes, projection nodes,
# and bake them (including camera-projected textures) to UV-space textures.
# Usage:
#   1) Select mesh(es)
#   2) run: TextureBakeUI.show_dialog()

import os
import re
import maya.cmds as cmds

UI_ACCENT_CYAN = "#00f7c8"

# ---- Qt compatibility ----
try:
    from PySide6 import QtWidgets, QtCore, QtGui
except Exception:
    from PySide2 import QtWidgets, QtCore, QtGui

# Theme: reuse Material Converter base stylesheet. Combos / spin boxes use the default
# style so platform arrows and dropdowns render correctly on Windows / Maya.
TEXTURE_BAKER_WIDGET_STYLESHEET = """
/* Result table */
QTableWidget {
    font-family: 'Segoe UI';
    font-size: 12px;
    color: #ffffff;
    background-color: #1e1e1e;
    alternate-background-color: #252525;
    gridline-color: #444444;
    border: 1px solid #666666;
    border-radius: 6px;
}
QTableWidget::item {
    padding: 4px;
}
QTableWidget::item:selected {
    background-color: #555555;
    color: #ffffff;
}
QHeaderView::section {
    font-family: 'Segoe UI';
    font-size: 11px;
    font-weight: 600;
    color: #ffffff;
    background-color: #3a3a3a;
    border: 1px solid #444444;
    padding: 6px 8px;
}
QHeaderView::section:hover {
    background-color: #444444;
}
/* Splitter handles use per-widget styling (thin grip); keep default transparent */
QSplitter::handle {
    background: transparent;
}
"""


def _texture_baker_stylesheet():
    try:
        from QuickMaterials.material_converter import FALLBACK_STYLESHEET as base
    except Exception:
        try:
            from .material_converter import FALLBACK_STYLESHEET as base
        except Exception:
            base = ""
    return base + TEXTURE_BAKER_WIDGET_STYLESHEET


class _GripSplitter(QtWidgets.QSplitter):
    """Vertical splitter: thin translucent grab bar with centered ellipsis."""

    def createHandle(self):
        handle = super(_GripSplitter, self).createHandle()
        handle.setObjectName("textureBakeSplitterHandle")
        handle.setStyleSheet(
            "#textureBakeSplitterHandle {"
            " background-color: rgba(255, 255, 255, 35);"
            " border: none;"
            " border-radius: 2px;"
            "}"
            "#textureBakeSplitterHandle:hover {"
            " background-color: rgba(0, 247, 200, 55);"
            "}"
        )
        lay = QtWidgets.QHBoxLayout(handle)
        lay.setContentsMargins(12, 0, 12, 0)
        dots = QtWidgets.QLabel("\u2026")  # horizontal ellipsis …
        dots.setAlignment(QtCore.Qt.AlignCenter)
        dots.setStyleSheet(
            "color: rgba(240, 240, 240, 140); font-size: 13px;"
            "background: transparent; padding: 0px;"
        )
        lay.addStretch(1)
        lay.addWidget(dots, 0, QtCore.Qt.AlignCenter)
        lay.addStretch(1)
        return handle


# ------------------ scanning helpers ------------------

PROCEDURAL_TEXTURE_TYPES = {
    "ramp", "fractal", "noise", "checker", "cloth", "cloud", "granite", "leather",
    "marble", "ocean", "snow", "solidFractal", "stucco", "wood", "bulge",
    "brownian", "cylinder", "mountain", "volumeNoise", "water", "wave",
    "aiNoise", "aiCellNoise", "aiFlakes", "aiSky", "aiRampRGB", "aiRampFloat",
    "aiCurvature", "aiAmbientOcclusion",
    "VrayCellular", "VrayNoise", "VrayTriplanarTex", "VrayEdgesTex",
}
# Maya's built-in camera/3D projection utility node.
# Created when you right-click a file node → "As Projection" in the Hypershade.
PROJECTION_TEXTURE_TYPES = {"projection"}

IMAGE_TEXTURE_TYPES = {"file", "aiImage", "psdFileTex", "imagePlane"}
UTILITY_TYPES = {
    "place2dTexture", "place3dTexture", "bump2d", "bump3d", "displacementShader",
    "layeredTexture", "blendColors", "plusMinusAverage", "multiplyDivide",
    "remapValue", "remapColor", "gammaCorrect", "range", "clamp", "reverse",
}
MATERIAL_TYPES = {
    "lambert", "blinn", "phong", "phongE", "surfaceShader", "standardSurface",
    "aiStandardSurface", "aiStandardHair", "aiCarPaint",
    "VRayMtl", "VRayAlSurface", "VRayHairNextMtl",
}
SHADER_PORTS = ("surfaceShader", "miMaterialShader", "aiSurfaceShader")

# Float weight inputs — baked color textures must not be wired here.
_SHADER_WEIGHT_ATTRS = frozenset({
    "base", "specular", "emission", "metalness", "transmission",
    "coat", "sheen", "diffuse", "reflectivity",
})

# Primary diffuse/base plug per shader type (last-resort assign target).
_SHADER_PRIMARY_COLOR_ATTR = {
    "lambert": "color",
    "blinn": "color",
    "phong": "color",
    "phongE": "color",
    "standardSurface": "baseColor",
    "aiStandardSurface": "baseColor",
    "surfaceShader": "outColor",
}


from contextlib import contextmanager

@contextmanager
def maya_undo_chunk(name="TextureBake"):
    cmds.undoInfo(openChunk=True, chunkName=name)
    try:
        yield
    finally:
        cmds.undoInfo(closeChunk=True)


def _short(obj):
    return obj.split("|")[-1] if obj else obj


def _sanitize_bake_filename_part(name):
    """Filesystem-safe token for baked texture filenames."""
    if not name:
        return "texture"
    part = _short(str(name))
    part = os.path.splitext(part)[0]
    part = re.sub(r"[^\w.\-]+", "_", part).strip("._")
    return part or "texture"


def _file_texture_basename(file_node):
    """Prefer the image file basename; fall back to the Maya node name."""
    if not file_node or not cmds.objExists(file_node):
        return None
    try:
        path = cmds.getAttr(file_node + ".fileTextureName") or ""
    except Exception:
        path = ""
    if path:
        base = os.path.splitext(os.path.basename(path))[0]
        if base:
            return _sanitize_bake_filename_part(base)
    return _sanitize_bake_filename_part(_short(file_node))


def _source_texture_basename_for_projection(proj_node):
    """Basename of the image driving a Maya projection node."""
    if not proj_node or not cmds.objExists(proj_node):
        return None
    for attr in ("image",):
        plug = f"{proj_node}.{attr}"
        if not cmds.objExists(plug):
            continue
        src_nodes = cmds.listConnections(plug, s=True, d=False) or []
        for src in src_nodes:
            if cmds.nodeType(src) in IMAGE_TEXTURE_TYPES:
                base = _file_texture_basename(src)
                if base:
                    return base
    try:
        hist = cmds.listHistory(proj_node, pruneDagObjects=True, future=False) or []
    except Exception:
        hist = []
    for node in hist:
        if cmds.nodeType(node) in IMAGE_TEXTURE_TYPES:
            base = _file_texture_basename(node)
            if base:
                return base
    return None


def _bake_output_filename(node, kind, res, fmt):
    """
    Concise output name: <textureOrNode>_<resolution>.<format>
    Projections use the source file texture name when available.
    """
    if kind == "projection":
        base = _source_texture_basename_for_projection(node) or _sanitize_bake_filename_part(_short(node))
    else:
        base = _sanitize_bake_filename_part(_short(node))
    return "%s_%s.%s" % (base, res, fmt.lower())

def _get_selected_shapes():
    sel = cmds.ls(sl=True, long=True) or []
    shapes = []
    for s in sel:
        if cmds.nodeType(s) == "mesh":
            shapes.append(s)
        else:
            shapes.extend(cmds.listRelatives(s, shapes=True, fullPath=True) or [])

    clean = []
    for sh in shapes:
        try:
            if not cmds.getAttr(sh + ".intermediateObject"):
                clean.append(sh)
        except Exception:
            clean.append(sh)

    seen = set(); out = []
    for sh in clean:
        if sh not in seen:
            out.append(sh); seen.add(sh)
    return out


def _sgs_for_shape(shape):
    sgs = cmds.listConnections(shape, type="shadingEngine") or []
    seen = set(); out = []
    for sg in sgs:
        if sg not in seen:
            out.append(sg); seen.add(sg)
    return out

def _material_from_sg(sg):
    for port in SHADER_PORTS:
        plug = f"{sg}.{port}"
        if cmds.objExists(plug):
            mats = cmds.listConnections(plug, d=False, s=True) or []
            if mats:
                return mats[0]
    return None

def _members_of_sg_for_shape(sg, shape):
    members = cmds.sets(sg, q=True) or []
    xform = cmds.listRelatives(shape, p=True, fullPath=True)[0]
    out = []
    for m in members:
        full = cmds.ls(m, long=True) or []
        if full and full[0].startswith(xform):
            out.append(full[0])
    return out

def _walk_upstream_nodes(start_nodes):
    hist = []
    for n in start_nodes:
        h = cmds.listHistory(n, pruneDagObjects=True, future=False) or []
        hist.extend(h)
    seen = set(); out = []
    for n in hist:
        if n not in seen:
            out.append(n); seen.add(n)
    return out

def _categorize_nodes(nodes):
    buckets = {
        "materials": [],
        "procedural_textures": [],
        "projection_nodes": [],      # NEW: Maya 'projection' utility nodes
        "image_textures": [],
        "utilities_misc": [],
        "other": [],
    }
    for n in nodes:
        t = cmds.nodeType(n)
        if t in MATERIAL_TYPES:
            buckets["materials"].append(n)
        elif t in PROJECTION_TEXTURE_TYPES:
            buckets["projection_nodes"].append(n)
        elif t in PROCEDURAL_TEXTURE_TYPES:
            buckets["procedural_textures"].append(n)
        elif t in IMAGE_TEXTURE_TYPES:
            buckets["image_textures"].append(n)
        elif t in UTILITY_TYPES:
            buckets["utilities_misc"].append(n)
        else:
            buckets["other"].append(n)
    return buckets


def _camera_for_projection_node(proj_node):
    """
    Return the camera transform driving a 'projection' node, or None.

    A projection node is wired via a place3dTexture whose worldInverseMatrix
    is connected to the projection node's placementMatrix.  The place3dTexture
    is typically constrained / parented to a camera transform.

    We look for the camera in two ways:
      1) Direct worldInverseMatrix feed into placementMatrix
      2) place3dTexture → parent transform that has a cameraShape child
    """
    # 1) Walk placementMatrix input
    pm_conns = cmds.listConnections(
        proj_node + ".placementMatrix", s=True, d=False, type="place3dTexture"
    ) or []

    for p3d in pm_conns:
        # Does this place3dTexture have a camera transform as parent?
        parents = cmds.listRelatives(p3d, parent=True, fullPath=True) or []
        for par in parents:
            cam_shapes = cmds.listRelatives(par, shapes=True, type="camera", fullPath=True) or []
            if cam_shapes:
                return par   # return the camera transform

        # Alternatively, look at what feeds into the place3dTexture's worldInverseMatrix
        wim_src = cmds.listConnections(
            p3d + ".worldInverseMatrix", s=True, d=False
        ) or []
        for src in wim_src:
            if cmds.nodeType(src) == "camera":
                return cmds.listRelatives(src, parent=True, f=True)[0]
            # src might already be the transform
            shapes = cmds.listRelatives(src, shapes=True, type="camera", fullPath=True) or []
            if shapes:
                return src

    return None   # no camera found — projection may be using a non-camera 3D placement


def collect_materials_and_nodes(mesh_shapes=None):
    if mesh_shapes is None:
        mesh_shapes = _get_selected_shapes()
    report = {}
    for shape in mesh_shapes:
        sgs = _sgs_for_shape(shape)
        report[shape] = {"shadingGroups": {}}
        for sg in sgs:
            mat = _material_from_sg(sg)
            members = _members_of_sg_for_shape(sg, shape)
            upstream = _walk_upstream_nodes([sg, mat] if mat else [sg])
            buckets = _categorize_nodes(upstream)
            report[shape]["shadingGroups"][sg] = {
                "material": mat,
                "assignments": members,
                "network": buckets,
            }
    return report

# ------------------ UI ------------------

class TextureBakeUI(QtWidgets.QDialog):
    _instance = None

    @classmethod
    def show_dialog(cls):
        if cls._instance and cls._instance.isVisible():
            cls._instance.raise_()
            cls._instance.activateWindow()
            return cls._instance

        cls._instance = cls()
        cls._instance.show()
        cls._instance.raise_()
        cls._instance.activateWindow()
        return cls._instance

    def __init__(self, parent=None):
        try:
            import maya.OpenMayaUI as omui
            mw_ptr = omui.MQtUtil.mainWindow()
            if mw_ptr:
                try:
                    from shiboken6 import wrapInstance
                except ImportError:
                    from shiboken2 import wrapInstance
                parent = wrapInstance(int(mw_ptr), QtWidgets.QWidget)
        except Exception:
            pass

        super(TextureBakeUI, self).__init__(parent)
        self.setObjectName("TextureBakeDialog")
        self.setWindowTitle("Texture Baker")
        self.setMinimumWidth(820)
        self.setMinimumHeight(520)

        self._data = {}

        self._build_ui()
        self.setStyleSheet(_texture_baker_stylesheet())
        self._wire()
        self._populate_defaults()
        self.center_on_parent()

    def center_on_parent(self):
        if self.parent() and self.parent().isVisible():
            geo = self.parent().frameGeometry()
            self.move(geo.center() - self.rect().center())

    def _build_ui(self):
        def make_separator(height=1):
            line = QtWidgets.QFrame()
            line.setFrameShape(QtWidgets.QFrame.HLine)
            line.setFrameShadow(QtWidgets.QFrame.Plain)
            line.setLineWidth(1)
            line.setFixedHeight(height)
            line.setStyleSheet("background-color:#333333; border:none;")
            return line

        title = QtWidgets.QLabel("Texture Baker")
        title.setAlignment(QtCore.Qt.AlignCenter)
        title.setStyleSheet("font-weight:600; font-size:16px; padding:2px;")
        title.setToolTip(
            "Scan selected meshes, find procedural or projected texture networks, and bake them to UV textures."
        )

        how_to_widget = QtWidgets.QWidget()
        how_to_layout = QtWidgets.QVBoxLayout(how_to_widget)
        how_to_layout.setContentsMargins(12, 0, 12, 6)
        how_to_layout.setSpacing(4)

        how_to_title = QtWidgets.QLabel("How to Use:")
        how_to_title.setAlignment(QtCore.Qt.AlignCenter)
        how_to_title.setStyleSheet(
            "font-weight:600; font-size:13px; color:#e8e8e8; padding:0px;"
        )

        lbl_how_step1 = QtWidgets.QLabel(
            "Select mesh(es), then scan for procedural or projected textures on their materials."
        )
        lbl_how_step2 = QtWidgets.QLabel(
            "Bake checked rows to flat UV textures. Assign to the existing shader or a duplicated _baked material."
        )
        for lbl in (lbl_how_step1, lbl_how_step2):
            lbl.setAlignment(QtCore.Qt.AlignCenter)
            lbl.setWordWrap(True)
            lbl.setStyleSheet("font-size:12px; color:#aaaaaa; padding:0px;")

        how_to_layout.addWidget(how_to_title)
        how_to_layout.addWidget(lbl_how_step1)
        how_to_layout.addWidget(lbl_how_step2)

        # --- Output settings (placed after table in layout) ---
        output_title = QtWidgets.QLabel("Output settings")
        output_title.setStyleSheet("font-weight:600; margin:0px; color:#ffffff;")

        lbl_res = QtWidgets.QLabel("Resolution:")
        lbl_res.setToolTip("Square bake resolution in pixels for convertSolidTx output.")
        self.cmb_res = QtWidgets.QComboBox()
        self.cmb_res.addItems(["512", "1024", "2048", "4096", "8192"])
        self.cmb_res.setToolTip(
            "Texture width and height. Larger values are sharper but slower and use more disk space."
        )

        lbl_fmt = QtWidgets.QLabel("Format:")
        lbl_fmt.setToolTip("File format written after baking (Maya re-saves from the temporary bake).")
        self.cmb_format = QtWidgets.QComboBox()
        self.cmb_format.addItems(["png", "tif", "exr", "jpg"])
        self.cmb_format.setToolTip(
            "png/tif: common for textures. exr: HDR/linear. jpg: smaller files, lossy."
        )

        lbl_cs = QtWidgets.QLabel("Color space:")
        lbl_cs.setToolTip("Color space set on the file texture when Assign to material is enabled.")
        self.cmb_colorspace = QtWidgets.QComboBox()
        self.cmb_colorspace.addItems(["sRGB", "Raw", "ACEScg"])
        self.cmb_colorspace.setToolTip(
            "sRGB: display-shaped color maps. Raw: linear data (roughness, masks). ACEScg: ACES working space."
        )

        lbl_pad = QtWidgets.QLabel("Padding:")
        lbl_pad.setToolTip("Pixels to extend UV islands during bake to reduce seams (convertSolidTx seam fill).")
        self.spn_padding = QtWidgets.QSpinBox()
        self.spn_padding.setRange(0, 10)
        self.spn_padding.setValue(4)
        self.spn_padding.setToolTip(
            "Higher values bleed baked color slightly across UV seams; 0 disables seam padding where supported."
        )

        output_row = QtWidgets.QHBoxLayout()
        output_row.setContentsMargins(0, 0, 0, 0)
        output_row.setSpacing(6)
        output_row.addWidget(lbl_res)
        output_row.addWidget(self.cmb_res)
        output_row.addWidget(lbl_fmt)
        output_row.addWidget(self.cmb_format)
        output_row.addWidget(lbl_cs)
        output_row.addWidget(self.cmb_colorspace)
        output_row.addWidget(lbl_pad)
        output_row.addWidget(self.spn_padding)
        output_row.addStretch(1)

        lbl_folder = QtWidgets.QLabel("Folder:")
        lbl_folder.setToolTip("Directory where baked images are saved (created if missing when you bake).")
        self.ed_folder = QtWidgets.QLineEdit()
        self.ed_folder.setToolTip(
            "Output directory for baked files. When the scene is saved, defaults to a _textures folder beside the scene file; otherwise the workspace root."
        )
        self.btn_pick_folder = QtWidgets.QToolButton()
        self.btn_pick_folder.setText("…")
        self.btn_pick_folder.setToolTip("Browse for an output folder.")

        folder_row = QtWidgets.QHBoxLayout()
        folder_row.setContentsMargins(0, 0, 0, 0)
        folder_row.setSpacing(6)
        folder_row.addWidget(lbl_folder)
        self.ed_folder.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        folder_row.addWidget(self.ed_folder, 1)
        folder_row.addWidget(self.btn_pick_folder)

        output_frame = QtWidgets.QFrame()
        output_frame.setFrameShape(QtWidgets.QFrame.NoFrame)
        output_frame.setObjectName("textureBakeOutputFrame")
        output_frame.setStyleSheet(
            "QFrame#textureBakeOutputFrame {"
            " background-color:#3a3a3a;"
            " border: 3px solid #444444;"
            " border-radius: 10px;"
            " padding: 5px;"
            " margin: 2px;"
            " color: #ffffff;"
            "}"
        )
        output_inner = QtWidgets.QVBoxLayout(output_frame)
        output_inner.setContentsMargins(10, 8, 10, 10)
        output_inner.setSpacing(5)
        output_inner.addWidget(output_title)
        output_inner.addWidget(make_separator())
        output_inner.addLayout(output_row)
        output_inner.addLayout(folder_row)

        # --- Targets table ---
        self.btn_scan = QtWidgets.QPushButton("Scan Selected Meshes for Procedural Nodes")
        self.btn_scan.setMinimumHeight(26)
        self.btn_scan.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self.btn_scan.setStyleSheet("color: %s; font-weight:600;" % UI_ACCENT_CYAN)
        self.btn_scan.setToolTip(
            "Analyze the current selection for mesh shapes, shading groups, and bakeable procedural or projection nodes."
        )

        scan_row = QtWidgets.QHBoxLayout()
        scan_row.setContentsMargins(0, 0, 0, 0)
        scan_row.setSpacing(0)
        scan_row.addWidget(self.btn_scan, 0, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        scan_row.addStretch(1)

        table_title = QtWidgets.QLabel("Bake targets")
        table_title.setStyleSheet("font-weight:600; margin:0px; color:#ffffff;")
        table_title.setToolTip(
            "One row per mesh shading assignment. Use checkboxes and UV column before clicking Bake Selected."
        )

        targets_title_row = QtWidgets.QHBoxLayout()
        targets_title_row.setContentsMargins(0, 0, 0, 0)
        targets_title_row.setSpacing(8)
        targets_title_row.addWidget(table_title, 0, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        targets_title_row.addStretch(1)

        self.table = QtWidgets.QTableWidget(0, 8)
        _hdr_labels = [
            "Bake",
            "Mesh",
            "SG",
            "Material",
            "Nodes to bake",
            "Kind",
            "Camera",
            "UV set",
        ]
        _hdr_tips = [
            "Include this row when running Bake Selected.",
            "Mesh transform using this shading assignment.",
            "Shading group (shading engine) for this surface.",
            "Surface shader driving this shading group.",
            "Procedural or projection nodes that will be baked to disk.",
            "Whether nodes are procedural-only, projection (camera-based), or mixed.",
            "Camera linked to projection placement (when applicable).",
            "UV set used when evaluating and baking to this mesh.",
        ]
        for col, (label, tip) in enumerate(zip(_hdr_labels, _hdr_tips)):
            hi = QtWidgets.QTableWidgetItem(label)
            hi.setToolTip(tip)
            self.table.setHorizontalHeaderItem(col, hi)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QtWidgets.QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setToolTip(
            "Populated by the scan button above. Each row lists bakeable nodes upstream of the assigned material."
        )

        table_frame = QtWidgets.QFrame()
        table_frame.setFrameShape(QtWidgets.QFrame.NoFrame)
        table_frame.setObjectName("textureBakeTableFrame")
        table_frame.setStyleSheet(
            "QFrame#textureBakeTableFrame {"
            " background-color:#3a3a3a;"
            " border: 3px solid #444444;"
            " border-radius: 10px;"
            " padding: 5px;"
            " margin: 2px;"
            " color: #ffffff;"
            "}"
        )
        table_inner = QtWidgets.QVBoxLayout(table_frame)
        table_inner.setContentsMargins(10, 8, 10, 10)
        table_inner.setSpacing(5)
        table_inner.addLayout(scan_row)
        table_inner.addLayout(targets_title_row)
        table_inner.addWidget(make_separator())
        table_inner.addWidget(self.table, 1)

        # --- Bake options ---
        options_title = QtWidgets.QLabel("Bake options")
        options_title.setStyleSheet("font-weight:600; margin:0px; color:#ffffff;")
        options_title.setToolTip(
            "Dry-run, assign baked textures to existing or duplicated shaders, and bake actions."
        )

        self.chk_dryrun = QtWidgets.QCheckBox("Dry-run (log only)")
        self.chk_dryrun.setChecked(False)
        self.chk_dryrun.setToolTip(
            "Print the bake plan to this Activity log and the Script Editor; no files written and no convertSolidTx execution."
        )

        self.chk_assign_existing = QtWidgets.QCheckBox("Assign to existing material")
        self.chk_assign_existing.setChecked(True)
        self.chk_assign_existing.setToolTip(
            "After baking, disconnect the procedural/projection node on the current shader "
            "and connect a file texture with the baked image."
        )

        self.chk_assign_new = QtWidgets.QCheckBox("Assign to new material (per mesh)")
        self.chk_assign_new.setChecked(False)
        self.chk_assign_new.setToolTip(
            "Duplicate the shader for each mesh, assign the mesh to the copy, "
            "then wire the baked file texture into that new material."
        )

        self._assign_mode_group = QtWidgets.QButtonGroup(self)
        self._assign_mode_group.setExclusive(True)
        self._assign_mode_group.addButton(self.chk_assign_existing, 0)
        self._assign_mode_group.addButton(self.chk_assign_new, 1)

        assign_col = QtWidgets.QVBoxLayout()
        assign_col.setContentsMargins(0, 0, 0, 0)
        assign_col.setSpacing(2)
        assign_col.addWidget(self.chk_assign_existing)
        assign_col.addWidget(self.chk_assign_new)

        opts_row = QtWidgets.QHBoxLayout()
        opts_row.setContentsMargins(0, 0, 0, 0)
        opts_row.setSpacing(8)
        opts_row.addWidget(self.chk_dryrun)
        opts_row.addLayout(assign_col)
        opts_row.addStretch(1)

        self.lbl_bake_hint = QtWidgets.QLabel("This will bake one texture per selected mesh.")
        self.lbl_bake_hint.setStyleSheet("font-style: italic; color: #aaaaaa; font-size: 11px;")
        self.lbl_bake_hint.setWordWrap(True)

        self.btn_bake = QtWidgets.QPushButton("Bake Selected")
        self.btn_bake.setFixedHeight(28)
        self.btn_bake.setMinimumWidth(160)
        self.btn_bake.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self.btn_bake.setStyleSheet("color: %s; font-weight:600;" % UI_ACCENT_CYAN)
        self.btn_bake.setToolTip(
            "Run convertSolidTx for every checked row using the resolution, format, and folder in Output settings."
        )

        self.btn_open_folder = QtWidgets.QPushButton("Open Texture Folder")
        self.btn_open_folder.setToolTip(
            "Open the output folder in Explorer / Finder (if it exists). Shown next to the main bake actions."
        )

        self.btn_close = QtWidgets.QPushButton("Close")
        self.btn_close.setToolTip("Close the Texture Baker window.")

        bake_btn_row = QtWidgets.QHBoxLayout()
        bake_btn_row.setContentsMargins(0, 2, 0, 0)
        bake_btn_row.setSpacing(8)
        bake_btn_row.addWidget(self.btn_bake, 0, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        bake_btn_row.addWidget(self.btn_open_folder, 0, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        bake_btn_row.addStretch(1)
        bake_btn_row.addWidget(self.btn_close, 0, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

        options_frame = QtWidgets.QFrame()
        options_frame.setFrameShape(QtWidgets.QFrame.NoFrame)
        options_frame.setObjectName("textureBakeOptionsFrame")
        options_frame.setStyleSheet(
            "QFrame#textureBakeOptionsFrame {"
            " background-color:#3a3a3a;"
            " border: 3px solid #444444;"
            " border-radius: 10px;"
            " padding: 5px;"
            " margin: 2px;"
            " color: #ffffff;"
            "}"
        )
        options_inner = QtWidgets.QVBoxLayout(options_frame)
        options_inner.setContentsMargins(10, 8, 10, 10)
        options_inner.setSpacing(5)
        options_inner.addWidget(options_title)
        options_inner.addWidget(make_separator())
        options_inner.addLayout(opts_row)
        options_inner.addWidget(self.lbl_bake_hint)
        options_inner.addLayout(bake_btn_row)

        log_title = QtWidgets.QLabel("Activity log")
        log_title.setStyleSheet("font-weight:600; margin:0px; color:#ffffff;")
        log_title.setToolTip("Short summary lines from the last bake or assign step.")

        self.txt_log = QtWidgets.QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMinimumHeight(72)
        self.txt_log.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.txt_log.setToolTip(
            "Read-only messages: bake progress, file paths, and assign warnings. See Script Editor for full detail."
        )

        log_panel = QtWidgets.QWidget()
        log_layout = QtWidgets.QVBoxLayout(log_panel)
        log_layout.setContentsMargins(0, 4, 0, 0)
        log_layout.setSpacing(5)
        log_layout.addWidget(log_title)
        log_layout.addWidget(self.txt_log, 1)

        upper_panel = QtWidgets.QWidget()
        upper_layout = QtWidgets.QVBoxLayout(upper_panel)
        upper_layout.setContentsMargins(0, 0, 0, 4)
        upper_layout.setSpacing(6)
        upper_layout.addWidget(table_frame, 1)
        upper_layout.addWidget(output_frame)
        upper_layout.addWidget(options_frame)

        self._main_splitter = _GripSplitter(QtCore.Qt.Vertical)
        self._main_splitter.setObjectName("textureBakeSplitter")
        self._main_splitter.setChildrenCollapsible(False)
        self._main_splitter.setHandleWidth(5)
        self._main_splitter.addWidget(upper_panel)
        self._main_splitter.addWidget(log_panel)
        self._main_splitter.setStretchFactor(0, 4)
        self._main_splitter.setStretchFactor(1, 1)
        self._main_splitter.setSizes([560, 140])

        outer_column = QtWidgets.QVBoxLayout()
        outer_column.setContentsMargins(6, 6, 6, 6)
        outer_column.setSpacing(5)
        outer_column.addWidget(self._main_splitter, 1)

        outer_frame = QtWidgets.QFrame()
        outer_frame.setObjectName("textureBakeOuterFrame")
        outer_frame.setStyleSheet(
            "QFrame#textureBakeOuterFrame { border: 1px solid #444444; border-radius: 8px; background-color: #333333; }"
        )
        outer_layout = QtWidgets.QVBoxLayout(outer_frame)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)
        outer_layout.addLayout(outer_column)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)
        lay.addWidget(title)
        lay.addWidget(how_to_widget)
        lay.addWidget(outer_frame)

    def _wire(self):
        self.btn_scan.clicked.connect(self.scan_selection)
        self.btn_pick_folder.clicked.connect(self.pick_folder)
        self.btn_bake.clicked.connect(self.bake_selected)
        self.btn_open_folder.clicked.connect(self._open_texture_folder)
        self.btn_close.clicked.connect(self.close)

    def _populate_defaults(self):
        scene = cmds.file(q=True, sn=True) or ""
        base_dir = os.path.dirname(scene) if scene else cmds.workspace(q=True, rd=True)
        out_dir = os.path.join(base_dir, "_textures")
        self.ed_folder.setText(out_dir.replace("\\", "/"))

    def pick_folder(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose Output Folder", self.ed_folder.text())
        if d:
            self.ed_folder.setText(d.replace("\\", "/"))

    def _log(self, msg):
        print(msg)
        if hasattr(self, "txt_log") and self.txt_log:
            self.txt_log.append(msg)

    def _open_texture_folder(self):
        path = self.ed_folder.text().strip()
        if not path:
            cmds.warning("No folder set.")
            return
        if not os.path.exists(path):
            cmds.warning("Folder does not exist yet.")
            return
        try:
            if cmds.about(nt=True):
                os.startfile(path)  # type: ignore
            elif cmds.about(macOS=True):
                import subprocess; subprocess.Popen(["open", path])
            else:
                import subprocess; subprocess.Popen(["xdg-open", path])
        except Exception as e:
            cmds.warning("Could not open folder: %s" % e)

    # --------------- scanning + table ---------------

    def scan_selection(self):
        self.table.setRowCount(0)
        self._data = collect_materials_and_nodes()
        if not self._data:
            cmds.warning("Nothing selected, or no shading networks found.")
            return

        for shape, payload in self._data.items():
            sgs = payload["shadingGroups"]
            if not sgs:
                continue
            for sg, info in sgs.items():
                mat   = info["material"]
                procs = info["network"].get("procedural_textures", [])
                projs = info["network"].get("projection_nodes", [])

                all_bakeable = []

                # Tag each node with its kind so _add_row can display it
                for p in procs:
                    all_bakeable.append({"node": p, "kind": "procedural"})
                for p in projs:
                    cam = _camera_for_projection_node(p)
                    all_bakeable.append({"node": p, "kind": "projection", "camera": cam})

                uv = self._detect_uvset(shape)
                row = self.table.rowCount()
                self.table.insertRow(row)
                self._add_row(row, shape, sg, mat, all_bakeable, uv_set=uv)

    def _detect_uvset(self, shape):
        sets = cmds.polyUVSet(shape, q=True, allUVSets=True) or ["map1"]
        cur  = cmds.polyUVSet(shape, q=True, currentUVSet=True) or ["map1"]
        return cur[0] if cur else sets[0]

    def _add_row(self, row, shape, sg, mat, bakeable_entries, uv_set="map1"):
        """
        bakeable_entries: list of dicts  {"node": str, "kind": "procedural"|"projection", "camera": str|None}
        """
        # ---- col 0: Bake checkbox ----
        chk = QtWidgets.QTableWidgetItem()
        chk.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
        chk.setCheckState(QtCore.Qt.Checked if bakeable_entries else QtCore.Qt.Unchecked)
        chk.setToolTip(
            "Include this row in Bake Selected. Unchecked rows are skipped."
        )
        self.table.setItem(row, 0, chk)

        # ---- col 1: Mesh ----
        mesh_short = _short(
            cmds.listRelatives(shape, p=True, f=False)[0]
            if cmds.listRelatives(shape, p=True) else shape
        )
        mesh_item = QtWidgets.QTableWidgetItem(mesh_short)
        mesh_item.setData(QtCore.Qt.UserRole, shape)
        mesh_item.setToolTip("Mesh transform for this shading assignment.")
        self.table.setItem(row, 1, mesh_item)

        # ---- col 2: SG ----
        sg_item = QtWidgets.QTableWidgetItem(_short(sg) if sg else "—")
        sg_item.setData(QtCore.Qt.UserRole, sg)
        sg_item.setToolTip("Shading group that connects this mesh to the surface shader.")
        self.table.setItem(row, 2, sg_item)

        # ---- col 3: Material ----
        mat_item = QtWidgets.QTableWidgetItem(_short(mat) if mat else "—")
        mat_item.setData(QtCore.Qt.UserRole, mat)
        mat_item.setToolTip("Surface shader assigned to this shading group's surface port.")
        self.table.setItem(row, 3, mat_item)

        # ---- col 4: Nodes label ----
        node_labels = (
            ", ".join(f"{_short(e['node'])}[{cmds.nodeType(e['node'])}]" for e in bakeable_entries)
            if bakeable_entries else "—"
        )
        nodes_item = QtWidgets.QTableWidgetItem(node_labels)
        nodes_item.setData(QtCore.Qt.UserRole, bakeable_entries)   # store the full list of dicts
        nodes_item.setToolTip(
            "Nodes baked via convertSolidTx (outputs flattened to the chosen UV set)."
        )
        self.table.setItem(row, 4, nodes_item)

        # ---- col 5: Kind ----
        kinds = set(e["kind"] for e in bakeable_entries) if bakeable_entries else set()
        if kinds == {"projection"}:
            kind_str = "projection"
        elif kinds == {"procedural"}:
            kind_str = "procedural"
        elif kinds:
            kind_str = "mixed"
        else:
            kind_str = "—"
        kind_item = QtWidgets.QTableWidgetItem(kind_str)
        kind_item.setToolTip("Procedural = 2D/3D procedural textures. Projection = Maya projection utility + placement.")
        self.table.setItem(row, 5, kind_item)

        # ---- col 6: Camera (for projection rows) ----
        cams = list({
            _short(e["camera"]) for e in bakeable_entries
            if e.get("kind") == "projection" and e.get("camera")
        })
        cam_str = ", ".join(cams) if cams else "—"
        cam_item = QtWidgets.QTableWidgetItem(cam_str)
        cam_item.setToolTip(
            "\n".join(
                f"{_short(e['node'])} → {_short(e['camera']) if e.get('camera') else 'no camera linked'}"
                for e in bakeable_entries if e.get("kind") == "projection"
            ) or ""
        )
        self.table.setItem(row, 6, cam_item)

        # ---- col 7: UV Set (editable combobox) ----
        uv_combo = QtWidgets.QComboBox()
        uv_sets = cmds.polyUVSet(shape, q=True, allUVSets=True) or ["map1"]
        uv_combo.addItems(uv_sets)
        if uv_set in uv_sets:
            uv_combo.setCurrentText(uv_set)
        uv_combo.setToolTip(
            "UV set active while baking this row; must exist on the mesh. Choose before baking."
        )
        self.table.setCellWidget(row, 7, uv_combo)

    # --------------- bake orchestration ---------------

    def bake_selected(self):
        out_dir = self.ed_folder.text().strip()
        res = int(self.cmb_res.currentText())
        fmt = self.cmb_format.currentText()
        cspace = self.cmb_colorspace.currentText()
        padding = int(self.spn_padding.value())
        dry = self.chk_dryrun.isChecked()
        assign_existing = self.chk_assign_existing.isChecked()
        assign_new = self.chk_assign_new.isChecked()
        assign = assign_existing or assign_new
        duplicate_material = assign_new

        if not out_dir:
            cmds.warning("Choose an output folder.")
            return

        if not os.path.exists(out_dir):
            try:
                os.makedirs(out_dir)
            except Exception as e:
                cmds.warning("Could not create folder: %s" % e)
                return

        rows = self.table.rowCount()
        if rows == 0:
            cmds.warning("Nothing to bake. Scan selection first.")
            return

        plan_lines = []

        def _plan(msg):
            print(msg)
            plan_lines.append(msg)

        _plan("=" * 80)
        _plan("Texture Bake — Plan")
        _plan("Output: %s" % out_dir)
        _plan(
            "Resolution: %s x %s  | Format: %s | ColorSpace: %s | Padding: %spx"
            % (res, res, fmt, cspace, padding)
        )
        if assign_new:
            assign_mode = "new material per mesh"
        elif assign_existing:
            assign_mode = "existing material"
        else:
            assign_mode = "no"
        _plan("Assign after bake: %s" % assign_mode)
        _plan("-" * 80)

        jobs = []

        for r in range(rows):
            chk = self.table.item(r, 0)
            if not chk or chk.checkState() != QtCore.Qt.Checked:
                continue

            shape = self.table.item(r, 1).data(QtCore.Qt.UserRole)
            sg = self.table.item(r, 2).data(QtCore.Qt.UserRole)
            mat = self.table.item(r, 3).data(QtCore.Qt.UserRole)
            entries = self.table.item(r, 4).data(QtCore.Qt.UserRole) or []
            uv_combo = self.table.cellWidget(r, 7)
            uvset = uv_combo.currentText() if isinstance(uv_combo, QtWidgets.QComboBox) else "map1"

            if not entries:
                mesh_name = _short(
                    cmds.listRelatives(shape, p=True, f=False)[0]
                    if cmds.listRelatives(shape, p=True)
                    else shape
                )
                _plan("[skip] %s — no bakeable nodes." % mesh_name)
                continue

            mesh_name = _short(
                cmds.listRelatives(shape, p=True, f=False)[0]
                if cmds.listRelatives(shape, p=True)
                else shape
            )

            for entry in entries:
                node = entry["node"]
                kind = entry["kind"]
                camera = entry.get("camera")
                node_type = cmds.nodeType(node)

                filename = _bake_output_filename(node, kind, res, fmt)
                out_path = os.path.join(out_dir, filename).replace("\\", "/")

                cam_label = "  Camera=%s" % _short(camera) if camera else ""
                _plan(
                    "[plan] Kind=%s  Mesh=%s  SG=%s  Mat=%s%s"
                    % (kind, mesh_name, _short(sg) if sg else "—", _short(mat) if mat else "—", cam_label)
                )
                _plan("       Node=%s[%s]  UV=%s  ->  %s" % (_short(node), node_type, uvset, out_path))

                src_plug = self._guess_output_attr(node)
                dest_plugs = cmds.listConnections(src_plug, plugs=True, s=False, d=True) or []

                jobs.append({
                    "shape": shape,
                    "mesh": mesh_name,
                    "sg": sg,
                    "material": mat,
                    "proc": node,
                    "kind": kind,
                    "camera": camera,
                    "src_plug": src_plug,
                    "dest_plugs": dest_plugs,
                    "proc_type": node_type,
                    "uv_set": uvset,
                    "out_path": out_path,
                    "res": res,
                    "fmt": fmt,
                    "colorspace": cspace,
                    "padding": padding,
                })

        if not jobs:
            self.txt_log.clear()
            self._log("No jobs to bake. Check rows in the table or run Scan Selection.")
            return

        if dry:
            self.txt_log.clear()
            for line in plan_lines:
                self._log(line)
            self._log("—" * 40)
            if assign_new:
                self._log(
                    "Assign to new material: each job will duplicate the shader per mesh before wiring "
                    "(skipped in dry-run)."
                )
            elif assign_existing:
                self._log(
                    "Assign to existing material: baked textures wire into the current shader "
                    "(skipped in dry-run)."
                )
            self._log("Dry-run only — no scene changes and no files written. Uncheck 'Dry-run' to execute.")
            return

        ok, failed = [], []

        with maya_undo_chunk("TextureBakeAll"):
            for j in jobs:
                j["all_targets"] = [j]

            if assign:
                for j in jobs:
                    if not j.get("material") or not cmds.objExists(j["material"]):
                        if j.get("sg") and cmds.objExists(j["sg"]):
                            for port in ("surfaceShader", "miMaterialShader", "aiSurfaceShader"):
                                plug = "%s.%s" % (j["sg"], port)
                                if cmds.objExists(plug):
                                    mats = cmds.listConnections(plug, s=True, d=False)
                                    if mats:
                                        j["material"] = mats[0]
                                        break
                    if duplicate_material and j.get("material") and j.get("sg"):
                        j["source_material"] = j["material"]
                        new_mat, new_sg = self._duplicate_material_for_mesh(
                            j["material"], j["sg"], j["mesh"], j["shape"]
                        )
                        j["material"] = new_mat
                        j["sg"] = new_sg

            for j in jobs:
                j["assign_to_material"] = assign
                j["duplicate_per_mesh"] = duplicate_material

            exec_msg = "Executing bake…"
            print(exec_msg)
            self._log(exec_msg)
            ok, failed = self.perform_bake(jobs)
            print("Done. OK: %s   Failed: %s" % (len(ok), len(failed)))

        for f in failed:
            print("  -", f.get("error", "Unknown error"))
        print("=" * 80)
        self._log(
            "OK: %s  Failed: %s  → %s"
            % (len(ok), len(failed), self.ed_folder.text().strip())
        )

    def _guess_output_attr(self, node):
        """Return the best output plug to bake for any texture/projection/procedural node."""
        # projection nodes expose 'outColor' which already resolves the full projected result
        for attr in ("outColor", "outValue", "outAlpha"):
            plug = f"{node}.{attr}"
            if cmds.objExists(plug):
                return plug
        for attr in ("outColorR", "outColorG", "outColorB", "out"):
            plug = f"{node}.{attr}"
            if cmds.objExists(plug):
                return plug
        raise RuntimeError("No bakeable output found on node: %s" % node)

    def _resave_image(self, src_path, dst_path, fmt):
        from maya import OpenMaya as om
        fmt = fmt.lower()
        img = om.MImage()
        img.readFromFile(src_path)
        if fmt == "exr":
            try:
                img.setPixels(img.floatPixels(), img.width(), img.height())
            except Exception:
                pass
        img.writeToFile(dst_path, fmt)
        if not os.path.exists(dst_path) or os.path.getsize(dst_path) == 0:
            raise RuntimeError("Re-save failed or produced empty file: %s" % dst_path)

    def _retarget_temp_file_nodes(self, temp_iff_path, final_path):
        file_nodes = cmds.ls(type="file") or []
        for fn in file_nodes:
            try:
                tex = cmds.getAttr(fn + ".fileTextureName")
            except Exception:
                continue
            if tex and os.path.normpath(tex) == os.path.normpath(temp_iff_path):
                try:
                    cmds.setAttr(fn + ".fileTextureName", final_path, type="string")
                except Exception:
                    pass

    @staticmethod
    def _is_shader_weight_attr(attr):
        """True for float weight plugs (e.g. standardSurface.base), not color inputs."""
        if not attr:
            return False
        return attr in _SHADER_WEIGHT_ATTRS

    @staticmethod
    def _same_shader_node(node_a, node_b):
        if not node_a or not node_b:
            return False
        return node_a == node_b or _short(node_a) == _short(node_b)

    def _shader_color_input_attrs(self, material):
        """Color attributes on this shader that may receive a baked texture."""
        t = cmds.nodeType(material)
        by_type = {
            "lambert": ("color",),
            "blinn": ("color", "specularColor", "incandescence", "reflectedColor", "ambientColor"),
            "phong": ("color", "specularColor", "incandescence", "reflectedColor", "ambientColor"),
            "phongE": ("color", "specularColor", "incandescence"),
            "standardSurface": (
                "baseColor", "specularColor", "emissionColor",
                "transmissionColor", "coatColor", "sheenColor",
            ),
            "aiStandardSurface": (
                "baseColor", "specularColor", "emissionColor",
                "transmissionColor", "coatColor", "sheenColor",
            ),
            "surfaceShader": ("outColor",),
        }
        attrs = by_type.get(t)
        if attrs:
            return tuple(
                a for a in attrs if cmds.objExists(f"{material}.{a}")
            )
        for a in ("baseColor", "color", "diffuseColor", "outColor"):
            if cmds.objExists(f"{material}.{a}"):
                return (a,)
        return ()

    def _primary_shader_color_attr(self, material):
        t = cmds.nodeType(material)
        primary = _SHADER_PRIMARY_COLOR_ATTR.get(t)
        if primary and cmds.objExists(f"{material}.{primary}"):
            return primary
        attrs = self._shader_color_input_attrs(material)
        return attrs[0] if attrs else None

    def _material_attrs_driven_by_texture(self, proc_node, material):
        """Material color inputs whose upstream network includes proc_node."""
        if not proc_node or not material or not cmds.objExists(material):
            return []
        driven = []
        for attr in self._shader_color_input_attrs(material):
            plug = f"{material}.{attr}"
            try:
                upstream = cmds.listHistory(plug, pruneDagObjects=True, future=False) or []
            except Exception:
                upstream = []
            if proc_node in upstream:
                driven.append(attr)
        return driven

    def _resolve_bake_assign_dests(self, dest_plugs, target_mat, proc_node=None):
        """
        Resolve where to wire the baked file on target_mat.
        Uses upstream history so utilities between texture and shader are handled.
        Never fans out to every color channel on the shader.
        """
        if not target_mat or not cmds.objExists(target_mat):
            return []

        driven = (
            self._material_attrs_driven_by_texture(proc_node, target_mat)
            if proc_node else []
        )
        driven_set = set(driven)

        seen = set()
        out = []

        for plug in dest_plugs or []:
            if "." not in plug:
                continue
            node, attr = plug.split(".", 1)
            if not self._same_shader_node(node, target_mat):
                continue
            if self._is_shader_weight_attr(attr):
                continue
            if driven_set and attr not in driven_set:
                continue
            dst = f"{target_mat}.{attr}"
            if dst in seen or not cmds.objExists(dst):
                continue
            seen.add(dst)
            out.append(dst)

        if not out and driven:
            for attr in driven:
                dst = f"{target_mat}.{attr}"
                if dst in seen or not cmds.objExists(dst):
                    continue
                seen.add(dst)
                out.append(dst)

        if not out:
            primary = self._primary_shader_color_attr(target_mat)
            if primary:
                dst = f"{target_mat}.{primary}"
                if cmds.objExists(dst):
                    out.append(dst)
        return out

    def _assign_baked_into_network(self, job, texture_path):
        proc_plug  = job["src_plug"]
        recorded   = job.get("dest_plugs", [])
        all_targets = job.get("all_targets", [job])

        base_dests = recorded or (cmds.listConnections(proc_plug, plugs=True, s=False, d=True) or [])

        file_node = self._ensure_file_node_for(texture_path, colorspace=job.get("colorspace", "sRGB"))

        for dst in base_dests:
            try:
                cmds.disconnectAttr(proc_plug, dst)
            except Exception:
                pass

        for tgt in all_targets:
            target_mat = tgt.get("material")
            if not target_mat or not cmds.objExists(target_mat):
                continue

            remapped = self._resolve_bake_assign_dests(
                base_dests, target_mat, proc_node=job.get("proc")
            )

            for dst_attr in remapped:
                self._try_connect_texture(file_node, dst_attr)

            for dst_attr in remapped:
                self._log(f"Assigned {os.path.basename(texture_path)} → {dst_attr} (mat {target_mat})")

    def _ensure_file_node_for(self, path, colorspace="sRGB"):
        for fn in cmds.ls(type="file") or []:
            try:
                if os.path.normpath(cmds.getAttr(fn + ".fileTextureName")) == os.path.normpath(path):
                    return fn
            except Exception:
                continue
        fn = cmds.shadingNode("file", asTexture=True, isColorManaged=True)
        try:
            cmds.setAttr(fn + ".fileTextureName", path, type="string")
        except Exception:
            pass
        if cmds.objExists(fn + ".colorSpace"):
            try:
                cmds.setAttr(fn + ".colorSpace", colorspace, type="string")
            except Exception:
                pass
        if not cmds.listConnections(fn + ".uvCoord", s=True, d=False):
            p2d = cmds.shadingNode("place2dTexture", asUtility=True)
            for a in ("coverage", "translateFrame", "rotateFrame", "mirrorU", "mirrorV", "stagger",
                      "wrapU", "wrapV", "repeatUV", "offset", "rotateUV", "noiseUV",
                      "vertexUvOne", "vertexUvTwo", "vertexUvThree", "vertexCameraOne",
                      "outUV", "outUvFilterSize"):
                src = f"{p2d}.{a}"
                dst = (f"{fn}.uvCoord"    if a == "outUV" else
                       f"{fn}.uvFilterSize" if a == "outUvFilterSize" else
                       f"{fn}.{a}")
                try:
                    cmds.connectAttr(src, dst, f=True)
                except Exception:
                    pass
        return fn

    def _try_connect_texture(self, file_node, dst_attr):
        try:
            cmds.connectAttr(file_node + ".outColor", dst_attr, f=True)
            return True
        except Exception:
            pass
        for ch in (".outAlpha", ".outColorR"):
            try:
                cmds.connectAttr(file_node + ch, dst_attr, f=True)
                return True
            except Exception:
                continue
        return False

    def _connect_material_to_sg(self, material, sg):
        for port in ("surfaceShader", "miMaterialShader", "aiSurfaceShader"):
            plug = f"{sg}.{port}"
            if cmds.objExists(plug):
                try:
                    old = cmds.listConnections(plug, s=True, d=False, plugs=True) or []
                    for o in old:
                        try: cmds.disconnectAttr(o, plug)
                        except Exception: pass
                    out_attr = material + ".outColor" if cmds.objExists(material + ".outColor") else material
                    cmds.connectAttr(out_attr, plug, f=True)
                    return
                except Exception:
                    continue

    def _duplicate_material_for_mesh(self, material, sg, mesh_name, shape):
        if not material or not cmds.objExists(material):
            if sg and cmds.objExists(sg):
                for port in ("surfaceShader", "miMaterialShader", "aiSurfaceShader"):
                    plug = f"{sg}.{port}"
                    if cmds.objExists(plug):
                        src = cmds.listConnections(plug, s=True, d=False)
                        if src: material = src[0]; break
        if not material or not cmds.objExists(material):
            raise RuntimeError("Cannot resolve material to duplicate for mesh '%s'." % mesh_name)

        mat_type      = cmds.nodeType(material)
        base_new_name = f"{_short(material)}_baked".replace(":", "_")

        new_mat = None
        try:
            dup = cmds.duplicate(material, rr=True, ic=True)
            if dup:
                try:    new_mat = cmds.rename(dup[0], base_new_name)
                except: new_mat = dup[0]
        except Exception:
            new_mat = None

        if not new_mat or not cmds.objExists(new_mat):
            try:
                new_mat = cmds.shadingNode(mat_type, asShader=True, name=base_new_name)
            except Exception:
                new_mat = cmds.shadingNode("aiStandardSurface", asShader=True, name=base_new_name)

            try:
                attrs = cmds.listAttr(material, k=True) or []
                for a in attrs:
                    src_plug = f"{material}.{a}"
                    dst_plug = f"{new_mat}.{a}"
                    if cmds.objExists(dst_plug) and not cmds.listConnections(src_plug, s=True, d=False):
                        try:
                            val = cmds.getAttr(src_plug)
                            if isinstance(val, (list, tuple)):
                                cmds.setAttr(dst_plug, *val,
                                             type="double3" if len(val) == 3 else None)
                            else:
                                cmds.setAttr(dst_plug, val)
                        except Exception:
                            pass
            except Exception:
                pass

        try:
            new_sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True,
                               name=f"{new_mat}SG")
        except Exception:
            new_sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True)
        self._connect_material_to_sg(new_mat, new_sg)

        xform = cmds.listRelatives(shape, p=True, f=True)
        xform = xform[0] if xform else shape
        try:
            cmds.sets(xform, e=True, forceElement=new_sg)
        except Exception:
            pass

        self._log(
            f"Per-mesh material: {material} ➜ {new_mat} (type {mat_type}); "
            f"SG ➜ {new_sg}; assigned to {mesh_name}"
        )
        return new_mat, new_sg

    def perform_bake(self, jobs):
        ok, failed = [], []
        for j in jobs:
            try:
                shape = j["shape"]
                proc  = j["proc"]
                kind  = j.get("kind", "procedural")

                if not cmds.objExists(shape) or not cmds.objExists(proc):
                    j["error"] = "Missing node(s): %s or %s" % (shape, proc)
                    failed.append(j); continue

                # For projection nodes, log the camera being baked
                if kind == "projection":
                    cam = j.get("camera")
                    self._log(
                        f"Baking projection node '{_short(proc)}'"
                        + (f" (camera: {_short(cam)})" if cam else " (no camera linked — using 3D placement)")
                    )

                # Set the correct UV set
                uv_set = j.get("uv_set") or "map1"
                try:
                    cmds.polyUVSet(shape, e=True, currentUVSet=True, uvSet=uv_set)
                except Exception:
                    pass

                src_plug = self._guess_output_attr(proc)

                dst_final = j["out_path"]
                temp_iff  = os.path.splitext(dst_final)[0] + "__TMP.iff"

                kw_safe = dict(
                    fileImageName=temp_iff,
                    antiAlias=True,
                    resolutionX=int(j.get("res", 1024)),
                    resolutionY=int(j.get("res", 1024)),
                    uvSetName=j.get("uv_set", "map1"),
                )

                # convertSolidTx fully resolves 'projection' nodes (including camera matrix)
                # at evaluation time, so no special handling is needed vs procedural nodes.
                try:
                    kw_safe["fillTextureSeams"] = int(j.get("padding", 4))
                    cmds.convertSolidTx(src_plug, shape, **kw_safe)
                except RuntimeError:
                    kw_safe.pop("fillTextureSeams", None)
                    kw_safe["seamPixelPadding"] = int(j.get("padding", 4))
                    cmds.convertSolidTx(src_plug, shape, **kw_safe)

                if not os.path.exists(temp_iff) or os.path.getsize(temp_iff) == 0:
                    raise RuntimeError("convertSolidTx produced no data: %s" % temp_iff)

                self._resave_image(temp_iff, dst_final, j["fmt"])
                self._retarget_temp_file_nodes(temp_iff, dst_final)
                self._log(f"Created: {dst_final}")

                try:
                    os.remove(temp_iff)
                except Exception:
                    pass

                if j.get("assign_to_material"):
                    try:
                        self._assign_baked_into_network(j, dst_final)
                    except Exception as assign_err:
                        j["error"] = f"Assign warning: {assign_err}"

                ok.append(j)

            except Exception as e:
                j["error"] = str(e)
                failed.append(j)
        return ok, failed


# --------------- quick entrypoint ---------------

def show():
    return TextureBakeUI.show_dialog()