import os
import sys
import colorsys  # For HSV to RGB conversion
import json
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


from functools import partial, wraps

import time

def timing_decorator(func_name):
    """Decorator to time function execution."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            result = func(*args, **kwargs)
            end_time = time.time()
            duration = (end_time - start_time) * 1000  # Convert to milliseconds
            print(f"[TIMING] {func_name}: {duration:.2f}ms")
            return result
        return wrapper
    return decorator
import maya.mel as mel
import maya.cmds as cmds
import maya.utils as mutils

import maya.OpenMayaUI as omui  # type: ignore
from maya.app.general.mayaMixin import MayaQWidgetDockableMixin

import random
import re
import importlib
import weakref  # guarded owner refs when QPointer is unavailable


# Import Material converter
import QuickMaterials.material_converter
importlib.reload(QuickMaterials.material_converter)

# Import Material Swatch Icon
try:
    from . import material_swatch_icon
except Exception:
    import sys
    import importlib.util
    MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
    _swatch_path = os.path.join(MODULE_DIR, "material_swatch_icon.py")
    _spec = importlib.util.spec_from_file_location("QuickMaterials.material_swatch_icon", _swatch_path)
    if _spec is not None and _spec.loader is not None:
        material_swatch_icon = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(material_swatch_icon)
        sys.modules.setdefault("material_swatch_icon", material_swatch_icon)
        sys.modules.setdefault("QuickMaterials.material_swatch_icon", material_swatch_icon)
    else:
        material_swatch_icon = None
if material_swatch_icon:
    importlib.reload(material_swatch_icon)
    MaterialSwatchIcon = material_swatch_icon.MaterialSwatchIcon
else:
    MaterialSwatchIcon = None

# Default icon path for utility nodes without specific icons (try multiple candidates)
DEFAULT_UTILITY_ICON_CANDIDATES = [
    ":/nodeIcons/out_multmatrix.png",
    ":/nodeIcons/out_multMatrix.png",
    ":/out_multmatrix.png",
]
DEFAULT_UTILITY_ICON = DEFAULT_UTILITY_ICON_CANDIDATES[0]

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

class MaterialListScrollContent(QtWidgets.QWidget):
    """Custom QWidget for material list scroll content that handles empty space clicks to clear selection."""
    def __init__(self, owner, parent=None):
        super(MaterialListScrollContent, self).__init__(parent)
        self._owner = owner  # Reference to QuickMaterialsUI instance
    
    def mousePressEvent(self, event):
        """Handle mouse clicks. If clicking on empty space, clear material selection."""
        if event.button() == QtCore.Qt.LeftButton:
            # Get the widget at the click position
            clicked_widget = self.childAt(event.pos())
            
            # Check if we clicked on empty space (no child widget) or on the scroll content itself
            # Material entries are containers with QLineEdit or QLabel children
            is_on_material_entry = False
            if clicked_widget:
                # Walk up the parent chain to see if we're within a material entry
                current = clicked_widget
                while current and current != self:
                    # Material entries have a property or contain line edits/labels
                    # Check if this widget or any parent is a material entry container
                    if (hasattr(current, 'findChild') and 
                        (current.findChild(QtWidgets.QLineEdit) is not None or 
                         current.findChild(QtWidgets.QLabel) is not None or
                         isinstance(current, (QtWidgets.QLineEdit, QtWidgets.QLabel, ClickableColorSwatch)))):
                        is_on_material_entry = True
                        break
                    current = current.parent() if hasattr(current, 'parent') else current.parentWidget()
            
            # If we didn't click on a material entry (empty space), clear selection
            if not is_on_material_entry:
                # Only clear if owner is valid and has the necessary methods
                try:
                    owner = self._owner
                    if owner and isValid(owner):
                        # Clear the selection
                        owner.selected_materials_list = []
                        # Update visuals
                        if hasattr(owner, '_apply_selection_visuals'):
                            owner._apply_selection_visuals()
                        # Update delete button count
                        if hasattr(owner, '_update_delete_button_count'):
                            owner._update_delete_button_count()
                except Exception:
                    pass
        
        # Always call parent to allow normal event propagation
        super(MaterialListScrollContent, self).mousePressEvent(event)


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


class TextureIcon(QtWidgets.QLabel):
    """Icon for file textures using Maya's built-in file texture icon."""
    
    def __init__(self, texture_name, icon_size=14, parent=None):
        """Create an icon for a file texture using Maya's built-in icon.
        
        Args:
            texture_name: Name of the texture node
            icon_size: Size of the icon in pixels (default 14, smaller for better alignment)
            parent: Parent widget
        """
        super(TextureIcon, self).__init__(parent)
        self.texture_name = texture_name
        self.icon_size = icon_size
        
        # Set fixed size to match list entry height
        self.setFixedSize(icon_size, icon_size)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setScaledContents(False)
        
        # Material list background color
        self._bg_color = "#3a3a3a"
        self.setStyleSheet(f"""
            QLabel {{
                background-color: {self._bg_color};
                border: none;
            }}
        """)
        
        # Try to load Maya's built-in file texture icon
        self._icon_pixmap = self._load_maya_file_icon()
        if self._icon_pixmap and not self._icon_pixmap.isNull():
            self.setPixmap(self._icon_pixmap)
            self._use_fallback = False
        else:
            # Fall back to checker pattern if Maya icon not found
            self._use_fallback = True
            self._checker_pixmap = self._create_checker_pattern(icon_size)
            if self._checker_pixmap:
                self.setPixmap(self._checker_pixmap)
        
        # Selection handler support (for clicking to select texture)
        self._owner_ref = None
        self._handler_name = None
        self._bound_handler = None
        self._qm_material_name = texture_name
        
        # Make it clickable
        self.setCursor(QtCore.Qt.PointingHandCursor)
    
    def _load_maya_file_icon(self):
        """Load Maya's built-in file texture icon from the resource system."""
        # Try different Maya file texture icon candidates
        # Using ":" prefix to access Maya's resource system
        icon_candidates = [
            ":file.svg",           # SVG version (preferred for scaling)
            ":out_file.png",       # PNG version from outliner
            ":file.png",           # PNG version
            ":render_file.png",    # Render version
        ]
        
        for path in icon_candidates:
            try:
                pixmap = QtGui.QPixmap(path)
                if pixmap and not pixmap.isNull():
                    # Scale to desired size while maintaining aspect ratio
                    return pixmap.scaled(
                        self.icon_size,
                        self.icon_size,
                        QtCore.Qt.KeepAspectRatio,
                        QtCore.Qt.SmoothTransformation,
                    )
            except Exception:
                continue
        
        return None
    
    def _create_checker_pattern(self, size):
        """Create a black and white checker pattern pixmap as fallback."""
        try:
            # Create image with transparent background
            image = QtGui.QImage(size, size, QtGui.QImage.Format_ARGB32)
            image.fill(QtCore.Qt.transparent)
            
            # Create painter
            painter = QtGui.QPainter(image)
            painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
            
            # Checker pattern: 4x4 grid
            checker_size = size // 4
            
            # Colors
            white = QtGui.QColor(255, 255, 255)
            black = QtGui.QColor(0, 0, 0)
            
            # Draw checker pattern
            for y in range(4):
                for x in range(4):
                    rect = QtCore.QRect(
                        x * checker_size,
                        y * checker_size,
                        checker_size,
                        checker_size
                    )
                    # Alternate colors based on position
                    if (x + y) % 2 == 0:
                        painter.fillRect(rect, white)
                    else:
                        painter.fillRect(rect, black)
            
            painter.end()
            
            # Convert to pixmap
            pixmap = QtGui.QPixmap.fromImage(image)
            return pixmap
        except Exception as e:
            print(f"[TextureIcon] Failed to create checker pattern: {e}")
            return None
    
    def paintEvent(self, event):
        """Override paintEvent to draw the icon or checker pattern."""
        if not self._use_fallback:
            # Use default QLabel painting for Maya icon
            return QtWidgets.QLabel.paintEvent(self, event)
        
        # Draw checker pattern fallback
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
        
        # Draw background square
        bg_color = QtGui.QColor(self._bg_color)
        painter.setBrush(QtGui.QBrush(bg_color))
        painter.setPen(QtCore.Qt.NoPen)
        painter.drawRect(0, 0, self.icon_size, self.icon_size)
        
        # Draw the checker pattern if available
        if self._checker_pixmap and not self._checker_pixmap.isNull():
            # Draw the pixmap to fill the entire square
            painter.drawPixmap(0, 0, self._checker_pixmap)
        
        painter.end()
    
    def setSelectionHandler(self, owner_or_callable, handler_name_or_material, maybe_material=None):
        """Set the selection handler for click events.
        Compatible with the same API as LeftClipLineEdit and MaterialSwatchIcon."""
        if isinstance(handler_name_or_material, str) and maybe_material is not None:
            # New signature: (owner, "handle_item_click", material)
            owner = owner_or_callable
            self._owner_ref = weakref.ref(owner) if owner is not None else None
            self._handler_name = handler_name_or_material
            self._bound_handler = None
            self._qm_material_name = maybe_material
            return
        
        # Back-compat: (callable, material_name)
        callable_handler = owner_or_callable
        material_name = handler_name_or_material
        self._owner_ref = None
        self._handler_name = None
        self._bound_handler = callable_handler
        self._qm_material_name = material_name
    
    def mousePressEvent(self, e):
        """Handle mouse clicks to select the texture."""
        try:
            if e.button() == QtCore.Qt.RightButton:
                super(TextureIcon, self).mousePressEvent(e)
                return
            
            mods = e.modifiers()
            shift = bool(mods & QtCore.Qt.ShiftModifier)
            ctrl = bool(mods & (QtCore.Qt.ControlModifier | QtCore.Qt.MetaModifier))
            
            # Try owner + handler name pattern first
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
                        handler(self._qm_material_name, source='texture_icon', shift=shift, ctrl=ctrl)
                        super(TextureIcon, self).mousePressEvent(e)
                        return
            
            # Try bound handler pattern
            if self._bound_handler and self._qm_material_name:
                try:
                    self._bound_handler(self._qm_material_name, source='texture_icon', shift=shift, ctrl=ctrl)
                    super(TextureIcon, self).mousePressEvent(e)
                    return
                except Exception:
                    pass
            
            # Default behavior
            super(TextureIcon, self).mousePressEvent(e)
        except Exception:
            super(TextureIcon, self).mousePressEvent(e)


class ProceduralTextureIcon(QtWidgets.QLabel):
    """Icon for procedural textures using Maya's built-in icons."""
    
    def __init__(self, texture_name, icon_size=14, parent=None):
        """Create an icon for a procedural texture using Maya's built-in icon.
        
        Args:
            texture_name: Name of the procedural texture node
            icon_size: Size of the icon in pixels (default 14, matching file texture icon size)
            parent: Parent widget
        """
        super(ProceduralTextureIcon, self).__init__(parent)
        self.texture_name = texture_name
        self.icon_size = icon_size
        self.node_type = None
        
        # Set fixed size
        self.setFixedSize(icon_size, icon_size)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setScaledContents(False)
        
        # Material list background color
        self._bg_color = "#3a3a3a"
        self.setStyleSheet(f"""
            QLabel {{
                background-color: {self._bg_color};
                border: none;
            }}
        """)
        
        # Try to load Maya's built-in icon
        self._icon_pixmap = self._load_maya_texture_icon()
        if self._icon_pixmap and not self._icon_pixmap.isNull():
            self.setPixmap(self._icon_pixmap)
            self._use_fallback = False
        else:
            # Fall back to swatch generation if Maya icon not found
            self._use_fallback = True
            self._texture_pixmap = None
            self._texture_loaded = False
            # Show loading placeholder
            self.setText("...")
            self.setStyleSheet(f"""
                QLabel {{
                    background-color: {self._bg_color};
                    border: none;
                    color: #888888;
                    font-size: 8px;
                }}
            """)
            # Load texture preview asynchronously to avoid blocking UI
            QtCore.QTimer.singleShot(50, self.load_texture_preview)
        
        # Selection handler support (for clicking to select texture)
        self._owner_ref = None
        self._handler_name = None
        self._bound_handler = None
        self._qm_material_name = texture_name
        
        # Make it clickable
        self.setCursor(QtCore.Qt.PointingHandCursor)
    
    def _get_node_type(self):
        """Get the node type for the texture node."""
        if self.node_type is None:
            try:
                if cmds.objExists(self.texture_name):
                    self.node_type = cmds.nodeType(self.texture_name)
            except Exception:
                pass
        return self.node_type
    
    def _get_icon_candidates_for_node_type(self, node_type):
        """Get icon candidate paths for a given node type."""
        if not node_type:
            return []
        
        # Map common procedural texture node types to their icon names
        # Try multiple variants: SVG (preferred), PNG from outliner, PNG from render
        icon_candidates = []
        
        # Direct mapping - use node type as icon name
        icon_candidates.extend([
            f":{node_type}.svg",
            f":out_{node_type}.png",
            f":{node_type}.png",
            f":render_{node_type}.png",
        ])
        
        # Special mappings for node types that don't match icon names exactly
        special_mappings = {
            'place2dTexture': ['place2dTexture', 'place2d'],
            'place3dTexture': ['place3dTexture', 'place3d', 'place3dTx'],
            'solidFractal': ['solidFractal', 'fractal'],
            'envBall': ['envBall', 'env'],
            'envChrome': ['envChrome', 'env'],
            'envCube': ['envCube', 'env'],
            'envFog': ['envFog', 'env'],
            'envSky': ['envSky', 'env'],
            'envSphere': ['envSphere', 'env'],
            'projection': ['projection'],
            'stencil': ['stencil'],
            'layeredTexture': ['layeredTexture', 'layered'],
            'rampShader': ['rampShader', 'ramp'],
        }
        
        if node_type in special_mappings:
            for mapping in special_mappings[node_type]:
                icon_candidates.extend([
                    f":{mapping}.svg",
                    f":out_{mapping}.png",
                    f":{mapping}.png",
                    f":render_{mapping}.png",
                ])
        
        return icon_candidates
    
    def _load_maya_texture_icon(self):
        """Load Maya's built-in icon for the procedural texture node type."""
        node_type = self._get_node_type()
        if not node_type:
            return None
        
        icon_candidates = self._get_icon_candidates_for_node_type(node_type)
        
        for path in icon_candidates:
            try:
                pixmap = QtGui.QPixmap(path)
                if pixmap and not pixmap.isNull():
                    # Scale to desired size while maintaining aspect ratio
                    return pixmap.scaled(
                        self.icon_size,
                        self.icon_size,
                        QtCore.Qt.KeepAspectRatio,
                        QtCore.Qt.SmoothTransformation,
                    )
            except Exception:
                continue
        
        return None
    
    def load_texture_preview(self):
        """Load and display the procedural texture preview (fallback only)."""
        if not self._use_fallback:
            return
            
        if not cmds.objExists(self.texture_name):
            self.clear()
            self._texture_loaded = True
            return
        
        try:
            pixmap = self._create_texture_preview(self.texture_name, self.icon_size)
            if pixmap and not pixmap.isNull():
                self._texture_pixmap = pixmap
                self.clear()  # Clear text
                self.update()
                self._texture_loaded = True
            else:
                self.clear()
                self._texture_loaded = True
        except Exception as e:
            self.clear()
            self._texture_loaded = True
            print(f"[ProceduralTextureIcon] Failed to load preview for {self.texture_name}: {e}")
    
    def _create_texture_preview(self, texture_node, size):
        """Create a preview image by sampling the procedural texture at multiple UV coordinates."""
        try:
            if not cmds.objExists(texture_node):
                return None
            
            node_type = cmds.nodeType(texture_node)
            
            # Create image
            image = QtGui.QImage(size, size, QtGui.QImage.Format_ARGB32)
            image.fill(QtCore.Qt.transparent)
            
            # Sample grid - use 4x4 grid for 14px icon (3-4px per sample)
            sample_count = 4
            sample_size = size // sample_count
            
            # Try to sample the texture at different UV coordinates
            # We'll create a temporary place2dTexture to control UV sampling
            temp_place2d = None
            try:
                # Check if texture has a place2dTexture connected
                place2d_connections = cmds.listConnections(f"{texture_node}.uvCoord", source=True, destination=False)
                if place2d_connections:
                    place2d_node = place2d_connections[0]
                else:
                    # Create temporary place2dTexture for sampling
                    temp_place2d = cmds.shadingNode("place2dTexture", asUtility=True)
                    try:
                        cmds.connectAttr(f"{temp_place2d}.outUV", f"{texture_node}.uvCoord", force=True)
                    except:
                        # Some textures might not use uvCoord
                        pass
                
                # Sample the texture at grid points
                for y_idx in range(sample_count):
                    for x_idx in range(sample_count):
                        # Calculate UV coordinates (0-1 range)
                        u = (x_idx + 0.5) / sample_count
                        v = 1.0 - (y_idx + 0.5) / sample_count  # Flip V for image coordinates
                        
                        # Try to get color at this UV coordinate
                        color = self._sample_texture_at_uv(texture_node, u, v)
                        
                        if color:
                            r, g, b = color
                            # Clamp to 0-255 range
                            r = max(0, min(255, int(r * 255)))
                            g = max(0, min(255, int(g * 255)))
                            b = max(0, min(255, int(b * 255)))
                            
                            # Fill the sample area
                            for py in range(sample_size):
                                for px in range(sample_size):
                                    x = x_idx * sample_size + px
                                    y = y_idx * sample_size + py
                                    if x < size and y < size:
                                        image.setPixel(x, y, QtGui.QColor(r, g, b).rgba())
                        else:
                            # Fallback: use average color
                            avg_color = self._get_texture_average_color(texture_node)
                            if avg_color:
                                r, g, b = avg_color
                                r = max(0, min(255, int(r * 255)))
                                g = max(0, min(255, int(g * 255)))
                                b = max(0, min(255, int(b * 255)))
                                for py in range(sample_size):
                                    for px in range(sample_size):
                                        x = x_idx * sample_size + px
                                        y = y_idx * sample_size + py
                                        if x < size and y < size:
                                            image.setPixel(x, y, QtGui.QColor(r, g, b).rgba())
                
                # Clean up temporary place2dTexture
                if temp_place2d and cmds.objExists(temp_place2d):
                    try:
                        cmds.delete(temp_place2d)
                    except:
                        pass
                
            except Exception as e:
                # Fallback: use average color as solid fill
                avg_color = self._get_texture_average_color(texture_node)
                if avg_color:
                    r, g, b = avg_color
                    r = max(0, min(255, int(r * 255)))
                    g = max(0, min(255, int(g * 255)))
                    b = max(0, min(255, int(b * 255)))
                    image.fill(QtGui.QColor(r, g, b).rgba())
                else:
                    # Final fallback: grey
                    image.fill(QtGui.QColor(128, 128, 128).rgba())
            
            # Convert to pixmap
            pixmap = QtGui.QPixmap.fromImage(image)
            return pixmap
            
        except Exception as e:
            print(f"[ProceduralTextureIcon] Error creating preview for {texture_node}: {e}")
            return None
    
    def _sample_texture_at_uv(self, texture_node, u, v):
        """Sample texture color at specific UV coordinates."""
        try:
            # Try using colorAtPoint if available (Maya 2016+)
            try:
                result = cmds.colorAtPoint(texture_node, u=u, v=v)
                if result and len(result) >= 3:
                    return tuple(float(x) for x in result[:3])
            except:
                pass
            
            # Fallback: try to evaluate the texture by setting place2dTexture UV
            # This is a simplified approach - we'll use the average color method
            return None
        except:
            return None
    
    def _get_texture_average_color(self, texture_node):
        """Get average color from procedural texture (fallback method)."""
        try:
            if cmds.attributeQuery("outColor", node=texture_node, exists=True):
                try:
                    color_value = cmds.getAttr(f"{texture_node}.outColor")[0]
                    if isinstance(color_value, (list, tuple)) and len(color_value) >= 3:
                        return tuple(float(x) for x in color_value[:3])
                except:
                    pass
            return None
        except:
            return None
    
    def paintEvent(self, event):
        """Override paintEvent to draw the icon or texture preview."""
        if not self._use_fallback:
            # Use default QLabel painting for Maya icon
            return QtWidgets.QLabel.paintEvent(self, event)
        
        # Draw swatch fallback
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
        
        # Draw background square
        bg_color = QtGui.QColor(self._bg_color)
        painter.setBrush(QtGui.QBrush(bg_color))
        painter.setPen(QtCore.Qt.NoPen)
        painter.drawRect(0, 0, self.icon_size, self.icon_size)
        
        # Draw the texture preview if available
        if self._texture_pixmap and not self._texture_pixmap.isNull():
            painter.drawPixmap(0, 0, self._texture_pixmap)
        else:
            # Draw text if no pixmap
            painter.setPen(QtGui.QColor("#888888"))
            painter.setFont(self.font())
            painter.drawText(self.rect(), QtCore.Qt.AlignCenter, self.text())
        
        painter.end()
    
    def setSelectionHandler(self, owner_or_callable, handler_name_or_material, maybe_material=None):
        """Set the selection handler for click events.
        Compatible with the same API as LeftClipLineEdit and MaterialSwatchIcon."""
        if isinstance(handler_name_or_material, str) and maybe_material is not None:
            owner = owner_or_callable
            self._owner_ref = weakref.ref(owner) if owner is not None else None
            self._handler_name = handler_name_or_material
            self._bound_handler = None
            self._qm_material_name = maybe_material
            return
        
        callable_handler = owner_or_callable
        material_name = handler_name_or_material
        self._owner_ref = None
        self._handler_name = None
        self._bound_handler = callable_handler
        self._qm_material_name = material_name
    
    def mousePressEvent(self, e):
        """Handle mouse clicks to select the texture."""
        try:
            if e.button() == QtCore.Qt.RightButton:
                super(ProceduralTextureIcon, self).mousePressEvent(e)
                return
            
            mods = e.modifiers()
            shift = bool(mods & QtCore.Qt.ShiftModifier)
            ctrl = bool(mods & (QtCore.Qt.ControlModifier | QtCore.Qt.MetaModifier))
            
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
                        handler(self._qm_material_name, source='procedural_texture_icon', shift=shift, ctrl=ctrl)
                        super(ProceduralTextureIcon, self).mousePressEvent(e)
                        return
            
            if self._bound_handler and self._qm_material_name:
                try:
                    self._bound_handler(self._qm_material_name, source='procedural_texture_icon', shift=shift, ctrl=ctrl)
                    super(ProceduralTextureIcon, self).mousePressEvent(e)
                    return
                except Exception:
                    pass
            
            super(ProceduralTextureIcon, self).mousePressEvent(e)
        except Exception:
            super(ProceduralTextureIcon, self).mousePressEvent(e)


class UtilityNodeIcon(QtWidgets.QLabel):
    """Shows the native Hypershade icon for a utility node (multiplyDivide, etc.)."""

    _default_base_pixmap = {}

    def __init__(self, node_name, node_type, icon_size=14, parent=None):
        super(UtilityNodeIcon, self).__init__(parent)
        self.node_name = node_name
        self.node_type = node_type or ""
        self.icon_size = icon_size
        self.setFixedSize(icon_size, icon_size)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setScaledContents(False)
        self.setCursor(QtCore.Qt.PointingHandCursor)

        self._owner_ref = None
        self._handler_name = None
        self._bound_handler = None
        self._qm_material_name = node_name

        self._use_fallback = False
        self._fallback_color = QtGui.QColor("#5e5e5e")

        self._icon_pixmap = self._load_icon_pixmap()
        if not self._icon_pixmap or self._icon_pixmap.isNull():
            self._icon_pixmap = self._load_default_icon()

        if self._icon_pixmap and not self._icon_pixmap.isNull():
            self.setPixmap(self._icon_pixmap)
            self._use_fallback = False
        else:
            self._use_fallback = True

        self.setStyleSheet("background-color: transparent; border: none;")

    def _load_icon_pixmap(self):
        if not self.node_type:
            return None
        
        # First, check the queried icon mapping (most accurate)
        # This uses icons actually used by Hypershade
        try:
            # Access the mapping from QuickMaterialsUI class (class variable)
            # Try to get it from parent widget hierarchy first
            icon_map = None
            parent = self.parent()
            while parent and not icon_map:
                if hasattr(parent, 'UTILITY_NODE_ICON_MAP'):
                    icon_map = parent.UTILITY_NODE_ICON_MAP
                    break
                # Also check if parent is QuickMaterialsUI instance
                if hasattr(parent, '__class__') and hasattr(parent.__class__, 'UTILITY_NODE_ICON_MAP'):
                    icon_map = parent.__class__.UTILITY_NODE_ICON_MAP
                    break
                parent = parent.parent() if hasattr(parent, 'parent') else None
            
            # If still not found, try accessing via module
            if not icon_map:
                try:
                    import sys
                    # Find QuickMaterialsUI in loaded modules
                    for module_name, module in sys.modules.items():
                        if 'quick_materials' in module_name:
                            if hasattr(module, 'QuickMaterialsUI'):
                                cls = getattr(module, 'QuickMaterialsUI')
                                if hasattr(cls, 'UTILITY_NODE_ICON_MAP'):
                                    icon_map = cls.UTILITY_NODE_ICON_MAP
                                    break
                except:
                    pass
            
            # Check the mapping first
            if icon_map and self.node_type in icon_map:
                icon_path = icon_map[self.node_type]
                try:
                    pixmap = QtGui.QPixmap(icon_path)
                    if pixmap and not pixmap.isNull():
                        return pixmap.scaled(
                            self.icon_size,
                            self.icon_size,
                            QtCore.Qt.KeepAspectRatio,
                            QtCore.Qt.SmoothTransformation,
                        )
                except Exception:
                    pass
        except Exception:
            pass
        
        # Fallback: Try multiple Maya icon candidates (same approach as TextureIcon)
        # Try different variants: SVG (preferred), PNG from outliner, PNG from render
        icon_candidates = [
            f":{self.node_type}.svg",           # SVG version (preferred for scaling)
            f":out_{self.node_type}.png",       # PNG version from outliner
            f":{self.node_type}.png",           # PNG version
            f":render_{self.node_type}.png",    # Render version
            f":/nodeIcons/{self.node_type}.svg",  # Fallback to nodeIcons path
            f":/nodeIcons/{self.node_type}.png",
            f":/nodeIcons/{self.node_type}.xpm",
        ]
        
        for path in icon_candidates:
            try:
                pixmap = QtGui.QPixmap(path)
                if pixmap and not pixmap.isNull():
                    return pixmap.scaled(
                        self.icon_size,
                        self.icon_size,
                        QtCore.Qt.KeepAspectRatio,
                        QtCore.Qt.SmoothTransformation,
                    )
            except Exception:
                continue
        
        return None

    def _load_default_icon(self):
        """Return the default utility icon pixmap (cached)."""
        if QtGui is None:
            return None
        for candidate in DEFAULT_UTILITY_ICON_CANDIDATES:
            pixmap = UtilityNodeIcon._default_base_pixmap.get(candidate)
            if pixmap is None:
                pixmap = QtGui.QPixmap(candidate)
                UtilityNodeIcon._default_base_pixmap[candidate] = pixmap
            if pixmap and not pixmap.isNull():
                return pixmap.scaled(
                    self.icon_size,
                    self.icon_size,
                    QtCore.Qt.KeepAspectRatio,
                    QtCore.Qt.SmoothTransformation,
                )
        # Try to discover a matching resource dynamically
        try:
            resources = cmds.resourceManager(nameFilter="*multmatrix*") or []
            for resource in resources:
                pixmap = QtGui.QPixmap(resource if resource.startswith(":") else f":/{resource.lstrip(':')}")
                if pixmap and not pixmap.isNull():
                    UtilityNodeIcon._default_base_pixmap[resource] = pixmap
                    return pixmap.scaled(
                        self.icon_size,
                        self.icon_size,
                        QtCore.Qt.KeepAspectRatio,
                        QtCore.Qt.SmoothTransformation,
                    )
        except Exception:
            pass
        return None

    def setSelectionHandler(self, owner_or_callable, handler_name_or_material, maybe_material=None):
        """Mirror MaterialSwatchIcon API for click handling."""
        if isinstance(handler_name_or_material, str) and maybe_material is not None:
            owner = owner_or_callable
            self._owner_ref = weakref.ref(owner) if owner is not None else None
            self._handler_name = handler_name_or_material
            self._bound_handler = None
            self._qm_material_name = maybe_material
            return

        callable_handler = owner_or_callable
        material_name = handler_name_or_material
        self._owner_ref = None
        self._handler_name = None
        self._bound_handler = callable_handler
        self._qm_material_name = material_name

    def mousePressEvent(self, e):
        try:
            if e.button() == QtCore.Qt.RightButton:
                super(UtilityNodeIcon, self).mousePressEvent(e)
                return

            mods = e.modifiers()
            shift = bool(mods & QtCore.Qt.ShiftModifier)
            ctrl = bool(mods & (QtCore.Qt.ControlModifier | QtCore.Qt.MetaModifier))

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
                        handler(self._qm_material_name, source='utility_icon', shift=shift, ctrl=ctrl)
                        super(UtilityNodeIcon, self).mousePressEvent(e)
                        return

            if self._bound_handler and self._qm_material_name:
                try:
                    self._bound_handler(self._qm_material_name, source='utility_icon', shift=shift, ctrl=ctrl)
                    super(UtilityNodeIcon, self).mousePressEvent(e)
                    return
                except Exception:
                    pass

            super(UtilityNodeIcon, self).mousePressEvent(e)
        except Exception:
            super(UtilityNodeIcon, self).mousePressEvent(e)

    def paintEvent(self, event):
        if not getattr(self, "_use_fallback", False):
            return QtWidgets.QLabel.paintEvent(self, event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setBrush(QtGui.QBrush(self._fallback_color))
        painter.setPen(QtCore.Qt.NoPen)
        radius = max(2, int(self.icon_size * 0.2))
        painter.drawRoundedRect(0, 0, self.icon_size, self.icon_size, radius, radius)
        painter.end()

class ShadingGroupIcon(QtWidgets.QLabel):
    """Simple blue rounded square icon for shading groups."""
    
    def __init__(self, shading_group_name, icon_size=12, parent=None):
        """Create a blue rounded square icon for a shading group.
        
        Args:
            shading_group_name: Name of the shading group node
            icon_size: Size of the icon in pixels (default 12, smaller than texture icons)
            parent: Parent widget
        """
        super(ShadingGroupIcon, self).__init__(parent)
        self.shading_group_name = shading_group_name
        self.icon_size = icon_size
        
        # Set fixed size
        self.setFixedSize(icon_size, icon_size)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setScaledContents(False)
        
        # Material list background color
        self._bg_color = "#3a3a3a"
        self._blue_color = "#45b2ff"  # Deeper blue matching base shading group styling
        
        # Create blue rounded square pixmap
        self._icon_pixmap = self._create_blue_rounded_square(icon_size)
        if self._icon_pixmap:
            self.setPixmap(self._icon_pixmap)
        
        # Selection handler support (for clicking to select shading group)
        self._owner_ref = None
        self._handler_name = None
        self._bound_handler = None
        self._qm_material_name = shading_group_name
        
        # Make it clickable
        self.setCursor(QtCore.Qt.PointingHandCursor)
    
    def _create_blue_rounded_square(self, size):
        """Create a blue rounded square pixmap."""
        try:
            # Create image with transparent background
            image = QtGui.QImage(size, size, QtGui.QImage.Format_ARGB32)
            image.fill(QtCore.Qt.transparent)
            
            # Create painter
            painter = QtGui.QPainter(image)
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            
            # Blue color with maximum saturation
            blue = QtGui.QColor(self._blue_color)
            # Convert to HSV, set saturation to maximum, convert back to RGB
            h, s, v, a = blue.getHsv()
            if h >= 0:  # Valid hue
                blue.setHsv(h, 255, v, a)  # Max saturation (255)
            else:
                # If color has no hue (grayscale), use a vibrant blue
                blue = QtGui.QColor("#0066ff")  # Vibrant blue fallback
            
            # Draw rounded rectangle (square with rounded corners)
            corner_radius = max(2, size // 6)  # Small rounded corners
            rect = QtCore.QRect(0, 0, size, size)
            painter.setBrush(QtGui.QBrush(blue))
            painter.setPen(QtCore.Qt.NoPen)
            painter.drawRoundedRect(rect, corner_radius, corner_radius)
            
            painter.end()
            
            # Convert to pixmap
            pixmap = QtGui.QPixmap.fromImage(image)
            return pixmap
        except Exception as e:
            pass
            return None
    
    def paintEvent(self, event):
        """Override paintEvent to draw the blue rounded square."""
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        
        # Draw background square
        bg_color = QtGui.QColor(self._bg_color)
        painter.setBrush(QtGui.QBrush(bg_color))
        painter.setPen(QtCore.Qt.NoPen)
        painter.drawRect(0, 0, self.icon_size, self.icon_size)
        
        # Draw the blue rounded square if available
        if self._icon_pixmap and not self._icon_pixmap.isNull():
            painter.drawPixmap(0, 0, self._icon_pixmap)
        
        painter.end()
    
    def setSelectionHandler(self, owner_or_callable, handler_name_or_material, maybe_material=None):
        """Set the selection handler for click events.
        Compatible with the same API as LeftClipLineEdit and MaterialSwatchIcon."""
        if isinstance(handler_name_or_material, str) and maybe_material is not None:
            owner = owner_or_callable
            self._owner_ref = weakref.ref(owner) if owner is not None else None
            self._handler_name = handler_name_or_material
            self._bound_handler = None
            self._qm_material_name = maybe_material
            return
        
        callable_handler = owner_or_callable
        material_name = handler_name_or_material
        self._owner_ref = None
        self._handler_name = None
        self._bound_handler = callable_handler
        self._qm_material_name = material_name
    
    def mousePressEvent(self, e):
        """Handle mouse clicks to select the shading group."""
        try:
            if e.button() == QtCore.Qt.RightButton:
                super(ShadingGroupIcon, self).mousePressEvent(e)
                return
            
            mods = e.modifiers()
            shift = bool(mods & QtCore.Qt.ShiftModifier)
            ctrl = bool(mods & (QtCore.Qt.ControlModifier | QtCore.Qt.MetaModifier))
            
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
                        handler(self._qm_material_name, source='shading_group_icon', shift=shift, ctrl=ctrl)
                        super(ShadingGroupIcon, self).mousePressEvent(e)
                        return
            
            if self._bound_handler and self._qm_material_name:
                try:
                    self._bound_handler(self._qm_material_name, source='shading_group_icon', shift=shift, ctrl=ctrl)
                    super(ShadingGroupIcon, self).mousePressEvent(e)
                    return
                except Exception:
                    pass
            
            super(ShadingGroupIcon, self).mousePressEvent(e)
        except Exception:
            super(ShadingGroupIcon, self).mousePressEvent(e)


class QHLine(QtWidgets.QFrame):
    """Custom horizontal line widget that works reliably in Maya."""
    def __init__(self, parent=None):
        super(QHLine, self).__init__(parent)
        self.setFrameShape(QtWidgets.QFrame.HLine)
        self.setFrameShadow(QtWidgets.QFrame.Sunken)
        self.setFixedHeight(1)
        self.setLineWidth(0)
        self.setMidLineWidth(1)
        # Force visibility with explicit styling
        self.setStyleSheet("""
            QFrame {
                background-color: #666666;
                border: none;
                border-top: 1px solid #666666;
                margin: 1px 0px;
                min-height: 1px;
                max-height: 1px;
            }
        """)

class TextureDisplayLabel(QtWidgets.QLabel):
    """
    QLabel for texture entries that supports rich text (HTML) for multi-colored display.
    Mimics the selection behavior of LeftClipLineEdit but doesn't support editing.
    """
    def __init__(self, html_text="", parent=None):
        super(TextureDisplayLabel, self).__init__(html_text, parent)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.setAutoFillBackground(True)
        self.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.setMinimumWidth(50)
        self.setMinimumHeight(22)
        self.setMaximumHeight(22)  # Lock height to prevent expansion from rich text
        self.setTextFormat(QtCore.Qt.RichText)  # Enable HTML
        self.setWordWrap(False)
        self.setMargin(0)  # Use stylesheet padding only
        self.setContentsMargins(0, 0, 0, 0)  # Ensure zero margins
        # selection callback wiring (same as LeftClipLineEdit)
        self._selection_handler = None
        self._qm_material_name = None
        self._owner_ref = None
        self._handler_name = None
        self._bound_handler = None
        self.setProperty("editing", "false")
    
    def setSelectionHandler(self, owner_or_callable, handler_name_or_material, maybe_material=None):
        """Same API as LeftClipLineEdit for compatibility"""
        if isinstance(handler_name_or_material, str) and maybe_material is not None:
            owner = owner_or_callable
            self._owner_ref = weakref.ref(owner) if owner is not None else None
            self._handler_name = handler_name_or_material
            self._bound_handler = None
            self._qm_material_name = maybe_material
            return
        
        callable_handler = owner_or_callable
        material_name = handler_name_or_material
        self._owner_ref = None
        self._handler_name = None
        self._bound_handler = callable_handler
        self._qm_material_name = material_name
    
    def mousePressEvent(self, e):
        """Handle selection on click"""
        try:
            if e.button() == QtCore.Qt.RightButton:
                QtWidgets.QLabel.mousePressEvent(self, e)
                return
            
            mods = e.modifiers()
            shift = bool(mods & QtCore.Qt.ShiftModifier)
            ctrl = bool(mods & (QtCore.Qt.ControlModifier | QtCore.Qt.MetaModifier))
            
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
                        handler(self._qm_material_name, source='label', shift=shift, ctrl=ctrl)
                        QtWidgets.QLabel.mousePressEvent(self, e)
                        return
            
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
                        self._bound_handler(self._qm_material_name, source='label', shift=shift, ctrl=ctrl)
                except RuntimeError:
                    pass
        except RuntimeError:
            pass
        
        QtWidgets.QLabel.mousePressEvent(self, e)
    
    def contextMenuEvent(self, event):
        """Forward to the same context menu handler as LeftClipLineEdit"""
        # The context menu logic will be the same, just access from QLabel
        # Copy the logic from LeftClipLineEdit.contextMenuEvent
        owner = self._owner_ref() if getattr(self, "_owner_ref", None) else None
        
        if not (owner and isValid(owner)):
            return
        
        actual_name = getattr(self, '_actual_material_name', None)
        if not actual_name:
            # Strip HTML tags to get plain text, then strip metadata
            import re
            plain_text = re.sub('<[^<]+?>', '', self.text())
            actual_name = plain_text.strip().split('  (')[0]
        mat = actual_name
        
        # Check node type property
        node_type_prop = self.property("nodeType")
        # Be robust: also detect actual Maya node type for safety
        try:
            is_file_by_cmds = cmds.objExists(mat) and (cmds.nodeType(mat) == "file")
        except Exception:
            is_file_by_cmds = False
        is_file_texture = (node_type_prop == "file_texture") or is_file_by_cmds
        is_procedural_texture = (node_type_prop == "procedural_texture")
        is_shading_group = (node_type_prop == "shading_group")
        is_texture = is_file_texture or is_procedural_texture  # Legacy check
        
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(context_menu_style)
        
        def _safe_call(fn_name, *args, **kwargs):
            try:
                fn = getattr(owner, fn_name, None)
                if callable(fn):
                    fn(*args, **kwargs)
            except Exception as e:
                pass
        
        if is_file_texture:
            act_open = menu.addAction("Open File Location")
            act_open.triggered.connect(lambda: _safe_call("open_file_texture_folder", mat))
            
            # View in Texture Viewer (file context)
            act_view = menu.addAction("View")
            def _open_view_file():
                try:
                    import importlib
                    import QuickMaterials.texture_viewer as _qm_tv
                    _qm_tv = importlib.reload(_qm_tv)
                    _qm_tv.show_texture_viewer_for_file_node(mat)
                except Exception as e:
                    pass
            act_view.triggered.connect(_open_view_file)
            
            menu.addSeparator()
            
            # Graph action for file textures
            act_graph = menu.addAction("Graph")
            act_graph.triggered.connect(lambda: _safe_call("graph_material_network", mat))
            
            menu.addSeparator()
            
            # Colorspace submenu
            cs_menu = menu.addMenu("Colorspace")
            cs_menu.setStyleSheet(context_menu_style)
            try:
                current_cs = owner._get_file_texture_colorspace(mat)
                colorspaces = ['sRGB', 'Raw', 'ACEScg']
                
                for cs in colorspaces:
                    cs_action = cs_menu.addAction(cs)
                    if cs == current_cs:
                        cs_action.setCheckable(True)
                        cs_action.setChecked(True)
                    cs_action.triggered.connect(lambda checked=False, colorspace=cs: _safe_call("_set_file_texture_colorspace", mat, colorspace))
            except Exception:
                pass
        
        elif is_procedural_texture:
            # Procedural texture menu: Graph only
            act_graph = menu.addAction("Graph")
            act_graph.triggered.connect(lambda: _safe_call("graph_material_network", mat))
        
        selected_mats = getattr(owner, "selected_materials_list", [])
        if len(selected_mats) > 1:
            menu.addSeparator()
            if not is_texture:
                batch_select = menu.addAction(f"Select Objs of Selected ({len(selected_mats)} mats)")
                batch_select.triggered.connect(lambda: _safe_call("highlight_selected_materials"))
            batch_graph = menu.addAction(f"Graph All Selected ({len(selected_mats)} items)")
            batch_graph.triggered.connect(lambda: _safe_call("graph_selected_materials"))
        
        menu.exec_(self.mapToGlobal(event.pos()))


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
        self.setProperty("qmEditMode", "false")  # Ensure default hover styling




        # timer to keep text snapped to the left on focus/resize
        self._snap_left_timer = QtCore.QTimer(self)
        self._snap_left_timer.setSingleShot(True)
        self._snap_left_timer.timeout.connect(self._snap_to_left)


    def contextMenuEvent(self, event):
        """Right-click menu with material/texture actions."""
        # Resolve owner (QuickMaterialsUI) safely
        owner = self._owner_ref() if getattr(self, "_owner_ref", None) else None

        if not (owner and isValid(owner)):
            # Owner gone; suppress menu to avoid calling into dead objects
            return

        # Get actual material/texture name (stored separately from display text)
        # For file textures, display text shows filename, but operations need the node name
        actual_name = getattr(self, '_actual_material_name', None)
        if not actual_name:
            # Fallback: try to parse from display text (shouldn't happen if _actual_material_name is set)
            actual_name = self.text().strip().split('  (')[0]  # Strip metadata
        mat = actual_name

        # Check node type property
        node_type_prop = self.property("nodeType")
        is_file_texture = (node_type_prop == "file_texture")
        is_procedural_texture = (node_type_prop == "procedural_texture")
        is_shading_group = (node_type_prop == "shading_group")
        is_utility = (node_type_prop == "utility")
        is_texture = is_file_texture or is_procedural_texture  # Legacy check

        # Build the menu
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(context_menu_style)
        
        # Wire actions to existing QuickMaterialsUI methods
        def _safe_call(fn_name, *args, **kwargs):
            try:
                fn = getattr(owner, fn_name, None)
                if callable(fn):
                    fn(*args, **kwargs)
            except Exception as e:
                pass

        if is_file_texture:
            # File texture menu
            act_open = menu.addAction("Open File Location")
            act_open.triggered.connect(lambda: _safe_call("open_file_texture_folder", mat))

            # View in Texture Viewer (file context)
            act_view = menu.addAction("View")
            def _open_view_file():
                try:
                    import importlib
                    import QuickMaterials.texture_viewer as _qm_tv
                    _qm_tv = importlib.reload(_qm_tv)
                    _qm_tv.show_texture_viewer_for_file_node(mat)
                except Exception as e:
                    pass
            act_view.triggered.connect(_open_view_file)
            
            menu.addSeparator()
            
            # Graph action for file textures
            act_graph = menu.addAction("Graph")
            act_graph.triggered.connect(lambda: _safe_call("graph_material_network", mat))
            
            menu.addSeparator()
            
            # Colorspace submenu
            cs_menu = menu.addMenu("Colorspace")
            cs_menu.setStyleSheet(context_menu_style)
            try:
                current_cs = owner._get_file_texture_colorspace(mat)
                colorspaces = ['sRGB', 'Raw', 'ACEScg']
                
                for cs in colorspaces:
                    cs_action = cs_menu.addAction(cs)
                    if cs == current_cs:
                        cs_action.setCheckable(True)
                        cs_action.setChecked(True)
                    cs_action.triggered.connect(lambda checked=False, colorspace=cs: _safe_call("_set_file_texture_colorspace", mat, colorspace))
            except Exception:
                pass
                
        elif is_procedural_texture:
            # Procedural texture menu: Graph only
            act_graph = menu.addAction("Graph")
            act_graph.triggered.connect(lambda: _safe_call("graph_material_network", mat))
                
        elif is_utility:
            # Utilities: Graph only
            act_graph = menu.addAction("Graph")
            act_graph.triggered.connect(lambda: _safe_call("graph_material_network", mat))

        elif is_shading_group:
            # Shading groups: Select Objects + Graph
            act_select_objs = menu.addAction("Select Objs")
            act_select_objs.triggered.connect(lambda: _safe_call("select_objects_for_shading_group", mat))

            act_graph = menu.addAction("Graph")
            act_graph.triggered.connect(lambda: _safe_call("graph_shading_group_network", mat))

        elif not is_texture:
            # Material menu
            act_assign = menu.addAction("Assign")
            act_select = menu.addAction("Select Objs")
            act_graph  = menu.addAction("Graph")
            act_imp_tx = menu.addAction("Import Textures")

            # Disable Import Tx for default materials
            try:
                is_default = (self.property("materialType") == "default")
            except Exception:
                is_default = False
            act_imp_tx.setEnabled(not is_default)

            act_assign.triggered.connect(lambda: _safe_call("assign_material", mat))
            act_select.triggered.connect(lambda: _safe_call("highlight_material", mat))
            act_graph.triggered.connect(lambda: _safe_call("graph_material_network", mat))
            act_imp_tx.triggered.connect(lambda: _safe_call("import_tx_material", mat))
            
            # Duplicate material (materials only, not textures or shading groups)
            act_duplicate = menu.addAction("Duplicate")
            act_duplicate.triggered.connect(lambda checked=False: _safe_call("duplicate_material", mat))

            # Add View Textures if the material has any file textures
            try:
                def _material_has_textures(material_name):
                    try:
                        attrs = cmds.listAttr(material_name, connectable=True) or []
                        for a in attrs:
                            conns = cmds.listConnections(f"{material_name}.{a}", s=True, d=False, plugs=True) or []
                            for c in conns:
                                node = c.split('.')[0]
                                if cmds.nodeType(node) == 'file':
                                    return True
                                files = cmds.listConnections(node, type='file', s=True, d=False) or []
                                if files:
                                    return True
                        return False
                    except Exception:
                        return False

                if _material_has_textures(mat):
                    act_view_textures = menu.addAction("View Textures")
                    def _open_view_mat():
                        try:
                            import importlib
                            import QuickMaterials.texture_viewer as _qm_tv
                            _qm_tv = importlib.reload(_qm_tv)
                            _qm_tv.show_texture_viewer_for_material(mat)
                        except Exception as e:
                            pass
                    act_view_textures.triggered.connect(_open_view_mat)
            except Exception as e:
                pass

        # Check if multiple materials/textures are selected for batch operations
        selected_mats = getattr(owner, "selected_materials_list", [])
        if len(selected_mats) > 1 and not (is_utility or is_shading_group):
            menu.addSeparator()
            
            # Batch operation: Select objects from all selected materials
            if not is_texture:
                act_select_all = menu.addAction(f"Select Objs of Selected ({len(selected_mats)})")
                act_select_all.triggered.connect(lambda: _safe_call("highlight_materials_batch", selected_mats))
            
            # Batch operation: Graph all selected materials/textures
            act_graph_all = menu.addAction(f"Graph All Selected ({len(selected_mats)})")
            act_graph_all.triggered.connect(lambda: _safe_call("graph_materials_batch", selected_mats))
            
            # Batch operation: Duplicate all selected materials (materials only)
            if not is_texture and not is_utility:
                act_duplicate_all = menu.addAction(f"Duplicate Selected ({len(selected_mats)})")
                act_duplicate_all.triggered.connect(lambda checked=False: _safe_call("duplicate_selected_materials"))

        # Show the menu (PySide2 vs PySide6 difference)
        try:
            if QT_LIB == 6:
                menu.exec(event.globalPos())
            else:
                menu.exec_(event.globalPos())
        except Exception as e:
            pass





    def mouseDoubleClickEvent(self, e):
        """
        Double-click toggles edit mode:
          • If read-only → enter edit mode.
          • If editing   → exit edit mode (same as clicking off).
        """
        if self.isReadOnly():
            # Enter edit mode
            # Remember the pre-edit name so rename() has the correct "from" value
            try:
                self._pre_edit_text = self.text()
            except Exception:
                self._pre_edit_text = self.text()
            self.setReadOnly(False)
            self.setProperty("editing", "true")
            self.setProperty("qmEditMode", "true")  # Enable edit mode highlighting
            self.style().unpolish(self); self.style().polish(self); self.update()
            # Set focus so typing works immediately
            self.setFocus()
            # Select all text for easy replacement
            self.selectAll()
        else:
            # Exit edit mode (editingFinished will handle rename on focus change)
            self.setReadOnly(True)
            self.setProperty("editing", "false")
            self.setProperty("qmEditMode", "false")  # Disable edit mode highlighting
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
            # Ignore right-clicks here (they'll be handled by contextMenuEvent)
            if e.button() == QtCore.Qt.RightButton:
                QtWidgets.QLineEdit.mousePressEvent(self, e)
                return

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




    def keyPressEvent(self, e):
        """Handle keyboard input, especially Escape key to cancel editing."""
        if e.key() == QtCore.Qt.Key_Escape:
            # If we're in edit mode, cancel the rename
            if not self.isReadOnly():
                # Revert to the original text
                original_text = getattr(self, "_pre_edit_text", None) or self.text()
                self.setText(original_text)
                # Exit edit mode
                self.setReadOnly(True)
                self.setProperty("editing", "false")
                self.setProperty("qmEditMode", "false")
                self.style().unpolish(self)
                self.style().polish(self)
                self.update()
                # Clear focus
                self.clearFocus()
                # Accept the event to prevent it from propagating (closing the dialog)
                e.accept()
                return
        
        # For all other keys, use default behavior
        super(LeftClipLineEdit, self).keyPressEvent(e)

    def focusOutEvent(self, e):
        """Lock again when focus is lost (optional)."""
        if not self.isReadOnly():
            self.setReadOnly(True)
            self.setProperty("editing", "false")  # QSS: revert background
            self.setProperty("qmEditMode", "false")  # Restore hover color after editing
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

    def setText(self, text):
        """
        Ensure text snaps back to the left when the widget is read-only, so the
        material name is always fully visible after programmatic updates.
        """
        super(LeftClipLineEdit, self).setText(text)
        if self.isReadOnly():
            try:
                self._snap_to_left()
            except Exception:
                pass


class MaterialDisplayLineEdit(LeftClipLineEdit):
    """
    LeftClipLineEdit variant that renders secondary metadata (e.g. shader type)
    next to the primary material name using the same muted grey styling as the
    file texture colorspace display.
    """
    def __init__(self, text="", parent=None):
        super(MaterialDisplayLineEdit, self).__init__(text, parent)
        self._secondary_text = ""
        self._secondary_color = QtGui.QColor("#999999")

    def setSecondaryText(self, secondary_text, color=None):
        """Assign the secondary metadata string and optional color override."""
        self._secondary_text = secondary_text or ""
        if color is not None:
            try:
                self._secondary_color = QtGui.QColor(color)
            except Exception:
                self._secondary_color = QtGui.QColor("#999999")
        if self.isReadOnly():
            self.update()

    def clearSecondaryText(self):
        if self._secondary_text:
            self._secondary_text = ""
            if self.isReadOnly():
                self.update()

    def setReadOnly(self, value):
        previous = self.isReadOnly()
        super(MaterialDisplayLineEdit, self).setReadOnly(value)
        if previous != value:
            self.update()

    def paintEvent(self, event):
        super(MaterialDisplayLineEdit, self).paintEvent(event)

        if not self.isReadOnly() or not self._secondary_text:
            return

        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
            color = self._secondary_color or QtGui.QColor("#999999")
            painter.setPen(color)

            # PySide6/PySide2 compatibility for text margins
            if QT_LIB == 6:
                # PySide6: textMargins() returns QMargins object
                margins = self.textMargins()
                left_margin = margins.left()
                top_margin = margins.top()
                right_margin = margins.right()
                bottom_margin = margins.bottom()
            else:
                # PySide2: getTextMargins() returns tuple
                left_margin, top_margin, right_margin, bottom_margin = self.getTextMargins()
            contents_rect = self.rect().adjusted(left_margin, top_margin, -right_margin, -bottom_margin)

            metrics = self.fontMetrics()
            primary_text = self.text() or ""
            primary_width = metrics.horizontalAdvance(primary_text) if primary_text else 0
            spacing_width = metrics.horizontalAdvance("  ") if primary_text else 0

            secondary_rect = QtCore.QRect(contents_rect)
            secondary_rect.setLeft(int(contents_rect.left() + primary_width + spacing_width))

            painter.drawText(secondary_rect, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, f"({self._secondary_text})")
        finally:
            painter.end()


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

/* ---- Container background ---- */
QWidget {
    background-color: #333333;
}

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
    padding: 0px 0px;
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
    padding: 1px 1px;
    min-height: 22px;
    selection-background-color: transparent;
    selection-color: #ffffff;
}

/* Hover highlighting for general material entries */
/* More specific rules for default, unused, and selected override this */
QLineEdit:hover {
    background-color: #4d4d4d;
    border: 1px solid #5d5d5d;
}

QLineEdit[nodeType="material"]:hover {
    background-color: #4d4d4d;
    border: 1px solid #5d5d5d;
}

/* Selected materials: no hover change - keep selected styling */
QLineEdit[nodeType="material"][qmSelected="true"]:hover {
    background-color: #3e637a;
    color: #ffffff;
    border: 1px solid #ccdbe6;
}

/* Disable hover highlighting when in edit mode */
QLineEdit[qmEditMode="true"]:hover {
    background-color: #333333 !important;
    border: 1px solid #777777 !important;
}

/* Disable hover highlighting for material entries when in edit mode (more specific) */
QLineEdit[nodeType="material"][qmEditMode="true"]:hover {
    background-color: #333333 !important;
    border: 1px solid #777777 !important;
}

/* Show text highlighting when in edit mode - visible blue selection background */
QLineEdit[qmEditMode="true"] {
    selection-background-color: #0078d4;
    selection-color: #ffffff;
}

/* Read-only vs editable */
QLineEdit[readOnly="true"]  { color: #dddddd; border: 1px solid #3d3d3d; }
QLineEdit[readOnly="false"] { color: #ffffff; border: 1px solid #777777; }

/* Editing hint (requires code to set editing=true while typing) */
QLineEdit[readOnly="false"][editing="true"] { background-color: #333333; }

/* Focus ring for editable fields */
QLineEdit[readOnly="false"]:focus { border: 1px solid #9a9a9a; }

/* Default-material rows: italic text only, otherwise same as regular materials */
QLineEdit[materialType="default"] {
    font-style: italic;
}

/* Selected line edits (list selection highlight) - always show blue highlight on single click */
QLineEdit[qmSelected="true"] {
    background-color: #3e637a;
    color: #ffffff;
    border: 1px solid #ccdbe6;
}

/* Selected state must override hover - keep selected styling even when hovering */
QLineEdit[qmSelected="true"]:hover {
    background-color: #3e637a !important;
    color: #ffffff !important;
    border: 1px solid #ccdbe6 !important;
}

/* ===================================================================
   Node Type Styling - File Textures (Yellow), Procedural (Purple), Shading Groups (Blue)
   =================================================================== */

/* File Textures - Yellow tint */
QLineEdit[nodeType="file_texture"],
QLabel[nodeType="file_texture"] {
    background-color: #4a4a3a;  /* yellowish tint */
    color: #e0e0e0;
    border: 1px solid #5a5a4a;
    border-radius: 6px;
    padding: 1px 1px;
    min-height: 22px;
    font-weight: bold;
}

QLineEdit[nodeType="file_texture"]:hover,
QLabel[nodeType="file_texture"]:hover {
    background-color: #565646;
    color: #ffffff;
}

/* Disable hover highlighting for file textures when in edit mode */
QLineEdit[nodeType="file_texture"][qmEditMode="true"]:hover,
QLabel[nodeType="file_texture"][qmEditMode="true"]:hover {
    background-color: #333333 !important;
    border: 1px solid #777777 !important;
}

QLineEdit[nodeType="file_texture"][qmSelected="true"],
QLabel[nodeType="file_texture"][qmSelected="true"] {
    background-color: #6a6a3a;
    color: #ffffff;
    border: 1px solid #b8b86a;
}

/* File texture selected state must override hover */
QLineEdit[nodeType="file_texture"][qmSelected="true"]:hover,
QLabel[nodeType="file_texture"][qmSelected="true"]:hover {
    background-color: #6a6a3a !important;
    color: #ffffff !important;
    border: 1px solid #b8b86a !important;
}

QLineEdit[nodeType="file_texture"][readOnly="false"][editing="true"] { 
    background-color: #333333;
}

QLabel[nodeType="file_texture"] {
    padding: 1px 1px;
    min-height: 22px;
    max-height: 22px;
    line-height: 20px;  /* Tight line height to match QLineEdit rendering */
    margin: 0px;
}

/* Procedural Textures - Grey styling */
QLineEdit[nodeType="procedural_texture"] {
    background-color: #565656;  /* dimmer grey */
    color: #e8e8e8;  /* brighter light grey text */
    border: 1px solid #565656;  /* invisible border matching background to prevent text shift */
    border-radius: 6px;
    padding: 1px 1px;
    min-height: 22px;
    font-weight: bold;
}

QLineEdit[nodeType="procedural_texture"]:hover {
    background-color: #5e5e5e;  /* slightly brighter on hover */
    color: #e8e8e8;
    border: 1px solid #5e5e5e;  /* invisible border matching hover background */
}

/* Disable hover highlighting for procedural textures when in edit mode */
QLineEdit[nodeType="procedural_texture"][qmEditMode="true"]:hover {
    background-color: #333333 !important;
    border: 1px solid #777777 !important;
}

QLineEdit[nodeType="procedural_texture"][qmSelected="true"] {
    background-color: #7a7a7a;
    color: #ffffff;  /* white text when selected */
    border: 1px solid #2cf28c;
}

/* Procedural texture selected state must override hover */
QLineEdit[nodeType="procedural_texture"][qmSelected="true"]:hover {
    background-color: #7a7a7a !important;
    color: #ffffff !important;  /* white text when selected */
    border: 1px solid #2cf28c !important;
}

QLineEdit[nodeType="procedural_texture"][readOnly="false"][editing="true"] { 
    background-color: #333333;
}

/* Shading Groups - Blue tint */
QLineEdit[nodeType="shading_group"] {
    background-color: #2a3a4a;  /* blue tint */
    color: #c0d0e0;
    border: 1px solid #3a4a5a;
    border-radius: 6px;
    padding: 1px 1px;
    min-height: 22px;
    font-weight: bold;
}

QLineEdit[nodeType="shading_group"]:hover {
    background-color: #364656;
    color: #ffffff;
}

/* Disable hover highlighting for shading groups when in edit mode */
QLineEdit[nodeType="shading_group"][qmEditMode="true"]:hover {
    background-color: #333333 !important;
    border: 1px solid #777777 !important;
}

QLineEdit[nodeType="shading_group"][qmSelected="true"] {
    background-color: #4a5a7a;  /* slightly brighter blue */
    color: #ffffff;
    border: 1px solid #2cf28c;
}

/* Shading group selected state must override hover */
QLineEdit[nodeType="shading_group"][qmSelected="true"]:hover {
    background-color: #4a5a7a !important;  /* slightly brighter blue */
    color: #ffffff !important;
    border: 1px solid #2cf28c !important;
}

QLineEdit[nodeType="shading_group"][readOnly="false"][editing="true"] { 
    background-color: #333333;
}

/* Utility Nodes - match procedural texture styling */
QLineEdit[nodeType="utility"],
QLabel[nodeType="utility"] {
    background-color: #565656;  /* dimmer grey */
    color: #e8e8e8;
    border: 1px solid #565656;
    border-radius: 6px;
    padding: 1px 1px;
    min-height: 22px;
    font-weight: bold;
}

QLineEdit[nodeType="utility"]:hover,
QLabel[nodeType="utility"]:hover {
    background-color: #5e5e5e;
    color: #e8e8e8;
    border: 1px solid #5e5e5e;
}

QLineEdit[nodeType="utility"][qmSelected="true"],
QLabel[nodeType="utility"][qmSelected="true"] {
    background-color: #7a7a7a;
    color: #ffffff;
    border: 1px solid #2cf28c;
}

QLineEdit[nodeType="utility"][qmSelected="true"]:hover,
QLabel[nodeType="utility"][qmSelected="true"]:hover {
    background-color: #7a7a7a !important;
    color: #ffffff !important;
    border: 1px solid #2cf28c !important;
}

QLineEdit[nodeType="utility"][qmEditMode="true"]:hover,
QLabel[nodeType="utility"][qmEditMode="true"]:hover {
    background-color: #333333 !important;
    border: 1px solid #777777 !important;
}

QLineEdit[nodeType="utility"][readOnly="false"][editing="true"],
QLabel[nodeType="utility"][readOnly="false"][editing="true"] {
    background-color: #333333;
}

/* Default materials with italic text - selected state uses normal selection styling */

/* Unused materials/textures - light red highlight (only when checkbox is checked) */
QLineEdit[qmUnused="true"],
QLabel[qmUnused="true"] {
    background-color: #4a3a3a !important;  /* light red tint */
    border: 1px solid #5a4a4a !important;
    color: #ffffff !important;
}

/* Unused materials: no hover change - keep red styling */
QLineEdit[qmUnused="true"]:hover,
QLabel[qmUnused="true"]:hover {
    background-color: #4a3a3a !important;
    color: #ffffff !important;
    border: 1px solid #5a4a4a !important;
}

/* Disable hover highlighting for unused materials when in edit mode */
QLineEdit[qmUnused="true"][qmEditMode="true"]:hover,
QLabel[qmUnused="true"][qmEditMode="true"]:hover {
    background-color: #333333 !important;
    border: 1px solid #777777 !important;
}

QLineEdit[qmUnused="true"][qmSelected="true"],
QLabel[qmUnused="true"][qmSelected="true"] {
    background-color: #6a4a4a !important;  /* slightly darker red when selected */
    color: #ffffff !important;
    border: 1px solid #8a5a5a !important;
}

/* Unused selected materials: no hover change - keep darker red styling */
QLineEdit[qmUnused="true"][qmSelected="true"]:hover,
QLabel[qmUnused="true"][qmSelected="true"]:hover {
    background-color: #6a4a4a !important;
    color: #ffffff !important;
    border: 1px solid #8a5a5a !important;
}

/* Default materials with unused styling - uses normal unused styling with italic text */

/* Unused file textures - override nodeType styling when unused */
/* Need higher specificity to override the yellow file texture styling */
QLineEdit[nodeType="file_texture"][qmUnused="true"],
QLabel[nodeType="file_texture"][qmUnused="true"] {
    background-color: #4a3a3a !important;  /* light red tint (overrides yellow) */
    border: 1px solid #5a4a4a !important;
    color: #ffffff !important;
}

QLineEdit[nodeType="file_texture"][qmUnused="true"]:hover,
QLabel[nodeType="file_texture"][qmUnused="true"]:hover {
    background-color: #563a3a !important;
    color: #ffffff !important;
    border: 1px solid #5a4a4a !important;
}

QLineEdit[nodeType="file_texture"][qmUnused="true"][qmSelected="true"],
QLabel[nodeType="file_texture"][qmUnused="true"][qmSelected="true"] {
    background-color: #6a4a4a !important;
    color: #ffffff !important;
    border: 1px solid #8a5a5a !important;
}

/* Unused procedural textures - override nodeType styling when unused */
QLineEdit[nodeType="procedural_texture"][qmUnused="true"] {
    background-color: #4a3a3a !important;  /* light red tint (overrides purple) */
    border: 1px solid #5a4a4a !important;
    color: #ffffff !important;
}

QLineEdit[nodeType="procedural_texture"][qmUnused="true"]:hover {
    background-color: #563a3a !important;
    color: #ffffff !important;
    border: 1px solid #5a4a4a !important;
}

QLineEdit[nodeType="procedural_texture"][qmUnused="true"][qmSelected="true"] {
    background-color: #6a4a4a !important;
    color: #ffffff !important;
    border: 1px solid #8a5a5a !important;
}

/* Unused shading groups - override nodeType styling when unused */
QLineEdit[nodeType="shading_group"][qmUnused="true"] {
    background-color: #4a3a3a !important;  /* light red tint (overrides blue) */
    border: 1px solid #5a4a4a !important;
    color: #ffffff !important;
}

QLineEdit[nodeType="shading_group"][qmUnused="true"]:hover {
    background-color: #563a3a !important;
    color: #ffffff !important;
    border: 1px solid #5a4a4a !important;
}

QLineEdit[nodeType="shading_group"][qmUnused="true"][qmSelected="true"] {
    background-color: #6a4a4a !important;
    color: #ffffff !important;
    border: 1px solid #8a5a5a !important;
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
QLabel[materialType="default"] { font-style: italic; }

/* Unused QLabels (textures) - ensure background is visible */
QLabel[qmUnused="true"] {
    background-color: #4a3a3a !important;
    border: 1px solid #5a4a4a !important;
    border-radius: 6px !important;
    padding: 1px 1px !important;
    min-height: 22px !important;
    max-height: 22px !important;
}

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

/* Default-material checkboxes: normal styling */

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

/* ---------------------------------------------
   Horizontal Lines (QFrame HLine)
   --------------------------------------------- */
QFrame[frameShape="4"] {  /* HLine */
    background-color: #666666;
    border: none;
    border-top: 1px solid #666666;
    min-height: 1px;
    max-height: 1px;
}

/* ---------------------------------------------
   Context Menu (Right-click menu)
   --------------------------------------------- */
QMenu {
    background-color: #3a3a3a;
    color: #ffffff;
    border: 1px solid #555555;
}

QMenu::item {
    background-color: transparent;
    color: #ffffff;
    padding: 4px 20px 4px 20px;
}

QMenu::item:selected {
    background-color: #444444;
    color: #ffffff;
}

QMenu::item:hover {
    background-color: #444444;
    color: #ffffff;
}

QMenu::separator {
    height: 1px;
    background-color: #555555;
    margin: 2px 0px;
}
"""

# Stylesheet for context menus (right-click menus)
context_menu_style = """
QMenu {
    background-color: #3a3a3a;
    color: #ffffff;
    border: 1px solid #555555;
}

QMenu::item {
    background-color: transparent;
    color: #ffffff;
    padding: 4px 20px 4px 20px;
}

QMenu::item:selected {
    background-color: #444444;
    color: #ffffff;
}

QMenu::item:hover {
    background-color: #444444;
    color: #ffffff;
}

QMenu::separator {
    height: 1px;
    background-color: #555555;
    margin: 2px 0px;
}
"""

material_filters_button_style = """
QPushButton {
    font-family: 'Segoe UI';
    font-size: 12px;
    color: #ffffff;
    background-color: #666666;
    border: 2px solid #444444;
    border-radius: 8px;
    padding: 2px 5px;
}

QPushButton:hover {
    background-color: #888888;
}

QPushButton:pressed {
    background-color: #333333;
}

QPushButton:checked {
    background-color: #333333;
    border: 1px solid #00f7c8;
}

QPushButton:disabled {
    color: #666666;
    border: 1px solid #555555;
    background-color: #4a4a4a;
}
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
        except Exception as e:
            pass

    if not registered:
        # Fallback: compiled Python resource module (pyrcc / pyside6-rcc output)
        try:
            # Try relative package import first
            try:
                from . import icons_rc  # type: ignore
            except Exception:
                import icons_rc  # type: ignore
            registered = True
        except Exception as e:
            pass

    # Tiny probe so we know the path is live
    try:
        probe = QtGui.QIcon(":/icons/arrow_up_pressed.png")
        # Test the new arrow_combo_box icon
        probe_combo = QtGui.QIcon(":/icons/arrow_combo_box.png")
    except Exception as e:
        pass

    return registered


def get_arrow_combo_box_icon():
    """Get the arrow_combo_box icon for combo box styling."""
    return QtGui.QIcon(":/icons/arrow_combo_box.png")


def get_combo_box_stylesheet_with_custom_arrow():
    """Get a stylesheet for combo boxes that uses the custom arrow_combo_box icon."""
    return """
        QComboBox {
            border: 1px solid #ccc;
            border-radius: 3px;
            padding: 2px 18px 2px 3px;
            min-width: 6em;
            background-color: white;
        }
        QComboBox::drop-down {
            subcontrol-origin: padding;
            subcontrol-position: top right;
            width: 16px;
            border-left-width: 1px;
            border-left-color: #ccc;
            border-left-style: solid;
            border-top-right-radius: 3px;
            border-bottom-right-radius: 3px;
        }
        QComboBox::down-arrow {
            image: url(:/icons/arrow_combo_box.png);
            width: 12px;
            height: 12px;
        }
        QComboBox:hover {
            border-color: #999;
        }
        QComboBox:focus {
            border-color: #0078d4;
        }
    """


# Load UI Function
class QuickMaterialsSettingsUI(QtWidgets.QDialog):
    """
    UI for Quick Materials Settings including texture importer settings.
    Loads from quickMaterialsSettings.ui and saves to the main settings JSON.
    """
    def __init__(self, parent=None):
        super(QuickMaterialsSettingsUI, self).__init__(parent)
        
        # Set dialog properties
        self.setWindowTitle("Quick Materials Settings")
        self.setModal(False)  # Non-modal dialog
        # Remove minimum size constraint to allow dialog to shrink
        
        # 1) Load the .ui file
        loader = QtUiTools.QUiLoader()
        script_dir = os.path.dirname(__file__)
        ui_path = os.path.join(script_dir, "QtDesigner", "quickMaterialsSettings.ui")
        
        ui_file = QtCore.QFile(ui_path)
        if not ui_file.open(QtCore.QFile.ReadOnly):
            cmds.warning(f"Cannot open Settings UI: {ui_path}")
            return
        self.ui_instance = loader.load(ui_file, parentWidget=self)
        ui_file.close()

        # Mount the loaded UI into a layout so it resizes with the window
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)  # Remove outer border margins (top, left, bottom, right)
        main_layout.addWidget(self.ui_instance)
        self.ui_instance.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

        # 2) Grab all named widgets
        self.ui_elements = {}
        self.auto_initialize_ui_elements(self.ui_instance)

        # 3) Connect buttons
        self.setup_connections()
        
        # 4) Load saved settings
        self._apply_saved_settings()
        
        # 5) Set up tooltip for custom path line edit
        custom_path_edit = self.ui_elements.get("textureSearchCustomPathLineEdit")
        if custom_path_edit:
            tooltip_text = (
                "Custom texture search path with dynamic key substitution.\n\n"
                "Available keys:\n"
                "• (scene) - Current Maya file folder\n"
                "• (project) - Current Maya project folder\n\n"
                "Add any path after the key:\n"
                "• (scene)/textures\n"
                "• (scene)/assets/textures\n"
                "• (project)/sourceimages\n"
                "• (project)/sourceimages/materials"
            )
            custom_path_edit.setToolTip(tooltip_text)
        
        # 6) Ensure custom path widgets are properly enabled/disabled based on initial state
        self._update_custom_path_widgets()
    
    def showEvent(self, event):
        """Override showEvent to prevent auto-focus on materialNamingPrefixLineEdit."""
        super(QuickMaterialsSettingsUI, self).showEvent(event)
        # Clear focus from materialNamingPrefixLineEdit to avoid auto-selection
        prefix_le = self.ui_elements.get("materialNamingPrefixLineEdit")
        if prefix_le:
            prefix_le.clearFocus()
        # Set focus to the dialog itself instead
        self.setFocus()
    
    def auto_initialize_ui_elements(self, widget):
        """Recursively find all named widgets and store them in self.ui_elements."""
        if hasattr(widget, 'objectName') and widget.objectName():
            self.ui_elements[widget.objectName()] = widget
        
        for child in widget.children():
            if isinstance(child, QtWidgets.QWidget):
                self.auto_initialize_ui_elements(child)

    def setup_connections(self):
        """Connect UI elements to their handlers."""
        # Connect save button
        save_btn = self.ui_elements.get("quickMaterialsSaveSettings")
        if save_btn:
            save_btn.clicked.connect(self._save_settings)
        
        # Connect close/cancel button if it exists
        close_btn = self.ui_elements.get("quickMaterialsCloseSettings")
        if close_btn:
            close_btn.clicked.connect(self.close)
            
        # Connect texture importer settings checkboxes
        for name in (
            "textureSearchMayaFileCheckbox",
            "textureSearchMayaSourceimagesCheckbox", 
            "textureSearchCustomPathCheckbox"
        ):
            cb = self.ui_elements.get(name)
            if cb:
                cb.toggled.connect(self._update_custom_path_widgets)
        
        # Connect custom path set button
        set_btn = self.ui_elements.get("textureSearchCustomPathSetButton")
        if set_btn:
            set_btn.clicked.connect(self._choose_custom_path)
        
        # Connect edit texture search names button
        names_btn = self.ui_elements.get("editTextureSearchNamesButton")
        if names_btn:
            try:
                names_btn.clicked.disconnect()
            except Exception:
                pass
            names_btn.clicked.connect(self.open_texture_search_names_ui)
        
        # Connect material attribute frame visibility checkboxes
        # Map checkbox names to frame names in the main UI
        self._attribute_checkbox_to_frame = {
            'colorAttributeFrameCheckbox': 'colorPickerFrame',
            'roughnesAttributeFrameCheckbox': 'roughnessSliderFrame',
            'metalnessAttributeFrameCheckbox': 'metalnessSliderFrame',
            'emissionAttributeFrameCheckbox': 'emissionSliderFrame',
            'opacityAttributeFrameCheckbox': 'opacitySliderFrame',
            'transmissionAttributeFrameCheckbox': 'transmissionSliderFrame',
            'subsurfaceAttributeFrameCheckbox': 'subsurfaceSliderFrame'
        }
        
        for checkbox_name in self._attribute_checkbox_to_frame.keys():
            cb = self.ui_elements.get(checkbox_name)
            if cb:
                try:
                    cb.toggled.disconnect()
                except Exception:
                    pass
                cb.toggled.connect(lambda checked, name=checkbox_name: self._on_attribute_checkbox_toggled(name, checked))
        
        # Connect restore defaults button
        restore_btn = self.ui_elements.get("restoreDefaultSettingsButton")
        if restore_btn:
            restore_btn.clicked.connect(self._restore_default_settings)
    
    def _restore_default_settings(self):
        """Restore all settings to their default values."""
        # Confirm with user
        result = cmds.confirmDialog(
            title="Restore Default Settings",
            message="This will reset all Quick Materials settings to defaults.\n\nThis includes:\n- Texture importer settings\n- Material creator attribute visibility\n- Material naming prefix/suffix\n- UI state (material list filters, tabs, etc.)\n\nContinue?",
            button=["Yes", "No"],
            defaultButton="No",
            cancelButton="No",
            dismissString="No"
        )
        
        if result != "Yes":
            return
        
        # Restore texture importer defaults
        if "textureSearchMayaFileCheckbox" in self.ui_elements:
            self.ui_elements["textureSearchMayaFileCheckbox"].setChecked(True)
        if "textureSearchMayaSourceimagesCheckbox" in self.ui_elements:
            self.ui_elements["textureSearchMayaSourceimagesCheckbox"].setChecked(False)
        if "textureSearchCustomPathCheckbox" in self.ui_elements:
            self.ui_elements["textureSearchCustomPathCheckbox"].setChecked(False)
        if "textureSearchCustomPathLineEdit" in self.ui_elements:
            self.ui_elements["textureSearchCustomPathLineEdit"].setText("")
        
        # Restore material creator attribute frame visibility defaults (only color and roughness visible)
        if hasattr(self, '_attribute_checkbox_to_frame'):
            # Default visibility: only color and roughness
            default_visible = {
                'colorAttributeFrameCheckbox': True,
                'roughnesAttributeFrameCheckbox': True,
                'metalnessAttributeFrameCheckbox': False,
                'emissionAttributeFrameCheckbox': False,
                'opacityAttributeFrameCheckbox': False,
                'transmissionAttributeFrameCheckbox': False,
                'subsurfaceAttributeFrameCheckbox': False
            }
            for checkbox_name in self._attribute_checkbox_to_frame.keys():
                cb = self.ui_elements.get(checkbox_name)
                if cb:
                    # Block signals to avoid triggering updates while restoring
                    cb.blockSignals(True)
                    default_state = default_visible.get(checkbox_name, False)
                    cb.setChecked(default_state)
                    cb.blockSignals(False)
                    # Update main UI if available
                    self._on_attribute_checkbox_toggled(checkbox_name, default_state)
        
        # Restore material naming defaults
        prefix_le = self.ui_elements.get("materialNamingPrefixLineEdit")
        suffix_le = self.ui_elements.get("materialNamingSuffixLineEdit")
        if prefix_le:
            prefix_le.setText("M_")
        if suffix_le:
            suffix_le.setText("")
        
        # Update custom path widgets state
        self._update_custom_path_widgets()
        
        # Reset settings file to defaults
        self._reset_settings_file_to_defaults()
        
        # Reset UI state file to defaults
        self._reset_ui_state_to_defaults()
        
        # Show confirmation
        cmds.inViewMessage(amg="<hl>✔ Settings restored to defaults</hl>", pos="topCenter", fade=True)
    
    def _reset_settings_file_to_defaults(self):
        """Reset the settings file (quick_materials_settings.json) to defaults."""
        try:
            script_dir = os.path.dirname(__file__)
            settings_dir = os.path.join(script_dir, "settings")
            os.makedirs(settings_dir, exist_ok=True)
            settings_path = os.path.join(settings_dir, "quick_materials_settings.json")
            
            # Default settings
            default_settings = {
                'material_creator': {
                    'name_prefix': 'M_',
                    'name_suffix': '',
                    # Only color and roughness visible by default
                    'attribute_frame_visible_colorPickerFrame': True,
                    'attribute_frame_visible_roughnessSliderFrame': True,
                    'attribute_frame_visible_metalnessSliderFrame': False,
                    'attribute_frame_visible_emissionSliderFrame': False,
                    'attribute_frame_visible_opacitySliderFrame': False,
                    'attribute_frame_visible_transmissionSliderFrame': False,
                    'attribute_frame_visible_subsurfaceSliderFrame': False
                },
                'material_list': {},
                'texture_importer': {
                    'default_mode': 'maya_file',
                    'custom_path': ''
                }
            }
            
            # Write default settings to file
            with open(settings_path, 'w') as f:
                json.dump(default_settings, f, indent=2)
        except Exception as e:
            print(f"[QuickMaterials] Failed to reset settings file to defaults: {e}")
    
    def _reset_ui_state_to_defaults(self):
        """Reset the UI state file (quick_materials_state.json) to defaults."""
        try:
            script_dir = os.path.dirname(__file__)
            state_file_path = os.path.join(script_dir, "quick_materials_state.json")
            
            # Default UI state
            default_state = {
                'material_creator': {
                    'material_type': 'standardSurface',
                    'color': {'r': 255, 'g': 0, 'b': 0},
                    'materialColorHueSlider': 0,
                    'materialColorSaturationSlider': 100,
                    'materialColorValueSlider': 100,
                    'roughnessSpinBox': 0.75,
                    'metalnessSpinBox': 0.0,
                    'emissionSpinBox': 0.0,
                    'opacitySpinBox': 1.0,
                    'transmissionSpinBox': 0.0,
                    'subsurfaceSpinBox': 0.0,
                    'materialPerMeshCheckbox': False,
                    'randomHueCheckbox': False,
                    'material_naming_template': '(selection)'
                },
                'material_list': {
                    'sort_mode': 'name',
                    'sort_desc': False,
                    'showTexturesCheckbox': True,
                    'showProceduralTexturesCheckbox': True,
                    'showShadingGroupsCheckbox': True,
                    'hideNamespacesCheckbox': False,
                    'highlightUnusedCheckbox': False,
                    'showIconsCheckbox': True,
                    'showShaderSwatchesCheckbox': True,
                    'showOtherIconsCheckbox': True,
                    'hideDefaultMaterialsCheckbox': False,
                    'material_list_options_visible': False,
                    'material_filters_visible': False,
                    'utilities_connected_only': True,
                    'toggleMaterialCreatorVis': True,
                    'toggleMaterialToolsVis': True,
                    'toggleMaterialListVis': True,
                    'toggleMaterialManagerVis': True,
                    'active_tab': 'shaders'
                },
                'texture_importer': {
                    'default_mode': 'maya_file',
                    'custom_path': ''
                }
            }
            
            # Write default state to file
            with open(state_file_path, 'w') as f:
                json.dump(default_state, f, indent=2)
            
            # Also reload the main UI state if it's open
            main_ui = self.parent()
            if main_ui and isinstance(main_ui, QuickMaterialsUI):
                # Reload UI state in main UI
                QtCore.QTimer.singleShot(100, lambda: main_ui._load_ui_state())
        except Exception as e:
            print(f"[QuickMaterials] Failed to reset UI state to defaults: {e}")

    def _apply_saved_settings(self):
        """Load settings from main quick materials settings JSON and apply to UI."""
        settings = self._load_settings()
        
        # Apply texture importer settings
        mode = settings.get("default_mode", "maya_file")
        
        # Set checkboxes based on mode
        if "textureSearchMayaFileCheckbox" in self.ui_elements:
            self.ui_elements["textureSearchMayaFileCheckbox"].setChecked(mode == "maya_file")
        if "textureSearchMayaSourceimagesCheckbox" in self.ui_elements:
            self.ui_elements["textureSearchMayaSourceimagesCheckbox"].setChecked(mode == "sourceimages")
        if "textureSearchCustomPathCheckbox" in self.ui_elements:
            self.ui_elements["textureSearchCustomPathCheckbox"].setChecked(mode == "custom")
        if "textureSearchCustomPathLineEdit" in self.ui_elements:
            self.ui_elements["textureSearchCustomPathLineEdit"].setText(settings.get("custom_path", ""))
        
        # Apply material creator attribute frame visibility settings
        # Load from main settings JSON, not just texture_importer section
        try:
            script_dir = os.path.dirname(__file__)
            settings_path = os.path.join(script_dir, "settings", "quick_materials_settings.json")
            if os.path.exists(settings_path):
                with open(settings_path, "r") as f:
                    all_settings = json.load(f)
                    mc_settings = all_settings.get('material_creator', {})
                    
                    # Apply attribute frame visibility checkboxes
                    if hasattr(self, '_attribute_checkbox_to_frame'):
                        for checkbox_name, frame_name in self._attribute_checkbox_to_frame.items():
                            setting_key = f"attribute_frame_visible_{frame_name}"
                            if setting_key in mc_settings:
                                cb = self.ui_elements.get(checkbox_name)
                                if cb:
                                    # Temporarily block signals to avoid triggering updates while loading
                                    cb.blockSignals(True)
                                    cb.setChecked(mc_settings[setting_key])
                                    cb.blockSignals(False)
                    # Apply material naming prefix/suffix if present
                    prefix_le = self.ui_elements.get("materialNamingPrefixLineEdit")
                    suffix_le = self.ui_elements.get("materialNamingSuffixLineEdit")
                    if prefix_le:
                        prefix_le.setText(mc_settings.get('name_prefix', 'M_'))
                    if suffix_le:
                        suffix_le.setText(mc_settings.get('name_suffix', ''))
        except Exception as e:
            pass

    def _load_settings(self):
        """Load texture importer settings from main quick materials settings JSON."""
        path = os.path.join(os.path.dirname(__file__), "settings", "quick_materials_settings.json")
        try:
            with open(path, "r") as f:
                all_settings = json.load(f)
            if isinstance(all_settings, dict) and 'texture_importer' in all_settings:
                return all_settings['texture_importer']
            else:
                return {}
        except FileNotFoundError:
            # Create default settings file
            self._create_default_settings_file(path)
            return {}
        except Exception as e:
            return {}

    def _create_default_settings_file(self, path):
        """Create a default settings file with empty texture importer settings."""
        try:
            script_dir = os.path.dirname(__file__)
            settings_dir = os.path.join(script_dir, "settings")
            os.makedirs(settings_dir, exist_ok=True)
            
            default_settings = {
                'material_creator': {},
                'material_list': {},
                'texture_importer': {
                    'default_mode': 'maya_file',
                    'custom_path': ''
                }
            }
            
            with open(path, "w") as f:
                json.dump(default_settings, f, indent=2)
        except Exception as e:
            pass

    def _update_custom_path_widgets(self):
        """Enable/disable custom-path widgets based on checkbox state."""
        custom_on = self.ui_elements.get("textureSearchCustomPathCheckbox", {}).isChecked()
        for widget_name in (
            "textureSearchCustomPathLineEdit",
            "textureSearchCustomPathSetButton",
            "customSearchFolderPathLabel",
            "createIfDoesntExistCheckbox"
        ):
            w = self.ui_elements.get(widget_name)
            if w:
                w.setEnabled(custom_on)
    
    def _on_attribute_checkbox_toggled(self, checkbox_name, checked):
        """Handle material attribute frame visibility checkbox toggling."""
        if not hasattr(self, '_attribute_checkbox_to_frame'):
            return
        
        frame_name = self._attribute_checkbox_to_frame.get(checkbox_name)
        if not frame_name:
            return
        
        # Get the main UI instance (parent)
        main_ui = self.parent()
        if main_ui and isinstance(main_ui, QuickMaterialsUI):
            # Toggle frame visibility in main UI
            frame = main_ui.findChild(QtWidgets.QWidget, frame_name)
            if frame:
                frame.setVisible(checked)
                if not checked and hasattr(main_ui, "_reset_attribute_to_default"):
                    try:
                        main_ui._reset_attribute_to_default(frame_name)
                    except Exception as exc:
                        pass
                # Refresh minimum size and snap to it to account for visibility change
                QtCore.QTimer.singleShot(0, main_ui.snap_to_minimum)

    def _choose_custom_path(self):
        """
        Enhanced custom path handling with key substitution and folder creation.
        
        If there's a custom path with keys, resolve it and open/create the folder.
        If no custom path, open folder dialog to select a new path.
        """
        current_path = self.ui_elements.get("textureSearchCustomPathLineEdit", {}).text().strip()
        
        if current_path:
            # Resolve the path with key substitution
            resolved_path = self._resolve_custom_path_keys(current_path)
            
            if resolved_path:
                # Check if path exists
                if os.path.exists(resolved_path):
                    # Open existing folder in file explorer
                    if os.name == 'nt':  # Windows
                        os.startfile(resolved_path)
                    elif os.name == 'posix':  # macOS and Linux
                        os.system(f'open "{resolved_path}"' if os.uname().sysname == 'Darwin' else f'xdg-open "{resolved_path}"')
                else:
                    # Path doesn't exist - ask if we should create it
                    create_if_not_exists = self.ui_elements.get("createIfDoesntExistCheckbox")
                    if create_if_not_exists and create_if_not_exists.isChecked():
                        try:
                            os.makedirs(resolved_path, exist_ok=True)
                            
                            # Open the newly created folder
                            if os.name == 'nt':  # Windows
                                os.startfile(resolved_path)
                            elif os.name == 'posix':  # macOS and Linux
                                os.system(f'open "{resolved_path}"' if os.uname().sysname == 'Darwin' else f'xdg-open "{resolved_path}"')
                        except Exception as e:
                            cmds.warning(f"Failed to create folder '{resolved_path}': {e}")
                    else:
                        cmds.warning(f"Folder does not exist: {resolved_path}\nEnable 'Create if doesn't exist' to create it automatically.")
            else:
                cmds.warning(f"Invalid path template: {current_path}")
        else:
            # No custom path set - open folder dialog to select one
            start_dir = cmds.workspace(q=True, rootDirectory=True) or ""
            folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Texture Folder", start_dir)
            if folder:
                self.ui_elements.get("textureSearchCustomPathLineEdit", {}).setText(folder)

    def _resolve_custom_path_keys(self, path_template):
        """
        Resolve key substitution in custom path template.
        
        Supported keys:
        - (scene) → current Maya file folder
        - (project) → current Maya project folder
        
        Everything after the key is treated as regular path components.
        
        Examples:
        - (scene)/textures → [maya file folder]/textures
        - (project)/sourceimages/materials → [project folder]/sourceimages/materials
        
        Returns the resolved path or None if invalid.
        """
        if not path_template:
            return None
            
        try:
            # Get current scene path
            scene_path = cmds.file(q=True, sn=True) or ""
            scene_dir = os.path.dirname(scene_path) if scene_path else ""
            
            # Get current project path
            project_path = cmds.workspace(q=True, rootDirectory=True) or ""
            project_dir = project_path.rstrip("/\\") if project_path else ""
            
            # Replace keys
            resolved = path_template
            resolved = resolved.replace("(scene)", scene_dir)
            resolved = resolved.replace("(project)", project_dir)
            
            # Normalize path separators
            resolved = os.path.normpath(resolved)
            
            return resolved
            
        except Exception as e:
            return None

    def _save_settings(self):
        """Save settings to main quick materials settings JSON."""
        mode = "maya_file"
        if "textureSearchMayaFileCheckbox" in self.ui_elements and self.ui_elements["textureSearchMayaFileCheckbox"].isChecked():
            mode = "maya_file"
        elif "textureSearchMayaSourceimagesCheckbox" in self.ui_elements and self.ui_elements["textureSearchMayaSourceimagesCheckbox"].isChecked():
            mode = "sourceimages"
        elif "textureSearchCustomPathCheckbox" in self.ui_elements and self.ui_elements["textureSearchCustomPathCheckbox"].isChecked():
            mode = "custom"
        
        custom_path = ""
        if "textureSearchCustomPathLineEdit" in self.ui_elements:
            custom_path = self.ui_elements["textureSearchCustomPathLineEdit"].text()
        
        data = {
            "default_mode": mode,
            "custom_path": custom_path,
        }
        
        # Save to main quick materials settings JSON
        try:
            script_dir = os.path.dirname(__file__)
            settings_dir = os.path.join(script_dir, "settings")
            os.makedirs(settings_dir, exist_ok=True)
            settings_path = os.path.join(settings_dir, "quick_materials_settings.json")
            
            # Load existing settings or create new structure
            if os.path.exists(settings_path):
                with open(settings_path, "r") as f:
                    all_settings = json.load(f)
            else:
                all_settings = {
                    'material_creator': {},
                    'material_list': {},
                    'texture_importer': {}
                }
            
            # Update texture importer section
            all_settings['texture_importer'] = data
            
            # Save material creator attribute frame visibility settings
            if not 'material_creator' in all_settings:
                all_settings['material_creator'] = {}
            
            # Save attribute frame visibility checkbox states
            if hasattr(self, '_attribute_checkbox_to_frame'):
                for checkbox_name, frame_name in self._attribute_checkbox_to_frame.items():
                    cb = self.ui_elements.get(checkbox_name)
                    if cb:
                        setting_key = f"attribute_frame_visible_{frame_name}"
                        all_settings['material_creator'][setting_key] = cb.isChecked()
            
            # Save material naming prefix/suffix
            try:
                prefix_text = self.ui_elements.get("materialNamingPrefixLineEdit").text().strip() if self.ui_elements.get("materialNamingPrefixLineEdit") else ""
                suffix_text = self.ui_elements.get("materialNamingSuffixLineEdit").text().strip() if self.ui_elements.get("materialNamingSuffixLineEdit") else ""
                all_settings['material_creator']['name_prefix'] = prefix_text
                all_settings['material_creator']['name_suffix'] = suffix_text
            except Exception as _e:
                pass
            
            # Save back to file
            with open(settings_path, "w") as f:
                json.dump(all_settings, f, indent=2)
                
            # Show yellow notification instead of dialog
            cmds.inViewMessage(amg="<hl>✔ Quick Materials Settings Saved</hl>", pos="topCenter", fade=True)
            # Close the dialog after saving
            self.accept()
        except Exception as e:
            cmds.confirmDialog(title="Error", message=f"Failed to save settings: {e}", button=["OK"])

    def reload_from_disk(self):
        """Re-read JSON and re-apply to widgets (call before showing the window)."""
        self._apply_saved_settings()
        # Ensure custom path widgets are properly enabled/disabled after reloading settings
        self._update_custom_path_widgets()
    
    def open_texture_search_names_ui(self):
        """Launch the TextureSearchNamesUI from the Settings window."""
        TextureSearchNamesUI = texture_importer.TextureSearchNamesUI
        if not hasattr(self, "_texture_search_names_ui") or self._texture_search_names_ui is None:
            self._texture_search_names_ui = TextureSearchNamesUI(parent=self)
        self._texture_search_names_ui.show()
        self._texture_search_names_ui.raise_()

def load_ui():
    """Convenience function to display the dockable Quick Materials UI."""
    QuickMaterialsUI.show_ui()



class QuickMaterialsUI(MayaQWidgetDockableMixin, QtWidgets.QDialog):
    """Dockable UI for the Quick Materials tool."""

    # Store the current dockable instance
    quick_materials_ui_instance = None
    workspace_control_name = "QuickMaterialsWorkspaceControl"

    # --- Node type configuration for scalability ---
    NODE_TYPES = {
        'materials': {
            'show_checkbox': None,  # Always shown
            'filter_checkbox': None,
            'header_text': 'Shaders',  # Header text for materials section
            'header_color': '#ffffff',  # White header color
            'entry_color': '#444444',  # Default material color
            'selected_color': '#3e637a',  # Blue selection
            'supports_rename': True,
            'supports_buttons': True,
        },
        'file_textures': {
            'show_checkbox': 'showTexturesCheckbox',
            'filter_checkbox': 'texturesFilterCheckbox',
            'header_text': 'File Textures',
            'header_color': '#ffee99',  # Yellow
            'entry_color': '#4a4a3a',  # Yellowish tint
            'selected_color': '#6a6a3a',
            'supports_rename': False,  # File textures show filename, not renamable
            'supports_buttons': True,
        },
        'procedural_textures': {
            'show_checkbox': 'showProceduralTexturesCheckbox',
            'filter_checkbox': 'proceduralTexturesFilterCheckbox',
            'header_text': 'Procedural Textures',
            'header_color': '#e8e8e8',  # Brighter grey for header label
            'entry_color': '#412e52',  # Dimmer purple tint
            'selected_color': '#5a3d6a',
            'supports_rename': True,
            'supports_buttons': False,  # No buttons for procedural textures
        },
        'shading_groups': {
            'show_checkbox': 'showShadingGroupsCheckbox',
            'filter_checkbox': 'shadingGroupsFilterCheckbox',
            'header_text': 'Shading Groups',
            'header_color': '#6fa3d8',  # Blue
            'entry_color': '#2a3a4a',  # Blue tint
            'selected_color': '#3a4a6a',
            'supports_rename': True,
            'supports_buttons': False,
        },
        'utilities': {
            'show_checkbox': None,
            'filter_checkbox': None,
            'header_text': 'Utilities',
            'header_color': '#7fb8e8',  # Light blue
            'entry_color': '#2a3a4a',  # Dim light blue
            'selected_color': '#3a5a7a',  # Medium blue for selection
            'supports_rename': True,
            'supports_buttons': False,
        },
    }

    # Icon mapping for utility nodes - queried from Hypershade
    # Maps node type to the actual icon path Maya uses
    # Generated from Hypershade icon browser tool - all 143 utility nodes included
    # Nodes without specific icons use out_multmatrix.png (Maya's default utility icon)
    UTILITY_NODE_ICON_MAP = {
        'aiAbs': DEFAULT_UTILITY_ICON,
        'aiAdd': DEFAULT_UTILITY_ICON,
        'aiAtan': DEFAULT_UTILITY_ICON,
        'aiBlackbody': DEFAULT_UTILITY_ICON,
        'aiBump2d': DEFAULT_UTILITY_ICON,
        'aiBump3d': DEFAULT_UTILITY_ICON,
        'aiCache': DEFAULT_UTILITY_ICON,
        'aiCameraProjection': DEFAULT_UTILITY_ICON,
        'aiClamp': DEFAULT_UTILITY_ICON,
        'aiColorConvert': DEFAULT_UTILITY_ICON,
        'aiColorCorrect': DEFAULT_UTILITY_ICON,
        'aiColorJitter': DEFAULT_UTILITY_ICON,
        'aiColorToFloat': DEFAULT_UTILITY_ICON,
        'aiCompare': DEFAULT_UTILITY_ICON,
        'aiComplement': DEFAULT_UTILITY_ICON,
        'aiComplexIor': DEFAULT_UTILITY_ICON,
        'aiComposite': DEFAULT_UTILITY_ICON,
        'aiCross': DEFAULT_UTILITY_ICON,
        'aiDistance': DEFAULT_UTILITY_ICON,
        'aiDivide': DEFAULT_UTILITY_ICON,
        'aiDot': DEFAULT_UTILITY_ICON,
        'aiExp': DEFAULT_UTILITY_ICON,
        'aiFacingRatio': DEFAULT_UTILITY_ICON,
        'aiFloatToInt': DEFAULT_UTILITY_ICON,
        'aiFloatToMatrix': DEFAULT_UTILITY_ICON,
        'aiFloatToRgba': DEFAULT_UTILITY_ICON,
        'aiFraction': DEFAULT_UTILITY_ICON,
        'aiIsFinite': DEFAULT_UTILITY_ICON,
        'aiLayerFloat': DEFAULT_UTILITY_ICON,
        'aiLayerRgba': DEFAULT_UTILITY_ICON,
        'aiLength': DEFAULT_UTILITY_ICON,
        'aiLog': DEFAULT_UTILITY_ICON,
        'aiMatrixInterpolate': DEFAULT_UTILITY_ICON,
        'aiMatrixMultiplyVector': DEFAULT_UTILITY_ICON,
        'aiMatrixTransform': DEFAULT_UTILITY_ICON,
        'aiMax': DEFAULT_UTILITY_ICON,
        'aiMin': DEFAULT_UTILITY_ICON,
        'aiModulo': DEFAULT_UTILITY_ICON,
        'aiMotionVector': DEFAULT_UTILITY_ICON,
        'aiMultiply': DEFAULT_UTILITY_ICON,
        'aiNegate': DEFAULT_UTILITY_ICON,
        'aiNormalMap': DEFAULT_UTILITY_ICON,
        'aiNormalize': DEFAULT_UTILITY_ICON,
        'aiOslShader': DEFAULT_UTILITY_ICON,
        'aiPow': DEFAULT_UTILITY_ICON,
        'aiRampFloat': DEFAULT_UTILITY_ICON,
        'aiRampRgb': DEFAULT_UTILITY_ICON,
        'aiRandom': DEFAULT_UTILITY_ICON,
        'aiRange': DEFAULT_UTILITY_ICON,
        'aiReadFloat': DEFAULT_UTILITY_ICON,
        'aiReadInt': DEFAULT_UTILITY_ICON,
        'aiReadRGB': DEFAULT_UTILITY_ICON,
        'aiReadRGBA': DEFAULT_UTILITY_ICON,
        'aiReciprocal': DEFAULT_UTILITY_ICON,
        'aiRgbToVector': DEFAULT_UTILITY_ICON,
        'aiRgbaToFloat': DEFAULT_UTILITY_ICON,
        'aiRoundCorners': DEFAULT_UTILITY_ICON,
        'aiShuffle': DEFAULT_UTILITY_ICON,
        'aiSign': DEFAULT_UTILITY_ICON,
        'aiSpaceTransform': DEFAULT_UTILITY_ICON,
        'aiSqrt': DEFAULT_UTILITY_ICON,
        'aiStateFloat': DEFAULT_UTILITY_ICON,
        'aiStateInt': DEFAULT_UTILITY_ICON,
        'aiStateVector': DEFAULT_UTILITY_ICON,
        'aiSubtract': DEFAULT_UTILITY_ICON,
        'aiTraceSet': DEFAULT_UTILITY_ICON,
        'aiTrigo': DEFAULT_UTILITY_ICON,
        'aiUserDataColor': DEFAULT_UTILITY_ICON,
        'aiUserDataFloat': DEFAULT_UTILITY_ICON,
        'aiUserDataInt': DEFAULT_UTILITY_ICON,
        'aiUserDataString': DEFAULT_UTILITY_ICON,
        'aiUvProjection': DEFAULT_UTILITY_ICON,
        'aiUvTransform': DEFAULT_UTILITY_ICON,
        'aiVectorMap': DEFAULT_UTILITY_ICON,
        'aiVectorToRgb': DEFAULT_UTILITY_ICON,
        'aiVolumeSampleFloat': DEFAULT_UTILITY_ICON,
        'aiVolumeSampleRgb': DEFAULT_UTILITY_ICON,
        'aiWriteColor': DEFAULT_UTILITY_ICON,
        'aiWriteFloat': DEFAULT_UTILITY_ICON,
        'aiWriteInt': DEFAULT_UTILITY_ICON,
        'aiWriteRgba': DEFAULT_UTILITY_ICON,
        'aiWriteVector': DEFAULT_UTILITY_ICON,
        'arrayMapper': ':/nodeIcons/arrayMapper.png',
        'blendColors': ':/nodeIcons/blendColors.png',
        'blendDevice': DEFAULT_UTILITY_ICON,
        'blendTwoAttr': ':/nodeIcons/blendTwoAttr.png',
        'bump2d': ':/nodeIcons/bump2d.png',
        'bump3d': ':/nodeIcons/bump3d.png',
        'channels': ':/nodeIcons/channels.png',
        'choice': ':/nodeIcons/choice.png',
        'chooser': ':/nodeIcons/chooser.png',
        'clamp': ':/nodeIcons/clamp.png',
        'colorComposite': DEFAULT_UTILITY_ICON,
        'colorCondition': DEFAULT_UTILITY_ICON,
        'colorConstant': DEFAULT_UTILITY_ICON,
        'colorCorrect': DEFAULT_UTILITY_ICON,
        'colorLogic': DEFAULT_UTILITY_ICON,
        'colorMask': DEFAULT_UTILITY_ICON,
        'colorMath': DEFAULT_UTILITY_ICON,
        'colorProfile': ':/nodeIcons/colorProfile.png',
        'composeMatrix': ':/nodeIcons/composeMatrix.png',
        'condition': ':/nodeIcons/condition.png',
        'contrast': ':/nodeIcons/contrast.png',
        'cpvColor': DEFAULT_UTILITY_ICON,
        'cryptomatte': DEFAULT_UTILITY_ICON,
        'curveInfo': ':/nodeIcons/curveInfo.png',
        'decomposeMatrix': ':/nodeIcons/decomposeMatrix.png',
        'distanceBetween': ':/nodeIcons/distanceBetween.png',
        'distanceDimShape': ':/nodeIcons/distanceDimShape.png',
        'doubleShadingSwitch': ':/nodeIcons/doubleShadingSwitch.png',
        'floatComposite': DEFAULT_UTILITY_ICON,
        'floatCondition': DEFAULT_UTILITY_ICON,
        'floatConstant': DEFAULT_UTILITY_ICON,
        'floatCorrect': DEFAULT_UTILITY_ICON,
        'floatLogic': DEFAULT_UTILITY_ICON,
        'floatMask': DEFAULT_UTILITY_ICON,
        'floatMath': DEFAULT_UTILITY_ICON,
        'fourByFourMatrix': ':/nodeIcons/fourByFourMatrix.png',
        'frameCache': ':/nodeIcons/frameCache.png',
        'frameExtension': DEFAULT_UTILITY_ICON,
        'gammaCorrect': ':/nodeIcons/gammaCorrect.png',
        'heightField': ':/nodeIcons/heightField.png',
        'hsvToRgb': ':/nodeIcons/hsvToRgb.png',
        'lightInfo': ':/nodeIcons/lightInfo.png',
        'luminance': ':/nodeIcons/luminance.png',
        'multDoubleLinear': ':/nodeIcons/multDoubleLinear.png',
        'multiplyDivide': ':/nodeIcons/multiplyDivide.png',
        'offset': ':/nodeIcons/offset.png',
        'particleSamplerInfo': ':/nodeIcons/particleSamplerInfo.png',
        'place2dTexture': ':/nodeIcons/place2dTexture.png',
        'place3dTexture': ':/nodeIcons/place3dTexture.png',
        'plusMinusAverage': ':/nodeIcons/plusMinusAverage.png',
        'premultiply': DEFAULT_UTILITY_ICON,
        'projection': ':/nodeIcons/projection.png',
        'quadShadingSwitch': ':/nodeIcons/quadShadingSwitch.png',
        'remapColor': ':/nodeIcons/remapColor.png',
        'remapHsv': ':/nodeIcons/remapHsv.png',
        'remapRgb': DEFAULT_UTILITY_ICON,
        'remapValue': ':/nodeIcons/remapValue.png',
        'remapVector': DEFAULT_UTILITY_ICON,
        'reverse': ':/nodeIcons/reverse.png',
        'rgbToHsv': ':/nodeIcons/rgbToHsv.png',
        'samplerInfo': ':/nodeIcons/samplerInfo.png',
        'setRange': ':/nodeIcons/setRange.png',
        'singleShadingSwitch': ':/nodeIcons/singleShadingSwitch.png',
        'stencil': ':/nodeIcons/stencil.png',
        'surfaceInfo': ':/nodeIcons/surfaceInfo.png',
        'surfaceLuminance': ':/nodeIcons/surfaceLuminance.png',
        'transposeMatrix': ':/nodeIcons/transposeMatrix.png',
        'tripleShadingSwitch': ':/nodeIcons/tripleShadingSwitch.png',
        'unitConversion': ':/nodeIcons/unitConversion.png',
        'unpremultiply': DEFAULT_UTILITY_ICON,
        'uvChooser': ':/nodeIcons/uvChooser.png',
        'vectorProduct': ':/nodeIcons/vectorProduct.png',
        'xgmHairMapping': DEFAULT_UTILITY_ICON,
        'xgmSeExpr': DEFAULT_UTILITY_ICON,
    }
    
    # Complete list of all utility node types (143 nodes total)
    # Generated from the icon map to ensure we include all nodes that appear in Hypershade
    UTILITY_NODE_TYPES = tuple(sorted(UTILITY_NODE_ICON_MAP.keys()))

    # --- filters for the material list (id, button objectName, chip label, chip visibility, exclusivity group) ---
    MATERIAL_FILTERS = [
        # Selected filter (standalone, no exclusivity)
        {"id": "selected",      "button": "selectedFilterButton",      "label": "Selected",  "chip": True,  "group": None},
        
        # Used/Unused pair (mutually exclusive)
        {"id": "used",          "button": "usedFilterButton",              "label": "Used",           "chip": True,  "group": "used_state"},
        {"id": "unUsed",        "button": "unusedFilterButton",            "label": "Unused",         "chip": True,  "group": "used_state"},

        # Referenced/Non-Referenced pair (mutually exclusive)
        {"id": "referenced",    "button": "referencedFilterButton",        "label": "Referenced",     "chip": True,  "group": "reference_state"},
        {"id": "nonReferenced", "button": "nonReferencedFilterButton",     "label": "Non-Referenced", "chip": True,  "group": "reference_state"},

        # Standalone
        {"id": "hideDefaults",          "checkbox": "hideDefaultMaterialsCheckbox",       "label": "Hide Defaults",         "chip": False, "group": None},
        
        # Utility filter removed - utilities now always show only those connected to shaders
        
        # Note: Node type filters (fileTextures, proceduralTextures, shadingGroups, utilities) are now handled
        # by tab buttons (materialListShadersButton, materialListTexturesButton, materialListShadingGroupButton, materialListUtilitiesButton)
        # instead of checkboxes in the options menu.
    ]

    MATERIAL_TABS = {
        'shaders': {
            'button': 'materialListShadersButton',
            'frame': 'shaderListFrame',
            'scroll': 'shaderListScrollArea',
            'header_frame': 'shadersHeaderLabelFrame',
        },
        'textures': {
            'button': 'materialListTexturesButton',
            'frame': 'textureListFrame',
            'scroll': 'textureListScrollArea',
            'header_frame': 'texturesHeaderLabelFrame',
        },
        'shading_groups': {
            'button': 'materialListShadingGroupButton',
            'frame': 'shadingGroupsListFrame',
            'scroll': 'shadingGroupsListScrollArea',
            'header_frame': 'shadingGroupsHeaderLabelFrame',
        },
        'utilities': {
            'button': 'materialListUtilitiesButton',
            'frame': 'utilitiesListFrame',
            'scroll': 'utilitiesListScrollArea',
            'header_frame': 'utilitiesHeaderLabelFrame',
        },
    }

    def _filter_registered_node_types(self, node_types):
        """
        Return only node types that Maya currently has registered.
        Prevents warnings when plugins that define certain nodes are not loaded.
        """
        valid = set()
        for node_type in node_types:
            try:
                if cmds.nodeType(node_type, isTypeName=True):
                    valid.add(node_type)
            except Exception:
                continue
        return valid


    def _get_tab_frame(self, tab_type):
        """
        Convenience helper to find the outer frame for a tab.
        Returns the QWidget reference or None if not found.
        """
        cfg = self.MATERIAL_TABS.get(tab_type)
        if not cfg:
            return None
        return self._get_widget(cfg['frame'], QtWidgets.QWidget)

    def _get_tab_scroll_area(self, tab_type):
        """
        Convenience helper to find the scroll area for a tab.
        Returns the QScrollArea reference or None if not found.
        """
        cfg = self.MATERIAL_TABS.get(tab_type)
        if not cfg:
            return None
        return self._get_widget(cfg['scroll'], QtWidgets.QScrollArea)

    def _update_tab_frames_visibility(self, active_tab):
        """
        Show only the active tab frame; hide the others.
        """
        if not active_tab:
            return
        for tab_type, cfg in self.MATERIAL_TABS.items():
            frame = self._get_widget(cfg.get('frame'), QtWidgets.QWidget)
            if not frame:
                continue
            frame.setVisible(tab_type == active_tab)
        self._current_active_tab = active_tab
        self._update_tab_specific_filters(active_tab)

    def _update_tab_specific_filters(self, active_tab):
        """Show/hide tab-specific filters."""
        is_utilities = (active_tab == 'utilities')

        # Hide utility filter button/frame (removed - utilities always show only those connected to shaders)
        utility_filter_frame = self._get_widget('shaderUtilitiesFilterFrame', QtWidgets.QFrame)
        if utility_filter_frame:
            utility_filter_frame.setVisible(False)

        utility_filter_btn = self._get_widget('shaderUtilitiesFilterButton', QtWidgets.QPushButton)
        if utility_filter_btn:
            utility_filter_btn.setVisible(False)

        # Show Selected / Used / Unused filters for utilities tab (they work based on connected shaders)
        for frame_name in ('selectedFilterFrame', 'usedFilterFrame'):
            frame = self._get_widget(frame_name, QtWidgets.QFrame)
            if frame:
                frame.setVisible(True)  # Always visible, including utilities tab

        for btn_name in ('selectedFilterButton', 'usedFilterButton', 'unusedFilterButton'):
            btn = self._get_widget(btn_name, QtWidgets.QPushButton)
            if btn:
                btn.setVisible(True)  # Always visible, including utilities tab

    def _restore_scroll_position(self, scroll_area, position):
        """Restore the scroll position of a scroll area."""
        if not scroll_area:
            return
        try:
            # PySide widgets become dangling pointers after deletion; guard with shiboken.isValid
            if not isValid(scroll_area):
                return
        except Exception:
            # If shiboken isn't available, optimistically continue but still guard RuntimeErrors below
            pass
        try:
            widget = scroll_area.widget()
        except RuntimeError:
            return
        if widget:
            try:
                v_scrollbar = scroll_area.verticalScrollBar()
            except RuntimeError:
                return
            if v_scrollbar:
                v_scrollbar.setValue(position)
    
    def _configure_materials_scroll_area(self, scroll_area):
        """
        Apply consistent policies/styling to a tab's scroll area.
        """
        if not scroll_area:
            return
        try:
            scroll_area.setWidgetResizable(True)
            scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            scroll_area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
            scroll_area.setFrameShape(QtWidgets.QFrame.NoFrame)
        except Exception:
            pass

    def _clear_tab_scroll_area(self, scroll_area):
        """
        Remove and delete the current widget inside a tab's scroll area.
        """
        if not scroll_area:
            return
        try:
            existing = scroll_area.takeWidget()
        except Exception:
            existing = None
        if existing:
            try:
                existing.setParent(None)
                existing.deleteLater()
            except Exception:
                pass


    # --- Small helpers over the spec ---
    def _filter_spec(self):
        return list(self.MATERIAL_FILTERS)
    
    def _update_filter_checkbox_states(self):
        """
        Legacy function - filter checkboxes have been replaced by tab buttons.
        This function is kept for compatibility but no longer does anything.
        Tab buttons (materialListShadersButton, materialListTexturesButton, materialListShadingGroupButton, materialListUtilitiesButton)
        now handle filtering directly.
        """
        # No-op: filter checkboxes have been removed in favor of tab buttons
        pass

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
        self._sort_state_by_tab = {
            tab: {'mode': self._sort_mode, 'desc': self._sort_desc}
            for tab in self.MATERIAL_TABS
        }

        # Cached Maya node types per classification (avoids repeated cmds.listNodeTypes calls)
        self._node_types_by_classification = {}
        self._procedural_texture_types = None

        # --- Silent refresh guards (used during in-place rename) ---
        self._suspend_refresh_count = 0
        self._mute_poll_until_ts = 0.0

        # --- One-shot sort freeze for rename (prevents jump while editing under 'Name' sort) ---
        self._freeze_name_sort_once = False

        # --- Scene material snapshot (poll fallback in case host events miss) ---
        self._last_materials_snapshot = set()  # names at last poll

        # Initialize selected_color early (needed for state loading)
        self.selected_color = QtGui.QColor("#ff0000")  # Default red color
        
        # List buttons removed - no longer needed
        self._last_refresh_request_ts = 0.0
        
        # Flag to prevent auto-save during state loading
        self._loading_state = False

        self.setObjectName("QuickMaterialsUI")  # ensure a stable name for parenting scriptJobs

        self.import_tx_tool = None
        # Store all UI elements in a dictionary
        self.ui_elements = LiveWidgetDict(self)
        
        # Initialize state management
        self.state_file_path = self._get_state_file_path()
        
        # Initialize debounced save timer
        self._save_timer = QtCore.QTimer()

        # Layout debug logging toggle (disable noisy size prints by default)
        self._layout_debug_enabled = False
        
        # PERFORMANCE OPTIMIZATION: Initialize caching system
        self._material_cache = {}
        self._cache_timestamp = 0
        self._cache_timeout = 2.0  # Cache expires after 2 seconds
        self._minimum_width_baseline = 300
        
        # Cache for file texture display info (expensive file system operations)
        self._file_texture_info_cache = {}  # {node_name: {info_dict, timestamp}}
        self._file_texture_cache_timeout = 5.0  # Cache for 5 seconds
        
        # Cache for node type classifications (avoid repeated cmds.nodeType calls)
        self._node_type_classification_cache = {}  # {node_name: classification}
        
        # Track material list state to detect changes
        self._last_material_list_hash = None  # Hash of material list to detect changes
        self._last_filter_state_hash = None  # Hash of filter/search state
        
        # Debug flag for tracking refresh triggers (set to True to enable debug logging)
        self._debug_refresh_triggers = False
        
        # PERFORMANCE OPTIMIZATION: Deferred icon creation queue
        self._pending_icon_creations = []  # List of icon creation requests to process after UI is built
        
        # List entry scaling state (0=small, 1=medium, 2=large)
        # Will be loaded from settings in _load_ui_state, default to 0
        self._list_entry_scale_level = 0
        self._list_entry_scale_sizes = {
            0: {'icon': 20, 'font': 11, 'spacing': 2, 'container_padding': 0, 'layout_margin': (0, 0, 0, 0)},  # Small (current default)
            1: {'icon': 28, 'font': 12, 'spacing': 2, 'container_padding': 0, 'layout_margin': (0, 0, 0, 0)},  # Medium (reduced margins)
            2: {'icon': 36, 'font': 13, 'spacing': 2, 'container_padding': 0, 'layout_margin': (0, 0, 0, 0)}   # Large (reduced margins)
        }
        
        # PERFORMANCE OPTIMIZATION: Debounced refresh timer
        self._refresh_timer = QtCore.QTimer()
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._perform_actual_refresh)
        self._refresh_delay_ms = 150  # Refresh after 150ms of inactivity
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._save_ui_state_immediate)
        self._save_delay_ms = 500  # Save after 500ms of inactivity
        # Embedded settings state (material creator / texture importer)
        self._settings_cache = None
        self._settings_cache_dirty = False
        self._settings_save_timer = QtCore.QTimer(self)
        self._settings_save_timer.setSingleShot(True)
        self._settings_save_timer.timeout.connect(lambda: self._flush_settings_cache_to_disk())
        self._settings_save_delay_ms = 400
        self._material_creator_settings = {}
        self._texture_importer_settings = {}
        self._attribute_checkbox_to_frame = {}
        self._attribute_visibility_overrides = {}
        self._material_name_prefix = None
        self._material_name_suffix = None
        self._texture_mode_checkboxes = {}
        self._texture_search_names_ui = None
        self._updating_texture_mode = False
        self._settings_frame_toggles = {}
        self._material_row_pool = []
        self._initial_populate_done = False
        
        # Track tab-specific UI state
        self._tab_entry_state = {
            tab: {'entries': [], 'index': {}}
            for tab in self.MATERIAL_TABS
        }
        self._tab_button_to_type = {
            cfg['button']: tab for tab, cfg in self.MATERIAL_TABS.items()
        }
        self._current_active_tab = None  # Track which tab is currently visible
        self._utilities_tab_populated = False  # Lazy loading flag for utilities tab
        self._utilities_cache = None  # Cache for collected utility nodes
        # Utilities are now always filtered to only show those connected to shaders
        try:
            self.destroyed.connect(self._remove_workspace_state_job)
        except Exception:
            pass
        self._workspace_state_job_id = None
        
        # Set loading flag early to prevent auto-save during initialization
        self._loading_state = True
        
        self.initialize_ui()
        
        # Load UI state after UI is initialized
        # Load state immediately instead of using QTimer
        self._load_ui_state()
        
        # Update sort buttons after state is loaded
        self._update_sort_buttons_after_state_load()
        
        # Initialize filter checkbox states based on show checkboxes
        self._update_filter_checkbox_states()



    # ------------------------------------------------------------------
    # Docking utilities
    # ------------------------------------------------------------------
    @classmethod
    def show_ui(cls, dockable=True):
        """Display the UI as a dockable widget inside Maya."""
        global quick_materials_ui_instance

        # Check if workspace control already exists (from saved workspace)
        if cmds.workspaceControl(cls.workspace_control_name, query=True, exists=True):
            # Check if widget is already attached to the workspace control
            control_widget = omui.MQtUtil.findControl(cls.workspace_control_name)
            if control_widget:
                wrapped_widget = wrapInstance(int(control_widget), QtWidgets.QWidget)
                layout = wrapped_widget.layout()
                if layout:
                    # Check if our widget is already there
                    for i in range(layout.count()):
                        item = layout.itemAt(i)
                        if item and item.widget() and isinstance(item.widget(), cls):
                            # Widget already attached, just make visible
                            cls.quick_materials_ui_instance = item.widget()
                            quick_materials_ui_instance = cls.quick_materials_ui_instance
                            cmds.workspaceControl(cls.workspace_control_name, edit=True, visible=True)
                            return
            # Workspace control exists but widget not attached - clean up and recreate
            cls.delete_existing_instance()

        # Create new instance
        cls.quick_materials_ui_instance = cls()
        quick_materials_ui_instance = cls.quick_materials_ui_instance
        cls.quick_materials_ui_instance.setup_dockability()

    @classmethod
    def workspace_ui_script(cls):
        """Return the Python command Maya should run to restore this panel."""
        # Some hosts (notably certain Maya builds) execute uiScript as Python, not MEL.
        # Use maya.utils.executeDeferred directly to avoid relying on MEL evalDeferred.
        # Wrap execution in try-except to handle any potential evalDeferred NameError issues.
        # This script will work whether Maya executes it as Python or MEL (via python() command)
        # NOTE: Python try/except requires newlines, not semicolons
        # Maya's uiScript can handle multi-line Python strings
        return (
            "import maya.utils as _mutils\n"
            "import QuickMaterials.quick_materials as _qm\n"
            "try:\n"
            "    _mutils.executeDeferred(_qm.QuickMaterialsUI.restore_from_workspace)\n"
            "except:\n"
            "    pass\n"
        )

    @classmethod
    def restore_from_workspace(cls):
        """Rebuild the UI when Maya restores a workspace layout."""
        try:
            import maya.cmds as cmds
            import maya.utils as mutils
            import maya.OpenMayaUI as omui
        except ImportError:
            return

        if not cmds.workspaceControl(cls.workspace_control_name, exists=True):
            return

        # Use executeDeferred to ensure Maya UI is fully ready
        def _do_restore():
            try:
                # Remove any existing instance so we can reattach cleanly
                if cls.quick_materials_ui_instance:
                    try:
                        cls.delete_existing_instance()
                    except Exception:
                        pass

                # Create and attach a new instance
                instance = cls()
                cls.quick_materials_ui_instance = instance
                
                # Attach widget to existing workspace control
                control_widget = omui.MQtUtil.findControl(cls.workspace_control_name)
                if control_widget:
                    wrapped_widget = wrapInstance(int(control_widget), QtWidgets.QWidget)
                    layout = wrapped_widget.layout() or QtWidgets.QVBoxLayout(wrapped_widget)
                    layout.setContentsMargins(0, 0, 0, 0)
                    wrapped_widget.setLayout(layout)

                    while layout.count():
                        child = layout.takeAt(0)
                        if child.widget():
                            child.widget().deleteLater()

                    layout.addWidget(instance)
                    wrapped_widget.setVisible(True)
                    wrapped_widget.update()
                    instance.setMinimumWidth(instance._minimum_width_baseline)
                    instance.resize(max(instance._minimum_width_baseline, instance.width()), instance.height())
                    instance.show()
                    QtCore.QTimer.singleShot(0, instance.snap_to_minimum)
                    QtCore.QTimer.singleShot(0, instance._apply_minimum_width_baseline)
                    instance._install_workspace_state_job()
            except Exception as e:
                import traceback
                traceback.print_exc()
                print(f"[QuickMaterials] Error restoring from workspace: {e}")
        
        mutils.executeDeferred(_do_restore)

    def setup_dockability(self):
        """Dock the window into a Maya workspace control."""
        # Get the MEL uiScript command (already properly formatted)
        ui_script_cmd = type(self).workspace_ui_script()
        
        if not cmds.workspaceControl(self.workspace_control_name, query=True, exists=True):
            # Create new workspace control with uiScript
            # Start as floating, retain=False (won't save to workspace until docked)
            cmds.workspaceControl(
                self.workspace_control_name,
                label="Quick Materials",
                retain=False,  # Don't save to workspace until docked
                floating=True,
                uiScript=ui_script_cmd,
            )
        else:
            # Control exists (from saved workspace) - ensure uiScript is set
            try:
                # Try to set uiScript on existing control
                cmds.workspaceControl(
                    self.workspace_control_name,
                    edit=True,
                    uiScript=ui_script_cmd,
                )
                # Update retain based on current docking state
                self._update_retain_state()
            except Exception:
                # If setting uiScript fails, that's okay - it might already be set
                pass

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

            # Ensure a reasonable initial size when docked/floating
            self.setMinimumWidth(self._minimum_width_baseline)
            self.resize(max(self._minimum_width_baseline, self.width()), self.height())
            self.show()
            # Defer snapping so the workspaceControl has fully realized its layout
            QtCore.QTimer.singleShot(0, self.snap_to_minimum)
            QtCore.QTimer.singleShot(0, self._apply_minimum_width_baseline)


        cmds.workspaceControl(self.workspace_control_name, edit=True, visible=True)
        # Update retain state based on current docking state
        self._update_retain_state()
        self._install_workspace_state_job()

    @classmethod
    def delete_existing_instance(cls):
        """Close and clean up any existing dock or window instance."""
        global quick_materials_ui_instance
        
        # proactively remove watchers if an instance exists
        if cls.quick_materials_ui_instance:
            try:
                cls.quick_materials_ui_instance._remove_material_watchers()
            except Exception as e:
                pass
            try:
                cls.quick_materials_ui_instance._remove_selection_watcher()
            except Exception as e:
                pass
            try:
                cls.quick_materials_ui_instance._remove_workspace_state_job()
            except Exception as e:
                pass
            
            # Disconnect all Qt signals to prevent callbacks on deleted objects
            try:
                cls.quick_materials_ui_instance.destroyed.disconnect()
            except Exception:
                pass
            
            # Close any child dialogs/tools
            try:
                if hasattr(cls.quick_materials_ui_instance, 'import_tx_tool') and cls.quick_materials_ui_instance.import_tx_tool:
                    cls.quick_materials_ui_instance.import_tx_tool.close()
                    cls.quick_materials_ui_instance.import_tx_tool.deleteLater()
                    cls.quick_materials_ui_instance.import_tx_tool = None
            except Exception as e:
                pass
            
            # Clear any cached data that might hold references
            try:
                if hasattr(cls.quick_materials_ui_instance, '_material_cache'):
                    cls.quick_materials_ui_instance._material_cache.clear()
                if hasattr(cls.quick_materials_ui_instance, '_file_texture_info_cache'):
                    cls.quick_materials_ui_instance._file_texture_info_cache.clear()
                if hasattr(cls.quick_materials_ui_instance, '_entry_list'):
                    cls.quick_materials_ui_instance._entry_list = []
                if hasattr(cls.quick_materials_ui_instance, '_index_by_material'):
                    cls.quick_materials_ui_instance._index_by_material.clear()
            except Exception as e:
                pass

        # Remove workspace control
        if cmds.workspaceControl(cls.workspace_control_name, query=True, exists=True):
            try:
                cmds.deleteUI(cls.workspace_control_name, control=True)
            except Exception as e:
                pass

        # Remove window if it exists
        if cmds.window(cls.workspace_control_name, exists=True):
            try:
                cmds.deleteUI(cls.workspace_control_name, window=True)
            except Exception as e:
                pass

        # Close and delete the instance
        if cls.quick_materials_ui_instance:
            try:
                cls.quick_materials_ui_instance.close()
            except Exception as e:
                pass
            try:
                cls.quick_materials_ui_instance.deleteLater()
            except Exception as e:
                pass
            cls.quick_materials_ui_instance = None

        quick_materials_ui_instance = None

        # Force garbage collection
        import gc
        gc.collect()

# Initialize UI
    def initialize_ui(self):
        # Base stylesheet for the color display button, with a placeholder for dynamic color
        self.base_stylesheet = base_stylesheet
        self.material_list_widget_style = material_list_widget_style
        self.material_filters_button_style = material_filters_button_style
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

        # Change current directory to the UI file's folder for loader
        QtCore.QDir.setCurrent(os.path.dirname(uiFilePath))

        loader = QtUiTools.QUiLoader()
        uiFile = QtCore.QFile(uiFilePath)

        try:
            # Open the .ui file for reading
            uiFile.open(QtCore.QFile.ReadOnly)

            # Load the .ui file
            loaded_ui = loader.load(uiFile)

            # Close the .ui file now that it's loaded
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
            pass

         # Set up stretch factors and spacer widget for layout scaling
        self.setup_layout_stretches()

        # Always use the dialog (`self`) – the UI's top-level widget may be
        # re-parented and become invalid once docked.
        self.ui_elements['quickMaterialsWindow'] = self



        # Wire up all button/slider/checkbox signals to their respective slots
        self.setup_connections()
        # Configure embedded settings sections (material creator + texture importer)
        self._initialize_embedded_settings_sections()

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

        # Configure the new attribute sliders and spinboxes
        self.setup_emission_slider()
        self.setup_opacity_slider()
        self.setup_transmission_slider()
        self.setup_subsurface_slider()

        # Ensure the attribute frames reflect the current material type at startup
        self.update_material_attr_visibility()

        # --- Sorting bar above the list (sticky toolbar) ---
        self._install_sort_bar()

        # Selection sync state
        self._sel_watcher_id = None

        self._syncing_selection = False  # guard to avoid feedback loops

        # Start listening to Maya selection changes
        self._install_selection_watcher()

        # --- NEW: hide Material List Options by default ---
        options_frame = self.findChild(QtWidgets.QWidget, 'materialListSettingsFrame')

        if options_frame:
            options_frame.setVisible(False)
        # keep the toggle button untoggled (text handled by Qt Designer)
        options_btn = self.findChild(QtWidgets.QPushButton, 'materialListSettingsButton')
        if options_btn:
            try:
                options_btn.setChecked(False)
            except Exception:
                pass

        # --- NEW: hide Material Filters by default ---
        filters_frame = self.findChild(QtWidgets.QWidget, 'materialListFiltersFrame')
        if filters_frame:
            filters_frame.setVisible(False)
        filters_btn = self.ui_elements.get('materialFiltersButton')
        if filters_btn:
            try:
                filters_btn.setChecked(False)
            except Exception:
                pass


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
        try:
            self.setMinimumWidth(self._minimum_width_baseline)
            self.resize(max(self._minimum_width_baseline, self.width()), self.height())
        except Exception:
            pass
        self._apply_minimum_width_baseline()

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

    # ---------- Embedded settings (material creator + texture importer) ----------
    def _initialize_embedded_settings_sections(self):
        """
        Wire the new in-panel settings frames for material creator + texture importer.
        """
        self._settings_frame_toggles.clear()
        settings = self._load_settings_cache()
        self._material_creator_settings = settings.setdefault('material_creator', {})
        self._texture_importer_settings = settings.setdefault('texture_importer', {})

        # Cache overrides + affixes up front so other routines can reuse them without disk hits
        self._attribute_checkbox_to_frame = {
            'colorAttributeFrameCheckbox': 'colorPickerFrame',
            'roughnesAttributeFrameCheckbox': 'roughnessSliderFrame',
            'metalnessAttributeFrameCheckbox': 'metalnessSliderFrame',
            'emissionAttributeFrameCheckbox': 'emissionSliderFrame',
            'opacityAttributeFrameCheckbox': 'opacitySliderFrame',
            'transmissionAttributeFrameCheckbox': 'transmissionSliderFrame',
            'subsurfaceAttributeFrameCheckbox': 'subsurfaceSliderFrame'
        }
        self._attribute_visibility_overrides = {}
        for frame_name in self._attribute_checkbox_to_frame.values():
            key = f'attribute_frame_visible_{frame_name}'
            if key in self._material_creator_settings:
                self._attribute_visibility_overrides[frame_name] = bool(self._material_creator_settings[key])

        self._material_name_prefix = self._material_creator_settings.get('name_prefix', 'M_') or ''
        self._material_name_suffix = self._material_creator_settings.get('name_suffix', '') or ''

        # Ensure texture importer defaults exist for downstream reads
        self._texture_importer_settings.setdefault('default_mode', 'maya_file')
        self._texture_importer_settings.setdefault('custom_path', '')
        self._texture_importer_settings.setdefault('create_if_doesnt_exist', False)

        self._setup_material_creator_settings_section()
        self._setup_texture_importer_settings_section()

    def _settings_file_path(self):
        return os.path.join(os.path.dirname(__file__), "settings", "quick_materials_settings.json")

    def _load_settings_cache(self):
        if self._settings_cache is not None:
            return self._settings_cache

        data = {}
        path = self._settings_file_path()
        try:
            with open(path, "r") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    data = loaded
        except FileNotFoundError:
            data = {}
        except Exception as exc:
            print(f"[QuickMaterials] Warning: failed to read settings ({path}): {exc}")
            data = {}

        if not isinstance(data, dict):
            data = {}

        data.setdefault('material_creator', {})
        data.setdefault('material_list', {})
        data.setdefault('texture_importer', {})
        self._settings_cache = data
        return self._settings_cache

    def _mark_settings_dirty(self):
        self._settings_cache_dirty = True
        if self._settings_save_timer:
            self._settings_save_timer.start(self._settings_save_delay_ms)
        else:
            self._flush_settings_cache_to_disk()

    def _flush_settings_cache_to_disk(self, force=False):
        if not self._settings_cache:
            return
        if not force and not self._settings_cache_dirty:
            return

        path = self._settings_file_path()
        settings_dir = os.path.dirname(path)
        try:
            os.makedirs(settings_dir, exist_ok=True)
            with open(path, "w") as f:
                json.dump(self._settings_cache, f, indent=2)
            self._settings_cache_dirty = False
        except Exception as exc:
            print(f"[QuickMaterials] Warning: failed to write settings ({path}): {exc}")

    def _setup_material_creator_settings_section(self):
        """Wire toggle button, affix edits, and attribute checkboxes."""
        self._wire_settings_frame_toggle(
            button_name='materialCreatorSettingsButton',
            frame_name='materialCreatorSettingsFrame',
            default_visible=True
        )

        prefix_edit = self.ui_elements.get('materialNamingPrefixLineEdit')
        if prefix_edit:
            prefix_edit.blockSignals(True)
            prefix_edit.setText(self._material_name_prefix or '')
            prefix_edit.blockSignals(False)
            prefix_edit.textChanged.connect(lambda text: self._on_material_affix_changed('prefix', text))

        suffix_edit = self.ui_elements.get('materialNamingSuffixLineEdit')
        if suffix_edit:
            suffix_edit.blockSignals(True)
            suffix_edit.setText(self._material_name_suffix or '')
            suffix_edit.blockSignals(False)
            suffix_edit.textChanged.connect(lambda text: self._on_material_affix_changed('suffix', text))

        for checkbox_name, frame_name in self._attribute_checkbox_to_frame.items():
            cb = self.ui_elements.get(checkbox_name)
            if not cb:
                continue
            target_state = self._attribute_visibility_overrides.get(frame_name)
            if target_state is None:
                target_state = cb.isChecked()
                self._attribute_visibility_overrides[frame_name] = bool(target_state)
            cb.blockSignals(True)
            cb.setChecked(bool(target_state))
            cb.blockSignals(False)
            cb.toggled.connect(lambda checked, frame=frame_name: self._on_attribute_checkbox_toggled(frame, checked))
            self._apply_attribute_checkbox_state(frame_name, bool(target_state), invoke_snap=False)

    def _wire_settings_frame_toggle(self, button_name, frame_name, default_visible=None):
        btn = self.ui_elements.get(button_name)
        frame = self.ui_elements.get(frame_name)
        if not btn or not frame:
            return
        try:
            btn.setCheckable(True)
        except Exception:
            pass

        if default_visible is None:
            initial_checked = btn.isChecked() if btn.isCheckable() else False
        else:
            initial_checked = bool(default_visible)
            if btn.isCheckable():
                blocker_cls = getattr(QtCore, "QSignalBlocker", None)
                blocker = blocker_cls(btn) if blocker_cls else None
                if blocker is None:
                    btn.blockSignals(True)
                btn.setChecked(initial_checked)
                if blocker_cls:
                    del blocker
                else:
                    btn.blockSignals(False)

        self._settings_frame_toggles[button_name] = {
            'button': btn,
            'frame': frame,
            'frame_name': frame_name,
        }
        self._set_settings_frame_visibility(button_name, initial_checked, trigger_snap=False)
        btn.toggled.connect(lambda checked, name=button_name: self._on_settings_frame_button_toggled(name, checked))
        if hasattr(self, '_save_ui_state'):
            btn.toggled.connect(self._save_ui_state)

    def _on_settings_frame_button_toggled(self, button_name, checked):
        self._set_settings_frame_visibility(button_name, checked, update_button=False)

    def _set_settings_frame_visibility(self, button_name, visible, *, trigger_snap=True, update_button=True):
        btn, frame = self._ensure_settings_toggle_refs(button_name)
        if btn is None and frame is None:
            return

        if update_button and btn:
            blocker_cls = getattr(QtCore, "QSignalBlocker", None)
            blocker = blocker_cls(btn) if blocker_cls else None
            if blocker is None:
                btn.blockSignals(True)
            btn.setChecked(visible)
            if blocker_cls:
                del blocker
            else:
                btn.blockSignals(False)

        if frame and isValid(frame):
            frame.setVisible(visible)

        if trigger_snap:
            # Use a small delay to ensure layout has processed, then snap smoothly
            QtCore.QTimer.singleShot(50, self.snap_to_minimum)

    def _get_settings_frame_checked(self, button_name):
        btn, _ = self._ensure_settings_toggle_refs(button_name)
        return bool(btn.isChecked()) if btn else False

    def _ensure_settings_toggle_refs(self, button_name):
        data = self._settings_frame_toggles.get(button_name)
        if not data:
            return None, None

        btn = data.get('button')
        if not btn or not isValid(btn):
            btn = self.findChild(QtWidgets.QPushButton, button_name)
            if btn:
                data['button'] = btn

        frame = data.get('frame')
        frame_name = data.get('frame_name')
        if not frame_name and frame and isValid(frame):
            frame_name = frame.objectName()
            data['frame_name'] = frame_name
        if frame_name and (not frame or not isValid(frame)):
            frame = self.findChild(QtWidgets.QWidget, frame_name)
            if frame:
                data['frame'] = frame

        return btn, frame

    def _on_material_affix_changed(self, kind, text):
        text = (text or "").strip()
        if kind == 'prefix':
            self._material_name_prefix = text
            self._material_creator_settings['name_prefix'] = text
        else:
            self._material_name_suffix = text
            self._material_creator_settings['name_suffix'] = text
        self._mark_settings_dirty()

    def _apply_attribute_checkbox_state(self, frame_name, checked, invoke_snap=True):
        frame = self.findChild(QtWidgets.QWidget, frame_name)
        if frame:
            frame.setVisible(checked)
            if not checked:
                try:
                    self._reset_attribute_to_default(frame_name)
                except Exception:
                    pass
            if invoke_snap:
                # Use a small delay to ensure layout has processed, then snap smoothly
                QtCore.QTimer.singleShot(50, self.snap_to_minimum)

    def _on_attribute_checkbox_toggled(self, frame_name, checked):
        self._attribute_visibility_overrides[frame_name] = bool(checked)
        key = f'attribute_frame_visible_{frame_name}'
        self._material_creator_settings[key] = bool(checked)
        self._apply_attribute_checkbox_state(frame_name, bool(checked))
        self._mark_settings_dirty()

    def _read_attribute_visibility_from_settings(self, frame_name, default_value):
        """Helper used by update_material_attr_visibility for legacy overrides."""
        settings = self._load_settings_cache().get('material_creator', {})
        key = f'attribute_frame_visible_{frame_name}'
        if key in settings:
            value = bool(settings[key])
            self._attribute_visibility_overrides.setdefault(frame_name, value)
            return value
        return default_value

    def _setup_texture_importer_settings_section(self):
        """Wire the embedded texture importer settings widgets."""
        self._wire_settings_frame_toggle(
            button_name='textureImporterSettingsButton',
            frame_name='textureImporterSettingsFrame',
            default_visible=False
        )

        self._texture_mode_checkboxes = {
            'maya_file': 'textureSearchMayaFileCheckbox',
            'sourceimages': 'textureSearchMayaSourceimagesCheckbox',
            'custom': 'textureSearchCustomPathCheckbox'
        }

        selected_mode = self._texture_importer_settings.get('default_mode', 'maya_file')
        for mode, widget_name in self._texture_mode_checkboxes.items():
            cb = self.ui_elements.get(widget_name)
            if not cb:
                continue
            cb.blockSignals(True)
            cb.setChecked(mode == selected_mode)
            cb.blockSignals(False)
            cb.toggled.connect(lambda checked, m=mode: self._on_texture_search_mode_toggled(m, checked))

        custom_path_edit = self.ui_elements.get('textureSearchCustomPathLineEdit')
        if custom_path_edit:
            custom_path_edit.blockSignals(True)
            custom_path_edit.setText(self._texture_importer_settings.get('custom_path', ''))
            custom_path_edit.blockSignals(False)
            custom_path_edit.editingFinished.connect(self._on_custom_path_changed)

        create_cb = self.ui_elements.get('createIfDoesntExistCheckbox')
        if create_cb:
            create_cb.blockSignals(True)
            create_cb.setChecked(bool(self._texture_importer_settings.get('create_if_doesnt_exist', False)))
            create_cb.blockSignals(False)
            create_cb.stateChanged.connect(lambda _: self._save_texture_importer_settings_from_widgets())

        set_btn = self.ui_elements.get('textureSearchCustomPathSetButton')
        if set_btn:
            set_btn.clicked.connect(self._handle_texture_custom_path_button)

        names_btn = self.ui_elements.get('editTextureSearchNamesButton')
        if names_btn:
            names_btn.clicked.connect(self.open_texture_search_names_ui)

        self._update_custom_path_widgets()
        self._save_texture_importer_settings_from_widgets()

    def _current_texture_search_mode(self):
        for mode, widget_name in self._texture_mode_checkboxes.items():
            cb = self.ui_elements.get(widget_name)
            if cb and cb.isChecked():
                return mode
        return 'maya_file'

    def _on_texture_search_mode_toggled(self, mode, checked):
        if self._updating_texture_mode:
            return
        self._updating_texture_mode = True
        try:
            if checked:
                for other_mode, widget_name in self._texture_mode_checkboxes.items():
                    if other_mode == mode:
                        continue
                    cb = self.ui_elements.get(widget_name)
                    if cb and cb.isChecked():
                        cb.blockSignals(True)
                        cb.setChecked(False)
                        cb.blockSignals(False)
                self._texture_importer_settings['default_mode'] = mode
            else:
                # Prevent scenario where all modes are unchecked
                any_checked = False
                for widget_name in self._texture_mode_checkboxes.values():
                    cb = self.ui_elements.get(widget_name)
                    if cb and cb.isChecked():
                        any_checked = True
                        break
                if not any_checked:
                    cb = self.ui_elements.get(self._texture_mode_checkboxes.get(mode))
                    if cb:
                        cb.blockSignals(True)
                        cb.setChecked(True)
                        cb.blockSignals(False)
                    self._texture_importer_settings['default_mode'] = mode
            self._update_custom_path_widgets()
            self._save_texture_importer_settings_from_widgets()
        finally:
            self._updating_texture_mode = False

    def _update_custom_path_widgets(self):
        """Enable/disable custom-path widgets based on checkbox state."""
        custom_cb_name = self._texture_mode_checkboxes.get('custom')
        custom_cb = self.ui_elements.get(custom_cb_name) if custom_cb_name else None
        custom_on = bool(custom_cb.isChecked()) if custom_cb else False
        for widget_name in (
            "textureSearchCustomPathLineEdit",
            "textureSearchCustomPathSetButton",
            "customSearchFolderPathLabel",
            "createIfDoesntExistCheckbox"
        ):
            w = self.ui_elements.get(widget_name)
            if w:
                w.setEnabled(custom_on)

    def _on_custom_path_changed(self):
        line_edit = self.ui_elements.get("textureSearchCustomPathLineEdit")
        if not line_edit:
            return
        self._texture_importer_settings['custom_path'] = line_edit.text().strip()
        self._save_texture_importer_settings_from_widgets()

    def _handle_texture_custom_path_button(self):
        line_edit = self.ui_elements.get("textureSearchCustomPathLineEdit")
        if not line_edit:
            return
        current_path = line_edit.text().strip()

        if current_path:
            resolved_path = self._resolve_custom_path_keys(current_path)
            if resolved_path:
                if os.path.exists(resolved_path):
                    if os.name == 'nt':
                        os.startfile(resolved_path)
                    elif os.name == 'posix':
                        if hasattr(os, 'uname') and os.uname().sysname == 'Darwin':
                            os.system(f'open "{resolved_path}"')
                        else:
                            os.system(f'xdg-open "{resolved_path}"')
                else:
                    create_cb = self.ui_elements.get("createIfDoesntExistCheckbox")
                    if create_cb and create_cb.isChecked():
                        try:
                            os.makedirs(resolved_path, exist_ok=True)
                            if os.name == 'nt':
                                os.startfile(resolved_path)
                            elif os.name == 'posix':
                                if hasattr(os, 'uname') and os.uname().sysname == 'Darwin':
                                    os.system(f'open "{resolved_path}"')
                                else:
                                    os.system(f'xdg-open "{resolved_path}"')
                        except Exception as exc:
                            cmds.warning(f"Failed to create folder '{resolved_path}': {exc}")
                    else:
                        cmds.warning(f"Folder does not exist: {resolved_path}\nEnable 'Create if doesn't exist' to create it automatically.")
            else:
                cmds.warning(f"Invalid path template: {current_path}")
        else:
            start_dir = cmds.workspace(q=True, rootDirectory=True) or ""
            folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Texture Folder", start_dir)
            if folder:
                line_edit.setText(folder)
                self._on_custom_path_changed()

    def _resolve_custom_path_keys(self, path_template):
        if not path_template:
            return None
        try:
            scene_path = cmds.file(q=True, sn=True) or ""
            scene_dir = os.path.dirname(scene_path) if scene_path else ""

            project_path = cmds.workspace(q=True, rootDirectory=True) or ""
            project_dir = project_path.rstrip("/\\") if project_path else ""

            resolved = path_template
            resolved = resolved.replace("(scene)", scene_dir)
            resolved = resolved.replace("(project)", project_dir)
            return os.path.normpath(resolved)
        except Exception as exc:
            print(f"[DEBUG] Error resolving path template '{path_template}': {exc}")
            return None

    def _save_texture_importer_settings_from_widgets(self):
        self._texture_importer_settings['default_mode'] = self._current_texture_search_mode()
        line_edit = self.ui_elements.get("textureSearchCustomPathLineEdit")
        if line_edit:
            self._texture_importer_settings['custom_path'] = line_edit.text().strip()
        create_cb = self.ui_elements.get("createIfDoesntExistCheckbox")
        if create_cb:
            self._texture_importer_settings['create_if_doesnt_exist'] = create_cb.isChecked()
        self._mark_settings_dirty()

    def open_texture_search_names_ui(self):
        """Launch the TextureSearchNamesUI from the embedded settings."""
        try:
            TextureSearchNamesUI = texture_importer.TextureSearchNamesUI
        except Exception as exc:
            cmds.warning(f"Failed to load texture search names UI: {exc}")
            return
        if not self._texture_search_names_ui or not isValid(self._texture_search_names_ui):
            try:
                self._texture_search_names_ui = TextureSearchNamesUI(parent=self)
            except Exception as exc:
                cmds.warning(f"Failed to initialize texture search names UI: {exc}")
                self._texture_search_names_ui = None
                return
        self._texture_search_names_ui.show()
        self._texture_search_names_ui.raise_()

    # ---------- Dynamic minimum size helpers ----------
    def _default_min_sizing_profile(self):
        """
        Returns a dict you can customize per section. Heights are additive.
        Keys are the *frame* objectNames (we already convert Layout->Frame elsewhere).
        """
        return {
            "base_width": 300,  # base min width even if all sections hidden
            "base_height": 50,  # base min height even if all sections hidden (reduced from 100)
            "sections": {
                "materialCreatorFrame": 140,  # visible => add this many pixels of min height (reduced from 210)
                "materialCreatorSettingsFrame": 200,
                "textureImporterSettingsFrame": 160,
                "materialToolsFrame": 75,
                "materialListFrame": 200,
                "materialListSettingsFrame": 220,
                "materialListFiltersFrame": 150,

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
        For materialListSettingsFrame, uses actual widget size to account for UI scaling.
        Also accounts for visible material attribute frames (20px each) within materialCreatorFrame.
        """
        # Ensure profile exists
        if not hasattr(self, "_minsize_profile"):
            self._minsize_profile = self._default_min_sizing_profile()

        profile = self._minsize_profile
        min_w = max(self._minimum_width_baseline, int(profile.get("base_width", self._minimum_width_baseline)))
        min_h = int(profile.get("base_height", 50))

        # Add section heights if frames are visible
        sections = profile.get("sections", {})
        for frame_name, add_h in sections.items():
            w = self.findChild(QtWidgets.QWidget, frame_name)
            if w and w.isVisible():
                try:
                    # For materialListSettingsFrame, use actual widget size to account for UI scaling
                    if frame_name == "materialListSettingsFrame":
                        # Get the actual preferred size hint (accounts for current UI scale/DPI)
                        hint = w.sizeHint()
                        if hint.height() > 0:
                            # Use sizeHint which already accounts for UI scaling in Qt
                            frame_height = hint.height()
                        else:
                            # Fallback: ensure frame is laid out and get its size
                            # Force layout update if needed
                            w.updateGeometry()
                            actual_size = w.size().height()
                            if actual_size > 0:
                                frame_height = actual_size
                            else:
                                # Last resort: use minimum size hint
                                min_hint = w.minimumSizeHint()
                                frame_height = min_hint.height() if min_hint.height() > 0 else add_h
                        
                        # Ensure we have a reasonable minimum even if size is 0
                        if frame_height <= 0:
                            frame_height = add_h
                        frame_height = max(int(add_h), int(frame_height))
                        min_h += int(frame_height)
                    elif frame_name == "materialListFiltersFrame":
                        hint = w.sizeHint()
                        frame_height = hint.height() if hint.height() > 0 else 0
                        if frame_height <= 0:
                            w.updateGeometry()
                            frame_height = w.size().height()
                        if frame_height <= 0:
                            min_hint = w.minimumSizeHint()
                            frame_height = min_hint.height() if min_hint.height() > 0 else int(add_h)
                        frame_height = max(int(add_h), int(frame_height))
                        min_h += int(frame_height)
                    elif frame_name == "materialCreatorFrame":
                        # For materialCreatorFrame, add base height, then add 20px for each visible attribute frame
                        min_h += int(add_h)
                        
                        # Check which material attribute frames are visible (20px each, reduced from 30px)
                        attribute_frames = [
                            'colorPickerFrame',
                            'roughnessSliderFrame',
                            'metalnessSliderFrame',
                            'emissionSliderFrame',
                            'opacitySliderFrame',
                            'transmissionSliderFrame',
                            'subsurfaceSliderFrame'
                        ]
                        
                        for attr_frame_name in attribute_frames:
                            attr_frame = self.findChild(QtWidgets.QWidget, attr_frame_name)
                            if attr_frame and attr_frame.isVisible():
                                min_h += 25  # 20px per visible attribute frame (reduced from 30px)
                    else:
                        # For other frames, use the profile value (Qt handles scaling automatically)
                        min_h += int(add_h)
                except Exception:
                    pass

        # Apply to the dialog (self) once, after computing the total
        self.setMinimumSize(min_w, min_h)
        self._last_minimum_size = QtCore.QSize(min_w, min_h)

        # Nudge layouts so Maya updates dock constraints
        self.resize_ui(delay=1)  # Keep your small micro-timer bump


    def snap_to_minimum(self):
        """
        Recompute and then snap to the minimum *height* only.
        Prevent any horizontal creep by freezing width for one tick (min=max=current),
        force the vertical shrink/expand, then release all caps.
        """
        def _apply_resize():
            # 0) Process any pending layout events to ensure visibility changes are applied
            # Force layout update to ensure all visibility changes are reflected
            self.updateGeometry()
            QtWidgets.QApplication.processEvents(QtCore.QEventLoop.ExcludeUserInputEvents)
            # Give layout a moment to settle
            QtWidgets.QApplication.sendPostedEvents(None, 0)
            QtWidgets.QApplication.processEvents(QtCore.QEventLoop.ExcludeUserInputEvents)
            self._debug_print_size("snap_to_minimum -> before refresh")
            
            # 1) Recompute dynamic minimums after layout has updated
            self.refresh_minimum_size()
            min_sz = self.minimumSize()
            min_h = max(1, min_sz.height())
            self._debug_print_size("snap_to_minimum -> after refresh")

            wc_name = getattr(self, "workspace_control_name", None)

            # Capture the current drawn width so we can preserve it
            try:
                current_width = int(max(
                    self.width() or 0,
                    self.geometry().width() if self.geometry() else 0,
                    min_sz.width() if min_sz.width() > 0 else 0,
                    self._minimum_width_baseline,
                ))
            except Exception:
                current_width = max(self._minimum_width_baseline, min_sz.width() if min_sz.width() > 0 else self._minimum_width_baseline)

            original_self_min_w = self.minimumWidth()
            original_self_max_w = self.maximumWidth()

            # Get the actual Qt host (workspaceControl widget) and its CURRENT drawn width
            qt_host = None
            memo_width_callable = getattr(getattr(self, "_last_minimum_size", None), "width", None)
            memo_min_w = memo_width_callable() if callable(memo_width_callable) else memo_width_callable
            target_min_w = max(self._minimum_width_baseline, memo_min_w if memo_min_w else min_sz.width(), 1)
            host_w = max(self.width(), current_width)
            host_original_min_w = None
            host_original_max_w = None
            try:
                if wc_name and cmds.workspaceControl(wc_name, q=True, exists=True):
                    ptr = omui.MQtUtil.findControl(wc_name)
                    if ptr:
                        qt_host = wrapInstance(int(ptr), QtWidgets.QWidget)
                        if qt_host:
                            host_original_min_w = qt_host.minimumWidth()
                            host_original_max_w = qt_host.maximumWidth()
                            if qt_host.width() > 0:
                                host_w = max(host_w, qt_host.width())
            except Exception:
                pass
            if host_w <= 0:
                host_w = current_width
            if host_w <= 0:
                host_w = target_min_w

            # 2) Tell workspaceControl about the new min height (helps dock splitters)
            try:
                if wc_name and cmds.workspaceControl(wc_name, q=True, exists=True):
                    cmds.workspaceControl(wc_name, e=True, minimumHeight=min_h)
            except Exception:
                pass

            # 3) Set minimum height constraint, then resize smoothly without temporary clamping
            original_self_min_h = self.minimumHeight()
            original_self_max_h = self.maximumHeight()
            host_original_min_h = None
            host_original_max_h = None
            
            # Set minimum height first to prevent shrinking below target
            self.setMinimumHeight(min_h)
            try:
                if qt_host:
                    host_original_min_h = qt_host.minimumHeight()
                    host_original_max_h = qt_host.maximumHeight()
                    qt_host.setMinimumHeight(min_h)
            except Exception:
                pass

            # Resize to target height smoothly (don't clamp max, let it resize naturally)
            try:
                if qt_host:
                    qt_host.resize(host_w, min_h)
                    qt_host.updateGeometry()
            except Exception:
                pass

            try:
                self.resize(current_width, min_h)
            except Exception:
                pass
            self.updateGeometry()
            self._debug_print_size("snap_to_minimum -> after resize")

            # 5) If floating, also ask Maya to size the container (helps on some hosts)
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

            # 6) Process a layout pass, then release max constraints after a brief delay
            QtWidgets.QApplication.sendPostedEvents(None, 0)
            QtWidgets.QApplication.processEvents()

            def _release_caps():
                try:
                    if qt_host:
                        if host_original_min_w is not None:
                            qt_host.setMinimumWidth(host_original_min_w)
                        else:
                            qt_host.setMinimumWidth(0)
                        if host_original_max_w is not None and host_original_max_w >= host_original_min_w if host_original_min_w is not None else True:
                            qt_host.setMaximumWidth(host_original_max_w)
                        else:
                            qt_host.setMaximumWidth(16777215)
                        if host_original_max_h is not None:
                            qt_host.setMaximumHeight(host_original_max_h)
                        else:
                            qt_host.setMaximumHeight(16777215)
                    # Keep our *minimumHeight* (we want the new min to persist),
                    # but release width and height maximums so user can resize.
                    self.setMinimumWidth(original_self_min_w if original_self_min_w else self._minimum_width_baseline)
                    self.setMaximumWidth(original_self_max_w if original_self_max_w else 16777215)
                    if original_self_max_h is not None:
                        self.setMaximumHeight(original_self_max_h)
                    else:
                        self.setMaximumHeight(16777215)
                except Exception:
                    pass
            # Release constraints after layout has settled
            QtCore.QTimer.singleShot(100, _release_caps)

        # Defer so the visibility/layout changes from toggles have been applied
        QtCore.QTimer.singleShot(0, _apply_resize)

    def _debug_print_size(self, label):
        """Utility debug helper to report current and minimum size."""
        if not getattr(self, "_layout_debug_enabled", False):
            return
        try:
            current = self.size()
            minimum = self.minimumSize()
            pieces = []

            wc_name = getattr(self, "workspace_control_name", None)
            if wc_name and cmds.workspaceControl(wc_name, q=True, exists=True):
                try:
                    host_height = cmds.workspaceControl(wc_name, q=True, height=True)
                    host_min_h = cmds.workspaceControl(wc_name, q=True, minimumHeight=True)
                    host_float = cmds.workspaceControl(wc_name, q=True, floating=True)
                    pieces.append(f"workspaceControl height={host_height} minHeight={host_min_h} floating={host_float}")
                except Exception as host_exc:
                    pieces.append(f"workspaceControl query failed: {host_exc}")

                try:
                    ptr = omui.MQtUtil.findControl(wc_name)
                    if ptr:
                        host_widget = wrapInstance(int(ptr), QtWidgets.QWidget)
                        if host_widget:
                            pieces.append(f"hostWidget size={host_widget.width()}x{host_widget.height()} min={host_widget.minimumWidth()}x{host_widget.minimumHeight()}")
                except Exception as host_w_exc:
                    pieces.append(f"hostWidget query failed: {host_w_exc}")

            print(" | ".join(pieces))
        except Exception as exc:
            pass

    def _apply_minimum_width_baseline(self):
        """Ensure the widget and its top-level container respect the baseline width."""
        wc_name = getattr(self, "workspace_control_name", None)
        if wc_name and cmds.workspaceControl(wc_name, q=True, exists=True):
            try:
                if not cmds.workspaceControl(wc_name, q=True, floating=True):
                    # When docked, leave sizing to Maya; just ensure the dialog keeps the baseline min width.
                    try:
                        self.setMinimumWidth(self._minimum_width_baseline)
                    except Exception:
                        pass
                    return
            except Exception:
                pass

        try:
            width = max(self._minimum_width_baseline, self.minimumSize().width())
        except Exception:
            width = self._minimum_width_baseline

        try:
            self.setMinimumWidth(width)
            self.resize(width, self.height() if self.height() >= 1 else width)
        except Exception:
            pass

        try:
            top = self.window()
            if top and top is not self and isValid(top):
                top.setMinimumWidth(width)
                top.resize(width, top.height() if top.height() >= 1 else width)
        except Exception:
            pass

        wc_name = getattr(self, "workspace_control_name", None)
        is_floating = False
        if wc_name and cmds.workspaceControl(wc_name, q=True, exists=True):
            try:
                is_floating = cmds.workspaceControl(wc_name, q=True, floating=True)
            except Exception:
                pass

        if is_floating or not wc_name:
            try:
                min_h = max(1, self.minimumSize().height())
                self._enforce_workspace_size(width, min_h)
            except Exception:
                pass

    def _enforce_workspace_size(self, width, height):
        """Ensure both the dialog and its workspace host retain the desired size."""
        try:
            width = int(width)
        except Exception:
            width = self.width() if self.width() > 0 else self._minimum_width_baseline
        try:
            height = max(1, int(height))
        except Exception:
            height = max(1, self.minimumSize().height())

        wc_name = getattr(self, "workspace_control_name", None)
        is_floating = False
        if wc_name and cmds.workspaceControl(wc_name, q=True, exists=True):
            try:
                is_floating = cmds.workspaceControl(wc_name, q=True, floating=True)
            except Exception:
                pass

        baseline_min_width = max(self._minimum_width_baseline, 1)

        if not is_floating and wc_name:
            # Docked: only update our minimums and leave the host/layout alone.
            try:
                self.setMinimumWidth(baseline_min_width)
            except Exception:
                pass
            try:
                self.setMinimumHeight(height)
            except Exception:
                pass
            return

        # Floating (or not attached to a workspace control)
        try:
            self.setMinimumWidth(baseline_min_width)
            self.setMinimumHeight(height)
        except Exception:
            pass

        if is_floating or not wc_name:
            try:
                self.resize(self.width() if self.width() > 0 else baseline_min_width, height)
            except Exception:
                pass
            try:
                top = self.window()
                if top and top is not self and isValid(top):
                    top.setMinimumWidth(baseline_min_width)
                    top.setMinimumHeight(height)
                    top.resize(top.width() if top.width() > 0 else baseline_min_width, height)
            except Exception:
                pass
        else:
            # Docked: avoid forcing a resize, just ensure baseline width propagates
            try:
                top = self.window()
                if top and top is not self and isValid(top):
                    top.setMinimumWidth(baseline_min_width)
            except Exception:
                pass

        if wc_name and cmds.workspaceControl(wc_name, q=True, exists=True) and is_floating:
            try:
                cmds.workspaceControl(wc_name, e=True, minimumHeight=height)
            except Exception:
                pass
            try:
                cmds.workspaceControl(wc_name, e=True, minimumWidth=width)
            except Exception:
                pass
            try:
                cmds.workspaceControl(wc_name, e=True, resizeHeight=height)
            except Exception:
                pass
            try:
                cmds.workspaceControl(wc_name, e=True, resizeWidth=width)
            except Exception:
                pass

            try:
                ptr = omui.MQtUtil.findControl(wc_name)
                if ptr:
                    host_widget = wrapInstance(int(ptr), QtWidgets.QWidget)
                    if host_widget:
                        host_widget.setMinimumSize(width, height)
                        host_widget.resize(width, height)
            except Exception:
                pass

    def _install_workspace_state_job(self):
        """Install a scriptJob that fires when this workspace control docks/undocks."""
        if self._workspace_state_job_id:
            return
        wc_name = getattr(self, "workspace_control_name", None)
        if not wc_name or not cmds.workspaceControl(wc_name, q=True, exists=True):
            return
        # Guard against Maya versions that don't provide this event
        try:
            available_events = set(cmds.scriptJob(listEvents=True) or [])
        except Exception:
            available_events = set()
        event_name = "workspaceControlStateChange"
        if event_name not in available_events:
            self._workspace_state_job_id = None
            return
        try:
            self._workspace_state_job_id = cmds.scriptJob(
                e=(event_name, self._workspace_control_state_changed),
                protected=True,
                parent=wc_name,
            )
        except Exception as exc:
            self._workspace_state_job_id = None

    def _remove_workspace_state_job(self, *args):
        """Remove the workspace control state scriptJob if it is active."""
        if self._workspace_state_job_id:
            try:
                if cmds.scriptJob(exists=self._workspace_state_job_id):
                    cmds.scriptJob(kill=self._workspace_state_job_id, force=True)
            except Exception:
                pass
            self._workspace_state_job_id = None

    def _workspace_control_state_changed(self, *args):
        """scriptJob callback when any workspace control changes state."""
        if not args:
            return
        wc_name = getattr(self, "workspace_control_name", None)
        if not wc_name or args[0] != wc_name:
            return
        QtCore.QTimer.singleShot(0, self._handle_workspace_state_change)

    def _handle_workspace_state_change(self):
        """Recompute and enforce the minimum height after docking or floating changes."""
        # Update retain state based on docking (so it only saves to workspace when docked)
        self._update_retain_state()
        
        self.refresh_minimum_size()
        size = getattr(self, "_last_minimum_size", self.minimumSize())
        min_w = max(1, size.width())
        min_h = max(1, size.height())
        self._debug_print_size("workspaceControlStateChanged")
        self._enforce_workspace_size(min_w, min_h)
    
    def _update_retain_state(self):
        """Update the retain flag based on whether the workspace control is docked or floating."""
        try:
            if not cmds.workspaceControl(self.workspace_control_name, query=True, exists=True):
                return
            
            # Check if floating
            is_floating = False
            try:
                is_floating = cmds.workspaceControl(self.workspace_control_name, query=True, floating=True)
            except Exception:
                pass
            
            # Only retain (save to workspace) when docked, not when floating
            retain_value = not is_floating
            
            try:
                cmds.workspaceControl(
                    self.workspace_control_name,
                    edit=True,
                    retain=retain_value,
                )
            except Exception as e:
                # If we can't set retain, that's okay - might not be supported in all Maya versions
                pass
        except Exception:
            pass

    def setup_connections(self):
        """Set up all the necessary connections for the UI elements."""
        # Apply material button connection
        if self.ui_elements.get('createNewMaterialButton'):
            self.ui_elements['createNewMaterialButton'].clicked.connect(self.create_material)

        # Delete unused materials button connection
        if self.ui_elements.get('deleteUnusedMaterialsButton'):
            self.ui_elements['deleteUnusedMaterialsButton'].clicked.connect(self.delete_unused_materials)

        # Connect delete selected materials button to delete_selected_materials function
        if self.ui_elements.get('deleteSelectedButton'):
            self.ui_elements['deleteSelectedButton'].clicked.connect(self.delete_selected_materials)

        # Connect highlight unused checkbox to refresh list
        highlight_unused_cb = self._get_widget('highlightUnusedCheckbox', QtWidgets.QCheckBox)
        if highlight_unused_cb:
            highlight_unused_cb.toggled.connect(self.refresh_materials_list)
            highlight_unused_cb.stateChanged.connect(self._save_ui_state)
        
        # Connect show icons checkbox (controls all icons: shader swatches, texture icons, shading group icons)
        show_icons_cb = self._get_widget('showIconsCheckbox', QtWidgets.QCheckBox)
        if show_icons_cb:
            show_icons_cb.toggled.connect(self.refresh_materials_list)
            show_icons_cb.stateChanged.connect(self._save_ui_state)

        # --- install auto-refresh watchers for the material list ---
        self._install_material_watchers()  # ensures list refreshes on scene/material changes

        # Connect toggle buttons for layouts with friendly names
        if self.ui_elements.get('toggleMaterialCreatorVis'):
            self.ui_elements['toggleMaterialCreatorVis'].clicked.connect(
                lambda: self.toggle_layout_visibility('materialCreatorLayout', 'toggleMaterialCreatorVis',
                                                      'Creator')
            )
            # Connect to save state when changed
            self.ui_elements['toggleMaterialCreatorVis'].clicked.connect(self._save_ui_state)
        if self.ui_elements.get('toggleMaterialToolsVis'):
            self.ui_elements['toggleMaterialToolsVis'].clicked.connect(
                lambda: self.toggle_layout_visibility('materialToolsLayout', 'toggleMaterialToolsVis', 'Tools')
            )
            # Connect to save state when changed
            self.ui_elements['toggleMaterialToolsVis'].clicked.connect(self._save_ui_state)
        if self.ui_elements.get('toggleMaterialListVis'):
            self.ui_elements['toggleMaterialListVis'].clicked.connect(
                lambda: self.toggle_layout_visibility('materialListLayout', 'toggleMaterialListVis', 'List')
            )
            # Connect to save state when changed
            self.ui_elements['toggleMaterialListVis'].clicked.connect(self._save_ui_state)
        
        # Connect the new Material Manager toggle button
        if self.ui_elements.get('toggleMaterialManagerVis'):
            self.ui_elements['toggleMaterialManagerVis'].clicked.connect(
                lambda: self.toggle_layout_visibility('materialManagerFrame', 'toggleMaterialManagerVis', 'Manager')
            )
            # Connect to save state when changed
            self.ui_elements['toggleMaterialManagerVis'].clicked.connect(self._save_ui_state)
        
        # Connect search bar text changes to filter materials
        materialSearchLineEdit = self.ui_elements.get('materialSearchLineEdit')
        if materialSearchLineEdit:
            materialSearchLineEdit.textChanged.connect(self.filter_materials)
            # Note: Search text is NOT saved to state (user preference: search should reset on reload)

        # Refresh materials list button connection
        if self.ui_elements.get('refreshMaterialsButton'):
            self.ui_elements['refreshMaterialsButton'].clicked.connect(lambda: self.refresh_materials_list())

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
            # Connect to save state when changed
            material_type_combo_box.currentTextChanged.connect(self._save_ui_state)
            # Update attribute UI visibility whenever the type changes
            material_type_combo_box.currentIndexChanged.connect(self.update_material_attr_visibility)
        
        # Fix horizontal lines that Maya's stylesheet hides
        self._fix_horizontal_lines()

        _mpm = self.ui_elements.get('materialPerMeshCheckbox')
        if _mpm:
            _mpm.stateChanged.connect(self.update_create_material_button)
            # Connect to save state when changed
            _mpm.stateChanged.connect(self._save_ui_state)



        # Connect the random hue checkbox to update the color immediately
        random_hue_cb = self.ui_elements.get('randomHueCheckbox')
        if random_hue_cb:
            random_hue_cb.stateChanged.connect(
                lambda state: self.set_random_hue_color() if state == QtCore.Qt.Checked else None
            )
            # Connect to save state when changed
            random_hue_cb.stateChanged.connect(self._save_ui_state)

        # Connect the clear search button to the clear function
        clear_search_button = self.ui_elements.get('clearMaterialSearchLineEditButton')
        if clear_search_button:
            clear_search_button.clicked.connect(self.clear_material_search)
        else:
            print("Error: clearMaterialSearchLineEditButton not found.")
        
        # Connect material naming line edit to save state when changed
        material_naming_edit = self.ui_elements.get('materialNamingLineEdit')
        if material_naming_edit:
            material_naming_edit.textChanged.connect(self._save_ui_state)

        # Launch the Material converter tool
        material_converter_btn = self.ui_elements.get('materialConverterButton')
        if material_converter_btn:
            material_converter_btn.clicked.connect(self.open_material_converter)
        else:
            print("Error: materialConverterButton not found.")
        
        # Launch the Mesh exporter tool
        mesh_exporter_btn = self.ui_elements.get('meshExporterButton')
        if mesh_exporter_btn:
            mesh_exporter_btn.clicked.connect(self.open_mesh_exporter)
        else:
            print("Error: meshExporterButton not found.")
        
        # Connect list entry scaling buttons
        scale_down_btn = self._get_widget('scaleDownListEntriesButton', QtWidgets.QPushButton)
        scale_up_btn = self._get_widget('scaleUpListEntriesButton', QtWidgets.QPushButton)
        if scale_down_btn:
            scale_down_btn.clicked.connect(self.scale_down_list_entries)
        if scale_up_btn:
            scale_up_btn.clicked.connect(self.scale_up_list_entries)
        
        # Initialize button states
        self._update_scale_button_states()

        # --- List buttons removed - functionality now available via right-click menu ---
        # Hide the toggle list buttons checkbox if it exists
        tlb_checkbox = self.ui_elements.get('toggleListButtonsCheckbox')
        if tlb_checkbox:
            tlb_checkbox.setVisible(False)

        # --- NEW: Material List Options panel toggle ---
        options_btn = self.ui_elements.get('materialListSettingsButton')
        if options_btn:
            # Make it a checkable toggle button (styling handled by Qt stylesheet)
            options_btn.setCheckable(True)
            
            # Connect to toggle function
            try:
                options_btn.toggled.connect(self.toggle_material_list_options)
            except Exception as e:
                pass
            # Connect to save state when changed
            try:
                options_btn.toggled.connect(self._save_ui_state)
            except Exception as e:
                pass
        else:
            # Try using _get_widget as fallback
            options_btn_fallback = self._get_widget('materialListSettingsButton', QtWidgets.QPushButton)

        # Hide Namespaces Checkbox - update display names without rebuilding list
        hide_namespaces_cb = self._get_widget('hideNamespacesCheckbox', QtWidgets.QCheckBox)
        if hide_namespaces_cb:
            def _on_hide_namespaces_changed(state):
                # Update display names of existing entries without rebuilding the list
                self._update_namespace_display()
            hide_namespaces_cb.stateChanged.connect(_on_hide_namespaces_changed)
            hide_namespaces_cb.stateChanged.connect(self._save_ui_state)

        # --- Node Type Show Checkboxes ---
        # File Textures
        show_file_tex_cb = self._get_widget('showTexturesCheckbox', QtWidgets.QCheckBox)
        if show_file_tex_cb:
            try:
                show_file_tex_cb.stateChanged.disconnect()
            except Exception:
                pass
            show_file_tex_cb.stateChanged.connect(lambda state: self.refresh_materials_list())
            show_file_tex_cb.stateChanged.connect(lambda state: self._update_filter_checkbox_states())
            show_file_tex_cb.stateChanged.connect(self._save_ui_state)
        
        # Procedural Textures
        show_proc_tex_cb = self._get_widget('showProceduralTexturesCheckbox', QtWidgets.QCheckBox)
        if show_proc_tex_cb:
            try:
                show_proc_tex_cb.stateChanged.disconnect()
            except Exception:
                pass
            show_proc_tex_cb.stateChanged.connect(lambda state: self.refresh_materials_list())
            show_proc_tex_cb.stateChanged.connect(lambda state: self._update_filter_checkbox_states())
            show_proc_tex_cb.stateChanged.connect(self._save_ui_state)
        
        # Shading Groups
        show_sg_cb = self._get_widget('showShadingGroupsCheckbox', QtWidgets.QCheckBox)
        if show_sg_cb:
            try:
                show_sg_cb.stateChanged.disconnect()
            except Exception:
                pass
            show_sg_cb.stateChanged.connect(lambda state: self.refresh_materials_list())
            show_sg_cb.stateChanged.connect(lambda state: self._update_filter_checkbox_states())
            show_sg_cb.stateChanged.connect(self._save_ui_state)
        
        # --- Live filters (auto-wired from MATERIAL_FILTERS) ---
        # Wire filter buttons and checkboxes
        for f in self._filter_spec():
            if "button" in f:
                btn = self._get_widget(f["button"], QtWidgets.QPushButton)
                if btn:
                    # Ensure button is checkable
                    btn.setCheckable(True)
                    try:
                        btn.toggled.disconnect()
                    except Exception:
                        pass
                    btn.toggled.connect(lambda checked: self.refresh_materials_list())
                    # Connect to save state when changed
                    btn.toggled.connect(self._save_ui_state)
            elif "checkbox" in f:
                cb = self._get_widget(f["checkbox"], QtWidgets.QCheckBox)
                if cb:
                    try:
                        cb.stateChanged.disconnect()
                    except Exception:
                        pass
                    cb.stateChanged.connect(lambda state: self.refresh_materials_list())
                    # Connect to save state when changed
                    cb.stateChanged.connect(self._save_ui_state)

        # ---- allow unchecking to "none" by disabling strict exclusivity in groups (if present) ----
        # If you still use QButtonGroups in the .ui, disable exclusivity so "none" is possible.
        for group_object_name in ('referenceButtonGroup', 'usedButtonGroup', 'selectedButtonGroup'):
            g = self.findChild(QtWidgets.QButtonGroup, group_object_name)
            if g:
                g.setExclusive(False)

        # ---- Wire exclusivity groups for filter buttons (allow unchecking) ----
        filter_button_groups = {}
        checkbox_groups = {}
        for f in self._filter_spec():
            grp = f.get("group")
            if grp:
                if "button" in f:
                    filter_button_groups.setdefault(grp, []).append(f["button"])
                elif "checkbox" in f:
                    checkbox_groups.setdefault(grp, []).append(f["checkbox"])

        # Wire filter button groups (allows unchecking)
        for grp, button_names in filter_button_groups.items():
            if len(button_names) >= 2:
                self._wire_at_most_one_group_filter_buttons(button_names, grp)

        # Wire checkbox groups (existing behavior)
        for grp, names in checkbox_groups.items():
            if len(names) == 2:
                self._wire_at_most_one(names[0], names[1])
            elif len(names) > 2:
                self._wire_at_most_one_group(names, grp)

        # ---- Wire up tab buttons for material list filtering (Shaders, Textures, Shading Groups, Utilities) ----
        # These buttons act as tabs to filter what's shown in the material list
        tab_button_names = [
            'materialListShadersButton',
            'materialListTexturesButton',
            'materialListShadingGroupButton',
            'materialListUtilitiesButton',
        ]
        
        # Check if buttons exist before wiring
        for btn_name in tab_button_names:
            btn = self._get_widget(btn_name, QtWidgets.QPushButton)
        
        self._wire_at_most_one_group_buttons(tab_button_names, 'material_list_tabs')
        
        # Set default: check shaders button if none are checked
        shaders_btn = self._get_widget('materialListShadersButton', QtWidgets.QPushButton)
        textures_btn = self._get_widget('materialListTexturesButton', QtWidgets.QPushButton)
        shading_groups_btn = self._get_widget('materialListShadingGroupButton', QtWidgets.QPushButton)
        utilities_btn = self._get_widget('materialListUtilitiesButton', QtWidgets.QPushButton)
        
        button_instances = [btn for btn in (shaders_btn, textures_btn, shading_groups_btn, utilities_btn) if btn]
        if button_instances and not any(btn.isChecked() for btn in button_instances):
            # Default to shaders button when nothing is checked
            target = shaders_btn or button_instances[0]
            if target:
                target.setChecked(True)

        self._current_active_tab = self._get_current_tab_type() or 'shaders'
        self._sync_sort_state_from_tab(self._current_active_tab, update_buttons=False)
        self._update_tab_frames_visibility(self._current_active_tab)
        
        # Update header label frames visibility based on active tab
        show_shaders = (self._current_active_tab == 'shaders')
        show_textures = (self._current_active_tab == 'textures')
        show_shading_groups = (self._current_active_tab == 'shading_groups')
        show_utilities = (self._current_active_tab == 'utilities')
        self._update_header_frames_visibility(show_shaders, show_textures, show_shading_groups, show_utilities)

        # Utility filter button removed - utilities now always show only those connected to shaders

        # --- verify filter button/checkbox hookups once UI is live ---
        def _verify_filters_once():
            for f in self._filter_spec():
                if "button" in f:
                    n = f["button"]
                    w = self._get_widget(n, QtWidgets.QPushButton)
                elif "checkbox" in f:
                    n = f["checkbox"]
                    w = self._get_widget(n, QtWidgets.QCheckBox)
        QtCore.QTimer.singleShot(0, _verify_filters_once)

        # --- Poll fallback: very cheap, only refreshes on change ---
        if not hasattr(self, "_material_poll_timer"):
            self._material_poll_timer = QtCore.QTimer(self)
            self._material_poll_timer.timeout.connect(self._poll_materials_snapshot)
            self._material_poll_timer.start(2000)  # Poll every 2 seconds

    
    def _update_scale_button_states(self):
        """Update enable/disable state of scale buttons based on current scale level."""
        scale_down_btn = self._get_widget('scaleDownListEntriesButton', QtWidgets.QPushButton)
        scale_up_btn = self._get_widget('scaleUpListEntriesButton', QtWidgets.QPushButton)
        
        if scale_down_btn:
            # Disable scale down if already at smallest (level 0)
            scale_down_btn.setEnabled(self._list_entry_scale_level > 0)
        
        if scale_up_btn:
            # Disable scale up if already at largest (level 2)
            scale_up_btn.setEnabled(self._list_entry_scale_level < 2)
    
    def scale_up_list_entries(self):
        """Scale up list entries (small -> medium -> large)."""
        if self._list_entry_scale_level >= 2:
            return  # Already at maximum
        
        self._list_entry_scale_level += 1
        self._update_scale_button_states()
        # Save scale level (will be saved via _save_ui_state)
        self._save_ui_state()
        # Refresh the list to apply new scale to all entries
        self.refresh_materials_list()
    
    def scale_down_list_entries(self):
        """Scale down list entries (large -> medium -> small)."""
        if self._list_entry_scale_level <= 0:
            return  # Already at minimum
        
        self._list_entry_scale_level -= 1
        self._update_scale_button_states()
        # Save scale level (will be saved via _save_ui_state)
        self._save_ui_state()
        # Refresh the list to apply new scale to all entries
        self.refresh_materials_list()
    
    
    def _force_layout_update(self):
        """Force layout update to apply scaling changes immediately."""
        try:
            # Update all scroll areas
            scroll_areas = {}
            for tab_type in ['shaders', 'textures', 'shading_groups', 'utilities']:
                area_key = f'{tab_type}_scroll_area'
                if hasattr(self, area_key):
                    area = getattr(self, area_key)
                    if area:
                        try:
                            from shiboken2 import isValid
                        except Exception:
                            try:
                                from shiboken6 import isValid
                            except Exception:
                                isValid = lambda obj: bool(obj)
                        if isValid(area):
                            scroll_content = area.widget()
                            if scroll_content and isValid(scroll_content):
                                scroll_content.updateGeometry()
                                scroll_content.update()
                                layout = scroll_content.layout()
                                if layout:
                                    layout.invalidate()
                                    layout.activate()
                        area.updateGeometry()
                        area.update()
        except Exception:
            pass
    
    def _apply_list_entry_scale(self):
        """Apply current scale to all existing list entries (icons and text)."""
        if not hasattr(self, '_entry_list') or not self._entry_list:
            return
        
        current_scale = self._list_entry_scale_sizes[self._list_entry_scale_level]
        new_icon_size = current_scale['icon']
        new_font_size = current_scale['font']
        
        # Get isValid function for validation
        try:
            from shiboken2 import isValid
        except Exception:
            try:
                from shiboken6 import isValid
            except Exception:
                isValid = lambda obj: bool(obj)
        
        # Update all icons and text widgets
        for entry in self._entry_list:
            material = entry.get('material')
            if not material:
                continue
            
            # Update swatch icon if it exists
            swatch = entry.get('swatch')
            if swatch and isValid(swatch):
                try:
                    swatch.setFixedSize(new_icon_size, new_icon_size)
                    # Enable scaled contents for MaterialSwatchIcon so it scales without re-rendering
                    widget_class_name = swatch.__class__.__name__
                    if widget_class_name == 'MaterialSwatchIcon':
                        swatch.setScaledContents(True)
                    # Update icon_size property if it exists
                    if hasattr(swatch, 'icon_size'):
                        swatch.icon_size = new_icon_size
                    # For MaterialSwatchIcon, we may need to trigger a resize
                    if hasattr(swatch, 'update'):
                        swatch.update()
                except Exception as e:
                    print(f"[QuickMaterials] Failed to resize swatch for {material}: {e}")
            
            # Update line edit (text widget) font size
            line_edit = entry.get('line_edit')
            if line_edit and isValid(line_edit):
                try:
                    font = line_edit.font()
                    font.setPointSize(new_font_size)
                    line_edit.setFont(font)
                    # Also update minimum height to match font size
                    line_edit.setMinimumHeight(max(22, new_icon_size + 2))
                except Exception as e:
                    print(f"[QuickMaterials] Failed to resize text for {material}: {e}")
            
            # Update container to accommodate larger icons
            container = entry.get('container')
            if container and isValid(container):
                try:
                    # Update minimum height to match icon size
                    container.setMinimumHeight(max(22, new_icon_size + 2))
                    # Update container padding based on scale
                    container.setContentsMargins(
                        current_scale['container_padding'],
                        current_scale['container_padding'],
                        current_scale['container_padding'],
                        current_scale['container_padding']
                    )
                except Exception:
                    pass
            
            # Find and update other icon types (texture, procedural, utility)
            # These are stored in the layout, so we need to search for them
            if container and isValid(container):
                try:
                    layout = container.layout()
                    if layout:
                        # Update layout margins based on scale
                        margin = current_scale['layout_margin']
                        # Ensure margins are integers
                        layout.setContentsMargins(int(margin[0]), int(margin[1]), int(margin[2]), int(margin[3]))
                        
                        # Iterate through layout items to find icon widgets
                        for i in range(layout.count()):
                            item = layout.itemAt(i)
                            if item:
                                widget = item.widget()
                                if widget and isValid(widget):
                                    # Check if it's an icon widget (has icon_size attribute)
                                    # Check by class name or attribute to avoid import issues
                                    is_icon = False
                                    widget_class_name = widget.__class__.__name__
                                    if (hasattr(widget, 'icon_size') or 
                                        widget_class_name in ('TextureIcon', 'ProceduralTextureIcon', 'UtilityNodeIcon', 'ShadingGroupIcon', 'MaterialSwatchIcon')):
                                        is_icon = True
                                    
                                    if is_icon:
                                        try:
                                            widget.setFixedSize(new_icon_size, new_icon_size)
                                            # Enable scaled contents for all icon types
                                            if hasattr(widget, 'setScaledContents'):
                                                widget.setScaledContents(True)
                                            if hasattr(widget, 'icon_size'):
                                                widget.icon_size = new_icon_size
                                            if hasattr(widget, 'update'):
                                                widget.update()
                                        except Exception:
                                            pass
                except Exception:
                    pass
        
        # Update scroll layout spacing based on current scale
        self._update_scroll_layout_spacing()
    
    def _update_scroll_layout_spacing(self):
        """Update spacing in all scroll layouts based on current scale level."""
        current_scale = self._list_entry_scale_sizes[self._list_entry_scale_level]
        new_spacing = current_scale['spacing']
        
        # Find all scroll areas and update their layouts
        try:
            from shiboken2 import isValid
        except Exception:
            try:
                from shiboken6 import isValid
            except Exception:
                isValid = lambda obj: bool(obj)
        
        # Get all scroll areas from the tabs
        scroll_areas = {}
        for tab_type in ['shaders', 'textures', 'shading_groups', 'utilities']:
            area_key = f'{tab_type}_scroll_area'
            if hasattr(self, area_key):
                area = getattr(self, area_key)
                if area and isValid(area):
                    scroll_areas[tab_type] = area
        
        # Update spacing for each scroll area's layout
        for area in scroll_areas.values():
            if not area or not isValid(area):
                continue
            try:
                scroll_content = area.widget()
                if scroll_content and isValid(scroll_content):
                    layout = scroll_content.layout()
                    if layout:
                        layout.setVerticalSpacing(new_spacing)
            except Exception:
                pass




    def update_material_attr_visibility(self):
        """
        Show/hide attribute setter frames depending on selected material type.
        Frames expected:
          - colorPickerFrame
          - roughnessSliderFrame
          - metalnessSliderFrame
          - emissionSliderFrame
          - opacitySliderFrame
          - transmissionSliderFrame
          - subsurfaceSliderFrame
        Rules:
          - standardSurface: color, roughness, metalness, emission, opacity, transmission, subsurface = ON
          - blinn/phong:     color, roughness, emission, opacity = ON; metalness, transmission, subsurface = OFF
          - lambert:        color, emission, opacity = ON; roughness, metalness, transmission, subsurface = OFF
          - surfaceShader:   color = ON; all others = OFF
        """
        t = self.determine_material_type().lower()

        color_on = True
        rough_on = False
        metal_on = False
        emission_on = False
        opacity_on = False
        transmission_on = False
        subsurface_on = False

        if t == 'standardsurface':
            rough_on = True
            metal_on = True
            emission_on = True
            opacity_on = True
            transmission_on = True
            subsurface_on = True
        elif t in ('blinn', 'phong'):
            rough_on = True
            metal_on = False
            emission_on = True
            opacity_on = True
            transmission_on = False
            subsurface_on = False
        elif t == 'lambert':
            rough_on = False
            metal_on = False
            emission_on = True
            opacity_on = True
            transmission_on = False
            subsurface_on = False
        elif t == 'surfaceshader':
            rough_on = False
            metal_on = False
            emission_on = False
            opacity_on = False
            transmission_on = False
            subsurface_on = False

        def _set_vis(name, vis):
            w = self.ui_elements.get(name)
            if w and isValid(w):
                final_vis = bool(vis)
                overrides = getattr(self, '_attribute_visibility_overrides', None)
                if overrides and name in overrides:
                    final_vis = bool(overrides[name])
                else:
                    final_vis = self._read_attribute_visibility_from_settings(name, final_vis)

                # Default: use material type logic
                w.setVisible(final_vis)
                if not final_vis:
                    self._reset_attribute_to_default(name)

        _set_vis('colorPickerFrame', True if color_on else False)
        _set_vis('roughnessSliderFrame', True if rough_on else False)
        _set_vis('metalnessSliderFrame', True if metal_on else False)
        _set_vis('emissionSliderFrame', True if emission_on else False)
        _set_vis('opacitySliderFrame', True if opacity_on else False)
        _set_vis('transmissionSliderFrame', True if transmission_on else False)
        _set_vis('subsurfaceSliderFrame', True if subsurface_on else False)

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

        # Force layout update to process visibility change immediately
        target_widget.updateGeometry()
        if hasattr(target_widget, 'parent') and target_widget.parent():
            target_widget.parent().updateGeometry()

        # Recompute min size and snap after the event loop processes the visibility change
        # Use a small delay when hiding to ensure layout has fully processed the change
        delay = 10 if not visible else 0  # 10ms delay when hiding, immediate when showing
        QtCore.QTimer.singleShot(delay, self.snap_to_minimum)

        if layout_name == "materialCreatorLayout":
            QtCore.QTimer.singleShot(
                delay + 15,
                lambda vis=not visible: self._debug_print_size(f"toggle_layout_visibility materialCreatorLayout -> visible={vis}")
            )

    def set_layout_visibility(self, layout_name, button_name, friendly_name, visible):
        """
        Set the visibility of the specified layout's widgets to a specific state.
        Used during state loading to restore exact visibility state.
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

        target_widget.setVisible(visible)
        if isValid(toggle_button):
            toggle_button.setChecked(visible)
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
                if visible:  # list will be shown
                    self.bottom_spacer.hide()
                else:  # list will be hidden
                    self.bottom_spacer.show()

        # Force layout update to process visibility change immediately
        target_widget.updateGeometry()
        if hasattr(target_widget, 'parent') and target_widget.parent():
            target_widget.parent().updateGeometry()

        # Recompute min size and snap after the event loop processes the visibility change
        # Use a small delay when hiding to ensure layout has fully processed the change
        delay = 10 if not visible else 0  # 10ms delay when hiding, immediate when showing
        QtCore.QTimer.singleShot(delay, self.snap_to_minimum)

        if layout_name == "materialCreatorLayout":
            QtCore.QTimer.singleShot(
                delay + 15,
                lambda vis=visible: self._debug_print_size(f"set_layout_visibility materialCreatorLayout -> visible={vis}")
            )

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

        # Initialize sliders only if not already set (to avoid overriding loaded state)
        if self.ui_elements[hue_name].value() == 0 and self.ui_elements[sat_name].value() == 0 and self.ui_elements[val_name].value() == 0:
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
            
            # Save state when color changes
            self._save_ui_state()

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
        
        # Connect spinbox to save state when changed
        spin.valueChanged.connect(self._save_ui_state)

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

    def setup_emission_slider(self):
        """Configure emission widgets via live lookups."""
        self._link_slider_spinbox('emissionSlider', 'emissionSpinBox', init_value=0.0)

    def setup_opacity_slider(self):
        """Configure opacity widgets via live lookups."""
        self._link_slider_spinbox('opacitySlider', 'opacitySpinBox', init_value=1.0)

    def setup_transmission_slider(self):
        """Configure transmission widgets via live lookups."""
        self._link_slider_spinbox('transmissionSlider', 'transmissionSpinBox', init_value=0.0)

    def setup_subsurface_slider(self):
        """Configure subsurface widgets via live lookups."""
        self._link_slider_spinbox('subsurfaceSlider', 'subsurfaceSpinBox', init_value=0.0)

    def _reset_attribute_to_default(self, frame_name):
        """
        Reset material attribute controls to their default values when the corresponding
        frame is hidden. Ensures hidden attributes do not retain stale values.
        """
        if frame_name == 'colorPickerFrame':
            self._reset_color_controls_to_default()
            return

        spin_defaults = {
            'roughnessSliderFrame': ('roughnessSpinBox', 0.75),
            'metalnessSliderFrame': ('metalnessSpinBox', 0.0),
            'emissionSliderFrame': ('emissionSpinBox', 0.0),
            'opacitySliderFrame': ('opacitySpinBox', 1.0),
            'transmissionSliderFrame': ('transmissionSpinBox', 0.0),
            'subsurfaceSliderFrame': ('subsurfaceSpinBox', 0.0),
        }

        spin_info = spin_defaults.get(frame_name)
        if not spin_info:
            return

        spin_name, default_value = spin_info
        spin_widget = self.ui_elements.get(spin_name)
        if spin_widget:
            try:
                spin_widget.setValue(default_value)
            except Exception as exc:
                pass

    def _reset_color_controls_to_default(self):
        """Reset color controls to fully saturated red, honoring random hue when enabled."""
        color_button = self.ui_elements.get('colorDisplayButton')
        hue_slider = self.ui_elements.get('materialColorHueSlider')
        sat_slider = self.ui_elements.get('materialColorSaturationSlider')
        val_slider = self.ui_elements.get('materialColorValueSlider')
        random_hue_cb = self.ui_elements.get('randomHueCheckbox')

        if random_hue_cb and random_hue_cb.isChecked():
            if sat_slider:
                sat_slider.setValue(100)
            if val_slider:
                val_slider.setValue(100)
            self.set_random_hue_color()
            return

        default_color = QtGui.QColor("#ff0000")  # Fully saturated red
        self.selected_color = default_color

        if hue_slider:
            hue_slider.setValue(0)
        if sat_slider:
            sat_slider.setValue(100)  # Full saturation
        if val_slider:
            val_slider.setValue(100)  # Full brightness

        if color_button:
            self.update_button_color(color_button, self.selected_color)

        try:
            self.update_saturation_slider_gradient()
        except KeyError:
            pass

    def _fix_horizontal_lines(self):
        """Fix horizontal lines that Maya's stylesheet hides by setting properties explicitly."""
        # Common HLine frame names - adjust these based on your Qt Designer names
        hline_names = [
            'separatorFrame1', 'separatorFrame2', 'separatorFrame3',
            'horizontalLine1', 'horizontalLine2', 'horizontalLine3',
            'line1', 'line2', 'line3', 'separator1', 'separator2', 'separator3'
        ]
        
        fixed_count = 0
        
        for frame_name in hline_names:
            frame = self.ui_elements.get(frame_name)
            if frame:
                # Simple approach - just set basic properties
                frame.setFrameShape(QtWidgets.QFrame.HLine)
                frame.setFrameShadow(QtWidgets.QFrame.Sunken)
                frame.setFixedHeight(2)  # Slightly thicker to ensure visibility
                frame.setStyleSheet("""
                    QFrame {
                        background-color: #666666;
                        border: none;
                        border-top: 1px solid #666666;
                        padding: 5px 0px;
                    }
                """)
                
                # Try to adjust parent layout spacing to accommodate margin
                parent_layout = frame.parent().layout() if frame.parent() else None
                if parent_layout and hasattr(parent_layout, 'setVerticalSpacing'):
                    parent_layout.setVerticalSpacing(5)  # Match the margin
                
                fixed_count += 1
        
        # Also try to find any QFrame widgets that might be HLines
        try:
            all_frames = self.findChildren(QtWidgets.QFrame)
            for frame in all_frames:
                if frame.frameShape() == QtWidgets.QFrame.HLine:
                    frame.setFrameShape(QtWidgets.QFrame.HLine)
                    frame.setFrameShadow(QtWidgets.QFrame.Sunken)
                    frame.setFixedHeight(1)
                    frame.setStyleSheet("""
                        QFrame {
                            background-color: #222222;
                            border: none;
                            border-top: 1px solid #222222;
                            padding: 5px 0px;
                        }
                    """)
                    
                    # Try to adjust parent layout spacing to accommodate margin
                    parent_layout = frame.parent().layout() if frame.parent() else None
                    if parent_layout and hasattr(parent_layout, 'setVerticalSpacing'):
                        parent_layout.setVerticalSpacing(5)  # Match the margin
                    
                    fixed_count += 1
        except Exception as e:
            pass
        
        # Force UI update
        self.update()
        QtCore.QTimer.singleShot(100, self.update)  # Delayed update

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
        print("[MATERIAL] Starting material creation...")

        # Open undo chunk at the start to make all operations undoable with a single undo
        cmds.undoInfo(openChunk=True)
        try:
            # Only loads Arnold when standardSurface is selected; otherwise no-op
            if not self.ensure_arnold_plugin():
                print("[MATERIAL] ERROR: Failed to load Arnold plugin (needed for standardSurface).")
                return

            # Check for empty or invalid selection before proceeding
            current_selection = cmds.ls(selection=True) or []
            if not current_selection:
                # Empty selection - show yellow warning
                cmds.inViewMessage(amg="<hl>⚠ Select mesh first to apply materials</hl>", pos="topCenter", fade=True)
                return

            # Build selection units: treat selected groups as single units, and selected meshes as their own units
            selection_units = self.get_selection_units()
            if not selection_units:
                # Check if selection failed due to vertex-only selection
                full_selection = cmds.ls(selection=True, flatten=True) or []
                has_vertex_only = False
                for sel in full_selection:
                    if '.' in sel and '.vtx[' in sel:
                        # Check if it's vertex-only (no faces or edges)
                        has_faces_or_edges = any('f[' in s or 'e[' in s for s in full_selection if '.' in s)
                        if not has_faces_or_edges:
                            has_vertex_only = True
                            break
                
                if has_vertex_only:
                    cmds.inViewMessage(amg="<hl>⚠ Materials must be assigned to faces, not vertices</hl>", pos="topCenter", fade=True)
                else:
                    # Invalid selection (components, non-mesh objects, etc.) - show yellow warning
                    cmds.inViewMessage(amg="<hl>⚠ Select mesh first to apply materials</hl>", pos="topCenter", fade=True)
                return

            is_single_material_for_all = not self.ui_elements.get('materialPerMeshCheckbox').isChecked()
            used_material_names = set()
            materials_created = []

            # Check if we're dealing with component selections
            has_components = any(unit.get('is_component', False) for unit in selection_units)
            
            # Use the current displayed color for material creation
            color_rgb = self.get_current_color_rgb()

            if is_single_material_for_all:
                # Create one material for all targets with the selected color.
                if has_components:
                    # Handle component selections: create one material for all components
                    all_components = []
                    for unit in selection_units:
                        if unit.get('is_component', False):
                            all_components.extend(unit.get('components', []))
                    
                    if all_components:
                        if len(selection_units) == 1:
                            selection_label = selection_units[0]['label']
                        else:
                            # Multiple component selections - use a generic label
                            selection_label = "selection"
                        
                        material_name = self.generate_material(selection_label, color_rgb, used_material_names)
                        if not material_name:
                            return
                        materials_created.append(material_name)
                        
                        # Assign material to all components
                        print(f"[MATERIAL] Assigning {material_name} to {len(all_components)} components...")
                        self.assign_material_to_components(all_components, material_name)
                else:
                    # Handle mesh/group selections (existing logic)
                    all_meshes = []
                    for unit in selection_units:
                        all_meshes.extend(unit.get('meshes', []))
                    
                    if len(selection_units) == 1:
                        selection_label = selection_units[0]['label']
                    else:
                        # When multiple selections are made, use the first selection unit's label
                        if selection_units:
                            selection_label = selection_units[0]['label']
                        else:
                            selection_label = "selection"
                    
                    material_name = self.generate_material(selection_label, color_rgb, used_material_names)
                    if not material_name:
                        return
                    materials_created.append(material_name)

                    for mesh in all_meshes:
                        print(f"[MATERIAL] Assigning {material_name} to mesh: {mesh}")
                        self.assign_material_to_mesh(mesh, material_name)

            else:
                # Create a different material per selection unit (group, mesh, or component selection).
                start_hue = self.selected_color.hueF()
                total_units = len(selection_units)

                for index, unit in enumerate(selection_units):
                    # If random hue is checked, adjust hue for each selection
                    if self.ui_elements['randomHueCheckbox'].isChecked():
                        hue = (start_hue + (index / max(1, total_units))) % 1.0
                        self.selected_color.setHsvF(hue, self.get_current_saturation(), self.get_current_value())
                        color_rgb = self.get_current_color_rgb()

                    selection_label = unit['label']
                    material_name = self.generate_material(selection_label, color_rgb, used_material_names)
                    if not material_name:
                        continue
                    materials_created.append(material_name)

                    # Handle component selections
                    if unit.get('is_component', False):
                        components = unit.get('components', [])
                        if components:
                            print(f"[MATERIAL] Assigning {material_name} to {len(components)} components...")
                            self.assign_material_to_components(components, material_name)
                    else:
                        # Handle mesh/group selections
                        for mesh_name in unit.get('meshes', []):
                            print(f"[MATERIAL] Assigning {material_name} to mesh: {mesh_name}")
                            self.assign_material_to_mesh(mesh_name, material_name)

            # Update the color display after creating the material(s)
            self.update_color_display_after_creation()

            # Refresh the materials list
            self._invalidate_material_cache()  # Clear cache since we added new materials
            self.populate_materials_scroll_area()
            
            # Show success message with material count (only if materials were actually created)
            material_count = len(materials_created)
            if material_count > 0:
                print(f"[MATERIAL] Successfully created {material_count} material(s): {materials_created}")
                if material_count == 1:
                    cmds.inViewMessage(amg="<hl>✔ 1 material created</hl>", pos="topCenter", fade=True)
                else:
                    cmds.inViewMessage(amg=f"<hl>✔ {material_count} materials created</hl>", pos="topCenter", fade=True)
            else:
                print("[MATERIAL] WARNING: No materials were created!")
        finally:
            # Always close the undo chunk, even if there was an error or early return
            cmds.undoInfo(closeChunk=True)

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
        """Retrieve valid mesh transform objects from the current selection, expanding groups.

        Rules:
          - If a group (transform with children) is selected, include all descendant mesh transforms.
          - If both a group and one of its child meshes are selected, do not duplicate; the child under the group is covered by the group selection.
          - Return unique transform paths (short names acceptable) that own mesh shapes.
        """
        selected_transforms = cmds.ls(selection=True, objectsOnly=True) or []
        if not selected_transforms:
            return None

        # Identify selected group transforms
        selected_groups = set()
        for obj in selected_transforms:
            # Treat as group if it has any descendants
            if cmds.listRelatives(obj, children=True):
                selected_groups.add(obj)

        # All descendants of selected groups (to exclude direct duplicates later)
        descendants_of_groups = set()
        for grp in selected_groups:
            for desc in cmds.listRelatives(grp, ad=True, type='transform') or []:
                descendants_of_groups.add(desc)

        # Collect mesh transforms from selected groups
        mesh_targets = set()
        for grp in selected_groups:
            # Find transforms that own a mesh shape anywhere under the group
            for desc in cmds.listRelatives(grp, ad=True, type='transform') or []:
                shapes = cmds.listRelatives(desc, shapes=True, fullPath=True) or []
                if any(cmds.nodeType(s) == 'mesh' for s in shapes):
                    mesh_targets.add(desc)

        # Collect explicitly selected mesh transforms that are NOT descendants of selected groups
        for obj in selected_transforms:
            if obj in descendants_of_groups:
                # Covered by the group's expansion; skip to avoid duplicates
                continue
            shapes = cmds.listRelatives(obj, shapes=True, fullPath=True) or []
            if any(cmds.nodeType(s) == 'mesh' for s in shapes):
                mesh_targets.add(obj)

        if not mesh_targets:
            return None

        # Return deterministic order
        return sorted(mesh_targets)

    def get_selection_units(self):
        """Return a list of selection units, where each unit is:
           - Component selections grouped by parent object: {'label': 'objectName faces', 'components': [...], 'is_component': True}
           - a selected group (transform with descendants) represented as one unit with label=group short name and meshes=all descendant mesh transforms (excluding explicitly selected meshes)
           - a selected mesh transform, represented with label=its short name and meshes=[itself] (even if inside a selected group)

        This enables per-selection creation while treating groups as single selections.
        Components take priority - if components are selected, return component units only.
        """
        # Get full selection including components
        full_selection = cmds.ls(selection=True, flatten=True) or []
        if not full_selection:
            return None
        
        # Check for component selections first (components take priority)
        component_groups = {}  # {object_path: [component_strings...]}
        
        for sel in full_selection:
            # Check if this is a component selection (contains .f[, .vtx[, .e[)
            if '.' in sel:
                parts = sel.split('.', 1)
                if len(parts) == 2:
                    obj_path = parts[0]  # The object path (transform or shape)
                    component_part = parts[1]
                    
                    # Determine if it's a valid component type
                    if component_part.startswith('f[') or component_part.startswith('vtx[') or component_part.startswith('e['):
                        # Normalize the object path to full path for consistent grouping
                        try:
                            # Get the full path of the object
                            full_paths = cmds.ls(obj_path, long=True)
                            if full_paths:
                                normalized_obj = full_paths[0]
                            else:
                                normalized_obj = obj_path
                        except:
                            normalized_obj = obj_path
                        
                        # Group components by their parent object path
                        if normalized_obj not in component_groups:
                            component_groups[normalized_obj] = []
                        component_groups[normalized_obj].append(sel)
        
        # Check for vertex selections - show warning if any vertices are selected
        has_vertices = False
        if component_groups:
            for parent_obj, components in component_groups.items():
                for comp in components:
                    if '.vtx[' in comp:
                        has_vertices = True
                        break
                if has_vertices:
                    break
        
        # Create component units if we have components (but continue to also process meshes/groups)
        component_units = []
        if component_groups:
            for parent_obj, components in component_groups.items():
                # Skip vertex selections - they'll get a warning
                comp_types = set()
                for comp in components:
                    if '.f[' in comp:
                        comp_types.add('faces')
                    elif '.vtx[' in comp:
                        comp_types.add('vertices')
                    elif '.e[' in comp:
                        comp_types.add('edges')
                
                # Skip if only vertices
                if 'vertices' in comp_types and len(comp_types) == 1:
                    continue
                
                # Get the transform node name for the label - use mesh name even if in a group
                try:
                    # parent_obj is the object path from the component selection
                    # Get the transform node (mesh name) even if it's in a group
                    if '|' in parent_obj:
                        # Already a full path - extract the mesh name (last part)
                        transform_node = parent_obj
                    else:
                        # Short name - get full path first
                        full_paths = cmds.ls(parent_obj, long=True)
                        transform_node = full_paths[0] if full_paths else parent_obj
                    
                    # Ensure we have the transform (not shape)
                    shapes_check = cmds.listRelatives(transform_node, parent=True, type='transform', fullPath=True)
                    if shapes_check:
                        transform_node = shapes_check[0]
                    
                except Exception as e:
                    transform_node = parent_obj
                
                # Get short name (mesh name) for label - use the mesh name, not group name
                short_name = transform_node.split('|')[-1].split(':')[-1]
                
                # Get component type(s) for label - use format "meshName_faces" for material naming
                # Use first component type for material naming (most common case is single type)
                primary_type = sorted(comp_types)[0] if comp_types else 'components'
                # Format: "meshName_faces" for material naming
                label = f"{short_name}_{primary_type}" if primary_type else short_name
                
                component_units.append({
                    'label': label,
                    'components': components,
                    'is_component': True,
                    'parent_object': parent_obj
                })
        # Get transforms from selection - try multiple methods to ensure we catch all selections
        selected_transforms = []
        
        # Method 1: Use objectsOnly (most reliable for transforms)
        selected_transforms_raw = cmds.ls(selection=True, objectsOnly=True) or []
        for obj in selected_transforms_raw:
            try:
                full_paths = cmds.ls(obj, long=True)
                if full_paths and full_paths[0] not in selected_transforms:
                    selected_transforms.append(full_paths[0])
                elif obj not in selected_transforms:
                    selected_transforms.append(obj)
            except Exception as e:
                if obj not in selected_transforms:
                    selected_transforms.append(obj)
        
        # Method 2: If objectsOnly didn't return anything, check full_selection for transforms
        if not selected_transforms:
            for sel in full_selection:
                if '.' not in sel:  # Not a component
                    try:
                        # Check if it's a transform
                        node_type = cmds.nodeType(sel)
                        if node_type == 'transform':
                            full_paths = cmds.ls(sel, long=True)
                            if full_paths and full_paths[0] not in selected_transforms:
                                selected_transforms.append(full_paths[0])
                            elif sel not in selected_transforms:
                                selected_transforms.append(sel)
                        else:
                            # Might be a shape - get parent transform
                            transforms = cmds.listRelatives(sel, parent=True, type='transform', fullPath=True)
                            if transforms and transforms[0] not in selected_transforms:
                                selected_transforms.append(transforms[0])
                    except Exception as e:
                        pass
        
        if not selected_transforms:
            return None

        # Identify groups in the normalized selection (a group is any transform with TRANSFORM children, not just shapes)
        selected_groups = []
        for obj in selected_transforms:
            try:
                # Check what type of children it has
                transform_children = cmds.listRelatives(obj, children=True, type='transform') or []
                
                # Only consider it a group if it has transform children (not just shape children)
                if transform_children:
                    selected_groups.append(obj)  # obj is already normalized to full path
            except Exception as e:
                pass

        # Build sets for tracking - all paths are already normalized to full paths
        # - All descendants of selected groups
        descendants_of_groups = set()
        for grp in selected_groups:
            # grp is already a full path
            for desc in cmds.listRelatives(grp, ad=True, type='transform', fullPath=True) or []:
                descendants_of_groups.add(desc)
        
        # - Explicitly selected meshes (for exclusion from group units)
        # These are meshes that are explicitly selected (not just part of a group)
        explicitly_selected_meshes = set()
        for obj in selected_transforms:
            if obj not in selected_groups:
                # obj is already normalized to full path
                try:
                    shapes = cmds.listRelatives(obj, shapes=True, fullPath=True) or []
                    has_mesh = any(cmds.nodeType(s) == 'mesh' for s in shapes)
                    if has_mesh:
                        explicitly_selected_meshes.add(obj)  # Use normalized path
                except Exception as e:
                    pass

        units = []

        # First, add group units (as single units) - but EXCLUDE explicitly selected meshes
        for grp in selected_groups:
            # grp is already normalized to full path
            label = grp.split('|')[-1].split(':')[-1]
            meshes = []
            descendants = cmds.listRelatives(grp, ad=True, type='transform', fullPath=True) or []
            for desc in descendants:
                # desc is already a full path from listRelatives
                # Skip if this mesh is explicitly selected (will be its own unit)
                if desc in explicitly_selected_meshes:
                    continue
                shapes = cmds.listRelatives(desc, shapes=True, fullPath=True) or []
                has_mesh = any(cmds.nodeType(s) == 'mesh' for s in shapes)
                if has_mesh:
                    meshes.append(desc)
            if meshes:
                unit = {'label': label, 'meshes': sorted(set(meshes))}
                units.append(unit)

        # Next, add individually selected meshes (even if they're inside a selected group)
        for obj in selected_transforms:
            if obj in selected_groups:
                # Skip groups, already handled above
                continue
            # obj is already normalized to full path
            # Check if this object has mesh shapes
            try:
                shapes = cmds.listRelatives(obj, shapes=True, fullPath=True) or []
                has_mesh = any(cmds.nodeType(s) == 'mesh' for s in shapes)
                if has_mesh:
                    label = obj.split('|')[-1].split(':')[-1]
                    unit = {'label': label, 'meshes': [obj]}
                    units.append(unit)
            except Exception as e:
                # Skip objects that can't be queried
                continue

        # Combine component units with mesh/group units
        all_units = component_units + units
        
        if not all_units:
            return None

        return all_units

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
          - standardSurface: specularRoughness (0..1), metalness (0..1), emissionColor+emissionWeight, opacity, transmission, subsurfaceWeight+subsurfaceColor
          - blinn:           eccentricity ≈ roughness (0..1); specularRollOff = 1-roughness; incandescence, transparency = 1-opacity
          - phong:           cosinePower ~ (1-roughness)*100 (min 2); specularColor = (1-roughness) grayscale; incandescence, transparency = 1-opacity
          - lambert:         incandescence, transparency = 1-opacity
          - surfaceShader:   flat pass-through
        """
        try:
            r = max(0.0, min(1.0, float(roughness)))
            inv = 1.0 - r  # Examples: 0.75 -> 0.25, 0.95 -> 0.05, 0.50 -> 0.50

            # Get color for emission and subsurface
            color_rgb = self.get_current_color_rgb()

            if material_type == 'standardSurface':
                cmds.setAttr(f"{material_name}.specularRoughness", r)
                metal_spin = self.ui_elements.get('metalnessSpinBox')
                metal_val = float(metal_spin.value()) if metal_spin else 0.0
                cmds.setAttr(f"{material_name}.metalness", max(0.0, min(1.0, metal_val)))
                
                # Emission - set emission color and weight
                emission_spin = self.ui_elements.get('emissionSpinBox')
                emission_val = float(emission_spin.value()) if emission_spin else 0.0
                if emission_val > 0:
                    cmds.setAttr(f"{material_name}.emissionColor", *color_rgb, type="double3")
                    cmds.setAttr(f"{material_name}.emission", emission_val)
                
                # Opacity - standardSurface uses opacity as RGB color
                opacity_spin = self.ui_elements.get('opacitySpinBox')
                opacity_val = float(opacity_spin.value()) if opacity_spin else 1.0
                cmds.setAttr(f"{material_name}.opacity", opacity_val, opacity_val, opacity_val, type="double3")
                
                # Transmission
                transmission_spin = self.ui_elements.get('transmissionSpinBox')
                transmission_val = float(transmission_spin.value()) if transmission_spin else 0.0
                cmds.setAttr(f"{material_name}.transmission", transmission_val)
                
                # Subsurface - set subsurface weight and color
                subsurface_spin = self.ui_elements.get('subsurfaceSpinBox')
                subsurface_val = float(subsurface_spin.value()) if subsurface_spin else 0.0
                if subsurface_val > 0:
                    cmds.setAttr(f"{material_name}.subsurface", subsurface_val)
                    cmds.setAttr(f"{material_name}.subsurfaceColor", *color_rgb, type="double3")

            elif material_type == 'blinn':
                # Roughness → eccentricity, inverse → specularRollOff
                cmds.setAttr(f"{material_name}.eccentricity", r)
                cmds.setAttr(f"{material_name}.specularRollOff", inv)
                
                # Emission - use incandescence for legacy materials
                emission_spin = self.ui_elements.get('emissionSpinBox')
                emission_val = float(emission_spin.value()) if emission_spin else 0.0
                if emission_val > 0:
                    cmds.setAttr(f"{material_name}.incandescence", *color_rgb, type="double3")
                
                # Opacity - reverse value for transparency (opacity 1.0 = transparency 0.0)
                opacity_spin = self.ui_elements.get('opacitySpinBox')
                opacity_val = float(opacity_spin.value()) if opacity_spin else 1.0
                transparency_val = 1.0 - opacity_val
                cmds.setAttr(f"{material_name}.transparency", transparency_val, transparency_val, transparency_val, type="double3")

            elif material_type == 'phong':
                # Roughness inverse → shininess (cosinePower) and specularColor intensity
                power = max(2.0, inv * 100.0)  # keep a floor to avoid super-broad lobes
                cmds.setAttr(f"{material_name}.cosinePower", power)
                cmds.setAttr(f"{material_name}.specularColor", inv, inv, inv, type="double3")
                
                # Emission - use incandescence for legacy materials
                emission_spin = self.ui_elements.get('emissionSpinBox')
                emission_val = float(emission_spin.value()) if emission_spin else 0.0
                if emission_val > 0:
                    cmds.setAttr(f"{material_name}.incandescence", *color_rgb, type="double3")
                
                # Opacity - reverse value for transparency (opacity 1.0 = transparency 0.0)
                opacity_spin = self.ui_elements.get('opacitySpinBox')
                opacity_val = float(opacity_spin.value()) if opacity_spin else 1.0
                transparency_val = 1.0 - opacity_val
                cmds.setAttr(f"{material_name}.transparency", transparency_val, transparency_val, transparency_val, type="double3")

            elif material_type == 'lambert':
                # Emission - use incandescence for legacy materials
                emission_spin = self.ui_elements.get('emissionSpinBox')
                emission_val = float(emission_spin.value()) if emission_spin else 0.0
                if emission_val > 0:
                    cmds.setAttr(f"{material_name}.incandescence", *color_rgb, type="double3")
                
                # Opacity - reverse value for transparency (opacity 1.0 = transparency 0.0)
                opacity_spin = self.ui_elements.get('opacitySpinBox')
                opacity_val = float(opacity_spin.value()) if opacity_spin else 1.0
                transparency_val = 1.0 - opacity_val
                cmds.setAttr(f"{material_name}.transparency", transparency_val, transparency_val, transparency_val, type="double3")

            elif material_type == 'surfaceShader':
                # Nothing to set for surfaceShader
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


    def _make_selection_label(self, mesh_list):
        """Build a concise selection label from a list of mesh transform names.

        Examples:
          - ["pCube1"]            -> "pCube1"
          - ["a","b","c"]       -> "a_b_c"
        """
        if not mesh_list:
            return "selection"
        # Normalize to short names
        short = [m.split('|')[-1] for m in mesh_list]
        return "_".join(short)


    def generate_material(self, selection_label, color_rgb, used_material_names):
        # Normalize type once
        mat_type = self.determine_material_type()
        mat_key = mat_type  # already normalized to 'standardSurface' or 'surfaceShader'

        # Name
        material_name = self.get_unique_material_name(selection_label, mat_key, used_material_names, color_rgb)

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

        # SG hookup - create shading group and connect material BEFORE setting attributes
        print(f"[MATERIAL] Creating shading group for {material_name}...")
        shading_group = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=f"{material_name}SG")
        print(f"[MATERIAL] Shading group created: {shading_group}")
        
        try:
            cmds.connectAttr(material + ".outColor", shading_group + ".surfaceShader", force=True)
            print(f"[MATERIAL] Connected {material}.outColor -> {shading_group}.surfaceShader")
        except RuntimeError as e:
            print(f"[MATERIAL] ERROR: Failed to connect {material}.outColor to {shading_group}.surfaceShader: {e}")
            cmds.warning(f"Failed to connect {material}.outColor to {shading_group}.surfaceShader: {e}")
            return None

        # Verify the shading group exists and connection is valid before proceeding
        if not cmds.objExists(shading_group):
            print(f"[MATERIAL] ERROR: Shading group {shading_group} was not created properly")
            cmds.warning(f"Shading group {shading_group} was not created properly")
            return None
        
        # Verify the connection exists (check both short and long names)
        connections = cmds.listConnections(shading_group + ".surfaceShader", source=True, destination=False) or []
        material_short = material.split('|')[-1]
        connection_found = False
        for conn in connections:
            if conn == material or conn.split('|')[-1] == material_short:
                connection_found = True
                break
        if not connection_found:
            print(f"[MATERIAL] ERROR: Material {material} is not properly connected to shading group {shading_group}")
            print(f"[MATERIAL]   Connections found: {connections}")
            cmds.warning(f"Material {material} is not properly connected to shading group {shading_group}")
            return None
        
        print(f"[MATERIAL] Verified shading group {shading_group} is properly connected to {material}")

        # Attributes: map roughness -> specularRoughness and add metalness (default 0)
        roughness = self.ui_elements.get('roughnessSpinBox').value() if self.ui_elements.get(
            'roughnessSpinBox') else 0.75
        self.set_material_attributes(material, mat_key, roughness)

        return material_name

    def get_unique_material_name(self, selection_label, material_type, used_material_names, color_rgb):
        """Generate a unique material name with tokens: (selection), (shader), (scene), (project), (color), (name).

        Back-compat: (mesh), (mat_type) and (current) are supported as aliases.
        """
        custom_name_template = self.ui_elements.get('materialNamingLineEdit').text().strip() if self.ui_elements.get(
            'materialNamingLineEdit') else ""

        # Resolve tokens
        try:
            scene_path = cmds.file(q=True, sn=True) or ""
            scene_name = os.path.splitext(os.path.basename(scene_path))[0] if scene_path else "untitled"
        except Exception:
            scene_name = "untitled"

        try:
            project_root = cmds.workspace(q=True, rd=True) or ""
            project_name = os.path.basename(os.path.normpath(project_root)) if project_root else "project"
        except Exception:
            project_name = "project"

        # Color token: use readable color name
        try:
            r, g, b = [max(0, min(1, float(c))) for c in (color_rgb[0], color_rgb[1], color_rgb[2])]
            color_name = self._readable_color_name(int(r*255), int(g*255), int(b*255))
        except Exception:
            color_name = "White"

        tokens = {
            "(selection)": selection_label,
            "(mesh)": selection_label,  # back-compat
            "(shader)": material_type,
            "(mat_type)": material_type,  # back-compat
            "(scene)": scene_name,
            "(project)": project_name,
            "(color)": color_name,
            "(name)": selection_label,
            "(current)": selection_label,  # back-compat
        }
        tokens_lower = {k.lower(): v for k, v in tokens.items()}
        pattern = re.compile("|".join(re.escape(k) for k in tokens_lower.keys()), re.IGNORECASE)

        if custom_name_template:
            def _replace(match):
                return tokens_lower.get(match.group(0).lower(), match.group(0))

            base_material_name = pattern.sub(_replace, custom_name_template)
        else:
            base_material_name = f"M_{selection_label}_{material_type}"

        # Apply optional prefix/suffix from embedded settings
        prefix = getattr(self, '_material_name_prefix', None)
        suffix = getattr(self, '_material_name_suffix', None)
        if prefix is None or suffix is None:
            mc_settings = self._load_settings_cache().get('material_creator', {})
            prefix = mc_settings.get('name_prefix', "") or ""
            suffix = mc_settings.get('name_suffix', "") or ""
            self._material_name_prefix = prefix
            self._material_name_suffix = suffix
        if prefix:
            base_material_name = f"{prefix}{base_material_name}"
        if suffix:
            base_material_name = f"{base_material_name}{suffix}"

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
        # Find the actual shading group that the material is connected to (Maya may have appended a number)
        shading_groups = cmds.listConnections(material_name + ".outColor", destination=True, type="shadingEngine") or []
        if not shading_groups:
            print(f"[ASSIGN] ERROR: No shading group found connected to material {material_name}")
            cmds.warning(f"No shading group found connected to material {material_name}")
            return
        
        shading_group = shading_groups[0]  # Use the first connected shading group
        print(f"[ASSIGN] Assigning {material_name} (SG: {shading_group}) to mesh: {mesh_name}")
        
        assignment_successful = False
        try:
            cmds.sets(mesh_name, edit=True, forceElement=shading_group)
            assignment_successful = True
            print(f"[ASSIGN] SUCCESS: Assigned {material_name} to {mesh_name}")
        except RuntimeError as e:
            print(f"[ASSIGN] ERROR: Failed to assign material: {e}")
            cmds.warning(f"Failed to assign material: {e}")
        
        # If assignment was successful, update the material widget's unused highlighting
        if assignment_successful:
            self._update_material_unused_status(material_name, is_used=True)

    def assign_material_to_components(self, components, material_name):
        """Assign the created material to the given component selections (faces, edges, vertices)."""
        # Find the actual shading group that the material is connected to (Maya may have appended a number)
        shading_groups = cmds.listConnections(material_name + ".outColor", destination=True, type="shadingEngine") or []
        if not shading_groups:
            print(f"[ASSIGN] ERROR: No shading group found connected to material {material_name}")
            cmds.warning(f"No shading group found connected to material {material_name}")
            return
        
        shading_group = shading_groups[0]  # Use the first connected shading group
        component_count = len(components) if isinstance(components, list) else 1
        print(f"[ASSIGN] Assigning {material_name} (SG: {shading_group}) to {component_count} components")
        
        assignment_successful = False
        try:
            
            # components can be a list or a single component string
            if isinstance(components, list):
                # Batch assign all components at once for better performance
                if components:
                    try:
                        cmds.sets(components, edit=True, forceElement=shading_group)
                        assignment_successful = True
                        print(f"[ASSIGN] SUCCESS: Batch assigned {material_name} to {len(components)} components")
                    except Exception as e:
                        print(f"[ASSIGN] Batch assignment failed, trying one by one: {e}")
                        # Fallback: assign one by one if batch fails
                        for comp in components:
                            try:
                                cmds.sets(comp, edit=True, forceElement=shading_group)
                                assignment_successful = True
                            except Exception as comp_err:
                                print(f"[ASSIGN] ERROR: Failed to assign material to component {comp}: {comp_err}")
                                cmds.warning(f"Failed to assign material to component {comp}: {comp_err}")
            else:
                cmds.sets(components, edit=True, forceElement=shading_group)
                assignment_successful = True
                print(f"[ASSIGN] SUCCESS: Assigned {material_name} to component: {components}")
        except RuntimeError as e:
            print(f"[ASSIGN] ERROR: Failed to assign material to components: {e}")
            cmds.warning(f"Failed to assign material to components: {e}")
        except Exception as e:
            print(f"[ASSIGN] ERROR: Failed to assign material to components: {e}")
            cmds.warning(f"Failed to assign material to components: {e}")
        
        # If assignment was successful, update the material widget's unused highlighting
        if assignment_successful:
            self._update_material_unused_status(material_name, is_used=True)

    def _readable_color_name(self, r, g, b):
        """Return a human-readable color name closest to the given RGB.

        Uses a compact subset of CSS color names for readability without external deps.
        """
        palette = [
            ("AliceBlue", (240, 248, 255)),
            ("AntiqueWhite", (250, 235, 215)),
            ("Aqua", (0, 255, 255)),
            ("Aquamarine", (127, 255, 212)),
            ("Azure", (240, 255, 255)),
            ("Beige", (245, 245, 220)),
            ("Bisque", (255, 228, 196)),
            ("Black", (0, 0, 0)),
            ("BlanchedAlmond", (255, 235, 205)),
            ("Blue", (0, 0, 255)),
            ("BlueViolet", (138, 43, 226)),
            ("Brown", (165, 42, 42)),
            ("BurlyWood", (222, 184, 135)),
            ("CadetBlue", (95, 158, 160)),
            ("Chartreuse", (127, 255, 0)),
            ("Chocolate", (210, 105, 30)),
            ("Coral", (255, 127, 80)),
            ("CornflowerBlue", (100, 149, 237)),
            ("Cornsilk", (255, 248, 220)),
            ("Crimson", (220, 20, 60)),
            ("Cyan", (0, 255, 255)),
            ("DarkBlue", (0, 0, 139)),
            ("DarkCyan", (0, 139, 139)),
            ("DarkGoldenrod", (184, 134, 11)),
            ("DarkGray", (169, 169, 169)),
            ("DarkGreen", (0, 100, 0)),
            ("DarkKhaki", (189, 183, 107)),
            ("DarkMagenta", (139, 0, 139)),
            ("DarkOliveGreen", (85, 107, 47)),
            ("DarkOrange", (255, 140, 0)),
            ("DarkOrchid", (153, 50, 204)),
            ("DarkRed", (139, 0, 0)),
            ("DarkSalmon", (233, 150, 122)),
            ("DarkSeaGreen", (143, 188, 143)),
            ("DarkSlateBlue", (72, 61, 139)),
            ("DarkSlateGray", (47, 79, 79)),
            ("DarkTurquoise", (0, 206, 209)),
            ("DarkViolet", (148, 0, 211)),
            ("DeepPink", (255, 20, 147)),
            ("DeepSkyBlue", (0, 191, 255)),
            ("DimGray", (105, 105, 105)),
            ("DodgerBlue", (30, 144, 255)),
            ("FireBrick", (178, 34, 34)),
            ("FloralWhite", (255, 250, 240)),
            ("ForestGreen", (34, 139, 34)),
            ("Fuchsia", (255, 0, 255)),
            ("Gainsboro", (220, 220, 220)),
            ("GhostWhite", (248, 248, 255)),
            ("Gold", (255, 215, 0)),
            ("Goldenrod", (218, 165, 32)),
            ("Gray", (128, 128, 128)),
            ("Green", (0, 128, 0)),
            ("GreenYellow", (173, 255, 47)),
            ("HoneyDew", (240, 255, 240)),
            ("HotPink", (255, 105, 180)),
            ("IndianRed", (205, 92, 92)),
            ("Indigo", (75, 0, 130)),
            ("Ivory", (255, 255, 240)),
            ("Khaki", (240, 230, 140)),
            ("Lavender", (230, 230, 250)),
            ("LavenderBlush", (255, 240, 245)),
            ("LawnGreen", (124, 252, 0)),
            ("LemonChiffon", (255, 250, 205)),
            ("LightBlue", (173, 216, 230)),
            ("LightCoral", (240, 128, 128)),
            ("LightCyan", (224, 255, 255)),
            ("LightGoldenrodYellow", (250, 250, 210)),
            ("LightGray", (211, 211, 211)),
            ("LightGreen", (144, 238, 144)),
            ("LightPink", (255, 182, 193)),
            ("LightSalmon", (255, 160, 122)),
            ("LightSeaGreen", (32, 178, 170)),
            ("LightSkyBlue", (135, 206, 250)),
            ("LightSlateGray", (119, 136, 153)),
            ("LightSteelBlue", (176, 196, 222)),
            ("LightYellow", (255, 255, 224)),
            ("Lime", (0, 255, 0)),
            ("LimeGreen", (50, 205, 50)),
            ("Linen", (250, 240, 230)),
            ("Magenta", (255, 0, 255)),
            ("Maroon", (128, 0, 0)),
            ("MediumAquamarine", (102, 205, 170)),
            ("MediumBlue", (0, 0, 205)),
            ("MediumOrchid", (186, 85, 211)),
            ("MediumPurple", (147, 112, 219)),
            ("MediumSeaGreen", (60, 179, 113)),
            ("MediumSlateBlue", (123, 104, 238)),
            ("MediumSpringGreen", (0, 250, 154)),
            ("MediumTurquoise", (72, 209, 204)),
            ("MediumVioletRed", (199, 21, 133)),
            ("MidnightBlue", (25, 25, 112)),
            ("MintCream", (245, 255, 250)),
            ("MistyRose", (255, 228, 225)),
            ("Moccasin", (255, 228, 181)),
            ("NavajoWhite", (255, 222, 173)),
            ("Navy", (0, 0, 128)),
            ("OldLace", (253, 245, 230)),
            ("Olive", (128, 128, 0)),
            ("OliveDrab", (107, 142, 35)),
            ("Orange", (255, 165, 0)),
            ("OrangeRed", (255, 69, 0)),
            ("Orchid", (218, 112, 214)),
            ("PaleGoldenrod", (238, 232, 170)),
            ("PaleGreen", (152, 251, 152)),
            ("PaleTurquoise", (175, 238, 238)),
            ("PaleVioletRed", (219, 112, 147)),
            ("PapayaWhip", (255, 239, 213)),
            ("PeachPuff", (255, 218, 185)),
            ("Peru", (205, 133, 63)),
            ("Pink", (255, 192, 203)),
            ("Plum", (221, 160, 221)),
            ("PowderBlue", (176, 224, 230)),
            ("Purple", (128, 0, 128)),
            ("RebeccaPurple", (102, 51, 153)),
            ("Red", (255, 0, 0)),
            ("RosyBrown", (188, 143, 143)),
            ("RoyalBlue", (65, 105, 225)),
            ("SaddleBrown", (139, 69, 19)),
            ("Salmon", (250, 128, 114)),
            ("SandyBrown", (244, 164, 96)),
            ("SeaGreen", (46, 139, 87)),
            ("SeaShell", (255, 245, 238)),
            ("Sienna", (160, 82, 45)),
            ("Silver", (192, 192, 192)),
            ("SkyBlue", (135, 206, 235)),
            ("SlateBlue", (106, 90, 205)),
            ("SlateGray", (112, 128, 144)),
            ("Snow", (255, 250, 250)),
            ("SpringGreen", (0, 255, 127)),
            ("SteelBlue", (70, 130, 180)),
            ("Tan", (210, 180, 140)),
            ("Teal", (0, 128, 128)),
            ("Thistle", (216, 191, 216)),
            ("Tomato", (255, 99, 71)),
            ("Turquoise", (64, 224, 208)),
            ("Violet", (238, 130, 238)),
            ("Wheat", (245, 222, 179)),
            ("White", (255, 255, 255)),
            ("WhiteSmoke", (245, 245, 245)),
            ("Yellow", (255, 255, 0)),
            ("YellowGreen", (154, 205, 50)),
        ]

        best_name = "White"
        best_dist = 1e9
        for name, (pr, pg, pb) in palette:
            dr = pr - r
            dg = pg - g
            db = pb - b
            d = dr*dr + dg*dg + db*db
            if d < best_dist:
                best_dist = d
                best_name = name
        return best_name

    # ------------------------------------------------------------------
    # UI State Management
    # ------------------------------------------------------------------
    
    def _get_state_file_path(self):
        """Get the path to the state file."""
        script_dir = os.path.dirname(__file__)
        return os.path.join(script_dir, "quick_materials_state.json")
    
    def _load_ui_state(self):
        """Load UI state from file."""
        # Debug: print(f"[UI_STATE] Loading UI state from: {self.state_file_path}")
        # Loading flag is already set during initialization
        self._begin_silent_refresh()
        self._initial_populate_done = False
        state = {}
        try:
            if not os.path.exists(self.state_file_path):
                # Debug: print("[UI_STATE] No state file found, using defaults")
                self._loading_state = False
                state = {}
            else:
                with open(self.state_file_path, 'r') as f:
                    state = json.load(f)
                # Debug: print(f"[UI_STATE] State file loaded successfully")
            
            # Set default material naming template if not in state
            if 'material_creator' not in state or 'material_naming_template' not in state.get('material_creator', {}):
                material_naming_edit = self.ui_elements.get('materialNamingLineEdit')
                if material_naming_edit:
                    material_naming_edit.setText('(selection)')
            
            if 'material_creator' in state:
                mc_state = state['material_creator']
                # Debug: print(f"[UI_STATE] Loading material creator state: type={mc_state.get('material_type', 'N/A')}, color=({mc_state.get('color', {}).get('r', 0)}, {mc_state.get('color', {}).get('g', 0)}, {mc_state.get('color', {}).get('b', 0)})")
                
                # Material type
                if 'material_type' in mc_state:
                    combo = self.ui_elements.get('materialTypeComboBox')
                    if combo:
                        combo.setCurrentText(mc_state['material_type'])
                
                # Color settings
                if 'color' in mc_state:
                    color = mc_state['color']
                    self.selected_color = QtGui.QColor(color['r'], color['g'], color['b'])
                    color_button = self.ui_elements.get('colorDisplayButton')
                    if color_button:
                        self.update_button_color(color_button, self.selected_color)
                
                # Slider values
                for slider_name in ['materialColorHueSlider', 'materialColorSaturationSlider', 'materialColorValueSlider']:
                    if slider_name in mc_state:
                        slider = self.ui_elements.get(slider_name)
                        if slider:
                            slider.setValue(mc_state[slider_name])
                
                # Spinbox values
                for spin_name in ['roughnessSpinBox', 'metalnessSpinBox', 'emissionSpinBox', 'opacitySpinBox', 'transmissionSpinBox', 'subsurfaceSpinBox']:
                    if spin_name in mc_state:
                        spin = self.ui_elements.get(spin_name)
                        if spin:
                            spin.setValue(mc_state[spin_name])
                
                # Checkboxes
                for cb_name in ['materialPerMeshCheckbox', 'randomHueCheckbox']:
                    if cb_name in mc_state:
                        cb = self.ui_elements.get(cb_name)
                        if cb:
                            cb.setChecked(mc_state[cb_name])
                
                # Material naming template
                line_edit = self.ui_elements.get('materialNamingLineEdit')
                if line_edit:
                    if 'material_naming_template' in mc_state:
                        line_edit.setText(mc_state['material_naming_template'])
                    else:
                        # Default if not in state
                        line_edit.setText('(selection)')
                
                # Load attribute frame visibility from settings (quick_materials_settings.json)
                # Also check the settings file for attribute frame visibility
                try:
                    script_dir = os.path.dirname(__file__)
                    settings_path = os.path.join(script_dir, "settings", "quick_materials_settings.json")
                    if os.path.exists(settings_path):
                        with open(settings_path, "r") as f:
                            all_settings = json.load(f)
                            settings_mc = all_settings.get('material_creator', {})
                            
                            # Map frame names to setting keys
                            attribute_frames = {
                                'colorPickerFrame': 'attribute_frame_visible_colorPickerFrame',
                                'roughnessSliderFrame': 'attribute_frame_visible_roughnessSliderFrame',
                                'metalnessSliderFrame': 'attribute_frame_visible_metalnessSliderFrame',
                                'emissionSliderFrame': 'attribute_frame_visible_emissionSliderFrame',
                                'opacitySliderFrame': 'attribute_frame_visible_opacitySliderFrame',
                                'transmissionSliderFrame': 'attribute_frame_visible_transmissionSliderFrame',
                                'subsurfaceSliderFrame': 'attribute_frame_visible_subsurfaceSliderFrame'
                            }
                            
                            for frame_name, setting_key in attribute_frames.items():
                                if setting_key in settings_mc:
                                    frame = self.findChild(QtWidgets.QWidget, frame_name)
                                    if frame:
                                        final_vis = bool(settings_mc[setting_key])
                                        frame.setVisible(final_vis)
                                        if not final_vis:
                                            try:
                                                self._reset_attribute_to_default(frame_name)
                                            except Exception as exc:
                                                pass
                            
                            # Refresh minimum size and snap after loading attribute frame visibility
                            QtCore.QTimer.singleShot(200, self.snap_to_minimum)
                except Exception as e:
                    pass

                if 'material_creator_settings_visible' in mc_state:
                    self._set_settings_frame_visibility(
                        'materialCreatorSettingsButton',
                        bool(mc_state['material_creator_settings_visible']),
                        trigger_snap=False
                    )
            
            # Load material list settings
            if 'material_list' in state:
                ml_state = state['material_list']
                # Debug: print(f"[UI_STATE] Loading material list state: tab={ml_state.get('active_tab', 'N/A')}, sort={ml_state.get('sort_mode', 'N/A')}")
                
                # Sorting
                if 'sort_mode' in ml_state:
                    self._sort_mode = ml_state['sort_mode']
                if 'sort_desc' in ml_state:
                    self._sort_desc = ml_state['sort_desc']
                
                # Filter checkboxes (some entries are buttons only)
                for filter_spec in self.MATERIAL_FILTERS:
                    cb_name = filter_spec.get('checkbox')
                    if not cb_name:
                        continue
                    if cb_name in ml_state:
                        cb = self.ui_elements.get(cb_name)
                        if cb:
                            cb.setChecked(ml_state[cb_name])
                
                # Node type show checkboxes
                for checkbox_name in ['showTexturesCheckbox', 'showProceduralTexturesCheckbox', 'showShadingGroupsCheckbox']:
                    if checkbox_name in ml_state:
                        cb = self.ui_elements.get(checkbox_name)
                        if cb:
                            cb.setChecked(ml_state[checkbox_name])
                
                # Material list option checkboxes
                for checkbox_name in ['hideNamespacesCheckbox', 'highlightUnusedCheckbox', 'showIconsCheckbox', 'showShaderSwatchesCheckbox', 'showOtherIconsCheckbox']:
                    if checkbox_name in ml_state:
                        cb = self.ui_elements.get(checkbox_name)
                        if cb:
                            cb.setChecked(ml_state[checkbox_name])
                
                # Material list options button
                if 'material_list_options_visible' in ml_state:
                    options_btn = self.ui_elements.get('materialListSettingsButton')
                    if options_btn:
                        options_btn.setChecked(ml_state['material_list_options_visible'])
                if 'material_filters_visible' in ml_state:
                    filters_btn = self.ui_elements.get('materialFiltersButton')
                    if filters_btn:
                        filters_btn.setChecked(ml_state['material_filters_visible'])
                
                # Load entry scale level
                if 'entry_scale_level' in ml_state:
                    scale_level = ml_state['entry_scale_level']
                    # Clamp to valid range
                    self._list_entry_scale_level = max(0, min(2, int(scale_level)))
                    # Update button states
                    self._update_scale_button_states()
                
                # Utilities filter removed - always show only utilities connected to shaders
                
                # List buttons removed - no state to load
                
                # Material search text - NOT saved (user preference: search should reset on reload)
                
                # Toggle buttons for panels - set visibility to match saved state
                panel_mappings = {
                    'toggleMaterialCreatorVis': ('materialCreatorLayout', 'Creator'),
                    'toggleMaterialToolsVis': ('materialToolsLayout', 'Tools'),
                    'toggleMaterialListVis': ('materialListLayout', 'List'),
                    'toggleMaterialManagerVis': ('materialManagerFrame', 'Manager')
                }
                
                for panel_name, (layout_name, friendly_name) in panel_mappings.items():
                    if panel_name in ml_state:
                        self.set_layout_visibility(layout_name, panel_name, friendly_name, ml_state[panel_name])

            if 'texture_importer' in state:
                ti_state = state['texture_importer']
                if 'texture_importer_settings_visible' in ti_state:
                    self._set_settings_frame_visibility(
                        'textureImporterSettingsButton',
                        bool(ti_state['texture_importer_settings_visible']),
                        trigger_snap=False
                    )
            
            # Always reset to shaders tab on reload (ignore saved tab state)
            # Ensure only materialListShadersButton is checked
            def _reset_to_shaders_tab():
                # Uncheck all tab buttons first
                textures_btn = self._get_widget('materialListTexturesButton', QtWidgets.QPushButton)
                utilities_btn = self._get_widget('materialListUtilitiesButton', QtWidgets.QPushButton)
                shading_groups_btn = self._get_widget('materialListShadingGroupButton', QtWidgets.QPushButton)
                shaders_btn = self._get_widget('materialListShadersButton', QtWidgets.QPushButton)
                
                # Block signals to prevent triggering refresh during state load
                blockers = []
                for btn in [textures_btn, utilities_btn, shading_groups_btn, shaders_btn]:
                    if btn:
                        blockers.append(QtCore.QSignalBlocker(btn))
                
                try:
                    # Uncheck the other buttons
                    if textures_btn:
                        textures_btn.setChecked(False)
                    if utilities_btn:
                        utilities_btn.setChecked(False)
                    if shading_groups_btn:
                        shading_groups_btn.setChecked(False)
                    
                    # Ensure only shaders button is checked
                    if shaders_btn:
                        shaders_btn.setChecked(True)
                        self._current_active_tab = 'shaders'
                        self._update_tab_frames_visibility('shaders')
                        # Update header frames
                        self._update_header_frames_visibility(True, False, False, False)
                        # Debug: print(f"[UI_STATE] Reset to shaders tab (ignoring saved tab state)")
                finally:
                    # Clean up blockers
                    for blocker in blockers:
                        del blocker
            QtCore.QTimer.singleShot(150, _reset_to_shaders_tab)
            
            # Refresh UI after loading state
            # Don't refresh immediately to prevent auto-save
            # self.populate_materials_scroll_area()
            QtCore.QTimer.singleShot(0, self._apply_minimum_width_baseline)
            QtCore.QTimer.singleShot(0, self.snap_to_minimum)
            
            # Add a small delay to prevent immediate auto-save after loading
            QtCore.QTimer.singleShot(500, lambda: setattr(self, '_loading_state', False))
            # Debug: print("[UI_STATE] UI state loading completed")
            
        except Exception as e:
            print(f"[UI_STATE] ERROR loading UI state: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self._end_silent_refresh()
            QtCore.QTimer.singleShot(0, self._ensure_initial_populate)
            # Clear loading flag after a delay to allow UI elements to finish setting
            QtCore.QTimer.singleShot(100, lambda: setattr(self, '_loading_state', False))
    
    def _ensure_initial_populate(self):
        """
        Make sure the material list is populated once after state loading completes.
        """
        if getattr(self, "_initial_populate_done", False):
            return
        self._initial_populate_done = True
        try:
            if hasattr(self, "_refresh_timer"):
                self._refresh_timer.stop()
        except Exception:
            pass
        search_line = self.ui_elements.get('materialSearchLineEdit')
        search_text = search_line.text() if search_line else ""
        self.populate_materials_scroll_area(search_text=search_text)

    def _save_ui_state(self):
        """Debounced save - starts a timer to save after inactivity."""
        # Don't save during state loading to prevent overwriting
        if self._loading_state:
            return
            
        # Restart the timer - this will cancel any pending save
        self._save_timer.start(self._save_delay_ms)
        # Debug: print("[UI_STATE] State save scheduled (debounced)")
    
    def _save_ui_state_immediate(self):
        """Actually save the UI state to file (called by timer)."""
        # Debug: print(f"[UI_STATE] Saving UI state to: {self.state_file_path}")
        try:
            state = {
                'material_creator': {},
                'material_list': {},
                'texture_importer': {}
            }
            
            # Save material creator settings
            mc_state = state['material_creator']
            
            # Material type
            combo = self.ui_elements.get('materialTypeComboBox')
            if combo:
                mc_state['material_type'] = combo.currentText()
            
            # Color
            mc_state['color'] = {
                'r': self.selected_color.red(),
                'g': self.selected_color.green(),
                'b': self.selected_color.blue()
            }
            
            # Slider values
            for slider_name in ['materialColorHueSlider', 'materialColorSaturationSlider', 'materialColorValueSlider']:
                slider = self.ui_elements.get(slider_name)
                if slider:
                    mc_state[slider_name] = slider.value()
            
            # Spinbox values
            for spin_name in ['roughnessSpinBox', 'metalnessSpinBox', 'emissionSpinBox', 'opacitySpinBox', 'transmissionSpinBox', 'subsurfaceSpinBox']:
                spin = self.ui_elements.get(spin_name)
                if spin:
                    mc_state[spin_name] = spin.value()
            
            # Checkboxes
            for cb_name in ['materialPerMeshCheckbox', 'randomHueCheckbox']:
                cb = self.ui_elements.get(cb_name)
                if cb:
                    mc_state[cb_name] = cb.isChecked()
            
            # Material naming template
            line_edit = self.ui_elements.get('materialNamingLineEdit')
            if line_edit:
                mc_state['material_naming_template'] = line_edit.text()
            
            mc_state['material_creator_settings_visible'] = self._get_settings_frame_checked('materialCreatorSettingsButton')
            
            # Save material list settings
            ml_state = state['material_list']
            
            # Sorting
            ml_state['sort_mode'] = self._sort_mode
            ml_state['sort_desc'] = self._sort_desc
            
            # Filter checkboxes (some entries are buttons only)
            for filter_spec in self.MATERIAL_FILTERS:
                cb_name = filter_spec.get('checkbox')
                if not cb_name:
                    continue
                cb = self.ui_elements.get(cb_name)
                if cb:
                    ml_state[cb_name] = cb.isChecked()
            
            # Node type show checkboxes
            for checkbox_name in ['showTexturesCheckbox', 'showProceduralTexturesCheckbox', 'showShadingGroupsCheckbox']:
                cb = self.ui_elements.get(checkbox_name)
                if cb:
                    ml_state[checkbox_name] = cb.isChecked()
            
            # Material list option checkboxes
            for checkbox_name in ['hideNamespacesCheckbox', 'highlightUnusedCheckbox', 'showIconsCheckbox', 'showShaderSwatchesCheckbox', 'showOtherIconsCheckbox']:
                cb = self.ui_elements.get(checkbox_name)
                if cb:
                    ml_state[checkbox_name] = cb.isChecked()
            
            # Material list options button
            options_btn = self.ui_elements.get('materialListSettingsButton')
            if options_btn:
                ml_state['material_list_options_visible'] = options_btn.isChecked()
            filters_btn = self.ui_elements.get('materialFiltersButton')
            if filters_btn:
                ml_state['material_filters_visible'] = filters_btn.isChecked()
            
            # Utilities filter removed - always show only utilities connected to shaders
            
            # List buttons removed - no state to save
            
            # Material search text - NOT saved (user preference: search should reset on reload)
            
            # Toggle buttons for panels
            for panel_name in ['toggleMaterialCreatorVis', 'toggleMaterialToolsVis', 'toggleMaterialListVis', 'toggleMaterialManagerVis']:
                btn = self.ui_elements.get(panel_name)
                if btn:
                    ml_state[panel_name] = btn.isChecked()
            
            # Save active tab state
            current_tab = self._get_current_tab_type() or self._current_active_tab or 'shaders'
            ml_state['active_tab'] = current_tab
            
            # Save entry scale level
            ml_state['entry_scale_level'] = self._list_entry_scale_level
            
            # Save texture importer settings
            ti_state = state['texture_importer']
            ti_settings = getattr(self, '_texture_importer_settings', {}) or {}
            ti_state['default_mode'] = ti_settings.get('default_mode', 'maya_file')
            ti_state['custom_path'] = ti_settings.get('custom_path', '')
            ti_state['create_if_doesnt_exist'] = ti_settings.get('create_if_doesnt_exist', False)
            ti_state['texture_importer_settings_visible'] = self._get_settings_frame_checked('textureImporterSettingsButton')

            # Write to file
            with open(self.state_file_path, 'w') as f:
                json.dump(state, f, indent=2)
            
            # Debug: print(f"[UI_STATE] State saved successfully: tab={current_tab}, material_type={mc_state.get('material_type', 'N/A')}, attributes={len([k for k in mc_state.keys() if 'SpinBox' in k])} spinboxes")
                
        except Exception as e:
            print(f"[UI_STATE] ERROR saving state: {e}")
            import traceback
            traceback.print_exc()
    
    def keyPressEvent(self, event):
        """Override keyPressEvent to prevent Escape from closing dialog when editing."""
        if event.key() == QtCore.Qt.Key_Escape:
            # Check if the focused widget is a line edit that's currently being edited
            focused_widget = self.focusWidget()
            if focused_widget and isinstance(focused_widget, QtWidgets.QLineEdit):
                if not focused_widget.isReadOnly():
                    # There's an active edit in the focused widget, let it handle Escape
                    # The line edit's keyPressEvent will accept the event, preventing dialog close
                    focused_widget.keyPressEvent(event)
                    return
            
            # Also check all scroll areas for any editing line edits (in case focus isn't set)
            for tab_type in self.MATERIAL_TABS:
                scroll_area = self._get_tab_scroll_area(tab_type)
                if scroll_area and scroll_area.widget():
                    scroll_content = scroll_area.widget()
                    # Find all QLineEdit widgets that are in edit mode
                    line_edits = scroll_content.findChildren(QtWidgets.QLineEdit)
                    for le in line_edits:
                        if not le.isReadOnly():
                            # There's an active edit, let the line edit handle Escape
                            le.keyPressEvent(event)
                            return
        
        # If no editing is active, use default behavior (which may close the dialog)
        super(QuickMaterialsUI, self).keyPressEvent(event)

    def closeEvent(self, event):
        """Override closeEvent to save state before closing."""
        self._save_ui_state()
        self._flush_settings_cache_to_disk(force=True)
        super(QuickMaterialsUI, self).closeEvent(event)

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





# Material Tools
    def delete_unused_materials(self):
        """
        Delete unused materials/nodes in the scene and refresh the materials list while maintaining the current visibility state.
        Shows a confirmation dialog before deletion and yellow confirmation text after deletion.
        """
        scrollArea = self.ui_elements.get('materialsListScrollArea')

        try:
            # Get count of all unused nodes before deletion
            unused_nodes = []
            
            # Try multiple methods to find unused nodes
            # Method 1: Try hyperShade command (may not work in all Maya versions)
            try:
                unused_nodes = cmds.hyperShade(listUnusedNodes=True) or []
                # Also try alternative syntax
                if not unused_nodes:
                    try:
                        unused_nodes = cmds.hyperShade(listUnusedNodes=1) or []
                    except Exception:
                        pass
            except Exception as e:
                print(f"[DEBUG] hyperShade method failed: {e}")
                unused_nodes = []
            
            # Method 2: If hyperShade fails, manually find unused nodes
            if not unused_nodes:
                try:
                    unused_nodes = self._find_unused_nodes_manual()
                except Exception as e:
                    print(f"[DEBUG] Manual method failed: {e}")
                    unused_nodes = []
            
            # Method 3: Try using MEL command to get the list
            if not unused_nodes:
                try:
                    # Use MEL to get unused nodes list
                    mel_result = mel.eval('string $unused[] = `hyperShade -listUnusedNodes`; $unused;')
                    if mel_result:
                        unused_nodes = mel_result if isinstance(mel_result, list) else [mel_result]
                except Exception as e:
                    print(f"[DEBUG] MEL method failed: {e}")
                    unused_nodes = []
            
            # Debug output
            print(f"[DEBUG] Found {len(unused_nodes)} unused nodes")
            if len(unused_nodes) > 0 and len(unused_nodes) <= 20:
                print(f"[DEBUG] Unused nodes: {unused_nodes}")
            
            # Check if there are any unused nodes to delete
            unused_count = len(unused_nodes)
            if unused_count == 0:
                cmds.inViewMessage(amg="<hl>No unused nodes found to delete</hl>", pos="topCenter", fade=True)
                return
            
            # Show confirmation dialog with count
            confirmation = QtWidgets.QMessageBox.question(
                None,
                "Confirm Deletion",
                f"Delete {unused_count} unused node(s)?\n\nThis action cannot be undone.",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No
            )
            
            # If user cancels, return without deleting
            if confirmation != QtWidgets.QMessageBox.Yes:
                return
            
            # Execute MEL command to delete unused nodes
            mel.eval('MLdeleteUnused;')
            
            # Show yellow confirmation message in viewport
            # Try multiple formats for yellow text - Maya's inViewMessage HTML support varies
            yellow_text_format = "<hl><font color='yellow'>{}</font></hl>"
            if unused_count == 1:
                cmds.inViewMessage(
                    amg=yellow_text_format.format("1 unused node removed"),
                    pos="topCenter",
                    fade=True
                )
            else:
                cmds.inViewMessage(
                    amg=yellow_text_format.format(f"{unused_count} unused nodes removed"),
                    pos="topCenter",
                    fade=True
                )
            
            # Show debug message with deleted nodes
            print(f"[DEBUG] {unused_count} unused nodes deleted")
            if unused_count <= 20:  # Only print full list if reasonable size
                print(f"[DEBUG] Nodes deleted: {unused_nodes}")

            # Refresh materials list after deletion
            self.populate_materials_scroll_area(hide_defaults=self.hide_defaults_state)
        except Exception as e:
            cmds.warning(f"Failed to delete unused materials: {e}")

    def _find_unused_nodes_manual(self):
        """
        Manually find unused shading nodes by checking which nodes are connected to assigned shading groups.
        This is a fallback method when hyperShade command doesn't work.
        """
        unused_nodes = []
        
        try:
            # Get all shading groups
            all_shading_groups = cmds.ls(type='shadingEngine') or []
            
            # Find which shading groups are actually assigned (have members)
            used_shading_groups = set()
            for sg in all_shading_groups:
                try:
                    # Skip default shading groups that Maya creates (but check if they have members)
                    if sg in ['initialShadingGroup', 'initialParticleSE']:
                        members = cmds.sets(sg, q=True) or []
                        if members:
                            used_shading_groups.add(sg)
                        continue
                    members = cmds.sets(sg, q=True) or []
                    if members:
                        used_shading_groups.add(sg)
                except Exception:
                    pass
            
            # Get all nodes connected to used shading groups (these are "used" nodes)
            used_nodes = set()
            used_nodes.update(used_shading_groups)  # Include the shading groups themselves
            
            for sg in used_shading_groups:
                try:
                    # Get all upstream connections from this shading group (shading network)
                    # Use listHistory with pruneDagObjects to exclude DAG nodes
                    connected = cmds.listHistory(sg, future=False, pruneDagObjects=True, ac=True) or []
                    used_nodes.update(connected)
                    
                    # Also get direct source connections
                    direct_conns = cmds.listConnections(sg, source=True, destination=False, skipConversionNodes=True) or []
                    used_nodes.update(direct_conns)
                    
                    # Get connections through all attributes (more comprehensive)
                    attrs = cmds.listAttr(sg, connectable=True) or []
                    for attr in attrs:
                        try:
                            conns = cmds.listConnections(f"{sg}.{attr}", source=True, destination=False, skipConversionNodes=True) or []
                            used_nodes.update(conns)
                        except Exception:
                            pass
                except Exception as e:
                    print(f"[DEBUG] Error getting connections for {sg}: {e}")
            
            # Get all shading-related nodes in the scene
            # Use a comprehensive list of shading node types
            shading_node_types = [
                # Materials
                'lambert', 'blinn', 'phong', 'phongE', 'anisotropic', 'layeredShader',
                'rampShader', 'surfaceShader', 'useBackground', 'shadingMap',
                # Arnold materials
                'aiStandardSurface', 'aiStandard', 'aiMixShader', 'aiCarPaint',
                'aiHair', 'aiSkin', 'aiToon', 'aiFlat', 'aiUtility',
                # Textures
                'file', 'place2dTexture', 'ramp', 'noise', 'fractal', 'brownian',
                'granite', 'leather', 'marble', 'mountain', 'rock', 'snow',
                'stucco', 'wood', 'ocean', 'cloth', 'crater', 'volumeNoise',
                'envBall', 'envChrome', 'envCube', 'envSky', 'envSphere',
                'projection', 'stencil', 'layeredTexture', 'movie',
                # Utility nodes
                'multiplyDivide', 'plusMinusAverage', 'reverse', 'setRange',
                'clamp', 'remapValue', 'remapColor', 'blendColors', 'gammaCorrect',
                'contrast', 'luminance', 'hsvToRgb', 'rgbToHsv', 'vectorProduct',
                'condition', 'samplerInfo', 'bump2d', 'bump3d', 'displacementShader',
                'volumeShader', 'lightInfo', 'distanceBetween', 'unitConversion',
                # Arnold utilities
                'aiImage', 'aiNormalMap', 'aiNoise', 'aiMix', 'aiColorCorrect',
                'aiRange', 'aiClamp', 'aiToFloat', 'aiToVector', 'aiToColor',
                'aiVectorToFloat', 'aiVectorToColor', 'aiColorToFloat', 'aiColorToVector',
                # Shading engines (already have these, but include for completeness)
                'shadingEngine'
            ]
            
            all_shading_nodes = set()
            for node_type in shading_node_types:
                try:
                    nodes = cmds.ls(type=node_type) or []
                    all_shading_nodes.update(nodes)
                except Exception:
                    pass
            
            # Remove default nodes that should never be deleted
            default_nodes = {'initialShadingGroup', 'initialParticleSE', 'lambert1', 'particleCloud1', 'defaultShaderList1'}
            all_shading_nodes = all_shading_nodes - default_nodes
            
            # Find unused nodes (nodes not in used_nodes set)
            unused_nodes = [node for node in all_shading_nodes if node not in used_nodes]
            
            # Additional check: remove nodes that are referenced (shouldn't delete referenced nodes)
            final_unused = []
            for node in unused_nodes:
                try:
                    # Check if node is referenced
                    if not self._is_referenced(node):
                        # Double-check: make sure node is actually a shading node (not a transform, etc.)
                        node_type = cmds.nodeType(node)
                        if node_type in shading_node_types or 'shader' in node_type.lower() or 'texture' in node_type.lower():
                            final_unused.append(node)
                except Exception as e:
                    # If we can't check, include it (better to find too many than too few)
                    try:
                        node_type = cmds.nodeType(node)
                        if node_type in shading_node_types or 'shader' in node_type.lower() or 'texture' in node_type.lower():
                            final_unused.append(node)
                    except Exception:
                        pass
            
            unused_nodes = final_unused
            
            print(f"[DEBUG] Manual method: Found {len(used_shading_groups)} used shading groups, {len(used_nodes)} used nodes, {len(all_shading_nodes)} total shading nodes, {len(unused_nodes)} unused nodes")
            
        except Exception as e:
            print(f"[DEBUG] Error in _find_unused_nodes_manual: {e}")
            import traceback
            traceback.print_exc()
        
        return unused_nodes

    def open_material_converter(self):
        """
        Wrapper to open the QuickMaterials.material_converter tool.
        Reloads the module during dev. Material converter uses standalone styling.
        """
        try:
            from QuickMaterials import material_converter as _matconv
            import importlib
            importlib.reload(_matconv)  # nice during iteration; remove if undesired

            # Material converter uses standalone styling, no style argument needed
            _matconv.show()
        except Exception as e:
            import maya.cmds as cmds
            cmds.warning(f"Material converter failed to open: {e}")

    def open_mesh_exporter(self):
        """
        Wrapper to open the QuickMaterials.mesh_exporter tool.
        Reloads the module during dev. Mesh exporter uses standalone styling.
        """
        try:
            from QuickMaterials import mesh_exporter as _meshexp
            import importlib
            importlib.reload(_meshexp)  # nice during iteration; remove if undesired

            # Mesh exporter uses standalone styling, no style argument needed
            _meshexp.show_export_ui()
        except Exception as e:
            import maya.cmds as cmds
            cmds.warning(f"Mesh exporter failed to open: {e}")




# Material List

    # -------------------------------
    # 1) Public Entrypoints: Build/Refresh UI
    # -------------------------------


    # Rebuilds the list UI from scene state + live filters + search. Adds chips row.
    def _compute_material_list_hash(self, all_nodes, flags, search_text):
        """
        Compute a hash of the material list state to detect if anything changed.
        Returns a hash string that changes when materials are added/removed or filters change.
        """
        import hashlib
        # Create a stable representation of the state
        state_str = f"{sorted(all_nodes)}|{sorted(flags.items())}|{search_text}"
        return hashlib.md5(state_str.encode()).hexdigest()
    
    def populate_materials_scroll_area(self, hide_defaults=False, search_text="", saved_selection=None):
        """
        Rebuild the material list UI, honoring live filters and search.
        Adds an optional chips row at the top when any filters are active.
        PERFORMANCE: Skips full rebuild if material list and filters haven't changed.
        """
        # Guard against rebuilds/teardown
        try:
            if not self._is_ui_alive():
                return
        except Exception:
            return

        # Clear material type cache to ensure shader types are re-queried
        # This is important after material conversion when shader types change
        if hasattr(self, "_material_type_cache"):
            self._material_type_cache.clear()

        start_time = time.perf_counter()
        
        # PERFORMANCE OPTIMIZATION: Clear pending icon creations, style updates, button creations, and signal connections queues for new build
        self._pending_icon_creations = []
        self._pending_style_updates = []
        # List buttons removed - no pending button creations
        self._pending_signal_connections = []  # Queue for deferred signal connections
        
        # PERFORMANCE OPTIMIZATION: Cache checkbox references to avoid repeated _get_widget() calls
        self._cached_show_icons_cb = self._get_widget('showIconsCheckbox', QtWidgets.QCheckBox)
        self._cached_hide_namespaces_cb = self._get_widget('hideNamespacesCheckbox', QtWidgets.QCheckBox)
        self._cached_highlight_unused_cb = self._get_widget('highlightUnusedCheckbox', QtWidgets.QCheckBox)
        
        # Reset selection & per-build registries
        # Note: shader_types will be set after batch computation
        # PERFORMANCE OPTIMIZATION: Return material row containers to pool for reuse
        old_entries = []
        if getattr(self, "_tab_entry_state", None):
            for state in self._tab_entry_state.values():
                if not state:
                    continue
                old_entries.extend(state.get("entries") or [])
        else:
            old_entries = list(getattr(self, "_entry_list", []))
        if old_entries:
            pool = getattr(self, "_material_row_pool", None)
            if pool is None:
                pool = []
                self._material_row_pool = pool
            max_pool_size = 100
            for entry in old_entries:
                container = entry.get("container")
                if container and hasattr(container, "_qm_line_edit"):
                    try:
                        container.hide()
                        container.setParent(None)
                        # Add to pool for reuse (limit pool size to avoid memory bloat)
                        if len(pool) < max_pool_size:
                            pool.append(container)
                    except Exception:
                        pass
        self.selected_materials_list = []
        scroll_areas = {tab: self._get_tab_scroll_area(tab) for tab in self.MATERIAL_TABS}
        if not any(scroll_areas.values()):
            return
        current_tab_type = self._get_current_tab_type() or self._current_active_tab or 'shaders'
        self._current_active_tab = current_tab_type
        self._update_tab_frames_visibility(current_tab_type)

        # List buttons removed - no longer tracking button rows

        # Reset row registry to avoid stale Qt refs
        self._entry_list = []          # list of dicts: {material, swatch, line_edit, is_default}
        self._index_by_material = {}   # material -> row index
        self._selection_anchor = None  # last clicked index for Shift range

        for area in scroll_areas.values():
            if area:
                self._configure_materials_scroll_area(area)

        # Defaults and permanently hidden
        # Include openPBR_shader1 for Maya 2026+ (will be filtered if it doesn't exist)
        DEFAULT_MATERIALS = getattr(self, "DEFAULT_MATERIALS", {'lambert1', 'standardSurface1', 'openPBR_shader1'})
        HIDDEN_MATERIALS  = getattr(self, "HIDDEN_MATERIALS",  {'particleCloud1'})
        DEFAULT_SHADING_GROUPS = {'initialShadingGroup', 'initialParticleSE'}

        # Always collect ALL data for all tabs (not just active tab)
        # This ensures all tabs can be populated and switching is instant
        all_nodes = []
        collect_start = time.perf_counter()

        # Collect materials (for shaders tab)
        materials_collect_start = time.perf_counter()
        all_materials = [m for m in (cmds.ls(materials=True) or []) if m not in HIDDEN_MATERIALS]
        all_nodes.extend(all_materials)
        materials_collect_duration = (time.perf_counter() - materials_collect_start) * 1000.0

        # Collect file textures (for textures tab)
        file_collect_start = time.perf_counter()
        file_textures = []
        try:
            file_textures = [n for n in cmds.ls(type='file') or [] if n not in HIDDEN_MATERIALS]
            all_nodes.extend(file_textures)
            file_collect_duration = (time.perf_counter() - file_collect_start) * 1000.0
        except Exception as e:
            file_collect_duration = (time.perf_counter() - file_collect_start) * 1000.0

        # Collect procedural textures (for textures tab)
        proc_collect_start = time.perf_counter()
        procedural_only = []
        try:
            all_textures = self._get_texture_nodes()
            procedural_only = [t for t in all_textures if cmds.nodeType(t) != 'file' and t not in HIDDEN_MATERIALS]
            all_nodes.extend(procedural_only)
            proc_collect_duration = (time.perf_counter() - proc_collect_start) * 1000.0
        except Exception as e:
            proc_collect_duration = (time.perf_counter() - proc_collect_start) * 1000.0

        # Collect shading engines (for shading groups tab)
        sg_collect_start = time.perf_counter()
        shading_engines = []
        try:
            shading_engines = cmds.ls(type='shadingEngine') or []
            shading_engines = [sg for sg in shading_engines if sg not in HIDDEN_MATERIALS and sg not in DEFAULT_MATERIALS]
            all_nodes.extend(shading_engines)
            sg_collect_duration = (time.perf_counter() - sg_collect_start) * 1000.0
        except Exception as e:
            sg_collect_duration = (time.perf_counter() - sg_collect_start) * 1000.0

        # Collect utility nodes (for utilities tab) - LAZY LOADED: Skip on initial populate
        # Utilities will be collected only when utilities tab is first accessed
        # When utilities tab is already populated, use cached utilities to avoid repopulation
        utility_nodes = []
        if not hasattr(self, '_utilities_tab_populated'):
            self._utilities_tab_populated = False
        if self._utilities_tab_populated and hasattr(self, '_utilities_cache'):
            # Use cached utilities if available (avoids repopulation when only other filters change)
            utility_nodes = self._utilities_cache
            all_nodes.extend(utility_nodes)
        elif self._utilities_tab_populated:
            # Tab was populated but cache is missing - collect utilities connected to shaders
            util_collect_start = time.perf_counter()
            try:
                utility_nodes = self._get_utility_nodes()
                self._utilities_cache = utility_nodes  # Cache for future use
                all_nodes.extend(utility_nodes)
                util_collect_duration = (time.perf_counter() - util_collect_start) * 1000.0
            except Exception as e:
                util_collect_duration = (time.perf_counter() - util_collect_start) * 1000.0

        collect_duration = (time.perf_counter() - collect_start) * 1000.0

        # Read live filter flags (Selected / Non-Selected / Referenced / Used / Hide Defaults)
        flags_start = time.perf_counter()
        flags = self._collect_filter_flags()
        # Back-compat: if the checkbox doesn't exist, honor the function argument
        if not self.ui_elements.get('hideDefaultMaterialsCheckbox'):
            flags["hideDefaults"] = bool(hide_defaults)
        flags_duration = (time.perf_counter() - flags_start) * 1000.0

        # Precompute current selection shapes for selected/non-selected filters
        # Traverse hierarchy to get all shapes from selected objects (including groups)
        sel_start = time.perf_counter()
        current_sel_shapes = self._get_all_shapes_from_selection() or []
        sel_duration = (time.perf_counter() - sel_start) * 1000.0

        # PERFORMANCE OPTIMIZATION: Batch compute material properties AND colors
        props_start = time.perf_counter()
        material_properties = self._batch_compute_material_properties(all_nodes, current_sel_shapes)
        props_duration_ms = (time.perf_counter() - props_start) * 1000.0

        # If hideDefaults is checked, filter out default shading groups
        hide_defaults_start = time.perf_counter()
        if flags.get("hideDefaults", False):
            all_nodes = [n for n in all_nodes if n not in DEFAULT_SHADING_GROUPS]
            hide_defaults_duration = (time.perf_counter() - hide_defaults_start) * 1000.0
        else:
            hide_defaults_duration = (time.perf_counter() - hide_defaults_start) * 1000.0

        # Build list using filters + search
        # When populating all tabs, exclude tab-specific filters so all node types pass through
        filter_start = time.perf_counter()
        nodes_to_display = []
        filter_calls = 0
        filter_cache_hits = 0
        filter_cache_misses = 0
        
        # Create a copy of flags without tab-specific filters for multi-tab population
        filter_flags = flags.copy()
        # Remove tab-specific filters so all node types are included
        filter_flags.pop("showShadersOnly", None)
        filter_flags.pop("fileTextures", None)
        filter_flags.pop("proceduralTextures", None)
        filter_flags.pop("shadingGroups", None)
        filter_flags.pop("utilitiesOnly", None)
        
        for node in all_nodes:
            filter_calls += 1
            if self._passes_filters_optimized(node, filter_flags, search_text, DEFAULT_MATERIALS, material_properties):
                nodes_to_display.append(node)
        filter_duration = (time.perf_counter() - filter_start) * 1000.0

        # PERFORMANCE OPTIMIZATION: Check if material list and filters actually changed
        hash_start = time.perf_counter()
        current_hash = self._compute_material_list_hash(nodes_to_display, flags, search_text)
        hash_duration = (time.perf_counter() - hash_start) * 1000.0
        
        if (self._last_material_list_hash == current_hash and 
            self._last_filter_state_hash == current_hash and
            hasattr(self, "_entry_list") and len(self._entry_list) > 0):
            # Nothing changed - skip rebuild
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return
        
        # Update hashes for next check
        self._last_material_list_hash = current_hash
        self._last_filter_state_hash = current_hash

        # Snapshot current on-screen order so we can preserve it for one rebuild after rename
        prev_order = [e.get("material") for e in getattr(self, "_entry_list", [])] if hasattr(self, "_entry_list") else []

        # PERFORMANCE OPTIMIZATION: Check if we can avoid a full rebuild
        if self._can_optimize_ui_refresh(nodes_to_display, search_text, flags):
            # Try to update existing UI instead of full rebuild
            if self._update_existing_ui(nodes_to_display, search_text, flags):
                return  # Successfully updated existing UI

        # Save scroll positions before replacing content (so we can restore them after rebuild)
        saved_scroll_positions = {}
        for tab_type, area in scroll_areas.items():
            if area and area.widget():
                # Get the vertical scroll bar value
                v_scrollbar = area.verticalScrollBar()
                if v_scrollbar:
                    saved_scroll_positions[tab_type] = v_scrollbar.value()
        
        # Mark that we're rebuilding the list so other operations can guard against it.
        # IMPORTANT: We intentionally DO NOT clear the existing scroll area content here.
        # Keeping the old widgets visible avoids a blank/flicker state while the new UI
        # (and new swatches) are being built. The old content will be replaced when we call
        # scroll_area.setWidget(info['content']) below.
        self._rebuilding_list = True

        # Prepare layouts for each tab
        tab_layouts = {}
        for tab_type, area in scroll_areas.items():
            if not area:
                continue
            scroll_content = MaterialListScrollContent(self)
            scroll_content.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
            scroll_content.setMinimumWidth(0)
            scroll_content.setStyleSheet(self.material_list_widget_style)
            layout = QtWidgets.QGridLayout(scroll_content)
            layout.setContentsMargins(3, 3, 3, 3)
            # Use current scale for spacing
            current_scale = self._list_entry_scale_sizes[self._list_entry_scale_level]
            layout.setVerticalSpacing(current_scale['spacing'])
            layout.setHorizontalSpacing(3)
            row = 0
            consumed = self._add_active_filters_bar(layout, row)
            row += consumed
            tab_layouts[tab_type] = {
                'scroll_area': area,
                'content': scroll_content,
                'layout': layout,
                'row': row
            }
        self._last_type_header = None  # reset type chunking per rebuild

        # --- Separate nodes by type ---
        # PERFORMANCE: Batch classify all nodes and cache results
        classify_start = time.perf_counter()
        materials_only = []
        file_textures_only = []
        procedural_textures_only = []
        shading_groups_only = []
        utilities_only = []
        
        # Pre-classify all nodes and store in a dict for reuse
        node_classifications = {}
        classify_cache_hits = 0
        classify_cache_misses = 0
        for item in nodes_to_display:
            # Check if already in cache
            if item in self._node_type_classification_cache:
                classify_cache_hits += 1
            else:
                classify_cache_misses += 1
            node_type_category = self._classify_node_type(item)  # This now uses cache
            node_classifications[item] = node_type_category
            if node_type_category == 'file_textures':
                file_textures_only.append(item)
            elif node_type_category == 'procedural_textures':
                procedural_textures_only.append(item)
            elif node_type_category == 'shading_groups':
                shading_groups_only.append(item)
            elif node_type_category == 'utilities':
                utilities_only.append(item)
            else:  # materials
                materials_only.append(item)
        classify_duration_ms = (time.perf_counter() - classify_start) * 1000.0

        # PERFORMANCE: Batch-compute shader types for all materials upfront
        shader_type_start = time.perf_counter()
        shader_types = {}
        if materials_only:
            # Ensure cache exists
            if not hasattr(self, "_material_type_cache"):
                self._material_type_cache = {}
            cache = self._material_type_cache
            
            for material in materials_only:
                if material in cache:
                    shader_types[material] = cache[material]
                else:
                    # Compute and cache
                    shader_type = None
                    try:
                        if cmds.objExists(material):
                            shader_type = cmds.nodeType(material)
                    except Exception:
                        shader_type = None
                    cache[material] = shader_type
                    shader_types[material] = shader_type
        shader_type_duration = (time.perf_counter() - shader_type_start) * 1000.0
        
        # Store shader types for use in add_material_entry_optimized
        self._current_shader_types = shader_types

        # --- Apply sorting (with optional one-shot freeze for 'Name' sort after rename) ---
        sort_start = time.perf_counter()
        sort_mode = getattr(self, "_sort_mode", "name")
        sort_desc = getattr(self, "_sort_desc", False)
        if sort_mode == "name" and getattr(self, "_freeze_name_sort_once", False):
            # Preserve prior visual order for the nodes that pass current filters
            index = {m: i for i, m in enumerate(prev_order)}
            large = 10**9
            materials_only.sort(key=lambda m: index.get(m, large))
            file_textures_only.sort(key=lambda m: index.get(m, large))
            procedural_textures_only.sort(key=lambda m: index.get(m, large))
            shading_groups_only.sort(key=lambda m: index.get(m, large))
            utilities_only.sort(key=lambda m: index.get(m, large))
            self._freeze_name_sort_once = False  # consume the freeze
            sort_duration = (time.perf_counter() - sort_start) * 1000.0
        else:
            materials_only = self._sort_materials(materials_only, all_nodes)
            file_textures_only = self._sort_materials(file_textures_only, all_nodes)
            procedural_textures_only = self._sort_materials(procedural_textures_only, all_nodes)
            shading_groups_only = self._sort_materials(shading_groups_only, all_nodes)
            utilities_only = self._sort_materials(utilities_only, all_nodes)
            sort_duration = (time.perf_counter() - sort_start) * 1000.0

        # --- Determine which sections to show based on active tab button ---
        shaders_btn = self._get_widget('materialListShadersButton', QtWidgets.QPushButton)
        textures_btn = self._get_widget('materialListTexturesButton', QtWidgets.QPushButton)
        shading_groups_btn = self._get_widget('materialListShadingGroupButton', QtWidgets.QPushButton)
        utilities_btn = self._get_widget('materialListUtilitiesButton', QtWidgets.QPushButton)
        
        active_tab = self._get_current_tab_type() or self._current_active_tab or 'shaders'
        show_shaders = (active_tab == 'shaders')
        show_textures = (active_tab == 'textures')
        show_shading_groups = (active_tab == 'shading_groups')
        show_utilities = (active_tab == 'utilities')
        
        # --- Update header frame visibility based on active tab ---
        self._update_header_frames_visibility(show_shaders, show_textures, show_shading_groups, show_utilities)
        
        # --- Add SHADERS section (populate ALL tabs, not just active) ---
        ui_create_start = time.perf_counter()
        shader_tab = tab_layouts.get('shaders')
        if shader_tab:
            scroll_layout = shader_tab['layout']
            row = shader_tab['row']
            # Populate materials entries (+ action rows) or show empty message
            if materials_only:
                materials_ui_start = time.perf_counter()
                for material in materials_only:
                    is_default = material in DEFAULT_MATERIALS
                    entry_start = time.perf_counter()
                    node_type = node_classifications.get(material)
                    self.add_material_entry_optimized(material, row, scroll_layout, DEFAULT_MATERIALS, saved_selection, material_properties, node_type)
                    entry_duration = (time.perf_counter() - entry_start) * 1000.0
                    self.add_material_buttons(material, row, scroll_layout, is_default, node_type_category=node_type)
                    row += 2
                materials_ui_duration = (time.perf_counter() - materials_ui_start) * 1000.0
            else:
                # Show empty state message
                self._add_empty_state_message(scroll_layout, row, 'materials', flags)
                row += 1
            shader_tab['row'] = row

        # --- Add TEXTURES section (populate ALL tabs, not just active) ---
        textures_tab = tab_layouts.get('textures')
        if textures_tab:
            scroll_layout = textures_tab['layout']
            row = textures_tab['row']
            if file_textures_only:
                file_ui_start = time.perf_counter()
                for texture in file_textures_only:
                    node_type = node_classifications.get(texture)
                    self.add_material_entry_optimized(texture, row, scroll_layout, DEFAULT_MATERIALS, saved_selection, material_properties, node_type)
                    self.add_material_buttons(texture, row, scroll_layout, False, node_type_category=node_type)
                    row += 2
                file_ui_duration = (time.perf_counter() - file_ui_start) * 1000.0
            
            if procedural_textures_only:
                proc_ui_start = time.perf_counter()
                for texture in procedural_textures_only:
                    node_type = node_classifications.get(texture)
                    self.add_material_entry_optimized(texture, row, scroll_layout, DEFAULT_MATERIALS, saved_selection, material_properties, node_type)
                    self.add_material_buttons(texture, row, scroll_layout, False, node_type_category=node_type)
                    row += 2
                proc_ui_duration = (time.perf_counter() - proc_ui_start) * 1000.0

            if not file_textures_only and not procedural_textures_only:
                # Show empty state message when no textures exist
                self._add_empty_state_message(scroll_layout, row, 'file_textures', flags)
                row += 1
            textures_tab['row'] = row

        # --- Add SHADING GROUPS section (populate ALL tabs, not just active) ---
        shading_tab = tab_layouts.get('shading_groups')
        if shading_tab:
            scroll_layout = shading_tab['layout']
            row = shading_tab['row']
            # Populate shading group entries or show empty message
            if shading_groups_only:
                sg_ui_start = time.perf_counter()
                for sg in shading_groups_only:
                    node_type = node_classifications.get(sg)
                    self.add_material_entry_optimized(sg, row, scroll_layout, DEFAULT_MATERIALS, saved_selection, material_properties, node_type)
                    self.add_material_buttons(sg, row, scroll_layout, False, node_type_category=node_type)
                    row += 2
                sg_ui_duration = (time.perf_counter() - sg_ui_start) * 1000.0
            else:
                # Show empty state message
                self._add_empty_state_message(scroll_layout, row, 'shading_groups', flags)
                row += 1
            shading_tab['row'] = row

        # --- Add UTILITIES section (LAZY LOADED: Only populate if already loaded) ---
        utilities_tab = tab_layouts.get('utilities')
        if utilities_tab:
            scroll_layout = utilities_tab['layout']
            row = utilities_tab['row']
            if self._utilities_tab_populated and utilities_only:
                util_ui_start = time.perf_counter()
                for node in utilities_only:
                    node_type = node_classifications.get(node)
                    self.add_material_entry_optimized(node, row, scroll_layout, DEFAULT_MATERIALS, saved_selection, material_properties, node_type)
                    self.add_material_buttons(node, row, scroll_layout, False, node_type_category=node_type)
                    row += 2
                util_ui_duration = (time.perf_counter() - util_ui_start) * 1000.0
            else:
                # Show placeholder message - will be populated on first tab access
                placeholder_label = QtWidgets.QLabel("Utilities will load when you switch to this tab")
                placeholder_label.setAlignment(QtCore.Qt.AlignCenter)
                placeholder_label.setStyleSheet("color: #888; padding: 20px;")
                scroll_layout.addWidget(placeholder_label, row, 0, 1, 4)
                row += 1
            utilities_tab['row'] = row
        
        ui_create_duration = (time.perf_counter() - ui_create_start) * 1000.0
        total_entries = len(materials_only) + len(file_textures_only) + len(procedural_textures_only) + len(shading_groups_only) + len(utilities_only)
        
        # Print UI creation timing breakdown
        ui_breakdown = []
        if materials_only:
            ui_breakdown.append(f"materials={materials_ui_duration:.2f}ms")
        if file_textures_only:
            ui_breakdown.append(f"file_textures={file_ui_duration:.2f}ms")
        if procedural_textures_only:
            ui_breakdown.append(f"proc_textures={proc_ui_duration:.2f}ms")
        if shading_groups_only:
            ui_breakdown.append(f"shading_groups={sg_ui_duration:.2f}ms")
        if utilities_only and self._utilities_tab_populated:
            ui_breakdown.append(f"utilities={util_ui_duration:.2f}ms")

        # PERFORMANCE OPTIMIZATION: Batch style updates for all widgets
        if hasattr(self, '_pending_style_updates') and self._pending_style_updates:
            style_start = time.perf_counter()
            style_count = len(self._pending_style_updates)
            for widget in self._pending_style_updates:
                try:
                    widget.style().unpolish(widget)
                    widget.style().polish(widget)
                    widget.update()
                except Exception:
                    pass
            style_duration = (time.perf_counter() - style_start) * 1000.0
            self._pending_style_updates = []

        # PERFORMANCE OPTIMIZATION: Batch-create icons asynchronously after UI is built
        if self._pending_icon_creations:
            icon_count = len(self._pending_icon_creations)
            swatch_count = sum(1 for icon in self._pending_icon_creations if icon.get('type') == 'swatch')
            
            # Initialize swatch timing tracking
            if swatch_count > 0:
                self._swatch_timing_start = time.perf_counter()
                self._swatch_timing_count = 0
                self._swatch_timing_total = 0.0
                self._swatch_timing_texture_total = 0.0
                self._swatch_timing_texture_count = 0
                self._swatch_timing_expected = swatch_count
            
            # Defer icon creation to avoid blocking UI
            QtCore.QTimer.singleShot(0, self._batch_create_icons)
        
        # PERFORMANCE OPTIMIZATION: Batch-create buttons asynchronously after UI is built
        # List buttons removed - no button creation needed
        
        # PERFORMANCE OPTIMIZATION: Batch-connect signals after UI is built
        if hasattr(self, "_pending_signal_connections") and self._pending_signal_connections:
            # Connect signals in batch to avoid blocking UI
            self._batch_connect_signals()
        
        # Clear shader types cache after UI is built
        if hasattr(self, '_current_shader_types'):
            del self._current_shader_types

        # Add spacers and install content per tab
        for tab_type, info in tab_layouts.items():
            layout = info['layout']
            row = info['row']
            spacer_item = QtWidgets.QSpacerItem(
                20, 40, QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Expanding
            )
            layout.addItem(spacer_item, row, 0, 1, 4)
            scroll_area = info['scroll_area']
            if scroll_area:
                # SMOOTH TRANSITION: Use a persistent container widget that never gets removed
                # This prevents layout recalculation at the scroll area level
                container_key = f'_persistent_container_{tab_type}'
                persistent_container = getattr(self, container_key, None)
                
                # Get old content widget (if any) and scroll position
                old_content = None
                old_scroll_pos = 0
                if persistent_container and isValid(persistent_container):
                    # Get the current content from the persistent container
                    layout = persistent_container.layout()
                    if layout and layout.count() > 0:
                        old_item = layout.itemAt(0)
                        if old_item:
                            old_content = old_item.widget()
                    # Get scroll position
                    try:
                        v_scrollbar = scroll_area.verticalScrollBar()
                        if v_scrollbar:
                            old_scroll_pos = v_scrollbar.value()
                    except Exception:
                        pass
                else:
                    # No persistent container yet, check if scroll area has a widget
                    try:
                        existing_widget = scroll_area.widget()
                        if existing_widget:
                            old_content = existing_widget
                            v_scrollbar = scroll_area.verticalScrollBar()
                            if v_scrollbar:
                                old_scroll_pos = v_scrollbar.value()
                    except Exception:
                        pass
                
                new_content = info['content']
                
                # Create or get persistent container
                if not persistent_container or not isValid(persistent_container):
                    persistent_container = QtWidgets.QWidget()
                    persistent_container.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
                    persistent_container.setMinimumWidth(0)
                    persistent_container.setStyleSheet(self.material_list_widget_style)
                    container_layout = QtWidgets.QVBoxLayout(persistent_container)
                    container_layout.setContentsMargins(0, 0, 0, 0)
                    container_layout.setSpacing(0)
                    setattr(self, container_key, persistent_container)
                    
                    # Set persistent container as scroll area widget (only once)
                    scroll_area.setWidget(persistent_container)
                
                # Get the container's layout
                container_layout = persistent_container.layout()
                
                # If there's old content, swap it smoothly
                if old_content and isValid(old_content):
                    # Remove old content from layout (but don't delete yet)
                    container_layout.removeWidget(old_content)
                    old_content.hide()
                    
                    # Add new content
                    container_layout.addWidget(new_content)
                    new_content.show()
                    
                    # Restore scroll position immediately
                    scroll_pos = saved_scroll_positions.get(tab_type, old_scroll_pos)
                    try:
                        v_scrollbar = scroll_area.verticalScrollBar()
                        if v_scrollbar:
                            v_scrollbar.setValue(scroll_pos)
                    except Exception:
                        pass
                    
                    # Clean up old content after a brief delay
                    def cleanup_old():
                        try:
                            if old_content and isValid(old_content):
                                old_content.setParent(None)
                                old_content.deleteLater()
                        except Exception:
                            pass
                    
                    QtCore.QTimer.singleShot(100, cleanup_old)
                else:
                    # No old content, just add new content
                    container_layout.addWidget(new_content)
                    new_content.show()
                
                # Restore scroll position for this tab if it was saved
                if tab_type in saved_scroll_positions:
                    saved_position = saved_scroll_positions[tab_type]
                    # Use QTimer to restore scroll position after layout is complete
                    # Use functools.partial to avoid lambda closure issues
                    QtCore.QTimer.singleShot(0, partial(self._restore_scroll_position, scroll_area, saved_position))
        
        self._rebuilding_list = False

        # Sync once: scene → list, then visuals
        if hasattr(self, "_sync_list_from_scene_selection"):
            self._sync_list_from_scene_selection()
        if hasattr(self, "_apply_selection_visuals"):
            self._apply_selection_visuals()
        duration_ms = (time.perf_counter() - start_time) * 1000.0
        total_nodes = len(nodes_to_display)
        texture_nodes = len(file_textures_only) + len(procedural_textures_only)
        
        # Print final summary

    def _populate_utilities_tab(self, force_refresh=False):
        """
        Populate the utilities tab with utility nodes.
        Uses lazy loading - only populates on first access or when force_refresh=True.
        Shows loading bar during background population.
        """
        if self._utilities_tab_populated and not force_refresh:
            return  # Already populated
        
        scroll_area = self._get_tab_scroll_area('utilities')
        if not scroll_area:
            return
        
        # Clear existing content
        self._clear_tab_scroll_area(scroll_area)
        
        # Create loading bar widget
        loading_widget = QtWidgets.QWidget()
        loading_widget.setStyleSheet(self.material_list_widget_style)
        loading_layout = QtWidgets.QVBoxLayout(loading_widget)
        loading_layout.setContentsMargins(20, 20, 20, 20)
        loading_layout.setSpacing(10)
        
        loading_label = QtWidgets.QLabel("Loading utilities...")
        loading_label.setAlignment(QtCore.Qt.AlignCenter)
        loading_label.setStyleSheet("color: #888; font-size: 14px;")
        loading_layout.addWidget(loading_label)
        
        progress_bar = QtWidgets.QProgressBar()
        progress_bar.setRange(0, 0)  # Indeterminate progress
        progress_bar.setTextVisible(False)
        progress_bar.setFixedHeight(6)  # thinner loading bar
        progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #555;
                border-radius: 3px;
                background-color: #2a2a2a;
                min-height: 4px;
                max-height: 6px;
            }
            QProgressBar::chunk {
                background-color: #00f7c8;
            }
        """)
        loading_layout.addWidget(progress_bar)
        loading_layout.addStretch()
        
        scroll_area.setWidget(loading_widget)
        
        # Defer actual collection and population to background
        QtCore.QTimer.singleShot(50, lambda: self._populate_utilities_tab_background(scroll_area, loading_widget))
    
    def _populate_utilities_tab_background(self, scroll_area, loading_widget):
        """
        Background worker to collect and populate utilities tab.
        This runs after a short delay to keep UI responsive.
        """
        try:
            start_time = time.perf_counter()
            
            # Collect utility nodes connected to shaders
            collect_start = time.perf_counter()
            utility_nodes = self._get_utility_nodes()
            collect_duration = (time.perf_counter() - collect_start) * 1000.0
            
            # Cache the results for use in _populate_materials_list (avoids repopulation when other filters change)
            self._utilities_cache = utility_nodes
            self._utilities_tab_populated = True
            
            # Now populate the UI
            self._populate_utilities_tab_ui(scroll_area, utility_nodes, loading_widget)
            
            total_duration = (time.perf_counter() - start_time) * 1000.0
        except Exception as e:
            import traceback
            traceback.print_exc()
            # Show error message
            error_widget = QtWidgets.QWidget()
            error_widget.setStyleSheet(self.material_list_widget_style)
            error_layout = QtWidgets.QVBoxLayout(error_widget)
            error_layout.setContentsMargins(20, 20, 20, 20)
            error_label = QtWidgets.QLabel(f"Error loading utilities: {str(e)}")
            error_label.setAlignment(QtCore.Qt.AlignCenter)
            error_label.setStyleSheet("color: #f00;")
            error_layout.addWidget(error_label)
            scroll_area.setWidget(error_widget)
    
    def _populate_utilities_tab_ui(self, scroll_area, utility_nodes, loading_widget):
        """
        Create the actual UI for utilities tab.
        """
        # Remove loading widget
        if loading_widget:
            scroll_area.takeWidget()
            loading_widget.deleteLater()
        
        # Create scroll content
        scroll_content = MaterialListScrollContent(self)
        scroll_content.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        scroll_content.setMinimumWidth(0)
        scroll_content.setStyleSheet(self.material_list_widget_style)
        layout = QtWidgets.QGridLayout(scroll_content)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setVerticalSpacing(2)
        layout.setHorizontalSpacing(3)
        row = 0
        
        # Add active filters bar
        consumed = self._add_active_filters_bar(layout, row)
        row += consumed
        
        if utility_nodes:
            # Get material properties for utilities
            current_sel_shapes = self._get_all_shapes_from_selection() or []
            material_properties = self._batch_compute_material_properties(utility_nodes, current_sel_shapes)
            
            # Classify nodes
            node_classifications = {}
            for node in utility_nodes:
                node_classifications[node] = self._classify_node_type(node)
            
            # Sort utilities
            DEFAULT_MATERIALS = getattr(self, "DEFAULT_MATERIALS", {'lambert1', 'standardSurface1', 'openPBR_shader1'})
            sorted_utilities = self._sort_materials(utility_nodes, utility_nodes)
            
            # Create UI entries
            for node in sorted_utilities:
                node_type = node_classifications.get(node)
                self.add_material_entry_optimized(node, row, layout, DEFAULT_MATERIALS, None, material_properties, node_type)
                self.add_material_buttons(node, row, layout, False, node_type_category=node_type)
                row += 2
        else:
            # Show empty state
            flags = self._collect_filter_flags()
            self._add_empty_state_message(layout, row, 'utilities', flags)
            row += 1
        
        # Add spacer
        spacer_item = QtWidgets.QSpacerItem(
            20, 40, QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Expanding
        )
        layout.addItem(spacer_item, row, 0, 1, 4)
        
        # Set widget
        scroll_area.setWidget(scroll_content)
        
        # Batch style updates
        if hasattr(self, '_pending_style_updates') and self._pending_style_updates:
            for widget in self._pending_style_updates:
                try:
                    widget.style().unpolish(widget)
                    widget.style().polish(widget)
                    widget.update()
                except Exception:
                    pass
            self._pending_style_updates = []
        
        # Defer icon creation
        if self._pending_icon_creations:
            QtCore.QTimer.singleShot(0, self._batch_create_icons)
        
        # Defer signal connections
        if self._pending_signal_connections:
            QtCore.QTimer.singleShot(0, self._batch_connect_signals)

    def _add_type_header(self, grid_layout, row, type_name):
        """Add a thin, full-width orange separator row for a material type chunk."""
        bar = QtWidgets.QWidget()
        bar.setObjectName("qmTypeHeader")
        bar.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        bar.setAutoFillBackground(True)

        bar.setStyleSheet("""
            QWidget#qmTypeHeader {
                background-color: #2a2a2a;    /* darker background for type headers */
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

    def _update_header_frames_visibility(self, show_shaders, show_textures, show_shading_groups, show_utilities):
        """
        Show/hide header label frames based on which tab is currently active.
        Frames: shadersHeaderLabelFrame, texturesHeaderLabelFrame, 
                shadingGroupsHeaderLabelFrame, utilitiesHeaderLabelFrame
        """
        frame_map = {
            'shadersHeaderLabelFrame': show_shaders,
            'texturesHeaderLabelFrame': show_textures,
            'shadingGroupsHeaderLabelFrame': show_shading_groups,
            'utilitiesHeaderLabelFrame': show_utilities,
        }
        
        for frame_name, should_show in frame_map.items():
            frame = self._get_widget(frame_name, QtWidgets.QWidget)
            if frame:
                frame.setVisible(should_show)

    def _add_empty_state_message(self, grid_layout, row, node_type_key, filter_flags=None):
        """
        Add an italic empty state message when there are no items of a given type.
        node_type_key: 'materials', 'file_textures', 'procedural_textures', 'shading_groups', or 'utilities'
        filter_flags: Optional dict of filter flags to check for special cases
        """
        config = self.NODE_TYPES.get(node_type_key, {})
        header_text = config.get('header_text', node_type_key)
        
        # Create empty state message widget
        empty_widget = QtWidgets.QWidget()
        empty_widget.setObjectName(f"qm{node_type_key.title().replace('_', '')}EmptyState")
        empty_widget.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        
        empty_widget.setStyleSheet(f"""
            QWidget#{empty_widget.objectName()} {{
                background-color: transparent;
                border: none;
            }}
            QWidget#{empty_widget.objectName()} QLabel {{
                color: #888888;
                font-style: italic;
                padding: 4px 8px;
                font-size: 11px;
            }}
        """)
        
        lay = QtWidgets.QHBoxLayout(empty_widget)
        lay.setContentsMargins(8, 2, 2, 2)
        lay.setSpacing(0)
        
        # Check if both Selected and Unused filters are active
        if filter_flags and filter_flags.get("selected", False) and filter_flags.get("unUsed", False):
            message_text = "Selected and Unused..? Impossible!"
        else:
            # Create empty state message text with specific messages per node type
            empty_messages = {
                'materials': "No Shaders Found",
                'file_textures': "No Textures Found",
                'procedural_textures': "No Procedural Textures Found",
                'shading_groups': "No Shading Groups Found",
                'utilities': "No Utility Nodes Found",
            }
            
            message_text = empty_messages.get(node_type_key, f"No {header_text} Found")
        
        empty_label = QtWidgets.QLabel(message_text)
        empty_label.setTextInteractionFlags(QtCore.Qt.NoTextInteraction)
        lay.addWidget(empty_label)
        lay.addStretch(1)
        
        grid_layout.addWidget(empty_widget, row, 0, 1, 4)

    def _strip_namespace(self, name):
        """
        Strip namespace from node name if hideNamespacesCheckbox is checked.
        """
        try:
            hide_namespaces_cb = self._get_widget('hideNamespacesCheckbox', QtWidgets.QCheckBox)
            if hide_namespaces_cb and hide_namespaces_cb.isChecked():
                # Get the short name (last part after the last colon)
                if ':' in name:
                    return name.split(':')[-1]
        except Exception:
            pass
        return name
    
    def _update_namespace_display(self):
        """
        Update display names of all existing entries when hideNamespaces checkbox is toggled.
        This updates the display without rebuilding the list, preserving search state and visibility.
        """
        if not hasattr(self, '_entry_list') or not self._entry_list:
            # If list hasn't been populated yet, do a full refresh
            self.refresh_materials_list()
            return
        
        # Get hideNamespaces checkbox state
        hide_namespaces = False
        if self._cached_hide_namespaces_cb and isValid(self._cached_hide_namespaces_cb):
            try:
                hide_namespaces = self._cached_hide_namespaces_cb.isChecked()
            except RuntimeError:
                self._cached_hide_namespaces_cb = self._get_widget('hideNamespacesCheckbox', QtWidgets.QCheckBox)
                if self._cached_hide_namespaces_cb and isValid(self._cached_hide_namespaces_cb):
                    try:
                        hide_namespaces = self._cached_hide_namespaces_cb.isChecked()
                    except RuntimeError:
                        hide_namespaces = False
        else:
            # Cache not available, get it directly
            self._cached_hide_namespaces_cb = self._get_widget('hideNamespacesCheckbox', QtWidgets.QCheckBox)
            if self._cached_hide_namespaces_cb and isValid(self._cached_hide_namespaces_cb):
                try:
                    hide_namespaces = self._cached_hide_namespaces_cb.isChecked()
                except RuntimeError:
                    hide_namespaces = False
        
        updated_count = 0
        # Update display names for all entries
        for entry in self._entry_list:
            # Get the actual material name (with namespace) from the entry
            material = entry.get('material')
            if not material:
                continue
            
            container = entry.get('container')
            if not container or not isValid(container):
                continue
            
            # Find the display widget (could be QLineEdit or QLabel)
            material_widget = None
            line_edit = entry.get('line_edit')
            if line_edit and isValid(line_edit):
                material_widget = line_edit
            elif hasattr(container, '_qm_line_edit'):
                material_widget = container._qm_line_edit
            else:
                # Try to find it
                material_widget = container.findChild(QtWidgets.QLineEdit)
                if not material_widget:
                    material_widget = container.findChild(QtWidgets.QLabel)
            
            if not material_widget or not isValid(material_widget):
                continue
            
            # Use the material from the entry (full name with namespace)
            actual_name = material
            
            # Calculate display name based on hideNamespaces setting
            if hide_namespaces and ':' in actual_name:
                display_name = actual_name.split(':')[-1]
            else:
                display_name = actual_name
            
            # For file textures, we need to preserve the rich text format
            node_type = material_widget.property("nodeType")
            if node_type == "file_texture":
                # File textures use rich text labels - need to rebuild the display text
                try:
                    info = self._get_file_texture_display_info(actual_name)
                    if info and info['filename']:
                        display_text = f'<span style="color: #e0e0e0;">{info["filename"]}</span>'
                        if info['udim_count'] > 1:
                            display_text += f'  <span style="color: #6fa3d8;">({info["udim_count"]} tiles)</span>'
                        if info['colorspace']:
                            display_text += f'  <span style="color: #999999;">({info["colorspace"]})</span>'
                        if isinstance(material_widget, QtWidgets.QLabel):
                            material_widget.setText(display_text)
                            updated_count += 1
                        continue
                except Exception as e:
                    pass
                # Fallback for file textures
                display_text = f'<span style="color: #e0e0e0;">{display_name}</span>'
                if isinstance(material_widget, QtWidgets.QLabel):
                    material_widget.setText(display_text)
                    updated_count += 1
            else:
                # For other types, just update the text
                # Preserve secondary text if it exists (for materials with shader type)
                if isinstance(material_widget, QtWidgets.QLineEdit):
                    # Check if it has secondary text (shader type) that we need to preserve
                    if hasattr(material_widget, '_secondary_text') and material_widget._secondary_text:
                        # Secondary text is handled by the widget itself, just update main text
                        material_widget.setText(display_name)
                    else:
                        material_widget.setText(display_name)
                    updated_count += 1
                elif isinstance(material_widget, QtWidgets.QLabel):
                    material_widget.setText(display_name)
                    updated_count += 1
        

    def _acquire_material_row(self):
        """
        Return a QWidget + LeftClipLineEdit pair for a material row, reusing pooled widgets when available.
        """
        pool = getattr(self, "_material_row_pool", [])
        container = None
        while pool:
            container = pool.pop()
            if container is not None:
                break
        line_edit = None
        if container is not None:
            # Clean up any existing swatch icons when reusing containers
            layout = container.layout()
            if layout:
                # Update layout margins based on current scale (for reused containers)
                current_scale = self._list_entry_scale_sizes[self._list_entry_scale_level]
                margin = current_scale['layout_margin']
                # Ensure margins are integers
                layout.setContentsMargins(int(margin[0]), int(margin[1]), int(margin[2]), int(margin[3]))
                # Update container padding
                container_padding = current_scale['container_padding']
                container.setContentsMargins(container_padding, container_padding, container_padding, container_padding)
                
                # Remove any MaterialSwatchIcon widgets
                if MaterialSwatchIcon is not None:
                    for i in range(layout.count() - 1, -1, -1):  # Iterate backwards to safely remove
                        item = layout.itemAt(i)
                        if item and item.widget():
                            widget = item.widget()
                            if isinstance(widget, MaterialSwatchIcon):
                                layout.removeWidget(widget)
                                widget.setParent(None)
                                widget.deleteLater()
            
            line_edit = getattr(container, "_qm_line_edit", None)
            if line_edit is None:
                line_edit = container.findChild(QtWidgets.QLineEdit)
                container._qm_line_edit = line_edit
        if line_edit is not None:
            # PERFORMANCE OPTIMIZATION: Batch property resets for reused widgets
            # Only set properties if they need to change (minimize style recalculations)
            if not line_edit.isReadOnly():
                line_edit.setReadOnly(True)
            # Reset properties to default state for reuse
            line_edit.setProperty("editing", "false")
            line_edit.setProperty("qmEditMode", "false")
            if hasattr(line_edit, "clearSecondaryText"):
                line_edit.clearSecondaryText()
        else:
            container = QtWidgets.QWidget()
            layout = QtWidgets.QHBoxLayout(container)
            # Use current scale for layout margins
            current_scale = self._list_entry_scale_sizes[self._list_entry_scale_level]
            margin = current_scale['layout_margin']
            layout.setContentsMargins(margin[0], margin[1], margin[2], margin[3])
            layout.setSpacing(2)  # Reduced spacing for tighter layout
            # Set container padding based on scale
            container_padding = current_scale['container_padding']
            container.setContentsMargins(container_padding, container_padding, container_padding, container_padding)
            line_edit = MaterialDisplayLineEdit("")
            line_edit.setObjectName("qmMaterialLineEdit")
            # Set default properties for new widgets (reused widgets get these in the if block above)
            line_edit.setProperty("editing", "false")
            line_edit.setProperty("qmEditMode", "false")
            try:
                line_edit.editingFinished.connect(partial(self.rename_material, line_edit))
                line_edit.returnPressed.connect(partial(self.rename_material, line_edit))
            except AttributeError:
                print("Error: rename_material function not found")
            layout.addWidget(line_edit)
            container._qm_line_edit = line_edit
        if container is not None:
            container.hide()
            container.setParent(None)
        return container, line_edit

    def add_material_entry_optimized(self, material, row, scroll_layout, default_materials, saved_selection=None, material_properties=None, node_type_category=None):
        """
        OPTIMIZED VERSION: Create material entry using pre-computed colors to avoid Maya API calls.
        
        Args:
            node_type_category: Pre-computed node type classification (optional, will compute if None)
        """
        # Debug: Track lambert1 at function entry
        # Strip namespace if option is enabled (use cached checkbox reference)
        # Check validity of cached checkbox before accessing (may be deleted in background threads)
        hide_namespaces = False
        if self._cached_hide_namespaces_cb and isValid(self._cached_hide_namespaces_cb):
            try:
                hide_namespaces = self._cached_hide_namespaces_cb.isChecked()
            except RuntimeError:
                # Widget was deleted, refresh cache and try again
                self._cached_hide_namespaces_cb = self._get_widget('hideNamespacesCheckbox', QtWidgets.QCheckBox)
                if self._cached_hide_namespaces_cb and isValid(self._cached_hide_namespaces_cb):
                    try:
                        hide_namespaces = self._cached_hide_namespaces_cb.isChecked()
                    except RuntimeError:
                        hide_namespaces = False
        
        if hide_namespaces:
            if ':' in material:
                display_name = material.split(':')[-1]
            else:
                display_name = material
        else:
            display_name = material

        # Classify the node type to determine display and behavior (use cached if provided)
        if node_type_category is None:
            node_type_category = self._classify_node_type(material)
        
        is_file_texture = (node_type_category == 'file_textures')
        is_procedural_texture = (node_type_category == 'procedural_textures')
        is_shading_group = (node_type_category == 'shading_groups')
        is_material = (node_type_category == 'materials')
        is_utility = (node_type_category == 'utilities')
        
        # PERFORMANCE: Use pre-computed shader type if available
        shader_type = None
        if is_material:
            # Try to get from pre-computed shader_types dict (passed from populate_materials_scroll_area)
            if hasattr(self, '_current_shader_types') and material in self._current_shader_types:
                shader_type = self._current_shader_types[material]
            else:
                # Fallback to individual lookup (shouldn't happen if batch computation works)
                shader_type = self._get_material_shader_type(material)
        node_type_name = None
        if is_utility:
            try:
                node_type_name = cmds.nodeType(material)
            except Exception:
                node_type_name = None

        # Create display widget based on node type
        display_text = display_name  # Use display_name which may have namespace stripped
        use_rich_text_label = False
        
        if is_file_texture:
            # File textures: always use rich text label for consistent formatting
            use_rich_text_label = True
            try:
                info = self._get_file_texture_display_info(material)
                if info and info['filename']:
                    # Build HTML formatted text with colors:
                    # - Filename: white (#e0e0e0)
                    # - UDIM count: blue (#6fa3d8)
                    # - Colorspace: grey (#999999)
                    display_text = f'<span style="color: #e0e0e0;">{info["filename"]}</span>'
                    
                    # Add UDIM count if applicable (in blue)
                    if info['udim_count'] > 1:
                        display_text += f'  <span style="color: #6fa3d8;">({info["udim_count"]} tiles)</span>'
                    
                    # Add colorspace in brackets (in grey)
                    if info['colorspace']:
                        display_text += f'  <span style="color: #999999;">({info["colorspace"]})</span>'
                else:
                    # No filename loaded - show node name in same format for consistency
                    display_text = f'<span style="color: #e0e0e0;">{display_name}</span>'
            except Exception:
                # Fallback to node name if getting info fails
                display_text = f'<span style="color: #e0e0e0;">{display_name}</span>'

        if node_type_category == 'materials':
            container, material_widget = self._acquire_material_row()
            material_layout = container.layout()
            material_widget.setText(display_name)
            if isinstance(material_widget, MaterialDisplayLineEdit):
                material_widget.setSecondaryText(shader_type)
            
            # PERFORMANCE OPTIMIZATION: Defer swatch icon creation until after UI is built
            if MaterialSwatchIcon is not None:
                # Check if show shader swatches checkbox is enabled
                # Use cached checkbox reference to avoid repeated _get_widget() calls
                show_icons = True  # Default to True
                if self._cached_show_icons_cb and isValid(self._cached_show_icons_cb):
                    try:
                        show_icons = self._cached_show_icons_cb.isChecked()
                    except RuntimeError:
                        # Widget was deleted, refresh cache
                        self._cached_show_icons_cb = self._get_widget('showIconsCheckbox', QtWidgets.QCheckBox)
                        if self._cached_show_icons_cb and isValid(self._cached_show_icons_cb):
                            try:
                                show_icons = self._cached_show_icons_cb.isChecked()
                            except RuntimeError:
                                show_icons = True
                
                if show_icons:
                    # Queue icon creation instead of creating immediately
                    current_scale = self._list_entry_scale_sizes[self._list_entry_scale_level]
                    self._pending_icon_creations.append({
                        'type': 'swatch',
                        'material': material,
                        'container': container,
                        'layout': material_layout,
                        'position': 0,  # Insert at beginning
                        'icon_size': current_scale['icon']
                    })
        else:
            container = QtWidgets.QWidget()
            material_layout = QtWidgets.QHBoxLayout()
            # Use current scale for layout margins
            current_scale = self._list_entry_scale_sizes[self._list_entry_scale_level]
            margin = current_scale['layout_margin']
            # Ensure margins are integers
            material_layout.setContentsMargins(int(margin[0]), int(margin[1]), int(margin[2]), int(margin[3]))
            material_layout.setSpacing(3)
            container.setLayout(material_layout)
            if use_rich_text_label:
                material_widget = TextureDisplayLabel(display_text)
            else:
                material_widget = LeftClipLineEdit(display_text)
            
            # Check if show icons checkbox is enabled (controls all icons: swatches, textures, shading groups)
            # Use cached checkbox reference to avoid repeated _get_widget() calls
            show_icons = True  # Default to True
            if self._cached_show_icons_cb and isValid(self._cached_show_icons_cb):
                try:
                    show_icons = self._cached_show_icons_cb.isChecked()
                except RuntimeError:
                    # Widget was deleted, refresh cache
                    self._cached_show_icons_cb = self._get_widget('showIconsCheckbox', QtWidgets.QCheckBox)
                    if self._cached_show_icons_cb and isValid(self._cached_show_icons_cb):
                        try:
                            show_icons = self._cached_show_icons_cb.isChecked()
                        except RuntimeError:
                            show_icons = True
            
            # PERFORMANCE OPTIMIZATION: Defer texture icon creation until after UI is built
            if is_file_texture and show_icons:
                # Reduced left spacer to move icon and entry closer to the left
                spacer = QtWidgets.QSpacerItem(2, 1, QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Minimum)
                material_layout.addItem(spacer)
                
                # Queue icon creation instead of creating immediately
                # Store position as 1 (after spacer at index 0, before material widget)
                current_scale = self._list_entry_scale_sizes[self._list_entry_scale_level]
                self._pending_icon_creations.append({
                    'type': 'texture',
                    'material': material,
                    'container': container,
                    'layout': material_layout,
                    'position': 1,  # Insert after spacer, before material widget
                    'icon_size': current_scale['icon'],
                    'spacer': spacer  # Store spacer reference
                })
            
            # PERFORMANCE OPTIMIZATION: Defer procedural texture icon creation until after UI is built
            if is_procedural_texture and show_icons:
                # Reduced left spacer to move icon and entry closer to the left
                spacer = QtWidgets.QSpacerItem(2, 1, QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Minimum)
                material_layout.addItem(spacer)
                
                # Queue icon creation instead of creating immediately
                # Store position as 1 (after spacer at index 0, before material widget)
                current_scale = self._list_entry_scale_sizes[self._list_entry_scale_level]
                self._pending_icon_creations.append({
                    'type': 'procedural_texture',
                    'material': material,
                    'container': container,
                    'layout': material_layout,
                    'position': 1,  # Insert after spacer, before material widget
                    'icon_size': current_scale['icon'],
                    'spacer': spacer  # Store spacer reference
                })
            
            # PERFORMANCE OPTIMIZATION: Defer utility icon creation until after UI is built
            if is_utility and show_icons:
                spacer = QtWidgets.QSpacerItem(2, 1, QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Minimum)
                material_layout.addItem(spacer)

                # Queue icon creation instead of creating immediately
                # Store position as 1 (after spacer at index 0, before material widget)
                current_scale = self._list_entry_scale_sizes[self._list_entry_scale_level]
                self._pending_icon_creations.append({
                    'type': 'utility',
                    'material': material,
                    'container': container,
                    'layout': material_layout,
                    'position': 1,  # Insert after spacer, before material widget
                    'icon_size': current_scale['icon'],
                    'node_type_name': node_type_name,
                    'spacer': spacer  # Store spacer reference
                })
            
            material_layout.addWidget(material_widget)
            # Set container padding based on current scale (for non-material entries)
            current_scale = self._list_entry_scale_sizes[self._list_entry_scale_level]
            container_padding = current_scale['container_padding']
            container.setContentsMargins(container_padding, container_padding, container_padding, container_padding)
            
            # Set font size and height for non-material entries (file textures, procedural, etc.)
            min_height = max(22, current_scale['icon'] + 2)
            material_widget.setMinimumHeight(min_height)
            if is_file_texture:
                material_widget.setMaximumHeight(min_height)
            # Set font size (works for both QLineEdit and QLabel)
            # Use both setFont and setStyleSheet to ensure it overrides the base stylesheet
            # Convert pt to px: use px directly to match base stylesheet
            font_size_px = current_scale['font']
            font = QtGui.QFont(material_widget.font())
            font.setPixelSize(font_size_px)
            material_widget.setFont(font)
            # Also set via stylesheet with !important to override base stylesheet font-size (use px to match base)
            material_widget.setStyleSheet(f"font-size: {font_size_px}px !important;")
            # Force update to ensure font change is visible
            material_widget.update()
        
        # Store the actual material name for operations
        material_widget._actual_material_name = material
        
        # Link clicks on the line edit to Outliner-style selection (owner + method name, guarded)
        material_widget.setSelectionHandler(self, "handle_item_click", material)
        # Start unselected (qmEditMode already set in _acquire_material_row)
        material_widget.setProperty("qmSelected", "false")
        # Note: qmEditMode is already set in _acquire_material_row, so we skip duplicate set here
        
        # Set nodeType property based on classification for CSS styling
        if is_file_texture:
            material_widget.setProperty("nodeType", "file_texture")
        elif is_procedural_texture:
            material_widget.setProperty("nodeType", "procedural_texture")
        elif is_shading_group:
            material_widget.setProperty("nodeType", "shading_group")
        elif is_utility:
            material_widget.setProperty("nodeType", "utility")
        else:
            material_widget.setProperty("nodeType", "material")

        # Check if unused materials/textures should be highlighted
        is_unused = False
        if material_properties:
            props = material_properties.get(material, {})
            is_unused = not props.get('used', False)  # If 'used' is False or missing, it's unused
        
        # Check if highlight unused checkbox is checked (use cached checkbox reference)
        should_highlight_unused = False
        if self._cached_highlight_unused_cb and isValid(self._cached_highlight_unused_cb):
            try:
                should_highlight_unused = self._cached_highlight_unused_cb.isChecked()
            except RuntimeError:
                # Widget was deleted, refresh cache
                self._cached_highlight_unused_cb = self._get_widget('highlightUnusedCheckbox', QtWidgets.QCheckBox)
                if self._cached_highlight_unused_cb and isValid(self._cached_highlight_unused_cb):
                    try:
                        should_highlight_unused = self._cached_highlight_unused_cb.isChecked()
                    except RuntimeError:
                        should_highlight_unused = False
        
        # Exclude default materials from unused highlighting
        is_default = material in default_materials if default_materials else False
        highlight_unused_applicable = should_highlight_unused and is_unused and not is_utility and not is_default
        material_widget.setProperty("qmUnused", "true" if highlight_unused_applicable else "false")

        # PERFORMANCE OPTIMIZATION: Defer style updates - will be batched after all widgets are created
        # Store widget reference for batch style update
        if not hasattr(self, '_pending_style_updates'):
            self._pending_style_updates = []
        self._pending_style_updates.append(material_widget)

        # Register this row for ordered selection behavior
        self._register_material_entry(material, None, material_widget, is_default=(material in default_materials), container=container)

        material_widget.setMinimumWidth(120)
        if material not in default_materials:
            material_widget.setProperty("materialType", "")

        # PERFORMANCE OPTIMIZATION: Defer signal connections until after UI is built
        if material in default_materials:
            # Default materials: editable like other materials, just marked with italic text
            material_widget.setProperty("materialType", "default")
            material_widget.setProperty("editing", "false")   # ensure non-editing visual
            # Queue signal connections for default materials
            if isinstance(material_widget, QtWidgets.QLineEdit):
                if not hasattr(self, '_pending_signal_connections'):
                    self._pending_signal_connections = []
                self._pending_signal_connections.append({
                    'widget': material_widget,
                    'signal': 'editingFinished',
                    'handler': partial(self.rename_material, material_widget)
                })
                self._pending_signal_connections.append({
                    'widget': material_widget,
                    'signal': 'returnPressed',
                    'handler': partial(self.rename_material, material_widget)
                })
        elif is_file_texture:
            # File textures: Read-only (QLabel with rich text), not renamable
            material_widget.setProperty("editing", "false")
        elif is_procedural_texture or is_shading_group or is_utility:
            # Procedural textures and shading groups: Renamable
            if isinstance(material_widget, QtWidgets.QLineEdit):
                if not hasattr(self, '_pending_signal_connections'):
                    self._pending_signal_connections = []
                self._pending_signal_connections.append({
                    'widget': material_widget,
                    'signal': 'editingFinished',
                    'handler': partial(self.rename_texture, material_widget)
                })
                self._pending_signal_connections.append({
                    'widget': material_widget,
                    'signal': 'returnPressed',
                    'handler': partial(self.rename_texture, material_widget)
                })
            material_widget.setProperty("editing", "false")
        elif is_material:
            material_widget.setProperty("editing", "false")
        else:
            # Regular materials: editable (only QLineEdit supports editing)
            if isinstance(material_widget, QtWidgets.QLineEdit):
                if not hasattr(self, '_pending_signal_connections'):
                    self._pending_signal_connections = []
                self._pending_signal_connections.append({
                    'widget': material_widget,
                    'signal': 'editingFinished',
                    'handler': partial(self.rename_material, material_widget)
                })
                self._pending_signal_connections.append({
                    'widget': material_widget,
                    'signal': 'returnPressed',
                    'handler': partial(self.rename_material, material_widget)
                })

        # Apply sizes; container already has the stylesheet
        material_widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                      QtWidgets.QSizePolicy.Fixed)  # Fixed height fits style

        # Use current scale for minimum height
        current_scale = self._list_entry_scale_sizes[self._list_entry_scale_level]
        min_height = max(22, current_scale['icon'] + 2)
        material_widget.setMinimumHeight(min_height)
        # For file textures, lock the height to prevent rich text from expanding
        if is_file_texture:
            material_widget.setMaximumHeight(min_height)
        
        # Set font size based on current scale (for both QLineEdit and QLabel)
        # Use both setFont and setStyleSheet to ensure it overrides the base stylesheet
        # Convert pt to px: 1pt ≈ 1.33px at 96 DPI, but we'll use px directly to match base stylesheet
        font_size_px = current_scale['font']
        font = QtGui.QFont(material_widget.font())
        font.setPixelSize(font_size_px)
        material_widget.setFont(font)
        # Also set via stylesheet with !important to override base stylesheet font-size (use px to match base)
        material_widget.setStyleSheet(f"font-size: {font_size_px}px !important;")
        # Force update to ensure font change is visible
        material_widget.update()

        # Use current scale for container padding
        container_padding = current_scale['container_padding']
        container.setContentsMargins(container_padding, container_padding, container_padding, container_padding)
        
        # Set layout margins based on current scale for ALL entry types
        margin = current_scale['layout_margin']
        # Ensure margins are integers
        material_layout.setContentsMargins(int(margin[0]), int(margin[1]), int(margin[2]), int(margin[3]))
        # Force layout update
        material_layout.update()

        # PERFORMANCE OPTIMIZATION: Stylesheet is already set on parent scroll_content,
        # so we don't need to set it on every container (stylesheets cascade in Qt)

        # Add to the grid layout
        scroll_layout.addWidget(container, row, 0, 1, 4)
        container.show()

        # Handle saved selection state
        if saved_selection and material in saved_selection:
            self._select_material_entry(material, True, update_visuals=False)

    def _batch_create_icons(self):
        """
        PERFORMANCE OPTIMIZATION: Batch-create all queued icons asynchronously.
        This is called after UI is built to avoid blocking the main UI creation.
        """
        if not self._pending_icon_creations:
            # Check if we should report swatch timing summary
            self._check_swatch_timing_summary()
            return
        
        batch_start = time.perf_counter()
        icon_count = len(self._pending_icon_creations)
        
        # Count by type
        swatch_count = sum(1 for icon in self._pending_icon_creations if icon.get('type') == 'swatch')
        texture_count = sum(1 for icon in self._pending_icon_creations if icon.get('type') == 'texture')
        proc_texture_count = sum(1 for icon in self._pending_icon_creations if icon.get('type') == 'procedural_texture')
        utility_count = sum(1 for icon in self._pending_icon_creations if icon.get('type') == 'utility')
        
        # Get isValid function for validation
        try:
            from shiboken2 import isValid
        except Exception:
            try:
                from shiboken6 import isValid
            except Exception:
                isValid = lambda obj: bool(obj)
        
        # Process icons in batches to avoid blocking UI
        batch_size = 10  # Create 10 icons at a time
        created = 0
        
        for i, icon_info in enumerate(self._pending_icon_creations):
            try:
                icon_type = icon_info['type']
                material = icon_info['material']
                container = icon_info.get('container')
                layout = icon_info.get('layout')
                icon_size = icon_info.get('icon_size', 20)
                position = icon_info.get('position', -1)
                
                # Validate container and layout before creating icons
                # Basic validation - just check they exist
                if not container:
                    continue
                if not layout:
                    continue
                
                # Try to validate container is still alive (but don't be too strict)
                # Just try to access a simple property - if it fails, the container is deleted
                try:
                    _ = container.parent()
                except (RuntimeError, AttributeError):
                    # Container is deleted, skip
                    continue
                
                if icon_type == 'swatch':
                    if MaterialSwatchIcon is not None:
                        try:
                            swatch_icon = MaterialSwatchIcon(material, icon_size=icon_size, parent=container)
                            swatch_icon.setFixedSize(icon_size, icon_size)
                            # Enable scaled contents so swatch scales smoothly without re-rendering
                            swatch_icon.setScaledContents(True)
                            swatch_icon.setSelectionHandler(self, "handle_item_click", material)
                            swatch_icon._actual_material_name = material
                            if position == 0:
                                layout.insertWidget(0, swatch_icon)
                            else:
                                layout.addWidget(swatch_icon)
                            
                            # OPTIMIZATION: Update the entry's swatch reference so it can be found later for updates
                            entry_idx = self._index_by_material.get(material)
                            if entry_idx is not None and entry_idx >= 0 and entry_idx < len(self._entry_list):
                                self._entry_list[entry_idx]["swatch"] = swatch_icon
                                # Debug: print(f"[QM][SWATCH] Updated entry swatch reference for {material}")
                            
                            # Load swatch asynchronously
                            QtCore.QTimer.singleShot(10, swatch_icon.load_swatch)
                            created += 1
                        except Exception as swatch_error:
                            print(f"[QuickMaterials] Failed to create swatch icon for {material}: {swatch_error}")
                            import traceback
                            traceback.print_exc()
                    else:
                        print(f"[QuickMaterials] MaterialSwatchIcon is None, cannot create swatch for {material}")
                
                elif icon_type == 'texture':
                    texture_icon = TextureIcon(material, icon_size=icon_size, parent=container)
                    texture_icon.setFixedSize(icon_size, icon_size)
                    texture_icon.setSelectionHandler(self, "handle_item_click", material)
                    texture_icon._actual_material_name = material
                    # Insert at specified position (after spacer, before material widget)
                    if position >= 0:
                        layout.insertWidget(position, texture_icon)
                    else:
                        layout.addWidget(texture_icon)
                    created += 1
                
                elif icon_type == 'procedural_texture':
                    proc_texture_icon = ProceduralTextureIcon(material, icon_size=icon_size, parent=container)
                    proc_texture_icon.setFixedSize(icon_size, icon_size)
                    proc_texture_icon.setSelectionHandler(self, "handle_item_click", material)
                    proc_texture_icon._actual_material_name = material
                    # Insert at specified position (after spacer, before material widget)
                    if position >= 0:
                        layout.insertWidget(position, proc_texture_icon)
                    else:
                        layout.addWidget(proc_texture_icon)
                    created += 1
                
                elif icon_type == 'utility':
                    node_type_name = icon_info.get('node_type_name')
                    util_icon = UtilityNodeIcon(material, node_type_name, icon_size=icon_size, parent=container)
                    util_icon.setSelectionHandler(self, "handle_item_click", material)
                    util_icon._actual_material_name = material
                    # Insert at specified position (after spacer, before material widget)
                    if position >= 0:
                        layout.insertWidget(position, util_icon)
                    else:
                        layout.addWidget(util_icon)
                    created += 1
                
                # Process in batches - schedule next batch if more remain
                if (i + 1) % batch_size == 0 and (i + 1) < icon_count:
                    # Remove processed icons before scheduling next batch
                    self._pending_icon_creations = self._pending_icon_creations[(i + 1):]
                    # Schedule next batch after a short delay
                    QtCore.QTimer.singleShot(1, self._batch_create_icons)
                    return
                    
            except Exception as e:
                import traceback
                print(f"[QuickMaterials] Failed to create {icon_info.get('type', 'unknown')} icon for {icon_info.get('material', 'unknown')}: {e}")
                print(f"[QuickMaterials] Traceback: {traceback.format_exc()}")
        
        # Remove processed icons from queue
        self._pending_icon_creations = self._pending_icon_creations[created:]
        
        batch_duration = (time.perf_counter() - batch_start) * 1000.0
        
        # If more icons remain, schedule next batch
        if self._pending_icon_creations:
            QtCore.QTimer.singleShot(1, lambda: self._batch_create_icons())
        else:
            # All icons created - check if we should report swatch timing summary
            # Delay a bit to allow swatches to start loading
            QtCore.QTimer.singleShot(100, self._check_swatch_timing_summary)
            QtCore.QTimer.singleShot(1000, self._check_swatch_timing_summary)  # Check again after 1 second
            QtCore.QTimer.singleShot(5000, self._check_swatch_timing_summary)  # Final check after 5 seconds

    def _check_swatch_timing_summary(self):
        """Check and report swatch timing summary if all swatches have loaded."""
        if not hasattr(self, '_swatch_timing_start'):
            return
        
        expected = getattr(self, '_swatch_timing_expected', 0)
        count = getattr(self, '_swatch_timing_count', 0)
        total = getattr(self, '_swatch_timing_total', 0.0)
        texture_count = getattr(self, '_swatch_timing_texture_count', 0)
        texture_total = getattr(self, '_swatch_timing_texture_total', 0.0)
        
        # If we've received timing for all expected swatches, or enough time has passed
        elapsed = (time.perf_counter() - self._swatch_timing_start) * 1000.0
        
        if count >= expected or elapsed > 5000:
            # Report summary (values are already in milliseconds, no need to multiply by 1000)
            avg_fast = (total / count) if count > 0 else 0.0
            avg_texture = (texture_total / texture_count) if texture_count > 0 else 0.0
            total_fast = total
            total_texture = texture_total
            
            
            # Clear tracking
            if hasattr(self, '_swatch_timing_start'):
                delattr(self, '_swatch_timing_start')
                delattr(self, '_swatch_timing_count')
                delattr(self, '_swatch_timing_total')
                delattr(self, '_swatch_timing_texture_total')
                delattr(self, '_swatch_timing_texture_count')
                delattr(self, '_swatch_timing_expected')

    # List buttons removed - no longer creating button rows
    def _batch_create_buttons(self):
        """
        List buttons have been removed. This function is kept for compatibility but does nothing.
        """
        # No-op: buttons are no longer created
        return

    def _batch_connect_signals(self):
        """
        PERFORMANCE OPTIMIZATION: Batch-connect all queued signals.
        This is called after UI is built to avoid blocking the main UI creation.
        """
        if not hasattr(self, "_pending_signal_connections") or not self._pending_signal_connections:
            return
        
        import time
        start_time = time.perf_counter()
        connection_count = len(self._pending_signal_connections)
        connected = 0
        
        for conn_info in self._pending_signal_connections:
            try:
                widget = conn_info['widget']
                signal_name = conn_info['signal']
                handler = conn_info['handler']
                
                # Get the signal from the widget
                signal = getattr(widget, signal_name, None)
                if signal:
                    signal.connect(handler)
                    connected += 1
            except Exception as e:
                print(f"[QuickMaterials] Failed to connect {conn_info.get('signal', 'unknown')} signal: {e}")
        
        # Clear the queue
        self._pending_signal_connections = []
        
        duration_ms = (time.perf_counter() - start_time) * 1000.0

    # Refresh list using current search/filter state (debounced).
    def refresh_materials_list(self):
        # PERFORMANCE OPTIMIZATION: Use debounced refresh to avoid excessive updates
        self._last_refresh_request_ts = time.perf_counter()
        if hasattr(self, '_refresh_timer'):
            self._refresh_timer.stop()  # Cancel any pending refresh
            self._refresh_timer.start(self._refresh_delay_ms)
        else:
            # Fallback to immediate refresh if timer not available
            self._perform_actual_refresh()

    def _perform_actual_refresh(self):
        """
        Actually perform the material list refresh (called by debounced timer).
        """
        # #region agent log
        import json
        import time
        try:
            log_path = r"d:\Maya Tools\QuickMaterials\.cursor\debug.log"
            with open(log_path, 'a') as f:
                f.write(json.dumps({
                    "sessionId": "debug-session",
                    "runId": "run1",
                    "hypothesisId": "B",
                    "location": "quick_materials.py:9996",
                    "message": "_perform_actual_refresh starting",
                    "data": {
                        "undo_state": cmds.undoInfo(query=True, state=True) if hasattr(cmds, 'undoInfo') else None,
                        "undo_queue_length": cmds.undoInfo(query=True, length=True) if hasattr(cmds, 'undoInfo') else None,
                    },
                    "timestamp": int(time.time() * 1000)
                }) + "\n")
        except:
            pass
        # #endregion
        start_ts = time.perf_counter()
        # Guard against late timer/scriptJob callbacks on a dead UI
        try:
            if getattr(self, "_suspend_refresh_count", 0) > 0:
                return
            if not self._is_ui_alive() or getattr(self, "_rebuilding_list", False):
                return
        except Exception:
            return

        # Get the current search text from the materialSearchLineEdit
        materialSearchLineEdit = self.ui_elements.get('materialSearchLineEdit')
        search_text = materialSearchLineEdit.text() if materialSearchLineEdit else ""

        # Live filters are read inside populate_materials_scroll_area
        # #region agent log
        try:
            log_path = r"d:\Maya Tools\QuickMaterials\.cursor\debug.log"
            with open(log_path, 'a') as f:
                f.write(json.dumps({
                    "sessionId": "debug-session",
                    "runId": "run1",
                    "hypothesisId": "B",
                    "location": "quick_materials.py:10015",
                    "message": "About to call populate_materials_scroll_area",
                    "data": {
                        "undo_state": cmds.undoInfo(query=True, state=True) if hasattr(cmds, 'undoInfo') else None,
                    },
                    "timestamp": int(time.time() * 1000)
                }) + "\n")
        except:
            pass
        # #endregion
        self.populate_materials_scroll_area(search_text=search_text)
        # #region agent log
        try:
            log_path = r"d:\Maya Tools\QuickMaterials\.cursor\debug.log"
            with open(log_path, 'a') as f:
                f.write(json.dumps({
                    "sessionId": "debug-session",
                    "runId": "run1",
                    "hypothesisId": "B",
                    "location": "quick_materials.py:10015",
                    "message": "Finished populate_materials_scroll_area",
                    "data": {
                        "undo_state": cmds.undoInfo(query=True, state=True) if hasattr(cmds, 'undoInfo') else None,
                        "undo_queue_length": cmds.undoInfo(query=True, length=True) if hasattr(cmds, 'undoInfo') else None,
                    },
                    "timestamp": int(time.time() * 1000)
                }) + "\n")
        except:
            pass
        # #endregion
        end_ts = time.perf_counter()
        build_ms = (end_ts - start_ts) * 1000.0
        request_ts = getattr(self, "_last_refresh_request_ts", 0.0)
        total_ms = (end_ts - request_ts) * 1000.0 if request_ts else build_ms
        self._last_refresh_request_ts = 0.0

    # Filter-as-you-type entrypoint; forwards to populate with search_text.
    def filter_materials(self, search_text):
        """
        PERFORMANCE OPTIMIZATION: Fast incremental search that only shows/hides widgets.
        Uses debouncing to avoid filtering on every keystroke.
        """
        # Store search text for incremental filtering
        self._current_search_text = search_text
        
        # Cancel any pending search debounce
        if hasattr(self, '_search_debounce_timer'):
            self._search_debounce_timer.stop()
        
        # Debounce search - wait 150ms after user stops typing
        self._search_debounce_timer = QtCore.QTimer()
        self._search_debounce_timer.setSingleShot(True)
        self._search_debounce_timer.timeout.connect(lambda: self._apply_search_filter(search_text))
        self._search_debounce_timer.start(150)  # 150ms debounce
    
    def _apply_search_filter(self, search_text):
        """
        Fast incremental search - only shows/hides existing widgets, no rebuild.
        Also handles selected filter for fast selection-based filtering.
        """
        if not hasattr(self, '_entry_list') or not self._entry_list:
            # No existing UI - need full rebuild
            self.refresh_materials_list()
            return
        
        # Get current filter flags (but don't rebuild for search-only changes)
        try:
            flags = self._collect_filter_flags()
            search_lower = search_text.lower() if search_text else ""
            
            # Fast check: if selected filter is active, get materials from current selection
            materials_from_selection = None
            if flags.get("selected", False) or flags.get("selectedOnly", False):
                import maya.cmds as cmds
                try:
                    # Get materials from current selection (fast)
                    current_sel_shapes = self._get_all_shapes_from_selection()
                    materials_from_selection = self._get_materials_from_selection()
                except Exception:
                    materials_from_selection = set()
            
            # Fast incremental search: just show/hide widgets
            import time
            start_time = time.perf_counter()
            visible_count = 0
            hidden_count = 0
            
            for entry in self._entry_list:
                material = entry.get('material')
                container = entry.get('container')
                
                if not material or not container:
                    continue
                
                # Fast text matching - check if material name matches search
                matches_search = True
                if search_lower:
                    material_lower = material.lower()
                    # Check if search text is in material name (case-insensitive)
                    matches_search = search_lower in material_lower
                
                # Check selected filter (fast - uses cached selection data if available)
                passes_selected_filter = True
                if flags.get("selected", False) or flags.get("selectedOnly", False):
                    if materials_from_selection is not None:
                        passes_selected_filter = material in materials_from_selection
                    else:
                        # Fallback: check cache or compute on the fly
                        cache = getattr(self, '_material_cache', {})
                        if material in cache and cache[material].get('affects_selection') is not None:
                            passes_selected_filter = cache[material]['affects_selection']
                        else:
                            # Quick check without full rebuild
                            try:
                                import maya.cmds as cmds
                                sel_mats = set(cmds.ls(sl=True, materials=True) or [])
                                passes_selected_filter = material in sel_mats
                            except Exception:
                                passes_selected_filter = True  # Show if check fails
                
                # Use cached node classification to avoid expensive API calls
                node_type_category = getattr(self, '_node_type_classification_cache', {}).get(material)
                if node_type_category is None:
                    # Fallback: quick heuristic check (materials don't end with "SG")
                    is_material = not material.endswith("SG")
                else:
                    is_material = (node_type_category == 'materials')
                
                # Check basic type filter
                passes_basic_filters = True
                if flags.get("showShadersOnly", False):
                    passes_basic_filters = is_material
                
                # Show/hide based on search match, selected filter, and basic filters
                should_show = matches_search and passes_selected_filter and passes_basic_filters
                
                if should_show:
                    if not container.isVisible():
                        container.setVisible(True)
                        visible_count += 1
                else:
                    if container.isVisible():
                        container.setVisible(False)
                        hidden_count += 1
            
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            filter_desc = []
            if search_text:
                filter_desc.append(f"search='{search_text}'")
            if flags.get("selected", False):
                filter_desc.append("selected")
            filter_str = ", ".join(filter_desc) if filter_desc else "all"
            
            # Show "no results" message if search returned no items
            if visible_count == 0 and search_lower:
                self._show_search_no_results_message()
            else:
                self._hide_search_no_results_message()
            
            # Also hide message if search is empty
            if not search_lower:
                self._hide_search_no_results_message()
        except Exception as e:
            # If incremental search fails, fall back to full rebuild
            pass
            self.refresh_materials_list()

    def _show_search_no_results_message(self):
        """Show a 'no results' message when search returns no items."""
        # Check if message already exists
        if hasattr(self, '_search_no_results_widget') and self._search_no_results_widget:
            if not self._search_no_results_widget.isVisible():
                self._search_no_results_widget.setVisible(True)
            return
        
        # Get current tab's scroll area
        current_tab = self._get_current_tab_type()
        scroll_area = self._get_tab_scroll_area(current_tab)
        if not scroll_area or not scroll_area.widget():
            return
        
        scroll_content = scroll_area.widget()
        scroll_layout = scroll_content.layout()
        if not scroll_layout:
            return
        
        # Create "no results" message widget
        no_results_widget = QtWidgets.QWidget()
        no_results_widget.setObjectName("qmSearchNoResults")
        no_results_widget.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        no_results_widget.setStyleSheet("""
            QWidget#qmSearchNoResults {
                background-color: transparent;
                border: none;
            }
            QWidget#qmSearchNoResults QLabel {
                color: #888888;
                font-style: italic;
                padding: 8px;
                font-size: 11px;
            }
        """)
        
        layout = QtWidgets.QHBoxLayout(no_results_widget)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)
        
        no_results_label = QtWidgets.QLabel("No items found")
        no_results_label.setTextInteractionFlags(QtCore.Qt.NoTextInteraction)
        layout.addWidget(no_results_label)
        layout.addStretch(1)
        
        # Add to layout at the end
        scroll_layout.addWidget(no_results_widget, scroll_layout.rowCount(), 0, 1, 4)
        self._search_no_results_widget = no_results_widget
    
    def _hide_search_no_results_message(self):
        """Hide the 'no results' message."""
        if hasattr(self, '_search_no_results_widget') and self._search_no_results_widget:
            if self._search_no_results_widget.isVisible():
                self._search_no_results_widget.setVisible(False)
    
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
        Tab-specific filters only show when on their designated tab.
        """
        active = []
        current_tab = self._get_current_tab_type() or self._current_active_tab or 'shaders'
        
        for f in self._filter_spec():
            if not f.get("chip", True):
                continue
            
            # Check if filter is tab-specific and we're on the right tab
            filter_tab = f.get("tab")
            if filter_tab and filter_tab != current_tab:
                continue
            
            # Support both buttons and checkboxes
            if "button" in f:
                widget = self._get_widget(f["button"], QtWidgets.QPushButton)
                if widget and widget.isChecked():
                    active.append((f["label"], f["id"]))
            elif "checkbox" in f:
                widget = self._get_widget(f["checkbox"], QtWidgets.QCheckBox)
                if widget and widget.isChecked():
                    active.append((f["label"], f["id"]))
        return active


    # Reads buttons/checkboxes safely and returns a dict of boolean flags.
    def _collect_filter_flags(self):
        """
        Reads filter buttons/checkboxes from MATERIAL_FILTERS; returns a dict keyed by filter id.
        Also reads tab button states for material list filtering.
        Also exposes legacy keys for compatibility (selectedOnly/nonSelectedOnly).
        """
        # Guard against deleted self
        try:
            if not self._is_ui_alive():
                return {}
        except (RuntimeError, AttributeError):
            return {}
        
        flags = {}
        for f in self._filter_spec():
            # Support both buttons and checkboxes (hideDefaults uses checkbox)
            if "button" in f:
                widget = self._get_widget(f["button"], QtWidgets.QPushButton)
                flags[f["id"]] = bool(widget and widget.isChecked())
            elif "checkbox" in f:
                widget = self._get_widget(f["checkbox"], QtWidgets.QCheckBox)
                flags[f["id"]] = bool(widget and widget.isChecked())

        # --- Tab button filters (replaces old checkbox filters) ---
        # Check which tab button is active to determine what to show
        shaders_btn = self._get_widget('materialListShadersButton', QtWidgets.QPushButton) 
        textures_btn = self._get_widget('materialListTexturesButton', QtWidgets.QPushButton)
        shading_groups_btn = self._get_widget('materialListShadingGroupButton', QtWidgets.QPushButton)
        utilities_btn = self._get_widget('materialListUtilitiesButton', QtWidgets.QPushButton)

        flags["utilitiesOnly"] = False
        
        # If shaders button is checked, show only materials (shaders)
        if shaders_btn and shaders_btn.isChecked():
            flags["fileTextures"] = False
            flags["proceduralTextures"] = False
            flags["shadingGroups"] = False
            flags["showShadersOnly"] = True
            flags["utilitiesOnly"] = False
        # If textures button is checked, show file and procedural textures
        elif textures_btn and textures_btn.isChecked():
            flags["fileTextures"] = True
            flags["proceduralTextures"] = True
            flags["shadingGroups"] = False
            flags["showShadersOnly"] = False
            flags["utilitiesOnly"] = False
        # If shading groups button is checked, show only shading groups
        elif shading_groups_btn and shading_groups_btn.isChecked():
            flags["fileTextures"] = False
            flags["proceduralTextures"] = False
            flags["shadingGroups"] = True
            flags["showShadersOnly"] = False
            flags["utilitiesOnly"] = False
        # Utilities tab: only show curated utility nodes
        elif utilities_btn and utilities_btn.isChecked():
            flags["fileTextures"] = False
            flags["proceduralTextures"] = False
            flags["shadingGroups"] = False
            flags["showShadersOnly"] = False
            flags["utilitiesOnly"] = True
        # If no button is checked, show everything (default behavior)
        else:
            flags["fileTextures"] = False
            flags["proceduralTextures"] = False
            flags["shadingGroups"] = False
            flags["showShadersOnly"] = False
            flags["utilitiesOnly"] = False

        # --- Back-compat keys (remove once all callsites use new ids) ---
        flags["selectedOnly"]    = flags.get("selected", False)       # legacy alias
        flags["nonSelectedOnly"] = flags.get("nonSelected", False)    # legacy alias (always False now, nonSelected removed)
        return flags

    def _is_selection_filter_active(self):
        """Return True if any selection-based filter (Selected / Non-Selected) is enabled."""
        flags = self._collect_filter_flags()
        return bool(
            flags.get("selected") or
            flags.get("selectedOnly") or
            flags.get("nonSelected") or
            flags.get("nonSelectedOnly")
        )


    # Applies filter flags + search to a single material name.
    def _batch_compute_material_properties(self, all_materials, current_sel_shapes):
        """
        PERFORMANCE OPTIMIZATION: Batch compute expensive material properties with caching.
        Returns a dict with material properties to avoid repeated Maya API calls.
        """
        import time
        
        batch_start = time.perf_counter()
        # Check if cache is still valid
        current_time = time.time()
        cache_check_start = time.perf_counter()
        cache_valid = (current_time - self._cache_timestamp) < self._cache_timeout and self._material_cache
        cache_check_duration = (time.perf_counter() - cache_check_start) * 1000.0
        
        if cache_valid:
            # Return cached results for materials that still exist
            cached_properties = {}
            needs_selection_update = []
            cache_hits = 0
            cache_misses = 0
            
            cache_lookup_start = time.perf_counter()
            for mat in all_materials:
                if mat in self._material_cache:
                    cache_hits += 1
                    props = self._material_cache[mat].copy()
                    # Check if affects_selection is stale (None means invalidated)
                    if props.get('affects_selection') is None:
                        needs_selection_update.append(mat)
                    cached_properties[mat] = props
                else:
                    cache_misses += 1
                    # New material not in cache - compute properties
                    cached_properties[mat] = self._compute_single_material_properties(mat, current_sel_shapes)
            cache_lookup_duration = (time.perf_counter() - cache_lookup_start) * 1000.0
            
            # If some materials have stale selection data, update just that
            if needs_selection_update:
                sel_update_start = time.perf_counter()
                materials_from_selection = self._get_materials_from_selection()
                for mat in needs_selection_update:
                    cached_properties[mat]['affects_selection'] = mat in materials_from_selection
                    # Update the main cache too
                    self._material_cache[mat]['affects_selection'] = mat in materials_from_selection
                sel_update_duration = (time.perf_counter() - sel_update_start) * 1000.0
            
            batch_duration = (time.perf_counter() - batch_start) * 1000.0
            return cached_properties
        
        # Cache expired or empty - recompute everything
        properties = {}
        
        # Separate materials, textures, shading groups, and utilities for different processing
        separate_start = time.perf_counter()
        materials_only = []
        textures_only = []
        shading_groups_only = []
        utilities_only = []
        for item in all_materials:
            try:
                if cmds.nodeType(item) == 'shadingEngine':
                    shading_groups_only.append(item)
                elif self._is_texture_node(item):
                    textures_only.append(item)
                elif self._is_utility_node(item):
                    utilities_only.append(item)
                else:
                    materials_only.append(item)
            except Exception:
                materials_only.append(item)
        separate_duration = (time.perf_counter() - separate_start) * 1000.0
        
        # Batch compute referenced status (materials only)
        ref_start = time.perf_counter()
        ref_duration = 0.0  # Initialize to ensure it's always defined
        try:
            if materials_only:
                ref_results = cmds.referenceQuery(materials_only, isNodeReferenced=True) or []
                if isinstance(ref_results, bool):
                    # Single result - apply to all materials
                    for mat in materials_only:
                        properties[mat] = {'referenced': ref_results, 'used': False, 'affects_selection': False}
                else:
                    # Multiple results
                    for i, mat in enumerate(materials_only):
                        is_ref = ref_results[i] if i < len(ref_results) else False
                        properties[mat] = {'referenced': is_ref, 'used': False, 'affects_selection': False}
                ref_duration = (time.perf_counter() - ref_start) * 1000.0
            else:
                # No materials to process, but still record the duration
                ref_duration = (time.perf_counter() - ref_start) * 1000.0
        except Exception as e:
            # Fallback to individual queries
            ref_duration = (time.perf_counter() - ref_start) * 1000.0
            for mat in materials_only:
                properties[mat] = {'referenced': self._is_referenced(mat), 'used': False, 'affects_selection': False}
        
        # Compute shading group properties
        for sg in shading_groups_only:
            is_ref = False
            is_used = False
            try:
                # Check if referenced
                is_ref = self._is_referenced(sg)
                # Check if used (has members)
                members = cmds.sets(sg, q=True) or []
                is_used = len(members) > 0
            except Exception:
                pass
            properties[sg] = {'referenced': is_ref, 'used': is_used, 'affects_selection': False}
        
        # Compute texture properties
        # Use Maya's built-in method to check if textures are used
        # This matches how Maya's HyperShade "Delete Unused Nodes" works
        unused_nodes = set()
        try:
            # Get list of unused nodes from Maya using hyperShade command
            unused_list = cmds.hyperShade(listUnusedNodes=True) or []
            unused_nodes = set(unused_list)
        except Exception:
            # Fallback: if hyperShade doesn't work, mark all as used
            pass
        
        # Build a map of shading groups to their usage status for quick lookup
        sg_usage_map = {}
        for sg in shading_groups_only:
            try:
                members = cmds.sets(sg, q=True) or []
                sg_usage_map[sg] = len(members) > 0
            except Exception:
                sg_usage_map[sg] = False
        
        for tex in textures_only:
            is_ref = False
            is_used = False
            try:
                # Check if referenced
                is_ref = self._is_referenced(tex)
                
                # Check if used - if NOT in unused_nodes, it's being used
                is_used = tex not in unused_nodes
                
                # Special handling for displacement shaders and similar utility shaders
                # If they're connected to a used shading group, mark them as used
                if not is_used:
                    node_type = cmds.nodeType(tex)
                    # Check if this is a displacement shader or similar utility shader
                    if node_type in ('displacementShader', 'volumeShader'):
                        # Check if connected to any shading group's displacementShader/volumeShader attribute
                        connected_sgs = cmds.listConnections(tex, type="shadingEngine", d=True, s=False) or []
                        for sg in connected_sgs:
                            # Check if this SG is connected via displacementShader or volumeShader
                            try:
                                if node_type == 'displacementShader':
                                    conns = cmds.listConnections(f"{sg}.displacementShader", s=True, d=False) or []
                                elif node_type == 'volumeShader':
                                    conns = cmds.listConnections(f"{sg}.volumeShader", s=True, d=False) or []
                                else:
                                    conns = []
                                
                                if tex in conns and sg in sg_usage_map and sg_usage_map[sg]:
                                    # This shader is connected to a used shading group
                                    is_used = True
                                    break
                            except Exception:
                                pass
            except Exception:
                pass
            properties[tex] = {'referenced': is_ref, 'used': is_used, 'affects_selection': False}
        
        # Batch compute material usage (OPTIMIZED VERSION) - materials only
        usage_start = time.perf_counter()
        # Get all shading engines for all materials at once
        sg_collect_start = time.perf_counter()
        all_sgs = set()
        material_to_sgs = {}
        for mat in materials_only:
            sgs = self._connected_shading_engines(mat)
            material_to_sgs[mat] = sgs
            all_sgs.update(sgs)
        sg_collect_duration = (time.perf_counter() - sg_collect_start) * 1000.0
        
        # Batch query which SGs have members
        sg_query_start = time.perf_counter()
        sgs_with_members = set()
        if all_sgs:
            try:
                # Batch query for all SGs
                for sg in all_sgs:
                    try:
                        if cmds.sets(sg, q=True):
                            sgs_with_members.add(sg)
                    except Exception:
                        pass
            except Exception:
                pass
        sg_query_duration = (time.perf_counter() - sg_query_start) * 1000.0
        
        # Now assign usage based on batch results (materials only)
        usage_assign_start = time.perf_counter()
        for mat in materials_only:
            sgs = material_to_sgs[mat]
            is_used = any(sg in sgs_with_members for sg in sgs)
            
            # Special handling for displacement shaders and similar utility shaders
            # If they're connected to a used shading group, mark them as used
            if not is_used:
                try:
                    node_type = cmds.nodeType(mat)
                    # Check if this is a displacement shader or similar utility shader
                    if node_type in ('displacementShader', 'volumeShader'):
                        # Check if connected to any shading group's displacementShader/volumeShader attribute
                        connected_sgs = cmds.listConnections(mat, type="shadingEngine", d=True, s=False) or []
                        for sg in connected_sgs:
                            # Check if this SG is connected via displacementShader or volumeShader
                            try:
                                if node_type == 'displacementShader':
                                    conns = cmds.listConnections(f"{sg}.displacementShader", s=True, d=False) or []
                                elif node_type == 'volumeShader':
                                    conns = cmds.listConnections(f"{sg}.volumeShader", s=True, d=False) or []
                                else:
                                    conns = []
                                
                                if mat in conns and sg in sgs_with_members:
                                    # This shader is connected to a used shading group
                                    is_used = True
                                    break
                            except Exception:
                                pass
                except Exception:
                    pass
            
            properties[mat]['used'] = is_used
        usage_assign_duration = (time.perf_counter() - usage_assign_start) * 1000.0
        usage_duration = (time.perf_counter() - usage_start) * 1000.0
        
        # Initialize utility properties BEFORE computing selection relationship
        # (needed because selection computation accesses properties[item] for utilities)
        for util in utilities_only:
            properties[util] = {'referenced': False, 'used': False, 'affects_selection': False}
        
        # Batch compute selection relationship (materials, textures, and utilities)
        sel_comp_start = time.perf_counter()
        materials_from_selection = self._get_materials_from_selection()
        for item in all_materials:
            if item in utilities_only:
                # For utilities: check if any connected shader affects selection
                affects_sel = False
                try:
                    # Find shaders connected to this utility (traverse network)
                    connected_shaders = self._get_shaders_connected_to_utility(item)
                    for shader in connected_shaders:
                        if shader in materials_from_selection:
                            affects_sel = True
                            break
                except Exception:
                    pass
                properties[item]['affects_selection'] = affects_sel
            else:
                properties[item]['affects_selection'] = item in materials_from_selection
        sel_comp_duration = (time.perf_counter() - sel_comp_start) * 1000.0
        
        # Compute utility properties based on connected shaders
        util_props_start = time.perf_counter()
        # Build a map of shader to usage status for quick lookup
        shader_usage_map = {}
        for mat in materials_only:
            sgs = material_to_sgs.get(mat, set())
            is_used = any(sg in sgs_with_members for sg in sgs)
            shader_usage_map[mat] = is_used
        
        for util in utilities_only:
            is_ref = False
            is_used = False
            try:
                # Check if referenced
                is_ref = self._is_referenced(util)
                
                # Check if used - find shaders connected to this utility and check if any are used
                connected_shaders = self._get_shaders_connected_to_utility(util)
                for shader in connected_shaders:
                    if shader in shader_usage_map and shader_usage_map[shader]:
                        is_used = True
                        break
            except Exception:
                pass
            properties[util].update({'referenced': is_ref, 'used': is_used})
        util_props_duration = (time.perf_counter() - util_props_start) * 1000.0
        
        # Update cache
        cache_update_start = time.perf_counter()
        self._material_cache = properties.copy()
        self._cache_timestamp = current_time
        cache_update_duration = (time.perf_counter() - cache_update_start) * 1000.0
        
        batch_duration = (time.perf_counter() - batch_start) * 1000.0
        
        # Print detailed breakdown
        
        return properties

    def _compute_single_material_properties(self, material, current_sel_shapes):
        """
        Compute properties for a single material/utility (used for cache misses).
        For utilities, properties are based on connected shaders.
        """
        materials_from_selection = self._get_materials_from_selection()
        
        # Check if this is a utility node
        if self._is_utility_node(material):
            # For utilities: check connected shaders
            is_used = False
            affects_sel = False
            try:
                connected_shaders = self._get_shaders_connected_to_utility(material)
                for shader in connected_shaders:
                    # Check if shader is used
                    if self._is_material_used(shader):
                        is_used = True
                    # Check if shader affects selection
                    if shader in materials_from_selection:
                        affects_sel = True
                    if is_used and affects_sel:
                        break
            except Exception:
                pass
            
            return {
                'referenced': self._is_referenced(material),
                'used': is_used,
                'affects_selection': affects_sel
            }
        else:
            # Regular material/texture/shading group
            return {
                'referenced': self._is_referenced(material),
                'used': self._is_material_used(material),
                'affects_selection': material in materials_from_selection
            }

    def _update_material_unused_status(self, material, is_used):
        """
        Update the unused highlighting for a material widget in real-time.
        Called after assigning a material to update its visual state without full refresh.
        """
        try:
            # Find the material widget in the entry list
            if not hasattr(self, '_entry_list') or not self._entry_list:
                return
            
            # Find the entry for this material
            material_widget = None
            entry = None
            for e in self._entry_list:
                if e.get("material") == material:
                    material_widget = e.get("line_edit")
                    entry = e
                    break
            
            if not material_widget or not entry:
                return
            
            # Check if widget is still valid
            try:
                from shiboken6 import isValid as _is_valid
            except Exception:
                try:
                    from shiboken2 import isValid as _is_valid
                except Exception:
                    _is_valid = lambda obj: bool(obj)
            
            if not _is_valid(material_widget):
                return
            
            # Check if highlight unused is enabled
            should_highlight_unused = False
            if self._cached_highlight_unused_cb and _is_valid(self._cached_highlight_unused_cb):
                try:
                    should_highlight_unused = self._cached_highlight_unused_cb.isChecked()
                except RuntimeError:
                    # Widget was deleted, refresh cache
                    self._cached_highlight_unused_cb = self._get_widget('highlightUnusedCheckbox', QtWidgets.QCheckBox)
                    if self._cached_highlight_unused_cb and _is_valid(self._cached_highlight_unused_cb):
                        try:
                            should_highlight_unused = self._cached_highlight_unused_cb.isChecked()
                        except RuntimeError:
                            should_highlight_unused = False
            
            # Only update if highlight unused is enabled
            if not should_highlight_unused:
                return
            
            # Check if it's a utility (utilities shouldn't be highlighted as unused)
            is_utility = material_widget.property("nodeType") == "utility"
            if is_utility:
                return
            
            # Check if it's a default material (default materials shouldn't be highlighted as unused)
            is_default = material_widget.property("materialType") == "default"
            if is_default:
                return
            
            # Update the unused property
            highlight_unused_applicable = not is_used
            new_value = "true" if highlight_unused_applicable else "false"
            material_widget.setProperty("qmUnused", new_value)
            
            # Find the scroll content widget that has the stylesheet applied
            # This is the parent widget that contains all material entries
            scroll_content = None
            try:
                parent = material_widget.parent()
                while parent:
                    # Check if it's MaterialListScrollContent (the widget with the stylesheet)
                    if isinstance(parent, MaterialListScrollContent):
                        scroll_content = parent
                        break
                    # Also check by stylesheet content as fallback
                    if hasattr(parent, 'styleSheet'):
                        ss = parent.styleSheet()
                        if ss and 'QLineEdit[qmUnused="true"]' in ss:
                            scroll_content = parent
                            break
                    parent = parent.parent() if hasattr(parent, 'parent') else None
            except Exception:
                pass
            
            # Force style update by unpolishing and repolishing the widget
            try:
                material_widget.style().unpolish(material_widget)
                material_widget.style().polish(material_widget)
            except Exception:
                pass
            
            # If we found the scroll content, force a style refresh on it
            # This ensures the stylesheet is reapplied with the new property values
            if scroll_content:
                try:
                    # Temporarily modify the stylesheet to force recalculation
                    current_ss = scroll_content.styleSheet()
                    if current_ss:
                        # Add a tiny whitespace change to force Qt to recalculate
                        scroll_content.setStyleSheet(current_ss + " ")
                        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.ExcludeUserInputEvents)
                        scroll_content.setStyleSheet(current_ss)
                except Exception:
                    pass
            
            # Update parent container if it exists
            container = entry.get("container")
            if container:
                try:
                    container.style().unpolish(container)
                    container.style().polish(container)
                    container.update()
                    container.repaint()
                except Exception:
                    pass
            
            # Force immediate repaint with multiple methods
            material_widget.update()
            material_widget.repaint()
            
            # Also update the widget's parent to force a refresh
            try:
                parent = material_widget.parent()
                if parent:
                    parent.update()
                    parent.repaint()
            except Exception:
                pass
            
            # Also try updating the scroll area to force a refresh
            try:
                scroll_area = self.ui_elements.get('materialsListScrollArea')
                if scroll_area:
                    scroll_area.viewport().update()
                    scroll_area.viewport().repaint()
            except Exception:
                pass
            
            # Process events to ensure all updates are applied
            QtWidgets.QApplication.processEvents(QtCore.QEventLoop.ExcludeUserInputEvents)
            
            # Update the cache entry for this material
            if hasattr(self, '_material_cache') and material in self._material_cache:
                self._material_cache[material]['used'] = is_used
            
        except Exception as e:
            # Silently fail - this is a UI update, shouldn't break material assignment
            print(f"[QM] Failed to update unused status for {material}: {e}")
    
    def _invalidate_material_cache(self, selection_only=False):
        """
        Invalidate the material cache - call this when materials are added/removed/modified.
        
        Args:
            selection_only: If True, only invalidate selection-related properties (affects_selection),
                          keeping referenced/used properties cached for better performance.
        """
        if selection_only:
            # Only invalidate the affects_selection property for each material
            # This is much faster than recomputing referenced/used status
            for mat in self._material_cache:
                if 'affects_selection' in self._material_cache[mat]:
                    self._material_cache[mat]['affects_selection'] = None  # Mark as stale
            # Don't clear timestamp - cache is still partially valid
        else:
            # Full invalidation when materials added/removed or other major changes
            self._material_cache.clear()
            self._cache_timestamp = 0
            # Also invalidate material list hash to force rebuild
            self._last_material_list_hash = None
            # Clear node type classification cache when materials change
            self._node_type_classification_cache.clear()
            # Clear file texture info cache when materials change
            self._file_texture_info_cache.clear()
            # PERFORMANCE OPTIMIZATION: Invalidate tab UI cache when materials change
            if hasattr(self, '_tab_ui_cache'):
                self._tab_ui_cache.clear()

    def _can_optimize_ui_refresh(self, materials_to_display, search_text, flags):
        """
        Check if we can optimize the UI refresh instead of doing a full rebuild.
        Returns True if optimization is possible.
        """
        # Only optimize for simple search text changes, not filter changes
        if any(flags.values()):
            return False  # Filters active - need full rebuild
        
        # Check if we have existing UI and only search text changed
        if not hasattr(self, '_entry_list') or not self._entry_list:
            return False  # No existing UI to optimize
        
        # For now, only optimize pure search text changes (no filters)
        return True

    def _update_existing_ui(self, materials_to_display, search_text, flags):
        """
        Try to update existing UI instead of full rebuild.
        Returns True if successful, False if full rebuild needed.
        """
        try:
            # Simple optimization: just show/hide existing entries based on search
            if not search_text:
                # No search - show all existing entries
                for entry in self._entry_list:
                    material = entry.get('material')
                    if material and material in materials_to_display:
                        # Show the entry
                        if hasattr(entry, 'widget') and entry.widget:
                            entry.widget.setVisible(True)
                    else:
                        # Hide the entry
                        if hasattr(entry, 'widget') and entry.widget:
                            entry.widget.setVisible(False)
            else:
                # Search active - show only matching entries
                search_lower = search_text.lower()
                for entry in self._entry_list:
                    material = entry.get('material')
                    if material:
                        matches_search = search_lower in material.lower()
                        should_show = matches_search and material in materials_to_display
                        if hasattr(entry, 'widget') and entry.widget:
                            entry.widget.setVisible(should_show)
            
            return True  # Successfully updated existing UI
        except Exception:
            return False  # Failed - need full rebuild

    def _passes_filters_optimized(self, mat, flags, search_text, default_materials, material_properties):
        """
        OPTIMIZED VERSION: Uses pre-computed material properties to avoid expensive API calls.
        Handles filtering for materials, file textures, procedural textures, and shading groups.
        """
        # Default shading groups that should be hidden when hideDefaults is checked
        DEFAULT_SHADING_GROUPS = {'initialShadingGroup', 'initialParticleSE'}
        
        # Classify the node type
        node_type_category = self._classify_node_type(mat)
        is_file_texture = (node_type_category == 'file_textures')
        is_procedural_texture = (node_type_category == 'procedural_textures')
        is_shading_group = (node_type_category == 'shading_groups')
        is_material = (node_type_category == 'materials')
        is_utility = (node_type_category == 'utilities')
        
        # Node type filters - if enabled, ONLY show that type
        # Tab button filtering: showShadersOnly means show only materials (shaders), not textures or shading groups
        if flags.get("showShadersOnly", False):
            if not is_material:
                return False
        
        # Textures filter shows BOTH file and procedural textures (when textures tab is active)
        if flags.get("fileTextures", False) and flags.get("proceduralTextures", False):
            # Both are True when textures tab is active - show both file and procedural textures
            if not (is_file_texture or is_procedural_texture):
                return False
        elif flags.get("fileTextures", False):
            # Legacy: if only fileTextures is set, show only file textures
            if not is_file_texture:
                return False
        elif flags.get("proceduralTextures", False):
            # Legacy: if only proceduralTextures is set, show only procedural textures
            if not is_procedural_texture:
                return False
        
        if flags.get("shadingGroups", False):
            if not is_shading_group:
                return False

        if flags.get("utilitiesOnly", False):
            if not is_utility:
                return False
        
        # Hide defaults (optionally) - applies to materials and shading groups
        if flags.get("hideDefaults", False):
            # Check if it's a default material
            if is_material and mat in default_materials:
                return False
            # Check if it's a default shading group
            if is_shading_group and mat in DEFAULT_SHADING_GROUPS:
                return False
        
        # Search text — match against user-visible names per node type
        if search_text:
            query = search_text.lower()
            # Materials: use display name (respect hideNamespaces option)
            if is_material:
                display_name = self._strip_namespace(mat)
                if query not in (display_name or "").lower():
                    return False
            # File textures: match only on filename shown in the UI
            elif is_file_texture:
                info = None
                try:
                    info = self._get_file_texture_display_info(mat)
                except Exception:
                    info = None
                filename_lc = (info.get("filename", "") if info else "").lower()
                if query not in filename_lc:
                    return False
            # Procedural textures and shading groups: match on node name
            else:
                if query not in mat.lower():
                    return False

        # Get pre-computed properties
        props = material_properties.get(mat, {'referenced': False, 'used': False, 'affects_selection': False})

        # Referenced / Non-Referenced (applies to materials, textures, and shading groups)
        is_ref = props.get('referenced', False)
        if flags.get("referenced", False) and not is_ref:
            return False
        if flags.get("nonReferenced", False) and is_ref:
            return False

        # Used / Unused (applies to materials, textures, and shading groups)
        is_used = props.get('used', False)
        if flags.get("used", False) and not is_used:
            return False
        if flags.get("unUsed", False) and is_used:
            return False

        # Selected / Non-Selected (applies to all node types)
        affects_sel = props['affects_selection']
        if flags.get("selectedOnly", False) and not affects_sel:
            return False
        if flags.get("nonSelectedOnly", False) and affects_sel:
            return False

        return True

    def _passes_filters(self, mat, flags, search_text, default_materials, current_sel_shapes):
        """
        LEGACY VERSION: Kept for compatibility, but should use _passes_filters_optimized instead.
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

    # Click on a chip → uncheck its corresponding button/checkbox and refresh (deferred).
    def _on_filter_chip_clicked(self, filter_id):
        """Chip click → clear the corresponding button/checkbox (deferred)."""
        QtCore.QTimer.singleShot(0, lambda fid=filter_id: self._clear_filter(fid))

    # Programmatically uncheck a specific filter button/checkbox by filter_id.
    def _clear_filter(self, filter_id):
        """Uncheck the button/checkbox for filter_id (from MATERIAL_FILTERS) and refresh (deferred)."""
        spec = self._find_filter(filter_id)
        if not spec:
            return
        # Support both buttons and checkboxes
        widget = None
        if "button" in spec:
            widget = self._get_widget(spec["button"], QtWidgets.QPushButton)
            if not widget:
                return
        elif "checkbox" in spec:
            widget = self._get_widget(spec["checkbox"], QtWidgets.QCheckBox)
            if not widget:
                return
        if widget and widget.isChecked():
            widget.setChecked(False)  # fire signals
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
                blocker = QtCore.QSignalBlocker(b_cb)
                try:
                    b_cb.setChecked(False)
                finally:
                    del blocker
            # Debounced refresh instead of immediate
            self._queue_material_refresh()

        def on_b(state):
            a_cb = self._get_widget(a_name, QtWidgets.QCheckBox)
            b_cb = self._get_widget(b_name, QtWidgets.QCheckBox)
            if not (a_cb and b_cb and _is_valid(a_cb) and _is_valid(b_cb)):
                return
            if state == QtCore.Qt.Checked and a_cb.isChecked():
                blocker = QtCore.QSignalBlocker(a_cb)
                try:
                    a_cb.setChecked(False)
                finally:
                    del blocker
            # Debounced refresh instead of immediate
            self._queue_material_refresh()

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
            self._queue_material_refresh()
            return

        names = list(self._exclusive_groups.get(group_name, []))
        for peer_name in names:
            if peer_name == changed_name:
                continue
            peer = self._get_widget(peer_name, QtWidgets.QCheckBox)
            if peer and peer.isChecked():
                blocker = QtCore.QSignalBlocker(peer)
                try:
                    peer.setChecked(False)
                finally:
                    del blocker

        self._queue_material_refresh()

    def _wire_at_most_one_group_buttons(self, button_names, group_name):
        """
        Make an arbitrary set of checkable QPushButtons mutually exclusive (at most one),
        while still allowing all to be unchecked (no forced selection).
        Name-based wiring: resolves widgets fresh each time; no stale pointers.
        Similar to _wire_at_most_one_group but for QPushButton instead of QCheckBox.
        """
        # Record mapping so the handler can find peers later
        if not hasattr(self, '_exclusive_button_groups'):
            self._exclusive_button_groups = {}
        self._exclusive_button_groups[group_name] = list(button_names)

        # Connect each button to a stable, named slot that uses objectNames
        connected_count = 0
        for name in button_names:
            btn = self._get_widget(name, QtWidgets.QPushButton)
            if not btn:
                continue
            # Ensure button is checkable
            btn.setCheckable(True)
            # Connect toggled signal (QPushButton uses toggled, not stateChanged)
            # Check if already connected to avoid duplicate connections
            # We'll create a unique slot identifier to check for existing connections
            try:
                # Try to connect - Qt will handle duplicate connections gracefully in most cases
                # but we'll catch any exceptions
                btn.toggled.connect(
                    lambda checked, _name=name, _grp=group_name: self._on_exclusive_button_group_changed(_name, _grp, checked)
                )
                connected_count += 1
            except RuntimeError as e:
                # If connection fails due to duplicate, try disconnecting first
                if "already connected" in str(e).lower() or "duplicate" in str(e).lower():
                    try:
                        # Disconnect all toggled signals and reconnect
                        btn.toggled.disconnect()
                        btn.toggled.connect(
                            lambda checked, _name=name, _grp=group_name: self._on_exclusive_button_group_changed(_name, _grp, checked)
                        )
                        connected_count += 1
                    except Exception as e2:
                        pass
                else:
                    pass
            except Exception as e:
                pass

    def _on_exclusive_button_group_changed(self, changed_name, group_name, checked):
        """
        Slot for button group exclusivity. When one is checked, uncheck all the others in its group.
        Prevents unchecking the currently checked button - at least one must always be checked.
        Uses live lookups via objectName to avoid stale Qt pointers.
        """
        # If trying to uncheck, prevent it - at least one button must always be checked
        if not checked:
            # Re-check the button that was just unchecked
            btn = self._get_widget(changed_name, QtWidgets.QPushButton)
            if btn:
                blocker = QtCore.QSignalBlocker(btn)
                try:
                    btn.setChecked(True)
                finally:
                    del blocker
            return  # Don't refresh, nothing changed

        # When a button is checked, uncheck all others in the group
        names = list(self._exclusive_button_groups.get(group_name, []))
        for peer_name in names:
            if peer_name == changed_name:
                continue
            peer = self._get_widget(peer_name, QtWidgets.QPushButton)
            if peer and peer.isChecked():
                blocker = QtCore.QSignalBlocker(peer)
                try:
                    peer.setChecked(False)
                finally:
                    del blocker

        # PERFORMANCE OPTIMIZATION: Handle material list tab switches
        if group_name == 'material_list_tabs':
            tab_type = self._tab_button_to_type.get(changed_name)
            if tab_type:
                self._current_active_tab = tab_type
                self._update_tab_frames_visibility(tab_type)
                self._sync_sort_state_from_tab(tab_type, update_buttons=False)
                
                # Update header label frames visibility based on active tab
                show_shaders = (tab_type == 'shaders')
                show_textures = (tab_type == 'textures')
                show_shading_groups = (tab_type == 'shading_groups')
                show_utilities = (tab_type == 'utilities')
                self._update_header_frames_visibility(show_shaders, show_textures, show_shading_groups, show_utilities)
                
                # Lazy loading: Populate utilities tab on first access
                if tab_type == 'utilities' and not self._utilities_tab_populated:
                    self._populate_utilities_tab()
                
                # Show/hide utility filter frame based on active tab
                utility_filter_frame = self._get_widget('shaderUtilitiesFilterFrame', QtWidgets.QFrame)
                if utility_filter_frame:
                    utility_filter_frame.setVisible(tab_type == 'utilities')
            return
        
        self._queue_material_refresh()
    
    # Utility filter removed - utilities now always show only those connected to shaders
    
    def _wire_at_most_one_group_filter_buttons(self, button_names, group_name):
        """
        Make an arbitrary set of checkable QPushButtons mutually exclusive (at most one),
        while still allowing all to be unchecked (no forced selection).
        This is for filter buttons (unlike tab buttons which require one to be checked).
        Name-based wiring: resolves widgets fresh each time; no stale pointers.
        """
        # Record mapping so the handler can find peers later
        if not hasattr(self, '_exclusive_filter_button_groups'):
            self._exclusive_filter_button_groups = {}
        self._exclusive_filter_button_groups[group_name] = list(button_names)

        # Connect each button to a stable, named slot that uses objectNames
        for name in button_names:
            btn = self._get_widget(name, QtWidgets.QPushButton)
            if not btn:
                continue
            # Ensure button is checkable
            btn.setCheckable(True)
            # Connect toggled signal (QPushButton uses toggled, not stateChanged)
            try:
                btn.toggled.connect(
                    lambda checked, _name=name, _grp=group_name: self._on_exclusive_filter_button_group_changed(_name, _grp, checked)
                )
            except Exception:
                # Some hosts error if double-connecting identical lambdas; safe to ignore
                pass

    def _on_exclusive_filter_button_group_changed(self, changed_name, group_name, checked):
        """
        Slot for filter button group exclusivity. When one is checked, uncheck all the others in its group.
        Allows unchecking - all buttons can be unchecked (unlike tab buttons).
        Uses live lookups via objectName to avoid stale Qt pointers.
        """
        # If unchecking, just refresh - no need to prevent it
        if not checked:
            self._queue_material_refresh()
            return

        # When a button is checked, uncheck all others in the group
        names = list(self._exclusive_filter_button_groups.get(group_name, []))
        for peer_name in names:
            if peer_name == changed_name:
                continue
            peer = self._get_widget(peer_name, QtWidgets.QPushButton)
            if peer and peer.isChecked():
                blocker = QtCore.QSignalBlocker(peer)
                try:
                    peer.setChecked(False)
                finally:
                    del blocker

        # If this is a tab button change, update current tab and save state
        if group_name == 'material_list_tabs' and checked:
            current_tab = self._get_current_tab_type()
            if current_tab:
                self._current_active_tab = current_tab
                self._save_ui_state()

        self._queue_material_refresh()
    
    def _get_current_tab_type(self):
        """
        Get the current active tab type based on which button is checked.
        Returns: 'shaders', 'textures', 'shading_groups', 'utilities', or None
        """
        shaders_btn = self._get_widget('materialListShadersButton', QtWidgets.QPushButton)
        textures_btn = self._get_widget('materialListTexturesButton', QtWidgets.QPushButton)
        shading_groups_btn = self._get_widget('materialListShadingGroupButton', QtWidgets.QPushButton)
        utilities_btn = self._get_widget('materialListUtilitiesButton', QtWidgets.QPushButton)
        
        if shaders_btn and shaders_btn.isChecked():
            return 'shaders'
        elif textures_btn and textures_btn.isChecked():
            return 'textures'
        elif shading_groups_btn and shading_groups_btn.isChecked():
            return 'shading_groups'
        elif utilities_btn and utilities_btn.isChecked():
            return 'utilities'
        return None
    
    def _try_instant_tab_switch(self):
        """
        Placeholder hook. With dedicated scroll areas per tab we always rebuild the active tab.
        """
        return False

    # --- Sorting UI & logic ---------------------------------------------------


    def _sort_materials(self, materials, all_materials=None):
        """
        Return a new list sorted per current state:
          - 'name' : alphabetical by name (A–Z) or reversed (Z–A)
            For file textures, sorts by filename instead of node name
          - 'type' : group by nodeType (alphabetical), then by name; reverse flips whole list
          - 'time' : creation order via MItDependencyNodes iteration index; reverse flips order
        """
        mats = list(materials or [])
        if not mats:
            return mats

        mode = getattr(self, "_sort_mode", "name")
        desc = bool(getattr(self, "_sort_desc", False))
        start_ts = time.perf_counter()

        try:
            if mode == 'name':
                # For file textures, sort by filename instead of node name
                def _get_sort_key(m):
                    try:
                        # Check if it's a file texture
                        if cmds.objExists(m) and cmds.nodeType(m) == 'file':
                            # Get filename for sorting
                            try:
                                info = self._get_file_texture_display_info(m)
                                if info and info['filename']:
                                    return info['filename'].lower()
                            except Exception:
                                pass
                        # For non-file textures or if filename unavailable, use node name
                        return m.lower()
                    except Exception:
                        return m.lower()
                
                mats.sort(key=_get_sort_key)
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
        finally:
            elapsed_ms = (time.perf_counter() - start_ts) * 1000.0
            # Debug: print(f"[QM][Sort] mode={mode} desc={int(desc)} count={len(mats)} duration={elapsed_ms:.3f} ms")


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
        Install sort bar as a sticky header above the scroll area.
        The bar stays visible when scrolling the material list.
        """
        # Connect buttons from Qt Designer (they stay in their original location)
        self._connect_sort_bar_buttons()
        
        # Check if UI-defined sorting buttons exist - if so, we don't need to create a sort bar widget
        name_btn = self._get_widget('nameSortingButton', QtWidgets.QPushButton)
        type_btn = self._get_widget('typeSortingButton', QtWidgets.QPushButton)
        time_btn = self._get_widget('timeSortingButton', QtWidgets.QPushButton)
        
        # If UI buttons exist, connect them and remove any old dynamically created sort bar widget
        if name_btn and type_btn and time_btn:
            # Remove any existing dynamically created sort bar widget (from old code)
            sort_bar = self.ui_elements.get('materialListSortBar')
            if sort_bar:
                # Remove from layout if it exists
                parent = sort_bar.parent()
                if parent:
                    parent_layout = parent.layout()
                    if parent_layout:
                        parent_layout.removeWidget(sort_bar)
                # Hide and delete
                sort_bar.setVisible(False)
                sort_bar.setParent(None)
                sort_bar.deleteLater()
                del self.ui_elements['materialListSortBar']
            return
        
        # No UI buttons found - do not create them dynamically
        # Sorting buttons must be created in Qt Designer UI file
    
    def _connect_sort_bar_buttons(self):
        """Connect filters and refresh buttons from Qt Designer without moving them."""
        # Connect refresh button from Qt Designer if it exists (stays in original location)
        refresh_btn = self._get_widget('materialListRefreshButton', QtWidgets.QPushButton)
        if refresh_btn:
            # Disconnect any existing connections to avoid duplicates
            try:
                if refresh_btn.receivers(refresh_btn.clicked) > 0:
                    refresh_btn.clicked.disconnect()
            except (TypeError, RuntimeError, AttributeError):
                pass
            refresh_btn.clicked.connect(self.refresh_materials_list)
            # Store in ui_elements
            self.ui_elements['materialListRefreshButton'] = refresh_btn
        
        # Connect filters button from Qt Designer if it exists (stays in original location)
        filters_btn = self._get_widget('materialListFiltersButton', QtWidgets.QPushButton)
        if not filters_btn:
            filters_btn = self._get_widget('materialFiltersButton', QtWidgets.QPushButton)
        if filters_btn:
            # Ensure it's checkable
            if not filters_btn.isCheckable():
                filters_btn.setCheckable(True)
            # Disconnect any existing connections
            try:
                if filters_btn.receivers(filters_btn.toggled) > 0:
                    filters_btn.toggled.disconnect()
            except (TypeError, RuntimeError, AttributeError):
                pass
            filters_btn.toggled.connect(self.toggle_material_filters)
            filters_btn.toggled.connect(self._save_ui_state)
            # Store in ui_elements for backward compatibility
            self.ui_elements['materialFiltersButton'] = filters_btn
            self.ui_elements['materialListFiltersButton'] = filters_btn
        
        # Connect sorting buttons from Qt Designer (same pattern as refresh/filters buttons)
        name_btn = self._get_widget('nameSortingButton', QtWidgets.QPushButton)
        if not name_btn:
            name_btn = self.ui_elements.get('nameSortingButton')
        type_btn = self._get_widget('typeSortingButton', QtWidgets.QPushButton)
        if not type_btn:
            type_btn = self.ui_elements.get('typeSortingButton')
        time_btn = self._get_widget('timeSortingButton', QtWidgets.QPushButton)
        if not time_btn:
            time_btn = self.ui_elements.get('timeSortingButton')
        
        if name_btn and type_btn and time_btn:
            # Disconnect any existing connections to avoid duplicates
            for btn in (name_btn, type_btn, time_btn):
                try:
                    if btn.receivers(btn.clicked) > 0:
                        btn.clicked.disconnect()
                except (TypeError, RuntimeError, AttributeError):
                    pass
            
            # Apply initial styles
            self._apply_sort_button_styles(name_btn, type_btn, time_btn)
            
            # Click behavior: toggle direction if same mode; otherwise select mode and reset to descending=False
            # Get fresh button references each time to avoid stale widget references
            def on_click(mode):
                # Get fresh button references (avoid closure capturing stale references)
                name_btn_fresh = self._get_widget('nameSortingButton', QtWidgets.QPushButton)
                if not name_btn_fresh:
                    name_btn_fresh = self.ui_elements.get('nameSortingButton')
                type_btn_fresh = self._get_widget('typeSortingButton', QtWidgets.QPushButton)
                if not type_btn_fresh:
                    type_btn_fresh = self.ui_elements.get('typeSortingButton')
                time_btn_fresh = self._get_widget('timeSortingButton', QtWidgets.QPushButton)
                if not time_btn_fresh:
                    time_btn_fresh = self.ui_elements.get('timeSortingButton')
                
                if not (name_btn_fresh and type_btn_fresh and time_btn_fresh):
                    return  # Buttons not available
                
                if self._sort_mode == mode:
                    self._sort_desc = not self._sort_desc
                else:
                    self._sort_mode = mode
                    self._sort_desc = False
                active_tab = self._current_active_tab or self._get_current_tab_type() or 'shaders'
                self._sort_state_by_tab.setdefault(active_tab, {})
                self._sort_state_by_tab[active_tab]['mode'] = self._sort_mode
                self._sort_state_by_tab[active_tab]['desc'] = self._sort_desc
                self._apply_sort_button_styles(name_btn_fresh, type_btn_fresh, time_btn_fresh)
                self.refresh_materials_list()
                
                # Save state when sorting changes
                self._save_ui_state()
            
            name_btn.clicked.connect(lambda: on_click('name'))
            type_btn.clicked.connect(lambda: on_click('type'))
            time_btn.clicked.connect(lambda: on_click('time'))
            
            # Store in ui_elements (same pattern as other buttons)
            self.ui_elements['nameSortingButton'] = name_btn
            self.ui_elements['typeSortingButton'] = type_btn
            self.ui_elements['timeSortingButton'] = time_btn


    # Removed _create_sort_bar_widget - sorting buttons must be created in Qt Designer UI file only
    def _sync_sort_state_from_tab(self, tab_type, update_buttons=True):
        """
        Ensure global sort state reflects the active tab's saved state.
        """
        if not tab_type:
            return
        state = self._sort_state_by_tab.get(tab_type)
        if not state:
            state = {'mode': self._sort_mode, 'desc': self._sort_desc}
            self._sort_state_by_tab[tab_type] = state
        self._sort_mode = state.get('mode', self._sort_mode)
        self._sort_desc = state.get('desc', self._sort_desc)
        if update_buttons:
            self._update_sort_buttons_after_state_load()

    def _apply_sort_button_styles(self, name_btn, type_btn, time_btn):
        """
        Update button text and styling for sorting buttons.
        Only modifies text content, text color, and bold state.
        Leaves all other stylesheet properties unchanged.
        
        Button text + bolding + chip-blue for active:
          • Name: ↓ A–Z, ↑ Z–A
          • Type: group by type then name; arrow flips whole order
          • Time: creation order; arrow flips order
        """
        arrow = "↑" if self._sort_desc else "↓"

        # Update text content
        name_btn.setText(f"Name {arrow}" if self._sort_mode == 'name' else "Name")
        type_btn.setText(f"Type {arrow}" if self._sort_mode == 'type' else "Type")
        time_btn.setText(f"Time {arrow}" if self._sort_mode == 'time' else "Time")

        # Update font bold state
        nf, tf, tif = name_btn.font(), type_btn.font(), time_btn.font()
        nf.setBold(self._sort_mode == 'name');  name_btn.setFont(nf)
        tf.setBold(self._sort_mode == 'type');  type_btn.setFont(tf)
        tif.setBold(self._sort_mode == 'time'); time_btn.setFont(tif)

        # Update text color only - preserve all other stylesheet properties
        # Use a simple approach: append a color override that will take precedence
        txt_color_name = "#00f7c8" if self._sort_mode == 'name' else "#ffffff"
        txt_color_type = "#00f7c8" if self._sort_mode == 'type' else "#ffffff"
        txt_color_time = "#00f7c8" if self._sort_mode == 'time' else "#ffffff"
        
        # Store original stylesheet if not already stored
        if not hasattr(name_btn, '_original_stylesheet'):
            name_btn._original_stylesheet = name_btn.styleSheet() or ""
        if not hasattr(type_btn, '_original_stylesheet'):
            type_btn._original_stylesheet = type_btn.styleSheet() or ""
        if not hasattr(time_btn, '_original_stylesheet'):
            time_btn._original_stylesheet = time_btn.styleSheet() or ""
        
        # Apply original stylesheet + color override
        # The more specific rule will override the color
        name_btn.setStyleSheet(name_btn._original_stylesheet + f"\nQPushButton#nameSortingButton {{ color: {txt_color_name}; }}")
        type_btn.setStyleSheet(type_btn._original_stylesheet + f"\nQPushButton#typeSortingButton {{ color: {txt_color_type}; }}")
        time_btn.setStyleSheet(time_btn._original_stylesheet + f"\nQPushButton#timeSortingButton {{ color: {txt_color_time}; }}")

    def _update_sort_buttons_after_state_load(self):
        """
        Update sort button styles after UI state has been loaded.
        This ensures the sort buttons reflect the saved state.
        """
        # Get the UI-defined sorting buttons
        name_btn = self._get_widget('nameSortingButton', QtWidgets.QPushButton)
        type_btn = self._get_widget('typeSortingButton', QtWidgets.QPushButton)
        time_btn = self._get_widget('timeSortingButton', QtWidgets.QPushButton)
        
        if name_btn and type_btn and time_btn:
            self._apply_sort_button_styles(name_btn, type_btn, time_btn)


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
        # Create a horizontal layout to contain the material name widget
        material_layout = QtWidgets.QHBoxLayout()
        material_layout.setContentsMargins(2, 0, 2, 0)  # Reduced vertical padding
        material_layout.setSpacing(3)  # tighter spacing

        # No checkboxes anymore; swatch drives selection
        material_checkbox = None  # kept for minimal downstream edits
        # If you later reintroduce spacing where the checkbox was, add a small spacer here.

        # Create a read-only or editable line edit for the material name (unify metrics)
        material_widget = LeftClipLineEdit(material)
        # Link clicks on the line edit to Outliner-style selection (owner + method name, guarded)
        material_widget.setSelectionHandler(self, "handle_item_click", material)
        # Start unselected
        material_widget.setProperty("qmSelected", "false")
        material_widget.setProperty("qmEditMode", "false")  # Default to non-edit mode


        material_widget.style().unpolish(material_widget); material_widget.style().polish(material_widget)


        # Register this row for ordered selection behavior
        self._register_material_entry(material, None, material_widget, is_default=(material in default_materials))


        material_widget.setMinimumWidth(120)

        if material in default_materials:
            # Default materials: editable like other materials, just marked with italic text
            material_widget.setProperty("materialType", "default")
            material_widget.setProperty("editing", "false")   # ensure non-editing visual
            # Connect rename handlers for default materials
            if isinstance(material_widget, QtWidgets.QLineEdit):
                try:
                    # Commit on focus-out
                    material_widget.editingFinished.connect(partial(self.rename_material, material_widget))
                    # Commit also when pressing Enter
                    material_widget.returnPressed.connect(partial(self.rename_material, material_widget))
                except AttributeError:
                    print("Error: rename_material function not found")

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

        # Add the material line edit (no swatch)
        material_layout.addWidget(material_widget)

        # Make the line-edit take remaining space and shrink from the right
        material_layout.setStretch(0, 1)  # line edit expands

        if isinstance(material_widget, QtWidgets.QLineEdit):
            material_widget.setAlignment(QtCore.Qt.AlignLeft)
            material_widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
            material_widget.setMinimumWidth(50)  # allow aggressive shrink

        # Create a container widget for the material entry and add it to the scroll layout
        entry_container = QtWidgets.QWidget()
        entry_container.setLayout(material_layout)
        scroll_layout.addWidget(entry_container, row, 0, 1, 4)

    def _build_material_button_row(self, container, material, node_type_category, is_default):
        """
        Populate a material button container with action buttons if not already created.
        """
        if getattr(container, "_qm_buttons_populated", False):
            return

        button_layout = container.layout()
        if button_layout is None:
            button_layout = QtWidgets.QHBoxLayout(container)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(3)

        # Resolve current name from the row's QLineEdit at click-time
        entry_idx = self._index_by_material.get(material)
        line_edit_ref = None
        if isinstance(entry_idx, int) and 0 <= entry_idx < len(self._entry_list):
            line_edit_ref = self._entry_list[entry_idx].get("line_edit")

        def _current_name():
            try:
                if line_edit_ref and isValid(line_edit_ref):
                    actual_name = getattr(line_edit_ref, '_actual_material_name', None)
                    if actual_name:
                        return actual_name
                    return line_edit_ref.text().strip().split('  (')[0]  # Strip colorspace metadata if present
            except Exception:
                pass
            return material

        if node_type_category == 'file_textures':
            open_file_btn = QtWidgets.QPushButton("Open File")
            colorspace_btn = QtWidgets.QPushButton("Colorspace")

            for btn in (open_file_btn, colorspace_btn):
                btn.setFixedHeight(20)
                btn.setSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Fixed)
                btn.setMinimumWidth(0)
                btn.setStyleSheet(self.material_list_widget_style)

            open_file_btn.clicked.connect(lambda: self.open_file_texture_folder(_current_name()))
            colorspace_btn.clicked.connect(lambda: self.show_colorspace_menu(_current_name(), colorspace_btn))

            button_layout.addWidget(open_file_btn)
            button_layout.addWidget(colorspace_btn)
        else:
            assign_btn = QtWidgets.QPushButton("Assign")
            highlight_btn = QtWidgets.QPushButton("Select Objs")
            graph_btn = QtWidgets.QPushButton("Graph")

            for _b in (assign_btn, highlight_btn, graph_btn):
                _b.setFixedHeight(20)
                _b.setSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Fixed)
                _b.setMinimumWidth(0)
                _b.setStyleSheet(self.material_list_widget_style)

            assign_btn.clicked.connect(lambda: self.assign_material(_current_name()))
            highlight_btn.clicked.connect(lambda: self.highlight_material(_current_name()))
            graph_btn.clicked.connect(lambda: self.graph_material_network(_current_name()))

            button_layout.addWidget(assign_btn)
            button_layout.addWidget(highlight_btn)
            button_layout.addWidget(graph_btn)

            import_tx_btn = QtWidgets.QPushButton("Imp Tx")
            import_tx_btn.setStyleSheet(self.material_list_widget_style)
            import_tx_btn.setFixedHeight(20)
            import_tx_btn.setSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Fixed)
            import_tx_btn.setMinimumWidth(0)

            if is_default:
                import_tx_btn.setEnabled(False)
                import_tx_btn.setToolTip("Cannot import textures for default materials.")
            else:
                import_tx_btn.clicked.connect(lambda: self.import_tx_material(_current_name()))

            button_layout.addWidget(import_tx_btn)

        container._qm_buttons_populated = True

    # List buttons removed - functionality now available via right-click menu
    def add_material_buttons(self, material, row, scroll_layout, is_default, node_type_category=None):
        """
        List buttons have been removed. This function is kept for compatibility but does nothing.
        All functionality (Assign, Select Objs, Graph, Import Tx) is now available via right-click menu.
        """
        # No-op: buttons are no longer created
        return

    # Register a row in internal structures for selection/lookup.
    def _register_material_entry(self, material, swatch, line_edit, is_default=False, container=None):
        idx = len(self._entry_list)
        self._entry_list.append({
            "material": material,
            "swatch": swatch,  # store direct refs in PySide2
            "line_edit": line_edit,  # guard with isValid() before use
            "is_default": bool(is_default),
            "container": container,
        })
        self._index_by_material[material] = idx
        
        # OPTIMIZATION: Register attribute change callback for materials
        # This allows automatic swatch updates when material attributes change
        node_type_category = self._classify_node_type(material)
        # Debug: print(f"[QM][REGISTER] Registering material entry: {material}, type: {node_type_category}")
        if node_type_category == 'materials' and material not in getattr(self, "_material_attribute_callbacks", {}):
            # Debug: print(f"[QM][REGISTER] Material detected, registering attribute callback for: {material}")
            self._register_material_attribute_callback(material)

    # Determine a suitable RGB attribute on a material node (baseColor/color/etc.).
    def get_material_color_attribute(self, material):
        """
        Return the name of a suitable color attribute for the given material, if any.
        This method tries each attribute in a predefined list in order, returning
        the first one that exists and is a triple (RGB) value. If none are found, returns None.
        """

        if not cmds.objExists(material):
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
        """Click = single, Shift = range from anchor, Ctrl = toggle single. Defaults are now selectable."""
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
        
        # Default materials are now selectable (only difference is italic text and hideDefaults filter)

        current_selection = list(self.selected_materials_list or [])
        sel_set = set(current_selection)
        n = len(self._entry_list)

        if shift:
            # Determine a valid anchor - preserve existing anchor if valid
            # The anchor should remain stable throughout a shift-click sequence
            stored_anchor = self._selection_anchor if isinstance(self._selection_anchor, int) else None
            anchor_is_valid = (stored_anchor is not None and 0 <= stored_anchor < n)
            
            if anchor_is_valid:
                # Use the existing valid anchor - this preserves it during shift-click sequences
                anchor = stored_anchor
            else:
                # Anchor is invalid or missing - find the best anchor from current selection
                # Use the material with the minimum index (earliest in list) as anchor
                # This ensures consistent behavior when extending selection in either direction
                anchor = None
                min_idx = None
                for m in self.selected_materials_list or []:
                    ai = self._index_by_material.get(m)
                    if isinstance(ai, int) and 0 <= ai < n:
                        if min_idx is None or ai < min_idx:
                            min_idx = ai
                            anchor = ai
                # If still none, use current idx as anchor (first shift-click)
                if anchor is None:
                    anchor = idx
                # Save the newly calculated anchor for future shift-clicks
                self._selection_anchor = anchor

            # Calculate range from anchor to current click
            a, b = sorted((anchor, idx))
            a = max(0, min(a, n - 1))
            b = max(0, min(b, n - 1))

            # Include all materials in range, including defaults
            rng = [self._entry_list[i]["material"] for i in range(a, b + 1)]

            if ctrl:
                # Ctrl+Shift adds a range without duplicates, preserve list order
                for mat in rng:
                    if mat not in sel_set:
                        current_selection.append(mat)
                        sel_set.add(mat)
            else:
                # Pure Shift replaces selection with the contiguous range in list order
                current_selection = list(rng)
                sel_set = set(current_selection)

        elif ctrl:
            if material in sel_set:
                sel_set.remove(material)
                current_selection = [m for m in current_selection if m != material]
            else:
                current_selection.append(material)
                sel_set.add(material)
            # Anchor remains as last non-modified click

        else:
            # Plain click: replace selection with this material,
            # except if this is already the only selected item → toggle off.
            if material in sel_set and len(sel_set) == 1:
                current_selection = []
                sel_set.clear()
                self._selection_anchor = None
            else:
                current_selection = [material]
                sel_set = {material}
                self._selection_anchor = idx  # set new anchor on plain click

        self.selected_materials_list = current_selection
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
        btn = self.ui_elements.get('deleteSelectedButton')
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
        Includes default materials (they are now selectable).
        """
        button = self.ui_elements.get('selectAllVisibleMaterialsButton')
        if not button:
            print("Error: Select All button not found.")
            return

        is_selecting_all = (button.text() == "Select All")

        if is_selecting_all:
            # Include all materials, including defaults
            mats = [e["material"] for e in getattr(self, "_entry_list", [])]
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
            pass
        except Exception as e:
            pass

    # Remove selection-change scriptJob (cleanup).
    def _remove_selection_watcher(self):
        """Stop listening to selection changes."""
        try:
            import maya.cmds as cmds
            if getattr(self, "_sel_watcher_id", None):
                if cmds.scriptJob(exists=self._sel_watcher_id):
                    cmds.scriptJob(kill=self._sel_watcher_id, force=True)
                pass
        except Exception as e:
            pass
        finally:
            self._sel_watcher_id = None

    # scriptJob callback → update list selection to match scene (unless we initiated it).
    def _on_maya_selection_changed(self):
        """
        Mirror Maya's selection into the list without forcing a rebuild.
        If a selection-based filter is active, use fast filter instead of full rebuild.
        However, if selected materials are missing from the list, force a rebuild to include them.
        """
        # Defensive check: don't run if we're being destroyed
        if getattr(self, "_destroying", False):
            return
        if getattr(self, "_syncing_selection", False):
            return
        if hasattr(self, "_sync_list_from_scene_selection"):
            self._sync_list_from_scene_selection()
        if self._is_selection_filter_active():
            # Use fast filter if UI already exists - much faster than full rebuild
            if hasattr(self, '_entry_list') and self._entry_list:
                # Check if any materials from selection are missing from the current list
                try:
                    materials_from_selection = self._get_materials_from_selection()
                    if materials_from_selection:
                        # Get materials currently in the list
                        current_list_materials = {entry.get('material') for entry in self._entry_list if entry.get('material')}
                        # Check if any selected materials are missing
                        missing_materials = materials_from_selection - current_list_materials
                        if missing_materials:
                            # Force rebuild to include missing materials
                            self._invalidate_material_cache(selection_only=True)
                            self._queue_material_refresh(120)
                            return
                    
                    # All materials are in the list - use fast filter
                    self._invalidate_material_cache(selection_only=True)
                    search_text = getattr(self, '_current_search_text', '')
                    self._apply_search_filter(search_text)
                except Exception as e:
                    # If check fails, fall back to fast filter
                    pass
                    self._invalidate_material_cache(selection_only=True)
                    search_text = getattr(self, '_current_search_text', '')
                    self._apply_search_filter(search_text)
            else:
                # No UI yet - need full rebuild
                self._invalidate_material_cache(selection_only=True)
                self._queue_material_refresh(120)

    # Compute list selection from current Maya selection (materials only) and apply visuals.
    def _sync_list_from_scene_selection(self):
        """Mirror Maya's current selection into the list (materials, textures, shading groups, utilities)."""
        if getattr(self, "_rebuilding_list", False):
            return
        try:
            import maya.cmds as cmds
            # Get materials from selection
            scene_mats = set(cmds.ls(sl=True, materials=True) or [])
            # Also get texture, shading group, and utility nodes from selection
            all_selected = cmds.ls(sl=True) or []
            for node in all_selected:
                # Check for texture nodes
                if self._is_texture_node(node):
                    scene_mats.add(node)
                    continue
                # Check for shading groups (shadingEngine)
                try:
                    if cmds.nodeType(node) == 'shadingEngine':
                        scene_mats.add(node)
                        continue
                except Exception:
                    pass
                if self._is_utility_node(node):
                    scene_mats.add(node)
        except Exception as e:
            pass
            scene_mats = set()

        if not hasattr(self, "_entry_list"):
            return

        present = [e.get("material") for e in self._entry_list]
        new_sel = [m for m in present if m in scene_mats]  # keep list order

        self.selected_materials_list = new_sel
        if new_sel:
            # Only adjust anchor if it is currently invalid (e.g. after rebuild)
            current_anchor = self._selection_anchor if isinstance(self._selection_anchor, int) else None
            anchor_is_valid = (current_anchor is not None and 0 <= current_anchor < len(self._entry_list))
            if not anchor_is_valid:
                # Preserve the original plain-click anchor by using the first item in the ordered selection
                self._selection_anchor = self._index_by_material.get(new_sel[0], self._selection_anchor)

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
                # Identify materials, textures, shading groups, and utilities to exclude from "non-materials"
                cur_all = cmds.ls(sl=True) or []
                cur_mats = set(cmds.ls(sl=True, materials=True) or [])
                # Also get currently selected texture nodes and shading groups
                cur_textures = set()
                cur_shading_groups = set()
                cur_utilities = set()
                for node in cur_all:
                    if self._is_texture_node(node):
                        cur_textures.add(node)
                        continue
                    try:
                        if cmds.nodeType(node) == 'shadingEngine':
                            cur_shading_groups.add(node)
                            continue
                    except Exception:
                        pass
                    if self._is_utility_node(node):
                        cur_utilities.add(node)
                        continue
                # Non-materials should exclude materials, textures, shading groups, AND utilities
                cur_non_mats = [
                    n for n in cur_all
                    if n not in cur_mats
                    and n not in cur_textures
                    and n not in cur_shading_groups
                    and n not in cur_utilities
                ]

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

        # Debounce timer (create once) - PERFORMANCE OPTIMIZATION: Use our optimized refresh
        if not hasattr(self, "_mat_refresh_timer"):
            self._mat_refresh_timer = QtCore.QTimer(self)
            self._mat_refresh_timer.setSingleShot(True)
            # Single connection; we only start/stop the timer later
            self._mat_refresh_timer.timeout.connect(lambda: self._perform_actual_refresh())

        self._material_watch_job_ids = []
        self._om_callbacks = []  # OpenMaya callback ids

        # Probe supported scriptJob events once
        try:
            _events = set(cmds.scriptJob(listEvents=True) or [])
        except Exception:
            _events = set()
        # Optional: one-shot debug dump (comment out if too chatty)

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
        # OPTIMIZATION: For scriptJob events, we can't filter by node type easily,
        # but we'll rely on the OpenMaya callbacks for NodeAdded/NodeRemoved which are filtered.
        # For other events (Undo, Redo, SceneOpened), we still want to refresh.
        def _safe_scene_event_cb(*_):
            # #region agent log
            import json
            import time
            try:
                log_path = r"d:\Maya Tools\QuickMaterials\.cursor\debug.log"
                with open(log_path, 'a') as f:
                    f.write(json.dumps({
                        "sessionId": "debug-session",
                        "runId": "run1",
                        "hypothesisId": "A",
                        "location": "quick_materials.py:12510",
                        "message": "Undo/Redo scriptJob callback fired",
                        "data": {
                            "undo_state": cmds.undoInfo(query=True, state=True) if hasattr(cmds, 'undoInfo') else None,
                            "undo_queue_length": cmds.undoInfo(query=True, length=True) if hasattr(cmds, 'undoInfo') else None,
                        },
                        "timestamp": int(time.time() * 1000)
                    }) + "\n")
            except:
                pass
            # #endregion
            try:
                inst = self_ref()
                if not inst:
                    return
                if not isValid(inst):
                    return
                # Check if the instance is being destroyed
                if getattr(inst, '_destroying', False):
                    return
            except Exception:
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
        # OPTIMIZATION: Filter to only trigger for material-related nodes (not meshes/transforms)
        # OPTIMIZATION: Pass node name to enable tab-specific refresh
        def _om_v2_node_added(self_ref_wr):
            try:
                from maya.api import OpenMaya as om
                def _cb(obj, *a):
                    try:
                        inst = self_ref_wr()
                        if not inst:
                            return
                        if not isValid(inst):
                            return
                        # Check if the instance is being destroyed
                        if getattr(inst, '_destroying', False):
                            return
                    except Exception:
                        return
                    try:
                        name = om.MFnDependencyNode(obj).name()
                    except Exception:
                        name = None
                    # Only trigger refresh for material-related nodes
                    if name and inst._is_material_node_type(name):
                        inst._on_material_scene_event(node_name=name)
                return om.MDGMessage.addNodeAddedCallback(_cb, None)
            except Exception:
                return None

        def _om_v1_node_added(self_ref_wr):
            try:
                import maya.OpenMaya as om1
                def _cb(obj, clientData):
                    try:
                        inst = self_ref_wr()
                        if not inst:
                            return
                        if not isValid(inst):
                            return
                        # Check if the instance is being destroyed
                        if getattr(inst, '_destroying', False):
                            return
                    except Exception:
                        return
                    try:
                        fn = om1.MFnDependencyNode(obj)
                        name = fn.name()
                    except Exception:
                        name = None
                    # Only trigger refresh for material-related nodes
                    if name and inst._is_material_node_type(name):
                        inst._on_material_scene_event(node_name=name)
                return om1.MDGMessage.addNodeAddedCallback(_cb, None)
            except Exception:
                return None

        def _om_v2_node_removed(self_ref_wr):
            try:
                from maya.api import OpenMaya as om
                def _cb(obj, *a):
                    try:
                        inst = self_ref_wr()
                        if not inst:
                            return
                        if not isValid(inst):
                            return
                        # Check if the instance is being destroyed
                        if getattr(inst, '_destroying', False):
                            return
                    except Exception:
                        return
                    try:
                        name = om.MFnDependencyNode(obj).name()
                    except Exception:
                        name = None
                    # Only trigger refresh for material-related nodes
                    if name and inst._is_material_node_type(name):
                        inst._on_material_scene_event(node_name=name)
                return om.MDGMessage.addNodeRemovedCallback(_cb, None)
            except Exception:
                return None

        def _om_v1_node_removed(self_ref_wr):
            try:
                import maya.OpenMaya as om1
                def _cb(obj, clientData):
                    try:
                        inst = self_ref_wr()
                        if not inst:
                            return
                        if not isValid(inst):
                            return
                        # Check if the instance is being destroyed
                        if getattr(inst, '_destroying', False):
                            return
                    except Exception:
                        return
                    try:
                        fn = om1.MFnDependencyNode(obj)
                        name = fn.name()
                    except Exception:
                        name = None
                    # Only trigger refresh for material-related nodes
                    if name and inst._is_material_node_type(name):
                        inst._on_material_scene_event(node_name=name)
                return om1.MDGMessage.addNodeRemovedCallback(_cb, None)
            except Exception:
                return None

        # OPTIMIZATION: Don't register unfiltered NodeAdded/NodeRemoved scriptJobs
        # We use filtered OpenMaya callbacks directly instead to avoid reloading on mesh creation/duplication
        # Register the filtered OpenMaya callbacks directly (they already check _is_material_node_type)
        cb_id = _om_v2_node_added(self_ref)
        if cb_id:
            self._om_callbacks.append(cb_id)
        cb_id = _om_v1_node_added(self_ref)
        if cb_id:
            self._om_callbacks.append(cb_id)
        cb_id = _om_v2_node_removed(self_ref)
        if cb_id:
            self._om_callbacks.append(cb_id)
        cb_id = _om_v1_node_removed(self_ref)
        if cb_id:
            self._om_callbacks.append(cb_id)
        
        # Only register other events that don't need filtering
        # CRITICAL FIX: Removed Undo/Redo scriptJobs - they trigger refresh operations
        # that query Maya during undo/redo, which corrupts the undo queue and prevents redo.
        # The OpenMaya callbacks already handle material changes, so we don't need these.
        # _add_job_multi("Undo")  # REMOVED: Causes undo queue corruption
        # _add_job_multi("Redo")  # REMOVED: Causes undo queue corruption
        _add_job_multi("SceneOpened")
        _add_job_multi("NewSceneOpened")

        # NEW: reference/import events (only register if host supports them)
        for ev in (
            "AfterReferenceLoad", "AfterReferenceUnload",
            "ReferenceEditsAdded", "ReferenceEditsRemoved",
            "AfterImport", "AfterFileRead", "PostSceneRead"
        ):
            _add_job_multi(ev)
        
        # OPTIMIZATION: Install attribute change monitoring for materials
        # We'll register per-material callbacks when materials are added to the list
        # This is handled in _register_material_attribute_callbacks()
        # For now, we initialize the tracking dictionary
        if not hasattr(self, "_material_attribute_callbacks"):
            self._material_attribute_callbacks = {}  # material_name -> callback_id



        # Ensure clean-up when this Qt object is destroyed
        try:
            # Mark that we're being destroyed to prevent callbacks from running
            self._destroying = False
            # When the widget is deleted, remove jobs
            self.destroyed.connect(self._on_destroyed)
        except Exception:
            pass

        # Initial kick to ensure we're up-to-date
        self._queue_material_refresh(0)
    
    def _on_destroyed(self):
        """Called when the widget is being destroyed - ensures all cleanup happens."""
        try:
            self._destroying = True
            self._remove_material_watchers()
            self._remove_selection_watcher()
            self._remove_workspace_state_job()
        except Exception as e:
            pass


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
        # Remove material attribute callbacks (CRITICAL: prevents crashes on reload)
        try:
            callbacks_dict = getattr(self, "_material_attribute_callbacks", {})
            if callbacks_dict:
                # Try OpenMaya v2 first
                try:
                    from maya.api import OpenMaya as om
                    for material_name, cb_id in list(callbacks_dict.items()):
                        try:
                            if cb_id is not None:
                                om.MMessage.removeCallback(cb_id)
                                pass
                        except Exception as e:
                            pass
                except Exception:
                    pass
                
                # Fallback to OpenMaya v1
                try:
                    import maya.OpenMaya as om1
                    for material_name, cb_id in list(callbacks_dict.items()):
                        try:
                            if cb_id is not None:
                                om1.MMessage.removeCallback(cb_id)
                                pass
                        except Exception as e:
                            pass
                except Exception:
                    pass
            
            # Clear the callbacks dictionary
            self._material_attribute_callbacks = {}
        except Exception as e:
            pass
        
        # Remove scriptJobs
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

        # Stop all timers
        try:
            if hasattr(self, "_material_poll_timer") and self._material_poll_timer is not None:
                self._material_poll_timer.stop()
                self._material_poll_timer.deleteLater()
        except Exception:
            pass
        
        try:
            if hasattr(self, "_mat_refresh_timer") and self._mat_refresh_timer is not None:
                self._mat_refresh_timer.stop()
                self._mat_refresh_timer.deleteLater()
        except Exception:
            pass
        
        try:
            if hasattr(self, "_tab_refresh_timers") and self._tab_refresh_timers:
                for timer in self._tab_refresh_timers.values():
                    if timer is not None:
                        timer.stop()
                        timer.deleteLater()
                self._tab_refresh_timers = {}
        except Exception:
            pass
        
        try:
            if hasattr(self, "_refresh_timer") and self._refresh_timer is not None:
                self._refresh_timer.stop()
                self._refresh_timer.deleteLater()
        except Exception:
            pass
        
        try:
            if hasattr(self, "_save_timer") and self._save_timer is not None:
                self._save_timer.stop()
                self._save_timer.deleteLater()
        except Exception:
            pass
        
        try:
            if hasattr(self, "_settings_save_timer") and self._settings_save_timer is not None:
                self._settings_save_timer.stop()
                self._settings_save_timer.deleteLater()
        except Exception:
            pass

    def _get_tab_for_node_type(self, node_name):
        """
        Determine which tab a node belongs to.
        Returns: 'shaders', 'textures', 'shading_groups', 'utilities', or None
        """
        try:
            if not cmds.objExists(node_name):
                return None
            
            node_type_category = self._classify_node_type(node_name)
            if not node_type_category:
                return None
            
            # Map node type categories to tabs
            if node_type_category == 'materials':
                return 'shaders'
            elif node_type_category in ('file_textures', 'procedural_textures'):
                return 'textures'
            elif node_type_category == 'shading_groups':
                return 'shading_groups'
            elif node_type_category == 'utilities':
                return 'utilities'
            
            return None
        except Exception:
            return None

    # Generic scene-event callback → schedule a list refresh (debounced).
    def _on_material_scene_event(self, node_name=None, *args):
        """
        scriptJob callback → debounce a UI refresh.
        OPTIMIZATION: If node_name is provided, only refresh the relevant tab.
        CRITICAL: Only refresh for material-related nodes. Ignore mesh/transform edits.
        """
        # #region agent log
        import json
        import time
        try:
            log_path = r"d:\Maya Tools\QuickMaterials\.cursor\debug.log"
            with open(log_path, 'a') as f:
                f.write(json.dumps({
                    "sessionId": "debug-session",
                    "runId": "run1",
                    "hypothesisId": "A",
                    "location": "quick_materials.py:12892",
                    "message": "_on_material_scene_event called",
                    "data": {
                        "node_name": node_name,
                        "undo_state": cmds.undoInfo(query=True, state=True) if hasattr(cmds, 'undoInfo') else None,
                    },
                    "timestamp": int(time.time() * 1000)
                }) + "\n")
        except:
            pass
        # #endregion
        # Defensive check: don't run if we're being destroyed
        if getattr(self, "_destroying", False):
            return
        
        # DEBUG: Log what's triggering the refresh (can be disabled later)
        if getattr(self, "_debug_refresh_triggers", False):
            import traceback
            caller = traceback.extract_stack()[-2]
            print(f"[QM][DEBUG] _on_material_scene_event called: node_name={node_name}, caller={caller.filename}:{caller.lineno}")
        
        if node_name:
            # CRITICAL FIX: Verify this is actually a material-related node before refreshing
            # This prevents refreshes when meshes, transforms, or other non-material nodes change
            if not self._is_material_node_type(node_name):
                # This is not a material node - ignore it completely
                if getattr(self, "_debug_refresh_triggers", False):
                    try:
                        node_type = cmds.nodeType(node_name)
                        print(f"[QM][DEBUG] Ignoring non-material node: {node_name} (type: {node_type})")
                    except:
                        print(f"[QM][DEBUG] Ignoring non-material node: {node_name}")
                return
            
            # Determine which tab this node belongs to
            tab = self._get_tab_for_node_type(node_name)
            if tab:
                # Only refresh the specific tab
                self._queue_tab_refresh(tab, delay_ms=150)
                return
            else:
                # This shouldn't happen if _is_material_node_type returned True, but handle it
                if getattr(self, "_debug_refresh_triggers", False):
                    print(f"[QM][DEBUG] Material node {node_name} has no tab, skipping refresh")
                return
        else:
            # No node name provided - this is from scriptJob events like SceneOpened
            # Note: Undo/Redo scriptJobs were removed to prevent undo queue corruption
            # These are legitimate reasons to refresh, so allow them
            # #region agent log
            import json
            import time
            try:
                log_path = r"d:\Maya Tools\QuickMaterials\.cursor\debug.log"
                with open(log_path, 'a') as f:
                    f.write(json.dumps({
                        "sessionId": "debug-session",
                        "runId": "run1",
                        "hypothesisId": "A",
                        "location": "quick_materials.py:12935",
                        "message": "Queueing refresh from Undo/Redo event",
                        "data": {
                            "undo_state": cmds.undoInfo(query=True, state=True) if hasattr(cmds, 'undoInfo') else None,
                        },
                        "timestamp": int(time.time() * 1000)
                    }) + "\n")
            except:
                pass
            # #endregion
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
    
    def _queue_tab_refresh(self, tab, delay_ms=120):
        """
        OPTIMIZATION: Queue a refresh for a specific tab only.
        This avoids refreshing all tabs when only one tab's content changed.
        
        Args:
            tab: Tab name ('shaders', 'textures', 'shading_groups', 'utilities')
            delay_ms: Debounce delay in milliseconds
        """
        # Suppress refresh while we're doing an in-place rename or muted poll window
        if getattr(self, "_suspend_refresh_count", 0) > 0:
            return
        
        # Initialize tab-specific timers if needed
        if not hasattr(self, "_tab_refresh_timers"):
            self._tab_refresh_timers = {}
        
        if tab not in self._tab_refresh_timers:
            timer = QtCore.QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: self._perform_tab_refresh(tab))
            self._tab_refresh_timers[tab] = timer
        
        timer = self._tab_refresh_timers[tab]
        timer.stop()
        timer.start(max(0, int(delay_ms)))
    
    def _perform_tab_refresh(self, tab):
        """
        Refresh only a specific tab instead of the entire list.
        This is much faster when only one tab's content changed.
        """
        try:
            if getattr(self, "_suspend_refresh_count", 0) > 0:
                return
            if not self._is_ui_alive() or getattr(self, "_rebuilding_list", False):
                return
            
            # Get the current search text
            materialSearchLineEdit = self.ui_elements.get('materialSearchLineEdit')
            search_text = materialSearchLineEdit.text() if materialSearchLineEdit else ""
            
            # Refresh the entire list (populate_materials_scroll_area handles all tabs)
            # But we could optimize further by only refreshing the specific tab's content
            # For now, we'll do a full refresh but this is still better than refreshing on every node creation
            self.populate_materials_scroll_area(search_text=search_text)
        except Exception as e:
            print(f"[QM] Error refreshing tab {tab}: {e}")
    
    def enable_refresh_debug_logging(self, enable=True):
        """
        Enable or disable debug logging for refresh triggers.
        This helps identify what's causing the shader list to reload unexpectedly.
        
        Usage:
            ui = QuickMaterialsUI.get_instance()
            ui.enable_refresh_debug_logging(True)  # Enable debug logging
            ui.enable_refresh_debug_logging(False)  # Disable debug logging
        """
        self._debug_refresh_triggers = enable
        if enable:
            print("[QM][DEBUG] Refresh trigger debug logging ENABLED")
            print("[QM][DEBUG] You will see messages when the shader list refresh is triggered")
        else:
            print("[QM][DEBUG] Refresh trigger debug logging DISABLED")


    # When "Selected Only" is active, react quickly to selection changes.
    def _on_selection_changed(self, *args):
        """
        Legacy hook; we now mirror selection without forcing a list rebuild.
        """
        if hasattr(self, "_sync_list_from_scene_selection"):
            self._sync_list_from_scene_selection()


    def _is_material_node_type(self, node_name):
        """
        Return True if node is a shader/material/texture/shading group.
        This is used by scene callbacks to determine if a new node should trigger a list refresh.
        """
        try:
            if not cmds.objExists(node_name):
                return False
            
            t = cmds.nodeType(node_name)
            if not t:
                return False
            
            # Check if it's a shading group first
            if t == 'shadingEngine':
                return True
            
            # Check if it's a texture
            if self._is_texture_node(node_name):
                return True
            
            # Check if it's a shader/material
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
        assignment_successful = False
        try:
            for obj in selected_objs:
                cmds.sets(obj, edit=True, forceElement=shading_group)
                print(f"Assigned {material} to {obj}.")
                assignment_successful = True
        except Exception as e:
            cmds.warning(f"Failed to assign material: {e}")
        finally:
            cmds.undoInfo(closeChunk=True)
        
        # If assignment was successful, update the material widget's unused highlighting
        if assignment_successful:
            self._update_material_unused_status(material, is_used=True)

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

    def highlight_materials_batch(self, materials):
        """Select objects assigned to all specified materials (batch operation)."""
        all_objects = []
        
        for material in materials:
            shading_group = cmds.listConnections(material + '.outColor', type='shadingEngine')
            if shading_group:
                objects_with_material = cmds.sets(shading_group[0], q=True)
                if objects_with_material:
                    all_objects.extend(objects_with_material)
        
        if all_objects:
            # Remove duplicates while preserving order
            unique_objects = list(dict.fromkeys(all_objects))
            cmds.select(unique_objects, r=True, ne=True)
            print(f"[QM] Selected {len(unique_objects)} objects from {len(materials)} materials.")
        else:
            cmds.warning(f"No objects found with any of the {len(materials)} selected materials.")

    def select_objects_for_shading_group(self, shading_group):
        """Select all objects that are assigned to the specified shading group."""
        if not shading_group or not cmds.objExists(shading_group):
            cmds.warning(f"Shading group '{shading_group}' does not exist.")
            return

        if cmds.nodeType(shading_group) != 'shadingEngine':
            cmds.warning(f"Node '{shading_group}' is not a shading group.")
            return

        members = cmds.sets(shading_group, q=True) or []
        if not members:
            cmds.warning(f"No objects found using shading group '{shading_group}'.")
            return

        transforms = []
        for member in members:
            target = member.split('.')[0] if '.' in member else member
            if cmds.objectType(target, isAType='shape'):
                parents = cmds.listRelatives(target, parent=True, fullPath=True) or []
                if parents:
                    target = parents[0]
            transforms.append(target)

        unique_transforms = [obj for obj in dict.fromkeys(transforms) if cmds.objExists(obj)]
        if unique_transforms:
            cmds.select(unique_transforms, r=True, ne=True)
            print(f"[QM] Selected {len(unique_transforms)} objects from shading group '{shading_group}'.")
        else:
            cmds.warning(f"No valid objects found for shading group '{shading_group}'.")

    # Select the material node itself.
    def select_material(self, material):
        # Replace only materials subset; keep meshes selected; make this material active
        self.selected_materials_list = [material]
        self._last_selected_material = material
        self._apply_selection_visuals()
        self._defer_scene_select_from_list(additive=False)

    # Rename material from line-edit edit; triggers refresh on success.
    def rename_texture(self, texture_name_edit):
        """
        Rename texture nodes in-place without rebuilding the list UI.
        Similar to rename_material but for texture nodes.
        """
        import time as _t
        prev_name = getattr(texture_name_edit, "_pre_edit_text", None) or texture_name_edit.text()
        new_name = (texture_name_edit.text() or "").strip()

        if new_name == prev_name:
            # Name unchanged, but still clear focus and lock to allow tab switching
            if not texture_name_edit.isReadOnly():
                texture_name_edit.setReadOnly(True)
                texture_name_edit.setProperty("editing", "false")
                texture_name_edit.setProperty("qmEditMode", "false")
                texture_name_edit.style().unpolish(texture_name_edit)
                texture_name_edit.style().polish(texture_name_edit)
                texture_name_edit.update()
                texture_name_edit.clearFocus()
            return

        # Silence auto-refreshers briefly so no rebuild occurs during rename
        self._begin_silent_refresh(mute_ms=800)

        def _update_internal_maps(_old, _actual_new):
            # Update entry registry and index map for textures
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
                        setattr(le, "_qm_material_name", _actual_new)
                except Exception:
                    pass

            # Update selected list if needed
            if _old in (self.selected_materials_list or []):
                try:
                    i = self.selected_materials_list.index(_old)
                    self.selected_materials_list[i] = _actual_new
                except (ValueError, IndexError):
                    pass

        try:
            # Check if texture exists
            if not cmds.objExists(prev_name):
                cmds.warning(f"Texture '{prev_name}' no longer exists. Skipping rename.")
                # Revert display
                texture_name_edit.setText(prev_name)
                # Clear focus and lock to prevent stuck edit mode
                if not texture_name_edit.isReadOnly():
                    texture_name_edit.setReadOnly(True)
                    texture_name_edit.setProperty("editing", "false")
                    texture_name_edit.setProperty("qmEditMode", "false")
                    texture_name_edit.style().unpolish(texture_name_edit)
                    texture_name_edit.style().polish(texture_name_edit)
                    texture_name_edit.update()
                    texture_name_edit.clearFocus()
                return

            # Rename the texture node in Maya
            actual_new = cmds.rename(prev_name, new_name)
            print(f"[QM] Renamed texture: {prev_name} → {actual_new}")
            
            # Update internal tracking
            _update_internal_maps(prev_name, actual_new)

            # Update display to reflect Maya's actual rename result
            texture_name_edit.setText(actual_new)
            setattr(texture_name_edit, "_pre_edit_text", actual_new)

            # Immediately lock again after pressing Enter (same as rename_material)
            if not texture_name_edit.isReadOnly():
                texture_name_edit.setReadOnly(True)
                texture_name_edit.setProperty("editing", "false")
                texture_name_edit.setProperty("qmEditMode", "false")  # Disable edit mode highlighting
                texture_name_edit.style().unpolish(texture_name_edit)
                texture_name_edit.style().polish(texture_name_edit)
                texture_name_edit.update()
                # Clear focus so tab buttons can be clicked after renaming
                texture_name_edit.clearFocus()

        except Exception as e:
            cmds.warning(f"Failed to rename texture '{prev_name}' to '{new_name}': {e}")
            texture_name_edit.setText(prev_name)
            # Also lock on error to prevent stuck edit mode
            if not texture_name_edit.isReadOnly():
                texture_name_edit.setReadOnly(True)
                texture_name_edit.setProperty("editing", "false")
                texture_name_edit.setProperty("qmEditMode", "false")
                texture_name_edit.style().unpolish(texture_name_edit)
                texture_name_edit.style().polish(texture_name_edit)
                texture_name_edit.update()
                # Clear focus so tab buttons can be clicked after renaming
                texture_name_edit.clearFocus()
        finally:
            # Allow normal refreshes again after the mute window
            self._end_silent_refresh()

    def rename_material(self, material_name_edit):
        """
        Rename in-place without rebuilding the list UI.
        Uses Maya's returned name (handles duplicate -> name1) and updates maps.
        """
        import time as _t
        # Get the actual material name (with namespace) if available, otherwise use display name
        actual_prev_name = getattr(material_name_edit, "_actual_material_name", None)
        if actual_prev_name:
            prev_name = actual_prev_name
        else:
            prev_name = getattr(material_name_edit, "_pre_edit_text", None) or material_name_edit.text()
        
        new_name = (material_name_edit.text() or "").strip()
        
        # Compare display names (not actual names) to avoid false positives when namespaces are hidden
        display_prev_name = self._strip_namespace(prev_name)
        if new_name == display_prev_name:
            # Name unchanged, but still clear focus and lock to allow tab switching
            if not material_name_edit.isReadOnly():
                material_name_edit.setReadOnly(True)
                material_name_edit.setProperty("editing", "false")
                material_name_edit.setProperty("qmEditMode", "false")
                material_name_edit.style().unpolish(material_name_edit)
                material_name_edit.style().polish(material_name_edit)
                material_name_edit.update()
                material_name_edit.clearFocus()
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
                # Clear focus and lock to allow tab switching
                if not material_name_edit.isReadOnly():
                    material_name_edit.setReadOnly(True)
                    material_name_edit.setProperty("editing", "false")
                    material_name_edit.setProperty("qmEditMode", "false")
                    material_name_edit.style().unpolish(material_name_edit)
                    material_name_edit.style().polish(material_name_edit)
                    material_name_edit.update()
                    material_name_edit.clearFocus()
                return

            # Perform Maya rename; this returns the ACTUAL final name (handles duplicates -> name1)
            actual_new = cmds.rename(prev_name, new_name)

            # Update _actual_material_name to the new name
            try:
                material_name_edit._actual_material_name = actual_new
            except Exception:
                pass

            # Update the widget text to the display name (may have namespace stripped)
            display_name = self._strip_namespace(actual_new)
            material_name_edit.setText(display_name)
            try:
                material_name_edit._pre_edit_text = display_name
            except Exception:
                pass

            # Immediately lock again after pressing Enter
            if not material_name_edit.isReadOnly():
                material_name_edit.setReadOnly(True)
                material_name_edit.setProperty("editing", "false")
                material_name_edit.setProperty("qmEditMode", "false")  # Disable edit mode highlighting
                material_name_edit.style().unpolish(material_name_edit)
                material_name_edit.style().polish(material_name_edit)
                material_name_edit.update()
                # Clear focus so tab buttons can be clicked after renaming
                material_name_edit.clearFocus()

            # Update all our internal mappings to the ACTUAL name
            _update_internal_maps(prev_name, actual_new)

            cache = getattr(self, "_material_type_cache", None)
            if isinstance(cache, dict):
                cache.pop(prev_name, None)
                cache.pop(actual_new, None)

            # Re-polish just this widget so visuals stay crisp
            try:
                material_name_edit.style().unpolish(material_name_edit); material_name_edit.style().polish(material_name_edit)
            except Exception:
                pass

        except Exception as e:
            # On failure, revert visible text; keep maps untouched
            # Use display name for the text (may have namespace stripped)
            display_prev_name = self._strip_namespace(prev_name)
            material_name_edit.setText(display_prev_name)
            try:
                material_name_edit._pre_edit_text = display_prev_name
            except Exception:
                pass
            cmds.warning(f"Failed to rename material: {e}")
            # Clear focus and lock on error to prevent stuck edit mode
            if not material_name_edit.isReadOnly():
                material_name_edit.setReadOnly(True)
                material_name_edit.setProperty("editing", "false")
                material_name_edit.setProperty("qmEditMode", "false")
                material_name_edit.style().unpolish(material_name_edit)
                material_name_edit.style().polish(material_name_edit)
                material_name_edit.update()
                material_name_edit.clearFocus()
        finally:
            # Allow normal refreshes again after the mute window
            self._end_silent_refresh()

    # Launch ImportTxTool for a given material and type; manages singleton instance.
    def import_tx_material(self, material=None):
        """Opens file dialog to select textures, then opens Import Tx Tool UI for the selected material."""
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

        # Open file dialog to select textures
        selected_textures = self._select_textures_for_import()
        if not selected_textures:
            return  # User cancelled

        # Initialize and show the Import Tx Tool with the correct material and type
        self.import_tx_tool = ImportTxTool(material=material, material_type=material_type, parent=maya_main_window())
        self.import_tx_tool.show()
        
        # Pre-populate the texture importer with selected textures
        self.import_tx_tool._pre_populate_textures(selected_textures)

    def _select_textures_for_import(self):
        """Open file dialog to select texture files for import.
        
        Returns:
            list: List of selected texture file paths, or None if cancelled
        """
        # Simple file selection dialog - clean and straightforward
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Select Textures for Import",
            "",
            "Image Files (*.png *.jpg *.jpeg *.tif *.tiff *.exr *.tga *.bmp);;All Files (*.*)"
        )
        
        return files if files else None
    
    def duplicate_material(self, material):
        """
        Duplicate a material along with its entire shading network.
        Handles reference materials by unlocking duplicated nodes to ensure they can be assigned.
        """
        try:
            if not cmds.objExists(material):
                cmds.warning(f"Material '{material}' does not exist.")
                return
            
            # Get the shading engine connected to this material
            sgs = cmds.listConnections(material, type='shadingEngine', destination=True) or []
            if not sgs:
                cmds.warning(f"No shading group found for material '{material}'.")
                return
            
            sg = sgs[0]
            
            # Duplicate the entire shading network
            # This includes the material, its shading group, and all connected texture nodes
            duplicated = cmds.duplicate(sg, upstreamNodes=True)
            
            if not duplicated:
                cmds.warning(f"Failed to duplicate shading network for '{material}'.")
                return
            
            # The first node in the list is typically the duplicated shading group
            new_sg = duplicated[0]
            
            # Unlock all duplicated nodes to ensure they can be connected to scene geometry
            # This is especially important for reference materials where duplicated nodes
            # may still have locked attributes preventing connections
            for node in duplicated:
                try:
                    # Unlock the node itself (safe to call even if already unlocked)
                    try:
                        cmds.lockNode(node, lock=False)
                    except Exception:
                        pass
                    
                    # Unlock all locked attributes on the node
                    try:
                        attrs = cmds.listAttr(node, locked=True) or []
                        for attr in attrs:
                            try:
                                attr_full = f"{node}.{attr}"
                                cmds.setAttr(attr_full, lock=False)
                            except Exception:
                                pass
                    except Exception:
                        pass
                except Exception:
                    pass
            
            # Specifically ensure the shading group's dagSetMembers attribute is unlocked
            # This is critical for being able to assign geometry to the shading group
            try:
                dag_members_attr = f"{new_sg}.dagSetMembers"
                if cmds.objExists(dag_members_attr):
                    cmds.setAttr(dag_members_attr, lock=False)
            except Exception:
                pass
            
            # Find the new material in the duplicated network
            new_materials = cmds.listConnections(f"{new_sg}.surfaceShader", source=True) or []
            if new_materials:
                new_material = new_materials[0]
                print(f"[QM] Duplicated material: {material} → {new_material}")
                
                # Refresh the list to show the new material
                self._invalidate_material_cache()
                self.refresh_materials_list()
                
                # Select the new material in Maya
                cmds.select(new_material, replace=True)
            else:
                cmds.warning(f"Failed to find duplicated material for '{material}'.")
        except Exception as e:
            cmds.warning(f"Failed to duplicate material '{material}': {e}")
            print(f"[QM] Duplicate error: {e}")
    
    def duplicate_selected_materials(self):
        """
        Duplicate all selected materials along with their shading networks.
        """
        selected_mats = getattr(self, "selected_materials_list", [])
        if not selected_mats:
            cmds.warning("No materials selected.")
            return
        
        # Filter out textures and shading groups - only duplicate materials
        materials_to_duplicate = []
        for mat in selected_mats:
            try:
                if cmds.ls(mat, materials=True):
                    materials_to_duplicate.append(mat)
            except Exception:
                pass
        
        if not materials_to_duplicate:
            cmds.warning("No materials selected to duplicate.")
            return
        
        duplicated_materials = []
        for material in materials_to_duplicate:
            try:
                # Get the shading engine connected to this material
                sgs = cmds.listConnections(material, type='shadingEngine', destination=True) or []
                if not sgs:
                    continue
                
                sg = sgs[0]
                
                # Duplicate the entire shading network
                duplicated = cmds.duplicate(sg, upstreamNodes=True)
                if not duplicated:
                    continue
                
                new_sg = duplicated[0]
                
                # Unlock all duplicated nodes to ensure they can be connected to scene geometry
                # This is especially important for reference materials where duplicated nodes
                # may still have locked attributes preventing connections
                for node in duplicated:
                    try:
                        # Check if node is locked and unlock it
                        if cmds.lockNode(node, query=True, lock=True)[0]:
                            cmds.lockNode(node, lock=False)
                        # Unlock all attributes on the node
                        attrs = cmds.listAttr(node, locked=True) or []
                        for attr in attrs:
                            try:
                                attr_full = f"{node}.{attr}"
                                cmds.setAttr(attr_full, lock=False)
                            except Exception:
                                pass
                    except Exception:
                        pass
                
                # Specifically ensure the shading group's dagSetMembers attribute is unlocked
                # This is critical for being able to assign geometry to the shading group
                try:
                    dag_members_attr = f"{new_sg}.dagSetMembers"
                    if cmds.objExists(dag_members_attr):
                        cmds.setAttr(dag_members_attr, lock=False)
                except Exception:
                    pass
                
                # Find the new material
                new_materials = cmds.listConnections(f"{new_sg}.surfaceShader", source=True) or []
                if new_materials:
                    duplicated_materials.append(new_materials[0])
            except Exception as e:
                print(f"[QM] Failed to duplicate '{material}': {e}")
        
        if duplicated_materials:
            print(f"[QM] Duplicated {len(duplicated_materials)} material(s)")
            
            # Refresh the list to show the new materials
            self._invalidate_material_cache()
            self.refresh_materials_list()
            
            # Select the new materials in Maya
            cmds.select(duplicated_materials, replace=True)
        else:
            cmds.warning("Failed to duplicate any materials.")

    # Open Node Editor and graph the material + upstream + SGs; frame selection.
    def graph_material_network(self, material, hops_up=6, step_delay_ms=40, open_timeout_ms=1500, include_downstream=True, filter_geometry=False):
        """
        Robust one-click graph with debug prints and micro timers.
        ...
        """
        prev_sel = cmds.ls(sl=True) or []


        try:
            node_category = self._classify_node_type(material)
        except Exception:
            node_category = None

        # ---- open editor and poll until control exists ----
        start = time.time()
        try:
            mel.eval('NodeEditorWindow;')
            print("[QM][Graph] NodeEditorWindow; executed")
        except Exception as e:
            pass

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
                            # Filter out geometry nodes if filter_geometry is True
                            if filter_geometry:
                                try:
                                    node_type = cmds.nodeType(u)
                                    obj_type = cmds.objectType(u)
                                    # Skip transforms, shapes, and meshes
                                    if obj_type in ('transform', 'mesh', 'nurbsSurface', 'nurbsCurve', 'subdiv') or \
                                       node_type in ('transform', 'mesh', 'nurbsSurface', 'nurbsCurve', 'subdiv'):
                                        continue
                                except Exception:
                                    pass
                            visited.add(u)
                            out.append(u)
                            nxt.append(u)
                if not nxt:
                    break
                frontier = nxt
            return out

        def _walk_outputs(seed_nodes, max_hops=6):
            if not seed_nodes:
                return []
            visited = set(seed_nodes)
            frontier = list(seed_nodes)
            out = []
            for _ in range(max_hops):
                nxt = []
                for n in list(frontier):
                    try:
                        downstream = cmds.listConnections(n, source=False, destination=True) or []
                    except Exception:
                        downstream = []
                    for d in downstream:
                        if d not in visited and cmds.objExists(d):
                            # Filter out geometry nodes if filter_geometry is True
                            if filter_geometry:
                                try:
                                    node_type = cmds.nodeType(d)
                                    obj_type = cmds.objectType(d)
                                    # Skip transforms, shapes, and meshes
                                    if obj_type in ('transform', 'mesh', 'nurbsSurface', 'nurbsCurve', 'subdiv') or \
                                       node_type in ('transform', 'mesh', 'nurbsSurface', 'nurbsCurve', 'subdiv'):
                                        continue
                                except Exception:
                                    pass
                            visited.add(d)
                            out.append(d)
                            nxt.append(d)
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


        # Helper to filter out geometry nodes
        def _filter_geometry_nodes(nodes):
            """Filter out transform, shape, and mesh nodes."""
            if not filter_geometry:
                return nodes
            filtered = []
            for n in nodes:
                try:
                    node_type = cmds.nodeType(n)
                    obj_type = cmds.objectType(n)
                    # Skip transforms, shapes, and meshes
                    if obj_type in ('transform', 'mesh', 'nurbsSurface', 'nurbsCurve', 'subdiv') or \
                       node_type in ('transform', 'mesh', 'nurbsSurface', 'nurbsCurve', 'subdiv'):
                        continue
                    filtered.append(n)
                except Exception:
                    # If we can't determine the type, include it (better safe than sorry)
                    filtered.append(n)
            return filtered

        # ---- PHASE A: add nodes ----
        def _phase_add(ed_local):
            # Recompute seeds & upstream right before add (in case scene changed)
            sgs = _material_sgs(material) if node_category == 'materials' else []
            upstream = _walk_inputs([material], max_hops=hops_up)
            downstream = _walk_outputs([material], max_hops=hops_up) if include_downstream else []

            # Everything we want to appear in the editor:
            all_nodes = list(dict.fromkeys(list(sgs) + [material] + upstream + downstream))
            # Filter out geometry nodes if requested
            nodes_to_add = _filter_geometry_nodes(all_nodes)
            # Everything we want to SELECT & FRAME at the end (material + SG + inputs):
            nodes_to_select = _filter_geometry_nodes(all_nodes)

            print(f"[QM][Graph][A] Editor: {ed_local}")
            print(f"[QM][Graph][A] SGs: {sgs}")
            print(f"[QM][Graph][A] Upstream count: {len(upstream)} Downstream count: {len(downstream)} total add: {len(nodes_to_add)}")

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
            # For textures, only select the texture node itself
            is_texture_node = node_category in ('file_textures', 'procedural_textures')
            if is_texture_node:
                to_pick = [material] if material else []
            else:
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

    def graph_shading_group_network(self, shading_group, hops_up=6):
        """Graph a shading group and its attached shaders without pulling downstream geometry."""
        if not shading_group or not cmds.objExists(shading_group):
            cmds.warning(f"Shading group '{shading_group}' does not exist.")
            return
        if cmds.nodeType(shading_group) != 'shadingEngine':
            cmds.warning(f"Node '{shading_group}' is not a shading group.")
            return
        
        # Clear selection before graphing to prevent meshes from being added to the graph
        prev_sel = cmds.ls(sl=True) or []
        cmds.select(clear=True)
        
        # Graph with filtering to exclude geometry nodes
        self.graph_material_network(shading_group, hops_up=hops_up, include_downstream=False, filter_geometry=True)
        
        # Restore previous selection after a short delay
        if prev_sel:
            QtCore.QTimer.singleShot(500, lambda: cmds.select(prev_sel, r=True))

    def graph_selected_materials(self):
        """Graph all currently selected materials/textures in the node editor."""
        selected = getattr(self, "selected_materials_list", [])
        if not selected:
            cmds.warning("No materials/textures selected to graph.")
            return
        self.graph_materials_batch(selected)
    
    def graph_materials_batch(self, materials):
        """Graph all specified materials/textures in the node editor (batch operation)."""
        if not materials:
            cmds.warning("No materials/textures to graph.")
            return
        
        print(f"[QM][GraphBatch] Graphing {len(materials)} items")
        
        # Collect all nodes to graph
        all_nodes = []
        all_sgs = []
        
        for material in materials:
            # Verify the node exists
            if not cmds.objExists(material):
                print(f"[QM][GraphBatch] Warning: Node '{material}' does not exist, skipping.")
                continue
            
            # Add the material itself
            all_nodes.append(material)
            
            # Check if this is a texture node (textures don't have shading groups)
            try:
                is_texture = False
                node_type = cmds.nodeType(material)
                if node_type == 'file':
                    is_texture = True
                else:
                    # Check if it's a procedural texture
                    try:
                        node_category = self._classify_node_type(material)
                        is_texture = (node_category in ('file_textures', 'procedural_textures'))
                    except Exception as e:
                        print(f"[QM][GraphBatch] Could not classify node type for {material}: {e}")
                        pass
            except Exception as e:
                print(f"[QM][GraphBatch] Error checking node type for {material}: {e}")
                is_texture = False
            
            # Find shading groups for this material (skip for textures)
            if not is_texture:
                try:
                    raw_sgs = set(cmds.listConnections(material, type='shadingEngine', s=False, d=True) or [])
                    raw_sgs.update(cmds.listConnections(f'{material}.outColor', type='shadingEngine', s=False, d=True) or [])
                    sgs = [sg for sg in raw_sgs if sg not in ('initialShadingGroup', 'initialParticleSE')]
                    all_sgs.extend(sgs)
                except Exception as e:
                    print(f"[QM][GraphBatch] Warning: Could not find shading groups for {material}: {e}")
                    pass
            
            # Walk upstream connections (textures, etc)
            try:
                upstream = []
                visited = {material}
                frontier = [material]
                max_hops = 6
                
                for _ in range(max_hops):
                    nxt = []
                    for n in frontier:
                        try:
                            connections = cmds.listConnections(n, source=True, destination=False) or []
                        except Exception as e:
                            print(f"[QM][GraphBatch] Warning: Could not get connections for {n}: {e}")
                            connections = []
                        for u in connections:
                            if u not in visited and cmds.objExists(u):
                                visited.add(u)
                                upstream.append(u)
                                nxt.append(u)
                    if not nxt:
                        break
                    frontier = nxt
                
                all_nodes.extend(upstream)
            except Exception as e:
                print(f"[QM][GraphBatch] Error walking upstream for {material}: {e}")
                pass
        
        # Remove duplicates while preserving order
        all_nodes = list(dict.fromkeys(all_nodes + all_sgs))
        
        # Open node editor
        try:
            mel.eval('NodeEditorWindow;')
        except Exception:
            pass
        
        # Wait for editor to be ready, then add nodes
        def _add_to_editor():
            try:
                ed = mel.eval('getCurrentNodeEditor;')
                if ed and cmds.control(ed, exists=True):
                    # Frame all first to clear view
                    try:
                        cmds.nodeEditor(ed, e=True, frameAll=True)
                    except Exception as e:
                        print(f"[QM][GraphBatch] Warning: Could not frameAll: {e}")
                    
                    # Add all nodes
                    added_count = 0
                    for node in all_nodes:
                        try:
                            if cmds.objExists(node):
                                cmds.nodeEditor(ed, e=True, addNode=node)
                                added_count += 1
                            else:
                                print(f"[QM][GraphBatch] Warning: Node '{node}' no longer exists, skipping.")
                        except Exception as e:
                            print(f"[QM][GraphBatch] Warning: Could not add node '{node}': {e}")
                    
                    # Select and frame
                    try:
                        cmds.nodeEditor(ed, e=True, clearSelection=True)
                        selected_count = 0
                        for node in all_nodes:
                            try:
                                if cmds.objExists(node):
                                    cmds.nodeEditor(ed, e=True, selectNode=node)
                                    selected_count += 1
                            except Exception as e:
                                print(f"[QM][GraphBatch] Warning: Could not select node '{node}': {e}")
                        cmds.nodeEditor(ed, e=True, frameSelected=True)
                        print(f"[QM][GraphBatch] Selected {selected_count} nodes in editor")
                    except Exception as e:
                        print(f"[QM][GraphBatch] Warning: Selection/framing failed: {e}")
                        try:
                            cmds.nodeEditor(ed, e=True, frameAll=True)
                        except Exception:
                            pass
                    
                    print(f"[QM][GraphBatch] Successfully graphed {added_count}/{len(all_nodes)} nodes from {len(materials)} items")
                else:
                    print(f"[QM][GraphBatch] Error: Node editor not ready (ed={ed})")
            except Exception as e:
                cmds.warning(f"[QM][GraphBatch] Failed to graph items: {e}")
                import traceback
                print(f"[QM][GraphBatch] Traceback: {traceback.format_exc()}")
        
        # Delay to let editor open
        QtCore.QTimer.singleShot(200, _add_to_editor)

    # Master toggle to show/hide all action-button rows under each entry.
    # toggle_material_list_buttons_checkbox removed - list buttons are no longer used

    def toggle_material_list_options(self, checked):
        """
        Toggle the visibility of the material list options panel.
        """
        # Find the options frame/layout
        options_frame = self.findChild(QtWidgets.QWidget, 'materialListSettingsFrame')
        if options_frame:
            options_frame.setVisible(checked)
            # Refresh minimum size and snap to it to account for filters frame visibility
            QtCore.QTimer.singleShot(0, self.snap_to_minimum)

    def toggle_material_filters(self, checked):
        """
        Toggle the visibility of the material list filters panel.
        """
        filters_frame = self.findChild(QtWidgets.QWidget, 'materialListFiltersFrame')
        if filters_frame:
            filters_frame.setVisible(checked)
            QtCore.QTimer.singleShot(0, self.snap_to_minimum)

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

    def _get_all_shapes_from_selection(self):
        """
        Get all shapes from the current selection, traversing hierarchy to include shapes within groups.
        Returns a list of shape nodes (full paths), including geometry shapes and light shapes.
        """
        selected_objects = cmds.ls(sl=True, l=True) or []
        if not selected_objects:
            return []
        
        all_shapes = []
        processed = set()  # Avoid duplicates
        
        # Light types that should be included
        light_types = ['ambientLight', 'areaLight', 'directionalLight', 'pointLight', 'spotLight', 
                      'volumeLight', 'aiSkyDomeLight', 'aiPhysicalSky', 'aiLightPortal', 
                      'aiMeshLight', 'aiPhotometricLight']
        
        def get_shapes_recursive(obj):
            """Recursively get all shapes from an object and its children."""
            if obj in processed:
                return
            processed.add(obj)
            
            try:
                # Check if this object is already a shape (geometry or light)
                node_type = cmds.nodeType(obj)
                if node_type in ['mesh', 'nurbsSurface', 'nurbsCurve', 'subdiv'] or node_type in light_types:
                    all_shapes.append(obj)
                    return
                
                # Get direct child shapes (geometry and lights)
                # First get geometry shapes
                geo_shapes = cmds.listRelatives(obj, s=True, f=True, type=['mesh', 'nurbsSurface', 'nurbsCurve', 'subdiv']) or []
                for shape in geo_shapes:
                    if shape not in processed:
                        all_shapes.append(shape)
                        processed.add(shape)
                
                # Get light shapes
                light_shapes = cmds.listRelatives(obj, s=True, f=True, type=light_types) or []
                for shape in light_shapes:
                    if shape not in processed:
                        all_shapes.append(shape)
                        processed.add(shape)
                
                # Get all child transforms/groups and recurse
                children = cmds.listRelatives(obj, c=True, f=True, type='transform') or []
                for child in children:
                    get_shapes_recursive(child)
                    
            except Exception:
                pass
        
        # Process each selected object
        for obj in selected_objects:
            get_shapes_recursive(obj)
        
        return all_shapes

    # True if the material is assigned to any of the selected shapes.
    def _get_materials_from_selection(self):
        """Get all materials, textures, and shading groups from currently selected objects or nodes."""
        selected_objects = cmds.ls(sl=True, l=True) or []
        if not selected_objects:
            return set()
        
        result = set()
        
        # Get all shapes from selection (traversing hierarchy to include shapes within groups)
        all_shapes = self._get_all_shapes_from_selection()
        
        # Get materials/textures/shading groups from selected objects
        for obj in selected_objects:
            # Check if the selected object itself is a material, texture, or shading group
            try:
                if cmds.ls(obj, materials=True):
                    result.add(obj)
                    continue
                # Check if it's a texture node
                if self._is_texture_node(obj):
                    result.add(obj)
                    continue
                # Check if it's a shading group (shadingEngine)
                if cmds.nodeType(obj) == 'shadingEngine':
                    result.add(obj)
                    continue
                if self._is_utility_node(obj):
                    result.add(obj)
                    continue
            except Exception:
                pass
        
        # Process all shapes found (including those from within groups)
        for shape in all_shapes:
            # Check if this is a light shape
            try:
                light_types = ['ambientLight', 'areaLight', 'directionalLight', 'pointLight', 'spotLight', 
                              'volumeLight', 'aiSkyDomeLight', 'aiPhysicalSky', 'aiLightPortal', 
                              'aiMeshLight', 'aiPhotometricLight']
                if cmds.nodeType(shape) in light_types:
                    # This is a light - check for textures connected to it
                    # Get all connections to the light
                    light_connections = cmds.listConnections(shape, source=True, destination=False) or []
                    for conn_node in light_connections:
                        # Check if any connection leads to a texture
                        if self._is_texture_node(conn_node):
                            result.add(conn_node)
                        elif self._is_utility_node(conn_node):
                            result.add(conn_node)
                        # Also check upstream nodes (textures through color correct, etc.)
                        upstream_nodes = cmds.listHistory(shape, allConnections=True, allFuture=False, interestLevel=0) or []
                        for upstream in upstream_nodes:
                            if self._is_texture_node(upstream):
                                result.add(upstream)
                            elif self._is_utility_node(upstream):
                                result.add(upstream)
            except Exception:
                pass
            
            # Find shading engines connected to this shape
            try:
                sgs = cmds.listConnections(shape, type="shadingEngine") or []
                for sg in sgs:
                    # Add the shading group itself
                    result.add(sg)
                    
                    # Find materials connected to this shading engine
                    surface_shaders = cmds.listConnections(f"{sg}.surfaceShader", source=True) or []
                    materials = cmds.ls(surface_shaders, materials=True) or []
                    result.update(materials)
                    
                    # For each material, find all connected textures
                    for mat in materials:
                        # Get all texture connections (going upstream from the material)
                        all_upstream = cmds.listHistory(mat, allConnections=True, allFuture=False, interestLevel=0) or []
                        for node in all_upstream:
                            if self._is_texture_node(node):
                                result.add(node)
                            elif self._is_utility_node(node):
                                result.add(node)
                    
                    # Check for displacement shader connected to the shading group
                    displacement_shaders = cmds.listConnections(f"{sg}.displacementShader", source=True) or []
                    for disp_shader in displacement_shaders:
                        # Add the displacement shader itself (if it's a material/shader)
                        if cmds.ls(disp_shader, materials=True):
                            result.add(disp_shader)
                        # Also check if it's a texture node
                        if self._is_texture_node(disp_shader):
                            result.add(disp_shader)
                        # Get all upstream nodes from the displacement shader (textures, etc.)
                        all_upstream = cmds.listHistory(disp_shader, allConnections=True, allFuture=False, interestLevel=0) or []
                        for node in all_upstream:
                            if self._is_texture_node(node):
                                result.add(node)
                            elif self._is_utility_node(node):
                                result.add(node)
                            # Also add any materials in the upstream chain
                            if cmds.ls(node, materials=True):
                                result.add(node)
                    
                    # Check for volume shader connected to the shading group
                    volume_shaders = cmds.listConnections(f"{sg}.volumeShader", source=True) or []
                    for vol_shader in volume_shaders:
                        # Add the volume shader itself (if it's a material/shader)
                        if cmds.ls(vol_shader, materials=True):
                            result.add(vol_shader)
                        # Also check if it's a texture node
                        if self._is_texture_node(vol_shader):
                            result.add(vol_shader)
                        # Get all upstream nodes from the volume shader (textures, etc.)
                        all_upstream = cmds.listHistory(vol_shader, allConnections=True, allFuture=False, interestLevel=0) or []
                        for node in all_upstream:
                            if self._is_texture_node(node):
                                result.add(node)
                            elif self._is_utility_node(node):
                                result.add(node)
                            # Also add any materials in the upstream chain
                            if cmds.ls(node, materials=True):
                                result.add(node)
            except Exception:
                pass
        
        return result

    def _get_texture_nodes(self):
        """Get all texture nodes in the scene (file, procedural, etc.) - same as Hypershade Textures tab."""
        all_textures = []
        
        try:
            # Get all texture types that Maya recognizes (2D, 3D, environment, utilities)
            # This matches what Hypershade shows in the "Textures" tab
            texture_classifications = [
                'texture/2d',      # 2D textures (file, checker, ramp, fractal, etc.)
                'texture/3d',      # 3D textures (solidFractal, marble, wood, etc.)
                'texture/env',     # Environment textures (envSphere, envCube)
                'texture/other',   # Other texture utilities
                'imageplane',      # Image planes
            ]
            
            for classification in texture_classifications:
                try:
                    # Get all node types in this classification
                    node_types = cmds.listNodeTypes(classification) or []
                    
                    # Get all instances of each type
                    for node_type in node_types:
                        try:
                            nodes = cmds.ls(type=node_type) or []
                            all_textures.extend(nodes)
                        except Exception:
                            pass
                except Exception:
                    pass
            
            # Remove duplicates while preserving order
            seen = set()
            unique_textures = []
            for tex in all_textures:
                if tex not in seen:
                    seen.add(tex)
                    unique_textures.append(tex)
            
            return unique_textures
            
        except Exception:
            # Fallback to common types if classification query fails
            texture_types = ['file', 'checker', 'ramp', 'fractal', 'noise', 'grid', 
                           'bulge', 'cloth', 'solidFractal', 'marble', 'wood', 
                           'aiImage', 'psdFileTex']
            all_textures = []
            for tex_type in texture_types:
                try:
                    nodes = cmds.ls(type=tex_type) or []
                    all_textures.extend(nodes)
                except Exception:
                    pass
            return all_textures

    def _get_utility_nodes(self, connected_to_materials_only=False):
        """
        Return utility nodes in the scene that are connected to shaders at any depth.
        Only shows utilities that are part of shader networks (this is a material tool after all).
        
        Args:
            connected_to_materials_only: Deprecated - always True now. Kept for compatibility.
        """
        # Get all utilities connected to shaders (traverses networks to find all connected utilities)
        utilities_connected_to_shaders = self._get_utilities_connected_to_shaders()
        
        # Return as sorted list for consistency
        return sorted(list(utilities_connected_to_shaders))
    
    def _get_all_shaders_in_scene(self):
        """
        Get all shader (material) nodes in the scene.
        Returns a set of shader node names.
        """
        shaders = set()
        try:
            # Get all materials using Maya's material classification
            materials = cmds.ls(materials=True) or []
            shaders.update(materials)
        except Exception:
            pass
        return shaders
    
    def _traverse_shader_network_for_utilities(self, shader_node, visited=None, utilities_found=None):
        """
        Recursively traverse a shader network to find all utility nodes connected at any depth.
        
        Args:
            shader_node: The shader node to start traversal from
            visited: Set of nodes already visited (to avoid cycles)
            utilities_found: Set to accumulate utility nodes found
        
        Returns:
            Set of utility node names connected to this shader
        """
        if visited is None:
            visited = set()
        if utilities_found is None:
            utilities_found = set()
        
        if shader_node in visited or not cmds.objExists(shader_node):
            return utilities_found
        
        visited.add(shader_node)
        
        try:
            # Get all input connections (nodes feeding into this shader)
            input_connections = cmds.listConnections(shader_node, source=True, destination=False, skipConversionNodes=True) or []
            
            for connected_node in input_connections:
                if connected_node in visited:
                    continue
                
                # Check if this node is a utility
                if self._is_utility_node(connected_node):
                    utilities_found.add(connected_node)
                    # Continue traversing from this utility to find utilities connected to utilities
                    self._traverse_shader_network_for_utilities(connected_node, visited, utilities_found)
                else:
                    # Continue traversing non-utility nodes to find utilities deeper in the network
                    self._traverse_shader_network_for_utilities(connected_node, visited, utilities_found)
        except Exception:
            pass
        
        return utilities_found
    
    def _get_utilities_connected_to_shaders(self):
        """
        Get all utility nodes that are connected to any shader in the scene, at any depth.
        Traverses shader networks to find utilities connected through intermediate nodes.
        
        Returns:
            Set of utility node names connected to shaders
        """
        all_utilities = set()
        shaders = self._get_all_shaders_in_scene()
        
        for shader in shaders:
            try:
                utilities = self._traverse_shader_network_for_utilities(shader)
                all_utilities.update(utilities)
            except Exception:
                continue
        
        return all_utilities
    
    def _traverse_utility_network_for_shaders(self, utility_node, visited=None, shaders_found=None):
        """
        Recursively traverse from a utility node to find all shaders it's connected to at any depth.
        
        Args:
            utility_node: The utility node to start traversal from
            visited: Set of nodes already visited (to avoid cycles)
            shaders_found: Set to accumulate shader nodes found
        
        Returns:
            Set of shader node names connected to this utility
        """
        if visited is None:
            visited = set()
        if shaders_found is None:
            shaders_found = set()
        
        if utility_node in visited or not cmds.objExists(utility_node):
            return shaders_found
        
        visited.add(utility_node)
        
        try:
            # Get all output connections (nodes this utility feeds into)
            output_connections = cmds.listConnections(utility_node, source=False, destination=True, skipConversionNodes=True) or []
            
            for connected_node in output_connections:
                if connected_node in visited:
                    continue
                
                # Check if this node is a shader (material)
                node_type = cmds.nodeType(connected_node)
                if node_type in ('lambert', 'blinn', 'phong', 'phongE', 'anisotropic', 'standardSurface', 
                                'aiStandardSurface', 'aiStandard', 'openPBR_shader'):
                    shaders_found.add(connected_node)
                    # Also check shading groups connected to this shader
                    sgs = self._connected_shading_engines(connected_node)
                    for sg in sgs:
                        if sg not in visited:
                            visited.add(sg)
                elif node_type == 'shadingEngine':
                    # Found a shading group - find its connected shader
                    shader_conns = cmds.listConnections(connected_node, type='lambert', s=True, d=False) or []
                    shader_conns.extend(cmds.listConnections(connected_node, type='blinn', s=True, d=False) or [])
                    shader_conns.extend(cmds.listConnections(connected_node, type='phong', s=True, d=False) or [])
                    shader_conns.extend(cmds.listConnections(connected_node, type='standardSurface', s=True, d=False) or [])
                    shader_conns.extend(cmds.listConnections(connected_node, type='aiStandardSurface', s=True, d=False) or [])
                    shader_conns.extend(cmds.listConnections(connected_node, type='openPBR_shader', s=True, d=False) or [])
                    for shader in shader_conns:
                        shaders_found.add(shader)
                else:
                    # Continue traversing non-shader nodes to find shaders deeper in the network
                    self._traverse_utility_network_for_shaders(connected_node, visited, shaders_found)
        except Exception:
            pass
        
        return shaders_found
    
    def _get_shaders_connected_to_utility(self, utility_node):
        """
        Get all shader nodes that a utility is connected to, at any depth.
        Traverses the network from the utility to find all connected shaders.
        
        Args:
            utility_node: The utility node to start from
        
        Returns:
            Set of shader node names connected to this utility
        """
        if not cmds.objExists(utility_node):
            return set()
        
        return self._traverse_utility_network_for_shaders(utility_node)

    def _node_types_for_classification(self, classification):
        """
        Return a cached set of node types for a given Maya classification string.
        Avoids repeated cmds.listNodeTypes calls, which are relatively slow.
        """
        cached = self._node_types_by_classification.get(classification)
        if cached is not None:
            return cached
        try:
            types_in_class = cmds.listNodeTypes(classification) or []
            cached = set(types_in_class)
        except Exception:
            cached = set()
        self._node_types_by_classification[classification] = cached
        return cached

    def _classify_node_type(self, node):
        """
        Classify a node into one of our NODE_TYPES categories.
        Returns: 'materials', 'file_textures', 'procedural_textures', 'shading_groups', 'utilities', or None
        PERFORMANCE: Cached to avoid repeated Maya API calls.
        """
        # Check cache first
        if node in self._node_type_classification_cache:
            return self._node_type_classification_cache[node]
        
        try:
            node_type = cmds.nodeType(node)
            classification = None
            
            # Check if it's a shading engine (shading group)
            if node_type == 'shadingEngine':
                classification = 'shading_groups'
            # Check if it's a file texture
            elif node_type == 'file':
                classification = 'file_textures'
            # Check if it's a material (shader) - MUST check before utilities to avoid misclassification
            # Materials should never be classified as utilities
            elif cmds.ls(node, materials=True):
                classification = 'materials'
            # Check if it's a procedural texture (using Maya's classifications)
            elif getattr(self, "_procedural_texture_types", None) is None:
                texture_classifications = ['texture/2d', 'texture/3d', 'texture/env', 'texture/other', 'imageplane']
                procedural_types = set()
                for classification in texture_classifications:
                    procedural_types.update(self._node_types_for_classification(classification))
                procedural_types.discard('file')  # ensure file textures stay in their own bucket
                self._procedural_texture_types = procedural_types
                if node_type in self._procedural_texture_types:
                    classification = 'procedural_textures'
            elif node_type in getattr(self, "_procedural_texture_types", set()):
                classification = 'procedural_textures'
            # Check if it's a utility node (using Maya's classification system) - LAST, after materials
            # This includes all math nodes, color utilities, particle utilities, scalar utilities, AOV utilities, and other shader utility nodes
            else:
                if getattr(self, "_utility_node_types_cache", None) is None:
                    # Use Maya's utility classification to get all utility nodes
                    # Match all utility classifications from Hypershade (includes all subcategories)
                    utility_classifications = [
                        'utility/general', 
                        'utility/color', 
                        'utility/math', 
                        'utility/switch',
                        'utility/particle',  # Particle utilities (Arnold nodes, etc.)
                        'utility/scalar',  # Scalar utilities (blendTwoAttr, choice, chooser, etc.)
                        'utility/aov',     # Arnold AOV utilities (aiReadFloat, aiWriteColor, etc.)
                        'utility'  # General utility classification (catches additional nodes)
                    ]
                    utility_types = set()
                    for classification in utility_classifications:
                        utility_types.update(self._node_types_for_classification(classification))
                    # Also include the curated list as a fallback
                    utility_types.update(self._filter_registered_node_types(self.UTILITY_NODE_TYPES))
                    self._utility_node_types_cache = utility_types
                
                if node_type in getattr(self, "_utility_node_types_cache", set()):
                    classification = 'utilities'
            
            # Cache the result
            if classification:
                self._node_type_classification_cache[node] = classification
            return classification
                
        except Exception:
            pass
        return None
    
    def _is_texture_node(self, node):
        """Check if a node is any kind of texture node (file or procedural)."""
        node_type_category = self._classify_node_type(node)
        return node_type_category in ('file_textures', 'procedural_textures')

    def _is_utility_node(self, node):
        """Check if a node is in the utilities category."""
        return self._classify_node_type(node) == 'utilities'
    
    def _is_file_texture(self, node):
        """Check if a node is specifically a file texture."""
        return self._classify_node_type(node) == 'file_textures'
    
    def _is_procedural_texture(self, node):
        """Check if a node is a procedural texture."""
        return self._classify_node_type(node) == 'procedural_textures'
    
    def _is_shading_group(self, node):
        """Check if a node is a shading group."""
        return self._classify_node_type(node) == 'shading_groups'

    def _get_material_shader_type(self, material):
        """
        Return the shader node type for a material (e.g. aiStandardSurface).
        Cached per material to minimize repeated Maya API calls.
        """
        cache = getattr(self, "_material_type_cache", None)
        if cache is None:
            cache = {}
            self._material_type_cache = cache

        if material in cache:
            return cache[material]

        shader_type = None
        try:
            if cmds.objExists(material):
                shader_type = cmds.nodeType(material)
        except Exception:
            shader_type = None

        cache[material] = shader_type
        return shader_type

    def _get_file_texture_display_info(self, file_node):
        """
        Get display information for a file texture.
        Returns dict with: filename, udim_count, colorspace
        PERFORMANCE: Cached to avoid expensive file system operations.
        """
        import time
        
        # Check cache first
        cache_entry = self._file_texture_info_cache.get(file_node)
        current_time = time.time()
        if cache_entry:
            cache_time, cached_info = cache_entry
            if (current_time - cache_time) < self._file_texture_cache_timeout:
                return cached_info
        
        try:
            if cmds.nodeType(file_node) != 'file':
                return None
            
            info = {
                'filename': None,
                'udim_count': 0,
                'colorspace': 'Raw'
            }
            
            # Get file path
            file_path = cmds.getAttr(f"{file_node}.fileTextureName")
            if file_path:
                info['filename'] = os.path.basename(file_path)
            
            # Check for UDIMs
            try:
                use_udim = cmds.getAttr(f"{file_node}.uvTilingMode")
                if use_udim == 3:  # UDIM mode
                    # Count UDIM tiles by checking the directory for matching files
                    if file_path and os.path.exists(file_path):
                        dir_path = os.path.dirname(file_path)
                        base_name = os.path.basename(file_path)
                        
                        # Check if the filename contains a UDIM pattern (1001-1999)
                        import re
                        udim_match = re.search(r'\.(\d{4})\.', base_name)
                        if udim_match:
                            # Extract parts: "texture.1001.exr" -> "texture", "1001", ".exr"
                            udim_num = udim_match.group(1)
                            # Split filename around the UDIM number
                            parts = base_name.split(f'.{udim_num}.')
                            if len(parts) == 2:
                                prefix, ext = parts
                                # Count all files matching: prefix.XXXX.ext (where XXXX is 1001-1999)
                                if os.path.isdir(dir_path):
                                    tiles = []
                                    for f in os.listdir(dir_path):
                                        # Match pattern: prefix.XXXX.ext
                                        match = re.match(rf'^{re.escape(prefix)}\.(\d{{4}})\.{re.escape(ext)}$', f)
                                        if match:
                                            tile_num = int(match.group(1))
                                            if 1001 <= tile_num <= 1999:  # Valid UDIM range
                                                tiles.append(tile_num)
                                    info['udim_count'] = len(tiles)
            except Exception as e:
                print(f"[QM] UDIM detection error: {e}")
                pass
            
            # Get colorspace
            try:
                if cmds.attributeQuery('colorSpace', node=file_node, exists=True):
                    colorspace = cmds.getAttr(f"{file_node}.colorSpace")
                    if colorspace:
                        info['colorspace'] = colorspace
            except Exception:
                pass
            
            # Cache the result
            self._file_texture_info_cache[file_node] = (current_time, info)
            return info
        except Exception:
            return None
    
    def _get_file_texture_colorspace(self, file_node):
        """Get the colorspace of a file texture node."""
        try:
            if cmds.nodeType(file_node) == 'file':
                # Check if colorSpace attribute exists
                if cmds.attributeQuery('colorSpace', node=file_node, exists=True):
                    colorspace = cmds.getAttr(f"{file_node}.colorSpace") or "Raw"
                    return colorspace
        except Exception:
            pass
        return None

    def _set_file_texture_colorspace(self, file_node, colorspace):
        """Set the colorspace of a file texture node and refresh only that entry."""
        try:
            if cmds.nodeType(file_node) == 'file':
                if cmds.attributeQuery('colorSpace', node=file_node, exists=True):
                    cmds.setAttr(f"{file_node}.colorSpace", colorspace, type="string")
                    print(f"[QM] Set colorspace of '{file_node}' to '{colorspace}'")
                    # Refresh only this entry to avoid scrolling to top
                    self._refresh_single_file_texture_entry(file_node, colorspace)
                    return True
        except Exception as e:
            print(f"[QM] Failed to set colorspace: {e}")
        return False
    
    def update_single_material_entry(self, material_name):
        """
        OPTIMIZATION: Update only a single material entry's swatch and properties.
        This avoids a full list rebuild when only one material changes.
        
        Args:
            material_name: Name of the material to update
        """
        try:
            # Debug: print(f"[QM][SWATCH] Updating swatch for material: {material_name}")
            # Find the entry in our registry
            entry_idx = self._index_by_material.get(material_name)
            if entry_idx is None or entry_idx < 0 or entry_idx >= len(self._entry_list):
                # Entry doesn't exist - might need full refresh
                # Debug: print(f"[QM][SWATCH] Material '{material_name}' not found in entry list (idx: {entry_idx}, list_len: {len(self._entry_list)})")
                return False
            
            entry = self._entry_list[entry_idx]
            swatch_icon = entry.get("swatch")
            
            # If swatch not in entry, try to find it in the container's layout
            if not swatch_icon or not hasattr(swatch_icon, 'load_swatch'):
                container = entry.get("container")
                if container:
                    try:
                        layout = container.layout()
                        if layout:
                            # Search for MaterialSwatchIcon in the layout
                            for i in range(layout.count()):
                                item = layout.itemAt(i)
                                if item:
                                    widget = item.widget()
                                    if widget and hasattr(widget, 'load_swatch'):
                                        # Check if this is the right material's swatch
                                        if hasattr(widget, '_actual_material_name') and widget._actual_material_name == material_name:
                                            swatch_icon = widget
                                            # Update entry for next time
                                            entry["swatch"] = swatch_icon
                                            # Debug: print(f"[QM][SWATCH] Found swatch icon in container layout for {material_name}")
                                            break
                    except Exception as e:
                        print(f"[QM][SWATCH] Error searching container for swatch: {e}")
            
            # Update swatch icon if it exists
            if swatch_icon and hasattr(swatch_icon, 'load_swatch'):
                # Invalidate cache for this material to force regeneration
                try:
                    # Use the already-imported material_swatch_icon module
                    if material_swatch_icon:
                        invalidate_swatch_cache = getattr(material_swatch_icon, 'invalidate_swatch_cache', None)
                        if invalidate_swatch_cache:
                            invalidate_swatch_cache(material_name)
                        else:
                            # Fallback: manually clear cache if function doesn't exist
                            if hasattr(material_swatch_icon, '_swatch_cache'):
                                keys_to_remove = [k for k in material_swatch_icon._swatch_cache.keys() if k[0] == material_name]
                                for k in keys_to_remove:
                                    del material_swatch_icon._swatch_cache[k]
                except Exception as e:
                    print(f"[QM][SWATCH] Error invalidating cache: {e}")
                    import traceback
                    traceback.print_exc()
                
                # Reload the swatch
                swatch_icon.load_swatch()
                # Debug: print(f"[QM][SWATCH] Successfully updated swatch for {material_name}")
                return True
            else:
                # Debug: print(f"[QM][SWATCH] No swatch icon found for {material_name} (swatch_icon: {swatch_icon})")
                pass
            
            return False
        except Exception as e:
            print(f"[QM][SWATCH] Error updating single material entry {material_name}: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _queue_material_swatch_update(self, material_name, delay_ms=200):
        """
        OPTIMIZATION: Queue a swatch update for a single material after attribute changes.
        This updates only the swatch, not the entire list.
        
        Args:
            material_name: Name of the material to update
            delay_ms: Debounce delay in milliseconds (default 200ms)
        """
        # Suppress updates while we're doing an in-place rename
        if getattr(self, "_suspend_refresh_count", 0) > 0:
            return
        
        # Initialize per-material update timers if needed
        if not hasattr(self, "_material_swatch_update_timers"):
            self._material_swatch_update_timers = {}
        
        if material_name not in self._material_swatch_update_timers:
            timer = QtCore.QTimer(self)
            timer.setSingleShot(True)
            # Capture material_name in closure to avoid issues
            mat_name = material_name
            timer.timeout.connect(lambda: self.update_single_material_entry(mat_name))
            self._material_swatch_update_timers[material_name] = timer
        
        timer = self._material_swatch_update_timers[material_name]
        timer.stop()
        timer.start(max(0, int(delay_ms)))
        # Debug: print(f"[QM][SWATCH] Queued swatch update for {material_name} (delay: {delay_ms}ms)")
    
    def _register_material_attribute_callback(self, material_name):
        """
        OPTIMIZATION: Register an attribute change callback for a specific material.
        This allows automatic swatch updates when the material's attributes change.
        
        Args:
            material_name: Name of the material to monitor
        """
        # Debug: print(f"[QM][ATTR_CB] Attempting to register callback for: {material_name}")
        try:
            if not cmds.objExists(material_name):
                # Debug: print(f"[QM][ATTR_CB] Material {material_name} does not exist, skipping")
                return
            
            # Check if already registered
            if material_name in getattr(self, "_material_attribute_callbacks", {}):
                # Debug: print(f"[QM][ATTR_CB] Callback already registered for {material_name}, skipping")
                return
            
            # Initialize callbacks dict if needed
            if not hasattr(self, "_material_attribute_callbacks"):
                self._material_attribute_callbacks = {}
            
            self_ref = weakref.ref(self)
            
            # Capture material_name in closure to avoid issues
            mat_name = material_name
            
            # Try OpenMaya v2 first
            try:
                from maya.api import OpenMaya as om
                def _cb(msg, plug, other_plug, client_data):
                    try:
                        inst = self_ref()
                        if not inst:
                            return
                        # Additional check: ensure the instance is still valid
                        if not isValid(inst):
                            return
                        # Check if the instance is being destroyed
                        if getattr(inst, '_destroying', False):
                            return
                    except Exception:
                        return
                    try:
                        # Get the attribute name - try full name first, then partial
                        try:
                            attr_name = plug.partialName(includeNodeName=False, includeNonMandatoryIndices=True, includeInstancedIndices=True, useFullNames=True)
                        except:
                            attr_name = plug.partialName(includeNodeName=False, includeNonMandatoryIndices=True, includeInstancedIndices=True)
                        
                        # Also try to get the full attribute name from the plug
                        try:
                            attr_full = plug.name()
                            # Extract just the attribute part (e.g., "material.baseColor" -> "baseColor")
                            if '.' in attr_full:
                                attr_full = attr_full.split('.')[-1]
                        except:
                            attr_full = attr_name
                        
                        # Use the longer of the two names (full name is usually better)
                        attr_name = attr_full if len(attr_full) > len(attr_name) else attr_name
                        
                        # Debug: print all attribute changes to see what's happening
                        # Debug: print(f"[QM][ATTR_CB] Attribute changed on {mat_name}: '{attr_name}' (msg: {msg})")
                        
                        # Only update swatch for visual attributes that affect the swatch
                        # Include both full names and common short names
                        visual_attrs = [
                            'baseColor', 'bc', 'color', 'diffuseColor', 'albedo',
                            'roughness', 'specularRoughness', 'roughnessX', 'roughnessY', 'r',
                            'metalness', 'metallic', 'm',
                            'emission', 'e', 'emissionColor', 'emissive',
                            'opacity', 'transparency', 'transmission', 'o',
                            'ior', 'specular', 'specularColor', 's',
                            'normalCamera', 'normal', 'bump', 'n',
                            'coat', 'coatColor', 'coatRoughness',
                            'sheen', 'sheenColor', 'sheenRoughness',
                            'subsurface', 'subsurfaceColor', 'subsurfaceRadius',
                            'thinWalled', 'doubleSided'
                        ]
                        
                        # Check if the changed attribute affects visual appearance
                        attr_lower = attr_name.lower()
                        matches = [va for va in visual_attrs if va.lower() == attr_lower or attr_lower.endswith(va.lower()) or attr_lower.startswith(va.lower())]
                        if matches:
                            # Debug: print(f"[QM][ATTR_CB] Visual attribute match: {matches} -> queueing swatch update")
                            # Queue a swatch update for this material
                            inst._queue_material_swatch_update(mat_name, delay_ms=200)
                        else:
                            # Debug: print(f"[QM][ATTR_CB] Non-visual attribute change, skipping swatch update")
                            pass
                    except Exception as e:
                        # Debug: print error to help diagnose issues
                        print(f"[QM][ATTR_CB] Error in attribute callback for {mat_name}: {e}")
                        import traceback
                        traceback.print_exc()
                
                # Get the MObject for this material
                sel = om.MSelectionList()
                sel.add(material_name)
                node = sel.getDependNode(0)
                
                if node:
                    cb_id = om.MNodeMessage.addAttributeChangedCallback(node, _cb, None)
                    if cb_id:
                        self._material_attribute_callbacks[material_name] = cb_id
                        # Debug: print(f"[QM][ATTR_CB] Registered attribute callback for material: {material_name}")
                        return
                    else:
                        print(f"[QM][ATTR_CB] Failed to register callback for {material_name}: returned None")
                else:
                    print(f"[QM][ATTR_CB] Failed to get MObject for {material_name}")
            except Exception as e:
                print(f"[QM][ATTR_CB] OpenMaya v2 registration failed for {material_name}: {e}")
                import traceback
                traceback.print_exc()
            
            # Fallback to OpenMaya v1
            try:
                import maya.OpenMaya as om1
                def _cb(msg, plug, other_plug, client_data):
                    try:
                        inst = self_ref()
                        if not inst:
                            return
                        # Additional check: ensure the instance is still valid
                        if not isValid(inst):
                            return
                        # Check if the instance is being destroyed
                        if getattr(inst, '_destroying', False):
                            return
                    except Exception:
                        return
                    try:
                        # Get the attribute name - try full name first, then partial
                        try:
                            attr_name = plug.partialName(includeNodeName=False, includeNonMandatoryIndices=True, includeInstancedIndices=True, useFullNames=True)
                        except:
                            attr_name = plug.partialName(includeNodeName=False, includeNonMandatoryIndices=True, includeInstancedIndices=True)
                        
                        # Also try to get the full attribute name from the plug
                        try:
                            attr_full = plug.name()
                            # Extract just the attribute part (e.g., "material.baseColor" -> "baseColor")
                            if '.' in attr_full:
                                attr_full = attr_full.split('.')[-1]
                        except:
                            attr_full = attr_name
                        
                        # Use the longer of the two names (full name is usually better)
                        attr_name = attr_full if len(attr_full) > len(attr_name) else attr_name
                        
                        # Debug: print all attribute changes to see what's happening
                        # Debug: print(f"[QM][ATTR_CB] [v1] Attribute changed on {mat_name}: '{attr_name}' (msg: {msg})")
                        
                        visual_attrs = [
                            'baseColor', 'bc', 'color', 'diffuseColor', 'albedo',
                            'roughness', 'specularRoughness', 'roughnessX', 'roughnessY', 'r',
                            'metalness', 'metallic', 'm',
                            'emission', 'e', 'emissionColor', 'emissive',
                            'opacity', 'transparency', 'transmission', 'o',
                            'ior', 'specular', 'specularColor', 's',
                            'normalCamera', 'normal', 'bump', 'n',
                            'coat', 'coatColor', 'coatRoughness',
                            'sheen', 'sheenColor', 'sheenRoughness',
                            'subsurface', 'subsurfaceColor', 'subsurfaceRadius',
                            'thinWalled', 'doubleSided'
                        ]
                        attr_lower = attr_name.lower()
                        matches = [va for va in visual_attrs if va.lower() == attr_lower or attr_lower.endswith(va.lower()) or attr_lower.startswith(va.lower())]
                        if matches:
                            # Debug: print(f"[QM][ATTR_CB] [v1] Visual attribute match: {matches} -> queueing swatch update")
                            inst._queue_material_swatch_update(mat_name, delay_ms=200)
                        else:
                            # Debug: print(f"[QM][ATTR_CB] [v1] Non-visual attribute change, skipping swatch update")
                            pass
                    except Exception as e:
                        print(f"[QM][ATTR_CB] [v1] Error in v1 attribute callback for {mat_name}: {e}")
                        import traceback
                        traceback.print_exc()
                
                sel = om1.MSelectionList()
                sel.add(material_name)
                node = sel.getDependNode(0)
                if node:
                    cb_id = om1.MNodeMessage.addAttributeChangedCallback(node, _cb, None)
                    if cb_id:
                        self._material_attribute_callbacks[material_name] = cb_id
                        # Debug: print(f"[QM][ATTR_CB] ✓ Registered v1 attribute callback for material: {material_name} (cb_id: {cb_id})")
                    else:
                        print(f"[QM][ATTR_CB] ✗ Failed to register v1 callback for {material_name}: returned None")
                else:
                    print(f"[QM][ATTR_CB] ✗ Failed to get v1 MObject for {material_name}")
            except Exception as e:
                print(f"[QM][ATTR_CB] OpenMaya v1 registration failed for {material_name}: {e}")
                import traceback
                traceback.print_exc()
        except Exception as e:
            print(f"[QM][ATTR_CB] Failed to register attribute callback for {material_name}: {e}")
            import traceback
            traceback.print_exc()
    
    def _refresh_single_file_texture_entry(self, file_node, new_colorspace):
        """
        Refresh only a single file texture entry's display text to show updated colorspace.
        This avoids a full list rebuild which would scroll to top.
        """
        try:
            # Invalidate cache for this file texture to force fresh data
            if hasattr(self, '_file_texture_info_cache') and file_node in self._file_texture_info_cache:
                del self._file_texture_info_cache[file_node]
            
            # Find the entry in our registry
            entry_idx = self._index_by_material.get(file_node)
            if entry_idx is None or entry_idx < 0 or entry_idx >= len(self._entry_list):
                print(f"[QM] File texture '{file_node}' not found in entry list")
                return
            
            entry = self._entry_list[entry_idx]
            line_edit = entry.get("line_edit")
            
            # Check if widget is still valid
            try:
                from shiboken6 import isValid as _is_valid
            except Exception:
                try:
                    from shiboken2 import isValid as _is_valid
                except Exception:
                    _is_valid = lambda obj: bool(obj)
            
            if not line_edit or not _is_valid(line_edit):
                print(f"[QM] Line edit widget for '{file_node}' is invalid")
                return
            
            # Check if this is a QLabel (TextureDisplayLabel) with rich text
            if isinstance(line_edit, QtWidgets.QLabel):
                # Rebuild the HTML display text with new colorspace
                info = self._get_file_texture_display_info(file_node)
                if info and info['filename']:
                    display_text = f'<span style="color: #e0e0e0;">{info["filename"]}</span>'
                    
                    # Add UDIM count if applicable (in blue)
                    if info['udim_count'] > 1:
                        display_text += f'  <span style="color: #6fa3d8;">({info["udim_count"]} tiles)</span>'
                    
                    # Add colorspace in brackets (in grey) - use new_colorspace directly
                    if new_colorspace:
                        display_text += f'  <span style="color: #999999;">({new_colorspace})</span>'
                    
                    # Update the label text
                    line_edit.setText(display_text)
                    
                    # Force immediate update
                    line_edit.update()
                    line_edit.repaint()
                    
                    # Also update parent container if it exists
                    container = entry.get("container")
                    if container and _is_valid(container):
                        container.update()
                        container.repaint()
                    
                    print(f"[QM] Refreshed display for '{file_node}' with colorspace '{new_colorspace}'")
            else:
                print(f"[QM] Line edit for '{file_node}' is not a QLabel (type: {type(line_edit)})")
        except Exception as e:
            print(f"[QM] Failed to refresh single entry: {e}")
            import traceback
            traceback.print_exc()

    def open_file_texture_folder(self, file_node):
        """Open the folder containing the file texture in the system file browser."""
        try:
            if cmds.nodeType(file_node) == 'file':
                file_path = cmds.getAttr(f"{file_node}.fileTextureName")
                if file_path and os.path.exists(file_path):
                    folder_path = os.path.dirname(file_path)
                    # Open folder in system file browser
                    if sys.platform == 'win32':
                        os.startfile(folder_path)
                    elif sys.platform == 'darwin':  # macOS
                        os.system(f'open "{folder_path}"')
                    else:  # Linux
                        os.system(f'xdg-open "{folder_path}"')
                    print(f"[QM] Opened folder: {folder_path}")
                else:
                    cmds.warning(f"File path not found or doesn't exist for {file_node}")
        except Exception as e:
            cmds.warning(f"Failed to open folder: {e}")

    def show_colorspace_menu(self, file_node, button):
        """Show a context menu to change the colorspace of a file texture."""
        try:
            # Check if button is still valid before using it
            if not button or not isValid(button):
                return
            
            # Check if UI is still alive
            if not self._is_ui_alive():
                return
            
            if cmds.nodeType(file_node) != 'file':
                return
            
            current_colorspace = self._get_file_texture_colorspace(file_node)
            
            # Create menu with button as parent (already validated above)
            menu = QtWidgets.QMenu(button)
            menu.setStyleSheet(context_menu_style)
            
            # Common colorspaces
            colorspaces = ['sRGB', 'Raw', 'ACEScg']
            
            for cs in colorspaces:
                action = menu.addAction(cs)
                if cs == current_colorspace:
                    action.setCheckable(True)
                    action.setChecked(True)
                action.triggered.connect(lambda checked=False, colorspace=cs: self._set_file_texture_colorspace(file_node, colorspace))
            
            # Show menu at button position
            menu.exec_(button.mapToGlobal(button.rect().bottomLeft()))
            
        except (RuntimeError, AttributeError) as e:
            # Widget was deleted - silently ignore
            pass
        except Exception as e:
            print(f"[QM] Failed to show colorspace menu: {e}")

    def _material_affects_any_of_selection(self, material, sel_shapes):
        """True if the material is assigned to any of the selected shapes."""
        if not sel_shapes:
            return False
        
        # Use the more direct approach: get all materials from selection and check if our material is in that set
        materials_from_selection = self._get_materials_from_selection()
        return material in materials_from_selection

    # -------------------------------
    # 9) Utilities / Qt Guards / Misc
    # -------------------------------

    # Safe widget fetch by objectName, repairing stale self.ui_elements entries.
    def _get_widget(self, name, cls=QtWidgets.QWidget):
        """Return a fresh, valid widget by objectName; repairs stale ui_elements entries."""
        try:
            # shiboken.isValid is the safest check across PySide2/6
            from shiboken2 import isValid as _is_valid
        except Exception:
            try:
                from shiboken6 import isValid as _is_valid
            except Exception:
                _is_valid = lambda obj: bool(obj)
        
        # First check if self is still valid (may be deleted in callbacks)
        # Wrap in try/except because accessing deleted objects can throw RuntimeError
        try:
            # Try to access a simple attribute to check if self is alive
            _ = self.ui_elements
            if not _is_valid(self):
                return None
        except (RuntimeError, AttributeError):
            # self has been deleted or is invalid
            return None
        
        try:
            w = self.ui_elements.get(name)
            if not (w and _is_valid(w)):
                try:
                    w = self.findChild(cls, name)
                    if w and _is_valid(w):
                        self.ui_elements[name] = w
                    else:
                        return None
                except RuntimeError:
                    # self was deleted during the call
                    return None
            return w
        except (RuntimeError, AttributeError):
            # self was deleted during execution
            return None

    # True if this QWidget is still valid (guards timers/scriptJobs).
    def _is_ui_alive(self):
        """True if 'self' QWidget is still valid (guards against stale callbacks)."""
        # First check if we can even access self attributes
        try:
            _ = self.ui_elements
        except (RuntimeError, AttributeError):
            return False
        
        try:
            # Try to get isValid function
            from shiboken2 import isValid as _is_valid
        except Exception:
            try:
                from shiboken6 import isValid as _is_valid
            except Exception:
                _is_valid = lambda obj: bool(obj)
        
        try:
            is_valid = _is_valid(self)
            has_parent = getattr(self, "parent", None) is not None
            return is_valid and has_parent
        except (RuntimeError, AttributeError):
            # self has been deleted
            return False

    # Legacy checkbox callback → maintain selected_materials_list and update delete label.
    def toggle_material_from_checkbox(self, state, material):
        """
        Toggle the material in the class's selection list based on checkbox state.

        Args:
            state (int): State of the checkbox (checked or unchecked).
            material (str): The name of the material associated with the checkbox.
        """
        selected_materials_list = self.selected_materials_list
        deleteSelectedButton = self.ui_elements.get('deleteSelectedButton')

        if state == QtCore.Qt.Checked:
            if material not in selected_materials_list:
                selected_materials_list.append(material)
        else:
            if material in selected_materials_list:
                selected_materials_list.remove(material)

        # Update the delete button text with the count of selected materials
        if deleteSelectedButton:
            deleteSelectedButton.setText(f"Delete Selected ({len(selected_materials_list)} items)")

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
        deleteSelectedButton = self.ui_elements.get('deleteSelectedButton')
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

        # If the user confirms, delete the selected materials in bulk
        if confirmation == QtWidgets.QMessageBox.Ok:
            cmds.undoInfo(openChunk=True)
            try:
                # Filter out materials that don't exist
                existing_materials = [mat for mat in selected_materials_list if cmds.objExists(mat)]
                
                if existing_materials:
                    # Store list of materials to be deleted for debug message
                    materials_deleted = existing_materials.copy()
                    
                    # Delete all materials in bulk (much faster than one by one)
                    cmds.delete(existing_materials)
                    self._invalidate_material_cache()  # Clear cache since we deleted materials
                    
                    # Show debug message with deleted materials
                    material_count = len(materials_deleted)
                    print(f"[DEBUG] Materials deleted count: {material_count}")
                    print(f"[DEBUG] Materials deleted: {materials_deleted}")
                    
                    # Show success message with material count (matching creation format)
                    if material_count == 1:
                        cmds.inViewMessage(amg="<hl>✔ 1 material deleted, check log for details</hl>", pos="topCenter", fade=True)
                    else:
                        cmds.inViewMessage(amg=f"<hl>✔ {material_count} materials deleted, check log for details</hl>", pos="topCenter", fade=True)
                else:
                    cmds.warning("No existing materials found to delete.")
            except Exception as e:
                cmds.warning(f"Failed to delete materials: {e}")
            finally:
                cmds.undoInfo(closeChunk=True)

            # Clear the selected materials list and update the UI
            self.selected_materials_list = []
            if deleteSelectedButton:
                deleteSelectedButton.setText("Delete Selected (0 items)")
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
            # Hide "no results" message when search is cleared
            self._hide_search_no_results_message()
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
