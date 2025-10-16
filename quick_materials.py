import os
import colorsys  # For HSV to RGB conversion
# --- Qt compatibility for Maya 2024 (PySide2) & Maya 2025 (PySide6) ---
try:
    # Maya 2025+
    from PySide6 import QtCore, QtUiTools, QtWidgets, QtGui
    from shiboken6 import wrapInstance, isValid
    QT_LIB = 6
except ImportError:
    # Maya 2024-
    from PySide2 import QtCore, QtUiTools, QtWidgets, QtGui
    from shiboken2 import wrapInstance, isValid
    QT_LIB = 2
# ----------------------------------------------------------------------


from functools import partial

import time
import maya.mel as mel
import maya.cmds as cmds
import maya.utils as mutils

import maya.OpenMayaUI as omui  # type: ignore
from maya.app.general.mayaMixin import MayaQWidgetDockableMixin

import random
import re
import importlib
import weakref  # guarded owner refs when QPointer is unavailable


# Import Material Converter
import QuickMaterials.material_converter
importlib.reload(QuickMaterials.material_converter)

# Texture Importer import (package-relative with robust fallback)
try:
    # Prefer package-relative import when available
    from . import texture_importer  # type: ignore
except Exception:
    # Fallback: load the sibling file by absolute path
    import sys
    import importlib.util
    MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
    _tex_path = os.path.join(MODULE_DIR, "texture_importer.py")
    _spec = importlib.util.spec_from_file_location("QuickMaterials.texture_importer", _tex_path)
    if _spec is None or _spec.loader is None:
        raise ImportError("Failed to locate texture_importer.py next to quick_materials.py")
    texture_importer = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(texture_importer)
    # Ensure it’s visible under both names in sys.modules for later imports/reloads
    sys.modules.setdefault("texture_importer", texture_importer)
    sys.modules.setdefault("QuickMaterials.texture_importer", texture_importer)
# Optionally reload during interactive dev
importlib.reload(texture_importer)
ImportTxTool = texture_importer.ImportTxTool
# ------------------------------------------------------------------------------


class LiveWidgetDict(dict):
    """Dictionary that auto-refreshes PySide widgets that may have been rebuilt."""

    def __init__(self, owner):
        super(LiveWidgetDict, self).__init__()
        self._owner = owner                  # QuickMaterialsUI instance

    def _refresh(self, name):
        w = super().get(name)
        if w is None or not isValid(w):
            try:
                owner_ok = isValid(self._owner)
            except Exception:
                owner_ok = bool(self._owner)
            if not owner_ok:
                return None  # owner was deleted; don’t deref findChild
            w = self._owner.findChild(QtWidgets.QWidget, name)
            if w:
                super().__setitem__(name, w)
        return w



    # [].  access
    def __getitem__(self, name):
        return self._refresh(name)

    # .get() access
    def get(self, name, default=None):
        return self._refresh(name) or default

class QMFlowLayout(QtWidgets.QLayout):
    """Minimal flow layout that wraps items to the next row when width is tight."""
    def __init__(self, parent=None, margin=2, hspacing=4, vspacing=4):
        super(QMFlowLayout, self).__init__(parent)
        self._items = []
        self.setContentsMargins(margin, margin, margin, margin)
        self._hspace = hspacing
        self._vspace = vspacing

    def addItem(self, item): self._items.append(item)
    def count(self): return len(self._items)
    def itemAt(self, i): return self._items[i] if 0 <= i < len(self._items) else None
    def takeAt(self, i): return self._items.pop(i) if 0 <= i < len(self._items) else None
    def expandingDirections(self): return QtCore.Qt.Orientations(0)
    def hasHeightForWidth(self): return True

    def heightForWidth(self, w):
        # Guard in case something weird calls this with a wrong 'self'
        if not hasattr(self, "_items"):
            return 0
        return self._do_layout(QtCore.QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):
        super(QMFlowLayout, self).setGeometry(rect)
        if hasattr(self, "_items"):
            self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        if not hasattr(self, "_items"):
            return QtCore.QSize(0, 0)
        size = QtCore.QSize()
        for i in self._items:
            size = size.expandedTo(i.minimumSize())
        m = self.contentsMargins()
        size += QtCore.QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect, test_only):
        x, y = rect.x(), rect.y()
        line_height = 0
        m = self.contentsMargins()
        effective_rect = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = effective_rect.x()
        y = effective_rect.y()
        max_w = effective_rect.right()
        for item in self._items:
            iw = item.sizeHint().width()
            ih = item.sizeHint().height()
            if x + iw > max_w and line_height > 0:
                x = effective_rect.x()
                y = y + line_height + self._vspace
                line_height = 0
            if not test_only:
                item.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), item.sizeHint()))
            x = x + iw + self._hspace
            line_height = max(line_height, ih)
        return y + line_height - rect.y()


class ClickableColorSwatch(QtWidgets.QWidget):
    def __init__(self, color_hex, on_clicked, parent=None):
        super(ClickableColorSwatch, self).__init__(parent)
        self._color_hex = color_hex
        self._on_clicked = on_clicked
        self._selected = False
        self._disabled = False  # used for default materials
        self.setFixedSize(14, 14)
        self.setCursor(QtCore.Qt.ArrowCursor)  # not clickable
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.setAutoFillBackground(True)
        # Never outline; just show color with a neutral frame
        self._apply_style(hover=False)

    def setDisabledSelection(self, disabled=True):
        """Disable selection (used for default materials)."""
        self._disabled = bool(disabled)
        self.setCursor(QtCore.Qt.ArrowCursor if self._disabled else QtCore.Qt.PointingHandCursor)
        self._apply_style(hover=False)

    def isSelected(self):
        return self._selected

    def setSelected(self, selected):
        self._selected = bool(selected)
        self._apply_style(hover=False)

    def _apply_style(self, hover):
        # Subtle hover outline; darker border if disabled
        border = "#2b2b2b" if self._disabled else ("#ffffff" if hover else "#333333")
        self.setStyleSheet(f"background-color: {self._color_hex}; border: 1px solid {border};")

    def enterEvent(self, e):
        QtWidgets.QWidget.enterEvent(self, e)  # no hover styling

    def leaveEvent(self, e):
        QtWidgets.QWidget.leaveEvent(self, e)  # no hover styling


    def mousePressEvent(self, e):
        # Swatches are non-interactive; only reflect selection visually
        QtWidgets.QWidget.mousePressEvent(self, e)  # let QWidget handle base behavior


class LeftClipLineEdit(QtWidgets.QLineEdit):
    """
    QLineEdit that:
      • Clips text on the right, always showing the left edge
      • Is read-only by default, becomes editable on double-click
    """
    def __init__(self, text="", parent=None):
        super(LeftClipLineEdit, self).__init__(text, parent)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)  # ensure QSS bg/radius render
        self.setAutoFillBackground(True)  # optional; helps in some Maya themes
        self.setAlignment(QtCore.Qt.AlignLeft)
        self.setMinimumWidth(50)          # allow aggressive shrink
        self.setMinimumHeight(22)         # avoid vertical clipping
        self.setReadOnly(True)            # lock by default
        # selection callback wiring
        self._selection_handler = None
        self._qm_material_name = None
        self.setProperty("editing", "false")  # QSS flag: not editing initially




        # timer to keep text snapped to the left on focus/resize
        self._snap_left_timer = QtCore.QTimer(self)
        self._snap_left_timer.setSingleShot(True)
        self._snap_left_timer.timeout.connect(self._snap_to_left)


    def contextMenuEvent(self, event):
        """Right-click menu with material actions."""
        # Resolve owner (QuickMaterialsUI) safely
        owner = self._owner_ref() if getattr(self, "_owner_ref", None) else None

        if not (owner and isValid(owner)):
            # Owner gone; suppress menu to avoid calling into dead objects
            return


        # Current material name (use live text so it works after a rename)
        mat = self.text().strip()

        # Build the menu
        menu = QtWidgets.QMenu(self)
        act_assign = menu.addAction("Assign")
        act_select = menu.addAction("Select Objs")
        act_graph  = menu.addAction("Graph")
        act_imp_tx = menu.addAction("Imp Tx")

        # Disable Import Tx for default materials (matches button behavior)
        try:
            is_default = (self.property("materialType") == "default")
        except Exception:
            is_default = False
        act_imp_tx.setEnabled(not is_default)

        # Wire actions to existing QuickMaterialsUI methods
        def _safe_call(fn_name, *args, **kwargs):
            try:
                fn = getattr(owner, fn_name, None)
                if callable(fn):
                    fn(*args, **kwargs)
            except Exception as e:
                print(f"[QM][CTX] {fn_name} failed: {e}")

        act_assign.triggered.connect(lambda: _safe_call("assign_material", mat))
        act_select.triggered.connect(lambda: _safe_call("highlight_material", mat))
        act_graph.triggered.connect(lambda: _safe_call("graph_material_network", mat))
        act_imp_tx.triggered.connect(lambda: _safe_call("import_tx_material", mat))

        # Show the menu (PySide2 vs PySide6 difference)
        try:
            if QT_LIB == 6:
                menu.exec(event.globalPos())
            else:
                menu.exec_(event.globalPos())
        except Exception as e:
            print(f"[QM][CTX] menu failed: {e}")





    def mouseDoubleClickEvent(self, e):
        """
        Double-click toggles edit mode:
          • If read-only → enter edit mode.
          • If editing   → exit edit mode (same as clicking off).
        """
        # Ignore editing for default materials
        if self.property("materialType") == "default":
            QtWidgets.QLineEdit.mouseDoubleClickEvent(self, e)
            return

        if self.isReadOnly():
            # Enter edit mode
            print(f"[QM][LineEdit] double-click → editable: {self.text()}")
            # Remember the pre-edit name so rename() has the correct "from" value
            try:
                self._pre_edit_text = self.text()
            except Exception:
                self._pre_edit_text = self.text()
            self.setReadOnly(False)
            self.setProperty("editing", "true")
            self.style().unpolish(self); self.style().polish(self); self.update()
        else:
            # Exit edit mode (editingFinished will handle rename on focus change)
            print(f"[QM][LineEdit] double-click → lock: {self.text()}")
            self.setReadOnly(True)
            self.setProperty("editing", "false")
            self.style().unpolish(self); self.style().polish(self); self.update()


        QtWidgets.QLineEdit.mouseDoubleClickEvent(self, e)  # explicit base call





    # Hook a selection handler from the parent UI
    # New API: setSelectionHandler(owner, handler_name: str, material_name)
    # Back-compat: setSelectionHandler(callable, material_name)
    def setSelectionHandler(self, owner_or_callable, handler_name_or_material, maybe_material=None):
        # New signature: (owner, "handle_item_click", material)
        if isinstance(handler_name_or_material, str) and maybe_material is not None:
            owner = owner_or_callable
            # store a weakref to the UI; we’ll validate with shiboken.isValid at call-time
            self._owner_ref = weakref.ref(owner) if owner is not None else None
            self._handler_name = handler_name_or_material
            self._bound_handler = None
            self._qm_material_name = maybe_material
            return

        # Back-compat signature: (callable, material)
        callable_handler = owner_or_callable
        material_name = handler_name_or_material
        self._owner_ref = None
        self._handler_name = None
        self._bound_handler = callable_handler
        self._qm_material_name = material_name



    def mousePressEvent(self, e):
        """
        Single-click behavior:
          • If currently editing (readOnly == False), treat as a normal QLineEdit click (no list selection).
          • If not editing, forward to the selection handler (single/cmd/shift selection).
        """
        try:
            # If we're already editing, do NOT call selection handler; just let QLineEdit handle it.
            if not self.isReadOnly():
                QtWidgets.QLineEdit.mousePressEvent(self, e)
                return

            # Not editing → behave like a list row click (selection logic)
            mods = e.modifiers()
            shift = bool(mods & QtCore.Qt.ShiftModifier)
            ctrl = bool(mods & (QtCore.Qt.ControlModifier | QtCore.Qt.MetaModifier))

            # Resolve owner via weakref and validate with shiboken.isValid
            if self._owner_ref is not None and self._handler_name and self._qm_material_name:
                owner = self._owner_ref() if callable(self._owner_ref) else None
                _is_valid = None
                try:
                    from shiboken6 import isValid as _is_valid
                except Exception:
                    try:
                        from shiboken2 import isValid as _is_valid
                    except Exception:
                        _is_valid = lambda obj: bool(obj)
                if owner is not None and _is_valid(owner):
                    handler = getattr(owner, self._handler_name, None)
                    if callable(handler):
                        handler(self._qm_material_name, source='lineedit', shift=shift, ctrl=ctrl)
                        QtWidgets.QLineEdit.mousePressEvent(self, e)
                        return

            # Back-compat: direct callable if we didn’t get an owner/method
            if self._bound_handler and self._qm_material_name:
                try:
                    handler_owner = getattr(self._bound_handler, "__self__", None)
                    _is_valid = None
                    try:
                        from shiboken6 import isValid as _is_valid
                    except Exception:
                        try:
                            from shiboken2 import isValid as _is_valid
                        except Exception:
                            _is_valid = lambda obj: bool(obj)
                    if handler_owner is None or _is_valid(handler_owner):
                        self._bound_handler(self._qm_material_name, source='lineedit', shift=shift, ctrl=ctrl)
                except RuntimeError:
                    pass
        except RuntimeError:
            pass

        QtWidgets.QLineEdit.mousePressEvent(self, e)  # explicit base call




    def focusOutEvent(self, e):
        """Lock again when focus is lost (optional)."""
        if not self.isReadOnly():
            print(f"[QM][LineEdit] focus out → lock: {self.text()}")
            self.setReadOnly(True)
            self.setProperty("editing", "false")  # QSS: revert background
            self.style().unpolish(self); self.style().polish(self); self.update()
        QtWidgets.QLineEdit.focusOutEvent(self, e)  # explicit base call





    def _snap_to_left(self):
        # Keep the beginning visible; don’t disrupt selection, just move caret if needed
        self.setCursorPosition(0)

    def focusInEvent(self, e):
        QtWidgets.QLineEdit.focusInEvent(self, e)  # explicit base call
        # After focus is in, nudge display to show the left edge
        if hasattr(self, "_snap_left_timer"):
            self._snap_left_timer.start(0)

    def resizeEvent(self, e):
        QtWidgets.QLineEdit.resizeEvent(self, e)  # explicit base call
        # On width changes, keep the left edge visible
        if hasattr(self, "_snap_left_timer"):
            self._snap_left_timer.start(0)


# Global instance for the UI
quick_materials_ui_instance = None




base_stylesheet = """
QPushButton#colorDisplayButton {{
background-color: {background_color};
color: #ffffff;
border: 0px solid #9c9c9c;
border-radius: 8px;  /* Rounded corners */
padding: 3px 11px;  /* Padding inside the button */
}}

QPushButton#colorDisplayButton:hover {{
border: 2px solid #f2f2f2;
}}

QPushButton#colorDisplayButton:pressed {{
border: 2px solid #888888;
}}
"""


# Stylesheet for the material list (buttons, line edits, labels)
material_list_widget_style = """
/* ===========================================================
   QuickMaterials — Material List Stylesheet (scoped to container)
   Apply only to the list root widget (scroll_content).
   =========================================================== */

/* ---- Base font ---- */
* {
    font-family: 'Segoe UI';
    font-size: 12px;
}

/* ---------------------------------------------
   Buttons (Assign / Select Objs / Graph / Imp Tx)
   --------------------------------------------- */
QPushButton {
    color: #d4d4d4;
    font-size: 11px;  /* Adjust the font size as needed */
    background-color: #666666;
    border: 1px solid #444444;
    border-radius: 6px;
    padding: 2px 6px;
}
QPushButton:hover    { background-color: #888888; }
QPushButton:pressed  { background-color: #1a1a1a; }
QPushButton:disabled {
    color: #666666;
    background-color: #4a4a4a;
    border: 1px solid #3d3d3d;
}

/* ---------------------------------------------
   LineEdits (material names)
   - Bold text
   - Unified default background
   - Selection and focus states are explicit
   --------------------------------------------- */
QLineEdit {
    font-weight: bold;
    color: #ffffff;
    background-color: #444444;
    border: 1px solid #3d3d3d;
    border-radius: 6px;
    padding: 1px 4px;
    selection-background-color: #2d7dff;
    selection-color: #ffffff;
}

/* Read-only vs editable */
QLineEdit[readOnly="true"]  { color: #dddddd; border: 1px solid #3d3d3d; }
QLineEdit[readOnly="false"] { color: #ffffff; border: 1px solid #777777; }

/* Editing hint (requires code to set editing=true while typing) */
QLineEdit[readOnly="false"][editing="true"] { background-color: #333333; }

/* Focus ring for editable fields */
QLineEdit[readOnly="false"]:focus { border: 1px solid #9a9a9a; }

/* Default-material rows: muted, no interactive change */
QLineEdit[materialType="default"] {
    color: #aaaaaa;
    background-color: #444444;
    border: 1px solid #3d3d3d;
}
QLineEdit[materialType="default"]:hover,
QLineEdit[materialType="default"]:focus {
    color: #aaaaaa;
    background-color: #444444;
    border: 1px solid #3d3d3d;
}

/* Selected line edits (list selection highlight) */
QLineEdit[qmSelected="true"] {
    background-color: #3e637a;
    color: #ffffff;
    border: 1px solid #ccdbe6;
}

/* --- OVERRIDE: default materials ignore highlight and focus --- */
QLineEdit[materialType="default"][qmSelected="true"],
QLineEdit[materialType="default"][qmSelected="true"]:focus,
QLineEdit[materialType="default"]:focus {
    color: #aaaaaa;              /* muted grey text */
    background-color: #444444;   /* normal default background */
    border: 1px solid #3d3d3d;   /* same muted border */
}
/* ---------------------------------------------
   Labels
   --------------------------------------------- */
QLabel {
    color: #ffffff;
    background-color: transparent;
    border: none;
    padding: 0px;
}
QLabel[materialType="default"] { color: #aaaaaa; }

/* ---------------------------------------------
   Checkboxes
   --------------------------------------------- */
QCheckBox {
    color: #ffffff;
}
QCheckBox::indicator {
    width: 13px;
    height: 13px;
    border: 1px solid #4a4a4a;
    background-color: #3f3f3f;
}

/* Checked/unchecked visual */
QCheckBox::indicator:checked   { background-color: #6b6b6b; }
QCheckBox::indicator:unchecked { background-color: #3f3f3f; }

/* Default-material checkboxes: muted */
QCheckBox[materialType="default"] { color: #888888; }
QCheckBox[materialType="default"]::indicator {
    background-color: #555555;
    border: 1px solid #4a4a4a;
}
QCheckBox[materialType="default"]::indicator:checked {
    background-color: #666666;
    border: 1px solid #555555;
}

/* Disabled state */
QCheckBox:disabled { color: #777777; }
QCheckBox:disabled::indicator {
    background-color: #555555;
    border: 1px solid #4a4a4a;
}

/* ---------------------------------------------
   (Optional) Filter Chips (role="chip")
   --------------------------------------------- */
/*
QPushButton[role="chip"] {
    font-size: 11px;
    color: #dddddd;
    background-color: #555555;
    border: 1px solid #444444;
    border-radius: 10px;
    padding: 1px 8px;
}
QPushButton[role="chip"]:hover  { background-color: #666666; }
QPushButton[role="chip"]:pressed{ background-color: #4a4a4a; }
*/
"""


