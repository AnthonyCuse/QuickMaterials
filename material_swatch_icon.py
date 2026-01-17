"""
Material Swatch Icon Generator
-------------------------------
Generates small swatch icons for materials to display in the Quick Materials list.
All swatch rendering logic is contained in this file.
"""

import os
import math
import weakref

# Qt compatibility for Maya 2024 (PySide2) & Maya 2025 (PySide6)
try:
    # Maya 2025+
    from PySide6 import QtCore, QtWidgets, QtGui
    QT_LIB = 6
except ImportError:
    # Maya 2024-
    from PySide2 import QtCore, QtWidgets, QtGui
    QT_LIB = 2

import maya.cmds as cmds

# Global cache for texture average colors
# Key: (texture_path, mtime), Value: (r, g, b) tuple
_texture_color_cache = {}

# Global cache for texture images (QPixmap)
# Key: (texture_path, mtime, size), Value: QPixmap
_texture_image_cache = {}

# Global cache for material swatch icons
# Key: (material_name, swatch_hash), Value: QPixmap
_swatch_cache = {}


def invalidate_swatch_cache(material_name):
    """
    Remove a specific material's swatch from the cache.
    This is called when a material's attributes change to force swatch regeneration.
    
    Args:
        material_name: Name of the material whose swatch cache should be invalidated
    """
    keys_to_remove = [k for k in _swatch_cache.keys() if k[0] == material_name]
    for k in keys_to_remove:
        del _swatch_cache[k]


def _get_cache_key(texture_path):
    """Generate a cache key based on file path and modification time.
    Returns None if file doesn't exist."""
    try:
        if not texture_path or not os.path.exists(texture_path):
            return None
        mtime = os.path.getmtime(texture_path)
        return (texture_path, mtime)
    except:
        return None


def _get_material_swatch_hash(material_name, fast_mode=False):
    """
    Generate a hash representing the material's visual state for swatch caching.
    Returns a hash string that changes when material properties affecting the swatch change.
    
    Args:
        material_name: Name of the material node
        fast_mode: If True, only hash basic properties (skip texture file checks)
    
    Returns:
        str: Hash string representing material state, or None if material doesn't exist
    """
    try:
        if not cmds.objExists(material_name):
            return None
        
        import hashlib
        node_type = cmds.nodeType(material_name)
        
        # Collect all properties that affect swatch appearance
        hash_data = []
        hash_data.append(("node_type", node_type))
        
        # Base color
        try:
            if node_type in ["standardSurface", "aiStandardSurface"]:
                color_attr = f"{material_name}.baseColor"
            elif node_type in ["lambert", "blinn", "phong"]:
                color_attr = f"{material_name}.color"
            else:
                color_attr = None
            
            if color_attr and cmds.attributeQuery(color_attr.split('.')[-1], node=material_name, exists=True):
                connections = cmds.listConnections(color_attr, s=True, d=False)
                if connections:
                    texture_node = connections[0]
                    hash_data.append(("color_texture", texture_node))
                    if not fast_mode:
                        # Include texture file path and mtime if available
                        if cmds.nodeType(texture_node) == "file":
                            try:
                                texture_path = cmds.getAttr(f"{texture_node}.fileTextureName")
                                if texture_path and os.path.exists(texture_path):
                                    mtime = os.path.getmtime(texture_path)
                                    hash_data.append(("color_texture_file", (texture_path, mtime)))
                            except:
                                pass
                else:
                    try:
                        color_value = cmds.getAttr(color_attr)
                        if isinstance(color_value, (list, tuple)):
                            if len(color_value) > 0 and isinstance(color_value[0], (list, tuple)):
                                hash_data.append(("color", tuple(float(x) for x in color_value[0][:3])))
                            elif len(color_value) >= 3:
                                hash_data.append(("color", tuple(float(x) for x in color_value[:3])))
                    except:
                        pass
        except:
            pass
        
        # Metalness
        try:
            if node_type in ["standardSurface", "aiStandardSurface"]:
                if cmds.attributeQuery("metalness", node=material_name, exists=True):
                    metalness = float(cmds.getAttr(f"{material_name}.metalness"))
                    hash_data.append(("metalness", round(metalness, 4)))
        except:
            pass
        
        # Roughness
        try:
            if node_type in ["standardSurface", "aiStandardSurface"]:
                if cmds.attributeQuery("specularRoughness", node=material_name, exists=True):
                    roughness = float(cmds.getAttr(f"{material_name}.specularRoughness"))
                    hash_data.append(("roughness", round(roughness, 4)))
        except:
            pass
        
        # Emission
        try:
            if node_type in ["standardSurface", "aiStandardSurface"]:
                if cmds.attributeQuery("emissionColor", node=material_name, exists=True):
                    emission_conns = cmds.listConnections(f"{material_name}.emissionColor", s=True, d=False)
                    if emission_conns:
                        hash_data.append(("emission_texture", emission_conns[0]))
                    else:
                        emission_value = cmds.getAttr(f"{material_name}.emissionColor")
                        if isinstance(emission_value, (list, tuple)):
                            if len(emission_value) > 0 and isinstance(emission_value[0], (list, tuple)):
                                hash_data.append(("emission", tuple(float(x) for x in emission_value[0][:3])))
                            elif len(emission_value) >= 3:
                                hash_data.append(("emission", tuple(float(x) for x in emission_value[:3])))
        except:
            pass
        
        # Opacity/Transmission
        try:
            if node_type in ["standardSurface", "aiStandardSurface"]:
                if cmds.attributeQuery("opacity", node=material_name, exists=True):
                    opacity_value = cmds.getAttr(f"{material_name}.opacity")
                    if isinstance(opacity_value, (list, tuple)):
                        if len(opacity_value) > 0 and isinstance(opacity_value[0], (list, tuple)):
                            hash_data.append(("opacity", tuple(float(x) for x in opacity_value[0][:3])))
                        elif len(opacity_value) >= 3:
                            hash_data.append(("opacity", tuple(float(x) for x in opacity_value[:3])))
                if cmds.attributeQuery("transmission", node=material_name, exists=True):
                    transmission = float(cmds.getAttr(f"{material_name}.transmission"))
                    hash_data.append(("transmission", round(transmission, 4)))
        except:
            pass
        
        # Generate hash from collected data
        hash_str = str(sorted(hash_data))
        return hashlib.md5(hash_str.encode()).hexdigest()
    except Exception:
        return None


