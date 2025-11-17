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


# Import Material manager
import QuickMaterials.material_manager
importlib.reload(QuickMaterials.material_manager)

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


class TextureIcon(QtWidgets.QLabel):
    """Simple black and white checker icon for file textures."""
    
    def __init__(self, texture_name, icon_size=14, parent=None):
        """Create a checker pattern icon for a file texture.
        
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
        
        # Create checker pattern pixmap
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
    
    def _create_checker_pattern(self, size):
        """Create a black and white checker pattern pixmap."""
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
        """Override paintEvent to draw the checker pattern as a square."""
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
    """Icon that renders a sample of a procedural texture."""
    
    def __init__(self, texture_name, icon_size=14, parent=None):
        """Create a procedural texture preview icon.
        
        Args:
            texture_name: Name of the procedural texture node
            icon_size: Size of the icon in pixels (default 14, matching file texture icon size)
            parent: Parent widget
        """
        super(ProceduralTextureIcon, self).__init__(parent)
        self.texture_name = texture_name
        self.icon_size = icon_size
        
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
        
        # Texture preview pixmap (will be loaded asynchronously)
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
        
        # Selection handler support (for clicking to select texture)
        self._owner_ref = None
        self._handler_name = None
        self._bound_handler = None
        self._qm_material_name = texture_name
        
        # Make it clickable
        self.setCursor(QtCore.Qt.PointingHandCursor)
        
        # Load texture preview asynchronously to avoid blocking UI
        QtCore.QTimer.singleShot(50, self.load_texture_preview)
    
    def load_texture_preview(self):
        """Load and display the procedural texture preview."""
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
        """Override paintEvent to draw the texture preview as a square."""
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

        self._icon_pixmap = self._load_icon_pixmap()
        if self._icon_pixmap and not self._icon_pixmap.isNull():
            self.setPixmap(self._icon_pixmap)
            self._use_fallback = False
        else:
            self._use_fallback = True
            self._fallback_color = QtGui.QColor("#ff9da4")
            self.setStyleSheet("background-color: transparent; border: none;")

    def _load_icon_pixmap(self):
        if not self.node_type:
            return None
        icon_candidates = [
            f":/nodeIcons/{self.node_type}.svg",
            f":/nodeIcons/{self.node_type}.png",
            f":/nodeIcons/{self.node_type}.xpm",
        ]
        for path in icon_candidates:
            pixmap = QtGui.QPixmap(path)
            if pixmap and not pixmap.isNull():
                return pixmap.scaled(
                    self.icon_size,
                    self.icon_size,
                    QtCore.Qt.KeepAspectRatio,
                    QtCore.Qt.SmoothTransformation,
                )
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
        if not self._use_fallback:
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
            print(f"[ShadingGroupIcon] Failed to create blue rounded square: {e}")
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
                print(f"[QM][CTX] {fn_name} failed: {e}")
        
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
                    print(f"[QM][CTX] open viewer (file) failed: {e}")
            act_view.triggered.connect(_open_view_file)
            
            menu.addSeparator()
            
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
                print(f"[QM][CTX] {fn_name} failed: {e}")

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
                    print(f"[QM][CTX] open viewer (file) failed: {e}")
            act_view.triggered.connect(_open_view_file)
            
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
                
        elif not is_texture:
            # Material menu
            act_assign = menu.addAction("Assign")
            act_select = menu.addAction("Select Objs")
            act_graph  = menu.addAction("Graph")
            act_imp_tx = menu.addAction("Imp Tx")

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
                            print(f"[QM][CTX] open viewer (material) failed: {e}")
                    act_view_textures.triggered.connect(_open_view_mat)
            except Exception as e:
                print(f"[QM][CTX] material texture check failed: {e}")

        # Check if multiple materials/textures are selected for batch operations
        selected_mats = getattr(owner, "selected_materials_list", [])
        if len(selected_mats) > 1:
            menu.addSeparator()
            
            # Batch operation: Select objects from all selected materials
            if not is_texture:  # Only for materials
                act_select_all = menu.addAction(f"Select Objs of Selected ({len(selected_mats)})")
                act_select_all.triggered.connect(lambda: _safe_call("highlight_materials_batch", selected_mats))
            
            # Batch operation: Graph all selected materials/textures
            act_graph_all = menu.addAction(f"Graph All Selected ({len(selected_mats)})")
            act_graph_all.triggered.connect(lambda: _safe_call("graph_materials_batch", selected_mats))
            
            # Batch operation: Duplicate all selected materials (materials only)
            if not is_texture:
                act_duplicate_all = menu.addAction(f"Duplicate Selected ({len(selected_mats)})")
                act_duplicate_all.triggered.connect(lambda checked=False: _safe_call("duplicate_selected_materials"))

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
            self.setProperty("qmEditMode", "true")  # Enable edit mode highlighting
            self.style().unpolish(self); self.style().polish(self); self.update()
        else:
            # Exit edit mode (editingFinished will handle rename on focus change)
            print(f"[QM][LineEdit] double-click → lock: {self.text()}")
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




    def focusOutEvent(self, e):
        """Lock again when focus is lost (optional)."""
        if not self.isReadOnly():
            print(f"[QM][LineEdit] focus out → lock: {self.text()}")
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

/* Disable hover highlighting when in edit mode */
QLineEdit[qmEditMode="true"]:hover {
    background-color: #333333 !important;
    border: 1px solid #777777 !important;
}

/* Show text highlighting when in edit mode */
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
   Node Type Styling - File Textures (Yellow), Procedural (Orange), Shading Groups (Blue)
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

/* Procedural Textures - Orange tint */
QLineEdit[nodeType="procedural_texture"] {
    background-color: #4a3a2a;  /* orange tint */
    color: #e0d0c0;
    border: 1px solid #5a4a3a;
    border-radius: 6px;
    padding: 1px 1px;
    min-height: 22px;
    font-weight: bold;
}

QLineEdit[nodeType="procedural_texture"]:hover {
    background-color: #564636;
    color: #ffffff;
}

/* Disable hover highlighting for procedural textures when in edit mode */
QLineEdit[nodeType="procedural_texture"][qmEditMode="true"]:hover {
    background-color: #333333 !important;
    border: 1px solid #777777 !important;
}

QLineEdit[nodeType="procedural_texture"][qmSelected="true"] {
    background-color: #6a4a2a;
    color: #ffffff;
    border: 1px solid #b86a4a;
}

/* Procedural texture selected state must override hover */
QLineEdit[nodeType="procedural_texture"][qmSelected="true"]:hover {
    background-color: #6a4a2a !important;
    color: #ffffff !important;
    border: 1px solid #b86a4a !important;
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
    background-color: #3a4a6a;
    color: #ffffff;
    border: 1px solid #4a6a8a;
}

/* Shading group selected state must override hover */
QLineEdit[nodeType="shading_group"][qmSelected="true"]:hover {
    background-color: #3a4a6a !important;
    color: #ffffff !important;
    border: 1px solid #4a6a8a !important;
}

QLineEdit[nodeType="shading_group"][readOnly="false"][editing="true"] { 
    background-color: #333333;
}

/* Utility Nodes - Light red tint */
QLineEdit[nodeType="utility"],
QLabel[nodeType="utility"] {
    background-color: #4a2a2a;
    color: #ffdede;
    border: 1px solid #5a3a3a;
    border-radius: 6px;
    padding: 1px 1px;
    min-height: 22px;
    font-weight: bold;
}

QLineEdit[nodeType="utility"]:hover,
QLabel[nodeType="utility"]:hover {
    background-color: #563232;
    color: #ffffff;
}

QLineEdit[nodeType="utility"][qmSelected="true"],
QLabel[nodeType="utility"][qmSelected="true"] {
    background-color: #6a3a3a;
    color: #ffffff;
    border: 1px solid #a25858;
}

QLineEdit[nodeType="utility"][qmSelected="true"]:hover,
QLabel[nodeType="utility"][qmSelected="true"]:hover {
    background-color: #6a3a3a !important;
    color: #ffffff !important;
    border: 1px solid #a25858 !important;
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

/* --- OVERRIDE: default materials ignore highlight and focus --- */
QLineEdit[materialType="default"][qmSelected="true"],
QLineEdit[materialType="default"][qmSelected="true"]:focus,
QLineEdit[materialType="default"]:focus {
    color: #aaaaaa;              /* muted grey text */
    background-color: #444444;   /* normal default background */
    border: 1px solid #3d3d3d;   /* same muted border */
}

/* Unused materials/textures - light red highlight (only when checkbox is checked) */
QLineEdit[qmUnused="true"],
QLabel[qmUnused="true"] {
    background-color: #4a3a3a !important;  /* light red tint */
    border: 1px solid #5a4a4a !important;
    color: #ffffff !important;
}

QLineEdit[qmUnused="true"]:hover,
QLabel[qmUnused="true"]:hover {
    background-color: #563a3a !important;
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

/* Override unused styling for default materials */
QLineEdit[materialType="default"][qmUnused="true"] {
    background-color: #444444;
    color: #aaaaaa;
    border: 1px solid #3d3d3d;
}

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
    background-color: #4a3a3a !important;  /* light red tint (overrides orange) */
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
QLabel[materialType="default"] { color: #aaaaaa; }

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
        
        # Test the new arrow_combo_box icon
        probe_combo = QtGui.QIcon(":/icons/arrow_combo_box.png")
        print(f"[QM][IconTest] :/icons/arrow_combo_box.png -> isNull={probe_combo.isNull()}")
    except Exception as e:
        print(f"[QM][IconTest] exception: {e}")

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
        print("[DEBUG] QuickMaterialsSettingsUI.__init__ called")
        
        # Set dialog properties
        self.setWindowTitle("Quick Materials Settings")
        self.setModal(False)  # Non-modal dialog
        # Remove minimum size constraint to allow dialog to shrink
        
        # 1) Load the .ui file
        loader = QtUiTools.QUiLoader()
        script_dir = os.path.dirname(__file__)
        ui_path = os.path.join(script_dir, "QtDesigner", "quickMaterialsSettings.ui")
        print(f"[DEBUG] Loading UI from: {ui_path}")
        
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
        
        print("[DEBUG] QuickMaterialsSettingsUI initialization complete")
    def auto_initialize_ui_elements(self, widget):
        """Recursively find all named widgets and store them in self.ui_elements."""
        if hasattr(widget, 'objectName') and widget.objectName():
            self.ui_elements[widget.objectName()] = widget
            print(f"[DEBUG] Found UI element: {widget.objectName()}")
        
        for child in widget.children():
            if isinstance(child, QtWidgets.QWidget):
                self.auto_initialize_ui_elements(child)

    def setup_connections(self):
        """Connect UI elements to their handlers."""
        print("[DEBUG] Setting up connections for QuickMaterialsSettingsUI")
        
        # Connect save button
        save_btn = self.ui_elements.get("quickMaterialsSaveSettings")
        if save_btn:
            print("[DEBUG] Found quickMaterialsSaveSettings, connecting to _save_settings")
            save_btn.clicked.connect(self._save_settings)
        else:
            print("[DEBUG] quickMaterialsSaveSettings not found")
        
        # Connect close/cancel button if it exists
        close_btn = self.ui_elements.get("quickMaterialsCloseSettings")
        if close_btn:
            print("[DEBUG] Found quickMaterialsCloseSettings, connecting to close")
            close_btn.clicked.connect(self.close)
        else:
            print("[DEBUG] quickMaterialsCloseSettings not found")
            
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
            print("[DEBUG] Found editTextureSearchNamesButton, connecting to open_texture_search_names_ui")
            try:
                names_btn.clicked.disconnect()
            except Exception:
                pass
            names_btn.clicked.connect(self.open_texture_search_names_ui)
        else:
            print("[DEBUG] editTextureSearchNamesButton not found")
        
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
                print(f"[DEBUG] Connected {checkbox_name} to toggle frame visibility")
            else:
                print(f"[DEBUG] Checkbox {checkbox_name} not found in UI elements")

    def _apply_saved_settings(self):
        """Load settings from main quick materials settings JSON and apply to UI."""
        print("[DEBUG] _apply_saved_settings called")
        settings = self._load_settings()
        
        # Apply texture importer settings
        mode = settings.get("default_mode", "maya_file")
        print(f"[DEBUG] Setting default mode to: {mode}")
        
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
                                    print(f"[DEBUG] Loaded {checkbox_name} state: {mc_settings[setting_key]}")
                    # Apply material naming prefix/suffix if present
                    prefix_le = self.ui_elements.get("materialNamingPrefixLineEdit")
                    suffix_le = self.ui_elements.get("materialNamingSuffixLineEdit")
                    if prefix_le:
                        prefix_le.setText(mc_settings.get('name_prefix', 'M_'))
                    if suffix_le:
                        suffix_le.setText(mc_settings.get('name_suffix', ''))
        except Exception as e:
            print(f"[DEBUG] Error loading material creator settings: {e}")

    def _load_settings(self):
        """Load texture importer settings from main quick materials settings JSON."""
        print("[DEBUG] _load_settings called")
        path = os.path.join(os.path.dirname(__file__), "settings", "quick_materials_settings.json")
        try:
            with open(path, "r") as f:
                all_settings = json.load(f)
            if isinstance(all_settings, dict) and 'texture_importer' in all_settings:
                print(f"[DEBUG] Loaded texture importer settings from main settings: {path}")
                return all_settings['texture_importer']
            else:
                print(f"[DEBUG] Main settings JSON missing texture_importer section at {path}; using defaults.")
                return {}
        except FileNotFoundError:
            print(f"[DEBUG] Settings file not found at {path}; creating default settings")
            # Create default settings file
            self._create_default_settings_file(path)
            return {}
        except Exception as e:
            print(f"[DEBUG] Failed to read main settings at {path}: {e}")
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
            print(f"[DEBUG] Created default settings file at: {path}")
        except Exception as e:
            print(f"[DEBUG] Failed to create default settings file: {e}")

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
                        print(f"[DEBUG] Failed to reset defaults for {frame_name}: {exc}")
                # Refresh minimum size and snap to it to account for visibility change
                QtCore.QTimer.singleShot(0, main_ui.snap_to_minimum)
                print(f"[DEBUG] Toggled {frame_name} visibility to {checked} via {checkbox_name}")

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
                    print(f"[DEBUG] Opened folder: {resolved_path}")
                else:
                    # Path doesn't exist - ask if we should create it
                    create_if_not_exists = self.ui_elements.get("createIfDoesntExistCheckbox")
                    if create_if_not_exists and create_if_not_exists.isChecked():
                        try:
                            os.makedirs(resolved_path, exist_ok=True)
                            print(f"[DEBUG] Created folder: {resolved_path}")
                            
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
            print(f"[DEBUG] Error resolving path template '{path_template}': {e}")
            return None

    def _save_settings(self):
        """Save settings to main quick materials settings JSON."""
        print("[DEBUG] _save_settings called")
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
                        print(f"[DEBUG] Saving {setting_key} = {cb.isChecked()}")
            
            # Save material naming prefix/suffix
            try:
                prefix_text = self.ui_elements.get("materialNamingPrefixLineEdit").text().strip() if self.ui_elements.get("materialNamingPrefixLineEdit") else ""
                suffix_text = self.ui_elements.get("materialNamingSuffixLineEdit").text().strip() if self.ui_elements.get("materialNamingSuffixLineEdit") else ""
                all_settings['material_creator']['name_prefix'] = prefix_text
                all_settings['material_creator']['name_suffix'] = suffix_text
                print(f"[DEBUG] Saving name_prefix='{prefix_text}', name_suffix='{suffix_text}'")
            except Exception as _e:
                print(f"[DEBUG] Skipped saving name prefix/suffix: {_e}")
            
            # Save back to file
            with open(settings_path, "w") as f:
                json.dump(all_settings, f, indent=2)
                
            print(f"[DEBUG] Settings saved successfully to: {settings_path}")
            # Show yellow notification instead of dialog
            cmds.inViewMessage(amg="<hl>✔ Quick Materials Settings Saved</hl>", pos="topCenter", fade=True)
            # Close the dialog after saving
            self.accept()
        except Exception as e:
            print(f"[DEBUG] Error saving settings: {e}")
            cmds.confirmDialog(title="Error", message=f"Failed to save settings: {e}", button=["OK"])

    def reload_from_disk(self):
        """Re-read JSON and re-apply to widgets (call before showing the window)."""
        print("[DEBUG] reload_from_disk called")
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
            'header_color': '#ffaa66',  # Orange
            'entry_color': '#4a3a2a',  # Orange tint
            'selected_color': '#6a4a2a',
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
            'header_color': '#ff9da4',  # Light red per request
            'entry_color': '#4a2a2a',
            'selected_color': '#6a3a3a',
            'supports_rename': True,
            'supports_buttons': False,
        },
    }

    # Focused subset of high-value utility node types we always track in the list
    UTILITY_NODE_TYPES = (
        'multiplyDivide',
        'plusMinusAverage',
        'setRange',
        'remapValue',
        'remapColor',
        'remapHsv',
        'remapRgb',
        'remapVector',
        'clamp',
        'condition',
        'reverse',
        'unitConversion',
        'vectorProduct',
        'composeMatrix',
        'decomposeMatrix',
        'distanceBetween',
        'multDoubleLinear',
        'fourByFourMatrix',
        'blendColors',
        'gammaCorrect',
    )

    # --- filters for the material list (id, checkbox objectName, chip label, chip visibility, exclusivity group) ---
    MATERIAL_FILTERS = [
        # Visibility-state group (mutually exclusive across all four)
        {"id": "selected",      "checkbox": "selectedOnlyFilterCheckbox",      "label": "Selected",  "chip": True,  "group": "selected_state"},
        {"id": "nonSelected",   "checkbox": "nonSelectedOnlyFilterCheckbox",   "label": "Non-Selected",   "chip": True,  "group": "selected_state"},
        {"id": "used",          "checkbox": "usedFilterCheckbox",              "label": "Used",           "chip": True,  "group": "used_state"},
        {"id": "unUsed",        "checkbox": "unUsedFilterCheckbox",            "label": "Unused",         "chip": True,  "group": "used_state"},

        # Referenced pair (its own exclusive group)
        {"id": "referenced",    "checkbox": "referencedFilterCheckbox",        "label": "Referenced",     "chip": True,  "group": "reference_state"},
        {"id": "nonReferenced", "checkbox": "nonReferencedFilterCheckbox",     "label": "Non-Referenced", "chip": True,  "group": "reference_state"},

        # Standalone
        {"id": "hideDefaults",          "checkbox": "hideDefaultMaterialsCheckbox",       "label": "Hide Defaults",         "chip": False, "group": None},
        
        # Note: Node type filters (fileTextures, proceduralTextures, shadingGroups, utilities) are now handled
        # by tab buttons (materialListShadersButton, materialListTexturesButton, materialListShadingGroupButton, materialListUtilitiesButton)
        # instead of checkboxes in the options menu.
    ]


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
        
        # Initialize list buttons visibility (needed for state loading)
        self._list_buttons_visible = True
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
        
        # PERFORMANCE OPTIMIZATION: Debounced refresh timer
        self._refresh_timer = QtCore.QTimer()
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._perform_actual_refresh)
        self._refresh_delay_ms = 150  # Refresh after 150ms of inactivity
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._save_ui_state_immediate)
        self._save_delay_ms = 500  # Save after 500ms of inactivity
        self._material_row_pool = []
        self._initial_populate_done = False
        try:
            self.destroyed.connect(self._remove_workspace_state_job)
        except Exception:
            pass
        self._workspace_state_job_id = None
        
        # Set loading flag early to prevent auto-save during initialization
        self._loading_state = True
        
        self.initialize_ui()
        
        # Load UI state after UI is initialized
        print(f"[DEBUG] About to load UI state...")
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

            # Ensure a reasonable initial size when docked/floating
            self.setMinimumWidth(self._minimum_width_baseline)
            self.resize(max(self._minimum_width_baseline, self.width()), self.height())
            self.show()
            # Defer snapping so the workspaceControl has fully realized its layout
            QtCore.QTimer.singleShot(0, self.snap_to_minimum)
            QtCore.QTimer.singleShot(0, self._apply_minimum_width_baseline)


        cmds.workspaceControl(self.workspace_control_name, edit=True, visible=True)
        self._install_workspace_state_job()

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
            try:
                cls.quick_materials_ui_instance._remove_workspace_state_job()
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
        options_frame = self.findChild(QtWidgets.QWidget, 'materialListOptionsFrame')

        if options_frame:
            options_frame.setVisible(False)
        # keep the toggle button untoggled (text handled by Qt Designer)
        options_btn = self.findChild(QtWidgets.QPushButton, 'materialListOptionsButton')
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
            "base_height": 50,  # base min height even if all sections hidden (reduced from 100)
            "sections": {
                "materialCreatorFrame": 140,  # visible => add this many pixels of min height (reduced from 210)
                "materialToolsFrame": 75,
                "materialListFrame": 200,
                "materialListOptionsFrame": 220,
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
        For materialListOptionsFrame, uses actual widget size to account for UI scaling.
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
                    # For materialListOptionsFrame, use actual widget size to account for UI scaling
                    if frame_name == "materialListOptionsFrame":
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
            QtWidgets.QApplication.processEvents(QtCore.QEventLoop.ExcludeUserInputEvents)
            self._debug_print_size("snap_to_minimum -> before refresh")
            
            # 1) Recompute dynamic minimums
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

            # 6) Process a layout pass, then RELEASE all temporary caps next tick
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
                        qt_host.setMaximumHeight(16777215)
                    # Keep our *minimumHeight* (we want the new min to persist),
                    # but release width and height maximums so user can resize.
                    self.setMinimumWidth(original_self_min_w if original_self_min_w else self._minimum_width_baseline)
                    self.setMaximumWidth(original_self_max_w if original_self_max_w else 16777215)
                    self.setMaximumHeight(16777215)
                except Exception:
                    pass
            QtCore.QTimer.singleShot(50, _release_caps)
            target_width_local = current_width
            min_h_local = min_h
            QtCore.QTimer.singleShot(
                60,
                lambda tw=target_width_local, mh=min_h_local: self._enforce_workspace_size(tw, mh),
            )

        # Defer so the visibility/layout changes from toggles have been applied
        QtCore.QTimer.singleShot(0, _apply_resize)

    def _debug_print_size(self, label):
        """Utility debug helper to report current and minimum size."""
        if not getattr(self, "_layout_debug_enabled", False):
            return
        try:
            current = self.size()
            minimum = self.minimumSize()
            pieces = [f"[QM][DEBUG] {label}: size={current.width()}x{current.height()} | min={minimum.width()}x{minimum.height()}"]

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
            print(f"[QM][DEBUG] {label}: failed to capture size -> {exc}")

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
            print(f"[QM][DEBUG] Skipping workspace state scriptJob; event not available: {event_name}")
            self._workspace_state_job_id = None
            return
        try:
            self._workspace_state_job_id = cmds.scriptJob(
                e=(event_name, self._workspace_control_state_changed),
                protected=True,
                parent=wc_name,
            )
        except Exception as exc:
            print(f"[QM][DEBUG] Failed to install workspace state scriptJob: {exc}")
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
        self.refresh_minimum_size()
        size = getattr(self, "_last_minimum_size", self.minimumSize())
        min_w = max(1, size.width())
        min_h = max(1, size.height())
        self._debug_print_size("workspaceControlStateChanged")
        self._enforce_workspace_size(min_w, min_h)

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
        
        # Connect Quick Materials Settings Button
        settings_btn = self.ui_elements.get('quickMaterialsSettingsButton')
        if settings_btn:
            print("[DEBUG] Found quickMaterialsSettingsButton, connecting to open_quick_materials_settings")
            settings_btn.clicked.connect(self.open_quick_materials_settings)
        else:
            print("[DEBUG] quickMaterialsSettingsButton not found in ui_elements")

        # Connect search bar text changes to filter materials
        materialSearchLineEdit = self.ui_elements.get('materialSearchLineEdit')
        if materialSearchLineEdit:
            materialSearchLineEdit.textChanged.connect(self.filter_materials)
            materialSearchLineEdit.textChanged.connect(self._save_ui_state)

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

        # Launch the Material manager tool
        material_manager_btn = self.ui_elements.get('materialManagerButton')
        if material_manager_btn:
            material_manager_btn.clicked.connect(self.open_material_manager)
        else:
            print("Error: materialManagerButton not found.")


        # --- NEW: Toggle all per-material button rows (Assign/Highlight/Select/Graph/Import Tx) ---
        # Replace toggle button with checkbox for material list options
        tlb_checkbox = self.ui_elements.get('toggleListButtonsCheckbox')
        if tlb_checkbox:
            tlb_checkbox.toggled.connect(self.toggle_material_list_buttons_checkbox)
            # Connect to save state when changed
            tlb_checkbox.toggled.connect(self._save_ui_state)
            # Initialize state - default to visible (checked) if not set
            if not hasattr(self, "_list_buttons_visible"):
                self._list_buttons_visible = True
            tlb_checkbox.setChecked(self._list_buttons_visible)

        # --- NEW: Material List Options panel toggle ---
        options_btn = self.ui_elements.get('materialListOptionsButton')
        if options_btn:
            # Make it a checkable toggle button (styling handled by Qt stylesheet)
            options_btn.setCheckable(True)
            
            # Connect to toggle function
            options_btn.toggled.connect(self.toggle_material_list_options)
            # Connect to save state when changed
            options_btn.toggled.connect(self._save_ui_state)

        # Hide Namespaces Checkbox - refresh list when changed
        hide_namespaces_cb = self._get_widget('hideNamespacesCheckbox', QtWidgets.QCheckBox)
        if hide_namespaces_cb:
            hide_namespaces_cb.stateChanged.connect(lambda state: self.refresh_materials_list())
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
        for f in self._filter_spec():
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

        # ---- Wire up tab buttons for material list filtering (Shaders, Textures, Shading Groups, Utilities) ----
        # These buttons act as tabs to filter what's shown in the material list
        tab_button_names = [
            'materialListShadersButton',
            'materialListTexturesButton',
            'materialListShadingGroupButton',
            'materialListUtilitiesButton',
        ]
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
                # Check if frame visibility is overridden by settings
                try:
                    script_dir = os.path.dirname(__file__)
                    settings_path = os.path.join(script_dir, "settings", "quick_materials_settings.json")
                    if os.path.exists(settings_path):
                        with open(settings_path, "r") as f:
                            all_settings = json.load(f)
                            settings_mc = all_settings.get('material_creator', {})
                            setting_key = f"attribute_frame_visible_{name}"
                            if setting_key in settings_mc:
                                final_vis = bool(settings_mc[setting_key])
                                w.setVisible(final_vis)
                                if not final_vis:
                                    self._reset_attribute_to_default(name)
                                return
                except Exception:
                    pass  # Fall through to default behavior
                
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
                print(f"[DEBUG] Failed to reset {spin_name} to default {default_value}: {exc}")

    def _reset_color_controls_to_default(self):
        """Reset color controls to a light grey/white, honoring random hue when enabled."""
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

        default_color = QtGui.QColor("#f2f2f2")
        self.selected_color = default_color

        if hue_slider:
            hue_slider.setValue(0)
        if sat_slider:
            sat_slider.setValue(0)
        if val_slider:
            val_slider.setValue(95)

        if color_button:
            self.update_button_color(color_button, self.selected_color)

        try:
            self.update_saturation_slider_gradient()
        except KeyError:
            pass

    def _fix_horizontal_lines(self):
        """Fix horizontal lines that Maya's stylesheet hides by setting properties explicitly."""
        print("[DEBUG] Starting HLine fix process...")
        
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
                print(f"[DEBUG] Fixing HLine frame: {frame_name}")
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
                    print(f"[DEBUG] Adjusting parent layout spacing for: {frame_name}")
                    parent_layout.setVerticalSpacing(5)  # Match the margin
                
                fixed_count += 1
                print(f"[DEBUG] Applied HLine fix to: {frame_name}")
        
        # Also try to find any QFrame widgets that might be HLines
        try:
            all_frames = self.findChildren(QtWidgets.QFrame)
            for frame in all_frames:
                if frame.frameShape() == QtWidgets.QFrame.HLine:
                    print(f"[DEBUG] Found HLine frame: {frame.objectName()}")
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
                        print(f"[DEBUG] Adjusting parent layout spacing for found frame: {frame.objectName()}")
                        parent_layout.setVerticalSpacing(5)  # Match the margin
                    
                    fixed_count += 1
                    print(f"[DEBUG] Applied HLine fix to found frame: {frame.objectName()}")
        except Exception as e:
            print(f"[DEBUG] Error searching for HLine frames: {e}")
        
        print(f"[DEBUG] Fixed {fixed_count} HLine frames total")
        
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
        print("Starting material creation...")  # Debug

        # Only loads Arnold when standardSurface is selected; otherwise no-op
        if not self.ensure_arnold_plugin():
            print("Failed to load Arnold plugin (needed for standardSurface).")
            return

        # Build selection units: treat selected groups as single units, and selected meshes as their own units
        selection_units = self.get_selection_units()
        if not selection_units:
            cmds.warning("No valid mesh objects selected.")
            return

        is_single_material_for_all = not self.ui_elements.get('materialPerMeshCheckbox').isChecked()
        used_material_names = set()

        # Use the current displayed color for material creation
        color_rgb = self.get_current_color_rgb()

        if is_single_material_for_all:
            # Create one material for all targets with the selected color.
            # If the single selection is a group, prefer the group name for (selection).
            all_meshes = []
            for unit in selection_units:
                all_meshes.extend(unit['meshes'])
            if len(selection_units) == 1:
                selection_label = selection_units[0]['label']
            else:
                # When multiple selections are made, use the first selection unit's label
                # (group name if it's a group, mesh name if it's a mesh)
                if selection_units:
                    selection_label = selection_units[0]['label']
                else:
                    selection_label = "selection"
            material_name = self.generate_material(selection_label, color_rgb, used_material_names)
            if not material_name:
                return

            for mesh in all_meshes:
                self.assign_material_to_mesh(mesh, material_name)
                print(f"Assigned {material_name} to {mesh}")

        else:
            # Create a different material per selection unit (group or mesh).
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
                    return

                for mesh_name in unit['meshes']:
                    self.assign_material_to_mesh(mesh_name, material_name)
                    print(f"Assigned {material_name} to {mesh_name}")

        # Update the color display after creating the material(s)
        self.update_color_display_after_creation()

        # Refresh the materials list and close the undo chunk
        self._invalidate_material_cache()  # Clear cache since we added new materials
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
           - a selected group (transform with descendants) represented as one unit with label=group short name and meshes=all descendant mesh transforms
           - a selected mesh transform not under any selected group, represented with label=its short name and meshes=[itself]

        This enables per-selection creation while treating groups as single selections.
        """
        selected_transforms = cmds.ls(selection=True, objectsOnly=True) or []
        if not selected_transforms:
            return None

        # Identify groups in the raw selection (a group is any transform with children)
        selected_groups = []
        for obj in selected_transforms:
            try:
                if cmds.listRelatives(obj, children=True):
                    selected_groups.append(obj)
            except Exception:
                pass

        # Build a quick lookup of all descendants of selected groups
        descendants_of_groups = set()
        for grp in selected_groups:
            for desc in cmds.listRelatives(grp, ad=True, type='transform') or []:
                descendants_of_groups.add(desc)

        units = []

        # First, add group units (as single units)
        for grp in selected_groups:
            label = grp.split('|')[-1]
            meshes = []
            for desc in cmds.listRelatives(grp, ad=True, type='transform') or []:
                shapes = cmds.listRelatives(desc, shapes=True, fullPath=True) or []
                if any(cmds.nodeType(s) == 'mesh' for s in shapes):
                    meshes.append(desc)
            if meshes:
                units.append({'label': label, 'meshes': sorted(set(meshes))})

        # Next, add individually selected meshes that are NOT under any selected group
        for obj in selected_transforms:
            if obj in descendants_of_groups:
                # It's covered by its group's unit; skip as an individual unit
                continue
            shapes = cmds.listRelatives(obj, shapes=True, fullPath=True) or []
            if any(cmds.nodeType(s) == 'mesh' for s in shapes):
                label = obj.split('|')[-1]
                units.append({'label': label, 'meshes': [obj]})

        if not units:
            return None

        return units

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
            print(f"[DEBUG] Setting attributes for {material_name} ({material_type})")
            print(f"[DEBUG] Color RGB: {color_rgb}")

            if material_type == 'standardSurface':
                cmds.setAttr(f"{material_name}.specularRoughness", r)
                metal_spin = self.ui_elements.get('metalnessSpinBox')
                metal_val = float(metal_spin.value()) if metal_spin else 0.0
                cmds.setAttr(f"{material_name}.metalness", max(0.0, min(1.0, metal_val)))
                
                # Emission - set emission color and weight
                emission_spin = self.ui_elements.get('emissionSpinBox')
                emission_val = float(emission_spin.value()) if emission_spin else 0.0
                print(f"[DEBUG] Emission value: {emission_val}")
                if emission_val > 0:
                    print(f"[DEBUG] Setting emissionColor to: {color_rgb}")
                    cmds.setAttr(f"{material_name}.emissionColor", *color_rgb, type="double3")
                    print(f"[DEBUG] Setting emission to: {emission_val}")
                    cmds.setAttr(f"{material_name}.emission", emission_val)
                
                # Opacity - standardSurface uses opacity as RGB color
                opacity_spin = self.ui_elements.get('opacitySpinBox')
                opacity_val = float(opacity_spin.value()) if opacity_spin else 1.0
                print(f"[DEBUG] Setting opacity to: {opacity_val}")
                cmds.setAttr(f"{material_name}.opacity", opacity_val, opacity_val, opacity_val, type="double3")
                
                # Transmission
                transmission_spin = self.ui_elements.get('transmissionSpinBox')
                transmission_val = float(transmission_spin.value()) if transmission_spin else 0.0
                print(f"[DEBUG] Setting transmission to: {transmission_val} (type: {type(transmission_val)})")
                if not (0.0 <= transmission_val <= 1.0):
                    print(f"[DEBUG] Warning: transmission value {transmission_val} is outside 0.0-1.0 range")
                cmds.setAttr(f"{material_name}.transmission", transmission_val)
                
                # Subsurface - set subsurface weight and color
                subsurface_spin = self.ui_elements.get('subsurfaceSpinBox')
                subsurface_val = float(subsurface_spin.value()) if subsurface_spin else 0.0
                print(f"[DEBUG] Subsurface value: {subsurface_val} (type: {type(subsurface_val)})")
                if subsurface_val > 0:
                    if not (0.0 <= subsurface_val <= 1.0):
                        print(f"[DEBUG] Warning: subsurface value {subsurface_val} is outside 0.0-1.0 range")
                    print(f"[DEBUG] Setting subsurface to: {subsurface_val}")
                    cmds.setAttr(f"{material_name}.subsurface", subsurface_val)
                    print(f"[DEBUG] Setting subsurfaceColor to: {color_rgb}")
                    cmds.setAttr(f"{material_name}.subsurfaceColor", *color_rgb, type="double3")

            elif material_type == 'blinn':
                # Roughness → eccentricity, inverse → specularRollOff
                cmds.setAttr(f"{material_name}.eccentricity", r)
                cmds.setAttr(f"{material_name}.specularRollOff", inv)
                
                # Emission - use incandescence for legacy materials
                emission_spin = self.ui_elements.get('emissionSpinBox')
                emission_val = float(emission_spin.value()) if emission_spin else 0.0
                print(f"[DEBUG] Legacy emission value: {emission_val}")
                if emission_val > 0:
                    print(f"[DEBUG] Setting incandescence to: {color_rgb}")
                    cmds.setAttr(f"{material_name}.incandescence", *color_rgb, type="double3")
                
                # Opacity - reverse value for transparency (opacity 1.0 = transparency 0.0)
                opacity_spin = self.ui_elements.get('opacitySpinBox')
                opacity_val = float(opacity_spin.value()) if opacity_spin else 1.0
                transparency_val = 1.0 - opacity_val
                print(f"[DEBUG] Setting transparency to: {transparency_val}")
                cmds.setAttr(f"{material_name}.transparency", transparency_val, transparency_val, transparency_val, type="double3")

            elif material_type == 'phong':
                # Roughness inverse → shininess (cosinePower) and specularColor intensity
                power = max(2.0, inv * 100.0)  # keep a floor to avoid super-broad lobes
                cmds.setAttr(f"{material_name}.cosinePower", power)
                cmds.setAttr(f"{material_name}.specularColor", inv, inv, inv, type="double3")
                
                # Emission - use incandescence for legacy materials
                emission_spin = self.ui_elements.get('emissionSpinBox')
                emission_val = float(emission_spin.value()) if emission_spin else 0.0
                print(f"[DEBUG] Legacy emission value: {emission_val}")
                if emission_val > 0:
                    print(f"[DEBUG] Setting incandescence to: {color_rgb}")
                    cmds.setAttr(f"{material_name}.incandescence", *color_rgb, type="double3")
                
                # Opacity - reverse value for transparency (opacity 1.0 = transparency 0.0)
                opacity_spin = self.ui_elements.get('opacitySpinBox')
                opacity_val = float(opacity_spin.value()) if opacity_spin else 1.0
                transparency_val = 1.0 - opacity_val
                print(f"[DEBUG] Setting transparency to: {transparency_val}")
                cmds.setAttr(f"{material_name}.transparency", transparency_val, transparency_val, transparency_val, type="double3")

            elif material_type == 'lambert':
                # Emission - use incandescence for legacy materials
                emission_spin = self.ui_elements.get('emissionSpinBox')
                emission_val = float(emission_spin.value()) if emission_spin else 0.0
                print(f"[DEBUG] Legacy emission value: {emission_val}")
                if emission_val > 0:
                    print(f"[DEBUG] Setting incandescence to: {color_rgb}")
                    cmds.setAttr(f"{material_name}.incandescence", *color_rgb, type="double3")
                
                # Opacity - reverse value for transparency (opacity 1.0 = transparency 0.0)
                opacity_spin = self.ui_elements.get('opacitySpinBox')
                opacity_val = float(opacity_spin.value()) if opacity_spin else 1.0
                transparency_val = 1.0 - opacity_val
                print(f"[DEBUG] Setting transparency to: {transparency_val}")
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

        # Apply optional prefix/suffix from settings (Quick Materials Settings → material creator)
        try:
            script_dir = os.path.dirname(__file__)
            settings_path = os.path.join(script_dir, "settings", "quick_materials_settings.json")
            prefix = ""
            suffix = ""
            if os.path.exists(settings_path):
                with open(settings_path, "r") as f:
                    all_settings = json.load(f)
                    mc = all_settings.get('material_creator', {})
                    prefix = mc.get('name_prefix', "") or ""
                    suffix = mc.get('name_suffix', "") or ""
            if prefix:
                base_material_name = f"{prefix}{base_material_name}"
            if suffix:
                base_material_name = f"{base_material_name}{suffix}"
        except Exception:
            pass

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
        print(f"[DEBUG] _load_ui_state called, state_file_path: {self.state_file_path}")
        
        # Loading flag is already set during initialization
        print(f"[DEBUG] Loading flag already set to: {self._loading_state}")
        self._begin_silent_refresh()
        self._initial_populate_done = False
        state = {}
        try:
            if not os.path.exists(self.state_file_path):
                print(f"[DEBUG] State file does not exist: {self.state_file_path}")
                self._loading_state = False
                print(f"[DEBUG] Set loading_state to False (no file)")
            else:
                with open(self.state_file_path, 'r') as f:
                    state = json.load(f)
                
                print(f"[DEBUG] Loaded state from file: {state}")
            
            if 'material_creator' in state:
                mc_state = state['material_creator']
                print(f"[DEBUG] Loading material creator state: {mc_state}")
                
                # Material type
                if 'material_type' in mc_state:
                    combo = self.ui_elements.get('materialTypeComboBox')
                    if combo:
                        combo.setCurrentText(mc_state['material_type'])
                        print(f"[DEBUG] Set material type to: {mc_state['material_type']}")
                
                # Color settings
                if 'color' in mc_state:
                    color = mc_state['color']
                    self.selected_color = QtGui.QColor(color['r'], color['g'], color['b'])
                    color_button = self.ui_elements.get('colorDisplayButton')
                    if color_button:
                        self.update_button_color(color_button, self.selected_color)
                    print(f"[DEBUG] Set color to: {color}")
                
                # Slider values
                for slider_name in ['materialColorHueSlider', 'materialColorSaturationSlider', 'materialColorValueSlider']:
                    if slider_name in mc_state:
                        slider = self.ui_elements.get(slider_name)
                        if slider:
                            slider.setValue(mc_state[slider_name])
                            print(f"[DEBUG] Set {slider_name} to: {mc_state[slider_name]}")
                
                # Spinbox values
                for spin_name in ['roughnessSpinBox', 'metalnessSpinBox', 'emissionSpinBox', 'opacitySpinBox', 'transmissionSpinBox', 'subsurfaceSpinBox']:
                    if spin_name in mc_state:
                        spin = self.ui_elements.get(spin_name)
                        if spin:
                            spin.setValue(mc_state[spin_name])
                            print(f"[DEBUG] Set {spin_name} to: {mc_state[spin_name]}")
                
                # Checkboxes
                for cb_name in ['materialPerMeshCheckbox', 'randomHueCheckbox']:
                    if cb_name in mc_state:
                        cb = self.ui_elements.get(cb_name)
                        if cb:
                            cb.setChecked(mc_state[cb_name])
                            print(f"[DEBUG] Set {cb_name} to: {mc_state[cb_name]}")
                
                # Material naming template
                if 'material_naming_template' in mc_state:
                    line_edit = self.ui_elements.get('materialNamingLineEdit')
                    if line_edit:
                        line_edit.setText(mc_state['material_naming_template'])
                        print(f"[DEBUG] Set material naming template to: {mc_state['material_naming_template']}")
                
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
                                                print(f"[DEBUG] Failed to reset defaults for {frame_name} during load: {exc}")
                                        print(f"[DEBUG] Set {frame_name} visibility from settings: {settings_mc[setting_key]}")
                            
                            # Refresh minimum size and snap after loading attribute frame visibility
                            QtCore.QTimer.singleShot(200, self.snap_to_minimum)
                except Exception as e:
                    print(f"[DEBUG] Error loading attribute frame visibility from settings: {e}")
            
            # Load material list settings
            if 'material_list' in state:
                ml_state = state['material_list']
                
                # Sorting
                if 'sort_mode' in ml_state:
                    self._sort_mode = ml_state['sort_mode']
                if 'sort_desc' in ml_state:
                    self._sort_desc = ml_state['sort_desc']
                
                # Filter checkboxes
                for filter_spec in self.MATERIAL_FILTERS:
                    cb_name = filter_spec['checkbox']
                    if cb_name in ml_state:
                        cb = self.ui_elements.get(cb_name)
                        if cb:
                            cb.setChecked(ml_state[cb_name])
                            print(f"[DEBUG] Set filter {cb_name} to: {ml_state[cb_name]}")
                
                # Node type show checkboxes
                for checkbox_name in ['showTexturesCheckbox', 'showProceduralTexturesCheckbox', 'showShadingGroupsCheckbox']:
                    if checkbox_name in ml_state:
                        cb = self.ui_elements.get(checkbox_name)
                        if cb:
                            cb.setChecked(ml_state[checkbox_name])
                            print(f"[DEBUG] Set {checkbox_name} to: {ml_state[checkbox_name]}")
                
                # Material list option checkboxes
                for checkbox_name in ['hideNamespacesCheckbox', 'highlightUnusedCheckbox', 'showIconsCheckbox']:
                    if checkbox_name in ml_state:
                        cb = self.ui_elements.get(checkbox_name)
                        if cb:
                            cb.setChecked(ml_state[checkbox_name])
                            print(f"[DEBUG] Set {checkbox_name} to: {ml_state[checkbox_name]}")
                
                # Material list options button
                if 'material_list_options_visible' in ml_state:
                    options_btn = self.ui_elements.get('materialListOptionsButton')
                    if options_btn:
                        options_btn.setChecked(ml_state['material_list_options_visible'])
                        print(f"[DEBUG] Set material list options visible to: {ml_state['material_list_options_visible']}")
                if 'material_filters_visible' in ml_state:
                    filters_btn = self.ui_elements.get('materialFiltersButton')
                    if filters_btn:
                        filters_btn.setChecked(ml_state['material_filters_visible'])
                        print(f"[DEBUG] Set material filters visible to: {ml_state['material_filters_visible']}")
                
                # Toggle list buttons checkbox
                if 'list_buttons_visible' in ml_state:
                    self._list_buttons_visible = ml_state['list_buttons_visible']
                    cb = self.ui_elements.get('toggleListButtonsCheckbox')
                    if cb:
                        cb.setChecked(self._list_buttons_visible)
                    print(f"[DEBUG] Set list buttons visible to: {self._list_buttons_visible}")
                
                # Material search text
                if 'material_search_text' in ml_state:
                    search_line = self.ui_elements.get('materialSearchLineEdit')
                    if search_line:
                        search_line.setText(ml_state['material_search_text'])
                        print(f"[DEBUG] Set material search text to: {ml_state['material_search_text']}")
                
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
                        print(f"[DEBUG] Set {panel_name} visibility to: {ml_state[panel_name]}")
            
            # Refresh UI after loading state
            # Don't refresh immediately to prevent auto-save
            # self.populate_materials_scroll_area()
            print(f"[DEBUG] UI state loading completed successfully")
            QtCore.QTimer.singleShot(0, self._apply_minimum_width_baseline)
            QtCore.QTimer.singleShot(0, self.snap_to_minimum)
            
            # Add a small delay to prevent immediate auto-save after loading
            QtCore.QTimer.singleShot(500, lambda: setattr(self, '_loading_state', False))
            
        except Exception as e:
            print(f"Error loading UI state: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self._end_silent_refresh()
            QtCore.QTimer.singleShot(0, self._ensure_initial_populate)
            # Clear loading flag after a delay to allow UI elements to finish setting
            QtCore.QTimer.singleShot(100, lambda: setattr(self, '_loading_state', False))
            print(f"[DEBUG] Will set loading_state to False in 100ms (loading complete)")
    
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
    
    def _save_ui_state_immediate(self):
        """Actually save the UI state to file (called by timer)."""
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
            
            # Save material list settings
            ml_state = state['material_list']
            
            # Sorting
            ml_state['sort_mode'] = self._sort_mode
            ml_state['sort_desc'] = self._sort_desc
            
            # Filter checkboxes
            for filter_spec in self.MATERIAL_FILTERS:
                cb_name = filter_spec['checkbox']
                cb = self.ui_elements.get(cb_name)
                if cb:
                    ml_state[cb_name] = cb.isChecked()
            
            # Node type show checkboxes
            for checkbox_name in ['showTexturesCheckbox', 'showProceduralTexturesCheckbox', 'showShadingGroupsCheckbox']:
                cb = self.ui_elements.get(checkbox_name)
                if cb:
                    ml_state[checkbox_name] = cb.isChecked()
            
            # Material list option checkboxes
            for checkbox_name in ['hideNamespacesCheckbox', 'highlightUnusedCheckbox', 'showShaderSwatchesCheckbox', 'showOtherIconsCheckbox']:
                cb = self.ui_elements.get(checkbox_name)
                if cb:
                    ml_state[checkbox_name] = cb.isChecked()
            
            # Material list options button
            options_btn = self.ui_elements.get('materialListOptionsButton')
            if options_btn:
                ml_state['material_list_options_visible'] = options_btn.isChecked()
            filters_btn = self.ui_elements.get('materialFiltersButton')
            if filters_btn:
                ml_state['material_filters_visible'] = filters_btn.isChecked()
            
            # Toggle list buttons checkbox
            ml_state['list_buttons_visible'] = getattr(self, '_list_buttons_visible', True)
            
            # Material search text
            search_line = self.ui_elements.get('materialSearchLineEdit')
            if search_line:
                ml_state['material_search_text'] = search_line.text()
            
            # Toggle buttons for panels
            for panel_name in ['toggleMaterialCreatorVis', 'toggleMaterialToolsVis', 'toggleMaterialListVis', 'toggleMaterialManagerVis']:
                btn = self.ui_elements.get(panel_name)
                if btn:
                    ml_state[panel_name] = btn.isChecked()
            
            # Save texture importer settings
            ti_state = state['texture_importer']
            # Note: These settings will be populated when the texture importer is used
            # For now, we'll save default values that can be updated by the texture importer
            ti_state['default_mode'] = 'maya_file'
            ti_state['custom_path'] = ''

            # Write to file
            with open(self.state_file_path, 'w') as f:
                json.dump(state, f, indent=2)
            
            # State saved successfully (silent)
                
        except Exception as e:
            # Error saving state (silent)
            pass
        
        # Performance timing (can be enabled for debugging)
        # end_time = time.time()
        # duration = (end_time - start_time) * 1000
        # print(f"[PERF] save_ui_state: {duration:.1f}ms")
    
    def open_quick_materials_settings(self):
        """Open the Quick Materials Settings UI."""
        print("[DEBUG] open_quick_materials_settings called")
        if not hasattr(self, "quick_materials_settings_ui") or self.quick_materials_settings_ui is None:
            print("[DEBUG] Creating new QuickMaterialsSettingsUI instance")
            self.quick_materials_settings_ui = QuickMaterialsSettingsUI(parent=self)
        else:
            print("[DEBUG] Reloading existing QuickMaterialsSettingsUI from disk")
            # Ensure we re-read the latest settings off disk each time we open
            self.quick_materials_settings_ui.reload_from_disk()
        print("[DEBUG] Showing QuickMaterialsSettingsUI as non-modal dialog")
        self.quick_materials_settings_ui.show()
        self.quick_materials_settings_ui.raise_()

    def closeEvent(self, event):
        """Override closeEvent to save state before closing."""
        self._save_ui_state()
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

    def open_material_manager(self):
        """
        Wrapper to open the QuickMaterials.material_manager tool.
        Reloads the module during dev. Material manager uses standalone styling.
        """
        try:
            from QuickMaterials import material_manager as _matconv
            import importlib
            importlib.reload(_matconv)  # nice during iteration; remove if undesired

            # Material manager uses standalone styling, no style argument needed
            _matconv.show()
        except Exception as e:
            import maya.cmds as cmds
            cmds.warning(f"Material manager failed to open: {e}")




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

        # Clear material type cache to ensure shader types are re-queried
        # This is important after material conversion when shader types change
        if hasattr(self, "_material_type_cache"):
            self._material_type_cache.clear()

        start_time = time.perf_counter()
        
        # Reset selection & per-build registries
        old_entries = getattr(self, "_entry_list", [])
        if old_entries:
            pool = getattr(self, "_material_row_pool", None)
            if pool is None:
                pool = []
                self._material_row_pool = pool
            for entry in old_entries:
                container = entry.get("container")
                if container and hasattr(container, "_qm_line_edit"):
                    try:
                        container.hide()
                        container.setParent(None)
                    except Exception:
                        pass
                    pool.append(container)
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
        DEFAULT_SHADING_GROUPS = {'initialShadingGroup', 'initialParticleSE'}

        # All scene materials except hidden ones
        all_materials = [m for m in (cmds.ls(materials=True) or []) if m not in HIDDEN_MATERIALS]
        
        # Collect all nodes - always include materials, textures, and shading groups
        # The tab buttons control what's displayed, not what's collected
        all_nodes = list(all_materials)  # Start with materials
        
        # Always include file textures
        try:
            file_textures = [n for n in cmds.ls(type='file') or [] if n not in HIDDEN_MATERIALS]
            all_nodes.extend(file_textures)
        except Exception:
            pass
        
        # Always include procedural textures
        try:
            all_textures = self._get_texture_nodes()
            # Filter out file textures (they're handled separately)
            procedural_only = [t for t in all_textures if cmds.nodeType(t) != 'file' and t not in HIDDEN_MATERIALS]
            all_nodes.extend(procedural_only)
        except Exception:
            pass
        
        # Always include shading groups
        try:
            # Get all shading engines, exclude default/hidden ones
            shading_engines = cmds.ls(type='shadingEngine') or []
            shading_engines = [sg for sg in shading_engines if sg not in HIDDEN_MATERIALS and sg not in DEFAULT_MATERIALS]
            all_nodes.extend(shading_engines)
        except Exception:
            pass

        # Include curated utility nodes (multiplyDivide, etc.)
        try:
            utility_nodes = self._get_utility_nodes()
            all_nodes.extend(utility_nodes)
        except Exception:
            pass

        # Read live filter flags (Selected / Non-Selected / Referenced / Used / Hide Defaults)
        flags = self._collect_filter_flags()
        # Back-compat: if the checkbox doesn't exist, honor the function argument
        if not self.ui_elements.get('hideDefaultMaterialsCheckbox'):
            flags["hideDefaults"] = bool(hide_defaults)

        # Precompute current selection shapes for selected/non-selected filters
        # Traverse hierarchy to get all shapes from selected objects (including groups)
        current_sel_shapes = self._get_all_shapes_from_selection() or []

        # PERFORMANCE OPTIMIZATION: Batch compute material properties AND colors
        props_start = time.perf_counter()
        material_properties = self._batch_compute_material_properties(all_nodes, current_sel_shapes)
        props_duration_ms = (time.perf_counter() - props_start) * 1000.0
        print(f"[QM][Profile] material_properties duration={props_duration_ms:.3f} ms")

        # If hideDefaults is checked, filter out default shading groups
        if flags.get("hideDefaults", False):
            all_nodes = [n for n in all_nodes if n not in DEFAULT_SHADING_GROUPS]

        # Build list using filters + search
        nodes_to_display = []
        for node in all_nodes:
            if self._passes_filters_optimized(node, flags, search_text, DEFAULT_MATERIALS, material_properties):
                nodes_to_display.append(node)

        # Snapshot current on-screen order so we can preserve it for one rebuild after rename
        prev_order = [e.get("material") for e in getattr(self, "_entry_list", [])] if hasattr(self, "_entry_list") else []

        # PERFORMANCE OPTIMIZATION: Check if we can avoid a full rebuild
        if self._can_optimize_ui_refresh(nodes_to_display, search_text, flags):
            # Try to update existing UI instead of full rebuild
            if self._update_existing_ui(nodes_to_display, search_text, flags):
                return  # Successfully updated existing UI

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

        # Note: Sort bar is installed as sticky header above scroll area (see _install_sort_bar)
        # Starting row for content inside scroll
        row = 0

        # Chips row (only if any filters active)
        consumed = self._add_active_filters_bar(scroll_layout, row)
        row += consumed

        # --- Separate nodes by type ---
        classify_start = time.perf_counter()
        materials_only = []
        file_textures_only = []
        procedural_textures_only = []
        shading_groups_only = []
        utilities_only = []
        
        for item in nodes_to_display:
            node_type_category = self._classify_node_type(item)
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
        print(f"[QM][Profile] classify_nodes duration={classify_duration_ms:.3f} ms")

        # --- Apply sorting (with optional one-shot freeze for 'Name' sort after rename) ---
        if getattr(self, "_sort_mode", "name") == "name" and getattr(self, "_freeze_name_sort_once", False):
            # Preserve prior visual order for the nodes that pass current filters
            index = {m: i for i, m in enumerate(prev_order)}
            large = 10**9
            materials_only.sort(key=lambda m: index.get(m, large))
            file_textures_only.sort(key=lambda m: index.get(m, large))
            procedural_textures_only.sort(key=lambda m: index.get(m, large))
            shading_groups_only.sort(key=lambda m: index.get(m, large))
            utilities_only.sort(key=lambda m: index.get(m, large))
            self._freeze_name_sort_once = False  # consume the freeze
        else:
            materials_only = self._sort_materials(materials_only, all_nodes)
            file_textures_only = self._sort_materials(file_textures_only, all_nodes)
            procedural_textures_only = self._sort_materials(procedural_textures_only, all_nodes)
            shading_groups_only = self._sort_materials(shading_groups_only, all_nodes)
            utilities_only = self._sort_materials(utilities_only, all_nodes)

        # --- Determine which sections to show based on active tab button ---
        shaders_btn = self._get_widget('materialListShadersButton', QtWidgets.QPushButton)
        textures_btn = self._get_widget('materialListTexturesButton', QtWidgets.QPushButton)
        shading_groups_btn = self._get_widget('materialListShadingGroupButton', QtWidgets.QPushButton)
        utilities_btn = self._get_widget('materialListUtilitiesButton', QtWidgets.QPushButton)
        
        show_shaders = shaders_btn and shaders_btn.isChecked()
        show_textures = textures_btn and textures_btn.isChecked()
        show_shading_groups = shading_groups_btn and shading_groups_btn.isChecked()
        show_utilities = utilities_btn and utilities_btn.isChecked()
        
        # If no button is checked (shouldn't happen with new exclusivity, but handle it), show all
        if not (show_shaders or show_textures or show_shading_groups or show_utilities):
            show_shaders = True
            show_textures = True
            show_shading_groups = True
            show_utilities = True
        
        # --- Add SHADERS section header (only if shaders tab is active) ---
        if show_shaders:
            self._add_node_type_header(scroll_layout, row, 'materials')
            row += 1
            
            # Populate materials entries (+ action rows) or show empty message
            if materials_only:
                for material in materials_only:
                    is_default = material in DEFAULT_MATERIALS
                    # Type headers are removed - just use the general 'Shaders:' header at the top
                    self.add_material_entry_optimized(material, row, scroll_layout, DEFAULT_MATERIALS, saved_selection, material_properties)
                    self.add_material_buttons(material, row, scroll_layout, is_default)
                    row += 2
            else:
                # Show empty state message
                self._add_empty_state_message(scroll_layout, row, 'materials')
                row += 1

        # --- Add TEXTURES section headers (only if textures tab is active) ---
        # File and procedural textures share the same "Textures" tab,
        # but get their own sub-headers for clarity.
        if show_textures:
            # Only show header if there are any textures (file or procedural)
            if file_textures_only:
                self._add_node_type_header(scroll_layout, row, 'file_textures')
                row += 1
                for texture in file_textures_only:
                    self.add_material_entry_optimized(texture, row, scroll_layout, DEFAULT_MATERIALS, saved_selection, material_properties)
                    self.add_material_buttons(texture, row, scroll_layout, False)
                    row += 2
            
            if procedural_textures_only:
                self._add_node_type_header(scroll_layout, row, 'procedural_textures')
                row += 1
                for texture in procedural_textures_only:
                    self.add_material_entry_optimized(texture, row, scroll_layout, DEFAULT_MATERIALS, saved_selection, material_properties)
                    self.add_material_buttons(texture, row, scroll_layout, False)
                    row += 2

            if not file_textures_only and not procedural_textures_only:
                # Show a single header + empty state when no textures exist
                self._add_node_type_header(scroll_layout, row, 'file_textures')
                row += 1
                self._add_empty_state_message(scroll_layout, row, 'file_textures')
                row += 1

        # --- Add SHADING GROUPS section header (only if shading groups tab is active) ---
        if show_shading_groups:
            self._add_node_type_header(scroll_layout, row, 'shading_groups')
            row += 1
            
            # Populate shading group entries or show empty message
            if shading_groups_only:
                for sg in shading_groups_only:
                    self.add_material_entry_optimized(sg, row, scroll_layout, DEFAULT_MATERIALS, saved_selection, material_properties)
                    self.add_material_buttons(sg, row, scroll_layout, False)
                    row += 2
            else:
                # Show empty state message
                self._add_empty_state_message(scroll_layout, row, 'shading_groups')
                row += 1

        # --- Add UTILITIES section header ---
        if show_utilities:
            self._add_node_type_header(scroll_layout, row, 'utilities')
            row += 1

            if utilities_only:
                for node in utilities_only:
                    self.add_material_entry_optimized(node, row, scroll_layout, DEFAULT_MATERIALS, saved_selection, material_properties)
                    self.add_material_buttons(node, row, scroll_layout, False)
                    row += 2
            else:
                self._add_empty_state_message(scroll_layout, row, 'utilities')
                row += 1


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
        duration_ms = (time.perf_counter() - start_time) * 1000.0
        total_nodes = len(nodes_to_display)
        texture_nodes = len(file_textures_only) + len(procedural_textures_only)
        print(f"[QM][Populate] nodes={total_nodes} materials={len(materials_only)} textures={texture_nodes} sgs={len(shading_groups_only)} utilities={len(utilities_only)} duration={duration_ms:.3f} ms")

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

    def _add_node_type_header(self, grid_layout, row, node_type_key):
        """
        Add a dedicated header for a node type category.
        node_type_key: 'materials', 'file_textures', 'procedural_textures', 'shading_groups', or 'utilities'
        """
        config = self.NODE_TYPES.get(node_type_key, {})
        header_text = config.get('header_text', node_type_key)
        header_color = config.get('header_color', '#ffffff')
        
        # Special case: make shaders header text white/light grey
        if node_type_key == 'materials':
            header_color = '#ffffff'  # White/light grey color for shaders
        
        bar = QtWidgets.QWidget()
        object_name = f"qm{node_type_key.title().replace('_', '')}Header"
        bar.setObjectName(object_name)
        bar.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        bar.setAutoFillBackground(True)

        bar.setStyleSheet(f"""
            QWidget#{object_name} {{
                background-color: transparent;
                border: none;
            }}
            QWidget#{object_name} QLabel {{
                color: {header_color};
                font-weight: bold;
                padding: 1px 4px;
                font-size: 12px;
            }}
        """)

        lay = QtWidgets.QHBoxLayout(bar)
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(0)
        
        lbl = QtWidgets.QLabel(f"{header_text}:")
        lbl.setTextInteractionFlags(QtCore.Qt.NoTextInteraction)
        lay.addWidget(lbl)
        lay.addStretch(1)

        grid_layout.addWidget(bar, row, 0, 1, 4)

    def _add_empty_state_message(self, grid_layout, row, node_type_key):
        """
        Add an italic empty state message when there are no items of a given type.
        node_type_key: 'materials', 'file_textures', 'procedural_textures', 'shading_groups', or 'utilities'
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
        
        # Create empty state message text
        empty_messages = {
            'materials': "There are no shaders in this scene",
            'file_textures': "There are no textures in this scene",
            'procedural_textures': "There are no procedural textures in this scene",
            'shading_groups': "There are no shading groups in this scene",
            'utilities': "There are no utility nodes in this scene",
        }
        
        message_text = empty_messages.get(node_type_key, f"There are no {header_text.lower()} in this scene")
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
            line_edit.setReadOnly(True)
            line_edit.setProperty("editing", "false")
            line_edit.setProperty("qmEditMode", "false")
            if hasattr(line_edit, "clearSecondaryText"):
                line_edit.clearSecondaryText()
        else:
            container = QtWidgets.QWidget()
            layout = QtWidgets.QHBoxLayout(container)
            layout.setContentsMargins(1, 0, 2, 0)  # Reduced left margin for less padding around swatch
            layout.setSpacing(2)  # Reduced spacing for tighter layout
            line_edit = MaterialDisplayLineEdit("")
            line_edit.setObjectName("qmMaterialLineEdit")
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

    def add_material_entry_optimized(self, material, row, scroll_layout, default_materials, saved_selection=None, material_properties=None):
        """
        OPTIMIZED VERSION: Create material entry using pre-computed colors to avoid Maya API calls.
        """
        # Strip namespace if option is enabled
        display_name = self._strip_namespace(material)

        # Classify the node type to determine display and behavior
        node_type_category = self._classify_node_type(material)
        is_file_texture = (node_type_category == 'file_textures')
        is_procedural_texture = (node_type_category == 'procedural_textures')
        is_shading_group = (node_type_category == 'shading_groups')
        is_material = (node_type_category == 'materials')
        is_utility = (node_type_category == 'utilities')
        
        shader_type = None
        if is_material:
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
            # File textures: show filename with HTML formatting
            try:
                info = self._get_file_texture_display_info(material)
                if info and info['filename']:
                    use_rich_text_label = True
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
            except Exception:
                pass

        if node_type_category == 'materials':
            container, material_widget = self._acquire_material_row()
            material_layout = container.layout()
            material_widget.setText(display_name)
            if isinstance(material_widget, MaterialDisplayLineEdit):
                material_widget.setSecondaryText(shader_type)
            
            # Add swatch icon to the left of material entries (if checkbox is enabled)
            if MaterialSwatchIcon is not None:
                # Check if show shader swatches checkbox is enabled
                # Use showIconsCheckbox to control all icons including shader swatches
                show_icons_cb = self._get_widget('showIconsCheckbox', QtWidgets.QCheckBox)
                show_icons = show_icons_cb.isChecked() if show_icons_cb else True  # Default to True if checkbox doesn't exist
                
                if show_icons:
                    try:
                        swatch_icon = MaterialSwatchIcon(material, icon_size=20, parent=container)
                        swatch_icon.setFixedSize(20, 20)  # Slightly smaller than list entry height
                        # Make swatch icon clickable to select material
                        swatch_icon.setSelectionHandler(self, "handle_item_click", material)
                        # Store the actual material name for operations
                        swatch_icon._actual_material_name = material
                        # Insert at the beginning of the layout (left side)
                        material_layout.insertWidget(0, swatch_icon)
                        # Load swatch asynchronously after a short delay to avoid blocking UI
                        QtCore.QTimer.singleShot(10, swatch_icon.load_swatch)
                    except Exception as e:
                        print(f"[QuickMaterials] Failed to create swatch icon for {material}: {e}")
        else:
            container = QtWidgets.QWidget()
            material_layout = QtWidgets.QHBoxLayout()
            material_layout.setContentsMargins(1, 0, 2, 0)  # Match material entry left margin for alignment
            material_layout.setSpacing(3)
            container.setLayout(material_layout)
            if use_rich_text_label:
                material_widget = TextureDisplayLabel(display_text)
            else:
                material_widget = LeftClipLineEdit(display_text)
            
            # Check if show icons checkbox is enabled (controls all icons: swatches, textures, shading groups)
            show_icons_cb = self._get_widget('showIconsCheckbox', QtWidgets.QCheckBox)
            show_icons = show_icons_cb.isChecked() if show_icons_cb else True  # Default to True if checkbox doesn't exist
            
            # Add texture icon to the left of file texture entries
            if is_file_texture and show_icons:
                try:
                    # Reduced left spacer to move icon and entry closer to the left
                    spacer = QtWidgets.QSpacerItem(2, 1, QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Minimum)
                    material_layout.addItem(spacer)
                    
                    texture_icon = TextureIcon(material, icon_size=14, parent=container)
                    texture_icon.setFixedSize(14, 14)  # Smaller icon, spacing kept same for alignment
                    # Make texture icon clickable to select texture
                    texture_icon.setSelectionHandler(self, "handle_item_click", material)
                    # Store the actual material name for operations
                    texture_icon._actual_material_name = material
                    # Add the icon after the spacer
                    material_layout.addWidget(texture_icon)
                except Exception as e:
                    print(f"[QuickMaterials] Failed to create texture icon for {material}: {e}")
            
            # Add procedural texture icon to the left of procedural texture entries
            if is_procedural_texture and show_icons:
                try:
                    # Reduced left spacer to move icon and entry closer to the left
                    spacer = QtWidgets.QSpacerItem(2, 1, QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Minimum)
                    material_layout.addItem(spacer)
                    
                    proc_texture_icon = ProceduralTextureIcon(material, icon_size=14, parent=container)
                    proc_texture_icon.setFixedSize(14, 14)  # Same size as file texture icon, smaller for alignment
                    # Make procedural texture icon clickable to select texture
                    proc_texture_icon.setSelectionHandler(self, "handle_item_click", material)
                    # Store the actual material name for operations
                    proc_texture_icon._actual_material_name = material
                    # Add the icon after the spacer
                    material_layout.addWidget(proc_texture_icon)
                except Exception as e:
                    print(f"[QuickMaterials] Failed to create procedural texture icon for {material}: {e}")
            
            # Shading group icons removed - no longer used
            if is_utility and show_icons:
                try:
                    spacer = QtWidgets.QSpacerItem(2, 1, QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Minimum)
                    material_layout.addItem(spacer)

                    util_icon = UtilityNodeIcon(material, node_type_name, icon_size=14, parent=container)
                    util_icon.setSelectionHandler(self, "handle_item_click", material)
                    util_icon._actual_material_name = material
                    material_layout.addWidget(util_icon)
                except Exception as e:
                    print(f"[QuickMaterials] Failed to create utility icon for {material}: {e}")
            
            material_layout.addWidget(material_widget)
            container.setContentsMargins(0, 0, 0, 0)
        
        # Store the actual material name for operations
        material_widget._actual_material_name = material
        
        # Link clicks on the line edit to Outliner-style selection (owner + method name, guarded)
        material_widget.setSelectionHandler(self, "handle_item_click", material)
        # Start unselected
        material_widget.setProperty("qmSelected", "false")
        material_widget.setProperty("qmEditMode", "false")  # Default to non-edit mode
        
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
            is_unused = not props.get('used', True)  # If 'used' is False or missing, it's unused
        
        # Check if highlight unused checkbox is checked
        highlight_unused_cb = self._get_widget('highlightUnusedCheckbox', QtWidgets.QCheckBox)
        should_highlight_unused = highlight_unused_cb and highlight_unused_cb.isChecked() if highlight_unused_cb else False
        
        # Set unused property for CSS styling (only if checkbox is checked)
        # Applies to materials, textures, and shading groups
        if should_highlight_unused and is_unused:
            material_widget.setProperty("qmUnused", "true")
        else:
            material_widget.setProperty("qmUnused", "false")

        # Force style update - especially important for QLabel (TextureDisplayLabel)
        material_widget.style().unpolish(material_widget)
        material_widget.style().polish(material_widget)
        material_widget.update()  # Force repaint

        # Register this row for ordered selection behavior
        self._register_material_entry(material, None, material_widget, is_default=(material in default_materials), container=container)

        material_widget.setMinimumWidth(120)
        if material not in default_materials:
            material_widget.setProperty("materialType", "")

        if material in default_materials:
            # Default materials: read-only, can't take focus; use muted style via property
            if isinstance(material_widget, QtWidgets.QLineEdit):
                material_widget.setReadOnly(True)
                material_widget.setFocusPolicy(QtCore.Qt.NoFocus)
            material_widget.setProperty("materialType", "default")
            material_widget.setProperty("editing", "false")   # ensure non-editing visual
        elif is_file_texture:
            # File textures: Read-only (QLabel with rich text), not renamable
            material_widget.setProperty("editing", "false")
        elif is_procedural_texture or is_shading_group or is_utility:
            # Procedural textures and shading groups: Renamable
            if isinstance(material_widget, QtWidgets.QLineEdit):
                try:
                    # Commit on focus-out
                    material_widget.editingFinished.connect(partial(self.rename_texture, material_widget))
                    # Commit also when pressing Enter
                    material_widget.returnPressed.connect(partial(self.rename_texture, material_widget))
                except AttributeError:
                    print("Error: rename_texture function not found")
            material_widget.setProperty("editing", "false")
        elif is_material:
            material_widget.setProperty("editing", "false")
        else:
            # Regular materials: editable (only QLineEdit supports editing)
            if isinstance(material_widget, QtWidgets.QLineEdit):
                try:
                    # Commit on focus-out
                    material_widget.editingFinished.connect(partial(self.rename_material, material_widget))
                    # Commit also when pressing Enter
                    material_widget.returnPressed.connect(partial(self.rename_material, material_widget))
                except AttributeError:
                    print("Error: rename_material function not found")

        # Apply sizes; container already has the stylesheet
        material_widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                      QtWidgets.QSizePolicy.Fixed)  # Fixed height fits style

        material_widget.setMinimumHeight(22)  # aligns with LeftClipLineEdit min we set
        # For file textures, lock the height to prevent rich text from expanding
        if is_file_texture:
            material_widget.setMaximumHeight(22)

        # Ensure zero margins to prevent extra spacing
        container.setContentsMargins(0, 0, 0, 0)
        # For file textures, ensure absolutely no vertical spacing
        if is_file_texture:
            material_layout.setContentsMargins(2, 0, 2, 0)  # Keep horizontal margins, zero vertical

        # Apply the same stylesheet as the parent scroll area
        container.setStyleSheet(self.material_list_widget_style)

        # Add to the grid layout
        scroll_layout.addWidget(container, row, 0, 1, 4)
        container.show()

        # Handle saved selection state
        if saved_selection and material in saved_selection:
            self._select_material_entry(material, True, update_visuals=False)



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
        start_ts = time.perf_counter()
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
            end_ts = time.perf_counter()
            build_ms = (end_ts - start_ts) * 1000.0
            request_ts = getattr(self, "_last_refresh_request_ts", 0.0)
            total_ms = (end_ts - request_ts) * 1000.0 if request_ts else build_ms
            print(f"[QM][Refresh] build={build_ms:.3f} ms total_since_request={total_ms:.3f} ms")
            self._last_refresh_request_ts = 0.0

    # Filter-as-you-type entrypoint; forwards to populate with search_text.
    def filter_materials(self, search_text):
        # PERFORMANCE OPTIMIZATION: Use debounced refresh for search
        self.refresh_materials_list()

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
        Also reads tab button states for material list filtering.
        Also exposes legacy keys for compatibility (selectedOnly/nonSelectedOnly).
        """
        flags = {}
        for f in self._filter_spec():
            cb = self._get_widget(f["checkbox"], QtWidgets.QCheckBox)
            flags[f["id"]] = bool(cb and cb.isChecked())

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
        flags["nonSelectedOnly"] = flags.get("nonSelected", False)    # legacy alias
        return flags


    # Applies filter flags + search to a single material name.
    def _batch_compute_material_properties(self, all_materials, current_sel_shapes):
        """
        PERFORMANCE OPTIMIZATION: Batch compute expensive material properties with caching.
        Returns a dict with material properties to avoid repeated Maya API calls.
        """
        import time
        
        # Check if cache is still valid
        current_time = time.time()
        if (current_time - self._cache_timestamp) < self._cache_timeout and self._material_cache:
            # Return cached results for materials that still exist
            cached_properties = {}
            needs_selection_update = []
            
            for mat in all_materials:
                if mat in self._material_cache:
                    props = self._material_cache[mat].copy()
                    # Check if affects_selection is stale (None means invalidated)
                    if props.get('affects_selection') is None:
                        needs_selection_update.append(mat)
                    cached_properties[mat] = props
                else:
                    # New material not in cache - compute properties
                    cached_properties[mat] = self._compute_single_material_properties(mat, current_sel_shapes)
            
            # If some materials have stale selection data, update just that
            if needs_selection_update:
                materials_from_selection = self._get_materials_from_selection()
                for mat in needs_selection_update:
                    cached_properties[mat]['affects_selection'] = mat in materials_from_selection
                    # Update the main cache too
                    self._material_cache[mat]['affects_selection'] = mat in materials_from_selection
            
            return cached_properties
        
        # Cache expired or empty - recompute everything
        properties = {}
        
        # Separate materials, textures, and shading groups for different processing
        materials_only = []
        textures_only = []
        shading_groups_only = []
        for item in all_materials:
            try:
                if cmds.nodeType(item) == 'shadingEngine':
                    shading_groups_only.append(item)
                elif self._is_texture_node(item):
                    textures_only.append(item)
                else:
                    materials_only.append(item)
            except Exception:
                materials_only.append(item)
        
        # Batch compute referenced status (materials only)
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
        except Exception:
            # Fallback to individual queries
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
        # Get all shading engines for all materials at once
        all_sgs = set()
        material_to_sgs = {}
        for mat in materials_only:
            sgs = self._connected_shading_engines(mat)
            material_to_sgs[mat] = sgs
            all_sgs.update(sgs)
        
        # Batch query which SGs have members
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
        
        # Now assign usage based on batch results (materials only)
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
        
        # Batch compute selection relationship (both materials and textures)
        materials_from_selection = self._get_materials_from_selection()
        for item in all_materials:
            properties[item]['affects_selection'] = item in materials_from_selection
        
        # Update cache
        self._material_cache = properties.copy()
        self._cache_timestamp = current_time
        
        return properties

    def _compute_single_material_properties(self, material, current_sel_shapes):
        """
        Compute properties for a single material (used for cache misses).
        """
        materials_from_selection = self._get_materials_from_selection()
        return {
            'referenced': self._is_referenced(material),
            'used': self._is_material_used(material),
            'affects_selection': material in materials_from_selection
        }

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
            # Debounced refresh instead of immediate
            self._queue_material_refresh()

        def on_b(state):
            a_cb = self._get_widget(a_name, QtWidgets.QCheckBox)
            b_cb = self._get_widget(b_name, QtWidgets.QCheckBox)
            if not (a_cb and b_cb and _is_valid(a_cb) and _is_valid(b_cb)):
                return
            if state == QtCore.Qt.Checked and a_cb.isChecked():
                QtCore.QSignalBlocker(a_cb)
                a_cb.setChecked(False)
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
                peer.setChecked(False)

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
        for name in button_names:
            btn = self._get_widget(name, QtWidgets.QPushButton)
            if not btn:
                continue
            # Ensure button is checkable
            btn.setCheckable(True)
            # Connect toggled signal (QPushButton uses toggled, not stateChanged)
            try:
                btn.toggled.connect(
                    lambda checked, _name=name, _grp=group_name: self._on_exclusive_button_group_changed(_name, _grp, checked)
                )
            except Exception:
                # Some hosts error if double-connecting identical lambdas; safe to ignore
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
                btn.setChecked(True)
            return  # Don't refresh, nothing changed

        # When a button is checked, uncheck all others in the group
        names = list(self._exclusive_button_groups.get(group_name, []))
        for peer_name in names:
            if peer_name == changed_name:
                continue
            peer = self._get_widget(peer_name, QtWidgets.QPushButton)
            if peer and peer.isChecked():
                blocker = QtCore.QSignalBlocker(peer)
                peer.setChecked(False)

        self._queue_material_refresh()


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
        start_ts = time.perf_counter()

        try:
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
        finally:
            elapsed_ms = (time.perf_counter() - start_ts) * 1000.0
            print(f"[QM][Sort] mode={mode} desc={int(desc)} count={len(mats)} duration={elapsed_ms:.3f} ms")


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
            print(f"[DEBUG] Found sorting buttons: name={name_btn is not None}, type={type_btn is not None}, time={time_btn is not None}")
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

    # Create the action-row buttons under each entry (Assign / Select Objs / Graph / Imp Tx).
    def add_material_buttons(self, material, row, scroll_layout, is_default):
        """
        Create and add action buttons based on NODE_TYPES config.
        - For materials: Assign, Highlight, Graph, Import Tx
        - For file textures: Open File, Colorspace
        - For procedural textures/shading groups: No buttons (per NODE_TYPES config)

        Args:
            material (str): The name of the material or texture.
            row (int): The row index to insert these buttons.
            scroll_layout (QGridLayout): The layout to which the buttons will be added.
            is_default (bool): Flag indicating if the material is a default material.
        """
        # Classify the node type
        node_type_category = self._classify_node_type(material)
        
        # Check if this node type supports buttons
        config = self.NODE_TYPES.get(node_type_category, {})
        supports_buttons = config.get('supports_buttons', True)
        
        if not supports_buttons:
            # This node type doesn't get buttons (procedural textures, shading groups)
            return
        
        # Create container and add to layout
        button_container = QtWidgets.QWidget()
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(3)
        button_container.setLayout(button_layout)
        button_container.setContentsMargins(0, 0, 0, 0)
        scroll_layout.addWidget(button_container, row + 1, 0, 1, 4)

        # Remember metadata so we can lazily build buttons on demand
        button_container._qm_button_meta = (material, node_type_category, is_default)
        button_container._qm_buttons_populated = False

        # Remember these rows for hide/show functionality
        if not hasattr(self, "_material_button_rows"):
            self._material_button_rows = []
        self._material_button_rows.append(button_container)

        # Respect current visibility state
        if getattr(self, "_list_buttons_visible", True):
            self._build_material_button_row(button_container, material, node_type_category, is_default)
            button_container.setVisible(True)
        else:
            button_container.setVisible(False)

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
        
        # Don't allow selection of default materials (lambert1, standardSurface1, etc.)
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
            # BUGFIX & OPTIMIZATION: Invalidate only selection-related cache for faster updates
            # This keeps referenced/used properties cached, only recomputes affects_selection
            self._invalidate_material_cache(selection_only=True)
            self._queue_material_refresh(120)


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
                return

            # Rename the texture node in Maya
            actual_new = cmds.rename(prev_name, new_name)
            print(f"[QM] Renamed texture: {prev_name} → {actual_new}")
            
            # Update internal tracking
            _update_internal_maps(prev_name, actual_new)

            # Update display to reflect Maya's actual rename result
            texture_name_edit.setText(actual_new)
            setattr(texture_name_edit, "_pre_edit_text", actual_new)

        except Exception as e:
            cmds.warning(f"Failed to rename texture '{prev_name}' to '{new_name}': {e}")
            texture_name_edit.setText(prev_name)

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
            
            # The first node in the list is typically the duplicated shading group
            new_sg = duplicated[0]
            
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
                new_sg = duplicated[0]
                
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

    def graph_materials_batch(self, materials):
        """Graph all specified materials in the node editor (batch operation)."""
        if not materials:
            cmds.warning("No materials to graph.")
            return
        
        print(f"[QM][GraphBatch] Graphing {len(materials)} materials")
        
        # Collect all nodes to graph
        all_nodes = []
        all_sgs = []
        
        for material in materials:
            # Add the material itself
            all_nodes.append(material)
            
            # Find shading groups for this material
            try:
                raw_sgs = set(cmds.listConnections(material, type='shadingEngine', s=False, d=True) or [])
                raw_sgs.update(cmds.listConnections(f'{material}.outColor', type='shadingEngine', s=False, d=True) or [])
                sgs = [sg for sg in raw_sgs if sg not in ('initialShadingGroup', 'initialParticleSE')]
                all_sgs.extend(sgs)
            except Exception:
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
                        except Exception:
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
            except Exception:
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
                    except Exception:
                        pass
                    
                    # Add all nodes
                    for node in all_nodes:
                        try:
                            cmds.nodeEditor(ed, e=True, addNode=node)
                        except Exception:
                            pass
                    
                    # Select and frame
                    try:
                        cmds.nodeEditor(ed, e=True, clearSelection=True)
                        for node in all_nodes:
                            try:
                                cmds.nodeEditor(ed, e=True, selectNode=node)
                            except Exception:
                                pass
                        cmds.nodeEditor(ed, e=True, frameSelected=True)
                    except Exception:
                        try:
                            cmds.nodeEditor(ed, e=True, frameAll=True)
                        except Exception:
                            pass
                    
                    print(f"[QM][GraphBatch] Graphed {len(all_nodes)} nodes from {len(materials)} materials")
            except Exception as e:
                cmds.warning(f"[QM][GraphBatch] Failed to graph materials: {e}")
        
        # Delay to let editor open
        QtCore.QTimer.singleShot(200, _add_to_editor)

    # Master toggle to show/hide all action-button rows under each entry.
    def toggle_material_list_buttons_checkbox(self, checked):
        """
        Show/hide the action-row buttons ('Assign', 'Highlight', 'Select', 'Graph', 'Import Tx')
        for every material entry based on checkbox state.
        """
        self._list_buttons_visible = checked
        
        rows = getattr(self, "_material_button_rows", []) or []
        for row_w in rows:
            try:
                if row_w and row_w.parent():
                    if self._list_buttons_visible:
                        meta = getattr(row_w, "_qm_button_meta", None)
                        if meta:
                            mat, node_type, is_default = meta
                            self._build_material_button_row(row_w, mat, node_type, is_default)
                    row_w.setVisible(self._list_buttons_visible)
            except Exception:
                pass  # Skip stale widgets

    def toggle_material_list_options(self, checked):
        """
        Toggle the visibility of the material list options panel.
        """
        # Find the options frame/layout
        options_frame = self.findChild(QtWidgets.QWidget, 'materialListOptionsFrame')
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

    def _get_utility_nodes(self):
        """Return a curated list of high-value utility nodes present in the scene."""
        gathered = []
        seen = set()
        for node_type in self.UTILITY_NODE_TYPES:
            try:
                nodes = cmds.ls(type=node_type) or []
            except Exception:
                nodes = []
            for node in nodes:
                if node in seen:
                    continue
                seen.add(node)
                gathered.append(node)
        return gathered

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
        """
        try:
            node_type = cmds.nodeType(node)
            
            # Check if it's a shading engine (shading group)
            if node_type == 'shadingEngine':
                return 'shading_groups'

            # Check curated utility types (multiplyDivide, etc.)
            if getattr(self, "_utility_node_types_cache", None) is None:
                self._utility_node_types_cache = set(self.UTILITY_NODE_TYPES)
            if node_type in self._utility_node_types_cache:
                return 'utilities'
            
            # Check if it's a file texture
            if node_type == 'file':
                return 'file_textures'
            
            # Check if it's a procedural texture (using Maya's classifications)
            if self._procedural_texture_types is None:
                texture_classifications = ['texture/2d', 'texture/3d', 'texture/env', 'texture/other', 'imageplane']
                procedural_types = set()
                for classification in texture_classifications:
                    procedural_types.update(self._node_types_for_classification(classification))
                procedural_types.discard('file')  # ensure file textures stay in their own bucket
                self._procedural_texture_types = procedural_types
            if node_type in self._procedural_texture_types:
                return 'procedural_textures'
            
            # Check if it's a material (shader)
            if cmds.ls(node, materials=True):
                return 'materials'
                
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
        """
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
    
    def _refresh_single_file_texture_entry(self, file_node, new_colorspace):
        """
        Refresh only a single file texture entry's display text to show updated colorspace.
        This avoids a full list rebuild which would scroll to top.
        """
        try:
            # Find the entry in our registry
            entry_idx = self._index_by_material.get(file_node)
            if entry_idx is None or entry_idx < 0 or entry_idx >= len(self._entry_list):
                return
            
            entry = self._entry_list[entry_idx]
            line_edit = entry.get("line_edit")
            
            # Check if this is a QLabel (TextureDisplayLabel) with rich text
            if isinstance(line_edit, QtWidgets.QLabel) and isValid(line_edit):
                # Rebuild the HTML display text with new colorspace
                info = self._get_file_texture_display_info(file_node)
                if info and info['filename']:
                    display_text = f'<span style="color: #e0e0e0;">{info["filename"]}</span>'
                    
                    # Add UDIM count if applicable (in blue)
                    if info['udim_count'] > 1:
                        display_text += f'  <span style="color: #6fa3d8;">({info["udim_count"]} tiles)</span>'
                    
                    # Add colorspace in brackets (in grey)
                    if info['colorspace']:
                        display_text += f'  <span style="color: #999999;">({info["colorspace"]})</span>'
                    
                    # Update the label text
                    line_edit.setText(display_text)
                    print(f"[QM] Refreshed display for '{file_node}' with colorspace '{new_colorspace}'")
        except Exception as e:
            print(f"[QM] Failed to refresh single entry: {e}")

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
            if cmds.nodeType(file_node) != 'file':
                return
            
            current_colorspace = self._get_file_texture_colorspace(file_node)
            
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
                self._invalidate_material_cache()  # Clear cache since we deleted materials
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