# Stylesheet for the QColorDialog (color picker)
qcolor_dialog_style = """
QColorDialog {
background-color: #444444;
border: 2px solid #666666;
}

QColorDialog QPushButton {
font-family: 'Segoe UI';
font-size: 12px;
color: #ffffff;
background-color: #666666;
border: 2px solid #666666;
border-radius: 8px;
padding: 3px 10px;
}

QColorDialog QPushButton:hover {
background-color: #5a5a5a;
}

QColorDialog QPushButton:pressed {
background-color: #4a4a4a;
}

QColorDialog QLineEdit {
font-family: 'Segoe UI';
font-size: 14px;
color: #ffffff;
background-color: #444444;
border: 2px solid #444444;
border-radius: 8px;
padding: 3px 10px;
}
"""





def maya_main_window():
    """
    Get the Maya main window as a QWidget instance.
    """
    try:
        main_window_ptr = omui.MQtUtil.mainWindow()
        return wrapInstance(int(main_window_ptr), QtWidgets.QWidget)
    except Exception as e:
        print(f"Error getting Maya main window: {e}")
        return None


# --- Register Qt .rcc (or Python rc) so :/icons/... works in Maya ---
def register_qt_resources():
    """
    Ensure the Qt resource file for icons is available (:/icons/*).
    Tries to register QtDesigner/icons.rcc first; falls back to importing icons_rc.
    """
    module_dir = os.path.dirname(os.path.abspath(__file__))
    rcc_path = os.path.join(module_dir, "QtDesigner", "icons.rcc")

    registered = False
    if os.path.exists(rcc_path):
        try:
            registered = QtCore.QResource.registerResource(rcc_path)
            print(f"[QM] registerResource(file) -> {registered} | path: {rcc_path}")
        except Exception as e:
            print(f"[QM] registerResource exception: {e}")

    if not registered:
        # Fallback: compiled Python resource module (pyrcc / pyside6-rcc output)
        try:
            # Try relative package import first
            try:
                from . import icons_rc  # type: ignore
            except Exception:
                import icons_rc  # type: ignore
            print("[QM] Fallback: icons_rc imported successfully")
            registered = True
        except Exception as e:
            print(f"[QM] Fallback failed: icons_rc not importable -> {e}")

    # Tiny probe so we know the path is live
    try:
        probe = QtGui.QIcon(":/icons/arrow_up_pressed.png")
        print(f"[QM][IconTest] :/icons/arrow_up_pressed.png -> isNull={probe.isNull()}")
    except Exception as e:
        print(f"[QM][IconTest] exception: {e}")

    return registered


# Load UI Function
def load_ui():
    """Convenience function to display the dockable Quick Materials UI."""
    QuickMaterialsUI.show_ui()



class QuickMaterialsUI(MayaQWidgetDockableMixin, QtWidgets.QDialog):
    """Dockable UI for the Quick Materials tool."""

    # Store the current dockable instance
    quick_materials_ui_instance = None
    workspace_control_name = "QuickMaterialsWorkspaceControl"

    # --- filters for the material list (id, checkbox objectName, chip label, chip visibility, exclusivity group) ---
    MATERIAL_FILTERS = [
        # Visibility-state group (mutually exclusive across all four)
        {"id": "selected",      "checkbox": "selectedOnlyFilterCheckbox",      "label": "Selected Only",  "chip": True,  "group": "visibility_state"},
        {"id": "nonSelected",   "checkbox": "nonSelectedOnlyFilterCheckbox",   "label": "Non-Selected",   "chip": True,  "group": "visibility_state"},
        {"id": "used",          "checkbox": "usedFilterCheckbox",              "label": "Used",           "chip": True,  "group": "visibility_state"},
        {"id": "unUsed",        "checkbox": "unUsedFilterCheckbox",            "label": "Unused",         "chip": True,  "group": "visibility_state"},

        # Referenced pair (its own exclusive group)
        {"id": "referenced",    "checkbox": "referencedFilterCheckbox",        "label": "Referenced",     "chip": True,  "group": "reference_state"},
        {"id": "nonReferenced", "checkbox": "nonReferencedFilterCheckbox",     "label": "Non-Referenced", "chip": True,  "group": "reference_state"},

        # Standalone
        {"id": "hideDefaults",  "checkbox": "hideDefaultMaterialsCheckbox",    "label": "Hide Defaults",  "chip": False, "group": None},
    ]


    # --- Small helpers over the spec ---
    def _filter_spec(self):
        return list(self.MATERIAL_FILTERS)

    def _find_filter(self, filter_id):
        for f in self.MATERIAL_FILTERS:
            if f["id"] == filter_id:
                return f
        return None

    def __init__(self, parent=None):
        super(QuickMaterialsUI, self).__init__(parent or maya_main_window())
        # --- Double-click grace (suppress selection-driven refresh during this window) ---
        self._dc_grace_deadline = 0.0  # monotonic seconds
        try:
            self._dc_interval_ms = QtWidgets.QApplication.instance().doubleClickInterval()
        except Exception:
            self._dc_interval_ms = 300  # sensible default if app not initialized yet

        # --- Exclusive checkbox groups (name-based; avoids stale Qt pointers) ---
        self._exclusive_groups = {}  # {group_name: [checkbox objectNames]}

        # --- Material list sorting state ---
        # modes: 'name' (A–Z/Z–A), 'type' (group by nodeType then name; reverse on toggle), 'time' (creation order; reverse on toggle)
        self._sort_mode = 'time'
        self._sort_desc = False

        # Cached swatch colors for texture-driven base color: {path: (mtime, "#rrggbb")}
        self._tex_swatch_cache = {}

        # --- Silent refresh guards (used during in-place rename) ---
        self._suspend_refresh_count = 0
        self._mute_poll_until_ts = 0.0

        # --- One-shot sort freeze for rename (prevents jump while editing under 'Name' sort) ---
        self._freeze_name_sort_once = False

        # --- Scene material snapshot (poll fallback in case host events miss) ---
        self._last_materials_snapshot = set()  # names at last poll

        self.setObjectName("QuickMaterialsUI")  # ensure a stable name for parenting scriptJobs

        self.import_tx_tool = None
        # Store all UI elements in a dictionary
        self.ui_elements = LiveWidgetDict(self)
        self.initialize_ui()



    # ------------------------------------------------------------------
    # Docking utilities
    # ------------------------------------------------------------------
    @classmethod
    def show_ui(cls, dockable=True):
        """Display the UI as a dockable widget inside Maya."""
        global quick_materials_ui_instance

        if cmds.workspaceControl(cls.workspace_control_name, query=True, exists=True):
            cls.delete_existing_instance()

        cls.quick_materials_ui_instance = cls()
        quick_materials_ui_instance = cls.quick_materials_ui_instance
        cls.quick_materials_ui_instance.setup_dockability()

    def setup_dockability(self):
        """Dock the window into a Maya workspace control."""
        if not cmds.workspaceControl(self.workspace_control_name, query=True, exists=True):
            cmds.workspaceControl(
                self.workspace_control_name, label="Quick Materials", retain=False, floating=True
            )

        control_widget = omui.MQtUtil.findControl(self.workspace_control_name)
        if control_widget:
            wrapped_widget = wrapInstance(int(control_widget), QtWidgets.QWidget)
            layout = wrapped_widget.layout() or QtWidgets.QVBoxLayout(wrapped_widget)
            layout.setContentsMargins(0, 0, 0, 0)
            wrapped_widget.setLayout(layout)

            while layout.count():
                child = layout.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()

            layout.addWidget(self)
            wrapped_widget.setVisible(True)
            wrapped_widget.update()

            # Ensure a reasonable initial size when docked
            self.setMinimumWidth(400)
            self.adjustSize()
            self.show()
            # Defer snapping so the workspaceControl has fully realized its layout
            QtCore.QTimer.singleShot(0, self.snap_to_minimum)


        cmds.workspaceControl(self.workspace_control_name, edit=True, visible=True)

    @classmethod
    def delete_existing_instance(cls):
        """Close and clean up any existing dock or window instance."""
        global quick_materials_ui_instance
        # proactively remove watchers if an instance exists
        if cls.quick_materials_ui_instance:
            try:
                cls.quick_materials_ui_instance._remove_material_watchers()
            except Exception:
                pass
            try:
                cls.quick_materials_ui_instance._remove_selection_watcher()
            except Exception:
                pass


        if cmds.workspaceControl(cls.workspace_control_name, query=True, exists=True):
            try:
                cmds.deleteUI(cls.workspace_control_name, control=True)
            except RuntimeError:
                pass


        if cmds.window(cls.workspace_control_name, exists=True):
            try:
                cmds.deleteUI(cls.workspace_control_name, window=True)
            except RuntimeError:
                pass

        if cls.quick_materials_ui_instance:
            cls.quick_materials_ui_instance.close()
            cls.quick_materials_ui_instance.deleteLater()
            cls.quick_materials_ui_instance = None

        quick_materials_ui_instance = None

        import gc
        gc.collect()

# Initialize UI
    def initialize_ui(self):
        # Base stylesheet for the color display button, with a placeholder for dynamic color
        self.base_stylesheet = base_stylesheet
        self.material_list_widget_style = material_list_widget_style
        self.qcolor_dialog_style = qcolor_dialog_style

        # Make sure :/icons/* is available to the UI and styles before loading .ui
        register_qt_resources()

        # State variable to track whether default materials are hidden
        self.hide_defaults_state = False

        # Locate the .ui file in the QtDesigner folder
        scriptDir = os.path.dirname(__file__)
        uiFilePath = os.path.join(scriptDir, 'QtDesigner', 'quickMaterials.ui')

        # Verify that the .ui file exists; if not, abort
        if not os.path.exists(uiFilePath):
            print(f"Error: UI file not found at: {uiFilePath}")
            return

        # Change current directory to the UI file’s folder for loader
        QtCore.QDir.setCurrent(os.path.dirname(uiFilePath))

        loader = QtUiTools.QUiLoader()
        uiFile = QtCore.QFile(uiFilePath)

        try:
            # Open the .ui file for reading
            uiFile.open(QtCore.QFile.ReadOnly)

            # Load the .ui file
            loaded_ui = loader.load(uiFile)

            # Close the .ui file now that it’s loaded
            uiFile.close()

            # Create a layout on this dialog and add the loaded UI
            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            if loaded_ui:
                loaded_ui.setParent(self)
                loaded_ui.setSizePolicy(
                    QtWidgets.QSizePolicy.Expanding,
                    QtWidgets.QSizePolicy.Expanding,
                )
                layout.addWidget(loaded_ui)
            self.setLayout(layout)
            self.setSizePolicy(
                QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding
            )
        except Exception as e:
            print(f"Error loading UI file: {e}")
            return


        # Set the window’s title
        self.setWindowTitle("Quick Materials")

        # Collect all child widgets and layouts into ui_elements for easy lookup
        self.auto_initialize_ui_elements(self)

        for key in ('toggleMaterialCreatorVis',
                    'toggleMaterialToolsVis',
                    'toggleMaterialListVis',
                    'materialCreatorLayout',
                    'materialToolsLayout',
                    'materialListLayout'):
            print(f"[DEBUG] {key} found:", key in self.ui_elements)

         # Set up stretch factors and spacer widget for layout scaling
        self.setup_layout_stretches()

        # Always use the dialog (`self`) – the UI's top-level widget may be
        # re-parented and become invalid once docked.
        self.ui_elements['quickMaterialsWindow'] = self



        # Wire up all button/slider/checkbox signals to their respective slots
        self.setup_connections()

        # Create and initialize the random color display button
        self.initialize_color_button()

        # Set up hue, saturation, and value sliders to update the color display
        self.setup_color_sliders()

        # Adjust the saturation slider’s gradient based on the current color
        self.update_saturation_slider_gradient()

        # Configure the roughness slider and spinbox linkage
        self.setup_roughness_slider()

        # Configure the metalness slider and spinbox linkage (new)
        self.setup_metalness_slider()

        # Ensure the attribute frames reflect the current material type at startup
        self.update_material_attr_visibility()

        self.populate_materials_scroll_area()

        # --- Sorting bar above the list (sticky toolbar) ---
        self._install_sort_bar()

        # Selection sync state
        self._sel_watcher_id = None

        self._syncing_selection = False  # guard to avoid feedback loops

        # Start listening to Maya selection changes
        self._install_selection_watcher()

        # --- NEW: hide Material List Filters by default ---
        filters_frame = self.findChild(QtWidgets.QWidget, 'materialListFiltersFrame')

        if filters_frame:
            filters_frame.setVisible(False)
        # keep the toggle button untoggled + label set to "Filters"
        filters_btn = self.findChild(QtWidgets.QPushButton, 'filterMaterialListButton')
        if filters_btn:
            try:
                filters_btn.setChecked(False)
            except Exception:
                pass
            filters_btn.setText('Show Filters')


        # Ensure toggle buttons reflect the default visible state
        for name in ('toggleMaterialCreatorVis', 'toggleMaterialToolsVis', 'toggleMaterialListVis'):
            btn = self.ui_elements.get(name)
            if btn and isValid(btn):
                btn.setChecked(True)

        # Create the QColorDialog instance (hidden until needed)
        self.ui_elements['colorPicker'] = QtWidgets.QColorDialog()
        self.ui_elements['colorPicker'].setOptions(
            QtWidgets.QColorDialog.DontUseNativeDialog
        )

        # Set dynamic minimum size based on which sections start visible
        self.refresh_minimum_size()
        self.setMaximumSize(16777215, 16777215)

        # Ensure this tool behaves like other Maya tools (not always on top)
        self.setWindowFlags(QtCore.Qt.Tool)

        # Give keyboard focus to the main UI
        self.setFocus()




