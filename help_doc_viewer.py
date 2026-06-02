"""
Help Doc Viewer
---------------
Simple image viewer for help documentation PNGs.
Supports zoom (scroll wheel, +/- buttons) and pan (click-drag).
Multi-page docs use a _1, _2, _3 suffix (e.g. textureImporter_helpDoc_1.png).
"""

import os
import re

try:
    from PySide6 import QtCore, QtWidgets, QtGui
    from shiboken6 import wrapInstance
except ImportError:
    from PySide2 import QtCore, QtWidgets, QtGui
    from shiboken2 import wrapInstance

import maya.OpenMayaUI as omui


STYLE_WINDOW = """
    QDialog {
        background-color: #2b2b2b;
    }
    QLabel {
        color: #cccccc;
    }
    QPushButton {
        font-family: 'Segoe UI';
        font-size: 12px;
        color: #ffffff;
        background-color: #666666;
        border: 2px solid #666666;
        border-radius: 8px;
        padding: 2px 5px;
    }
    QPushButton:hover {
        background-color: #888888;
        border: 2px solid #888888;
    }
    QPushButton:pressed {
        background-color: #1a1a1a;
        border: 2px solid #1a1a1a;
    }
"""

STYLE_IMAGE_VIEWER = """
    QWidget {
        background-color: #1a1a1a;
        border: 1px solid #444444;
        border-radius: 4px;
    }
"""

STYLE_IMAGE_LABEL = """
    QLabel {
        background-color: transparent;
        border: none;
    }
"""

STYLE_BUTTON_ICON = """
    QPushButton {
        font-family: 'Segoe UI';
        font-size: 14px;
        font-weight: bold;
        color: #ffffff;
        background-color: #555555;
        border: 1px solid #555555;
        border-radius: 4px;
        padding: 0px;
    }
    QPushButton:hover {
        background-color: #777777;
        border: 1px solid #777777;
    }
    QPushButton:pressed {
        background-color: #1a1a1a;
        border: 1px solid #1a1a1a;
    }
"""

STYLE_LABEL_PAGE_PREFIX = "color: #cccccc; font-size: 11px; padding-right: 3px; margin: 0px;"
STYLE_LABEL_PAGE = "color: #6fa3d8; font-size: 12px; min-width: 35px; max-width: 35px; padding: 0px; margin: 0px;"

HELP_DOCS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "helpDocs")

HELP_DOC_MAP = {
    "quickMaterialsHelpButton":      "quickMaterials_helpDoc.png",
    "materialCreatorHelpButton":     "materialCreator_helpDoc.png",
    "materialToolsHelpButton":       "materialTools_helpDoc.png",
    "materialListHelpButton":        "materialList_helpDoc.png",
    "textureImporterHelpButton":     "textureImporter_helpDoc.png",
}


def _discover_help_doc_pages(base_filename):
    """Return ordered paths for a help doc, including numbered pages when present."""
    base_stem = os.path.splitext(base_filename)[0]
    pages = []
    page_num = 1
    while True:
        page_path = os.path.join(HELP_DOCS_DIR, f"{base_stem}_{page_num}.png")
        if os.path.isfile(page_path):
            pages.append(page_path)
            page_num += 1
        else:
            break

    if pages:
        return pages

    single_path = os.path.join(HELP_DOCS_DIR, base_filename)
    if os.path.isfile(single_path):
        return [single_path]

    return []


def _friendly_help_doc_title(filename):
    """Build a readable title from a mapped help-doc filename."""
    stem = os.path.splitext(filename)[0]
    stem = re.sub(r"_helpDoc(_\d+)?$", "", stem)
    return stem.replace("_", " ").title()


