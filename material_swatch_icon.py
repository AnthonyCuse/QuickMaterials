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
        if not cmds.objExists(self.material_name):
            self.clear()
            self._swatch_loaded = True
            return
        
        try:
            # First, create swatch in fast mode for instant display
            pixmap = self._create_swatch_icon(
                self.material_name, 
                self.icon_size,
                fast_mode=True  # Use fast mode for instant loading
            )
            if pixmap and not pixmap.isNull():
                self._swatch_pixmap = pixmap
                self.clear()  # Clear text
                # Force a repaint to show the new swatch
                self.update()
                self._swatch_loaded = True
                
                # Then, update with texture colors if available (async, non-blocking)
                # Check if material has texture connections
                try:
                    node_type = cmds.nodeType(self.material_name)
                    color_attr = None
                    if node_type in ["standardSurface", "aiStandardSurface"]:
                        color_attr = f"{self.material_name}.baseColor"
                    elif node_type in ["lambert", "blinn", "phong"]:
                        color_attr = f"{self.material_name}.color"
                    
                    if color_attr:
                        connections = cmds.listConnections(color_attr, s=True, d=False)
                        if connections:
                            # Has texture - update with texture color asynchronously
                            QtCore.QTimer.singleShot(50, self._update_with_texture_color)
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
            print(f"[MaterialSwatchIcon] Failed to load swatch for {self.material_name}: {e}")
    
    def _update_with_texture_color(self):
        """Update the swatch with texture average color (called asynchronously)."""
        if not cmds.objExists(self.material_name):
            return
        
        try:
            # Recreate swatch with texture colors (not fast mode)
            pixmap = self._create_swatch_icon(
                self.material_name, 
                self.icon_size,
                fast_mode=False  # Calculate texture colors
            )
            if pixmap and not pixmap.isNull():
                self._swatch_pixmap = pixmap
                # Force a repaint to show the updated swatch
                self.update()
        except Exception as e:
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
        """
        try:
            # Get the material color (fast mode skips texture processing)
            original_material_color = self._get_material_color(material_name, fast_mode=fast_mode)
            
            # Check for emission
            emission_data = self._get_emission_data(material_name)
            has_emission = emission_data is not None and any(x > 0.001 for x in emission_data[:3] if isinstance(x, (int, float)))
            
            # Apply metalness darkening
            material_color_with_metalness = self._apply_metalness_darkening(material_name, original_material_color)
            material_color = material_color_with_metalness
            
            # Get roughness value for specular highlight
            roughness = self._get_material_roughness(material_name)
            
            # Get opacity/transmission/transparency values
            opacity = 1.0
            opacity_color = (1.0, 1.0, 1.0)
            transmission = 0.0
            transmission_color = (1.0, 1.0, 1.0)
            transparency = 0.0
            transparency_color = (0.0, 0.0, 0.0)
            node_type = cmds.nodeType(material_name)
            
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
            
            # Calculate effective opacity
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
            specular_softness = self.DEFAULT_SPECULAR_SOFTNESS
            specular_size_multiplier = self.DEFAULT_SPECULAR_SIZE_MULTIPLIER
            specular_position_offset = self.DEFAULT_SPECULAR_POSITION_OFFSET
            transparency_bg_brightness = self.DEFAULT_TRANSPARENCY_BG_BRIGHTNESS
            
            # Create low-resolution image for performance
            # Use small size for icons (32-64px is sufficient for 22px display)
            image_size = max(32, min(size * 2, 64))
            
            # Create QImage with transparent background
            image = QtGui.QImage(image_size, image_size, QtGui.QImage.Format_ARGB32)
            image.fill(QtCore.Qt.transparent)
            
            # Create QPainter
            painter = QtGui.QPainter(image)
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            
            # Set up drawing parameters
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
            blend_factor = 1.0 - transparency_factor
            bg_blend_r = material_color[0] * blend_factor + (bg_color.red() / 255.0) * (1.0 - blend_factor)
            bg_blend_g = material_color[1] * blend_factor + (bg_color.green() / 255.0) * (1.0 - blend_factor)
            bg_blend_b = material_color[2] * blend_factor + (bg_color.blue() / 255.0) * (1.0 - blend_factor)
            material_color = (bg_blend_r, bg_blend_g, bg_blend_b)
            
            bg_blend_orig_r = original_material_color[0] * blend_factor + (bg_color.red() / 255.0) * (1.0 - blend_factor)
            bg_blend_orig_g = original_material_color[1] * blend_factor + (bg_color.green() / 255.0) * (1.0 - blend_factor)
            bg_blend_orig_b = original_material_color[2] * blend_factor + (bg_color.blue() / 255.0) * (1.0 - blend_factor)
            original_material_color = (bg_blend_orig_r, bg_blend_orig_g, bg_blend_orig_b)
            
            center_color = QtGui.QColor(
                int(material_color[0] * 255),
                int(material_color[1] * 255),
                int(material_color[2] * 255)
            )
            
            # Create gradient stops
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
            
            painter.setBrush(QtGui.QBrush(gradient))
            painter.setPen(QtCore.Qt.NoPen)
            painter.drawEllipse(circle_rect)
            
            # Draw emission glow if present
            if has_emission and emission_data:
                emission_strength = max(emission_data[0], emission_data[1], emission_data[2])
                glow_intensity = min(1.0, emission_strength)
                glow_scale = 1.0 if emission_strength <= 1.0 else 1.0 + (emission_strength - 1.0) * 0.5
                glow_scale = max(1.0, glow_scale)
                
                if glow_intensity > 0.001:
                    final_glow_strength = glow_intensity
                    if final_glow_strength > 0.001:
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
                        
                        emission_color_gradient = QtGui.QRadialGradient(glow_center_x, glow_center_y, glow_radius)
                        emission_r = min(1.0, emission_data[0] * 1.5)
                        emission_g = min(1.0, emission_data[1] * 1.5)
                        emission_b = min(1.0, emission_data[2] * 1.5)
                        emission_overlay_alpha = final_glow_strength * 0.6
                        
                        emission_color_gradient.setColorAt(0.0, QtGui.QColor(
                            int(emission_r * 255), int(emission_g * 255), int(emission_b * 255),
                            int(emission_overlay_alpha * 255)
                        ))
                        emission_color_gradient.setColorAt(0.8, QtGui.QColor(
                            int(emission_r * 255), int(emission_g * 255), int(emission_b * 255),
                            int(emission_overlay_alpha * 0.3 * 255)
                        ))
                        emission_color_gradient.setColorAt(1.0, QtGui.QColor(
                            int(emission_r * 255), int(emission_g * 255), int(emission_b * 255), 0
                        ))
                        
                        circle_clip_path = QtGui.QPainterPath()
                        circle_clip_path.addEllipse(circle_rect)
                        painter.setClipPath(circle_clip_path)
                        
                        painter.setBrush(QtGui.QBrush(emission_color_gradient))
                        painter.setPen(QtCore.Qt.NoPen)
                        painter.drawEllipse(glow_rect)
                        
                        white_glow_gradient = QtGui.QRadialGradient(glow_center_x, glow_center_y, glow_radius)
                        white_glow_center_alpha = final_glow_strength * 0.8
                        white_glow_gradient.setColorAt(0.0, QtGui.QColor(255, 255, 255, int(white_glow_center_alpha * 255)))
                        white_glow_gradient.setColorAt(0.7, QtGui.QColor(255, 255, 255, int(white_glow_center_alpha * 0.3 * 255)))
                        white_glow_gradient.setColorAt(1.0, QtGui.QColor(255, 255, 255, 0))
                        
                        painter.setBrush(QtGui.QBrush(white_glow_gradient))
                        painter.setPen(QtCore.Qt.NoPen)
                        painter.drawEllipse(glow_rect)
                        
                        painter.setClipping(False)
            
            # Draw specular highlight if roughness is available
            if roughness is not None:
                highlight_opacity = 1.0 - roughness
                highlight_opacity = max(0.05, min(1.0, highlight_opacity))
                
                min_size_ratio = 0.15
                max_size_ratio = 0.40
                base_size_ratio = min_size_ratio + (max_size_ratio - min_size_ratio) * roughness
                highlight_size_ratio = base_size_ratio * specular_size_multiplier
                highlight_diameter = int(circle_diameter * highlight_size_ratio)
                
                max_possible_diameter = circle_diameter - (margin * 2)
                if highlight_diameter > max_possible_diameter:
                    highlight_diameter = max_possible_diameter
                
                base_offset_ratio = 0.15
                shine_offset_ratio = 0.23
                offset_ratio = shine_offset_ratio + (base_offset_ratio - shine_offset_ratio) * roughness
                
                diagonal_offset = 0.03 + (specular_position_offset * 0.1)
                highlight_offset_x = int(circle_diameter * (offset_ratio + diagonal_offset))
                highlight_offset_y = int(circle_diameter * (offset_ratio + diagonal_offset - 0.02 - (specular_position_offset * 0.05)))
                
                highlight_center_x = margin + circle_diameter - highlight_offset_x - (highlight_diameter // 2)
                highlight_center_y = margin + highlight_offset_y + (highlight_diameter // 2)
                
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
                
                center_alpha = highlight_opacity
                edge_alpha = 0.0
                falloff_start = 1.0 - specular_softness
                falloff_start = max(0.0, min(1.0, falloff_start))
                
                highlight_gradient.setColorAt(0.0, QtGui.QColor(255, 255, 255, int(center_alpha * 255)))
                if falloff_start > 0.001:
                    highlight_gradient.setColorAt(falloff_start, QtGui.QColor(255, 255, 255, int(center_alpha * 255)))
                highlight_gradient.setColorAt(1.0, QtGui.QColor(255, 255, 255, int(edge_alpha * 255)))
                
                painter.setBrush(QtGui.QBrush(highlight_gradient))
                painter.setPen(QtCore.Qt.NoPen)
                painter.drawEllipse(highlight_rect)
            
            painter.end()
            
            # Convert to QPixmap and scale to display size
            pixmap = QtGui.QPixmap.fromImage(image)
            # Always scale to exact size for crisp display
            if pixmap.width() != size or pixmap.height() != size:
                pixmap = pixmap.scaled(
                    size, size,
                    QtCore.Qt.IgnoreAspectRatio,  # Use IgnoreAspectRatio to ensure exact size
                    QtCore.Qt.SmoothTransformation
                )
            
            return pixmap
            
        except Exception as e:
            import traceback
            print(f"[MaterialSwatchIcon] Error creating swatch icon for {material_name}: {e}")
            traceback.print_exc()
            return None
    
    def _get_emission_data(self, material_name):
        """Get emission/incandescence data from the material."""
        try:
            node_type = cmds.nodeType(material_name)
            emission = None
            
            if node_type in ["standardSurface", "aiStandardSurface"]:
                if cmds.attributeQuery("emission", node=material_name, exists=True):
                    try:
                        emission_intensity = cmds.getAttr(f"{material_name}.emission")
                        if emission_intensity and emission_intensity > 0:
                            if cmds.attributeQuery("emissionColor", node=material_name, exists=True):
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
                    except:
                        pass
            
            if emission and len(emission) >= 3:
                return tuple(float(x) for x in emission[:3])
            return None
        except Exception:
            return None
    
    def _apply_metalness_darkening(self, material_name, base_color):
        """Apply metalness darkening to the base color."""
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
                darken_factor = 1.0 - (metalness * 0.75)
                r = base_color[0] * darken_factor
                g = base_color[1] * darken_factor
                b = base_color[2] * darken_factor
                return (r, g, b)
            
            return base_color
        except Exception:
            return base_color
    
    def _get_material_roughness(self, material_name):
        """Get roughness value from a material.
        Returns roughness value (0-1) or None if not available.
        Lower roughness = shinier, higher roughness = more matte."""
        try:
            node_type = cmds.nodeType(material_name)
            roughness = None
            
            if node_type in ["standardSurface", "aiStandardSurface"]:
                if cmds.attributeQuery("specularRoughness", node=material_name, exists=True):
                    try:
                        roughness = cmds.getAttr(f"{material_name}.specularRoughness")
                        if roughness is not None:
                            return float(roughness)
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
        try:
            node_type = cmds.nodeType(material_name)
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
                    connections = cmds.listConnections(color_attr, s=True, d=False)
                    
                    if connections:
                        if fast_mode:
                            color = (0.7, 0.7, 0.7)  # Light grey for texture
                        else:
                            # Try to get average color from texture (slower)
                            texture_color = self._get_texture_average_color(connections[0], color_attr)
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
    
    def _get_file_texture_average_color(self, file_node):
        """Get average color from a file texture node."""
        global _texture_color_cache
        
        try:
            texture_path = cmds.getAttr(f"{file_node}.fileTextureName")
            if not texture_path or not os.path.exists(texture_path):
                try:
                    if "<UDIM>" in texture_path or "<u>" in texture_path.lower():
                        pass
                    else:
                        project_dir = cmds.workspace(query=True, rootDirectory=True)
                        if project_dir:
                            resolved = os.path.join(project_dir, "sourceimages", texture_path)
                            if os.path.exists(resolved):
                                texture_path = resolved
                except:
                    pass
            
            if not texture_path or not os.path.exists(texture_path):
                return None
            
            cache_key = _get_cache_key(texture_path)
            if cache_key and cache_key in _texture_color_cache:
                return _texture_color_cache[cache_key]
            
            avg_color = self._calculate_image_average_color(texture_path)
            
            if avg_color and cache_key:
                _texture_color_cache[cache_key] = avg_color
            
            return avg_color
        except Exception:
            return None
    
    def _calculate_image_average_color(self, image_path):
        """Calculate average color from an image file."""
        try:
            image = QtGui.QImage(image_path)
            
            if image.isNull():
                return None
            
            sample_size = 32  # Even smaller for icons
            if image.width() > sample_size or image.height() > sample_size:
                image = image.scaled(
                    sample_size, sample_size,
                    QtCore.Qt.KeepAspectRatio,
                    QtCore.Qt.FastTransformation
                )
            
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