class MaterialSwatchIcon(QtWidgets.QLabel):
    """Small swatch icon widget for material list entries."""
    
    # Default display values (same as shader swatch viewer defaults)
    DEFAULT_GRADIENT_START_RATIO = 0.0
    DEFAULT_GRADIENT_VALUE_INSIDE = 1.0
    DEFAULT_GRADIENT_VALUE_OUTSIDE = 0.6
    DEFAULT_GRADIENT_EXPONENT = 100.0
    DEFAULT_SPECULAR_SOFTNESS = 1.0
    DEFAULT_SPECULAR_SIZE_MULTIPLIER = 1.6
    DEFAULT_SPECULAR_POSITION_OFFSET = -0.55
    DEFAULT_TRANSPARENCY_BG_BRIGHTNESS = 0.3
    
    @staticmethod
    def _desaturate_specular_color(specular_color, white_blend=0.65):
        """Desaturate specular color by blending with white to keep it shiny and white-tinted.
        
        Args:
            specular_color: Tuple of (r, g, b) values in range [0.0, 1.0]
            white_blend: Amount of white to blend in [0.0, 1.0]. Higher = more white/less saturated.
                         Default 0.65 means 65% white, 35% specular color.
        
        Returns:
            Tuple of (r, g, b) values in range [0.0, 1.0]
        """
        spec_r, spec_g, spec_b = specular_color
        # Blend with white - this reduces saturation while maintaining the tint
        desat_r = spec_r * (1.0 - white_blend) + 1.0 * white_blend
        desat_g = spec_g * (1.0 - white_blend) + 1.0 * white_blend
        desat_b = spec_b * (1.0 - white_blend) + 1.0 * white_blend
        # Clamp to valid range
        return (max(0.0, min(1.0, desat_r)), 
                max(0.0, min(1.0, desat_g)), 
                max(0.0, min(1.0, desat_b)))
    
    def __init__(self, material_name, icon_size=22, parent=None):
        """Create a small swatch icon for a material.
        
        Args:
            material_name: Name of the material node
            icon_size: Size of the icon in pixels (default 22 to match list entry height)
            parent: Parent widget
        """
        super(MaterialSwatchIcon, self).__init__(parent)
        self.material_name = material_name
        self.icon_size = icon_size
        self._swatch_pixmap = None
        
        # Set fixed size to match list entry height
        self.setFixedSize(icon_size, icon_size)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setScaledContents(False)  # Don't scale contents automatically
        # Material list background color
        self._bg_color = "#3a3a3a"
        self.setStyleSheet(f"""
            QLabel {{
                background-color: {self._bg_color};
                border: none;
                border-radius: {icon_size // 2}px;
            }}
        """)
        
        # Show loading placeholder
        self.setText("...")
        self.setStyleSheet(f"""
            QLabel {{
                background-color: {self._bg_color};
                border: none;
                border-radius: {icon_size // 2}px;
                color: #888888;
                font-size: 8px;
            }}
        """)
        
        # Load swatch asynchronously
        self._swatch_loaded = False
        
        # Selection handler support (for clicking to select material)
        self._owner_ref = None
        self._handler_name = None
        self._bound_handler = None
        self._qm_material_name = material_name
        
        # Make it clickable
        self.setCursor(QtCore.Qt.PointingHandCursor)
    
    def load_swatch(self):
        """Load and display the shader swatch icon."""
        import time as _time
        
        load_start = _time.perf_counter()
        
        if not cmds.objExists(self.material_name):
            self.clear()
            self._swatch_loaded = True
            return
        
        try:
            # First, create swatch in fast mode for instant display
            fast_start = _time.perf_counter()
            pixmap = self._create_swatch_icon(
                self.material_name, 
                self.icon_size,
                fast_mode=True  # Use fast mode for instant loading
            )
            fast_duration = (_time.perf_counter() - fast_start) * 1000.0
            total_duration = (_time.perf_counter() - load_start) * 1000.0

            if pixmap and not pixmap.isNull():
                self._swatch_pixmap = pixmap
                self.clear()  # Clear text
                # Force a repaint to show the new swatch
                self.update()
                self._swatch_loaded = True

                # Then, update with texture images/colors if available (async, non-blocking)
                # Always check for any texture connections (base, emission, subsurface)
                try:
                    node_type = cmds.nodeType(self.material_name)
                    has_base_texture = False
                    has_emission_texture = False
                    has_subsurface_texture = False

                    # Check for base color texture
                    color_attr = None
                    if node_type in ["standardSurface", "aiStandardSurface"]:
                        color_attr = f"{self.material_name}.baseColor"
                    elif node_type in ["lambert", "blinn", "phong"]:
                        color_attr = f"{self.material_name}.color"

                    if color_attr:
                        connections = cmds.listConnections(color_attr, s=True, d=False)
                        if connections:
                            texture_node = connections[0]
                            texture_node_type = cmds.nodeType(texture_node)
                            if texture_node_type == "file":
                                has_base_texture = True

                    # Check for emission texture (always check, not just if no base texture)
                    if node_type in ["standardSurface", "aiStandardSurface"]:
                        if cmds.attributeQuery("emissionColor", node=self.material_name, exists=True):
                            emission_conns = cmds.listConnections(f"{self.material_name}.emissionColor", s=True, d=False)
                            if emission_conns and cmds.nodeType(emission_conns[0]) == "file":
                                has_emission_texture = True
                    elif node_type in ["lambert", "blinn", "phong"]:
                        if cmds.attributeQuery("incandescence", node=self.material_name, exists=True):
                            incandescence_conns = cmds.listConnections(f"{self.material_name}.incandescence", s=True, d=False)
                            if incandescence_conns and cmds.nodeType(incandescence_conns[0]) == "file":
                                has_emission_texture = True

                    # Check for subsurface texture (standardSurface only)
                    if node_type in ["standardSurface", "aiStandardSurface"]:
                        if cmds.attributeQuery("subsurfaceColor", node=self.material_name, exists=True):
                            subsurface_conns = cmds.listConnections(f"{self.material_name}.subsurfaceColor", s=True, d=False)
                            if subsurface_conns and cmds.nodeType(subsurface_conns[0]) == "file":
                                has_subsurface_texture = True

                    # Schedule async update if any textures are found
                    if has_base_texture or has_emission_texture or has_subsurface_texture:
                        texture_types = []
                        if has_base_texture:
                            texture_types.append("base")
                        if has_emission_texture:
                            texture_types.append("emission")
                        if has_subsurface_texture:
                            texture_types.append("subsurface")
                        QtCore.QTimer.singleShot(50, self._update_with_texture_color)
                except Exception as e:
                    pass

                # Track swatch timing for aggregate reporting
                try:
                    # Try to find the parent UI to track timing
                    parent = self.parent()
                    while parent:
                        if hasattr(parent, '_swatch_timing_count'):
                            parent._swatch_timing_count += 1
                            parent._swatch_timing_total += fast_duration
                            break
                        parent = parent.parent() if hasattr(parent, 'parent') else None
                except:
                    pass

                return
            else:
                # Fallback: clear and show nothing
                self.clear()
                self._swatch_loaded = True
        except Exception as e:
            self.clear()
            self._swatch_loaded = True
            pass
    
    def _update_with_texture_color(self):
        """Update the swatch with texture image and colors (called asynchronously)."""
        import time as _time
        
        if not cmds.objExists(self.material_name):
            return
        
        try:
            # Recreate swatch with texture images and colors (not fast mode)
            texture_start = _time.perf_counter()
            pixmap = self._create_swatch_icon(
                self.material_name, 
                self.icon_size,
                fast_mode=False  # Load texture images and calculate texture colors
            )
            texture_duration = (_time.perf_counter() - texture_start) * 1000.0
            
            if pixmap and not pixmap.isNull():
                self._swatch_pixmap = pixmap
                # Force a repaint to show the updated swatch
                self.update()
                
                # Track texture color update timing for aggregate reporting
                try:
                    # Try to find the parent UI to track timing
                    parent = self.parent()
                    while parent:
                        if hasattr(parent, '_swatch_timing_texture_count'):
                            parent._swatch_timing_texture_count += 1
                            parent._swatch_timing_texture_total += texture_duration
                            break
                        parent = parent.parent() if hasattr(parent, 'parent') else None
                except:
                    pass
        except Exception as e:
            # Log errors for debugging (only first few)
            if not hasattr(self, '_update_error_logged'):
                self._update_error_logged = True
                import traceback
                print(f"[MaterialSwatchIcon] Error updating texture for {self.material_name}: {e}")
                print(f"[MaterialSwatchIcon] Traceback: {traceback.format_exc()}")
            # Silently fail - keep the fast mode swatch
            pass
    
    def _apply_circular_mask(self):
        """Apply a circular mask to the label so it displays as a circle."""
        try:
            # Create a circular mask
            mask = QtGui.QPixmap(self.icon_size, self.icon_size)
            mask.fill(QtCore.Qt.transparent)
            
            painter = QtGui.QPainter(mask)
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            painter.setBrush(QtCore.Qt.black)
            painter.setPen(QtCore.Qt.NoPen)
            painter.drawEllipse(0, 0, self.icon_size, self.icon_size)
            painter.end()
            
            # Apply the mask
            self.setMask(mask.mask())
        except Exception as e:
            print(f"[MaterialSwatchIcon] Failed to apply circular mask: {e}")
    
    def paintEvent(self, event):
        """Override paintEvent to draw the pixmap in a circular shape with background."""
        # Draw background circle first
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        
        # Draw background circle
        bg_color = QtGui.QColor(self._bg_color)
        painter.setBrush(QtGui.QBrush(bg_color))
        painter.setPen(QtCore.Qt.NoPen)
        painter.drawEllipse(0, 0, self.icon_size, self.icon_size)
        
        # Draw the pixmap if available
        if self._swatch_pixmap and not self._swatch_pixmap.isNull():
            # Create a circular clipping path
            path = QtGui.QPainterPath()
            path.addEllipse(0, 0, self.icon_size, self.icon_size)
            painter.setClipPath(path)
            
            # Draw the pixmap centered
            pixmap_rect = QtCore.QRect(
                (self.icon_size - self._swatch_pixmap.width()) // 2,
                (self.icon_size - self._swatch_pixmap.height()) // 2,
                self._swatch_pixmap.width(),
                self._swatch_pixmap.height()
            )
            painter.drawPixmap(pixmap_rect, self._swatch_pixmap)
        else:
            # Draw text if no pixmap
            painter.setPen(QtGui.QColor("#888888"))
            painter.setFont(self.font())
            painter.drawText(self.rect(), QtCore.Qt.AlignCenter, self.text())
        
        painter.end()
    
    def setSelectionHandler(self, owner_or_callable, handler_name_or_material, maybe_material=None):
        """Set the selection handler for click events.
        Compatible with the same API as LeftClipLineEdit and other material list widgets."""
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
        """Handle mouse clicks to select the material."""
        try:
            if e.button() == QtCore.Qt.RightButton:
                super(MaterialSwatchIcon, self).mousePressEvent(e)
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
                        handler(self._qm_material_name, source='swatch', shift=shift, ctrl=ctrl)
                        super(MaterialSwatchIcon, self).mousePressEvent(e)
                        return
            
            # Try bound handler pattern
            if self._bound_handler and self._qm_material_name:
                try:
                    self._bound_handler(self._qm_material_name, source='swatch', shift=shift, ctrl=ctrl)
                    super(MaterialSwatchIcon, self).mousePressEvent(e)
                    return
                except Exception:
                    pass
            
            # Default behavior
            super(MaterialSwatchIcon, self).mousePressEvent(e)
        except Exception:
            super(MaterialSwatchIcon, self).mousePressEvent(e)
    
    def _create_swatch_icon(self, material_name, size, fast_mode=False):
        """Create a small swatch icon for the material.
        
        Uses the same rendering logic as shader_swatch_viewer but at low resolution.
        Uses default display values for consistent appearance.
        
        OPTIMIZATION: Checks cache before generating to avoid redundant work.
        """
        import time as _time
        global _swatch_cache
        
        swatch_start = _time.perf_counter()
        
        # Check cache first (only for non-fast-mode, as fast mode is temporary)
        if not fast_mode:
            swatch_hash = _get_material_swatch_hash(material_name, fast_mode=False)
            if swatch_hash:
                cache_key = (material_name, swatch_hash, size)
                cached_pixmap = _swatch_cache.get(cache_key)
                if cached_pixmap and not cached_pixmap.isNull():
                    # Cache hit - return cached swatch
                    return cached_pixmap

        # Avoid "flat-then-textured" flicker for textured materials:
        # if fast_mode is requested but the material uses any key textures
        # (basecolor, emission, subsurface), immediately fall back to a full
        # non-fast render so we only ever show the textured version.
        if fast_mode:
            try:
                node_type_for_fast = cmds.nodeType(material_name)

                def _has_any_key_texture():
                    """Lightweight check for important texture connections."""
                    # Base color / color
                    if node_type_for_fast in ["standardSurface", "aiStandardSurface"]:
                        if cmds.attributeQuery("baseColor", node=material_name, exists=True):
                            color_attr = f"{material_name}.baseColor"
                            if cmds.listConnections(color_attr, s=True, d=False):
                                return True
                    elif node_type_for_fast in ["lambert", "blinn", "phong"]:
                        if cmds.attributeQuery("color", node=material_name, exists=True):
                            color_attr = f"{material_name}.color"
                            if cmds.listConnections(color_attr, s=True, d=False):
                                return True

                    # Emission / incandescence
                    if node_type_for_fast in ["standardSurface", "aiStandardSurface"]:
                        if cmds.attributeQuery("emissionColor", node=material_name, exists=True):
                            emission_attr = f"{material_name}.emissionColor"
                            if cmds.listConnections(emission_attr, s=True, d=False):
                                return True
                    elif node_type_for_fast in ["lambert", "blinn", "phong"]:
                        if cmds.attributeQuery("incandescence", node=material_name, exists=True):
                            inc_attr = f"{material_name}.incandescence"
                            if cmds.listConnections(inc_attr, s=True, d=False):
                                return True

                    # Subsurface color
                    if node_type_for_fast in ["standardSurface", "aiStandardSurface"]:
                        if cmds.attributeQuery("subsurfaceColor", node=material_name, exists=True):
                            subsurface_attr = f"{material_name}.subsurfaceColor"
                            if cmds.listConnections(subsurface_attr, s=True, d=False):
                                return True

                    return False

                if _has_any_key_texture():
                    # Re-run in non-fast mode so we don't draw an intermediate
                    # flat swatch before the textured one loads.
                    return self._create_swatch_icon(material_name, size, fast_mode=False)
            except Exception:
                # On any error, just proceed with the normal fast_mode path.
                pass
        
        # Detailed timing breakdown
        timings = {}
        
        try:
            # Get the material color (fast mode skips texture processing)
            color_start = _time.perf_counter()
            original_material_color = self._get_material_color(material_name, fast_mode=fast_mode)
            
            # Note: texture images will be loaded later after image_size is determined
            texture_image = None
            emission_texture_image = None
            subsurface_texture_image = None
            
            timings['color'] = (_time.perf_counter() - color_start) * 1000.0
            
            # Check for emission (standardSurface emission / legacy incandescence)
            emission_start = _time.perf_counter()
            emission_result = self._get_emission_data(material_name)
            if emission_result:
                emission_data, emission_intensity = emission_result
                has_emission = (
                    emission_data is not None
                    and any(x > 0.001 for x in emission_data[:3] if isinstance(x, (int, float)))
                )
            else:
                emission_data = None
                emission_intensity = 0.0
                has_emission = False
            timings['emission'] = (_time.perf_counter() - emission_start) * 1000.0
            
            # Apply metalness darkening
            metalness_start = _time.perf_counter()
            material_color_with_metalness = self._apply_metalness_darkening(material_name, original_material_color)
            material_color = material_color_with_metalness
            timings['metalness'] = (_time.perf_counter() - metalness_start) * 1000.0
            
            # Get node type early (needed for specular color and opacity checks)
            node_type = cmds.nodeType(material_name)
            
            # Get roughness value for specular highlight (handles textures)
            roughness_start = _time.perf_counter()
            roughness = self._get_material_roughness(material_name)
            timings['roughness'] = (_time.perf_counter() - roughness_start) * 1000.0
            
            # Get specular weight (handles textures / legacy shaders)
            specular_weight = 1.0  # Default to full specular
            # StandardSurface / aiStandardSurface: use "specular" attribute (and texture if present)
            if node_type in ["standardSurface", "aiStandardSurface"]:
                if cmds.attributeQuery("specular", node=material_name, exists=True):
                    try:
                        # Check if specular has a texture connection
                        specular_connections = cmds.listConnections(f"{material_name}.specular", s=True, d=False)
                        if specular_connections:
                            # Texture connected - average the texture value
                            specular_texture_node = specular_connections[0]
                            if cmds.nodeType(specular_texture_node) == "file":
                                specular_weight = self._get_texture_average_value(specular_texture_node)
                            else:
                                specular_weight = 0.5  # Default for non-file textures
                        else:
                            # Get specular weight value directly
                            specular_weight = float(cmds.getAttr(f"{material_name}.specular"))
                            specular_weight = max(0.0, min(1.0, specular_weight))
                    except:
                        pass
            # Legacy shaders: lambert / blinn / phong
            elif node_type in ["lambert", "blinn", "phong"]:
                try:
                    if node_type == "lambert":
                        # Lambert has no specular lobe – keep specular effectively off
                        specular_weight = 0.0
                    else:
                        # For blinn/phong, derive a specular "weight" from specularColor
                        # brightness and, for blinn, specularRollOff. This makes all
                        # specular elements behave similarly to standardSurface.
                        spec_color = None
                        if cmds.attributeQuery("specularColor", node=material_name, exists=True):
                            spec_attr = cmds.getAttr(f"{material_name}.specularColor")[0]
                            if isinstance(spec_attr, (list, tuple)) and len(spec_attr) >= 3:
                                if isinstance(spec_attr[0], (list, tuple)):
                                    spec_color = tuple(float(x) for x in spec_attr[0][:3])
                                else:
                                    spec_color = tuple(float(x) for x in spec_attr[:3])

                        # Default brightness if no color is set
                        brightness = 1.0
                        if spec_color:
                            sr, sg, sb = spec_color
                            brightness = max(0.0, min(1.0, max(sr, sg, sb)))

                        rolloff = 1.0
                        if node_type == "blinn" and cmds.attributeQuery("specularRollOff", node=material_name, exists=True):
                            try:
                                rolloff = float(cmds.getAttr(f"{material_name}.specularRollOff"))
                                rolloff = max(0.0, min(1.0, rolloff))
                            except:
                                rolloff = 1.0

                        # Combine brightness and rolloff into a 0–1 specular_weight
                        specular_weight = max(0.0, min(1.0, brightness * rolloff))
                except:
                    # On any error, leave specular_weight at its default
                    pass
            
            # Get specular color (if available)
            specular_color = (1.0, 1.0, 1.0)  # Default to white
            if node_type in ["standardSurface", "aiStandardSurface"]:
                if cmds.attributeQuery("specularColor", node=material_name, exists=True):
                    try:
                        specular_color_attr = cmds.getAttr(f"{material_name}.specularColor")[0]
                        if isinstance(specular_color_attr, (list, tuple)) and len(specular_color_attr) >= 3:
                            if isinstance(specular_color_attr[0], (list, tuple)):
                                specular_color = tuple(float(x) for x in specular_color_attr[0][:3])
                            else:
                                specular_color = tuple(float(x) for x in specular_color_attr[:3])
                    except:
                        pass
            elif node_type in ["blinn", "phong"]:
                # Legacy shaders: use their specularColor as the specular tint so
                # highlights behave like on a standardSurface.
                if cmds.attributeQuery("specularColor", node=material_name, exists=True):
                    try:
                        specular_color_attr = cmds.getAttr(f"{material_name}.specularColor")[0]
                        if isinstance(specular_color_attr, (list, tuple)) and len(specular_color_attr) >= 3:
                            if isinstance(specular_color_attr[0], (list, tuple)):
                                specular_color = tuple(float(x) for x in specular_color_attr[0][:3])
                            else:
                                specular_color = tuple(float(x) for x in specular_color_attr[:3])
                    except:
                        pass

            # Get ambient color for legacy shaders (lambert/blinn/phong)
            ambient_color = None
            if node_type in ["lambert", "blinn", "phong"]:
                try:
                    if cmds.attributeQuery("ambientColor", node=material_name, exists=True):
                        ambient_attr = cmds.getAttr(f"{material_name}.ambientColor")[0]
                        if isinstance(ambient_attr, (list, tuple)) and len(ambient_attr) >= 3:
                            if isinstance(ambient_attr[0], (list, tuple)):
                                ambient_color = tuple(float(x) for x in ambient_attr[0][:3])
                            else:
                                ambient_color = tuple(float(x) for x in ambient_attr[:3])
                except Exception:
                    ambient_color = None
            
            # Get coat attributes (standardSurface/aiStandardSurface only)
            coat_color = (1.0, 1.0, 1.0)  # Default to white
            coat_roughness = None
            coat_weight = 0.0  # Default to no coat
            if node_type in ["standardSurface", "aiStandardSurface"]:
                # Get coat color (with texture support)
                if cmds.attributeQuery("coatColor", node=material_name, exists=True):
                    try:
                        # Check if coatColor has a texture connection
                        coat_color_connections = cmds.listConnections(f"{material_name}.coatColor", s=True, d=False)
                        if coat_color_connections:
                            # Texture connected - get average color from texture
                            coat_color_texture_node = coat_color_connections[0]
                            if not fast_mode:
                                texture_color = self._get_texture_average_color(coat_color_texture_node, f"{material_name}.coatColor")
                                if texture_color:
                                    coat_color = texture_color
                                else:
                                    # Fallback to default if texture color extraction fails
                                    coat_color = (1.0, 1.0, 1.0)
                            else:
                                # Fast mode: use default white
                                coat_color = (1.0, 1.0, 1.0)
                        else:
                            # No texture - get color value directly
                            coat_color_attr = cmds.getAttr(f"{material_name}.coatColor")[0]
                            if isinstance(coat_color_attr, (list, tuple)) and len(coat_color_attr) >= 3:
                                if isinstance(coat_color_attr[0], (list, tuple)):
                                    coat_color = tuple(float(x) for x in coat_color_attr[0][:3])
                                else:
                                    coat_color = tuple(float(x) for x in coat_color_attr[:3])
                    except:
                        pass
                
                # Get coat roughness
                if cmds.attributeQuery("coatRoughness", node=material_name, exists=True):
                    try:
                        # Check if coatRoughness has a texture connection
                        coat_roughness_connections = cmds.listConnections(f"{material_name}.coatRoughness", s=True, d=False)
                        if coat_roughness_connections:
                            # Texture connected - average the texture value
                            coat_roughness_texture_node = coat_roughness_connections[0]
                            if cmds.nodeType(coat_roughness_texture_node) == "file":
                                coat_roughness = self._get_texture_average_value(coat_roughness_texture_node)
                            else:
                                coat_roughness = 0.5  # Default for non-file textures
                        else:
                            # Get coat roughness value directly
                            coat_roughness = float(cmds.getAttr(f"{material_name}.coatRoughness"))
                            coat_roughness = max(0.0, min(1.0, coat_roughness))
                    except:
                        pass
                
                # Get coat weight
                if cmds.attributeQuery("coat", node=material_name, exists=True):
                    try:
                        # Check if coat has a texture connection
                        coat_connections = cmds.listConnections(f"{material_name}.coat", s=True, d=False)
                        if coat_connections:
                            # Texture connected - average the texture value
                            coat_texture_node = coat_connections[0]
                            if cmds.nodeType(coat_texture_node) == "file":
                                coat_weight = self._get_texture_average_value(coat_texture_node)
                            else:
                                coat_weight = 0.5  # Default for non-file textures
                        else:
                            # Get coat weight value directly
                            coat_weight = float(cmds.getAttr(f"{material_name}.coat"))
                            coat_weight = max(0.0, min(1.0, coat_weight))
                    except:
                        pass
            
            # Get sheen attributes (standardSurface/aiStandardSurface only)
            sheen_color = (0.0, 0.0, 0.0)  # Default to black (no sheen)
            sheen_weight = 0.0  # Default to no sheen
            sheen_roughness = None  # Default to no sheen roughness
            if node_type in ["standardSurface", "aiStandardSurface"]:
                # Get sheen color (with texture support)
                if cmds.attributeQuery("sheenColor", node=material_name, exists=True):
                    try:
                        # Check if sheenColor has a texture connection
                        sheen_color_connections = cmds.listConnections(f"{material_name}.sheenColor", s=True, d=False)
                        if sheen_color_connections:
                            # Texture connected - get average color from texture
                            sheen_color_texture_node = sheen_color_connections[0]
                            if not fast_mode:
                                texture_color = self._get_texture_average_color(sheen_color_texture_node, f"{material_name}.sheenColor")
                                if texture_color:
                                    sheen_color = texture_color
                                else:
                                    # Fallback to default if texture color extraction fails
                                    sheen_color = (0.0, 0.0, 0.0)
                            else:
                                # Fast mode: use default black
                                sheen_color = (0.0, 0.0, 0.0)
                        else:
                            # No texture - get color value directly
                            sheen_color_attr = cmds.getAttr(f"{material_name}.sheenColor")[0]
                            if isinstance(sheen_color_attr, (list, tuple)) and len(sheen_color_attr) >= 3:
                                if isinstance(sheen_color_attr[0], (list, tuple)):
                                    sheen_color = tuple(float(x) for x in sheen_color_attr[0][:3])
                                else:
                                    sheen_color = tuple(float(x) for x in sheen_color_attr[:3])
                    except:
                        pass
                
                # Get sheen weight
                if cmds.attributeQuery("sheen", node=material_name, exists=True):
                    try:
                        # Check if sheen has a texture connection
                        sheen_connections = cmds.listConnections(f"{material_name}.sheen", s=True, d=False)
                        if sheen_connections:
                            # Texture connected - average the texture value
                            sheen_texture_node = sheen_connections[0]
                            if cmds.nodeType(sheen_texture_node) == "file":
                                sheen_weight = self._get_texture_average_value(sheen_texture_node)
                            else:
                                sheen_weight = 0.0  # Default for non-file textures
                        else:
                            # Get sheen weight value directly
                            sheen_weight = float(cmds.getAttr(f"{material_name}.sheen"))
                            sheen_weight = max(0.0, min(1.0, sheen_weight))
                    except:
                        pass
                
                # Get sheen roughness
                if cmds.attributeQuery("sheenRoughness", node=material_name, exists=True):
                    try:
                        # Check if sheenRoughness has a texture connection
                        sheen_roughness_connections = cmds.listConnections(f"{material_name}.sheenRoughness", s=True, d=False)
                        if sheen_roughness_connections:
                            # Texture connected - average the texture value
                            sheen_roughness_texture_node = sheen_roughness_connections[0]
                            if cmds.nodeType(sheen_roughness_texture_node) == "file":
                                sheen_roughness = self._get_texture_average_value(sheen_roughness_texture_node)
                            else:
                                sheen_roughness = 0.5  # Default for non-file textures
                        else:
                            # Get sheen roughness value directly
                            sheen_roughness = float(cmds.getAttr(f"{material_name}.sheenRoughness"))
                            sheen_roughness = max(0.0, min(1.0, sheen_roughness))
                    except:
                        pass
            
            # Apply base-color–driven desaturation to the specular color.
            # Brighter base colors get whiter speculars, but with a lower max desaturation,
            # and the desaturation ramps up more quickly at lower base values.
            try:
                if material_color and isinstance(material_color, (list, tuple)) and len(material_color) >= 3:
                    base_r, base_g, base_b = material_color[:3]
                    base_value = max(0.0, min(1.0, max(float(base_r), float(base_g), float(base_b))))
                    # Desaturation amount:
                    #   - 0 at black base
                    #   - ~max_white_blend at white base
                    #   - Ramps up faster for mid/low base values using a sqrt curve
                    max_white_blend = 0.35  # Lower maximum desaturation (35%)
                    curve_value = base_value ** 0.5  # > base_value for 0 < v < 1 → earlier desaturation
                    white_blend = max_white_blend * curve_value
                    if white_blend > 0.0:
                        specular_color = self._desaturate_specular_color(specular_color, white_blend=white_blend)
            except Exception:
                # Fail-safe: keep original specular_color if anything goes wrong
                pass
            
            # Get opacity/transmission/transparency values
            opacity_start = _time.perf_counter()
            opacity = 1.0
            opacity_color = (1.0, 1.0, 1.0)
            transmission = 0.0
            transmission_color = (1.0, 1.0, 1.0)
            transparency = 0.0
            transparency_color = (0.0, 0.0, 0.0)
            # node_type already assigned earlier
            
            if node_type in ["standardSurface", "aiStandardSurface"]:
                if cmds.attributeQuery("opacity", node=material_name, exists=True):
                    try:
                        opacity_attr = cmds.getAttr(f"{material_name}.opacity")[0]
                        if isinstance(opacity_attr, (list, tuple)) and len(opacity_attr) >= 3:
                            if isinstance(opacity_attr[0], (list, tuple)):
                                opacity_color = tuple(float(x) for x in opacity_attr[0][:3])
                                opacity = sum(opacity_color) / 3.0
                            else:
                                opacity_color = tuple(float(x) for x in opacity_attr[:3])
                                opacity = sum(opacity_color) / 3.0
                    except:
                        pass
                
                if cmds.attributeQuery("transmission", node=material_name, exists=True):
                    try:
                        transmission = float(cmds.getAttr(f"{material_name}.transmission"))
                    except:
                        pass
                
                if cmds.attributeQuery("transmissionColor", node=material_name, exists=True):
                    try:
                        transmission_color_attr = cmds.getAttr(f"{material_name}.transmissionColor")[0]
                        if isinstance(transmission_color_attr, (list, tuple)) and len(transmission_color_attr) >= 3:
                            if isinstance(transmission_color_attr[0], (list, tuple)):
                                transmission_color = tuple(float(x) for x in transmission_color_attr[0][:3])
                            else:
                                transmission_color = tuple(float(x) for x in transmission_color_attr[:3])
                    except:
                        pass
            else:
                if cmds.attributeQuery("transparency", node=material_name, exists=True):
                    try:
                        transparency_attr = cmds.getAttr(f"{material_name}.transparency")[0]
                        if isinstance(transparency_attr, (list, tuple)) and len(transparency_attr) >= 3:
                            if isinstance(transparency_attr[0], (list, tuple)):
                                transparency_color = tuple(float(x) for x in transparency_attr[0][:3])
                                transparency = sum(transparency_color) / 3.0
                            else:
                                transparency_color = tuple(float(x) for x in transparency_attr[:3])
                                transparency = sum(transparency_color) / 3.0
                            opacity = 1.0 - transparency
                            opacity_color = (1.0 - transparency_color[0], 1.0 - transparency_color[1], 1.0 - transparency_color[2])
                    except:
                        pass
            
            timings['opacity'] = (_time.perf_counter() - opacity_start) * 1000.0
            
            # Calculate effective opacity
            calc_start = _time.perf_counter()
            effective_opacity = opacity * (1.0 - transmission * 0.8)
            
            # Calculate transparency factor
            if transparency > 0.001:
                transparency_factor = transparency
            else:
                transmission_power = transmission ** 1.2
                opacity_contribution = 1.0 - opacity
                transparency_factor = max(transmission_power, opacity_contribution)
                transparency_factor = max(0.0, min(1.0, transparency_factor))
            
            # Use default display values
            gradient_start_ratio = self.DEFAULT_GRADIENT_START_RATIO
            gradient_value_inside = self.DEFAULT_GRADIENT_VALUE_INSIDE
            gradient_value_outside = self.DEFAULT_GRADIENT_VALUE_OUTSIDE + (0.9 - 0.6) * transparency_factor
            gradient_exponent = self.DEFAULT_GRADIENT_EXPONENT
            # Make specular softness vary with roughness - higher roughness = softer highlight
            # Roughness 0.0 = softness 1.0 (sharp), roughness 1.0 = softness 8.0 (very soft)
            # Use steeper power curve to make it softer much faster - soft blur by 0.75 roughness
            if roughness is not None:
                # Use steeper power curve (roughness^2.5) to accelerate softness increase much faster
                # Increased range from 1.0-4.0 to 1.0-8.0 to make highlights much softer at high roughness
                specular_softness = self.DEFAULT_SPECULAR_SOFTNESS + (roughness ** 2.5 * 7.0)  # Range: 1.0 to 8.0, much softer at high roughness
            else:
                specular_softness = self.DEFAULT_SPECULAR_SOFTNESS
            specular_size_multiplier = self.DEFAULT_SPECULAR_SIZE_MULTIPLIER
            specular_position_offset = self.DEFAULT_SPECULAR_POSITION_OFFSET
            transparency_bg_brightness = self.DEFAULT_TRANSPARENCY_BG_BRIGHTNESS
            # Make the swatch background a bit darker when the material is fully
            # transparent / transmissive so it doesn't disappear completely,
            # but keep the change very subtle.
            try:
                # Consider "full" when the effective opacity is very low or transmission very high.
                if effective_opacity < 0.1 or transmission > 0.9 or transparency_factor > 0.9:
                    # Darken by a small amount (0.07) towards a darker grey (but keep within 0..1)
                    transparency_bg_brightness = max(0.0, min(1.0, transparency_bg_brightness - 0.07))
            except Exception:
                # Fail-safe: keep original brightness if anything goes wrong.
                pass
            timings['calc'] = (_time.perf_counter() - calc_start) * 1000.0
            
            # Create low-resolution image for performance
            # Use small size for icons (32-64px is sufficient for 22px display)
            image_start = _time.perf_counter()
            image_size = max(32, min(size * 2, 64))
            
            # Try to get texture images for base, emission, and subsurface layers
            emission_texture_image = None
            subsurface_texture_image = None
            
            if not fast_mode:
                try:
                    # node_type already assigned earlier
                    
                    # Get base color texture
                    color_attr = None
                    if node_type in ["standardSurface", "aiStandardSurface"]:
                        if cmds.attributeQuery("baseColor", node=material_name, exists=True):
                            color_attr = f"{material_name}.baseColor"
                    elif node_type in ["lambert", "blinn", "phong"]:
                        if cmds.attributeQuery("color", node=material_name, exists=True):
                            color_attr = f"{material_name}.color"
                    
                    if color_attr:
                        connections = cmds.listConnections(color_attr, s=True, d=False)
                        if connections:
                            # Check if directly connected node is a file texture
                            texture_node = connections[0]
                            texture_node_type = cmds.nodeType(texture_node)
                            if texture_node_type == "file":
                                texture_image = self._get_file_texture_image(texture_node, size=image_size)
                            else:
                                # Traverse connection chain to find file texture node
                                file_texture_node = self._find_file_texture_node(texture_node)
                                if file_texture_node:
                                    texture_image = self._get_file_texture_image(file_texture_node, size=image_size)
                    
                    # Get emission texture (if emissionColor has texture connection)
                    if node_type in ["standardSurface", "aiStandardSurface"]:
                        if cmds.attributeQuery("emissionColor", node=material_name, exists=True):
                            try:
                                emission_connections = cmds.listConnections(f"{material_name}.emissionColor", s=True, d=False)
                                if emission_connections:
                                    emission_texture_node = emission_connections[0]
                                    emission_node_type = cmds.nodeType(emission_texture_node)
                                    
                                    if emission_node_type == "file":
                                        emission_texture_image = self._get_file_texture_image(emission_texture_node, size=image_size)
                                    else:
                                        # Traverse connection chain to find file texture node
                                        file_texture_node = self._find_file_texture_node(emission_texture_node)
                                        if file_texture_node:
                                            emission_texture_image = self._get_file_texture_image(file_texture_node, size=image_size)
                            except Exception as e:
                                pass
                    elif node_type in ["lambert", "blinn", "phong"]:
                        if cmds.attributeQuery("incandescence", node=material_name, exists=True):
                            try:
                                incandescence_connections = cmds.listConnections(f"{material_name}.incandescence", s=True, d=False)
                                if incandescence_connections:
                                    emission_texture_node = incandescence_connections[0]
                                    emission_node_type = cmds.nodeType(emission_texture_node)
                                    if emission_node_type == "file":
                                        emission_texture_image = self._get_file_texture_image(emission_texture_node, size=image_size)
                                    else:
                                        # Traverse connection chain to find file texture node
                                        file_texture_node = self._find_file_texture_node(emission_texture_node)
                                        if file_texture_node:
                                            emission_texture_image = self._get_file_texture_image(file_texture_node, size=image_size)
                            except:
                                pass
                    
                    # Get subsurface color texture (standardSurface only)
                    if node_type in ["standardSurface", "aiStandardSurface"]:
                        if cmds.attributeQuery("subsurfaceColor", node=material_name, exists=True):
                            try:
                                subsurface_connections = cmds.listConnections(f"{material_name}.subsurfaceColor", s=True, d=False)
                                if subsurface_connections:
                                    subsurface_texture_node = subsurface_connections[0]
                                    subsurface_node_type = cmds.nodeType(subsurface_texture_node)
                                    
                                    if subsurface_node_type == "file":
                                        subsurface_texture_image = self._get_file_texture_image(subsurface_texture_node, size=image_size)
                                    else:
                                        # Traverse connection chain to find file texture node
                                        file_texture_node = self._find_file_texture_node(subsurface_texture_node)
                                        if file_texture_node:
                                            subsurface_texture_image = self._get_file_texture_image(file_texture_node, size=image_size)
                            except Exception as e:
                                pass
                except Exception as e:
                    # Debug: log errors (only first few)
                    if not hasattr(self, '_texture_error_log_counter'):
                        self._texture_error_log_counter = 0
                    self._texture_error_log_counter += 1
                    pass
            
            # Create QImage with transparent background
            image = QtGui.QImage(image_size, image_size, QtGui.QImage.Format_ARGB32)
            image.fill(QtCore.Qt.transparent)
            
            # Create QPainter
            painter = QtGui.QPainter(image)
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            timings['image_init'] = (_time.perf_counter() - image_start) * 1000.0
            
            # Set up drawing parameters
            setup_start = _time.perf_counter()
            margin = 2  # Smaller margin for small icons
            circle_diameter = image_size - (margin * 2)
            circle_rect = QtCore.QRect(margin, margin, circle_diameter, circle_diameter)
            
            # Create radial gradient
            center_x = margin + (circle_diameter // 2)
            center_y = margin + (circle_diameter // 2)
            radius = circle_diameter // 2
            gradient = QtGui.QRadialGradient(center_x, center_y, radius)
            
            # Background color for transparency blending
            bg_brightness = transparency_bg_brightness
            transparency_tint_color = None
            is_black_color = False
            max_tint_strength = 0.25
            timings['setup'] = (_time.perf_counter() - setup_start) * 1000.0
            
            if node_type in ["standardSurface", "aiStandardSurface"]:
                if transmission > 0.001:
                    transmission_is_black = all(c < 0.01 for c in transmission_color)
                    transmission_is_white = all(c > 0.99 for c in transmission_color)
                    if transmission_is_black:
                        is_black_color = True
                    elif not transmission_is_white:
                        transparency_tint_color = transmission_color
                elif opacity < 0.999:
                    if opacity > 0.001:
                        opacity_is_black = all(c < 0.01 for c in opacity_color)
                        opacity_is_white = all(c > 0.99 for c in opacity_color)
                        if not opacity_is_black and not opacity_is_white:
                            transparency_tint_color = opacity_color
            else:
                if transparency > 0.001 and transparency < 0.999:
                    transparency_is_black = all(c < 0.01 for c in transparency_color)
                    transparency_is_white = all(c > 0.99 for c in transparency_color)
                    if not transparency_is_black and not transparency_is_white:
                        transparency_tint_color = transparency_color
            
            grey_base = bg_brightness
            
            if is_black_color:
                darken_factor = 0.8
                bg_color_value = int(grey_base * darken_factor * 255)
                bg_color = QtGui.QColor(bg_color_value, bg_color_value, bg_color_value)
            elif transparency_tint_color:
                tint_r, tint_g, tint_b = transparency_tint_color
                min_channel = min(tint_r, tint_g, tint_b)
                max_channel = max(tint_r, tint_g, tint_b)
                saturation = max_channel - min_channel if max_channel > 0.001 else 0.0
                tint_strength = transparency_factor * saturation * max_tint_strength
                bg_r = grey_base * (1.0 - tint_strength) + tint_r * tint_strength
                bg_g = grey_base * (1.0 - tint_strength) + tint_g * tint_strength
                bg_b = grey_base * (1.0 - tint_strength) + tint_b * tint_strength
                bg_color = QtGui.QColor(int(bg_r * 255), int(bg_g * 255), int(bg_b * 255))
            else:
                bg_color_value = int(grey_base * 255)
                bg_color = QtGui.QColor(bg_color_value, bg_color_value, bg_color_value)
            
            # Blend material colors with background
            blend_start = _time.perf_counter()
            blend_factor = 1.0 - transparency_factor
            bg_blend_r = material_color[0] * blend_factor + (bg_color.red() / 255.0) * (1.0 - blend_factor)
            bg_blend_g = material_color[1] * blend_factor + (bg_color.green() / 255.0) * (1.0 - blend_factor)
            bg_blend_b = material_color[2] * blend_factor + (bg_color.blue() / 255.0) * (1.0 - blend_factor)
            material_color = (bg_blend_r, bg_blend_g, bg_blend_b)
            
            bg_blend_orig_r = original_material_color[0] * blend_factor + (bg_color.red() / 255.0) * (1.0 - blend_factor)
            bg_blend_orig_g = original_material_color[1] * blend_factor + (bg_color.green() / 255.0) * (1.0 - blend_factor)
            bg_blend_orig_b = original_material_color[2] * blend_factor + (bg_color.blue() / 255.0) * (1.0 - blend_factor)
            original_material_color = (bg_blend_orig_r, bg_blend_orig_g, bg_blend_orig_b)

            # Apply ambientColor as an additive tint on top of the base color
            # for legacy shaders. We cap its contribution so it adds at most
            # ~50% of its color on top of the underlying base/texture.
            if ambient_color is not None:
                try:
                    amb_r, amb_g, amb_b = ambient_color
                    amb_scale = 0.5  # 50% max contribution

                    def _add_ambient(base_r, base_g, base_b):
                        out_r = max(0.0, min(1.0, base_r + amb_r * amb_scale))
                        out_g = max(0.0, min(1.0, base_g + amb_g * amb_scale))
                        out_b = max(0.0, min(1.0, base_b + amb_b * amb_scale))
                        return (out_r, out_g, out_b)

                    material_color = _add_ambient(*material_color)
                    original_material_color = _add_ambient(*original_material_color)
                except Exception:
                    pass
            
            center_color = QtGui.QColor(
                int(material_color[0] * 255),
                int(material_color[1] * 255),
                int(material_color[2] * 255)
            )
            timings['blend'] = (_time.perf_counter() - blend_start) * 1000.0
            
            # Draw base circle with texture or gradient
            draw_circle_start = _time.perf_counter()
            
            # Debug: log texture usage (only first few or every 50th)
            if not hasattr(self, '_texture_usage_log_counter'):
                self._texture_usage_log_counter = 0
            self._texture_usage_log_counter += 1
            if self._texture_usage_log_counter <= 5 or self._texture_usage_log_counter % 50 == 0:
                textures_used = []
                if texture_image and not texture_image.isNull():
                    textures_used.append("base")
                if emission_texture_image and not emission_texture_image.isNull():
                    textures_used.append("emission")
                if subsurface_texture_image and not subsurface_texture_image.isNull():
                    textures_used.append("subsurface")
            
            if texture_image and not texture_image.isNull():
                # Use texture image as base layer
                # Create a circular clipping path
                circle_path = QtGui.QPainterPath()
                circle_path.addEllipse(circle_rect)
                painter.setClipPath(circle_path)
                
                # Draw texture image centered and scaled to fit
                # Calculate scaling to fill the circle while maintaining aspect ratio
                texture_width = texture_image.width()
                texture_height = texture_image.height()
                circle_w = circle_diameter
                circle_h = circle_diameter
                
                # Scale to cover the circle (may crop edges)
                scale_w = circle_w / texture_width if texture_width > 0 else 1.0
                scale_h = circle_h / texture_height if texture_height > 0 else 1.0
                scale = max(scale_w, scale_h)  # Use larger scale to ensure coverage
                
                scaled_w = int(texture_width * scale)
                scaled_h = int(texture_height * scale)
                
                # Center the texture
                offset_x = center_x - (scaled_w // 2)
                offset_y = center_y - (scaled_h // 2)
                
                texture_rect = QtCore.QRect(offset_x, offset_y, scaled_w, scaled_h)
                painter.drawPixmap(texture_rect, texture_image)
                
                painter.setClipping(False)
                
                # Apply darkening gradient overlay for depth (similar to original gradient effect)
                # Create a subtle radial gradient that darkens the edges
                overlay_gradient = QtGui.QRadialGradient(center_x, center_y, radius)
                overlay_gradient.setColorAt(0.0, QtGui.QColor(0, 0, 0, 0))  # Transparent center
                overlay_gradient.setColorAt(gradient_start_ratio, QtGui.QColor(0, 0, 0, 0))
                
                # Darken edges based on gradient settings
                num_stops = 8
                gradient_range = 1.0 - gradient_start_ratio
                exp_exponent = max(0.1, float(gradient_exponent))
                
                for i in range(num_stops + 1):
                    linear_t = float(i) / num_stops
                    stop_ratio = gradient_start_ratio + (gradient_range * linear_t)
                    
                    # Calculate darkening factor (same logic as original gradient)
                    if linear_t <= 0.5:
                        smooth_t = linear_t * 2.0
                    else:
                        smooth_t = 1.0
                    
                    bright_start = gradient_value_inside
                    bright_end = gradient_value_outside
                    bright_range = bright_start - bright_end
                    brightness_factor = bright_start - (bright_range * smooth_t)
                    
                    # Darken based on brightness factor (inverse - lower brightness = more darkening)
                    darken_alpha = int((1.0 - brightness_factor) * 60)  # Max 60 alpha for subtle effect
                    overlay_gradient.setColorAt(stop_ratio, QtGui.QColor(0, 0, 0, darken_alpha))
                
                # Draw overlay gradient
                painter.setBrush(QtGui.QBrush(overlay_gradient))
                painter.setPen(QtCore.Qt.NoPen)
                painter.setCompositionMode(QtGui.QPainter.CompositionMode_Multiply)
                painter.drawEllipse(circle_rect)
                painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)

                # Apply metalness contrast increase to texture (same as base color)
                try:
                    metalness = None
                    if node_type in ["standardSurface", "aiStandardSurface"]:
                        if cmds.attributeQuery("metalness", node=material_name, exists=True):
                            try:
                                metalness = cmds.getAttr(f"{material_name}.metalness")
                                if metalness is not None:
                                    metalness = float(metalness)
                            except:
                                pass
                    
                    if metalness is not None and metalness > 0:
                        # Apply contrast increase instead of just darkening
                        # Use Overlay mode which naturally increases contrast
                        # Combined with slight multiply for minimal darkening
                        circle_path = QtGui.QPainterPath()
                        circle_path.addEllipse(circle_rect)
                        painter.setClipPath(circle_path)
                        
                        # First: darkening (45% at max metalness, increased by 50%)
                        slight_darken_factor = 1.0 - (metalness * 0.45)  # 45% darkening (increased from 30%)
                        darken_value = int(slight_darken_factor * 255)
                        metalness_darken_overlay = QtGui.QColor(darken_value, darken_value, darken_value, 255)
                        painter.setBrush(QtGui.QBrush(metalness_darken_overlay))
                        painter.setPen(QtCore.Qt.NoPen)
                        painter.setCompositionMode(QtGui.QPainter.CompositionMode_Multiply)
                        painter.drawEllipse(circle_rect)
                        
                        # Second: increase contrast using Overlay mode
                        # Overlay mode increases contrast: values < 0.5 get darker, values > 0.5 get brighter
                        # Use a grey overlay that pushes toward contrast
                        # At metalness 1.0, use a stronger overlay effect (increased by 50%)
                        overlay_strength = int(min(255, metalness * 270))  # Max 270 alpha (increased from 180, clamped to 255)
                        # Use a mid-grey (128) for overlay - this creates contrast when using Overlay mode
                        metalness_contrast_overlay = QtGui.QColor(128, 128, 128, overlay_strength)
                        painter.setBrush(QtGui.QBrush(metalness_contrast_overlay))
                        painter.setCompositionMode(QtGui.QPainter.CompositionMode_Overlay)
                        painter.drawEllipse(circle_rect)
                        
                        painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
                        painter.setClipping(False)
                except Exception:
                    pass

                # If ambientColor is present (legacy shaders), overlay it additively on top of the
                # basecolor texture as well so the behavior matches the flat-color case.
                if ambient_color is not None:
                    try:
                        amb_r, amb_g, amb_b = ambient_color
                        amb_scale = 0.5  # 50% max contribution
                        amb_color = QtGui.QColor(
                            int(max(0.0, min(1.0, amb_r * amb_scale)) * 255),
                            int(max(0.0, min(1.0, amb_g * amb_scale)) * 255),
                            int(max(0.0, min(1.0, amb_b * amb_scale)) * 255),
                            int(amb_scale * 255),
                        )
                        circle_path = QtGui.QPainterPath()
                        circle_path.addEllipse(circle_rect)
                        painter.setClipPath(circle_path)
                        painter.setBrush(QtGui.QBrush(amb_color))
                        painter.setPen(QtCore.Qt.NoPen)
                        # Use additive blend so ambient truly adds on top of the texture
                        painter.setCompositionMode(QtGui.QPainter.CompositionMode_Plus)
                        painter.drawEllipse(circle_rect)
                        painter.setClipping(False)
                    except Exception:
                        pass

                # When a basecolor texture is present, still apply the same grey overlay
                # driven by opacity / transmission so textured materials appear transparent
                # in the same way as flat-colored ones.
                if transparency_factor > 0.001:
                    overlay_alpha = int(max(0.0, min(1.0, transparency_factor)) * 255)
                    if overlay_alpha > 0:
                        circle_path = QtGui.QPainterPath()
                        circle_path.addEllipse(circle_rect)
                        painter.setClipPath(circle_path)
                        grey_overlay = QtGui.QColor(
                            bg_color.red(), bg_color.green(), bg_color.blue(), overlay_alpha
                        )
                        painter.setBrush(QtGui.QBrush(grey_overlay))
                        painter.setPen(QtCore.Qt.NoPen)
                        painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
                        painter.drawEllipse(circle_rect)
                        painter.setClipping(False)
            else:
                # Fallback to original gradient approach
                gradient_start = _time.perf_counter()
                gradient.setColorAt(0.0, center_color)
                gradient.setColorAt(gradient_start_ratio, center_color)
                
                # Create smooth gradient stops
                num_stops = 8  # Fewer stops for small icons
                stops = []
                gradient_range = 1.0 - gradient_start_ratio
                exp_exponent = max(0.1, float(gradient_exponent))
                
                for i in range(num_stops + 1):
                    linear_t = float(i) / num_stops
                    
                    # Simplified curve calculation for performance
                    log_min = math.log(1.0)
                    log_max = math.log(1000.0)
                    log_exp = math.log(max(1.0, exp_exponent))
                    log_normalized = (log_exp - log_min) / (log_max - log_min)
                    midpoint_ratio = 0.1 + log_normalized * 0.85
                    midpoint_ratio = max(0.05, min(0.95, midpoint_ratio))
                    
                    if linear_t <= midpoint_ratio:
                        if midpoint_ratio > 0.001:
                            normalized_t = linear_t / midpoint_ratio
                            log_exp = math.log(max(1.0, exp_exponent))
                            log_normalized = (log_exp - log_min) / (log_max - log_min)
                            power = 3.0 - log_normalized * 2.7
                            power = max(0.2, min(4.0, power))
                            curve_t = normalized_t ** power
                            smooth_t = curve_t * 0.5
                        else:
                            smooth_t = 0.0
                    else:
                        range_after = 1.0 - midpoint_ratio
                        if range_after > 0.001:
                            normalized_t = (linear_t - midpoint_ratio) / range_after
                            log_exp = math.log(max(1.0, exp_exponent))
                            log_normalized = (log_exp - log_min) / (log_max - log_min)
                            power = 3.0 - log_normalized * 2.7
                            power = max(0.2, min(4.0, power))
                            curve_t = normalized_t ** power
                            smooth_t = 0.5 + (curve_t * 0.5)
                        else:
                            smooth_t = 1.0
                    
                    if i == 0:
                        smooth_t = 0.0
                    elif i == num_stops:
                        smooth_t = 1.0
                    
                    stop_ratio = gradient_start_ratio + (gradient_range * linear_t)
                    bright_start = gradient_value_inside
                    bright_end = gradient_value_outside
                    bright_range = bright_start - bright_end
                    brightness_factor = bright_start - (bright_range * smooth_t)
                    
                    edge_r = material_color[0]
                    edge_g = material_color[1]
                    edge_b = material_color[2]
                    
                    edge_r_final = edge_r * brightness_factor
                    edge_g_final = edge_g * brightness_factor
                    edge_b_final = edge_b * brightness_factor
                    
                    edge_color = QtGui.QColor(
                        int(edge_r_final * 255),
                        int(edge_g_final * 255),
                        int(edge_b_final * 255)
                    )
                    stops.append((stop_ratio, edge_color))
                
                for stop_ratio, stop_color in stops:
                    if stop_ratio > gradient_start_ratio:
                        gradient.setColorAt(stop_ratio, stop_color)
                timings['gradient'] = (_time.perf_counter() - gradient_start) * 1000.0
                
                painter.setBrush(QtGui.QBrush(gradient))
                painter.setPen(QtCore.Qt.NoPen)
                painter.drawEllipse(circle_rect)
            
            timings['draw_circle'] = (_time.perf_counter() - draw_circle_start) * 1000.0
            
            # Draw subsurface color texture overlay (before emission, as base tint)
            subsurface_draw_start = _time.perf_counter()
            
            # Get subsurface weight and color
            subsurface_weight = 0.0
            subsurface_color = None
            if node_type in ["standardSurface", "aiStandardSurface"]:
                if cmds.attributeQuery("subsurface", node=material_name, exists=True):
                    try:
                        # Check if subsurface has a texture connection
                        subsurface_connections = cmds.listConnections(f"{material_name}.subsurface", s=True, d=False)
                        if subsurface_connections:
                            # Texture connected to subsurface weight - use 0.5 as default
                            subsurface_weight = 0.5
                        else:
                            # Get subsurface weight value
                            subsurface_weight = float(cmds.getAttr(f"{material_name}.subsurface"))
                            subsurface_weight = max(0.0, min(1.0, subsurface_weight))
                    except:
                        pass
                
                # Get subsurface color (if no texture)
                if subsurface_weight > 0.001 and not (subsurface_texture_image and not subsurface_texture_image.isNull()):
                    if cmds.attributeQuery("subsurfaceColor", node=material_name, exists=True):
                        try:
                            subsurface_color_attr = cmds.getAttr(f"{material_name}.subsurfaceColor")[0]
                            if isinstance(subsurface_color_attr, (list, tuple)) and len(subsurface_color_attr) >= 3:
                                if isinstance(subsurface_color_attr[0], (list, tuple)):
                                    subsurface_color = tuple(float(x) for x in subsurface_color_attr[0][:3])
                                else:
                                    subsurface_color = tuple(float(x) for x in subsurface_color_attr[:3])
                        except:
                            pass
            
            # Draw subsurface (texture or color)
            if subsurface_weight > 0.001:
                circle_clip_path = QtGui.QPainterPath()
                circle_clip_path.addEllipse(circle_rect)
                painter.setClipPath(circle_clip_path)
                
                if subsurface_texture_image and not subsurface_texture_image.isNull():
                    # Debug: log subsurface drawing
                    if not hasattr(self, '_subsurface_draw_logged'):
                        self._subsurface_draw_logged = True
                    try:
                        # Scale and center subsurface texture (same as base texture)
                        texture_width = subsurface_texture_image.width()
                        texture_height = subsurface_texture_image.height()
                        scale_w = circle_diameter / texture_width if texture_width > 0 else 1.0
                        scale_h = circle_diameter / texture_height if texture_height > 0 else 1.0
                        scale = max(scale_w, scale_h)
                        
                        scaled_w = int(texture_width * scale)
                        scaled_h = int(texture_height * scale)
                        offset_x = center_x - (scaled_w // 2)
                        offset_y = center_y - (scaled_h // 2)
                        subsurface_rect = QtCore.QRect(offset_x, offset_y, scaled_w, scaled_h)
                        
                        # Blend subsurface texture with screen mode (blend on top, not additive)
                        # At weight 1, subsurface should almost fully override base color
                        # Use a curve that ramps up quickly: weight 0 = 0%, weight 1 = ~90%
                        subsurface_alpha_factor = subsurface_weight ** 0.7  # Curve that ramps up quickly
                        subsurface_alpha_factor = subsurface_alpha_factor * 0.9  # Max 90% at weight 1
                        subsurface_alpha = int(subsurface_alpha_factor * 255)
                        painter.setCompositionMode(QtGui.QPainter.CompositionMode_Screen)
                        painter.setOpacity(subsurface_alpha / 255.0)
                        painter.drawPixmap(subsurface_rect, subsurface_texture_image)
                        painter.setOpacity(1.0)
                        painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)

                        # Apply the same grey-overlay behavior for transparency/opacity so
                        # subsurface textures also appear to fade toward the swatch grey.
                        if transparency_factor > 0.001:
                            overlay_alpha = int(max(0.0, min(1.0, transparency_factor)) * 255)
                            if overlay_alpha > 0:
                                grey_overlay = QtGui.QColor(
                                    bg_color.red(), bg_color.green(), bg_color.blue(), overlay_alpha
                                )
                                painter.setBrush(QtGui.QBrush(grey_overlay))
                                painter.setPen(QtCore.Qt.NoPen)
                                painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
                                painter.drawEllipse(circle_rect)
                    except:
                        pass
                elif subsurface_color:
                    # Draw subsurface color overlay (no texture, just color)
                    try:
                        # Blend subsurface color with screen mode (blend on top, not additive)
                        # At weight 1, subsurface should almost fully override base color
                        # Use a curve that ramps up quickly: weight 0 = 0%, weight 1 = ~90%
                        # This matches shader behavior where subsurface at 1 almost fully overrides
                        subsurface_alpha_factor = subsurface_weight ** 0.7  # Curve that ramps up quickly
                        subsurface_alpha_factor = subsurface_alpha_factor * 0.9  # Max 90% at weight 1
                        subsurface_alpha = int(subsurface_alpha_factor * 255)
                        painter.setCompositionMode(QtGui.QPainter.CompositionMode_Screen)
                        subsurface_color_qcolor = QtGui.QColor(
                            int(subsurface_color[0] * 255),
                            int(subsurface_color[1] * 255),
                            int(subsurface_color[2] * 255),
                            subsurface_alpha
                        )
                        painter.setBrush(QtGui.QBrush(subsurface_color_qcolor))
                        painter.setPen(QtCore.Qt.NoPen)
                        painter.drawEllipse(circle_rect)
                        painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
                    except:
                        pass
                
                painter.setClipping(False)
            timings['subsurface_draw'] = (_time.perf_counter() - subsurface_draw_start) * 1000.0
            
            # Draw emission glow/texture if present
            emission_draw_start = _time.perf_counter()
            # Check if we have emission (either color data or texture)
            has_emission_texture_only = emission_texture_image and not emission_texture_image.isNull()
            
            # Get emission intensity for opacity control
            # Always use emission_intensity from emission_result if available
            if emission_result is not None:
                # We have emission result, use the intensity from it
                _, emission_intensity_from_result = emission_result
                glow_intensity = emission_intensity_from_result
                # If intensity is 0 or very low, don't draw even if texture exists
                if glow_intensity <= 0.001:
                    glow_intensity = 0.0
                    glow_scale = 1.0  # Set scale even when not drawing
                else:
                    if has_emission and emission_data:
                        emission_strength = max(emission_data[0], emission_data[1], emission_data[2])
                        glow_scale = 1.0 if emission_strength <= 1.0 else 1.0 + (emission_strength - 1.0) * 0.5
                        glow_scale = max(1.0, glow_scale)
                    else:
                        glow_scale = 1.0
            elif has_emission_texture_only:
                # Fallback: if we have texture but no emission_result, check emission attribute directly
                try:
                    node_type = cmds.nodeType(material_name)
                    if node_type in ["standardSurface", "aiStandardSurface"]:
                        if cmds.attributeQuery("emission", node=material_name, exists=True):
                            emission_conns = cmds.listConnections(f"{material_name}.emission", s=True, d=False)
                            if not emission_conns:
                                # No texture on emission, get value directly - allow values up to 10.0
                                glow_intensity = float(cmds.getAttr(f"{material_name}.emission"))
                                glow_intensity = max(0.0, min(10.0, glow_intensity))
                            else:
                                # Texture on emission - try to get average value, default to 0.0 if can't
                                try:
                                    emission_texture_node = emission_conns[0]
                                    if cmds.nodeType(emission_texture_node) == "file":
                                        glow_intensity = self._get_texture_average_value(emission_texture_node)
                                    else:
                                        glow_intensity = 0.0  # Default to 0.0 (invisible) if can't determine
                                except:
                                    glow_intensity = 0.0  # Default to 0.0 (invisible) if error
                        else:
                            glow_intensity = 0.0  # No emission attribute = invisible
                    else:
                        glow_intensity = 0.0  # Legacy shaders don't have emission attribute
                except:
                    glow_intensity = 0.0  # Default to 0.0 (invisible) when texture only and can't get intensity
                glow_scale = 1.0
            else:
                glow_intensity = 0.0
                glow_scale = 1.0
            
            # Only draw emission if intensity is above threshold
            if glow_intensity > 0.001:
                glow_size_ratio = glow_scale
                glow_diameter = int(circle_diameter * glow_size_ratio)
                glow_margin = (circle_diameter - glow_diameter) // 2
                glow_rect = QtCore.QRect(
                    margin + glow_margin,
                    margin + glow_margin,
                    glow_diameter,
                    glow_diameter
                )
            
                glow_center_x = margin + circle_diameter // 2
                glow_center_y = margin + circle_diameter // 2
                glow_radius = glow_diameter // 2
            
                circle_clip_path = QtGui.QPainterPath()
                circle_clip_path.addEllipse(circle_rect)
                painter.setClipPath(circle_clip_path)
            
                # If we have an emission texture, use it with additive blend
                if emission_texture_image and not emission_texture_image.isNull():
                    # Debug: log emission drawing
                    if not hasattr(self, '_emission_draw_logged'):
                        self._emission_draw_logged = True
                    # Draw emission texture as additive glow onto base color
                    texture_width = emission_texture_image.width()
                    texture_height = emission_texture_image.height()
                    scale_w = glow_diameter / texture_width if texture_width > 0 else 1.0
                    scale_h = glow_diameter / texture_height if texture_height > 0 else 1.0
                    scale = max(scale_w, scale_h)
                
                    scaled_w = int(texture_width * scale)
                    scaled_h = int(texture_height * scale)
                    offset_x = glow_center_x - (scaled_w // 2)
                    offset_y = glow_center_y - (scaled_h // 2)
                    emission_texture_rect = QtCore.QRect(offset_x, offset_y, scaled_w, scaled_h)
                
                    # Use half-additive blend: mix of normal (50%) and additive (50%) to preserve color while adding brightness
                    # Intensity 0 = invisible, 1 = bright, >1 up to 10 = even brighter
                
                    if glow_intensity > 1.0:
                        # For values > 1.0, draw multiple passes to increase brightness
                        # Map 1.0-10.0 to 1-10 passes for increasing brightness
                        num_passes = max(1, min(10, int(glow_intensity + 0.5)))  # Round and clamp to 1-10
                        for _ in range(num_passes):
                            # Draw with normal blend at 70% opacity to preserve color
                            painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
                            painter.setOpacity(0.7)
                            painter.drawPixmap(emission_texture_rect, emission_texture_image)
                            # Then draw with additive blend at 30% opacity for subtle brightness
                            painter.setCompositionMode(QtGui.QPainter.CompositionMode_Plus)
                            painter.setOpacity(0.3)
                            painter.drawPixmap(emission_texture_rect, emission_texture_image)
                    else:
                        # Normal case: 0.0 to 1.0 - use intensity directly as opacity
                        # At 0, this will be 0 and texture won't be visible
                        # Draw with normal blend at 70% of intensity to preserve color
                        painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
                        painter.setOpacity(glow_intensity * 0.7)
                        painter.drawPixmap(emission_texture_rect, emission_texture_image)
                        # Then draw with additive blend at 30% of intensity for subtle brightness
                        painter.setCompositionMode(QtGui.QPainter.CompositionMode_Plus)
                        painter.setOpacity(glow_intensity * 0.3)
                        painter.drawPixmap(emission_texture_rect, emission_texture_image)
                    painter.setOpacity(1.0)
                    painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)

                    # Apply the same grey-overlay behavior for transparency/opacity so
                    # emission textures also visually fade toward the swatch grey.
                    if transparency_factor > 0.001:
                        overlay_alpha = int(max(0.0, min(1.0, transparency_factor)) * 255)
                        if overlay_alpha > 0:
                            grey_overlay = QtGui.QColor(
                                bg_color.red(), bg_color.green(), bg_color.blue(), overlay_alpha
                            )
                            painter.setBrush(QtGui.QBrush(grey_overlay))
                            painter.setPen(QtCore.Qt.NoPen)
                            painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
                            painter.drawEllipse(circle_rect)
                else:
                    # Fallback to original color-based emission glow (only if we have emission_data)
                    if has_emission and emission_data:
                            # Use half-additive blend: mix of normal (50%) and additive (50%) to preserve color
                            
                            emission_color_gradient = QtGui.QRadialGradient(glow_center_x, glow_center_y, glow_radius)
                            # Scale brightness: 0-1 = normal scaling, >1 = brighter (clamp color channels to 1.0)
                            brightness_mult = 1.5 + (glow_intensity - 1.0) * 0.3 if glow_intensity > 1.0 else 1.5
                            emission_r = min(1.0, emission_data[0] * brightness_mult)
                            emission_g = min(1.0, emission_data[1] * brightness_mult)
                            emission_b = min(1.0, emission_data[2] * brightness_mult)
                            # Scale opacity: 0 = invisible, 1 = normal, >1 = brighter
                            emission_overlay_alpha = min(1.0, glow_intensity) * 0.6
                            if glow_intensity > 1.0:
                                emission_overlay_alpha = min(1.0, 0.6 + (glow_intensity - 1.0) * 0.15)
                            
                            # Draw with half-additive blend: normal (50%) + additive (50%)
                            # First draw with normal blend at 50% opacity to preserve color
                            painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
                            
                            if glow_intensity > 1.0:
                                num_passes = max(1, min(10, int(glow_intensity + 0.5)))
                                pass_alpha = emission_overlay_alpha / num_passes
                                for _ in range(num_passes):
                                    emission_color_gradient.setColorAt(0.0, QtGui.QColor(
                                        int(emission_r * 255), int(emission_g * 255), int(emission_b * 255),
                                        int(pass_alpha * 0.7 * 255)  # 70% opacity for normal blend
                                    ))
                                    emission_color_gradient.setColorAt(0.8, QtGui.QColor(
                                        int(emission_r * 255), int(emission_g * 255), int(emission_b * 255),
                                        int(pass_alpha * 0.7 * 0.3 * 255)
                                    ))
                                    emission_color_gradient.setColorAt(1.0, QtGui.QColor(
                                        int(emission_r * 255), int(emission_g * 255), int(emission_b * 255), 0
                                    ))
                                    painter.setBrush(QtGui.QBrush(emission_color_gradient))
                                    painter.setPen(QtCore.Qt.NoPen)
                                    painter.drawEllipse(glow_rect)
                                    
                                    # Then draw with additive blend at 50% opacity for brightness
                                    painter.setCompositionMode(QtGui.QPainter.CompositionMode_Plus)
                                    emission_color_gradient.setColorAt(0.0, QtGui.QColor(
                                        int(emission_r * 255), int(emission_g * 255), int(emission_b * 255),
                                        int(pass_alpha * 0.3 * 255)  # 30% opacity for additive blend
                                    ))
                                    emission_color_gradient.setColorAt(0.8, QtGui.QColor(
                                        int(emission_r * 255), int(emission_g * 255), int(emission_b * 255),
                                        int(pass_alpha * 0.7 * 0.3 * 255)
                                    ))
                                    emission_color_gradient.setColorAt(1.0, QtGui.QColor(
                                        int(emission_r * 255), int(emission_g * 255), int(emission_b * 255), 0
                                    ))
                                    painter.setBrush(QtGui.QBrush(emission_color_gradient))
                                    painter.setPen(QtCore.Qt.NoPen)
                                    painter.drawEllipse(glow_rect)
                                    painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
                            else:
                                emission_color_gradient.setColorAt(0.0, QtGui.QColor(
                                    int(emission_r * 255), int(emission_g * 255), int(emission_b * 255),
                                    int(emission_overlay_alpha * 0.7 * 255)  # 70% opacity for normal blend
                                ))
                                emission_color_gradient.setColorAt(0.8, QtGui.QColor(
                                    int(emission_r * 255), int(emission_g * 255), int(emission_b * 255),
                                    int(emission_overlay_alpha * 0.7 * 0.3 * 255)
                                ))
                                emission_color_gradient.setColorAt(1.0, QtGui.QColor(
                                    int(emission_r * 255), int(emission_g * 255), int(emission_b * 255), 0
                                ))
                                painter.setBrush(QtGui.QBrush(emission_color_gradient))
                                painter.setPen(QtCore.Qt.NoPen)
                                painter.drawEllipse(glow_rect)
                                
                                # Then draw with additive blend at 50% opacity for brightness
                                painter.setCompositionMode(QtGui.QPainter.CompositionMode_Plus)
                                emission_color_gradient.setColorAt(0.0, QtGui.QColor(
                                    int(emission_r * 255), int(emission_g * 255), int(emission_b * 255),
                                    int(emission_overlay_alpha * 0.3 * 255)  # 30% opacity for additive blend
                                ))
                                emission_color_gradient.setColorAt(0.8, QtGui.QColor(
                                    int(emission_r * 255), int(emission_g * 255), int(emission_b * 255),
                                    int(emission_overlay_alpha * 0.7 * 0.3 * 255)
                                ))
                                emission_color_gradient.setColorAt(1.0, QtGui.QColor(
                                    int(emission_r * 255), int(emission_g * 255), int(emission_b * 255), 0
                                ))
                                painter.setBrush(QtGui.QBrush(emission_color_gradient))
                                painter.setPen(QtCore.Qt.NoPen)
                                painter.drawEllipse(glow_rect)
                                painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
                            
                            # White glow with half-additive blend
                            white_glow_gradient = QtGui.QRadialGradient(glow_center_x, glow_center_y, glow_radius)
                            white_glow_center_alpha = min(1.0, glow_intensity) * 0.8
                            if glow_intensity > 1.0:
                                white_glow_center_alpha = min(1.0, 0.8 + (glow_intensity - 1.0) * 0.2)
                            
                            if glow_intensity > 1.0:
                                num_passes = max(1, min(10, int(glow_intensity + 0.5)))
                                pass_alpha = white_glow_center_alpha / num_passes
                                for _ in range(num_passes):
                                    # Normal blend at 70%
                                    painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
                                    white_glow_gradient.setColorAt(0.0, QtGui.QColor(255, 255, 255, int(pass_alpha * 0.7 * 255)))
                                    white_glow_gradient.setColorAt(0.7, QtGui.QColor(255, 255, 255, int(pass_alpha * 0.7 * 0.3 * 255)))
                                    white_glow_gradient.setColorAt(1.0, QtGui.QColor(255, 255, 255, 0))
                                    painter.setBrush(QtGui.QBrush(white_glow_gradient))
                                    painter.setPen(QtCore.Qt.NoPen)
                                    painter.drawEllipse(glow_rect)
                                    # Additive blend at 30%
                                    painter.setCompositionMode(QtGui.QPainter.CompositionMode_Plus)
                                    white_glow_gradient.setColorAt(0.0, QtGui.QColor(255, 255, 255, int(pass_alpha * 0.3 * 255)))
                                    white_glow_gradient.setColorAt(0.7, QtGui.QColor(255, 255, 255, int(pass_alpha * 0.3 * 0.3 * 255)))
                                    white_glow_gradient.setColorAt(1.0, QtGui.QColor(255, 255, 255, 0))
                                    painter.setBrush(QtGui.QBrush(white_glow_gradient))
                                    painter.setPen(QtCore.Qt.NoPen)
                                    painter.drawEllipse(glow_rect)
                                    painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
                            else:
                                # Normal blend at 70%
                                painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
                                white_glow_gradient.setColorAt(0.0, QtGui.QColor(255, 255, 255, int(white_glow_center_alpha * 0.7 * 255)))
                                white_glow_gradient.setColorAt(0.7, QtGui.QColor(255, 255, 255, int(white_glow_center_alpha * 0.7 * 0.3 * 255)))
                                white_glow_gradient.setColorAt(1.0, QtGui.QColor(255, 255, 255, 0))
                                painter.setBrush(QtGui.QBrush(white_glow_gradient))
                                painter.setPen(QtCore.Qt.NoPen)
                                painter.drawEllipse(glow_rect)
                                # Additive blend at 30%
                                painter.setCompositionMode(QtGui.QPainter.CompositionMode_Plus)
                                white_glow_gradient.setColorAt(0.0, QtGui.QColor(255, 255, 255, int(white_glow_center_alpha * 0.3 * 255)))
                                white_glow_gradient.setColorAt(0.7, QtGui.QColor(255, 255, 255, int(white_glow_center_alpha * 0.3 * 0.3 * 255)))
                                white_glow_gradient.setColorAt(1.0, QtGui.QColor(255, 255, 255, 0))
                                painter.setBrush(QtGui.QBrush(white_glow_gradient))
                                painter.setPen(QtCore.Qt.NoPen)
                                painter.drawEllipse(glow_rect)
                                painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
                    
                    painter.setClipping(False)
            timings['emission_draw'] = (_time.perf_counter() - emission_draw_start) * 1000.0
            
            # Draw sheen edge glow (opposite of emission - glow around the edge instead of center)
            # Sheen creates a soft gradient from the outside in, on top of other layers
            # Sheen roughness controls the softness: 0 = few pixels fade, 1 = very soft gradient
            sheen_draw_start = _time.perf_counter()
            has_sheen = sheen_weight > 0.001 and any(x > 0.001 for x in sheen_color[:3] if isinstance(x, (int, float)))
            
            if has_sheen and sheen_roughness is not None:
                sheen_r, sheen_g, sheen_b = sheen_color
                sheen_brightness = max(sheen_r, sheen_g, sheen_b)
                
                # Calculate sheen intensity based on weight and color brightness
                sheen_intensity = sheen_weight * sheen_brightness
                
                # Only draw if intensity is above threshold
                if sheen_intensity > 0.001:
                    # Clamp roughness
                    clamped_sheen_roughness = max(0.0, min(1.0, sheen_roughness))
                    
                    # Sheen roughness controls the softness of the gradient
                    # At 0 roughness: few pixels of fade (sharp edge, small fade zone)
                    # At 1 roughness: very soft gradient (large fade zone)
                    # Map roughness to fade distance: 0 = ~8% of radius, 1 = ~60% of radius (softer globally)
                    min_fade_ratio = 0.08  # 8% at roughness 0 (few pixels, but softer than before)
                    max_fade_ratio = 0.60  # 60% at roughness 1 (very soft, softer than before)
                    fade_ratio = min_fade_ratio + (max_fade_ratio - min_fade_ratio) * clamped_sheen_roughness
                    
                    # Create radial gradient from outside in
                    sheen_gradient = QtGui.QRadialGradient(
                        center_x, center_y, radius
                    )
                    
                    # Scale sheen color brightness
                    brightness_mult = 1.2
                    sheen_glow_r = min(1.0, sheen_r * brightness_mult)
                    sheen_glow_g = min(1.0, sheen_g * brightness_mult)
                    sheen_glow_b = min(1.0, sheen_b * brightness_mult)
                    
                    # Opacity based on intensity, also affected by material opacity and transmission
                    # Apply opacity and transmission the same way they affect other colors
                    effective_opacity = opacity * (1.0 - transmission * 0.8)
                    sheen_overlay_alpha = min(1.0, sheen_intensity) * 0.5 * effective_opacity
                    
                    # Gradient starts at the edge (1.0) and fades inward
                    # The fade zone is controlled by roughness
                    edge_start = 1.0 - fade_ratio  # Where the fade starts (from edge)
                    
                    # At the edge: full opacity
                    sheen_gradient.setColorAt(1.0, QtGui.QColor(
                        int(sheen_glow_r * 255), int(sheen_glow_g * 255), int(sheen_glow_b * 255),
                        int(sheen_overlay_alpha * 255)
                    ))
                    
                    # At the fade start: still visible but starting to fade
                    if edge_start > 0.5:
                        # Fade zone is significant, add intermediate stop
                        mid_fade = edge_start + (1.0 - edge_start) * 0.5
                        sheen_gradient.setColorAt(mid_fade, QtGui.QColor(
                            int(sheen_glow_r * 255), int(sheen_glow_g * 255), int(sheen_glow_b * 255),
                            int(sheen_overlay_alpha * 0.6 * 255)  # 60% opacity at mid-fade
                        ))
                    
                    # At the fade start: fading
                    sheen_gradient.setColorAt(edge_start, QtGui.QColor(
                        int(sheen_glow_r * 255), int(sheen_glow_g * 255), int(sheen_glow_b * 255),
                        int(sheen_overlay_alpha * 0.3 * 255)  # 30% opacity at fade start
                    ))
                    
                    # Fade to transparent at center
                    sheen_gradient.setColorAt(0.0, QtGui.QColor(
                        int(sheen_glow_r * 255), int(sheen_glow_g * 255), int(sheen_glow_b * 255), 0
                    ))
                    
                    # Clip to the circle
                    circle_clip_path = QtGui.QPainterPath()
                    circle_clip_path.addEllipse(circle_rect)
                    painter.setClipPath(circle_clip_path)
                    
                    # Draw sheen with additive blend for soft glow effect
                    painter.setCompositionMode(QtGui.QPainter.CompositionMode_Plus)
                    painter.setBrush(QtGui.QBrush(sheen_gradient))
                    painter.setPen(QtCore.Qt.NoPen)
                    painter.drawEllipse(circle_rect)
                    
                    # Also draw with normal blend at lower opacity for color preservation
                    painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
                    # Reduce opacity for normal blend
                    sheen_gradient.setColorAt(1.0, QtGui.QColor(
                        int(sheen_glow_r * 255), int(sheen_glow_g * 255), int(sheen_glow_b * 255),
                        int(sheen_overlay_alpha * 0.4 * 255)  # 40% opacity for normal blend
                    ))
                    if edge_start > 0.5:
                        mid_fade = edge_start + (1.0 - edge_start) * 0.5
                        sheen_gradient.setColorAt(mid_fade, QtGui.QColor(
                            int(sheen_glow_r * 255), int(sheen_glow_g * 255), int(sheen_glow_b * 255),
                            int(sheen_overlay_alpha * 0.4 * 0.6 * 255)  # Scaled down
                        ))
                    sheen_gradient.setColorAt(edge_start, QtGui.QColor(
                        int(sheen_glow_r * 255), int(sheen_glow_g * 255), int(sheen_glow_b * 255),
                        int(sheen_overlay_alpha * 0.4 * 0.3 * 255)  # Scaled down
                    ))
                    sheen_gradient.setColorAt(0.0, QtGui.QColor(
                        int(sheen_glow_r * 255), int(sheen_glow_g * 255), int(sheen_glow_b * 255), 0
                    ))
                    painter.setBrush(QtGui.QBrush(sheen_gradient))
                    painter.drawEllipse(circle_rect)
                    painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
                    painter.setClipping(False)
            
            timings['sheen_draw'] = (_time.perf_counter() - sheen_draw_start) * 1000.0
            
            # Draw coat color overlay (affects base color/subsurface based on coat color)
            # Coat color overlays on base color regardless of brightness (bright green/pink overlays)
            # This should be drawn before specular hits but after base color/subsurface
            if coat_weight > 0.001:
                coat_overlay_start = _time.perf_counter()
                try:
                    coat_r, coat_g, coat_b = coat_color
                    
                    # Overlay intensity: affected by coat weight
                    # Coat color always overlays on base color, regardless of brightness
                    overlay_intensity = coat_weight
                    
                    # Reduce overlay when emission is high (emission should show through)
                    if emission_result is not None:
                        _, emission_intensity_val = emission_result
                        if emission_intensity_val > 0.001:
                            # Linearly reduce overlay based on emission intensity
                            emission_reduction = max(0.2, 1.0 - (emission_intensity_val * 0.4))
                            overlay_intensity = overlay_intensity * emission_reduction
                    
                    if overlay_intensity > 0.001:
                        # Use multiply blend mode to overlay the coat color on base color
                        painter.setCompositionMode(QtGui.QPainter.CompositionMode_Multiply)
                        
                        # Alpha based on overlay intensity
                        overlay_alpha = int(overlay_intensity * 0.6 * 255)  # 60% max opacity for overlay
                        overlay_alpha = max(0, min(255, overlay_alpha))
                        
                        # Draw color overlay covering the entire circle
                        overlay_color = QtGui.QColor(
                            int(coat_r * 255), int(coat_g * 255), int(coat_b * 255), overlay_alpha
                        )
                        painter.setBrush(QtGui.QBrush(overlay_color))
                        painter.setPen(QtCore.Qt.NoPen)
                        painter.drawEllipse(circle_rect)
                        painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
                except Exception:
                    pass
                timings['coat_overlay'] = (_time.perf_counter() - coat_overlay_start) * 1000.0
            
            # Draw specular highlight if roughness is available
            specular_start = _time.perf_counter()
            if roughness is not None:
                # Save current composition mode for specular elements (using half-additive blend)
                original_composition_mode = painter.compositionMode()

                # Fade all specular contributions by the value (brightness) of the specular color.
                # Value is approximated as the max RGB component of the (possibly desaturated) specular color.
                try:
                    spec_val_r, spec_val_g, spec_val_b = specular_color
                    spec_value_factor = max(float(spec_val_r), float(spec_val_g), float(spec_val_b))
                    spec_value_factor = max(0.0, min(1.0, spec_value_factor))
                except Exception:
                    spec_value_factor = 1.0
                # Make highlight fade much faster - barely visible by 0.75 roughness (soft blur)
                # Lower roughness = higher opacity, but fade aggressively
                # Also multiply by specular weight (0 = no specular, 1 = full specular)
                # Use steeper power curve to fade much faster
                highlight_opacity = (1.0 - roughness) ** 2.5  # Much steeper curve - fades faster
                # Boost strength at low roughness but fade aggressively
                highlight_opacity = highlight_opacity * 1.3  # Boost overall strength
                highlight_opacity = min(1.0, highlight_opacity)  # Clamp to max
                # Apply specular weight and specular color value
                highlight_opacity = highlight_opacity * specular_weight * spec_value_factor
                # Additional aggressive fade starting at 0.5 to make it barely visible by 0.75
                if roughness >= 0.5:
                    # Fade from full at 0.5 to nearly zero by 0.75
                    fade_progress = (roughness - 0.5) / 0.5  # 0.0 at 0.5, 1.0 at 1.0
                    fade_factor = (1.0 - fade_progress) ** 2.0  # Steep fade curve
                    highlight_opacity = highlight_opacity * fade_factor
                
                # Add metalness as an additive boost to specular highlight opacity
                try:
                    metalness = None
                    if node_type in ["standardSurface", "aiStandardSurface"]:
                        if cmds.attributeQuery("metalness", node=material_name, exists=True):
                            try:
                                metalness = cmds.getAttr(f"{material_name}.metalness")
                                if metalness is not None:
                                    metalness = float(metalness)
                            except:
                                pass
                    
                    if metalness is not None and metalness > 0:
                        # Add metalness as additive boost (up to 0.3 additional opacity at metalness 1.0)
                        metalness_boost = metalness * 0.3
                        highlight_opacity = highlight_opacity + metalness_boost
                        highlight_opacity = min(1.0, highlight_opacity)  # Clamp to max
                except Exception:
                    pass
                
                min_size_ratio = 0.15
                max_size_ratio = 0.50  # Increased from 0.40 to make it larger
                base_size_ratio = min_size_ratio + (max_size_ratio - min_size_ratio) * roughness
                highlight_size_ratio = base_size_ratio * specular_size_multiplier
                highlight_diameter = int(circle_diameter * highlight_size_ratio)
                
                max_possible_diameter = circle_diameter - (margin * 2)
                if highlight_diameter > max_possible_diameter:
                    highlight_diameter = max_possible_diameter
                
                # Keep highlight in top-right position - moves toward center as roughness increases
                # At roughness 0.0: top-right position
                # At roughness 1.0: near center position
                # Use smooth easing curve for natural movement (not straight diagonal)
                
                # Target positions
                start_x_ratio = 0.70  # Top-right X at low roughness
                start_y_ratio = 0.28  # Top-right Y at low roughness (higher up)
                # End positions: 50% less movement than before
                # Previous movement: X 0.15, Y 0.165. Reduced by 50%: X 0.075, Y 0.0825
                end_x_ratio = 0.625   # X at max roughness (0.70 - 0.075 = 0.625)
                end_y_ratio = 0.3625  # Y at max roughness (0.28 + 0.0825 = 0.3625)
                
                # Perfectly linear movement - no easing curves
                t = roughness  # 0.0 to 1.0
                
                # Linear interpolation for perfectly smooth, constant-speed movement
                highlight_center_x_ratio = start_x_ratio + (end_x_ratio - start_x_ratio) * t
                highlight_center_y_ratio = start_y_ratio + (end_y_ratio - start_y_ratio) * t
                
                # Calculate center position
                highlight_center_x = margin + int(circle_diameter * highlight_center_x_ratio)
                highlight_center_y = margin + int(circle_diameter * highlight_center_y_ratio)
                
                min_center_x = margin + (highlight_diameter // 2)
                max_center_x = margin + circle_diameter - (highlight_diameter // 2)
                min_center_y = margin + (highlight_diameter // 2)
                max_center_y = margin + circle_diameter - (highlight_diameter // 2)
                
                highlight_center_x = max(min_center_x, min(max_center_x, highlight_center_x))
                highlight_center_y = max(min_center_y, min(max_center_y, highlight_center_y))
                
                highlight_rect = QtCore.QRect(
                    highlight_center_x - (highlight_diameter // 2),
                    highlight_center_y - (highlight_diameter // 2),
                    highlight_diameter,
                    highlight_diameter
                )
                
                highlight_gradient = QtGui.QRadialGradient(
                    highlight_center_x, highlight_center_y, highlight_diameter // 2
                )
                
                # Use original specular color (no desaturation needed with additive blend)
                spec_r, spec_g, spec_b = specular_color
                
                # Check if specular color is black/dark - if so, use fully additive (black = invisible)
                spec_brightness = max(spec_r, spec_g, spec_b)
                is_dark_specular = spec_brightness < 0.1  # Very dark/black specular
                
                center_alpha = highlight_opacity
                edge_alpha = 0.0
                falloff_start = 1.0 - specular_softness
                falloff_start = max(0.0, min(1.0, falloff_start))
                
                if is_dark_specular:
                    # For black/dark specular, use fully additive blend only (black adds nothing, so invisible)
                    painter.setCompositionMode(QtGui.QPainter.CompositionMode_Plus)
                    highlight_gradient.setColorAt(0.0, QtGui.QColor(
                        int(spec_r * 255), int(spec_g * 255), int(spec_b * 255), 
                        int(center_alpha * 255)  # Full opacity for additive blend
                    ))
                    if falloff_start > 0.001:
                        highlight_gradient.setColorAt(falloff_start, QtGui.QColor(
                            int(spec_r * 255), int(spec_g * 255), int(spec_b * 255), 
                            int(center_alpha * 255)
                        ))
                    highlight_gradient.setColorAt(1.0, QtGui.QColor(
                        int(spec_r * 255), int(spec_g * 255), int(spec_b * 255), 
                        int(edge_alpha * 255)
                    ))
                    painter.setBrush(QtGui.QBrush(highlight_gradient))
                    painter.setPen(QtCore.Qt.NoPen)
                    painter.drawEllipse(highlight_rect)
                    painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
                else:
                    # For normal specular colors, use half-additive blend: normal (50%) + additive (50%)
                    # First draw with normal blend at 50% opacity to preserve color
                    painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
                    highlight_gradient.setColorAt(0.0, QtGui.QColor(
                        int(spec_r * 255), int(spec_g * 255), int(spec_b * 255), 
                        int(center_alpha * 0.5 * 255)  # 50% opacity for normal blend
                    ))
                    if falloff_start > 0.001:
                        highlight_gradient.setColorAt(falloff_start, QtGui.QColor(
                            int(spec_r * 255), int(spec_g * 255), int(spec_b * 255), 
                            int(center_alpha * 0.5 * 255)
                        ))
                    highlight_gradient.setColorAt(1.0, QtGui.QColor(
                        int(spec_r * 255), int(spec_g * 255), int(spec_b * 255), 
                        int(edge_alpha * 255)
                    ))
                    painter.setBrush(QtGui.QBrush(highlight_gradient))
                    painter.setPen(QtCore.Qt.NoPen)
                    painter.drawEllipse(highlight_rect)
                    
                    # Then draw with additive blend at 50% opacity for brightness
                    painter.setCompositionMode(QtGui.QPainter.CompositionMode_Plus)
                    highlight_gradient.setColorAt(0.0, QtGui.QColor(
                        int(spec_r * 255), int(spec_g * 255), int(spec_b * 255), 
                        int(center_alpha * 0.5 * 255)  # 50% opacity for additive blend
                    ))
                    if falloff_start > 0.001:
                        highlight_gradient.setColorAt(falloff_start, QtGui.QColor(
                            int(spec_r * 255), int(spec_g * 255), int(spec_b * 255), 
                            int(center_alpha * 0.5 * 255)
                        ))
                    highlight_gradient.setColorAt(1.0, QtGui.QColor(
                        int(spec_r * 255), int(spec_g * 255), int(spec_b * 255), 
                        int(edge_alpha * 255)
                    ))
                    painter.setBrush(QtGui.QBrush(highlight_gradient))
                    painter.setPen(QtCore.Qt.NoPen)
                    painter.drawEllipse(highlight_rect)
                    painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
                
                # Draw small secondary highlight on the left side (soft half moon)
                # Only draw if specular weight > 0 and roughness is not 1.0
                if specular_weight > 0.001 and (roughness is None or roughness < 0.99):
                    # Smaller, softer highlight on the left side
                    secondary_size_ratio = 0.20  # Smaller than main highlight
                    secondary_diameter = int(circle_diameter * secondary_size_ratio)
                    
                    # Position on the left side
                    secondary_offset_x = int(circle_diameter * 0.25)  # Left side
                    secondary_offset_y = int(circle_diameter * 0.35)  # Slightly below center
                    
                    secondary_center_x = margin + secondary_offset_x
                    secondary_center_y = margin + secondary_offset_y
                    
                    secondary_rect = QtCore.QRect(
                        secondary_center_x - (secondary_diameter // 2),
                        secondary_center_y - (secondary_diameter // 2),
                        secondary_diameter,
                        secondary_diameter
                    )
                    
                    # Softer, more subtle gradient
                    secondary_gradient = QtGui.QRadialGradient(
                        secondary_center_x, secondary_center_y, secondary_diameter // 2
                    )
                    
                    # Linear fade with roughness, specular_weight, and specular color value
                    # visibility = (1 - roughness) * specular_weight * spec_value_factor, clamped to [0, 1]
                    clamped_roughness = max(0.0, min(1.0, roughness if roughness is not None else 0.0))
                    clamped_spec_weight = max(0.0, min(1.0, specular_weight))
                    visibility = (1.0 - clamped_roughness) * clamped_spec_weight * spec_value_factor
                    
                    # Global scaling to keep it subtle
                    base_scale = 0.3  # tweak if you want stronger/weaker base
                    secondary_opacity = visibility * base_scale  # 0..base_scale
                    
                    # Add metalness as an additive boost to secondary highlight opacity
                    try:
                        metalness = None
                        if node_type in ["standardSurface", "aiStandardSurface"]:
                            if cmds.attributeQuery("metalness", node=material_name, exists=True):
                                try:
                                    metalness = cmds.getAttr(f"{material_name}.metalness")
                                    if metalness is not None:
                                        metalness = float(metalness)
                                except:
                                    pass
                        
                        if metalness is not None and metalness > 0:
                            # Add metalness as additive boost (up to 0.15 additional opacity at metalness 1.0)
                            metalness_boost = metalness * 0.15
                            secondary_opacity = secondary_opacity + metalness_boost
                            secondary_opacity = min(1.0, secondary_opacity)  # Clamp to max
                    except Exception:
                        pass
                    
                    # Convert to alpha
                    secondary_alpha = int(secondary_opacity * 255.0)
                    secondary_alpha = max(0, min(150, secondary_alpha))
                    
                    # Skip drawing entirely if alpha is 0 (invisible)
                    if secondary_alpha <= 0:
                        # Don't draw anything - gradient is invisible
                        pass
                    else:
                        # Check if specular color is black/dark - if so, use fully additive (black = invisible)
                        spec_r, spec_g, spec_b = specular_color
                        spec_brightness = max(spec_r, spec_g, spec_b)
                        is_dark_specular = spec_brightness < 0.1  # Very dark/black specular
                        
                        if is_dark_specular:
                            # For black/dark specular, use fully additive blend only (black adds nothing, so invisible)
                            painter.setCompositionMode(QtGui.QPainter.CompositionMode_Plus)
                            secondary_gradient.setColorAt(0.0, QtGui.QColor(
                                int(spec_r * 255), int(spec_g * 255), int(spec_b * 255), secondary_alpha))
                            secondary_gradient.setColorAt(0.4, QtGui.QColor(
                                int(spec_r * 255), int(spec_g * 255), int(spec_b * 255), int(secondary_alpha * 0.6)))
                            secondary_gradient.setColorAt(1.0, QtGui.QColor(
                                int(spec_r * 255), int(spec_g * 255), int(spec_b * 255), 0))
                            painter.setBrush(QtGui.QBrush(secondary_gradient))
                            painter.setPen(QtCore.Qt.NoPen)
                            painter.drawEllipse(secondary_rect)
                            painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
                        else:
                            # For normal specular colors, use half-additive blend: normal (50%) + additive (50%)
                            # First draw with normal blend at 50% opacity
                            painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
                            secondary_gradient.setColorAt(0.0, QtGui.QColor(
                                int(spec_r * 255), int(spec_g * 255), int(spec_b * 255), int(secondary_alpha * 0.5)))
                            secondary_gradient.setColorAt(0.4, QtGui.QColor(
                                int(spec_r * 255), int(spec_g * 255), int(spec_b * 255), int(secondary_alpha * 0.5 * 0.6)))
                            secondary_gradient.setColorAt(1.0, QtGui.QColor(
                                int(spec_r * 255), int(spec_g * 255), int(spec_b * 255), 0))
                            painter.setBrush(QtGui.QBrush(secondary_gradient))
                            painter.setPen(QtCore.Qt.NoPen)
                            painter.drawEllipse(secondary_rect)
                            # Then draw with additive blend at 50% opacity
                            painter.setCompositionMode(QtGui.QPainter.CompositionMode_Plus)
                            secondary_gradient.setColorAt(0.0, QtGui.QColor(
                                int(spec_r * 255), int(spec_g * 255), int(spec_b * 255), int(secondary_alpha * 0.5)))
                            secondary_gradient.setColorAt(0.4, QtGui.QColor(
                                int(spec_r * 255), int(spec_g * 255), int(spec_b * 255), int(secondary_alpha * 0.5 * 0.6)))
                            secondary_gradient.setColorAt(1.0, QtGui.QColor(
                                int(spec_r * 255), int(spec_g * 255), int(spec_b * 255), 0))
                            painter.setBrush(QtGui.QBrush(secondary_gradient))
                            painter.setPen(QtCore.Qt.NoPen)
                            painter.drawEllipse(secondary_rect)
                            painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
                
                # Draw large soft white reflection (environment reflection)
                # This creates a crescent/half-moon shape that follows the curve of the ball
                # Opacity should:
                #   - Fade with roughness so it's ~99% invisible at roughness == 1.0
                #   - Fade linearly with specular weight (1 -> fully visible, 0 -> fully invisible)
                #   - Fade with the value (brightness) of the specular color
                if specular_weight > 0.001:
                    # Clamp inputs defensively
                    clamped_roughness = max(0.0, min(1.0, roughness))
                    clamped_spec_weight = max(0.0, min(1.0, specular_weight))

                    # Roughness visibility: 1 at roughness 0, 0.01 at roughness 1
                    # This matches the request: "99% invisible when roughness is 1"
                    roughness_visibility = 1.0 - 0.99 * clamped_roughness

                    # Specular visibility: linear from 0..1, also scaled by specular color value
                    spec_visibility = clamped_spec_weight * spec_value_factor

                    # Combined visibility factor
                    visibility = roughness_visibility * spec_visibility

                    # Overall scale for this reflection (make it more subtle so it doesn't pop too much)
                    # Previously 0.5; lowered to 0.25, and now to 0.18 for an even softer cue.
                    reflection_opacity = visibility * 0.18  # 0..0.18
                    
                    # Add metalness as an additive boost to reflection opacity
                    try:
                        metalness = None
                        if node_type in ["standardSurface", "aiStandardSurface"]:
                            if cmds.attributeQuery("metalness", node=material_name, exists=True):
                                try:
                                    metalness = cmds.getAttr(f"{material_name}.metalness")
                                    if metalness is not None:
                                        metalness = float(metalness)
                                except:
                                    pass
                        
                        if metalness is not None and metalness > 0:
                            # Add metalness as additive boost (up to 0.1 additional opacity at metalness 1.0)
                            metalness_boost = metalness * 0.1
                            reflection_opacity = reflection_opacity + metalness_boost
                            reflection_opacity = min(1.0, reflection_opacity)  # Clamp to max
                    except Exception:
                        pass
                    
                    # Create a strongly left‑anchored gradient.
                    # Instead of spanning the whole ball, we confine the gradient line
                    # to the left side so the bright band clearly hugs the left edge.
                    #
                    # Start near bottom‑left of the circle, end near upper‑left/center.
                    gradient_start_x = margin                             # Left edge
                    gradient_start_y = margin + circle_diameter           # Bottom area
                    gradient_end_x = margin + int(circle_diameter * 0.35) # About one‑third in from the left
                    gradient_end_y = margin                               # Upper area
                    
                    reflection_gradient = QtGui.QLinearGradient(
                        gradient_start_x, gradient_start_y,  # Start from bottom-left
                        gradient_end_x, gradient_end_y  # End at top-right
                    )
                    
                    # Check if specular color is black/dark - if so, use fully additive (black = invisible)
                    spec_r, spec_g, spec_b = specular_color
                    spec_brightness = max(spec_r, spec_g, spec_b)
                    is_dark_specular = spec_brightness < 0.1  # Very dark/black specular
                    
                    # Convert opacity to alpha; allow it to hit 0 so it truly disappears
                    reflection_alpha = int(reflection_opacity * 255)
                    reflection_alpha = max(0, min(255, reflection_alpha))
                    
                    # Draw a large rectangle covering the entire circle area with the gradient
                    # Then clip to the circle to get the curved shape
                    gradient_rect = QtCore.QRect(margin, margin, circle_diameter, circle_diameter)
                    
                    # Clip to the circle to ensure it follows the curve perfectly
                    circle_clip_path = QtGui.QPainterPath()
                    circle_clip_path.addEllipse(circle_rect)
                    
                    # Set clipping to the circle
                    painter.setClipPath(circle_clip_path)
                    
                    if is_dark_specular:
                        # For black/dark specular, use fully additive blend only (black adds nothing, so invisible)
                        painter.setCompositionMode(QtGui.QPainter.CompositionMode_Plus)
                        reflection_gradient.setColorAt(0.0, QtGui.QColor(
                            int(spec_r * 255), int(spec_g * 255), int(spec_b * 255), reflection_alpha))
                        reflection_gradient.setColorAt(0.2, QtGui.QColor(
                            int(spec_r * 255), int(spec_g * 255), int(spec_b * 255), int(reflection_alpha * 0.9)))
                        reflection_gradient.setColorAt(0.5, QtGui.QColor(
                            int(spec_r * 255), int(spec_g * 255), int(spec_b * 255), int(reflection_alpha * 0.5)))
                        reflection_gradient.setColorAt(0.8, QtGui.QColor(
                            int(spec_r * 255), int(spec_g * 255), int(spec_b * 255), int(reflection_alpha * 0.2)))
                        reflection_gradient.setColorAt(1.0, QtGui.QColor(
                            int(spec_r * 255), int(spec_g * 255), int(spec_b * 255), 0))
                        painter.setBrush(QtGui.QBrush(reflection_gradient))
                        painter.setPen(QtCore.Qt.NoPen)
                        painter.drawRect(gradient_rect)
                        painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
                    else:
                        # For normal specular colors, use half-additive blend: normal (50%) + additive (50%)
                        # First draw with normal blend at 50% opacity
                        painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
                        reflection_gradient.setColorAt(0.0, QtGui.QColor(
                            int(spec_r * 255), int(spec_g * 255), int(spec_b * 255), int(reflection_alpha * 0.5)))
                        reflection_gradient.setColorAt(0.2, QtGui.QColor(
                            int(spec_r * 255), int(spec_g * 255), int(spec_b * 255), int(reflection_alpha * 0.5 * 0.9)))
                        reflection_gradient.setColorAt(0.5, QtGui.QColor(
                            int(spec_r * 255), int(spec_g * 255), int(spec_b * 255), int(reflection_alpha * 0.5 * 0.5)))
                        reflection_gradient.setColorAt(0.8, QtGui.QColor(
                            int(spec_r * 255), int(spec_g * 255), int(spec_b * 255), int(reflection_alpha * 0.5 * 0.2)))
                        reflection_gradient.setColorAt(1.0, QtGui.QColor(
                            int(spec_r * 255), int(spec_g * 255), int(spec_b * 255), 0))
                        painter.setBrush(QtGui.QBrush(reflection_gradient))
                        painter.setPen(QtCore.Qt.NoPen)
                        painter.drawRect(gradient_rect)
                        
                        # Then draw with additive blend at 50% opacity
                        painter.setCompositionMode(QtGui.QPainter.CompositionMode_Plus)
                        reflection_gradient.setColorAt(0.0, QtGui.QColor(
                            int(spec_r * 255), int(spec_g * 255), int(spec_b * 255), int(reflection_alpha * 0.5)))
                        reflection_gradient.setColorAt(0.2, QtGui.QColor(
                            int(spec_r * 255), int(spec_g * 255), int(spec_b * 255), int(reflection_alpha * 0.5 * 0.9)))
                        reflection_gradient.setColorAt(0.5, QtGui.QColor(
                            int(spec_r * 255), int(spec_g * 255), int(spec_b * 255), int(reflection_alpha * 0.5 * 0.5)))
                        reflection_gradient.setColorAt(0.8, QtGui.QColor(
                            int(spec_r * 255), int(spec_g * 255), int(spec_b * 255), int(reflection_alpha * 0.5 * 0.2)))
                        reflection_gradient.setColorAt(1.0, QtGui.QColor(
                            int(spec_r * 255), int(spec_g * 255), int(spec_b * 255), 0))
                        painter.setBrush(QtGui.QBrush(reflection_gradient))
                        painter.setPen(QtCore.Qt.NoPen)
                        painter.drawRect(gradient_rect)
                        painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
                    
                    # Reset clipping
                    painter.setClipping(False)
            
            # Draw coat specular highlight (layered on top of base specular)
            # This creates a second specular layer to simulate clearcoat/coat materials
            # This is a fully new layer on top of everything else
            if coat_roughness is not None and coat_weight > 0.001:
                coat_specular_start = _time.perf_counter()
                
                # Coat spec hit is determined by coat weight and roughness only, NOT coat color
                # Spec hit should always show regardless of coat color (even at black)
                
                # Reduce coat visibility when emission is high (emission should show through)
                emission_reduction = 1.0
                if emission_result is not None:
                    _, emission_intensity_val = emission_result
                    if emission_intensity_val > 0.001:
                        # Linearly reduce coat visibility based on emission intensity
                        # At emission intensity 0: full coat, at 1.0: 50% coat, at 2.0+: 20% coat
                        emission_reduction = max(0.2, 1.0 - (emission_intensity_val * 0.4))
                
                # Use same fade logic as base specular but with coat roughness
                coat_highlight_opacity = (1.0 - coat_roughness) ** 2.5  # Same steep curve
                coat_highlight_opacity = coat_highlight_opacity * 1.3  # Same boost
                coat_highlight_opacity = min(1.0, coat_highlight_opacity)  # Clamp to max
                # Apply coat weight and emission reduction only (NOT coat color)
                coat_highlight_opacity = coat_highlight_opacity * coat_weight * emission_reduction
                # Additional aggressive fade starting at 0.5
                if coat_roughness >= 0.5:
                    fade_progress = (coat_roughness - 0.5) / 0.5
                    fade_factor = (1.0 - fade_progress) ** 2.0
                    coat_highlight_opacity = coat_highlight_opacity * fade_factor
                
                # Size calculation (same as base specular)
                min_size_ratio = 0.15
                max_size_ratio = 0.50
                base_size_ratio = min_size_ratio + (max_size_ratio - min_size_ratio) * coat_roughness
                coat_highlight_size_ratio = base_size_ratio * specular_size_multiplier
                coat_highlight_diameter = int(circle_diameter * coat_highlight_size_ratio)
                
                max_possible_diameter = circle_diameter - (margin * 2)
                if coat_highlight_diameter > max_possible_diameter:
                    coat_highlight_diameter = max_possible_diameter
                
                # Position: match main specular highlight position exactly
                coat_start_x_ratio = 0.70  # Same as main specular
                coat_start_y_ratio = 0.28  # Same as main specular
                coat_end_x_ratio = 0.625   # Same as main specular at max roughness (50% less movement)
                coat_end_y_ratio = 0.3625  # Same as main specular at max roughness (50% less movement)
                
                t = coat_roughness
                coat_highlight_center_x_ratio = coat_start_x_ratio + (coat_end_x_ratio - coat_start_x_ratio) * t
                coat_highlight_center_y_ratio = coat_start_y_ratio + (coat_end_y_ratio - coat_start_y_ratio) * t
                
                # Calculate center position
                coat_highlight_center_x = margin + int(circle_diameter * coat_highlight_center_x_ratio)
                coat_highlight_center_y = margin + int(circle_diameter * coat_highlight_center_y_ratio)
                
                min_center_x = margin + (coat_highlight_diameter // 2)
                max_center_x = margin + circle_diameter - (coat_highlight_diameter // 2)
                min_center_y = margin + (coat_highlight_diameter // 2)
                max_center_y = margin + circle_diameter - (coat_highlight_diameter // 2)
                
                coat_highlight_center_x = max(min_center_x, min(max_center_x, coat_highlight_center_x))
                coat_highlight_center_y = max(min_center_y, min(max_center_y, coat_highlight_center_y))
                
                coat_highlight_rect = QtCore.QRect(
                    coat_highlight_center_x - (coat_highlight_diameter // 2),
                    coat_highlight_center_y - (coat_highlight_diameter // 2),
                    coat_highlight_diameter,
                    coat_highlight_diameter
                )
                
                coat_highlight_gradient = QtGui.QRadialGradient(
                    coat_highlight_center_x, coat_highlight_center_y, coat_highlight_diameter // 2
                )
                
                coat_r, coat_g, coat_b = coat_color
                coat_brightness = max(coat_r, coat_g, coat_b)
                
                coat_center_alpha = coat_highlight_opacity
                coat_edge_alpha = 0.0
                coat_falloff_start = 1.0 - specular_softness
                coat_falloff_start = max(0.0, min(1.0, coat_falloff_start))
                
                # Coat spec hit should be white and additive like specular (matching specular behavior)
                # Spec hit is determined by coat weight and roughness only, not coat color
                # Use half-additive blend: normal (50%) + additive (50%) like specular
                # First draw with normal blend at 50% opacity (white)
                painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
                coat_highlight_gradient.setColorAt(0.0, QtGui.QColor(
                    255, 255, 255, int(coat_center_alpha * 0.5 * 255)  # White
                ))
                if coat_falloff_start > 0.001:
                    coat_highlight_gradient.setColorAt(coat_falloff_start, QtGui.QColor(
                        255, 255, 255, int(coat_center_alpha * 0.5 * 255)  # White
                    ))
                coat_highlight_gradient.setColorAt(1.0, QtGui.QColor(
                    255, 255, 255, int(coat_edge_alpha * 255)  # White
                ))
                painter.setBrush(QtGui.QBrush(coat_highlight_gradient))
                painter.setPen(QtCore.Qt.NoPen)
                painter.drawEllipse(coat_highlight_rect)
                
                # Then draw with additive blend at 50% opacity (white)
                painter.setCompositionMode(QtGui.QPainter.CompositionMode_Plus)
                coat_highlight_gradient.setColorAt(0.0, QtGui.QColor(
                    255, 255, 255, int(coat_center_alpha * 0.5 * 255)  # White
                ))
                if coat_falloff_start > 0.001:
                    coat_highlight_gradient.setColorAt(coat_falloff_start, QtGui.QColor(
                        255, 255, 255, int(coat_center_alpha * 0.5 * 255)  # White
                    ))
                coat_highlight_gradient.setColorAt(1.0, QtGui.QColor(
                    255, 255, 255, int(coat_edge_alpha * 255)  # White
                ))
                painter.setBrush(QtGui.QBrush(coat_highlight_gradient))
                painter.setPen(QtCore.Qt.NoPen)
                painter.drawEllipse(coat_highlight_rect)
                painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
                
                timings['coat_specular'] = (_time.perf_counter() - coat_specular_start) * 1000.0
                
                # Draw coat left-side gradient (matching specular gradient style)
                # This creates a large soft reflection on the left side of the ball
                if coat_weight > 0.001:
                    coat_gradient_start = _time.perf_counter()
                    
                    # Clamp inputs defensively
                    clamped_coat_roughness = max(0.0, min(1.0, coat_roughness))
                    clamped_coat_weight = max(0.0, min(1.0, coat_weight))
                    
                    # Roughness visibility: 1 at roughness 0, 0.01 at roughness 1
                    coat_roughness_visibility = 1.0 - 0.99 * clamped_coat_roughness
                    
                    # Coat visibility: linear from 0..1, scaled by coat color value
                    coat_r, coat_g, coat_b = coat_color
                    coat_brightness = max(coat_r, coat_g, coat_b)
                    coat_value_factor = coat_brightness
                    coat_value_factor = max(0.0, min(1.0, coat_value_factor))
                    
                    # Reduce coat visibility when emission is high (emission should show through)
                    emission_reduction = 1.0
                    if emission_result is not None:
                        _, emission_intensity_val = emission_result
                        if emission_intensity_val > 0.001:
                            # Linearly reduce coat visibility based on emission intensity
                            # At emission intensity 0: full coat, at 1.0: 50% coat, at 2.0+: 20% coat
                            emission_reduction = max(0.2, 1.0 - (emission_intensity_val * 0.4))
                    
                    coat_visibility = clamped_coat_weight * coat_value_factor * emission_reduction
                    
                    # Combined visibility factor
                    coat_gradient_visibility = coat_roughness_visibility * coat_visibility
                    
                    # Overall scale for this reflection (same as specular)
                    coat_reflection_opacity = coat_gradient_visibility * 0.18  # 0..0.18
                    
                    # Create a strongly left-anchored gradient (same as specular)
                    gradient_start_x = margin                             # Left edge
                    gradient_start_y = margin + circle_diameter           # Bottom area
                    gradient_end_x = margin + int(circle_diameter * 0.35) # About one-third in from the left
                    gradient_end_y = margin                               # Upper area
                    
                    coat_reflection_gradient = QtGui.QLinearGradient(
                        gradient_start_x, gradient_start_y,  # Start from bottom-left
                        gradient_end_x, gradient_end_y  # End at top-right
                    )
                    
                    # Convert opacity to alpha
                    coat_reflection_alpha = int(coat_reflection_opacity * 255)
                    coat_reflection_alpha = max(0, min(255, coat_reflection_alpha))
                    
                    # Draw a large rectangle covering the entire circle area with the gradient
                    gradient_rect = QtCore.QRect(margin, margin, circle_diameter, circle_diameter)
                    
                    # Clip to the circle to ensure it follows the curve perfectly
                    circle_clip_path = QtGui.QPainterPath()
                    circle_clip_path.addEllipse(circle_rect)
                    painter.setClipPath(circle_clip_path)
                    
                    # Coat gradient should be additive like specular (matching specular behavior)
                    # Use half-additive blend: normal (50%) + additive (50%) like specular
                    # First draw with normal blend at 50% opacity
                    painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
                    coat_reflection_gradient.setColorAt(0.0, QtGui.QColor(
                        int(coat_r * 255), int(coat_g * 255), int(coat_b * 255), int(coat_reflection_alpha * 0.5)))
                    coat_reflection_gradient.setColorAt(0.2, QtGui.QColor(
                        int(coat_r * 255), int(coat_g * 255), int(coat_b * 255), int(coat_reflection_alpha * 0.5 * 0.9)))
                    coat_reflection_gradient.setColorAt(0.5, QtGui.QColor(
                        int(coat_r * 255), int(coat_g * 255), int(coat_b * 255), int(coat_reflection_alpha * 0.5 * 0.5)))
                    coat_reflection_gradient.setColorAt(0.8, QtGui.QColor(
                        int(coat_r * 255), int(coat_g * 255), int(coat_b * 255), int(coat_reflection_alpha * 0.5 * 0.2)))
                    coat_reflection_gradient.setColorAt(1.0, QtGui.QColor(
                        int(coat_r * 255), int(coat_g * 255), int(coat_b * 255), 0))
                    painter.setBrush(QtGui.QBrush(coat_reflection_gradient))
                    painter.setPen(QtCore.Qt.NoPen)
                    painter.drawRect(gradient_rect)
                    
                    # Then draw with additive blend at 50% opacity
                    painter.setCompositionMode(QtGui.QPainter.CompositionMode_Plus)
                    coat_reflection_gradient.setColorAt(0.0, QtGui.QColor(
                        int(coat_r * 255), int(coat_g * 255), int(coat_b * 255), int(coat_reflection_alpha * 0.5)))
                    coat_reflection_gradient.setColorAt(0.2, QtGui.QColor(
                        int(coat_r * 255), int(coat_g * 255), int(coat_b * 255), int(coat_reflection_alpha * 0.5 * 0.9)))
                    coat_reflection_gradient.setColorAt(0.5, QtGui.QColor(
                        int(coat_r * 255), int(coat_g * 255), int(coat_b * 255), int(coat_reflection_alpha * 0.5 * 0.5)))
                    coat_reflection_gradient.setColorAt(0.8, QtGui.QColor(
                        int(coat_r * 255), int(coat_g * 255), int(coat_b * 255), int(coat_reflection_alpha * 0.5 * 0.2)))
                    coat_reflection_gradient.setColorAt(1.0, QtGui.QColor(
                        int(coat_r * 255), int(coat_g * 255), int(coat_b * 255), 0))
                    painter.setBrush(QtGui.QBrush(coat_reflection_gradient))
                    painter.setPen(QtCore.Qt.NoPen)
                    painter.drawRect(gradient_rect)
                    painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
                    
                    # Reset clipping
                    painter.setClipping(False)
                    timings['coat_gradient'] = (_time.perf_counter() - coat_gradient_start) * 1000.0
            
            timings['specular'] = (_time.perf_counter() - specular_start) * 1000.0
            
            painter_end_start = _time.perf_counter()
            painter.end()
            timings['painter_end'] = (_time.perf_counter() - painter_end_start) * 1000.0
            
            # Convert to QPixmap and scale to display size
            pixmap_start = _time.perf_counter()
            pixmap = QtGui.QPixmap.fromImage(image)
            # Always scale to exact size for crisp display
            if pixmap.width() != size or pixmap.height() != size:
                pixmap = pixmap.scaled(
                    size, size,
                    QtCore.Qt.IgnoreAspectRatio,  # Use IgnoreAspectRatio to ensure exact size
                    QtCore.Qt.SmoothTransformation
                )
            timings['pixmap'] = (_time.perf_counter() - pixmap_start) * 1000.0
            
            swatch_duration = (_time.perf_counter() - swatch_start) * 1000.0
            
            # Cache the generated swatch (only for non-fast-mode, as fast mode is temporary)
            cache_status = "new"
            if not fast_mode and pixmap and not pixmap.isNull():
                swatch_hash = _get_material_swatch_hash(material_name, fast_mode=False)
                if swatch_hash:
                    cache_key = (material_name, swatch_hash, size)
                    # Check if this was a cache hit (we would have returned early if it was)
                    if cache_key not in _swatch_cache:
                        _swatch_cache[cache_key] = pixmap
                        # Limit cache size to prevent memory bloat (keep last 1000 entries)
                        if len(_swatch_cache) > 1000:
                            # Remove oldest entries (simple FIFO - remove first 200)
                            keys_to_remove = list(_swatch_cache.keys())[:200]
                            for key in keys_to_remove:
                                _swatch_cache.pop(key, None)
                    else:
                        cache_status = "cached"
            
            # Log detailed breakdown (only for first few swatches or every 50th to avoid spam)
            if not hasattr(self, '_detailed_log_counter'):
                self._detailed_log_counter = 0
            self._detailed_log_counter += 1
            
            
            return pixmap
            
        except Exception as e:
            import traceback
            print(f"[MaterialSwatchIcon] Error creating swatch icon for {material_name}: {e}")
            traceback.print_exc()
            return None
    
    def _get_emission_data(self, material_name):
        """Get emission/incandescence data from the material.
        Returns (emission_color_tuple, emission_intensity) or None if no emission."""
        try:
            node_type = cmds.nodeType(material_name)
            emission = None
            emission_intensity = 0.0
            
            if node_type in ["standardSurface", "aiStandardSurface"]:
                if cmds.attributeQuery("emission", node=material_name, exists=True):
                    try:
                        # Always try to get emission intensity value first
                        # Check if emission has a texture connection
                        emission_connections = cmds.listConnections(f"{material_name}.emission", s=True, d=False)
                        if emission_connections:
                            # Texture connected to emission weight - try to get average value
                            emission_texture_node = emission_connections[0]
                            if cmds.nodeType(emission_texture_node) == "file":
                                emission_intensity = self._get_texture_average_value(emission_texture_node)
                            else:
                                # For non-file textures, use 0.5 as default
                                emission_intensity = 0.5
                        else:
                            # Get emission intensity value directly - allow values up to 10.0
                            emission_intensity = float(cmds.getAttr(f"{material_name}.emission"))
                            emission_intensity = max(0.0, min(10.0, emission_intensity))
                        
                        # Always try to get color if no texture connection, regardless of intensity value
                        # (intensity might be 0, but we still need to know if there's a color/texture)
                        if cmds.attributeQuery("emissionColor", node=material_name, exists=True):
                            # Check if emissionColor has a texture connection
                            emission_color_connections = cmds.listConnections(f"{material_name}.emissionColor", s=True, d=False)
                            if not emission_color_connections:
                                # No texture on emissionColor, try to get color value (only if intensity > 0)
                                if emission_intensity > 0:
                                    try:
                                        emission_color = cmds.getAttr(f"{material_name}.emissionColor")[0]
                                        if isinstance(emission_color, (list, tuple)) and len(emission_color) >= 3:
                                            if isinstance(emission_color[0], (list, tuple)):
                                                emission = tuple(float(x) for x in emission_color[0][:3])
                                            else:
                                                emission = tuple(float(x) for x in emission_color[:3])
                                        if emission:
                                            emission = tuple(float(x) * emission_intensity for x in emission[:3])
                                    except:
                                        pass
                            # If emissionColor has texture, emission will remain None but intensity will be returned
                            # This ensures we always return the intensity value even when there's a texture
                    except:
                        pass
            elif node_type in ["lambert", "blinn", "phong"]:
                if cmds.attributeQuery("incandescence", node=material_name, exists=True):
                    try:
                        incandescence = cmds.getAttr(f"{material_name}.incandescence")[0]
                        if isinstance(incandescence, (list, tuple)) and len(incandescence) >= 3:
                            if isinstance(incandescence[0], (list, tuple)):
                                emission = tuple(float(x) for x in incandescence[0][:3])
                            else:
                                emission = tuple(float(x) for x in incandescence[:3])
                        # For legacy shaders, use max color component as intensity,
                        # but allow it to go up to 10.0 (similar to standardSurface emission weight).
                        if emission:
                            max_channel = max(emission[0], emission[1], emission[2])
                            # Treat incandescence value as 0–10 range for glow intensity
                            emission_intensity = max(0.0, min(10.0, float(max_channel) * 10.0))
                    except:
                        pass
            
            if emission and len(emission) >= 3:
                return (tuple(float(x) for x in emission[:3]), emission_intensity)
            elif emission_intensity is not None:
                # Return intensity even if no color (for texture-only emission or when intensity is 0)
                # This ensures we always know the intensity value, even if it's 0
                return (None, emission_intensity)
            return None
        except Exception:
            return None
    
    def _apply_metalness_darkening(self, material_name, base_color):
        """Apply metalness contrast increase to the base color.
        Metalness now increases contrast rather than just darkening."""
        try:
            node_type = cmds.nodeType(material_name)
            metalness = None
            
            if node_type in ["standardSurface", "aiStandardSurface"]:
                if cmds.attributeQuery("metalness", node=material_name, exists=True):
                    try:
                        metalness = cmds.getAttr(f"{material_name}.metalness")
                        if metalness is not None:
                            metalness = float(metalness)
                    except:
                        pass
            
            if metalness is not None and metalness > 0:
                # Apply darkening with increased contrast
                # At metalness 1.0: moderate darkening (50%) + increased contrast
                # Contrast makes darks darker and slightly reduces brights, but overall darkens
                base_darken = 1.0 - (metalness * 0.5)  # 50% darkening at max (reduced from 75%)
                contrast_strength = metalness * 1.2  # Contrast strength (increased by 50% from 0.8)
                
                def apply_metalness_effect(value, darken, contrast):
                    """Apply metalness: darken overall and increase contrast.
                    Contrast pushes darks darker and slightly reduces brights."""
                    # First apply base darkening
                    darkened = value * darken
                    
                    # Then apply contrast: push values away from 0.5, but bias toward darkening
                    # Values below 0.5 get pushed darker, values above 0.5 get pushed toward 0.5 (darker)
                    centered = darkened - 0.5
                    # Asymmetric contrast: darks get darker faster than brights get brighter
                    if centered < 0:
                        # Dark values: push darker
                        contrasted = centered * (1.0 + contrast * 1.5)
                    else:
                        # Bright values: push toward 0.5 (darker) but less aggressively
                        contrasted = centered * (1.0 - contrast * 0.3)
                    
                    result = contrasted + 0.5
                    return max(0.0, min(1.0, result))
                
                r = apply_metalness_effect(base_color[0], base_darken, contrast_strength)
                g = apply_metalness_effect(base_color[1], base_darken, contrast_strength)
                b = apply_metalness_effect(base_color[2], base_darken, contrast_strength)
                return (r, g, b)
            
            return base_color
        except Exception:
            return base_color
    
    def _get_material_roughness(self, material_name):
        """Get roughness value from a material.
        Returns roughness value (0-1) or None if not available.
        Lower roughness = shinier, higher roughness = more matte.
        Handles texture connections by averaging the texture value."""
        try:
            node_type = cmds.nodeType(material_name)
            roughness = None
            
            if node_type in ["standardSurface", "aiStandardSurface"]:
                if cmds.attributeQuery("specularRoughness", node=material_name, exists=True):
                    try:
                        # Check if roughness has a texture connection
                        roughness_connections = cmds.listConnections(f"{material_name}.specularRoughness", s=True, d=False)
                        if roughness_connections:
                            # Texture connected - average the texture value
                            roughness_texture_node = roughness_connections[0]
                            if cmds.nodeType(roughness_texture_node) == "file":
                                roughness = self._get_texture_average_value(roughness_texture_node)
                            else:
                                roughness = 0.5  # Default for non-file textures
                        else:
                            # Get roughness value
                            roughness = cmds.getAttr(f"{material_name}.specularRoughness")
                            if roughness is not None:
                                roughness = float(roughness)
                        if roughness is not None:
                            return max(0.0, min(1.0, roughness))
                    except:
                        pass
            elif node_type == "phong":
                # Phong uses cosinePower - higher power = shinier (lower roughness)
                # Convert cosinePower to roughness using the same formula as material_manager
                try:
                    if cmds.attributeQuery("cosinePower", node=material_name, exists=True):
                        cosine_power = cmds.getAttr(f"{material_name}.cosinePower")
                        if cosine_power is not None:
                            # Convert cosinePower to roughness
                            # Formula: roughness = sqrt(2.0 / (cosinePower + 2.0))
                            # Higher cosinePower (e.g., 100) = lower roughness (shinier)
                            # Lower cosinePower (e.g., 2) = higher roughness (matte)
                            n = max(0.001, float(cosine_power))
                            roughness = max(0.0, min(1.0, math.sqrt(2.0 / (n + 2.0))))
                            return roughness
                except:
                    pass
            elif node_type == "blinn":
                # Blinn uses eccentricity - higher eccentricity = more matte (higher roughness)
                try:
                    if cmds.attributeQuery("eccentricity", node=material_name, exists=True):
                        eccentricity = cmds.getAttr(f"{material_name}.eccentricity")
                        # Eccentricity ranges from 0 to ~1, where 0 = very shiny
                        # Convert to roughness: higher eccentricity = higher roughness
                        roughness = float(eccentricity)
                        return roughness
                except:
                    pass
            
            return None
        except Exception:
            return None
    
    def _get_material_color(self, material_name, fast_mode=False):
        """Extract the base color from a material."""
        import time as _time
        color_start = _time.perf_counter()
        
        try:
            node_type_start = _time.perf_counter()
            node_type = cmds.nodeType(material_name)
            node_type_duration = (_time.perf_counter() - node_type_start) * 1000.0
            
            color_attr = None
            color = None
            
            if node_type in ["standardSurface", "aiStandardSurface"]:
                if cmds.attributeQuery("baseColor", node=material_name, exists=True):
                    color_attr = f"{material_name}.baseColor"
            elif node_type in ["lambert", "blinn", "phong"]:
                if cmds.attributeQuery("color", node=material_name, exists=True):
                    color_attr = f"{material_name}.color"
            elif node_type == "surfaceShader":
                if cmds.attributeQuery("outColor", node=material_name, exists=True):
                    color_attr = f"{material_name}.outColor"
            elif node_type == "useBackground":
                color = (0.2, 0.2, 0.2)
            elif node_type == "layeredShader":
                color = (0.5, 0.5, 0.5)
            elif node_type == "displacementShader":
                color = (0.3, 0.3, 0.3)
            elif node_type == "volumeShader":
                color = (0.4, 0.4, 0.4)
            
            if color is None and color_attr:
                try:
                    connections_start = _time.perf_counter()
                    connections = cmds.listConnections(color_attr, s=True, d=False)
                    connections_duration = (_time.perf_counter() - connections_start) * 1000.0
                    
                    if connections:
                        if fast_mode:
                            color = (0.7, 0.7, 0.7)  # Light grey for texture
                        else:
                            # Try to get average color from texture (slower)
                            texture_color_start = _time.perf_counter()
                            texture_color = self._get_texture_average_color(connections[0], color_attr)
                            texture_color_duration = (_time.perf_counter() - texture_color_start) * 1000.0
                            
                            if texture_color:
                                color = texture_color
                            else:
                                try:
                                    color_value = cmds.getAttr(color_attr)
                                    if isinstance(color_value, (list, tuple)):
                                        if len(color_value) >= 3:
                                            if isinstance(color_value[0], (list, tuple)):
                                                color = tuple(float(x) for x in color_value[0][:3])
                                            else:
                                                color = tuple(float(x) for x in color_value[:3])
                                    if not color or color == (0.5, 0.5, 0.5):
                                        color = (0.7, 0.7, 0.7)
                                except:
                                    color = (0.7, 0.7, 0.7)
                    else:
                        try:
                            color_value = cmds.getAttr(color_attr)
                            if isinstance(color_value, (list, tuple)):
                                if len(color_value) > 0 and isinstance(color_value[0], (list, tuple)):
                                    inner_tuple = color_value[0]
                                    if len(inner_tuple) >= 3:
                                        color = tuple(float(x) for x in inner_tuple[:3])
                                elif len(color_value) >= 3:
                                    color = tuple(float(x) for x in color_value[:3])
                        except Exception:
                            try:
                                base_attr_name = color_attr.split('.')[-1]
                                if cmds.attributeQuery(base_attr_name + 'R', node=material_name, exists=True):
                                    r_val = cmds.getAttr(f"{material_name}.{base_attr_name}R")
                                    g_val = cmds.getAttr(f"{material_name}.{base_attr_name}G")
                                    b_val = cmds.getAttr(f"{material_name}.{base_attr_name}B")
                                    color = (float(r_val), float(g_val), float(b_val))
                            except:
                                pass
                except Exception:
                    pass
            
            if color is None:
                color = (0.5, 0.5, 0.5)
            
            color = (
                max(0.0, min(1.0, float(color[0]))),
                max(0.0, min(1.0, float(color[1]))),
                max(0.0, min(1.0, float(color[2])))
            )
            
            color_duration = (_time.perf_counter() - color_start) * 1000.0
            
            # Log detailed color extraction timing (only for first few or every 50th)
            if not hasattr(self, '_color_log_counter'):
                self._color_log_counter = 0
            self._color_log_counter += 1
            
            if self._color_log_counter <= 5 or self._color_log_counter % 50 == 0:
                parts = []
                if node_type_duration > 0.1:
                    parts.append(f"node_type={node_type_duration:.2f}ms")
                if 'connections_duration' in locals() and connections_duration > 0.1:
                    parts.append(f"connections={connections_duration:.2f}ms")
                if 'texture_color_duration' in locals() and texture_color_duration > 0.1:
                    parts.append(f"texture_color={texture_color_duration:.2f}ms")
            
            return color
        except Exception as e:
            return (0.5, 0.5, 0.5)
    
    def _get_texture_average_color(self, texture_node, color_attr):
        """Get average color from a texture node."""
        try:
            node_type = cmds.nodeType(texture_node)
            
            if node_type == "file":
                return self._get_file_texture_average_color(texture_node)
            elif node_type in ["checker", "noise", "ramp", "grid"]:
                return self._get_procedural_texture_average_color(texture_node)
            else:
                try:
                    if cmds.attributeQuery("outColor", node=texture_node, exists=True):
                        color_value = cmds.getAttr(f"{texture_node}.outColor")[0]
                        if isinstance(color_value, (list, tuple)) and len(color_value) >= 3:
                            return tuple(float(x) for x in color_value[:3])
                except:
                    pass
                return None
        except Exception:
            return None
    
    def _get_file_texture_path(self, file_node):
        """Get the file path from a file texture node, with path resolution."""
        try:
            texture_path = cmds.getAttr(f"{file_node}.fileTextureName")
            if not texture_path:
                return None
            
            # Check if this is a UDIM texture (placeholder or already has tile number)
            is_udim = False
            if "<UDIM>" in texture_path or "<u>" in texture_path.lower():
                is_udim = True
            else:
                # Check if path already contains a UDIM tile number (1001-1999)
                import re
                udim_match = re.search(r'\.(\d{4})\.', os.path.basename(texture_path))
                if udim_match:
                    tile_num = int(udim_match.group(1))
                    if 1001 <= tile_num <= 1999:
                        is_udim = True
            
            if is_udim:
                # UDIM texture - find and use the first available tile
                first_tile_path = self._find_first_udim_tile(texture_path)
                if first_tile_path and os.path.exists(first_tile_path):
                    return first_tile_path
                # If first tile not found, fall back to None (will use average color)
                return None
            
            # Non-UDIM texture - check if file exists, try project directory if not
            if not os.path.exists(texture_path):
                try:
                    project_dir = cmds.workspace(query=True, rootDirectory=True)
                    if project_dir:
                        resolved = os.path.join(project_dir, "sourceimages", texture_path)
                        if os.path.exists(resolved):
                            texture_path = resolved
                except:
                    pass
            
            if not texture_path or not os.path.exists(texture_path):
                return None
            
            return texture_path
        except Exception:
            return None
    
    def _find_first_udim_tile(self, texture_path):
        """Find the first available UDIM tile (1001) for a UDIM texture path.
        
        Args:
            texture_path: Path that may contain <UDIM> or <u> placeholder, or already have a tile number
            
        Returns:
            Path to first available tile (1001), or None if not found
        """
        try:
            import re
            
            # If path already has a tile number, extract the directory and pattern
            dir_path = os.path.dirname(texture_path)
            base_name = os.path.basename(texture_path)
            
            # Check if it already has a UDIM number
            udim_match = re.search(r'\.(\d{4})\.', base_name)
            if udim_match:
                # Replace existing tile number with 1001
                tile_num = udim_match.group(1)
                parts = base_name.split(f'.{tile_num}.')
                if len(parts) == 2:
                    prefix, ext = parts
                    first_tile_name = f"{prefix}.1001.{ext}"
                    first_tile_path = os.path.join(dir_path, first_tile_name)
                    if os.path.exists(first_tile_path):
                        return first_tile_path
                    
                    # If 1001 doesn't exist, search for any available tile in the directory
                    if os.path.isdir(dir_path):
                        tiles = []
                        for f in os.listdir(dir_path):
                            # Match pattern: prefix.XXXX.ext
                            match = re.match(rf'^{re.escape(prefix)}\.(\d{{4}})\.{re.escape(ext)}$', f)
                            if match:
                                tile_num_found = int(match.group(1))
                                if 1001 <= tile_num_found <= 1999:  # Valid UDIM range
                                    tiles.append((tile_num_found, f))
                        
                        if tiles:
                            # Sort by tile number and return the first (lowest) one
                            tiles.sort(key=lambda x: x[0])
                            return os.path.join(dir_path, tiles[0][1])
            else:
                # Path has <UDIM> or <u> placeholder
                # Replace placeholder with 1001
                first_tile_path = texture_path.replace("<UDIM>", "1001").replace("<u>", "1001").replace("<U>", "1001")
                if os.path.exists(first_tile_path):
                    return first_tile_path
                
                # If direct replacement doesn't work, try to find any tile in the directory
                if os.path.isdir(dir_path):
                    # Look for any file with a UDIM pattern (1001-1999)
                    for f in os.listdir(dir_path):
                        match = re.search(r'\.(\d{4})\.', f)
                        if match:
                            tile_num_found = int(match.group(1))
                            if 1001 <= tile_num_found <= 1999:
                                # Found a valid UDIM tile, use it
                                return os.path.join(dir_path, f)
            
            return None
        except Exception:
            return None
    
    def _find_file_texture_node(self, node, visited=None, max_depth=10):
        """Recursively traverse connection chain to find a file texture node.
        
        This handles cases where utility nodes (like colorCorrect, multiply, etc.)
        are between the material attribute and the actual file texture.
        
        Args:
            node: Starting node to search from
            visited: Set of already visited nodes to prevent infinite loops
            max_depth: Maximum recursion depth to prevent infinite loops
        
        Returns:
            File texture node name if found, None otherwise
        """
        if visited is None:
            visited = set()
        
        if max_depth <= 0 or node in visited:
            return None
        
        visited.add(node)
        
        try:
            node_type = cmds.nodeType(node)
            
            # If this is a file texture node, return it
            if node_type == "file":
                return node
            
            # For utility nodes, check common input attributes
            # These are common attributes that might connect to file textures
            input_attrs = []
            
            # Common input attributes to check for various utility node types
            common_input_attrs = [
                "input", "input1", "input2", "input3",
                "color1", "color2", "color3",
                "value", "value1", "value2",
                "tex", "texture", "texture1", "texture2"
            ]
            
            # Check all common input attributes
            for attr_name in common_input_attrs:
                if cmds.attributeQuery(attr_name, node=node, exists=True):
                    input_attrs.append(f"{node}.{attr_name}")
            
            # For nodes with outColor (most utility nodes), also check specific attributes
            if cmds.attributeQuery("outColor", node=node, exists=True):
                # Most utility nodes output outColor, so check their inputs
                # This covers: colorCorrect, multiplyDivide, blendColors, etc.
                pass  # Already handled by common_input_attrs above
            
            # Search through all input attributes
            for input_attr in input_attrs:
                try:
                    connections = cmds.listConnections(input_attr, s=True, d=False)
                    if connections:
                        for connected_node in connections:
                            result = self._find_file_texture_node(connected_node, visited, max_depth - 1)
                            if result:
                                return result
                except Exception:
                    continue
            
        except Exception:
            pass
        
        return None
    
    def _get_file_texture_image(self, file_node, size=64):
        """Get the texture image as a QPixmap, cached for performance."""
        import time as _time
        global _texture_image_cache
        
        texture_start = _time.perf_counter()
        timings = {}
        
        try:
            path_start = _time.perf_counter()
            texture_path = self._get_file_texture_path(file_node)
            timings['path'] = (_time.perf_counter() - path_start) * 1000.0
            
            if not texture_path:
                return None
            
            # Create cache key with size (tuple: path, mtime, size)
            base_cache_key = _get_cache_key(texture_path)
            cache_key = (base_cache_key[0], base_cache_key[1], size) if base_cache_key else None
            
            cache_check_start = _time.perf_counter()
            if cache_key and cache_key in _texture_image_cache:
                timings['cache_check'] = (_time.perf_counter() - cache_check_start) * 1000.0
                texture_duration = (_time.perf_counter() - texture_start) * 1000.0
                return _texture_image_cache[cache_key]
            timings['cache_check'] = (_time.perf_counter() - cache_check_start) * 1000.0
            
            # Load and scale image
            load_start = _time.perf_counter()
            image = QtGui.QImage(texture_path)
            if image.isNull():
                # Debug: log failed image loads (only first few)
                if not hasattr(self, '_image_load_error_counter'):
                    self._image_load_error_counter = 0
                self._image_load_error_counter += 1
                return None
            
            # Scale to target size (square, keep aspect ratio)
            scale_start = _time.perf_counter()
            if image.width() != size or image.height() != size:
                image = image.scaled(
                    size, size,
                    QtCore.Qt.KeepAspectRatio,
                    QtCore.Qt.SmoothTransformation
                )
            timings['scale'] = (_time.perf_counter() - scale_start) * 1000.0
            timings['load'] = (_time.perf_counter() - load_start) * 1000.0
            
            # Convert to pixmap
            pixmap = QtGui.QPixmap.fromImage(image)
            
            if pixmap and not pixmap.isNull() and cache_key:
                cache_store_start = _time.perf_counter()
                _texture_image_cache[cache_key] = pixmap
                timings['cache_store'] = (_time.perf_counter() - cache_store_start) * 1000.0
            
            texture_duration = (_time.perf_counter() - texture_start) * 1000.0
            return pixmap
        except Exception:
            return None
    
    def _get_file_texture_average_color(self, file_node):
        """Get average color from a file texture node."""
        import time as _time
        global _texture_color_cache
        
        texture_start = _time.perf_counter()
        timings = {}
        
        try:
            path_start = _time.perf_counter()
            texture_path = self._get_file_texture_path(file_node)
            timings['path'] = (_time.perf_counter() - path_start) * 1000.0
            
            if not texture_path:
                return None
            
            cache_key = _get_cache_key(texture_path)
            cache_check_start = _time.perf_counter()
            if cache_key and cache_key in _texture_color_cache:
                timings['cache_check'] = (_time.perf_counter() - cache_check_start) * 1000.0
                texture_duration = (_time.perf_counter() - texture_start) * 1000.0
                return _texture_color_cache[cache_key]
            timings['cache_check'] = (_time.perf_counter() - cache_check_start) * 1000.0
            
            calc_start = _time.perf_counter()
            avg_color = self._calculate_image_average_color(texture_path)
            timings['calc'] = (_time.perf_counter() - calc_start) * 1000.0
            
            if avg_color and cache_key:
                cache_store_start = _time.perf_counter()
                _texture_color_cache[cache_key] = avg_color
                timings['cache_store'] = (_time.perf_counter() - cache_store_start) * 1000.0
            
            texture_duration = (_time.perf_counter() - texture_start) * 1000.0
            
            # Log detailed texture color timing (only for first few or every 50th)
            if not hasattr(self, '_texture_detail_log_counter'):
                self._texture_detail_log_counter = 0
            self._texture_detail_log_counter += 1
            
            
            return avg_color
        except Exception:
            return None
    
    def _calculate_image_average_color(self, image_path):
        """Calculate average color from an image file."""
        import time as _time
        
        try:
            load_start = _time.perf_counter()
            image = QtGui.QImage(image_path)
            load_duration = (_time.perf_counter() - load_start) * 1000.0
            
            if image.isNull():
                return None
            
            scale_start = _time.perf_counter()
            sample_size = 32  # Even smaller for icons
            if image.width() > sample_size or image.height() > sample_size:
                image = image.scaled(
                    sample_size, sample_size,
                    QtCore.Qt.KeepAspectRatio,
                    QtCore.Qt.FastTransformation
                )
            scale_duration = (_time.perf_counter() - scale_start) * 1000.0
            
            sample_start = _time.perf_counter()
            sample_step = max(1, min(image.width(), image.height()) // 8)  # Sample fewer points
            total_r = 0.0
            total_g = 0.0
            total_b = 0.0
            sample_count = 0
            
            for y in range(0, image.height(), sample_step):
                for x in range(0, image.width(), sample_step):
                    pixel = image.pixel(x, y)
                    color = QtGui.QColor.fromRgba(pixel)
                    total_r += color.red()
                    total_g += color.green()
                    total_b += color.blue()
                    sample_count += 1
            
            if sample_count > 0:
                avg_r = (total_r / sample_count) / 255.0
                avg_g = (total_g / sample_count) / 255.0
                avg_b = (total_b / sample_count) / 255.0
                sample_duration = (_time.perf_counter() - sample_start) * 1000.0
                
                # Log detailed image processing timing (only for first few or every 50th)
                if not hasattr(self, '_image_detail_log_counter'):
                    self._image_detail_log_counter = 0
                self._image_detail_log_counter += 1
                
                if self._image_detail_log_counter <= 5 or self._image_detail_log_counter % 50 == 0:
                    parts = []
                    if load_duration > 0.1:
                        parts.append(f"load={load_duration:.2f}ms")
                    if scale_duration > 0.1:
                        parts.append(f"scale={scale_duration:.2f}ms")
                    if sample_duration > 0.1:
                        parts.append(f"sample={sample_duration:.2f}ms")
                
                return (avg_r, avg_g, avg_b)
            
            return None
        except Exception:
            return None
    
    def _get_procedural_texture_average_color(self, texture_node):
        """Get average color from procedural textures."""
        try:
            if cmds.attributeQuery("outColor", node=texture_node, exists=True):
                try:
                    color_value = cmds.getAttr(f"{texture_node}.outColor")[0]
                    if isinstance(color_value, (list, tuple)) and len(color_value) >= 3:
                        return tuple(float(x) for x in color_value[:3])
                except:
                    pass
            return None
        except Exception:
            return None
    
    def _get_texture_average_value(self, file_node):
        """Get average grayscale value from a file texture node.
        Returns a value from 0.0 (black) to 1.0 (white).
        Used for roughness and specular weight textures."""
        try:
            # Get the texture path
            texture_path = self._get_file_texture_path(file_node)
            if not texture_path:
                return 0.5  # Default if path can't be resolved
            
            # Load the image
            image = QtGui.QImage(texture_path)
            if image.isNull():
                return 0.5  # Default if image can't be loaded
            
            # Scale down for faster sampling
            sample_size = 32
            if image.width() > sample_size or image.height() > sample_size:
                image = image.scaled(
                    sample_size, sample_size,
                    QtCore.Qt.KeepAspectRatio,
                    QtCore.Qt.FastTransformation
                )
            
            # Sample pixels and calculate average grayscale value
            sample_step = max(1, min(image.width(), image.height()) // 8)
            total_value = 0.0
            sample_count = 0
            
            for y in range(0, image.height(), sample_step):
                for x in range(0, image.width(), sample_step):
                    pixel = image.pixel(x, y)
                    color = QtGui.QColor.fromRgba(pixel)
                    # Convert to grayscale using standard weights
                    gray_value = (color.red() * 0.299 + color.green() * 0.587 + color.blue() * 0.114) / 255.0
                    total_value += gray_value
                    sample_count += 1
            
            if sample_count > 0:
                avg_value = total_value / sample_count
                return max(0.0, min(1.0, avg_value))
            
            return 0.5  # Default if no samples
        except Exception:
            return 0.5  # Default on error