# UI Functions
    def auto_initialize_ui_elements(self, parent_widget):
        """
        Recursively cache *widgets* (skip QLayouts) so we don't keep
        dangling pointers once the UI is docked / rebuilt.
        """
        for child in parent_widget.findChildren(QtWidgets.QWidget):
            object_name = child.objectName()
            if object_name:
                self.ui_elements[object_name] = child

    def setup_layout_stretches(self):
        """
        Wire quickMaterialsBottomSpacerFrame for later show / hide.
        Always refresh the pointer in ui_elements.
        """
        main_frame     = self.findChild(QtWidgets.QWidget, 'mainUIFrame')
        mat_list_frame = self.findChild(QtWidgets.QWidget, 'materialListFrame')
        spacer_frame   = self.findChild(QtWidgets.QWidget, 'quickMaterialsBottomSpacerFrame')

        if not (main_frame and mat_list_frame and spacer_frame):
            print("[DEBUG] stretch setup: mainUIFrame / materialListFrame / spacer frame not found")
            return

        self.root_layout   = main_frame.layout()
        self.bottom_spacer = spacer_frame
        self.ui_elements['quickMaterialsBottomSpacerFrame'] = spacer_frame

        if self.root_layout:
            list_idx = self.root_layout.indexOf(mat_list_frame)
            spacer_idx = self.root_layout.indexOf(spacer_frame)
            if list_idx != -1: self.root_layout.setStretch(list_idx, 1)
            if spacer_idx != -1: self.root_layout.setStretch(spacer_idx, 0)

        spacer_frame.hide()  # list starts visible

    # ---------- Dynamic minimum size helpers ----------
    def _default_min_sizing_profile(self):
        """
        Returns a dict you can customize per section. Heights are additive.
        Keys are the *frame* objectNames (we already convert Layout->Frame elsewhere).
        """
        return {
            "base_width": 300,  # base min width even if all sections hidden
            "base_height": 100,  # base min height even if all sections hidden
            "sections": {
                "materialCreatorFrame": 210,  # visible => add this many pixels of min height
                "materialToolsFrame": 100,
                "materialListFrame": 200,
                "materialListFiltersFrame": 175,

            }
        }

    def set_section_minimums(self, overrides):
        """
        Public API: allow you to override min width/height or per-section heights at runtime.
        Example:
            self.set_section_minimums({
                "base_width": 420,
                "sections": {"materialListFrame": 240}
            })
        """
        if not hasattr(self, "_minsize_profile"):
            self._minsize_profile = self._default_min_sizing_profile()
        # Shallow update base keys
        for k in ("base_width", "base_height"):
            if k in overrides:
                self._minsize_profile[k] = overrides[k]
        # Merge sections
        if "sections" in overrides and isinstance(overrides["sections"], dict):
            self._minsize_profile["sections"].update(overrides["sections"])

        # Apply immediately
        self.refresh_minimum_size()

    def refresh_minimum_size(self):
        """
        Recalculate and apply window minimum size from currently visible sections.
        """
        # Ensure profile exists
        if not hasattr(self, "_minsize_profile"):
            self._minsize_profile = self._default_min_sizing_profile()

        profile = self._minsize_profile
        min_w = int(profile.get("base_width", 400))
        min_h = int(profile.get("base_height", 200))

        # Add section heights if frames are visible
        sections = profile.get("sections", {})
        for frame_name, add_h in sections.items():
            w = self.findChild(QtWidgets.QWidget, frame_name)
            if w and w.isVisible():
                try:
                    min_h += int(add_h)
                except Exception:
                    pass

        # Apply to the dialog (self) once, after computing the total
        self.setMinimumSize(min_w, min_h)

        # Nudge layouts so Maya updates dock constraints
        self.resize_ui(delay=1)  # Keep your small micro-timer bump


    def snap_to_minimum(self):
        """
        Recompute and then snap to the minimum *height* only.
        Prevent any horizontal creep by freezing width for one tick (min=max=current),
        force the vertical shrink/expand, then release all caps.
        """
        def _apply_resize():
            # 0) Recompute dynamic minimums
            self.refresh_minimum_size()
            min_sz = self.minimumSize()
            min_h = max(1, min_sz.height())

            wc_name = getattr(self, "workspace_control_name", None)

            # Get the actual Qt host (workspaceControl widget) and its CURRENT drawn width
            qt_host = None
            host_w = max(1, self.width())
            try:
                if wc_name and cmds.workspaceControl(wc_name, q=True, exists=True):
                    ptr = omui.MQtUtil.findControl(wc_name)
                    if ptr:
                        qt_host = wrapInstance(int(ptr), QtWidgets.QWidget)
                        if qt_host and qt_host.width() > 0:
                            host_w = qt_host.width()
            except Exception:
                pass

            # 1) Tell workspaceControl about the new min height (helps dock splitters)
            try:
                if wc_name and cmds.workspaceControl(wc_name, q=True, exists=True):
                    cmds.workspaceControl(wc_name, e=True, minimumHeight=min_h)
            except Exception:
                pass

            # 2) HARD-FREEZE WIDTH for one tick on both host and self (min==max==current)
            try:
                if qt_host:
                    qt_host.setMinimumWidth(host_w)
                    qt_host.setMaximumWidth(host_w)
            except Exception:
                pass
            self.setMinimumWidth(host_w)
            self.setMaximumWidth(host_w)

            # 3) Force the vertical snap by clamping height to min_h for one tick
            try:
                if qt_host:
                    qt_host.setMinimumHeight(min_h)
                    qt_host.setMaximumHeight(min_h)
                    qt_host.resize(host_w, min_h)
                    qt_host.updateGeometry()
            except Exception:
                pass

            self.setMinimumHeight(min_h)
            self.setMaximumHeight(min_h)
            self.resize(host_w, min_h)
            self.updateGeometry()

            # 4) If floating, also ask Maya to size the container (helps on some hosts)
            try:
                if wc_name and cmds.workspaceControl(wc_name, q=True, exists=True):
                    is_floating = False
                    try:
                        is_floating = cmds.workspaceControl(wc_name, q=True, floating=True)
                    except Exception:
                        pass
                    if is_floating:
                        try:
                            cmds.workspaceControl(wc_name, e=True, resizeWidth=host_w, resizeHeight=min_h)
                        except Exception:
                            pass
            except Exception:
                pass

            # 5) Process a layout pass, then RELEASE all temporary caps next tick
            QtWidgets.QApplication.sendPostedEvents(None, 0)
            QtWidgets.QApplication.processEvents()

            def _release_caps():
                try:
                    if qt_host:
                        qt_host.setMinimumWidth(0)
                        qt_host.setMaximumWidth(16777215)
                        qt_host.setMaximumHeight(16777215)
                    # Keep our *minimumHeight* (we want the new min to persist),
                    # but release width and height maximums so user can resize.
                    self.setMinimumWidth(0)
                    self.setMaximumWidth(16777215)
                    self.setMaximumHeight(16777215)
                except Exception:
                    pass
            QtCore.QTimer.singleShot(0, _release_caps)

        # Defer so the visibility/layout changes from toggles have been applied
        QtCore.QTimer.singleShot(0, _apply_resize)

    def setup_connections(self):
        """Set up all the necessary connections for the UI elements."""

        # Apply material button connection
        if self.ui_elements.get('createNewMaterialButton'):
            self.ui_elements['createNewMaterialButton'].clicked.connect(self.create_material)

        # Delete unused materials button connection
        if self.ui_elements.get('deleteUnusedMaterialsButton'):
            self.ui_elements['deleteUnusedMaterialsButton'].clicked.connect(self.delete_unused_materials)

        # Connect delete selected materials button to delete_selected_materials function
        if self.ui_elements.get('deleteSelectedMaterialsButton'):
            self.ui_elements['deleteSelectedMaterialsButton'].clicked.connect(self.delete_selected_materials)

        # --- install auto-refresh watchers for the material list ---
        self._install_material_watchers()  # ensures list refreshes on scene/material changes


        # Connect toggle buttons for layouts with friendly names
        if self.ui_elements.get('toggleMaterialCreatorVis'):
            self.ui_elements['toggleMaterialCreatorVis'].clicked.connect(
                lambda: self.toggle_layout_visibility('materialCreatorLayout', 'toggleMaterialCreatorVis',
                                                      'Creator')
            )
        if self.ui_elements.get('toggleMaterialToolsVis'):
            self.ui_elements['toggleMaterialToolsVis'].clicked.connect(
                lambda: self.toggle_layout_visibility('materialToolsLayout', 'toggleMaterialToolsVis', 'Tools')
            )
        if self.ui_elements.get('toggleMaterialListVis'):
            self.ui_elements['toggleMaterialListVis'].clicked.connect(
                lambda: self.toggle_layout_visibility('materialListLayout', 'toggleMaterialListVis', 'List')
            )

        # Connect search bar text changes to filter materials
        materialSearchLineEdit = self.ui_elements.get('materialSearchLineEdit')
        if materialSearchLineEdit:
            materialSearchLineEdit.textChanged.connect(self.filter_materials)

        # Refresh materials list button connection
        if self.ui_elements.get('refreshMaterialsButton'):
            self.ui_elements['refreshMaterialsButton'].clicked.connect(self.refresh_materials_list)

        # Connect select/deselect all visible materials button
        if self.ui_elements.get('selectAllVisibleMaterialsButton'):
            self.ui_elements['selectAllVisibleMaterialsButton'].clicked.connect(
                self.toggle_select_all_visible_materials)

        # Populate the materialTypeComboBox with supported materials
        material_type_combo_box = self.ui_elements.get('materialTypeComboBox')
        if material_type_combo_box:
            material_type_combo_box.clear()
            # Order: standardSurface default, then common legacy types
            material_type_combo_box.addItems(['standardSurface', 'blinn', 'phong', 'lambert', 'surfaceShader'])
            material_type_combo_box.setCurrentIndex(0)
            # Update attribute UI visibility whenever the type changes
            material_type_combo_box.currentIndexChanged.connect(self.update_material_attr_visibility)


        _mpm = self.ui_elements.get('materialPerMeshCheckbox')
        if _mpm:
            _mpm.stateChanged.connect(self.update_create_material_button)



        # Connect the random hue checkbox to update the color immediately
        self.ui_elements.get('randomHueCheckbox').stateChanged.connect(
            lambda state: self.set_random_hue_color() if state == QtCore.Qt.Checked else None
        )

        # Connect the clear search button to the clear function
        clear_search_button = self.ui_elements.get('clearMaterialSearchLineEditButton')
        if clear_search_button:
            clear_search_button.clicked.connect(self.clear_material_search)
        else:
            print("Error: clearMaterialSearchLineEditButton not found.")

        # Launch the Material Converter tool
        convert_btn = self.ui_elements.get('convertMaterialsButton')
        if convert_btn:
            convert_btn.clicked.connect(self.open_material_converter)
        else:
            print("Error: convertMaterialsButton not found.")

        # --- NEW: Toggle all per-material button rows (Assign/Highlight/Select/Graph/Import Tx) ---
        tlb = self.ui_elements.get('toggleListButtonsButton')
        if tlb:
            tlb.clicked.connect(self.toggle_material_list_buttons)

        # --- NEW: Filters panel toggle (uses existing toggle_layout_visibility helper) ---
        flt_btn = self.ui_elements.get('filterMaterialListButton')
        if flt_btn:
            # We pass the *Layout* name so the helper can resolve the corresponding Frame too.
            flt_btn.clicked.connect(
                lambda: self.toggle_layout_visibility('materialListFiltersLayout',
                                                      'filterMaterialListButton',
                                                      'Show Filters')
            )

        # --- Live filters (auto-wired from MATERIAL_FILTERS) ---
        for f in self._filter_spec():
            cb = self._get_widget(f["checkbox"], QtWidgets.QCheckBox)
            if cb:
                try:
                    cb.stateChanged.disconnect()
                except Exception:
                    pass
                cb.stateChanged.connect(self.refresh_materials_list)

        # ---- allow unchecking to "none" by disabling strict exclusivity in groups (if present) ----
        # If you still use QButtonGroups in the .ui, disable exclusivity so "none" is possible.
        for group_object_name in ('referenceButtonGroup', 'usedButtonGroup', 'selectedButtonGroup'):
            g = self.findChild(QtWidgets.QButtonGroup, group_object_name)
            if g:
                g.setExclusive(False)

        # ---- keep "at most one" checked per group, but allow zero (derived from MATERIAL_FILTERS) ----
        groups = {}
        for f in self._filter_spec():
            grp = f.get("group")
            if grp:
                groups.setdefault(grp, []).append(f["checkbox"])

        for grp, names in groups.items():
            if len(names) == 2:
                self._wire_at_most_one(names[0], names[1])
            elif len(names) > 2:
                self._wire_at_most_one_group(names, grp)



        # --- verify filter checkbox hookups once UI is live ---
        def _verify_filters_once():
            for f in self._filter_spec():
                n = f["checkbox"]
                w = self._get_widget(n, QtWidgets.QCheckBox)
                print(f"[QM][Filters] verify '{n}': exists={bool(w)} checked={w.isChecked() if w else None}")
        QtCore.QTimer.singleShot(0, _verify_filters_once)

        # --- Poll fallback: very cheap, only refreshes on change ---
        if not hasattr(self, "_material_poll_timer"):
            self._material_poll_timer = QtCore.QTimer(self)
            self._material_poll_timer.setInterval(800)  # ms
            self._material_poll_timer.timeout.connect(self._poll_materials_snapshot)
            self._material_poll_timer.start()
            # seed snapshot
            try:
                HIDDEN_MATERIALS = getattr(self, "HIDDEN_MATERIALS", {'particleCloud1'})
                self._last_materials_snapshot = set(m for m in (cmds.ls(materials=True) or []) if m not in HIDDEN_MATERIALS)
            except Exception:
                self._last_materials_snapshot = set()




    def update_material_attr_visibility(self):
        """
        Show/hide attribute setter frames depending on selected material type.
        Frames expected:
          - colorPickerFrame
          - roughnessSliderFrame
          - metalnessSliderFrame
        Rules:
          - standardSurface: color, roughness, metalness = ON
          - blinn/phong:     color, roughness = ON; metalness = OFF
          - lambert/surfaceShader: color = ON; roughness/metalness = OFF
        """
        t = self.determine_material_type().lower()

        color_on = True
        rough_on = False
        metal_on = False

        if t == 'standardsurface':
            rough_on = True
            metal_on = True
        elif t in ('blinn', 'phong'):
            rough_on = True
            metal_on = False
        elif t in ('lambert', 'surfaceshader'):
            rough_on = False
            metal_on = False

        def _set_vis(name, vis):
            w = self.ui_elements.get(name)
            if w and isValid(w):
                w.setVisible(bool(vis))

        _set_vis('colorPickerFrame', True if color_on else False)
        _set_vis('roughnessSliderFrame', True if rough_on else False)
        _set_vis('metalnessSliderFrame', True if metal_on else False)

        # Recompute and snap to new dynamic minimum height
        self.snap_to_minimum()



    def toggle_layout_visibility(self, layout_name, button_name, friendly_name, force_hide=False):
        """
        Toggle the visibility of the specified layout's widgets.
        Shows / hides quickMaterialsBottomSpacerFrame only when the
        Material List is toggled.
        """
        toggle_button = self.ui_elements.get(button_name)
        if toggle_button and not isValid(toggle_button):
            toggle_button = self.findChild(QtWidgets.QPushButton, button_name)
            if toggle_button:
                self.ui_elements[button_name] = toggle_button

        main_window = self.ui_elements.get('quickMaterialsWindow')
        if not toggle_button or not main_window or not isValid(main_window):
            print(f"Error: {button_name} or main window not found.")
            return

        frame_name    = layout_name.replace("Layout", "Frame")
        target_widget = (self.findChild(QtWidgets.QWidget, frame_name) or
                         self.findChild(QtWidgets.QWidget, layout_name))
        if not target_widget:
            print(f"Error: Could not find widget for {layout_name}")
            return

        visible = target_widget.isVisible()
        if force_hide:
            visible = True

        target_widget.setVisible(not visible)
        if isValid(toggle_button):
            toggle_button.setChecked(not visible)
            toggle_button.setText(friendly_name)

        # Show / hide spacer frame only for the Material List toggle
        if layout_name == "materialListLayout":
            spacer = self.ui_elements.get('quickMaterialsBottomSpacerFrame')
            if spacer and not isValid(spacer):              # pointer went stale
                spacer = self.findChild(QtWidgets.QWidget, 'quickMaterialsBottomSpacerFrame')
                if spacer:
                    self.ui_elements['quickMaterialsBottomSpacerFrame'] = spacer
            self.bottom_spacer = spacer                      # keep freshest copy

            if self.bottom_spacer:
                if not visible:  # list will be shown
                    self.bottom_spacer.hide()
                else:  # list will be hidden
                    self.bottom_spacer.show()


        # Recompute min size and snap after the event loop processes the visibility change
        QtCore.QTimer.singleShot(0, self.snap_to_minimum)


    def resize_ui(self, delay=5):
        """Force the layout to recalc but keep the user’s current window size."""
        quick_materials_window = self.ui_elements.get('quickMaterialsWindow')
        if not quick_materials_window:
            print("Error: quickMaterialsWindow not found.")
            return

        def perform_resize():
            if quick_materials_window and isValid(quick_materials_window):
                # Refresh root_layout (it may have been rebuilt) and invalidate safely
                main_frame = self.findChild(QtWidgets.QWidget, 'mainUIFrame')
                if main_frame:
                    self.root_layout = main_frame.layout()
                if getattr(self, "root_layout", None):
                    try:
                        self.root_layout.invalidate()
                    except RuntimeError:
                        pass  # layout was deleted; skip
                quick_materials_window.updateGeometry()
        QtCore.QTimer.singleShot(delay, perform_resize)



    def setup_color_sliders(self):
        """Connect HSV sliders to the color display, using live look-ups each time."""
        hue_name        = 'materialColorHueSlider'
        sat_name        = 'materialColorSaturationSlider'
        val_name        = 'materialColorValueSlider'
        button_name     = 'colorDisplayButton'

        # Quick sanity check
        for n in (hue_name, sat_name, val_name, button_name):
            if not self.ui_elements.get(n):
                print(f"Error: {n} not found.")
                return

        # Set slider ranges once
        self.ui_elements[hue_name].setRange(0, 360)
        self.ui_elements[sat_name].setRange(0, 100)
        self.ui_elements[val_name].setRange(0, 100)

        # Initialise sliders
        self.ui_elements[hue_name].setValue(0)
        self.ui_elements[sat_name].setValue(100)
        self.ui_elements[val_name].setValue(100)

        def update_color_from_sliders():
            """Fetch fresh widgets, then push the colour through."""
            hue_slider        = self.ui_elements[hue_name]
            sat_slider        = self.ui_elements[sat_name]
            val_slider        = self.ui_elements[val_name]
            color_button      = self.ui_elements[button_name]

            hue = hue_slider.value() / 360.0
            sat = sat_slider.value() / 100.0
            val = val_slider.value() / 100.0

            r, g, b = colorsys.hsv_to_rgb(hue, sat, val)
            hex_col  = "#{:02x}{:02x}{:02x}".format(int(r*255), int(g*255), int(b*255))
            self.selected_color = QtGui.QColor(hex_col)
            self.update_button_color(color_button, self.selected_color)
            self.update_saturation_slider_gradient()

        # Connect signals->slot
        self.ui_elements[hue_name].valueChanged.connect(update_color_from_sliders)
        self.ui_elements[sat_name].valueChanged.connect(update_color_from_sliders)
        self.ui_elements[val_name].valueChanged.connect(update_color_from_sliders)

        # Run once for initial state
        update_color_from_sliders()

        # Open colour picker
        self.ui_elements[button_name].clicked.connect(self.open_and_sync_color_picker)

    def update_saturation_slider_gradient(self):
        """Update the saturation slider's gradient to reflect the current hue and value."""
        if not self.selected_color:
            self.selected_color = QtGui.QColor(255, 0, 0)  # Default to red

        selected_color_hsv = self.selected_color.toHsv()
        hue = selected_color_hsv.hueF()
        value = selected_color_hsv.valueF()

        # If hue is -1, it means there's no saturation (a shade of gray),
        # so we fallback to the actual hue slider value instead of 0.0.
        if hue == -1:
            # Convert the hue slider's value (0 to 360) back to a fraction (0 to 1)
            hue_slider_value = self.ui_elements['materialColorHueSlider'].value()
            hue = hue_slider_value / 360.0

        hue_deg = hue * 360
        value_percent = value * 100

        gradient_style = f"""
        QSlider::groove:horizontal {{
            height: 4px;        /* Thinner bar */
            border-radius: 2px; /* Rounded edges */
            margin: 0 0;
            background: qlineargradient(
                spread:pad,
                x1:0, y1:0,
                x2:1, y2:0,
                stop:0 hsv({hue_deg}, 0%, {value_percent}%),
                stop:1 hsv({hue_deg}, 100%, {value_percent}%)
            );
        }}
            QSlider::handle:horizontal {{
                background: #e0e0e0;
                border: 1px solid #555555;
                width: 10px;        /* Round handle size */
                height: 10px;
                margin: -4px 0;     /* Centers the handle on thin groove */
                border-radius: 5px; /* Fully rounded handle */
        }}
        """

        self.ui_elements['materialColorSaturationSlider'].setStyleSheet(gradient_style)

    def update_color_from_external_change(self):
        """Update the sliders and color display when the color is changed externally."""
        hue = self.selected_color.hueF() * 360  # Convert to degrees for the slider
        saturation = self.selected_color.saturationF() * 100  # Convert to percentage
        value = self.selected_color.valueF() * 100  # Convert to percentage

        # Update the sliders to reflect the new color
        self.ui_elements['materialColorHueSlider'].setValue(int(hue))
        self.ui_elements['materialColorSaturationSlider'].setValue(int(saturation))
        self.ui_elements['materialColorValueSlider'].setValue(int(value))

        # Update the saturation slider gradient
        self.update_saturation_slider_gradient()

    def initialize_color_button(self):
        """Initialize the color display button with an initial color and connect its click event."""
        color_display_button = self.ui_elements.get('colorDisplayButton')

        if color_display_button:
            # Set initial color to a random hue (instead of fixed gray) to enhance usability
            random_hue = random.uniform(0, 1)  # Generate a random hue
            self.selected_color = QtGui.QColor()
            self.selected_color.setHsvF(random_hue, 1.0, 1.0)  # Full saturation and brightness

            # Update the display button to reflect the initial random color
            self.update_button_color(color_display_button, self.selected_color)

            # Connect the display button to the color picker sync function
            color_display_button.clicked.connect(self.open_and_sync_color_picker)

    def open_and_sync_color_picker(self):
        """Open the color picker and sync it with the current color display button."""
        button_color = self.selected_color  # Use the stored selected color

        color_picker = self.ui_elements.get('colorPicker')
        if color_picker:
            color_picker.setCurrentColor(button_color)

            # Keep the color picker on top of the Maya window
            color_picker.setWindowFlags(color_picker.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)

            # Connect color changes to update the color display and sliders in real time
            color_picker.currentColorChanged.connect(self.update_color_from_qcolordialog)

            color_picker.show()
        else:
            print("Error: Color picker not found in UI elements")

    def update_color_from_qcolordialog(self, color):
        """Update the sliders and color display when the QColorDialog changes the color."""
        self.selected_color = color

        hue = color.hueF() * 360  # Convert to degrees
        saturation = color.saturationF() * 100  # Convert to percentage
        value = color.valueF() * 100  # Convert to percentage

        # Update the sliders to reflect the new color values
        self.ui_elements['materialColorHueSlider'].setValue(int(hue))
        self.ui_elements['materialColorSaturationSlider'].setValue(int(saturation))
        self.ui_elements['materialColorValueSlider'].setValue(int(value))

        # Update the display button with the new color
        self.update_button_color(self.ui_elements['colorDisplayButton'], color)

        # Refresh the saturation slider gradient
        self.update_saturation_slider_gradient()

    def update_button_color(self, button, color):
        """
        Update the QPushButton background color and store the selected color.

        Args:
            button (QPushButton): The button to update.
            color (QColor): The new color to apply.
        """
        # Convert QColor to hex and update the button's background
        color_hex = color.name()
        button.setStyleSheet(self.base_stylesheet.format(background_color=color_hex))

        # Store the selected color for further use
        self.selected_color = color

    def set_selected_color(self, display_button, color_hex):
        """
        Set the selected color and update the display button.

        Args:
            display_button (QPushButton): The button to update.
            color_hex (str): The hex code of the color.
        """
        self.selected_color = QtGui.QColor(color_hex)  # Store the new color
        self.update_button_color(display_button, self.selected_color)  # Sync the button

    def open_maya_color_picker(self, button):
        """
        Open Maya's color picker and update the button background color.

        Args:
            button (QPushButton): The button to update with the selected color.
        """
        # Open Maya's native color editor
        cmds.colorEditor()

        # Get the selected color as an RGB string (e.g., "1.0 0.5 0.0")
        if cmds.colorEditor(query=True, result=True):
            selected_color = cmds.colorEditor(query=True, rgb=True)

            # Convert the RGB values to hexadecimal format
            color_hex = "#{:02x}{:02x}{:02x}".format(
                int(selected_color[0] * 255),
                int(selected_color[1] * 255),
                int(selected_color[2] * 255)
            )

            # Update the button's background color to the selected color
            button.setStyleSheet(f"background-color: {color_hex}; border: 1px solid #333;")
            # print(f"Color from Maya color editor: {color_hex}")

    def _link_slider_spinbox(self, slider_name, spin_name, init_value=0.0):
        """
        Safely link a QSlider (0..1000) to a QDoubleSpinBox (0..1) using live lookups.
        Avoids capturing stale Qt pointers by resolving by name at call time.
        """
        slider = self.ui_elements.get(slider_name)
        spin = self.ui_elements.get(spin_name)

        if not (slider and spin):
            print(f"Warning: widgets missing: {slider_name} / {spin_name}")
            return

        # Set slider range once
        slider.setMinimum(0)
        slider.setMaximum(1000)

        # Slider ➜ SpinBox (live name lookup)
        slider.valueChanged.connect(
            lambda v, sname=spin_name: self.ui_elements[sname].setValue(v / 1000.0)
        )

        # SpinBox ➜ Slider (live name lookup)
        spin.valueChanged.connect(
            lambda v, slname=slider_name: self.ui_elements[slname].setValue(int(v * 1000))
        )

        # Initialize
        slider.setValue(int(init_value * 1000))
        spin.setValue(float(init_value))

    def setup_roughness_slider(self):
        """Configure roughness widgets, always resolving fresh pointers."""
        slider_name = 'roughnessSlider'
        spin_name = 'roughnessSpinBox'

        if not all((self.ui_elements.get(slider_name), self.ui_elements.get(spin_name))):
            print("Error: roughness widgets not found.")
            return

        self.ui_elements[slider_name].setMinimum(0)
        self.ui_elements[slider_name].setMaximum(1000)

        # Slider ➜ SpinBox
        self.ui_elements[slider_name].valueChanged.connect(
            lambda v, sname=spin_name: self.ui_elements[sname].setValue(v / 1000.0)
        )

        # SpinBox ➜ Slider
        self.ui_elements[spin_name].valueChanged.connect(
            lambda v, sname=slider_name: self.ui_elements[sname].setValue(int(v * 1000))
        )

        # Initial value
        init = 0.75
        self.ui_elements[slider_name].setValue(int(init * 1000))
        self.ui_elements[spin_name].setValue(init)

    def setup_metalness_slider(self):
        """Configure metalness widgets via live lookups (prevents stale Qt object captures)."""
        self._link_slider_spinbox('metalnessSlider', 'metalnessSpinBox', init_value=0.0)

    def update_create_material_button(self):
        """
        Update the text on 'createNewMaterialButton' based on the state of 'materialPerMeshCheckbox'.
        Uncheck 'randomHueCheckbox' when 'materialPerMeshCheckbox' is unchecked.
        """
        material_per_mesh_checked = self.ui_elements.get('materialPerMeshCheckbox').isChecked()
        create_material_button = self.ui_elements.get('createNewMaterialButton')

        if material_per_mesh_checked:
            create_material_button.setText('Create Material(s)')
        else:
            create_material_button.setText('Create Material')