class HelpDocViewer(QtWidgets.QDialog):
    """Zoomable / pannable viewer for help-doc PNGs (single or multi-page)."""

    def __init__(self, image_paths, title="Help", parent=None):
        if parent is None:
            main_win = omui.MQtUtil.mainWindow()
            if main_win:
                parent = wrapInstance(int(main_win), QtWidgets.QWidget)

        super(HelpDocViewer, self).__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(500, 400)
        self.resize(900, 750)
        self.setStyleSheet(STYLE_WINDOW)

        self._image_paths = list(image_paths or [])
        self._current_page_index = 0
        self._original_pixmap = None
        self._manual_zoom = None
        self._is_dragging = False
        self._drag_last_pos = None
        self._base_title = title

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(4)

        # --- controls bar ---
        controls = QtWidgets.QHBoxLayout()

        self._page_prefix_label = QtWidgets.QLabel("Page:")
        self._page_prefix_label.setStyleSheet(STYLE_LABEL_PAGE_PREFIX)
        self._page_prefix_label.setAlignment(QtCore.Qt.AlignCenter | QtCore.Qt.AlignVCenter)
        self._page_prefix_label.hide()

        self._page_prev_btn = QtWidgets.QPushButton("←")
        self._page_prev_btn.setToolTip("Previous page")
        self._page_prev_btn.setFixedSize(22, 22)
        self._page_prev_btn.setStyleSheet(STYLE_BUTTON_ICON)
        self._page_prev_btn.clicked.connect(self._previous_page)
        self._page_prev_btn.hide()

        self._page_label = QtWidgets.QLabel("")
        self._page_label.setStyleSheet(STYLE_LABEL_PAGE)
        self._page_label.setAlignment(QtCore.Qt.AlignCenter)
        self._page_label.hide()

        self._page_next_btn = QtWidgets.QPushButton("→")
        self._page_next_btn.setToolTip("Next page")
        self._page_next_btn.setFixedSize(22, 22)
        self._page_next_btn.setStyleSheet(STYLE_BUTTON_ICON)
        self._page_next_btn.clicked.connect(self._next_page)
        self._page_next_btn.hide()

        controls.addWidget(self._page_prefix_label)
        controls.addSpacing(1)
        controls.addWidget(self._page_prev_btn)
        controls.addSpacing(-2)
        controls.addWidget(self._page_label)
        controls.addSpacing(-2)
        controls.addWidget(self._page_next_btn)
        controls.addStretch()

        self._zoom_out_btn = QtWidgets.QPushButton("-")
        self._zoom_out_btn.setToolTip("Zoom out 10%")
        self._zoom_out_btn.setFixedSize(22, 22)
        self._zoom_out_btn.setStyleSheet(STYLE_BUTTON_ICON)
        self._zoom_out_btn.clicked.connect(self._zoom_out)

        self._zoom_in_btn = QtWidgets.QPushButton("+")
        self._zoom_in_btn.setToolTip("Zoom in 10%")
        self._zoom_in_btn.setFixedSize(22, 22)
        self._zoom_in_btn.setStyleSheet(STYLE_BUTTON_ICON)
        self._zoom_in_btn.clicked.connect(self._zoom_in)

        self._reset_btn = QtWidgets.QPushButton("Reset")
        self._reset_btn.setToolTip("Reset zoom to fit window")
        self._reset_btn.clicked.connect(self._reset_zoom)

        controls.addWidget(self._zoom_out_btn)
        controls.addSpacing(8)
        controls.addWidget(self._zoom_in_btn)
        controls.addSpacing(16)
        controls.addWidget(self._reset_btn)
        root.addLayout(controls)

        # --- image viewer area ---
        self.image_viewer = QtWidgets.QWidget()
        self.image_viewer.setStyleSheet(STYLE_IMAGE_VIEWER)
        self.image_viewer.setMinimumSize(400, 300)

        self.image_label = QtWidgets.QLabel(self.image_viewer)
        self.image_label.setAlignment(QtCore.Qt.AlignCenter)
        self.image_label.setStyleSheet(STYLE_IMAGE_LABEL)

        root.addWidget(self.image_viewer)

        self.image_viewer.installEventFilter(self)
        self.image_label.installEventFilter(self)

        self._resize_timer = QtCore.QTimer()
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._scale_image_to_fit)

        self._setup_page_navigation()
        self._load_current_page()

    # ------------------------------------------------------------------
    # Page navigation
    # ------------------------------------------------------------------

    def _setup_page_navigation(self):
        has_pages = len(self._image_paths) > 1
        self._page_prefix_label.setVisible(has_pages)
        self._page_prev_btn.setVisible(has_pages)
        self._page_label.setVisible(has_pages)
        self._page_next_btn.setVisible(has_pages)
        self._update_page_controls()

    def _update_page_controls(self):
        total = len(self._image_paths)
        if total <= 1:
            self.setWindowTitle(self._base_title)
            return

        current = self._current_page_index + 1
        self._page_label.setText(str(current))
        self.setWindowTitle(f"{self._base_title} ({current}/{total})")
        self._page_prev_btn.setEnabled(self._current_page_index > 0)
        self._page_next_btn.setEnabled(self._current_page_index < total - 1)

    def _previous_page(self):
        if self._current_page_index <= 0:
            return
        self._current_page_index -= 1
        self._load_current_page()

    def _next_page(self):
        if self._current_page_index >= len(self._image_paths) - 1:
            return
        self._current_page_index += 1
        self._load_current_page()

    def _load_current_page(self):
        if not self._image_paths:
            self.image_label.setText("No help documentation found.")
            return

        path = self._image_paths[self._current_page_index]
        self._manual_zoom = None
        self._load_image(path)
        self._update_page_controls()

    # ------------------------------------------------------------------
    # Image loading
    # ------------------------------------------------------------------

    def _load_image(self, path):
        if not os.path.isfile(path):
            self.image_label.setText(f"Image not found:\n{path}")
            self._original_pixmap = None
            return
        pixmap = QtGui.QPixmap(path)
        if pixmap.isNull():
            self.image_label.setText(f"Failed to load image:\n{path}")
            self._original_pixmap = None
            return
        self._original_pixmap = pixmap
        QtCore.QTimer.singleShot(50, self._scale_image_to_fit)

    # ------------------------------------------------------------------
    # Zoom helpers
    # ------------------------------------------------------------------

    def _current_fit_scale(self):
        """Return the scale that fits the image inside the viewer."""
        vs = self.image_viewer.size()
        aw = vs.width() - 20
        ah = vs.height() - 20
        if aw <= 20 or ah <= 20:
            aw, ah = 880, 700
        orig = self._original_pixmap.size()
        return min(aw / orig.width(), ah / orig.height())

    def _ensure_manual_zoom(self):
        if self._manual_zoom is None:
            self._manual_zoom = self._current_fit_scale()

    def _handle_zoom(self, delta, center_pos):
        if self._original_pixmap is None or self._original_pixmap.isNull():
            return
        self._ensure_manual_zoom()
        old = self._manual_zoom
        factor = 1.1
        self._manual_zoom *= factor if delta > 0 else 1.0 / factor
        self._manual_zoom = max(0.01, min(10.0, self._manual_zoom))
        ratio = self._manual_zoom / old if old else 1.0

        old_x = self.image_label.x()
        old_y = self.image_label.y()
        new_x = int(center_pos.x() - ratio * (center_pos.x() - old_x))
        new_y = int(center_pos.y() - ratio * (center_pos.y() - old_y))

        self._apply_zoom(skip_center=True)
        self.image_label.move(new_x, new_y)

    def _zoom_in(self):
        if self._original_pixmap is None or self._original_pixmap.isNull():
            return
        self._ensure_manual_zoom()
        old = self._manual_zoom
        self._manual_zoom = max(0.01, min(10.0, self._manual_zoom * 1.1))
        ratio = self._manual_zoom / old if old else 1.0
        vs = self.image_viewer.size()
        cx, cy = vs.width() // 2, vs.height() // 2
        new_x = int(cx - ratio * (cx - self.image_label.x()))
        new_y = int(cy - ratio * (cy - self.image_label.y()))
        self._apply_zoom(skip_center=True)
        self.image_label.move(new_x, new_y)

    def _zoom_out(self):
        if self._original_pixmap is None or self._original_pixmap.isNull():
            return
        self._ensure_manual_zoom()
        old = self._manual_zoom
        self._manual_zoom = max(0.01, min(10.0, self._manual_zoom / 1.1))
        ratio = self._manual_zoom / old if old else 1.0
        vs = self.image_viewer.size()
        cx, cy = vs.width() // 2, vs.height() // 2
        new_x = int(cx - ratio * (cx - self.image_label.x()))
        new_y = int(cy - ratio * (cy - self.image_label.y()))
        self._apply_zoom(skip_center=True)
        self.image_label.move(new_x, new_y)

    def _reset_zoom(self):
        self._manual_zoom = None
        self._scale_image_to_fit()

    def _apply_zoom(self, skip_center=False):
        if self._original_pixmap is None or self._original_pixmap.isNull():
            return
        orig = self._original_pixmap.size()
        w = int(orig.width() * self._manual_zoom)
        h = int(orig.height() * self._manual_zoom)
        scaled = self._original_pixmap.scaled(w, h, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        self.image_label.setPixmap(scaled)
        self.image_label.resize(w, h)
        if not skip_center:
            self._center_image()

    def _center_image(self):
        vs = self.image_viewer.size()
        ls = self.image_label.size()
        self.image_label.move(int((vs.width() - ls.width()) / 2),
                              int((vs.height() - ls.height()) / 2))

    def _scale_image_to_fit(self):
        if self._original_pixmap is None or self._original_pixmap.isNull():
            return
        if self._manual_zoom is not None:
            self._apply_zoom(skip_center=True)
            return
        scale = self._current_fit_scale()
        orig = self._original_pixmap.size()
        w = int(orig.width() * scale)
        h = int(orig.height() * scale)
        scaled = self._original_pixmap.scaled(w, h, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        self.image_label.setPixmap(scaled)
        self.image_label.resize(w, h)
        self._center_image()

    # ------------------------------------------------------------------
    # Event filter  (resize / drag-pan / wheel-zoom)
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Resize:
            if obj is self.image_label and self._manual_zoom is not None:
                return False
            if obj is self.image_viewer:
                self._resize_timer.stop()
                self._resize_timer.start(10)

        elif event.type() == QtCore.QEvent.MouseButtonPress:
            if obj in (self.image_viewer, self.image_label):
                if event.button() == QtCore.Qt.LeftButton:
                    self._is_dragging = True
                    try:
                        pos = event.position().toPoint()
                    except AttributeError:
                        pos = event.pos()
                    if obj is self.image_label:
                        pos = self.image_label.mapTo(self.image_viewer, pos)
                    self._drag_last_pos = pos
                    self.image_viewer.setCursor(QtCore.Qt.ClosedHandCursor)
                    return True

        elif event.type() == QtCore.QEvent.MouseMove:
            if self._is_dragging and obj in (self.image_viewer, self.image_label):
                try:
                    pos = event.position().toPoint()
                except AttributeError:
                    pos = event.pos()
                if obj is self.image_label:
                    pos = self.image_label.mapTo(self.image_viewer, pos)
                if self._drag_last_pos is not None:
                    delta = pos - self._drag_last_pos
                    cur = self.image_label.pos()
                    self.image_label.move(cur.x() + delta.x(), cur.y() + delta.y())
                    self._drag_last_pos = pos
                    return True

        elif event.type() == QtCore.QEvent.MouseButtonRelease:
            if obj in (self.image_viewer, self.image_label):
                if event.button() == QtCore.Qt.LeftButton and self._is_dragging:
                    self._is_dragging = False
                    self._drag_last_pos = None
                    self.image_viewer.unsetCursor()
                    return True

        elif event.type() == QtCore.QEvent.Wheel:
            if obj in (self.image_viewer, self.image_label):
                delta = event.angleDelta().y()
                if delta != 0:
                    vs = self.image_viewer.size()
                    center = QtCore.QPoint(vs.width() // 2, vs.height() // 2)
                    self._handle_zoom(delta, center)
                    return True

        if obj == self:
            return QtWidgets.QDialog.eventFilter(self, obj, event)
        return False


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------

_open_viewers = {}


def show_help_doc(button_name):
    """Open (or re-focus) the help doc viewer for the given button name.

    ``button_name`` must be a key in ``HELP_DOC_MAP``.
    """
    filename = HELP_DOC_MAP.get(button_name)
    if filename is None:
        print(f"[HelpDocViewer] No help doc mapped for button '{button_name}'")
        return None

    image_paths = _discover_help_doc_pages(filename)
    if not image_paths:
        print(f"[HelpDocViewer] No help doc images found for '{filename}' in {HELP_DOCS_DIR}")
        return None

    existing = _open_viewers.get(button_name)
    if existing is not None:
        try:
            existing.close()
        except Exception:
            pass

    friendly = _friendly_help_doc_title(filename)
    viewer = HelpDocViewer(image_paths, title=f"Help - {friendly}")
    viewer.show()
    _open_viewers[button_name] = viewer
    return viewer