# Material Creator Functions
    def create_material(self):
        """Create and apply materials with proper color handling."""
        print("Starting material creation...")  # Debug

        # Only loads Arnold when standardSurface is selected; otherwise no-op
        if not self.ensure_arnold_plugin():
            print("Failed to load Arnold plugin (needed for standardSurface).")
            return

        valid_mesh_objs = self.get_valid_meshes()
        if not valid_mesh_objs:
            cmds.warning("No valid mesh objects selected.")
            return

        is_single_material_for_all = not self.ui_elements.get('materialPerMeshCheckbox').isChecked()
        used_material_names = set()

        # Use the current displayed color for material creation
        color_rgb = self.get_current_color_rgb()

        if is_single_material_for_all:
            # Create one material for all meshes with the selected color
            material_name = self.generate_material(valid_mesh_objs[0], color_rgb, used_material_names)
            if not material_name:
                return

            for mesh in valid_mesh_objs:
                self.assign_material_to_mesh(mesh, material_name)
                print(f"Assigned {material_name} to {mesh}")

        else:
            # Create a different material for each mesh (using the same color unless random hue is checked)
            start_hue = self.selected_color.hueF()
            total_meshes = len(valid_mesh_objs)

            for index, mesh_name in enumerate(valid_mesh_objs):
                # If random hue is checked, adjust hue for each mesh
                if self.ui_elements['randomHueCheckbox'].isChecked():
                    hue = (start_hue + (index / total_meshes)) % 1.0  # Increment hue
                    self.selected_color.setHsvF(hue, self.get_current_saturation(), self.get_current_value())
                    color_rgb = self.get_current_color_rgb()

                material_name = self.generate_material(mesh_name, color_rgb, used_material_names)
                if not material_name:
                    return

                self.assign_material_to_mesh(mesh_name, material_name)
                print(f"Assigned {material_name} to {mesh_name}")

        # Update the color display after creating the material(s)
        self.update_color_display_after_creation()

        # Refresh the materials list and close the undo chunk
        self.populate_materials_scroll_area()
        cmds.undoInfo(closeChunk=True)
        print("Material creation completed.")

    def ensure_arnold_plugin(self):
        """
        Ensure Arnold is loaded when creating standardSurface.
        surfaceShader does not require mtoa.
        """
        # Peek current selection from UI; fall back safe
        mat_type = self.determine_material_type()
        if mat_type.lower() != 'standardsurface':
            return True  # Not needed

        if not cmds.pluginInfo('mtoa', query=True, loaded=True):
            try:
                cmds.loadPlugin('mtoa')
            except RuntimeError:
                cmds.warning("Arnold plugin could not be loaded (required for standardSurface).")
                return False
        return True

    def get_valid_meshes(self):
        """Retrieve valid mesh objects from the current selection."""
        selected_objs = cmds.ls(selection=True, objectsOnly=True)

        valid_meshes = []
        for obj in selected_objs:
            shapes = cmds.listRelatives(obj, shapes=True, fullPath=True)

            if shapes:
                for shape in shapes:
                    shape_type = cmds.nodeType(shape)
                    if shape_type == 'mesh':
                        valid_meshes.append(obj)
                        break
            else:
                return None
                # print(f"[DEBUG] {obj} has no shapes or no mesh shapes.")

        if not valid_meshes:
            return None
            # print("[DEBUG] No valid meshes found in the selection.")

        return valid_meshes

    def determine_material_type(self):
        """
        Determine material type from the combo box.
        Supported:
          - standardSurface
          - blinn
          - phong
          - lambert
          - surfaceShader
        Defaults to 'standardSurface' on unknown.
        """
        cb = self.ui_elements.get('materialTypeComboBox')
        t = (cb.currentText().strip() if cb and cb.currentText() else 'standardSurface').lower()

        if t == 'standardsurface':
            return 'standardSurface'
        if t in ('blinn', 'phong', 'lambert'):
            return t
        if t == 'surfaceshader':
            return 'surfaceShader'
        return 'standardSurface'

    def set_material_attributes(self, material_name, material_type, roughness):
        """
        Set per-type attributes from UI:
          - standardSurface: specularRoughness (0..1), metalness (0..1)
          - blinn:           eccentricity ≈ roughness (0..1); specularRollOff = 1-roughness
          - phong:           cosinePower ~ (1-roughness)*100 (min 2); specularColor = (1-roughness) grayscale
          - lambert:         no roughness/spec params
          - surfaceShader:   flat pass-through
        """
        try:
            r = max(0.0, min(1.0, float(roughness)))
            inv = 1.0 - r  # Examples: 0.75 -> 0.25, 0.95 -> 0.05, 0.50 -> 0.50

            if material_type == 'standardSurface':
                cmds.setAttr(f"{material_name}.specularRoughness", r)
                metal_spin = self.ui_elements.get('metalnessSpinBox')
                metal_val = float(metal_spin.value()) if metal_spin else 0.0
                cmds.setAttr(f"{material_name}.metalness", max(0.0, min(1.0, metal_val)))

            elif material_type == 'blinn':
                # Roughness → eccentricity, inverse → specularRollOff
                cmds.setAttr(f"{material_name}.eccentricity", r)
                cmds.setAttr(f"{material_name}.specularRollOff", inv)

            elif material_type == 'phong':
                # Roughness inverse → shininess (cosinePower) and specularColor intensity
                power = max(2.0, inv * 100.0)  # keep a floor to avoid super-broad lobes
                cmds.setAttr(f"{material_name}.cosinePower", power)
                cmds.setAttr(f"{material_name}.specularColor", inv, inv, inv, type="double3")

            elif material_type in ('lambert', 'surfaceShader'):
                # Nothing to set for roughness/metalness.
                pass

        except RuntimeError as e:
            cmds.warning(f"Failed to set attributes on {material_name}: {e}")

    def get_color_rgb_for_mesh(self, index, total_meshes, is_single_material_for_all):
        """
        Get the RGB color to be applied to the material.
        If creating one material for all, use a single random hue.
        """
        random_hue_checkbox = self.ui_elements.get('randomHueCheckbox')

        if random_hue_checkbox.isChecked():
            if is_single_material_for_all:
                # Generate a random hue for a single material
                hue = random.uniform(0, 1)
            else:
                # Incremental hue for each mesh (if we had separate materials per mesh)
                hue = index / max(1, total_meshes - 1) * 0.95

            # Set the hue on the color and update the UI button color
            self.selected_color.setHsvF(hue, 1.0, 1.0)
            self.update_button_color(self.ui_elements['colorDisplayButton'], self.selected_color)

        # Convert to RGB
        return (
            self.selected_color.redF(),
            self.selected_color.greenF(),
            self.selected_color.blueF()
        )

    def get_current_color_rgb(self):
        """Retrieve the RGB values from the current color in the display button."""
        return (
            self.selected_color.redF(),
            self.selected_color.greenF(),
            self.selected_color.blueF()
        )

    def get_current_saturation(self):
        """Get the current saturation value from the slider."""
        return self.ui_elements['materialColorSaturationSlider'].value() / 100.0

    def get_current_value(self):
        """Get the current value (brightness) from the slider."""
        return self.ui_elements['materialColorValueSlider'].value() / 100.0

    def update_color_display_after_creation(self):
        """Update the color display and sliders after creating a material."""
        # If user wants a new random hue after creation, use the canonical path
        # that already updates the hue slider and button consistently.
        rh_cb = self.ui_elements.get('randomHueCheckbox')
        if rh_cb and rh_cb.isChecked():
            self.set_random_hue_color()  # updates selected_color, hue slider, and button

        # Ensure the saturation slider gradient matches whatever hue/value we now have
        self.update_saturation_slider_gradient()


    def generate_material(self, mesh_name, color_rgb, used_material_names):
        # Normalize type once
        mat_type = self.determine_material_type()
        mat_key = mat_type  # already normalized to 'standardSurface' or 'surfaceShader'

        # Name
        material_name = self.get_unique_material_name(mesh_name, mat_key, used_material_names)

        try:
            material = cmds.shadingNode(mat_key, asShader=True, name=material_name)
        except RuntimeError as e:
            cmds.warning(f"Failed to create material ({mat_key}): {e}")
            return None

        # Color attribute per-type
        if mat_key == "standardSurface":
            color_attr = ".baseColor"
        elif mat_key in ("lambert", "blinn", "phong"):
            color_attr = ".color"
        elif mat_key == "surfaceShader":
            color_attr = ".outColor"
        else:
            color_attr = ".baseColor"  # safe fallback


        try:
            cmds.setAttr(material + color_attr, *color_rgb, type="double3")
        except RuntimeError as e:
            cmds.warning(f"Error setting color on {material} ({color_attr}): {e}")
            return None

        # SG hookup
        shading_group = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=f"{material_name}SG")
        try:
            cmds.connectAttr(material + ".outColor", shading_group + ".surfaceShader", force=True)
        except RuntimeError as e:
            cmds.warning(f"Failed to connect {material}.outColor to {shading_group}.surfaceShader: {e}")
            return None

        # Attributes: map roughness -> specularRoughness and add metalness (default 0)
        roughness = self.ui_elements.get('roughnessSpinBox').value() if self.ui_elements.get(
            'roughnessSpinBox') else 0.75
        self.set_material_attributes(material, mat_key, roughness)

        return material_name

    def get_unique_material_name(self, mesh_name, material_type, used_material_names):
        """Generate a unique material name."""
        custom_name_template = self.ui_elements.get('materialNamingLineEdit').text().strip() if self.ui_elements.get(
            'materialNamingLineEdit') else ""
        base_material_name = custom_name_template.replace("(mesh)", mesh_name).replace(
            "(mat_type)", material_type) if custom_name_template else f"M_{mesh_name}_{material_type}"

        # Ensure the name is unique
        final_material_name = base_material_name
        count = 1
        while final_material_name in used_material_names or cmds.objExists(final_material_name):
            final_material_name = f"{base_material_name}_{count}"
            count += 1

        used_material_names.add(final_material_name)
        return final_material_name

    def assign_material_to_mesh(self, mesh_name, material_name):
        """Assign the created material to the given mesh."""
        shading_group = f"{material_name}SG"
        try:
            cmds.sets(mesh_name, edit=True, forceElement=shading_group)
        except RuntimeError as e:
            cmds.warning(f"Failed to assign material: {e}")

    def set_random_hue_color(self):
        """Generate and apply a random hue while maintaining the current saturation and value."""
        random_hue = random.uniform(0, 1)  # Random hue value between 0 and 1

        # Get the current saturation and value from the sliders
        saturation = self.ui_elements['materialColorSaturationSlider'].value() / 100.0
        value = self.ui_elements['materialColorValueSlider'].value() / 100.0

        # Set the selected color with the new hue, and existing saturation and value
        self.selected_color.setHsvF(random_hue, saturation, value)

        # Update the hue slider to reflect the new random hue
        hue_value = int(random_hue * 360)  # Convert hue to degrees (0-360)
        self.ui_elements['materialColorHueSlider'].setValue(hue_value)

        # Convert the new color to RGB and update the color display button
        color_rgb = (
            self.selected_color.redF(),
            self.selected_color.greenF(),
            self.selected_color.blueF()
        )
        hex_color = "#{:02x}{:02x}{:02x}".format(
            int(color_rgb[0] * 255),
            int(color_rgb[1] * 255),
            int(color_rgb[2] * 255)
        )
        self.update_button_color(self.ui_elements['colorDisplayButton'], self.selected_color)

        print(f"New random hue applied: {hex_color}")  # Debugging message




# Material Tools
    def delete_unused_materials(self):
        """
        Delete unused materials in the scene and refresh the materials list while maintaining the current visibility state.
        """
        scrollArea = self.ui_elements.get('materialsListScrollArea')

        try:
            # Execute MEL command to delete unused materials
            mel.eval('MLdeleteUnused;')

            # Refresh materials list after deletion
            self.populate_materials_scroll_area(hide_defaults=self.hide_defaults_state)
        except Exception as e:
            cmds.warning(f"Failed to delete unused materials: {e}")

    def open_material_converter(self):
        """
        Wrapper to open the QuickMaterials.material_converter tool.
        Reloads the module during dev, and passes our sheet if available.
        """
        try:
            from QuickMaterials import material_converter as _matconv
            import importlib
            importlib.reload(_matconv)  # nice during iteration; remove if undesired

            # If your global style var is in this module, pass it through
            style = globals().get('material_list_widget_style', None)
            _matconv.show(style)
        except Exception as e:
            import maya.cmds as cmds
            cmds.warning(f"Material Converter failed to open: {e}")




# Material List

    # -------------------------------
    # 1) Public Entrypoints: Build/Refresh UI
    # -------------------------------


    # Rebuilds the list UI from scene state + live filters + search. Adds chips row.
    def populate_materials_scroll_area(self, hide_defaults=False, search_text="", saved_selection=None):
        """
        Rebuild the material list UI, honoring live filters and search.
        Adds an optional chips row at the top when any filters are active.
        """
        # Guard against rebuilds/teardown
        try:
            if not self._is_ui_alive():
                return
        except Exception:
            return

        # Reset selection & per-build registries
        self.selected_materials_list = []
        scrollArea = self.ui_elements.get('materialsListScrollArea')

        # Track action-row widgets for the list-buttons toggle
        self._material_button_rows = []

        # Reset row registry to avoid stale Qt refs
        self._entry_list = []          # list of dicts: {material, swatch, line_edit, is_default}
        self._index_by_material = {}   # material -> row index
        self._selection_anchor = None  # last clicked index for Shift range

        if not scrollArea:
            return

        # Vertical-only scrolling; children shrink horizontally
        scrollArea.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scrollArea.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        scrollArea.setWidgetResizable(True)
        scrollArea.setSizeAdjustPolicy(QtWidgets.QAbstractScrollArea.AdjustToContents)

        # Defaults and permanently hidden
        DEFAULT_MATERIALS = getattr(self, "DEFAULT_MATERIALS", {'lambert1', 'standardSurface1'})
        HIDDEN_MATERIALS  = getattr(self, "HIDDEN_MATERIALS",  {'particleCloud1'})

        # All scene materials except hidden ones
        all_materials = [m for m in (cmds.ls(materials=True) or []) if m not in HIDDEN_MATERIALS]

        # Read live filter flags (Selected / Non-Selected / Referenced / Used / Hide Defaults)
        flags = self._collect_filter_flags()
        # Back-compat: if the checkbox doesn't exist, honor the function argument
        if not self.ui_elements.get('hideDefaultMaterialsCheckbox'):
            flags["hideDefaults"] = bool(hide_defaults)

        # Precompute current selection shapes for selected/non-selected filters
        current_sel_shapes = cmds.ls(sl=True, dag=True, shapes=True) or []

        # Build list using filters + search
        materials_to_display = []
        for mat in all_materials:
            if self._passes_filters(mat, flags, search_text, DEFAULT_MATERIALS, current_sel_shapes):
                materials_to_display.append(mat)

        # Snapshot current on-screen order so we can preserve it for one rebuild after rename
        prev_order = [e.get("material") for e in getattr(self, "_entry_list", [])] if hasattr(self, "_entry_list") else []

        # Clear old contents safely
        self._rebuilding_list = True
        if scrollArea.widget():
            old = scrollArea.takeWidget()
            try:
                old.setEnabled(False)
                old.setParent(None)
            except Exception:
                pass
            old.deleteLater()

        # New container + layout
        scroll_content = QtWidgets.QWidget()
        scroll_content.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        scroll_content.setMinimumWidth(0)
        # List-level stylesheet so entries inherit properly
        scroll_content.setStyleSheet(self.material_list_widget_style)

        scroll_layout = QtWidgets.QGridLayout(scroll_content)
        scroll_layout.setContentsMargins(3, 3, 3, 3)
        scroll_layout.setVerticalSpacing(2)
        scroll_layout.setHorizontalSpacing(3)
        self._last_type_header = None  # reset type chunking per rebuild

        # Start at row 0 inside the scroll; the sticky sort bar is outside the scroll area
        row = 0

        # Chips row (only if any filters active)
        row = 0
        consumed = self._add_active_filters_bar(scroll_layout, row)
        row += consumed

        # --- Apply sorting (with optional one-shot freeze for 'Name' sort after rename) ---
        if getattr(self, "_sort_mode", "name") == "name" and getattr(self, "_freeze_name_sort_once", False):
            # Preserve prior visual order for the materials that pass current filters
            index = {m: i for i, m in enumerate(prev_order)}
            large = 10**9
            materials_to_display.sort(key=lambda m: index.get(m, large))
            self._freeze_name_sort_once = False  # consume the freeze
        else:
            materials_to_display = self._sort_materials(materials_to_display, all_materials)

        # Populate entries (+ action rows)
        for material in materials_to_display:
            is_default = material in DEFAULT_MATERIALS
            # --- TYPE HEADER (only when sorting by type) ---
            if getattr(self, "_sort_mode", "name") == "type":
                try:
                    _t = (cmds.nodeType(material) or "").strip()
                except Exception:
                    _t = ""
                if not hasattr(self, "_last_type_header"):
                    self._last_type_header = None
                if _t != self._last_type_header:
                    self._add_type_header(scroll_layout, row, _t or "Unknown")
                    row += 1
                    self._last_type_header = _t
            self.add_material_entry(material, row, scroll_layout, DEFAULT_MATERIALS, saved_selection)
            self.add_material_buttons(material, row, scroll_layout, is_default)
            row += 2


        # Bottom spacer
        spacer_item = QtWidgets.QSpacerItem(
            20, 40, QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Expanding
        )
        scroll_layout.addItem(spacer_item, row, 0, 1, 4)

        # Install content; end rebuild guard
        scrollArea.setWidget(scroll_content)
        self._rebuilding_list = False

        # Sync once: scene → list, then visuals
        if hasattr(self, "_sync_list_from_scene_selection"):
            self._sync_list_from_scene_selection()
        if hasattr(self, "_apply_selection_visuals"):
            self._apply_selection_visuals()

    def _add_type_header(self, grid_layout, row, type_name):
        """Add a thin, full-width orange separator row for a material type chunk."""
        bar = QtWidgets.QWidget()
        bar.setObjectName("qmTypeHeader")
        bar.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        bar.setAutoFillBackground(True)

        bar.setStyleSheet("""
            QWidget#qmTypeHeader {
                background-color: #444444;    /* orange stripe */
                border: none;
            }
            QWidget#qmTypeHeader QLabel {
                color: #00f7c8;
                font-weight: bold;
                padding: 1px 4px;   /* minimal padding */
                font-size: 12px;    /* slightly smaller text */
            }
        """)

        lay = QtWidgets.QHBoxLayout(bar)
        lay.setContentsMargins(2, 0, 2, 0)  # thin vertical height
        lay.setSpacing(0)

        lbl = QtWidgets.QLabel(f"{type_name}:")
        lbl.setTextInteractionFlags(QtCore.Qt.NoTextInteraction)
        lay.addWidget(lbl)
        lay.addStretch(1)

        grid_layout.addWidget(bar, row, 0, 1, 4)



    # Refresh list using current search/filter state (debounced elsewhere).
    def refresh_materials_list(self):
        # Guard against late timer/scriptJob callbacks on a dead UI
        try:
            if getattr(self, "_suspend_refresh_count", 0) > 0:
                return
            if not self._is_ui_alive() or getattr(self, "_rebuilding_list", False):
                return
        except Exception:
            return

        # Get the scroll area from the UI elements
        scrollArea = self.ui_elements.get('materialsListScrollArea')

        # Get the current search text from the materialSearchLineEdit
        materialSearchLineEdit = self.ui_elements.get('materialSearchLineEdit')
        search_text = materialSearchLineEdit.text() if materialSearchLineEdit else ""

        # Live filters are read inside populate_materials_scroll_area
        if scrollArea:
            self.populate_materials_scroll_area(search_text=search_text)

    # Filter-as-you-type entrypoint; forwards to populate with search_text.
    def filter_materials(self, search_text):
        scrollArea = self.ui_elements.get('materialsListScrollArea')
        # live filters (including hide-defaults) are read internally
        self.populate_materials_scroll_area(search_text=search_text)

    # --- Silent refresh helpers (guard list rebuilds during in-place edits) ---
    def _begin_silent_refresh(self, mute_ms=800):
        import time as _t
        self._suspend_refresh_count = getattr(self, "_suspend_refresh_count", 0) + 1
        try:
            self._mute_poll_until_ts = max(getattr(self, "_mute_poll_until_ts", 0.0),
                                           _t.monotonic() + (mute_ms / 1000.0))
        except Exception:
            pass


    def _end_silent_refresh(self):
        self._suspend_refresh_count = max(0, getattr(self, "_suspend_refresh_count", 1) - 1)

    def _find_basecolor_file_node(self, material):
        """
        Try to find a file node that ultimately drives the base color for this material.
        Strategy:
          1) resolve color attr via get_material_color_attribute()
          2) look at direct source; if not 'file', walk its upstream history until a 'file' is found
        Returns the first file node name or None.
        """
        try:
            attr = self.get_material_color_attribute(material)
            if not attr:
                return None
            plug = f"{material}.{attr}"
            direct = cmds.listConnections(plug, s=True, d=False) or []
            if not direct:
                return None

            # if direct driver is a file node, done
            for n in direct:
                if cmds.nodeType(n) == "file":
                    return n

            # otherwise walk upstream from the first driver (covers colorCorrect, layeredTexture, etc.)
            upstream = cmds.listHistory(direct, pruneDagObjects=True) or []
            for n in upstream:
                if cmds.nodeType(n) == "file":
                    return n
        except Exception:
            pass
        return None

    def _file_average_color_hex(self, file_node):
        """
        Compute a quick average color for a file node's image using Qt (fast, no Maya API quirks).
        Caches by file mtime to avoid re-reading on every build.
        """
        try:
            path = cmds.getAttr(f"{file_node}.fileTextureName") or ""
        except Exception:
            path = ""
        if not path or not os.path.isfile(path):
            return None

        # cache check
        try:
            mtime = os.path.getmtime(path)
            cached = self._tex_swatch_cache.get(path)
            if cached and cached[0] == mtime:
                return cached[1]
        except Exception:
            mtime = None

        # load with Qt and sample sparsely (~max 64x64 samples)
        try:
            img = QtGui.QImage(path)
            if img.isNull():
                return None

            w = img.width()
            h = img.height()
            if w <= 0 or h <= 0:
                return None

            # choose stride to cap samples
            max_samples = 64
            step_x = max(1, int(round(w / max_samples)))
            step_y = max(1, int(round(h / max_samples)))

            r = g = b = cnt = 0
            for y in range(0, h, step_y):
                scanline = img.scanLine(y)
                # QImage.pixel() is fine too; direct pixel is clearer and safe across formats
                for x in range(0, w, step_x):
                    c = QtGui.QColor(img.pixel(x, y))
                    r += c.red()
                    g += c.green()
                    b += c.blue()
                    cnt += 1

            if cnt == 0:
                return None
            r //= cnt; g //= cnt; b //= cnt
            hex_col = "#{:02x}{:02x}{:02x}".format(r, g, b)

            # update cache
            if mtime is not None:
                self._tex_swatch_cache[path] = (mtime, hex_col)
            return hex_col
        except Exception:
            return None

    def _rgb_to_hex(self, r, g, b):
        """Clamp 0..1 floats to #RRGGBB hex."""
        r = max(min(r, 1.0), 0.0); g = max(min(g, 1.0), 0.0); b = max(min(b, 1.0), 0.0)
        return "#{:02x}{:02x}{:02x}".format(int(r * 255), int(g * 255), int(b * 255))



    # -------------------------------
    # 2) Filter Chips & Flags (UI → flags)
    # -------------------------------


    # Returns active filters as (label, filter_id) tuples for chip rendering.
    def _gather_active_filters(self):
        """
        Return a list of (label, filter_id) for currently active filters.
        Chips are included/excluded per MATERIAL_FILTERS['chip'].
        """
        active = []
        for f in self._filter_spec():
            if not f.get("chip", True):
                continue
            cb = self._get_widget(f["checkbox"], QtWidgets.QCheckBox)
            if cb and cb.isChecked():
                active.append((f["label"], f["id"]))
        return active


    # Reads checkboxes safely and returns a dict of boolean flags.
    def _collect_filter_flags(self):
        """
        Reads filter checkboxes from MATERIAL_FILTERS; returns a dict keyed by filter id.
        Also exposes legacy keys for compatibility (selectedOnly/nonSelectedOnly).
        """
        flags = {}
        for f in self._filter_spec():
            cb = self._get_widget(f["checkbox"], QtWidgets.QCheckBox)
            flags[f["id"]] = bool(cb and cb.isChecked())

        # --- Back-compat keys (remove once all callsites use new ids) ---
        flags["selectedOnly"]    = flags.get("selected", False)       # legacy alias
        flags["nonSelectedOnly"] = flags.get("nonSelected", False)    # legacy alias
        return flags


    # Applies filter flags + search to a single material name.
    def _passes_filters(self, mat, flags, search_text, default_materials, current_sel_shapes):
        """
        Returns True if 'mat' should be shown under current filter flags + search.
        """
        # Hide defaults (optionally)
        if flags.get("hideDefaults", False) and mat in default_materials:
            return False

        # Search text
        if search_text and search_text.lower() not in mat.lower():
            return False

        # Referenced / Non-Referenced (mutually exclusive by wiring, but we guard anyway)
        is_ref = self._is_referenced(mat)
        if flags.get("referenced", False) and not is_ref:
            return False
        if flags.get("nonReferenced", False) and is_ref:
            return False

        # Used / Unused (mutually exclusive by wiring)
        is_used = self._is_material_used(mat)
        if flags.get("used", False) and not is_used:
            return False
        if flags.get("unUsed", False) and is_used:
            return False

        # Selected / Non-Selected relative to current scene selection
        affects_sel = self._material_affects_any_of_selection(mat, current_sel_shapes)
        if flags.get("selectedOnly", False) and not affects_sel:
            return False
        if flags.get("nonSelectedOnly", False) and affects_sel:
            return False

        return True

    # Build a removable chip button for an active filter.
    def _make_filter_chip(self, label, filter_id):
        btn = QtWidgets.QPushButton(f"{label}  ✕")
        btn.setToolTip(f"Clear “{label}” filter")
        btn.setCursor(QtCore.Qt.PointingHandCursor)
        btn.setFixedHeight(20)
        btn.setMinimumWidth(0)
        btn.setSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Fixed)
        btn.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        btn.setCheckable(False)  # not a toggle

        # Force strong inline style so chips remain visible regardless of parent QSS
        btn.setStyleSheet("""
            QPushButton {
                font-family: 'Segoe UI';
                font-size: 11px;
                color: #00f7c8;
                background-color: #595959;       /* slightly brighter than list bg */
                border: 1px solid #434343;
                border-radius: 9px;
                padding: 0px 8px;
                text-align: left;
            }
            QPushButton:hover { background-color: #6a6a6a; }
            QPushButton:pressed { background-color: #4f4f4f; }
        """)


        # Always disconnect any previous signals (UI is rebuilt often)
        try:
            btn.clicked.disconnect()
        except Exception:
            pass
        try:
            btn.pressed.disconnect()
        except Exception:
            pass

        # Connect using CLICKED but swallow its bool arg and pass our filter_id.
        # This works identically on PySide2 and PySide6.
        btn.clicked.connect(lambda _checked=False, fid=filter_id: self._on_filter_chip_clicked(fid))
        return btn

    # Build a removable chip button for an active filter.
    def _add_active_filters_bar(self, grid_layout, row):
        active = self._gather_active_filters()
        if not active:
            # Nothing to render; consume no rows
            return 0

        bar_container = QtWidgets.QWidget()
        flow = QMFlowLayout(bar_container, margin=2, hspacing=4, vspacing=4)
        for label, fid in active:
            flow.addWidget(self._make_filter_chip(label, fid))

        grid_layout.addWidget(bar_container, row, 0, 1, 4)
        return 1

    # Click on a chip → uncheck its corresponding checkbox and refresh (deferred).
    def _on_filter_chip_clicked(self, filter_id):
        """Chip click → clear the corresponding checkbox (deferred)."""
        print(f"[QM][Filters] chip -> clear '{filter_id}'")
        QtCore.QTimer.singleShot(0, lambda fid=filter_id: self._clear_filter(fid))

    # Programmatically uncheck a specific filter checkbox by filter_id.
    def _clear_filter(self, filter_id):
        """Uncheck the checkbox for filter_id (from MATERIAL_FILTERS) and refresh (deferred)."""
        spec = self._find_filter(filter_id)
        if not spec:
            print(f"[QM][Filters] _clear_filter: unknown filter_id='{filter_id}'")
            return
        cb = self._get_widget(spec["checkbox"], QtWidgets.QCheckBox)
        if not cb:
            print(f"[QM][Filters] _clear_filter: checkbox '{spec['checkbox']}' not found")
            return
        if cb.isChecked():
            cb.setChecked(False)  # fire signals
        QtCore.QTimer.singleShot(0, self.refresh_materials_list)


    # Wire two checkboxes to be mutually exclusive but allow both to be unchecked.
    def _wire_at_most_one(self, a_name, b_name):
        """Make two checkboxes mutually exclusive BUT allow unchecking to zero (safe to UI rebuilds)."""
        try:
            from shiboken2 import isValid as _is_valid
        except Exception:
            try:
                from shiboken6 import isValid as _is_valid
            except Exception:
                _is_valid = lambda obj: bool(obj)

        a = self._get_widget(a_name, QtWidgets.QCheckBox)
        b = self._get_widget(b_name, QtWidgets.QCheckBox)
        if not (a and b):
            return

        def on_a(state):
            a_cb = self._get_widget(a_name, QtWidgets.QCheckBox)
            b_cb = self._get_widget(b_name, QtWidgets.QCheckBox)
            if not (a_cb and b_cb and _is_valid(a_cb) and _is_valid(b_cb)):
                return
            if state == QtCore.Qt.Checked and b_cb.isChecked():
                QtCore.QSignalBlocker(b_cb)
                b_cb.setChecked(False)
            self.refresh_materials_list()

        def on_b(state):
            a_cb = self._get_widget(a_name, QtWidgets.QCheckBox)
            b_cb = self._get_widget(b_name, QtWidgets.QCheckBox)
            if not (a_cb and b_cb and _is_valid(a_cb) and _is_valid(b_cb)):
                return
            if state == QtCore.Qt.Checked and a_cb.isChecked():
                QtCore.QSignalBlocker(a_cb)
                a_cb.setChecked(False)
            self.refresh_materials_list()

        a.stateChanged.connect(on_a)
        b.stateChanged.connect(on_b)

    def _wire_at_most_one_group(self, checkbox_names, group_name):
        """
        Make an arbitrary set of checkboxes mutually exclusive (at most one),
        while still allowing all to be unchecked (no forced selection).
        Name-based wiring: resolves widgets fresh each time; no stale pointers.
        """
        # Record mapping so the handler can find peers later
        self._exclusive_groups[group_name] = list(checkbox_names)

        # Connect each checkbox to a stable, named slot that uses objectNames
        for name in checkbox_names:
            cb = self._get_widget(name, QtWidgets.QCheckBox)
            if not cb:
                continue
            # Do NOT blanket-disconnect here; setup_connections already handled refresh wiring.
            # Just add our group handler.
            try:
                cb.stateChanged.connect(
                    lambda st, _name=name, _grp=group_name: self._on_exclusive_group_changed(_name, _grp, st)
                )
            except Exception:
                # Some hosts error if double-connecting identical lambdas; safe to ignore
                pass

    def _on_exclusive_group_changed(self, changed_name, group_name, state):
        """
        Slot for group exclusivity. When one is checked, uncheck all the others in its group.
        Uses live lookups via objectName to avoid stale Qt pointers.
        """
        # Only act on "checked" events; unchecking doesn't force anything
        if state != QtCore.Qt.Checked:
            self.refresh_materials_list()
            return

        names = list(self._exclusive_groups.get(group_name, []))
        for peer_name in names:
            if peer_name == changed_name:
                continue
            peer = self._get_widget(peer_name, QtWidgets.QCheckBox)
            if peer and peer.isChecked():
                blocker = QtCore.QSignalBlocker(peer)
                peer.setChecked(False)

        self.refresh_materials_list()


    # --- Sorting UI & logic ---------------------------------------------------


    def _sort_materials(self, materials, all_materials=None):
        """
        Return a new list sorted per current state:
          - 'name' : alphabetical by name (A–Z) or reversed (Z–A)
          - 'type' : group by nodeType (alphabetical), then by name; reverse flips whole list
          - 'time' : creation order via MItDependencyNodes iteration index; reverse flips order
        """
        mats = list(materials or [])
        if not mats:
            return mats

        mode = getattr(self, "_sort_mode", "name")
        desc = bool(getattr(self, "_sort_desc", False))

        if mode == 'name':
            mats.sort(key=lambda m: m.lower())
            if desc:
                mats.reverse()
            return mats

        if mode == 'type':
            def _safe_type(n):
                try:
                    return (cmds.nodeType(n) or "").lower()
                except Exception:
                    return ""
            mats.sort(key=lambda m: (_safe_type(m), m.lower()))
            if desc:
                mats.reverse()
            return mats

        # mode == 'time'
        try:
            # Build a creation-order index map for the current scene snapshot.
            order_map = self._material_creation_index_map(target_set=set(all_materials or mats))
        except Exception:
            order_map = {}

        # Missing entries go to the end (use a large default)
        large = 10**9
        mats.sort(key=lambda m: order_map.get(m, large))
        if desc:
            mats.reverse()
        return mats


    def _material_creation_index_map(self, target_set):
        """
        Return {nodeName: creationIndex} for nodes in target_set by iterating the
        dependency graph in Maya's internal creation order. Older nodes have lower indices.
        """
        order = {}
        try:
            from maya.api import OpenMaya as om
            it = om.MItDependencyNodes()
            idx = 0
            while not it.isDone():
                obj = it.thisNode()
                try:
                    name = om.MFnDependencyNode(obj).name()
                except Exception:
                    name = None
                if name and name in target_set and name not in order:
                    order[name] = idx
                idx += 1
                it.next()
        except Exception:
            # Fallback: preserve incoming order as the "creation order"
            for i, n in enumerate(target_set):
                order.setdefault(n, i)
        return order


    def _install_sort_bar(self):
        """
        Create a small sorting toolbar (Name/Type/Time) above the materials scroll area
        so it remains visible while the list scrolls.
        """
        # Find the frame/layout that contains the scroll area
        parent_layout_widget = self.findChild(QtWidgets.QWidget, 'materialListFrame') or self.findChild(QtWidgets.QWidget, 'materialListLayout')
        scroll = self.ui_elements.get('materialsListScrollArea')
        if not (parent_layout_widget and scroll):
            return

        # Fetch the parent layout that holds the scroll area
        parent_layout = parent_layout_widget.layout() or self.findChild(QtWidgets.QLayout, 'materialListLayout')
        if not parent_layout:
            parent_layout = self.findChild(QtWidgets.QLayout, 'materialListFrame')

        if not parent_layout:
            return

        # If already created, just ensure it's inserted above the scroll area
        sort_bar = self.ui_elements.get('materialListSortBar')
        if not sort_bar:
            sort_bar = self._create_sort_bar_widget()
            sort_bar.setObjectName('materialListSortBar')
            self.ui_elements['materialListSortBar'] = sort_bar

        # Insert the bar just before the scroll area widget
        # Remove existing reference first to avoid duplicates on rebuild
        idx_scroll = parent_layout.indexOf(scroll)
        if idx_scroll == -1:
            # Fall back: put at top
            idx_scroll = 0
        # Avoid re-inserting if it's already placed
        if parent_layout.indexOf(sort_bar) == -1:
            parent_layout.insertWidget(max(0, idx_scroll), sort_bar)

        # Keep visuals fresh
        sort_bar.setVisible(True)
        sort_bar.update()


    def _create_sort_bar_widget(self):
        """Create the sticky sort toolbar (Name / Type / Time)."""
        bar = QtWidgets.QWidget(self)
        lay = QtWidgets.QHBoxLayout(bar)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(6)

        name_btn = QtWidgets.QPushButton()
        type_btn = QtWidgets.QPushButton()
        time_btn = QtWidgets.QPushButton()

        for b in (name_btn, type_btn, time_btn):
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setFixedHeight(22)
            b.setMinimumWidth(0)
            b.setSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Fixed)
            b.setStyleSheet(self.material_list_widget_style)

        # Apply initial labels/bolding
        self._apply_sort_button_styles(name_btn, type_btn, time_btn)

        # Click behavior: toggle direction if same mode; otherwise select mode and reset to descending=False
        def on_click(mode):
            if self._sort_mode == mode:
                self._sort_desc = not self._sort_desc
            else:
                self._sort_mode = mode
                self._sort_desc = False
            self._apply_sort_button_styles(name_btn, type_btn, time_btn)
            self.refresh_materials_list()

        name_btn.clicked.connect(lambda: on_click('name'))
        type_btn.clicked.connect(lambda: on_click('type'))
        time_btn.clicked.connect(lambda: on_click('time'))

        title = QtWidgets.QLabel("Sort:")
        lay.addWidget(title)
        lay.addWidget(name_btn)
        lay.addWidget(type_btn)
        lay.addWidget(time_btn)
        lay.addStretch(1)

        return bar


    def _apply_sort_button_styles(self, name_btn, type_btn, time_btn):
        """
        Button text + bolding + chip-blue for active:
          • Name: ↓ A–Z, ↑ Z–A
          • Type: group by type then name; arrow flips whole order
          • Time: creation order; arrow flips order
        """
        arrow = "↑" if self._sort_desc else "↓"

        name_btn.setText(f"Name {arrow}" if self._sort_mode == 'name' else "Name")
        type_btn.setText(f"Type {arrow}" if self._sort_mode == 'type' else "Type")
        time_btn.setText(f"Time {arrow}" if self._sort_mode == 'time' else "Time")

        nf, tf, tif = name_btn.font(), type_btn.font(), time_btn.font()
        nf.setBold(self._sort_mode == 'name');  name_btn.setFont(nf)
        tf.setBold(self._sort_mode == 'type');  type_btn.setFont(tf)
        tif.setBold(self._sort_mode == 'time'); time_btn.setFont(tif)

        self._style_sort_button(name_btn, active=(self._sort_mode == 'name'))
        self._style_sort_button(type_btn, active=(self._sort_mode == 'type'))
        self._style_sort_button(time_btn, active=(self._sort_mode == 'time'))


    def _style_sort_button(self, btn, active=False):
        """
        Apply minimal, consistent button styling.
        Active buttons get chip-blue text (#00f7c8), others stay white.
        """
        txt_color = "#00f7c8" if active else "#ffffff"
        btn.setStyleSheet(f"""
            QPushButton {{
                color: {txt_color};
                background-color: #666666;
                border: 1px solid #444444;
                border-radius: 6px;
                padding: 2px 6px;
            }}
            QPushButton:hover   {{ background-color: #888888; }}
            QPushButton:pressed {{ background-color: #1a1a1a; }}
            QPushButton:disabled {{
                color: #666666;
                background-color: #4a4a4a;
                border: 1px solid #3d3d3d;
            }}
        """)



    # -------------------------------
    # 3) Entry Creation & Row Registry
    # -------------------------------


    # Create the row: swatch + name line edit; register in selection model.
    def add_material_entry(self, material, row, scroll_layout, default_materials, saved_selection=None):
        """
        Create and add a material entry with a checkbox, color swatch, and material name.

        Args:
            material (str): The name of the material.
            row (int): The row index to insert this material entry.
            scroll_layout (QGridLayout): The layout to which the entry will be added.
            default_materials (set): A set of default materials to exclude.
            saved_selection (set): A set of previously selected materials to reapply selection state.
        """
        # Create a horizontal layout to contain the checkbox, color swatch, and material name
        material_layout = QtWidgets.QHBoxLayout()
        material_layout.setContentsMargins(1, 1, 1, 1)
        material_layout.setSpacing(3)  # tighter spacing

        # No checkboxes anymore; swatch drives selection
        material_checkbox = None  # kept for minimal downstream edits
        # If you later reintroduce spacing where the checkbox was, add a small spacer here.


        # Try to find a suitable color attribute
        color_attr = self.get_material_color_attribute(material)

        # 1) Prefer a basecolor texture if one is driving this slot
        color_hex = None
        try:
            file_node = self._find_basecolor_file_node(material)
            if file_node:
                color_hex = self._file_average_color_hex(file_node)
        except Exception:
            color_hex = None

        # 2) Fallback to the attribute RGB value
        if not color_hex and color_attr:
            try:
                val = cmds.getAttr(f"{material}.{color_attr}")
                if isinstance(val, list) and len(val) == 1 and isinstance(val[0], (tuple, list)) and len(val[0]) == 3:
                    r, g, b = val[0]
                    color_hex = self._rgb_to_hex(r, g, b)
                else:
                    color_hex = "#808080"  # neutral mid-gray for visibility
            except Exception:
                color_hex = "#ffffff"

        # 3) Final fallback
        if not color_hex:
            color_hex = "#ffffff"


        # Create the color box (selectable unless default)
        color_box = self.create_color_box(color_hex, material, selectable=(material not in default_materials))
        # tag swatch with material name so we can find it later
        color_box._qm_material_name = material  # simple tag for lookup

        # Create a read-only or editable line edit for the material name (unify metrics)
        material_widget = LeftClipLineEdit(material)
        # Link clicks on the line edit to Outliner-style selection (owner + method name, guarded)
        material_widget.setSelectionHandler(self, "handle_item_click", material)
        # Start unselected
        material_widget.setProperty("qmSelected", "false")


        material_widget.style().unpolish(material_widget); material_widget.style().polish(material_widget)


        # Register this row for ordered selection behavior
        self._register_material_entry(material, color_box, material_widget, is_default=(material in default_materials))


        material_widget.setMinimumWidth(120)

        if material in default_materials:
            # Default materials: read-only, can’t take focus; use muted style via property
            material_widget.setReadOnly(True)
            material_widget.setFocusPolicy(QtCore.Qt.NoFocus)
            material_widget.setProperty("materialType", "default")
            material_widget.setProperty("editing", "false")   # ensure non-editing visual

        else:
            try:
                # Commit on focus-out
                material_widget.editingFinished.connect(partial(self.rename_material, material_widget))
                # Commit also when pressing Enter
                material_widget.returnPressed.connect(partial(self.rename_material, material_widget))
            except AttributeError:
                print("Error: rename_material function not found")

        # Apply sizes; container already has the stylesheet
        # material_widget.setStyleSheet(self.material_list_widget_style)  # not needed when parent has QSS
        material_widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                      QtWidgets.QSizePolicy.Fixed)  # Fixed height fits style

        material_widget.setMinimumHeight(22)  # aligns with LeftClipLineEdit min we set
        color_box.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)

        # Add only swatch + line edit (no checkbox)
        material_layout.addWidget(color_box, 0)
        material_layout.addWidget(material_widget, 1)

        # Make the line-edit take remaining space and shrink from the right
        material_layout.setStretch(0, 0)  # color swatch
        material_layout.setStretch(1, 1)  # line edit expands

        if isinstance(material_widget, QtWidgets.QLineEdit):
            material_widget.setAlignment(QtCore.Qt.AlignLeft)
            material_widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
            material_widget.setMinimumWidth(50)  # allow aggressive shrink

        # Create a container widget for the material entry and add it to the scroll layout
        entry_container = QtWidgets.QWidget()
        entry_container.setLayout(material_layout)
        scroll_layout.addWidget(entry_container, row, 0, 1, 4)

    # Create the action-row buttons under each entry (Assign / Select Objs / Graph / Imp Tx).
    def add_material_buttons(self, material, row, scroll_layout, is_default):
        """
        Create and add action buttons (Assign, Highlight, Select, Import Tx) for the material.
        The 'Import Tx' button is disabled for default materials but still displayed.

        Args:
            material (str): The name of the material.
            row (int): The row index to insert these buttons.
            scroll_layout (QGridLayout): The layout to which the buttons will be added.
            is_default (bool): Flag indicating if the material is a default material.
        """
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(3)  # tighter

        # Create and style common buttons
        assign_btn = QtWidgets.QPushButton("Assign")
        highlight_btn = QtWidgets.QPushButton("Select Objs")  # RENAMED
        graph_btn = QtWidgets.QPushButton("Graph")  # NEW

        # Make buttons a bit smaller
        for _b in (assign_btn, highlight_btn, graph_btn):
            _b.setFixedHeight(20)  # was 22
            _b.setSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Fixed)
            _b.setMinimumWidth(0)

        # Apply styles (include Graph now)
        for btn in (assign_btn, highlight_btn, graph_btn):
            btn.setStyleSheet(self.material_list_widget_style)

        # Connect signals (resolve current name from the row's QLineEdit at click-time)
        entry_idx = self._index_by_material.get(material)
        line_edit_ref = None
        if isinstance(entry_idx, int) and 0 <= entry_idx < len(self._entry_list):
            line_edit_ref = self._entry_list[entry_idx].get("line_edit")

        def _current_name():
            try:
                if line_edit_ref and isValid(line_edit_ref):
                    return line_edit_ref.text().strip()
            except Exception:
                pass
            return material  # fallback

        assign_btn.clicked.connect(lambda: self.assign_material(_current_name()))
        highlight_btn.clicked.connect(lambda: self.highlight_material(_current_name()))
        graph_btn.clicked.connect(lambda: self.graph_material_network(_current_name()))


        # Add common buttons to the layout (no separate Select button now)
        button_layout.addWidget(assign_btn)
        button_layout.addWidget(highlight_btn)
        button_layout.addWidget(graph_btn)  # NEW

        # Always create 'Import Tx' button
        import_tx_btn = QtWidgets.QPushButton("Imp Tx")
        import_tx_btn.setStyleSheet(self.material_list_widget_style)
        import_tx_btn.setFixedHeight(20)  # match smaller button height
        import_tx_btn.setSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Fixed)
        import_tx_btn.setMinimumWidth(0)

        if is_default:
            import_tx_btn.setEnabled(False)
            import_tx_btn.setToolTip("Cannot import textures for default materials.")


        else:
            # Enable the button and connect its signal
            import_tx_btn.setEnabled(True)
            import_tx_btn.clicked.connect(lambda: self.import_tx_material(_current_name()))

        # Add 'Import Tx' button to the layout
        button_layout.addWidget(import_tx_btn)

        # Create a container widget for the buttons and add it below the material entry
        button_container = QtWidgets.QWidget()
        button_container.setLayout(button_layout)
        scroll_layout.addWidget(button_container, row + 1, 0, 1, 4)

        # --- NEW: remember these rows so we can hide/show them globally ---
        if not hasattr(self, "_material_button_rows"):
            self._material_button_rows = []
        self._material_button_rows.append(button_container)

        # Respect current visibility state if it was toggled previously
        if hasattr(self, "_list_buttons_visible"):
            button_container.setVisible(bool(self._list_buttons_visible))

    # Register a row in internal structures for selection/lookup.
    def _register_material_entry(self, material, swatch, line_edit, is_default=False):
        idx = len(self._entry_list)
        self._entry_list.append({
            "material": material,
            "swatch": swatch,  # store direct refs in PySide2
            "line_edit": line_edit,  # guard with isValid() before use
            "is_default": bool(is_default),
        })
        self._index_by_material[material] = idx

    # Create a small, selection-aware color swatch widget for a material.
    def create_color_box(self, color_hex, material, selectable=True):
        """Create a non-interactive swatch that mirrors selection state."""
        # pass a no-op since we disabled swatch interaction
        color_box = ClickableColorSwatch(color_hex, on_clicked=None)
        color_box.setToolTip("Material color")
        if not selectable:
            color_box.setDisabledSelection(True)
        return color_box


    # Determine a suitable RGB attribute on a material node (baseColor/color/etc.).
    def get_material_color_attribute(self, material):
        """
        Return the name of a suitable color attribute for the given material, if any.
        This method tries each attribute in a predefined list in order, returning
        the first one that exists and is a triple (RGB) value. If none are found, returns None.
        """

        if not cmds.objExists(material):
            print(f"[DEBUG] Material '{material}' does not exist.")
            return None

        potential_color_attrs = [
            "color",
            "baseColor",
            "diffuseColor",
            "outColor",
            "specularColor"
        ]

        available_attrs = cmds.listAttr(material) or []
        # print(f"[DEBUG] Checking material: {material}")
        # print(f"[DEBUG] Available attributes: {available_attrs}")

        mat_type = cmds.nodeType(material)
        # print(f"[DEBUG] Material type: (mat_type)")

        for attr in potential_color_attrs:
            full_attr = f"{material}.{attr}"
            if full_attr in [f"{material}.{a}" for a in available_attrs]:
                try:
                    val = cmds.getAttr(full_attr)
                    if isinstance(val, list) and len(val) == 1 and isinstance(val[0], (tuple, list)) and len(val[0]) == 3:
                        return attr
                except Exception:
                    # keep searching other candidate attributes
                    continue
        return None


        # print("[DEBUG] No suitable color attribute found.")
        return None


    # -------------------------------
    # 4) Selection Model (List behavior + Visuals)
    # -------------------------------


    # Outliner-style selection handler for clicks (plain / Ctrl / Shift-range).
    def handle_item_click(self, material, source='lineedit', shift=False, ctrl=False):
        """Click = single, Shift = range from anchor, Ctrl = toggle single. Defaults are skipped."""
        # Ignore during rebuilds / teardown
        try:
            if getattr(self, "_rebuilding_list", False) or not self._is_ui_alive():
                return
        except Exception:
            return

        idx = self._index_by_material.get(material)
        if idx is None:
            return
        if not isinstance(self._entry_list, list) or idx < 0 or idx >= len(self._entry_list):
            return
        if self._entry_list[idx].get("is_default"):
            return

        sel = set(self.selected_materials_list or [])
        n = len(self._entry_list)

        if shift:
            # Determine a valid anchor
            anchor = self._selection_anchor if isinstance(self._selection_anchor, int) else None
            if anchor is None or anchor < 0 or anchor >= n:
                # Try to use the last selected visible material as the anchor
                anchor = None
                for m in reversed(self.selected_materials_list or []):
                    ai = self._index_by_material.get(m)
                    if isinstance(ai, int) and 0 <= ai < n:
                        anchor = ai
                        break
                # If still none, fall back to current idx so Shift acts like a single click
                if anchor is None:
                    anchor = idx

            a, b = sorted((anchor, idx))
            a = max(0, min(a, n - 1))
            b = max(0, min(b, n - 1))

            rng = [self._entry_list[i]["material"] for i in range(a, b + 1)
                   if not self._entry_list[i].get("is_default")]

            if ctrl:
                sel.update(rng)    # Ctrl+Shift adds a range
            else:
                sel = set(rng)     # pure Shift replaces with range

            # Keep the anchor stable like the Outliner
            self._selection_anchor = anchor

        elif ctrl:
            if material in sel:
                sel.remove(material)
            else:
                sel.add(material)
            # Anchor remains as last non-modified click

        else:
            # Plain click: replace selection with this material,
            # except if this is already the only selected item → toggle off.
            if material in sel and len(sel) == 1:
                sel = set()
                self._selection_anchor = None
            else:
                sel = {material}
                self._selection_anchor = idx  # set new anchor on plain click

        self.selected_materials_list = list(sel)
        # Remember the last clicked material for active selection in Maya
        self._last_selected_material = material

        # If Selected/Non-Selected filters are in play, suppress refresh briefly to allow double-click.
        try:
            flags = self._collect_filter_flags()
            if flags.get("selected") or flags.get("selectedOnly") or flags.get("nonSelected") or flags.get("nonSelectedOnly"):
                import time as _t
                self._dc_grace_deadline = _t.monotonic() + (max(80, int(self._dc_interval_ms)) / 1000.0)
        except Exception:
            pass

        self._apply_selection_visuals()
        # Plain click replaces materials subset; Shift/Ctrl adds materials
        self._defer_scene_select_from_list(additive=bool(shift or ctrl))
        self._update_delete_button_count()

    # Apply selection visuals to line edits and swatches based on internal selection list.
    def _apply_selection_visuals(self):
        """Sync visuals, safely (skip deleted) and avoid running while rebuilding."""
        if getattr(self, "_rebuilding_list", False):
            return
        sel = set(self.selected_materials_list or [])
        for entry in getattr(self, "_entry_list", []):
            m  = entry.get("material")
            le = entry.get("line_edit")
            sw = entry.get("swatch")

            if not le or not isValid(le):
                continue

            is_sel = (m in sel)
            le.setProperty("qmSelected", "true" if is_sel else "false")
            try:
                le.style().unpolish(le); le.style().polish(le)
            except Exception:
                pass


            # swatch (visual only)
            if sw and isValid(sw) and hasattr(sw, "setSelected"):
                try:
                    sw.setSelected(is_sel)
                except Exception:
                    pass

    # Toggle a single material selection (swatch-driven). Clears others unless extend=True.
    def toggle_material_from_checkbox(self, material, extend=False):
        """Toggle selection for one material; if not extend, clear others first."""
        if not extend:
            self._set_all_swatch_selection(False)
            self.selected_materials_list = []

        if material in self.selected_materials_list:
            # toggle off when extend True
            self.selected_materials_list.remove(material)
            self._set_swatch_selected(material, False)
        else:
            self.selected_materials_list.append(material)
            self._set_swatch_selected(material, True)

        self._update_delete_button_count()

    # Update delete button label with current count of selected materials.
    def _update_delete_button_count(self):
        btn = self.ui_elements.get('deleteSelectedMaterialsButton')
        if not btn:
            return
        n = len(self.selected_materials_list or [])
        if n == 1:
            btn.setText("Delete (1 item)")
        else:
            btn.setText(f"Delete ({n} items)")

    # Set visual selection state for a specific swatch by material name.
    def _set_swatch_selected(self, material, selected):
        """Find swatch for a material and set its visual state."""
        scrollArea = self.ui_elements.get('materialsListScrollArea')
        if not scrollArea or not scrollArea.widget():
            return
        for w in scrollArea.widget().findChildren(ClickableColorSwatch):
            if getattr(w, "_qm_material_name", None) == material:
                w.setSelected(bool(selected))

    # Set selection state for all visible swatches; optionally include defaults.
    def _set_all_swatch_selection(self, selected, include_defaults=False):
        """Set selection state on all visible swatches (optionally including defaults)."""
        scrollArea = self.ui_elements.get('materialsListScrollArea')
        if not scrollArea or not scrollArea.widget():
            return
        for w in scrollArea.widget().findChildren(ClickableColorSwatch):
            if w._disabled and not include_defaults:
                continue
            w.setSelected(bool(selected))
        # sync list
        if selected:
            # rebuild from visible, skipping defaults by design
            mats = []
            scroll_content = scrollArea.widget()
            for entry in scroll_content.findChildren(QtWidgets.QWidget):
                le = entry.findChild(QtWidgets.QLineEdit)
                if le:
                    mat = le.text()
                    # skip defaults via property if tagged
                    if le.property("materialType") == "default":
                        continue
                    mats.append(mat)
            self.selected_materials_list = mats
        else:
            self.selected_materials_list = []

        self._update_delete_button_count()

    # Toggle select/deselect all visible non-default materials; updates UI and mirrors to scene.
    def toggle_select_all_visible_materials(self):
        """
        Toggle between selecting and deselecting all visible materials.
        Skips default materials (non-selectable).
        """
        button = self.ui_elements.get('selectAllVisibleMaterialsButton')
        if not button:
            print("Error: Select All button not found.")
            return

        is_selecting_all = (button.text() == "Select All")

        if is_selecting_all:
            mats = [e["material"] for e in getattr(self, "_entry_list", []) if not e["is_default"]]
            self.selected_materials_list = mats
        else:
            self.selected_materials_list = []

        # Apply visuals + counts
        self._apply_selection_visuals()
        # Mirror selection to Maya scene (replace for bulk ops)
        self._defer_scene_select_from_list(additive=False)





        button.setText("Deselect All" if is_selecting_all else "Select All")

        self._update_delete_button_count()


    # -------------------------------
    # 5) Scene ⇄ List Selection Sync
    # -------------------------------


    # Install scriptJob to mirror Maya selection into the list.
    def _install_selection_watcher(self):
        """Listen to Maya selection changes so the list mirrors the scene."""
        try:
            import maya.cmds as cmds
            # Remove stale job first, just in case
            self._remove_selection_watcher()
            # Establish scriptJob for SelectionChanged
            self._sel_watcher_id = cmds.scriptJob(
                event=["SelectionChanged", lambda *a: self._on_maya_selection_changed()],
                protected=True
            )
            print(f"[QM][SelSync] scriptJob installed: {self._sel_watcher_id}")
        except Exception as e:
            print(f"[QM][SelSync] install failed: {e}")

    # Remove selection-change scriptJob (cleanup).
    def _remove_selection_watcher(self):
        """Stop listening to selection changes."""
        try:
            import maya.cmds as cmds
            if getattr(self, "_sel_watcher_id", None):
                if cmds.scriptJob(exists=self._sel_watcher_id):
                    cmds.scriptJob(kill=self._sel_watcher_id, force=True)
                print(f"[QM][SelSync] scriptJob removed: {self._sel_watcher_id}")
        except Exception as e:
            print(f"[QM][SelSync] remove failed: {e}")
        finally:
            self._sel_watcher_id = None

    # scriptJob callback → update list selection to match scene (unless we initiated it).
    def _on_maya_selection_changed(self):
        """Called by scriptJob; update list to match scene selection unless we initiated it."""
        if getattr(self, "_syncing_selection", False):
            return  # ignore our own programmatic changes
        self._sync_list_from_scene_selection()

    # Compute list selection from current Maya selection (materials only) and apply visuals.
    def _sync_list_from_scene_selection(self):
        """Mirror Maya's current selection into the list (materials only)."""
        if getattr(self, "_rebuilding_list", False):
            return
        try:
            import maya.cmds as cmds
            scene_mats = set(cmds.ls(sl=True, materials=True) or [])
        except Exception as e:
            print(f"[QM][SelSync] query failed: {e}")
            scene_mats = set()

        if not hasattr(self, "_entry_list"):
            return

        present = [e.get("material") for e in self._entry_list]
        new_sel = [m for m in present if m in scene_mats]  # keep list order

        self.selected_materials_list = new_sel
        if new_sel:
            self._selection_anchor = self._index_by_material.get(new_sel[-1], self._selection_anchor)

        if hasattr(self, "_apply_selection_visuals"):
            self._apply_selection_visuals()

        self._update_delete_button_count()

    # Mirror list selection back to Maya selection on the next tick (guard against feedback).
    def _defer_scene_select_from_list(self, additive=False):
        """
        Apply self.selected_materials_list to scene selection on the next tick.
        - Preserve meshes/transforms (non-materials) so filters remain stable.
        - Make the *last clicked material* active only if it’s still in the current selection.
        """
        self._syncing_selection = True

        def _apply_materials(preserve_meshes=True):
            try:
                new_mats = list(self.selected_materials_list or [])
                last_mat = getattr(self, "_last_selected_material", None)

                if not preserve_meshes:
                    # Full replace (materials only); do NOT re-inject last_mat if not selected
                    ordered = list(dict.fromkeys(new_mats))
                    if last_mat and last_mat in ordered:
                        ordered = [m for m in ordered if m != last_mat] + [last_mat]
                    if ordered:
                        cmds.select(ordered, r=True, ne=True)
                    else:
                        cmds.select(clear=True)
                    return

                # Preserve any non-material nodes (meshes, transforms, etc.)
                cur_all = cmds.ls(sl=True) or []
                cur_mats = set(cmds.ls(sl=True, materials=True) or [])
                cur_non_mats = [n for n in cur_all if n not in cur_mats]

                if additive:
                    # Build selection strictly from current non-materials + new materials
                    ordered = list(dict.fromkeys(cur_non_mats + new_mats))
                else:
                    # Replace only the materials subset; keep non-materials intact
                    ordered = list(dict.fromkeys(cur_non_mats + new_mats))

                # Only reorder to make last_mat active if it’s actually in the new selection
                if last_mat and last_mat in ordered:
                    ordered = [n for n in ordered if n != last_mat] + [last_mat]

                cmds.select(ordered, r=True, ne=True)
            except Exception as e:
                print(f"[QM] Scene selection failed: {e}")


        def _do():
            try:
                _apply_materials(preserve_meshes=True)
            finally:
                self._syncing_selection = False

        QtCore.QTimer.singleShot(0, _do)


    # -------------------------------
    # 6) Auto-Refresh Plumbing (Scene Events + Debounce)
    # -------------------------------


    # Install scriptJobs/OpenMaya callbacks that trigger a debounced list refresh.
    def _install_material_watchers(self):
        """
        Install Maya scriptJobs that trigger a debounced refresh of the materials list
        whenever relevant scene/material events occur.
        """

        # guard: don't double-install
        if getattr(self, "_material_watch_job_ids", None):
            return

        # Debounce timer (create once)
        if not hasattr(self, "_mat_refresh_timer"):
            self._mat_refresh_timer = QtCore.QTimer(self)
            self._mat_refresh_timer.setSingleShot(True)
            # Single connection; we only start/stop the timer later
            self._mat_refresh_timer.timeout.connect(self.refresh_materials_list)

        self._material_watch_job_ids = []
        self._om_callbacks = []  # OpenMaya callback ids

        # Probe supported scriptJob events once
        try:
            _events = set(cmds.scriptJob(listEvents=True) or [])
        except Exception:
            _events = set()
        # Optional: one-shot debug dump (comment out if too chatty)
        print(f"[QM][SJ] Available events: {len(_events)} found")

        # Weak self for safe callbacks
        self_ref = weakref.ref(self)


        # Find a parent UI control for scriptJobs so they auto-kill with the UI
        _sj_parent = None
        try:
            if cmds.workspaceControl(self.workspace_control_name, q=True, exists=True):
                _sj_parent = self.workspace_control_name
            else:
                # fall back to the dialog objectName (we set it in __init__)
                _sj_parent = self.objectName()
        except Exception:
            _sj_parent = self.objectName()

        # Safe callable that checks the instance on each event
        def _safe_scene_event_cb(*_):
            inst = self_ref()
            if not inst or not isValid(inst):
                return
            inst._on_material_scene_event()

        # Robust add: only attempt scriptJobs that this host actually supports; then OM v2→v1
        def _add_job_multi(event_names, om_fallback_pair=None):
            """
            event_names: str or list of scriptJob names to try.
            om_fallback_pair: (add_v2_cb, add_v1_cb) callables returning callback id or None.
            """
            candidates = [event_names] if isinstance(event_names, str) else list(event_names)

            # Filter to supported events first
            supported = [ev for ev in candidates if ev in _events]
            for ev in supported:
                try:
                    if _sj_parent:
                        jid = cmds.scriptJob(e=(ev, _safe_scene_event_cb), protected=True, parent=_sj_parent)
                    else:
                        jid = cmds.scriptJob(e=(ev, _safe_scene_event_cb), protected=True)
                    self._material_watch_job_ids.append(jid)
                    return True
                except Exception:
                    continue

            # If none supported (or failed), try OpenMaya fallbacks
            if om_fallback_pair:
                add_v2, add_v1 = om_fallback_pair
                for reg in (add_v2, add_v1):
                    try:
                        cb_id = reg(self_ref)
                        if cb_id:
                            self._om_callbacks.append(cb_id)
                            return True
                    except Exception:
                        continue

            # Stay quiet if unsupported here; other watchers still work
            return False

        # OM v2/v1 callback creators — capture weakref safely
        def _om_v2_node_added(self_ref_wr):
            try:
                from maya.api import OpenMaya as om
                def _cb(obj, *a):
                    inst = self_ref_wr()
                    if inst and isValid(inst):
                        inst._on_material_scene_event()
                return om.MDGMessage.addNodeAddedCallback(_cb, None)
            except Exception:
                return None

        def _om_v1_node_added(self_ref_wr):
            try:
                import maya.OpenMaya as om1
                def _cb(obj, clientData):
                    inst = self_ref_wr()
                    if inst and isValid(inst):
                        inst._on_material_scene_event()
                return om1.MDGMessage.addNodeAddedCallback(_cb, None)
            except Exception:
                return None

        def _om_v2_node_removed(self_ref_wr):
            try:
                from maya.api import OpenMaya as om
                def _cb(obj, *a):
                    inst = self_ref_wr()
                    if inst and isValid(inst):
                        inst._on_material_scene_event()
                return om.MDGMessage.addNodeRemovedCallback(_cb, None)
            except Exception:
                return None

        def _om_v1_node_removed(self_ref_wr):
            try:
                import maya.OpenMaya as om1
                def _cb(obj, clientData):
                    inst = self_ref_wr()
                    if inst and isValid(inst):
                        inst._on_material_scene_event()
                return om1.MDGMessage.addNodeRemovedCallback(_cb, None)
            except Exception:
                return None

        # Try scriptJobs first (only if available), then OM API fallbacks
        _add_job_multi(("NodeAdded", "DagObjectCreated"), om_fallback_pair=(_om_v2_node_added, _om_v1_node_added))
        _add_job_multi(("NodeRemoved", "DagObjectRemoved"), om_fallback_pair=(_om_v2_node_removed, _om_v1_node_removed))
        _add_job_multi("Undo")
        _add_job_multi("Redo")
        _add_job_multi("SceneOpened")
        _add_job_multi("NewSceneOpened")

        # NEW: reference/import events (only register if host supports them)
        for ev in (
            "AfterReferenceLoad", "AfterReferenceUnload",
            "ReferenceEditsAdded", "ReferenceEditsRemoved",
            "AfterImport", "AfterFileRead", "PostSceneRead"
        ):
            _add_job_multi(ev)



        # NEW: only refresh on selection changes when "Selected Only" filter is active
        try:
            def _safe_sel_cb(*_):
                inst = self_ref()
                if not inst or not isValid(inst):
                    return
                inst._on_selection_changed()
            if "SelectionChanged" in _events:
                if _sj_parent:
                    jid = cmds.scriptJob(e=("SelectionChanged", _safe_sel_cb), protected=True, parent=_sj_parent)
                else:
                    jid = cmds.scriptJob(e=("SelectionChanged", _safe_sel_cb), protected=True)
                self._material_watch_job_ids.append(jid)
            else:
                # Fallback to OM selection callback if needed (quietly)
                from maya.api import OpenMaya as om
                def _om_sel_cb(*__):
                    inst = self_ref()
                    if inst and isValid(inst):
                        inst._on_selection_changed()
                try:
                    sel_list = om.MGlobal.getActiveSelectionList()  # touch API to ensure module ok
                    # No direct selectionChanged callback in MDGMessage; rely on existing timers/debounce when needed
                except Exception:
                    pass
        except Exception:
            pass




        # Ensure clean-up when this Qt object is destroyed
        try:
            # When the widget is deleted, remove jobs
            self.destroyed.connect(self._remove_material_watchers)
        except Exception:
            pass

        def _om_v2_node_added_shaders(self_ref_wr):
            try:
                from maya.api import OpenMaya as om
                def _cb(obj, *a):
                    inst = self_ref_wr()
                    if not (inst and isValid(inst)):
                        return
                    try:
                        name = om.MFnDependencyNode(obj).name()
                    except Exception:
                        name = None
                    if name and inst._is_material_node_type(name):
                        inst._on_material_scene_event()  # debounce and refresh
                return om.MDGMessage.addNodeAddedCallback(_cb, None)
            except Exception:
                return None

        def _om_v1_node_added_shaders(self_ref_wr):
            try:
                import maya.OpenMaya as om1
                def _cb(obj, clientData):
                    inst = self_ref_wr()
                    if not (inst and isValid(inst)):
                        return
                    try:
                        fn = om1.MFnDependencyNode(obj)
                        name = fn.name()
                    except Exception:
                        name = None
                    if name and inst._is_material_node_type(name):
                        inst._on_material_scene_event()
                return om1.MDGMessage.addNodeAddedCallback(_cb, None)
            except Exception:
                return None


        # Initial kick to ensure we're up-to-date
        self._queue_material_refresh(0)

        _add_job_multi(("NodeAdded",), om_fallback_pair=(_om_v2_node_added_shaders, _om_v1_node_added_shaders))


    def _poll_materials_snapshot(self):
        """Fallback poll: if material set changes due to create/delete, refresh list (ignore renames)."""
        import time as _t
        if getattr(self, "_suspend_refresh_count", 0) > 0 or _t.monotonic() < getattr(self, "_mute_poll_until_ts", 0.0):
            return
        try:
            HIDDEN_MATERIALS = getattr(self, "HIDDEN_MATERIALS", {'particleCloud1'})
            mats = set(m for m in (cmds.ls(materials=True) or []) if m not in HIDDEN_MATERIALS)
        except Exception:
            mats = set()
        if mats != getattr(self, "_last_materials_snapshot", set()):
            self._last_materials_snapshot = mats
            self._queue_material_refresh(0)


    # Remove installed scriptJobs/OM callbacks (on widget destroy).
    def _remove_material_watchers(self, *args):
        """Kill installed scriptJobs to avoid leaks."""
        job_ids = list(getattr(self, "_material_watch_job_ids", []) or [])
        for jid in job_ids:
            try:
                if cmds.scriptJob(exists=jid):
                    cmds.scriptJob(kill=jid, force=True)
            except Exception:
                pass
        self._material_watch_job_ids = []


        # Remove OpenMaya callbacks (v2 then v1)
        ok = False
        try:
            from maya.api import OpenMaya as om
            for cb in getattr(self, "_om_callbacks", []) or []:
                try:
                    om.MMessage.removeCallback(cb)
                    ok = True
                except Exception:
                    pass
        except Exception:
            pass
        if not ok:
            try:
                import maya.OpenMaya as om1
                for cb in getattr(self, "_om_callbacks", []) or []:
                    try:
                        om1.MMessage.removeCallback(cb)
                    except Exception:
                        pass
            except Exception:
                pass
        self._om_callbacks = []

        # Stop polling fallback
        try:
            if hasattr(self, "_material_poll_timer") and self._material_poll_timer is not None:
                self._material_poll_timer.stop()
        except Exception:
            pass

    # Generic scene-event callback → schedule a list refresh (debounced).
    def _on_material_scene_event(self, *args):
        """scriptJob callback → debounce a UI refresh."""
        self._queue_material_refresh(150)

    # Debounce helper: coalesce refresh requests within delay_ms.
    def _queue_material_refresh(self, delay_ms=120):
        """Debounced refresh: coalesces bursts of events into a single UI update."""
        # Suppress refresh while we're doing an in-place rename or muted poll window
        if getattr(self, "_suspend_refresh_count", 0) > 0:
            return
        if hasattr(self, "_mat_refresh_timer") and self._mat_refresh_timer is not None:
            self._mat_refresh_timer.stop()
            self._mat_refresh_timer.start(max(0, int(delay_ms)))


    # When "Selected Only" is active, react quickly to selection changes.
    def _on_selection_changed(self, *args):
        """Refresh quickly when Selected/Non-Selected filters are active, but not during double-click grace."""
        # If either Selected Only or Non-Selected filters are relevant
        if self._checkbox_state('selectedOnlyFilterCheckbox') or self._checkbox_state('nonSelectedOnlyFilterCheckbox'):
            # Suppress refresh until the grace window ends so double-click can land on the same widget
            import time as _t
            if getattr(self, "_dc_grace_deadline", 0.0) > _t.monotonic():
                return
            self._queue_material_refresh(120)


    def _is_material_node_type(self, node_name):
        """Return True if node is a shader/material (surface/volume/displacement)."""
        try:
            t = cmds.nodeType(node_name)
            if not t:
                return False
            classes = cmds.getClassification(t) or []
            flat = "/".join(classes).lower()
            # catches 'shader/surface', 'shader/volume', 'shader/displacement', etc.
            return "shader" in flat
        except Exception:
            return False


    # -------------------------------
    # 7) Material Actions (Row Buttons)
    # -------------------------------

    # Assign material to current selection (handles lambert1 → initialShadingGroup).
    def assign_material(self, material):
        """
        Assign the selected material to the currently selected objects.
        Handles default materials like lambert1 by using initialShadingGroup.
        """
        selected_objs = cmds.ls(selection=True, flatten=True)
        if not selected_objs:
            cmds.warning("No objects selected.")
            return

        # Retrieve the shading group for the material
        if material == "lambert1":
            shading_group = "initialShadingGroup"
            print(f"Using {shading_group} for lambert1.")
        else:
            shading_group = cmds.listConnections(f"{material}.outColor", type="shadingEngine")

            if not shading_group:
                cmds.warning(f"No shading group found for material {material}.")
                return

            shading_group = shading_group[0]  # Use the first shading group

        # Assign the shading group to the selected objects
        cmds.undoInfo(openChunk=True)
        try:
            for obj in selected_objs:
                cmds.sets(obj, edit=True, forceElement=shading_group)
                print(f"Assigned {material} to {obj}.")
        except Exception as e:
            cmds.warning(f"Failed to assign material: {e}")
        finally:
            cmds.undoInfo(closeChunk=True)

    # Select all objects using the material (via shadingEngine membership).
    def highlight_material(self, material):
        shading_group = cmds.listConnections(material + '.outColor', type='shadingEngine')
        if not shading_group:
            cmds.warning(f"No shading group found for material {material}.")
            return

        shading_group = shading_group[0]
        objects_with_material = cmds.sets(shading_group, q=True)
        if objects_with_material:
            # Replace selection with all objects using this material
            cmds.select(objects_with_material, r=True, ne=True)
        else:
            cmds.warning(f"No objects found with material {material}.")


    # Select the material node itself.
    def select_material(self, material):
        # Replace only materials subset; keep meshes selected; make this material active
        self.selected_materials_list = [material]
        self._last_selected_material = material
        self._apply_selection_visuals()
        self._defer_scene_select_from_list(additive=False)

    # Rename material from line-edit edit; triggers refresh on success.
    def rename_material(self, material_name_edit):
        """
        Rename in-place without rebuilding the list UI.
        Uses Maya's returned name (handles duplicate -> name1) and updates maps.
        """
        import time as _t
        prev_name = getattr(material_name_edit, "_pre_edit_text", None) or material_name_edit.text()
        new_name = (material_name_edit.text() or "").strip()

        if new_name == prev_name:
            return

        # Silence auto-refreshers briefly so no rebuild occurs during rename
        self._begin_silent_refresh(mute_ms=800)

        def _update_internal_maps(_old, _actual_new):
            # Update entry registry and index map
            idx = self._index_by_material.pop(_old, None)
            if isinstance(idx, int) and 0 <= idx < len(getattr(self, "_entry_list", [])):
                self._entry_list[idx]["material"] = _actual_new
                self._index_by_material[_actual_new] = idx

                # update swatch tag if present
                sw = self._entry_list[idx].get("swatch")
                try:
                    if sw and isValid(sw):
                        setattr(sw, "_qm_material_name", _actual_new)
                except Exception:
                    pass

                # update line edit's bound material name and selection handler
                le = self._entry_list[idx].get("line_edit")
                try:
                    if le and isValid(le):
                        le._qm_material_name = _actual_new
                        if hasattr(le, "setSelectionHandler"):
                            le.setSelectionHandler(self, "handle_item_click", _actual_new)
                except Exception:
                    pass

            # Update selection lists and anchor/last
            sel = getattr(self, "selected_materials_list", []) or []
            self.selected_materials_list = [(_actual_new if m == _old else m) for m in sel]
            if getattr(self, "_last_selected_material", None) == _old:
                self._last_selected_material = _actual_new

            # Keep poll snapshot in sync so poller doesn't think a create/delete happened
            try:
                HIDDEN_MATERIALS = getattr(self, "HIDDEN_MATERIALS", {'particleCloud1'})
                mats = set(m for m in (cmds.ls(materials=True) or []) if m not in HIDDEN_MATERIALS)
                self._last_materials_snapshot = mats
            except Exception:
                pass

            # Refresh selection visuals only (no rebuild)
            if hasattr(self, "_apply_selection_visuals"):
                self._apply_selection_visuals()

        try:
            if not new_name:
                # revert and keep everything as-is
                material_name_edit.setText(prev_name)
                try:
                    material_name_edit._pre_edit_text = prev_name
                except Exception:
                    pass
                return

            # Perform Maya rename; this returns the ACTUAL final name (handles duplicates -> name1)
            actual_new = cmds.rename(prev_name, new_name)

            # Update the widget text to the real new name and baseline for next edit
            material_name_edit.setText(actual_new)
            try:
                material_name_edit._pre_edit_text = actual_new
            except Exception:
                pass

            # Immediately lock again after pressing Enter
            if not material_name_edit.isReadOnly():
                material_name_edit.setReadOnly(True)
                material_name_edit.setProperty("editing", "false")
                material_name_edit.style().unpolish(material_name_edit)
                material_name_edit.style().polish(material_name_edit)
                material_name_edit.update()

            # Update all our internal mappings to the ACTUAL name
            _update_internal_maps(prev_name, actual_new)

            # Re-polish just this widget so visuals stay crisp
            try:
                material_name_edit.style().unpolish(material_name_edit); material_name_edit.style().polish(material_name_edit)
            except Exception:
                pass

        except Exception as e:
            # On failure, revert visible text; keep maps untouched
            material_name_edit.setText(prev_name)
            try:
                material_name_edit._pre_edit_text = prev_name
            except Exception:
                pass
            cmds.warning(f"Failed to rename material: {e}")
        finally:
            # Allow normal refreshes again after the mute window
            self._end_silent_refresh()

    # Launch ImportTxTool for a given material and type; manages singleton instance.
    def import_tx_material(self, material=None):
        """Opens the Import Tx Tool UI for the selected material."""
        if not material:
            cmds.warning("No material selected for importing textures.")
            return

        # Check if an existing instance of ImportTxTool is open and close it
        if self.import_tx_tool:
            if self.import_tx_tool.isVisible():
                self.import_tx_tool.close()
                self.import_tx_tool = None

        try:
            material_type = cmds.nodeType(material)  # Get the material type dynamically
        except Exception as e:
            cmds.warning(f"Error retrieving material type: {e}")
            return

        # Initialize and show the Import Tx Tool with the correct material and type
        self.import_tx_tool = ImportTxTool(material=material, material_type=material_type, parent=maya_main_window())
        self.import_tx_tool.show()

    # Open Node Editor and graph the material + upstream + SGs; frame selection.
    def graph_material_network(self, material, hops_up=6, step_delay_ms=40, open_timeout_ms=1500):
        """
        Robust one-click graph with debug prints and micro timers.
        ...
        """
        prev_sel = cmds.ls(sl=True) or []

        print(f"[QM][Graph] Requested material: {material}")
        print(f"[QM][Graph] Preserving selection: {prev_sel}")

        # ---- open editor and poll until control exists ----
        start = time.time()
        try:
            mel.eval('NodeEditorWindow;')
            print("[QM][Graph] NodeEditorWindow; executed")
        except Exception as e:
            ...

        settle_delay_ms = 120
        pre_frame_delay_ms = 120

        def _poll_editor():
            try:
                ed_local = mel.eval('getCurrentNodeEditor;')

            except Exception:
                ed_local = None
            ready = bool(ed_local and cmds.control(ed_local, exists=True))
            elapsed = int((time.time() - start) * 1000)
            print(f"[QM][Graph] Poll editor: ready={ready} ed={ed_local} elapsed={elapsed}ms")
            if ready:
                # NEW: frameAll immediately after opening, then proceed to add nodes
                QtCore.QTimer.singleShot(pre_frame_delay_ms, lambda: _phase_pre_frame_all(ed_local))
            elif elapsed < open_timeout_ms:
                QtCore.QTimer.singleShot(step_delay_ms, _poll_editor)
            else:
                cmds.warning("[QM] Node Editor control not ready (timeout).")

        # Kick off polling → Phase A → Phase B → Phase C (start after a small settle)
        QtCore.QTimer.singleShot(settle_delay_ms, _poll_editor)

        # ---- helpers ----
        def _walk_inputs(seed_nodes, max_hops=6):
            if not seed_nodes:
                return []
            visited = set(seed_nodes)
            frontier = list(seed_nodes)
            out = list(seed_nodes)
            for _ in range(max_hops):
                nxt = []
                for n in list(frontier):
                    try:
                        upstream = cmds.listConnections(n, source=True, destination=False) or []
                    except Exception:
                        upstream = []
                    for u in upstream:
                        if u not in visited and cmds.objExists(u):
                            visited.add(u)
                            out.append(u)
                            nxt.append(u)
                if not nxt:
                    break
                frontier = nxt
            return out

        def _material_sgs(mat):
            """
            Return SGs the material feeds, preferring ones where `sg.surfaceShader`
            is driven by `mat` (most common case). Filters out default SGs.
            """
            try:
                # All downstream SGs from the material
                raw_sgs = set(cmds.listConnections(mat, type='shadingEngine', s=False, d=True) or [])
                raw_sgs.update(cmds.listConnections(f'{mat}.outColor', type='shadingEngine', s=False, d=True) or [])
                sgs = [sg for sg in raw_sgs if sg not in ('initialShadingGroup', 'initialParticleSE')]
                if not sgs:
                    return []

                # Rank: SGs whose surfaceShader is fed by this material come first
                preferred, others = [], []
                for sg in sgs:
                    try:
                        drivers = cmds.listConnections(f'{sg}.surfaceShader', s=True, d=False, plugs=True) or []
                        if any(d.split('.')[0] == mat for d in drivers):
                            preferred.append(sg)
                        else:
                            others.append(sg)
                    except Exception:
                        others.append(sg)
                # Keep unique order
                ordered = list(dict.fromkeys(preferred + others))
                return ordered
            except Exception:
                return []


        # ---- PHASE A: add nodes ----
        def _phase_add(ed_local):
            # Recompute seeds & upstream right before add (in case scene changed)
            sgs = _material_sgs(material)
            upstream = _walk_inputs([material], max_hops=hops_up)

            # Everything we want to appear in the editor:
            nodes_to_add = list(dict.fromkeys(list(sgs) + [material] + upstream))
            # Everything we want to SELECT & FRAME at the end (material + SG + inputs):
            nodes_to_select = list(dict.fromkeys(list(sgs) + [material] + upstream))

            print(f"[QM][Graph][A] Editor: {ed_local}")
            print(f"[QM][Graph][A] SGs: {sgs}")
            print(f"[QM][Graph][A] Upstream count: {len(upstream)} total add: {len(nodes_to_add)}")

            added = 0
            for n in nodes_to_add:
                try:
                    cmds.nodeEditor(ed_local, e=True, addNode=n)
                    added += 1
                except Exception as e:
                    print(f"[QM][Graph][A] addNode fail: {n} | {e}")
            print(f"[QM][Graph][A] Added {added}/{len(nodes_to_add)} nodes")

            # Schedule a second add pass (belt & suspenders), then frame
            QtCore.QTimer.singleShot(step_delay_ms, lambda: _phase_add_again(ed_local, nodes_to_add, nodes_to_select))

        def _phase_add_again(ed_local, nodes_to_add, nodes_to_select):
            print(f"[QM][Graph][B] Re-adding {len(nodes_to_add)} nodes to ensure visibility")
            for n in nodes_to_add:
                try:
                    cmds.nodeEditor(ed_local, e=True, addNode=n)
                except Exception:
                    pass
            QtCore.QTimer.singleShot(step_delay_ms, lambda: _phase_frame(ed_local, nodes_to_select))

        def _phase_pre_frame_all(ed_local):
            print(f"[QM][Graph][PRE] frameAll on editor before adding nodes: {ed_local}")
            try:
                cmds.nodeEditor(ed_local, e=True, frameAll=True)
                print("[QM][Graph][PRE] frameAll() done")
            except Exception as e:
                print(f"[QM][Graph][PRE] frameAll failed: {e}")
            # After a short settle, continue with adding SG + material + inputs
            QtCore.QTimer.singleShot(settle_delay_ms, lambda: _phase_add(ed_local))

        def _phase_frame(ed_local, nodes_to_select):
            print(f"[QM][Graph][C] Frame SELECTED (material + inputs) on editor: {ed_local}")
            # Build the final list to pick (ensure material is present)
            to_pick = list(dict.fromkeys(([material] if material else []) + (nodes_to_select or [])))

            # 1) Select INSIDE the Node Editor (guarantees SGs are truly selected for frameSelected)
            try:
                cmds.nodeEditor(ed_local, e=True, clearSelection=True)
                picked = 0
                for n in to_pick:
                    if cmds.objExists(n):
                        try:
                            cmds.nodeEditor(ed_local, e=True, selectNode=n)
                            picked += 1
                        except Exception:
                            pass
                print(f"[QM][Graph][C] Node Editor selected {picked}/{len(to_pick)} nodes")
            except Exception as e:
                print(f"[QM][Graph][C] Editor selection failed: {e}")

            # 2) Also select in the scene WITHOUT expanding sets (so SGs remain selected)
            try:
                try:
                    cmds.selectType(all=True)       # reset masks safely
                    cmds.selectType(sets=True)      # allow selecting set-type nodes (shadingEngine)
                except Exception:
                    pass
                if to_pick:
                    cmds.select(to_pick, r=True, ne=True)  # noExpand
                    mel.eval('select -r -noExpand {};'.format(" ".join('"%s"' % n for n in to_pick)))
            except Exception as e:
                print(f"[QM][Graph][C] Scene selection (noExpand) failed: {e}")

            # 3) Frame only the selection in the Node Editor; MEL fallback if needed
            did_frame_selected = False
            try:
                cmds.nodeEditor(ed_local, e=True, frameSelected=True)
                did_frame_selected = True
            except Exception:
                try:
                    mel_cmd = 'nodeEditor -e -frameSelected "{}";'.format(ed_local)
                    mel.eval(mel_cmd)
                    did_frame_selected = True
                except Exception:
                    pass

            if did_frame_selected:
                print("[QM][Graph][C] frameSelected() done")
            else:
                try:
                    cmds.nodeEditor(ed_local, e=True, frameAll=True)
                    print("[QM][Graph][C] frameAll() fallback done")
                except Exception as e:
                    print(f"[QM][Graph][C] frameAll fallback failed: {e}")

        # Kick off polling → Phase A → Phase B → Phase C
        _poll_editor()

    # Master toggle to show/hide all action-button rows under each entry.
    def toggle_material_list_buttons(self):
        """
        Show/hide the action-row buttons ('Assign', 'Highlight', 'Select', 'Graph', 'Import Tx')
        for every material entry. Uses self._material_button_rows gathered during population.
        """
        # default to visible if never toggled
        if not hasattr(self, "_list_buttons_visible"):
            self._list_buttons_visible = True

        self._list_buttons_visible = not self._list_buttons_visible

        rows = getattr(self, "_material_button_rows", []) or []
        for row_w in rows:
            try:
                if row_w and row_w.parent():
                    row_w.setVisible(self._list_buttons_visible)
            except RuntimeError:
                pass  # stale Qt ptr

        # Nudge layout
        self.resize_ui()

    # Check a named checkbox state (safe).
    def _checkbox_state(self, name):
        w = self.ui_elements.get(name)
        return w.isChecked() if w else False

    # True if node is referenced (referenceQuery guard).
    def _is_referenced(self, node):
        try:
            # Works on nodes that may or may not be in a reference
            return cmds.referenceQuery(node, isNodeReferenced=True)
        except Exception:
            return False

    # Return connected shading engines for a material (filters defaults).
    def _connected_shading_engines(self, material):
        try:
            sgs = set(cmds.listConnections(f"{material}.outColor", type="shadingEngine", s=False, d=True) or [])
            sgs.update(cmds.listConnections(material, type="shadingEngine", s=False, d=True) or [])
            # Filter default SGs; you can expand this as desired
            return [sg for sg in sgs if sg not in ('initialShadingGroup', 'initialParticleSE')]
        except Exception:
            return []

    # True if any connected shadingEngine has members.
    def _is_material_used(self, material):
        """A material is 'used' if any connected SG has any members."""
        for sg in self._connected_shading_engines(material):
            try:
                if cmds.sets(sg, q=True):
                    return True
            except Exception:
                pass
        return False

    # True if the material is assigned to any of the selected shapes.
    def _material_affects_any_of_selection(self, material, sel_shapes):
        """True if the material is assigned to any of the selected shapes."""
        if not sel_shapes:
            return False
        sgs = self._connected_shading_engines(material)
        if not sgs:
            return False
        sel_set = set(sel_shapes)
        for sg in sgs:
            try:
                members = cmds.sets(sg, q=True) or []
                # Compare sets directly; names can be long-path, so we normalize to node names
                if any((m.split('|')[-1] in s or s.split('|')[-1] in m) for s in sel_set for m in members):
                    return True
            except Exception:
                pass
        return False

    # -------------------------------
    # 9) Utilities / Qt Guards / Misc
    # -------------------------------

    # Safe widget fetch by objectName, repairing stale self.ui_elements entries.
    def _get_widget(self, name, cls=QtWidgets.QWidget):
        """Return a fresh, valid widget by objectName; repairs stale ui_elements entries."""
        w = self.ui_elements.get(name)
        try:
            # shiboken.isValid is the safest check across PySide2/6
            from shiboken2 import isValid as _is_valid
        except Exception:
            try:
                from shiboken6 import isValid as _is_valid
            except Exception:
                _is_valid = lambda obj: bool(obj)

        if not (w and _is_valid(w)):
            w = self.findChild(cls, name)
            if w and _is_valid(w):
                self.ui_elements[name] = w
            else:
                return None
        return w

    # True if this QWidget is still valid (guards timers/scriptJobs).
    def _is_ui_alive(self):
        """True if 'self' QWidget is still valid (guards against stale callbacks)."""
        try:
            from shiboken2 import isValid as _is_valid
        except Exception:
            try:
                from shiboken6 import isValid as _is_valid
            except Exception:
                _is_valid = lambda obj: bool(obj)
        return _is_valid(self) and getattr(self, "parent", None) is not None

    # Legacy checkbox callback → maintain selected_materials_list and update delete label.
    def toggle_material_from_checkbox(self, state, material):
        """
        Toggle the material in the class's selection list based on checkbox state.

        Args:
            state (int): State of the checkbox (checked or unchecked).
            material (str): The name of the material associated with the checkbox.
        """
        selected_materials_list = self.selected_materials_list
        deleteSelectedMaterialsButton = self.ui_elements.get('deleteSelectedMaterialsButton')

        if state == QtCore.Qt.Checked:
            if material not in selected_materials_list:
                selected_materials_list.append(material)
        else:
            if material in selected_materials_list:
                selected_materials_list.remove(material)

        # Update the delete button text with the count of selected materials
        if deleteSelectedMaterialsButton:
            deleteSelectedMaterialsButton.setText(f"Delete Selected ({len(selected_materials_list)} items)")

    # Helper: Is an object currently a member of a given shadingEngine?
    def is_object_in_shading_group(self, obj, shading_group):
        current_shading_groups = cmds.listConnections(obj, type='shadingEngine')
        return current_shading_groups and shading_group in current_shading_groups


    # -------------------------------
    # 10) Bulk Ops & UX bits
    # -------------------------------

    # Delete all selected materials after confirmation; refresh UI; resets select-all label.
    def delete_selected_materials(self):
        """
        Delete the selected materials after user confirmation.
        Displays a confirmation dialog and deletes materials if confirmed.
        """
        selected_materials_list = self.selected_materials_list
        scrollArea = self.ui_elements.get('materialsListScrollArea')
        deleteSelectedMaterialsButton = self.ui_elements.get('deleteSelectedMaterialsButton')
        selectAllButton = self.ui_elements.get('selectAllVisibleMaterialsButton')  # Get the select all button

        # Check if there are selected materials
        if not selected_materials_list:
            cmds.warning("No materials selected for deletion.")
            return

        # Create a confirmation dialog
        confirmation = QtWidgets.QMessageBox.question(
            None,
            "Confirm Deletion",
            f"Delete {len(selected_materials_list)} materials?",
            QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel
        )

        # If the user confirms, delete the selected materials
        if confirmation == QtWidgets.QMessageBox.Ok:
            cmds.undoInfo(openChunk=True)
            try:
                for material in selected_materials_list:
                    if cmds.objExists(material):
                        cmds.delete(material)
                cmds.warning(f"Deleted {len(selected_materials_list)} materials.")
            except Exception as e:
                cmds.warning(f"Failed to delete materials: {e}")
            finally:
                cmds.undoInfo(closeChunk=True)

            # Clear the selected materials list and update the UI
            self.selected_materials_list = []
            deleteSelectedMaterialsButton.setText("Delete Selected (0 items)")
            self.populate_materials_scroll_area()

            # Set the select all button's text back to "Select All"
            if selectAllButton:
                selectAllButton.setText("Select All")

    # Clear the material search line edit.
    def clear_material_search(self):
        """
        Clears the text in the material search line edit field.
        """
        material_search_line_edit = self.ui_elements.get('materialSearchLineEdit')

        if material_search_line_edit:
            material_search_line_edit.clear()
            print("Cleared material search input.")
        else:
            print("Error: materialSearchLineEdit not found.")

    # -------------------------------
    # 11) Small UI Callbacks (Checkbox glue)
    # -------------------------------

    # Helper: Is an object currently a member of a given shadingEngine?
    def on_checkbox_state_changed(self, state, material):
        """Handle checkbox state changes for material selection."""
        if state == QtCore.Qt.Checked:
            if material not in self.selected_materials_list:
                self.selected_materials_list.append(material)
        else:
            if material in self.selected_materials_list:
                self.selected_materials_list.remove(material)

        self._update_delete_button_count()
