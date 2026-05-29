"""
Shader Texture Viewer
---------------------
Displays file textures from Maya materials or from a standalone file node.
Material and Attribute combos list scene hook-ups so you can switch context without a separate UI mode.
"""

import os
import sys
import re

# Qt compatibility for Maya 2024 (PySide2) & Maya 2025 (PySide6)
try:
    # Maya 2025+
    from PySide6 import QtCore, QtWidgets, QtGui
    from shiboken6 import wrapInstance
    QT_LIB = 6
except ImportError:
    # Maya 2024-
    from PySide2 import QtCore, QtWidgets, QtGui
    from shiboken2 import wrapInstance
    QT_LIB = 2

import maya.cmds as cmds
import maya.OpenMayaUI as omui


# ==============================
# Stylesheets (centralized)
# ==============================
# Edit these strings to tweak the look of the entire tool.
STYLE_APP = """
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
            QPushButton:disabled {
                color: #666666;
        border: 1px solid #4a4a4a;
                background-color: #4a4a4a;
            }
"""

STYLE_LABEL_FIELD = "color: #cccccc; font-size: 13px; font-weight: bold; padding-right: 5px;"
STYLE_LABEL_INFO = "color: #cccccc; font-size: 12px;"
STYLE_LABEL_UDIM_PREFIX = "color: #cccccc; font-size: 11px; padding-right: 3px; margin: 0px;"
STYLE_LABEL_UDIM = "color: #6fa3d8; font-size: 12px; min-width: 35px; max-width: 35px; padding: 0px; margin: 0px;"

# ComboBox styles
STYLE_COMBOBOX_MAIN = """
    QComboBox {
        font-family: 'Segoe UI';
        font-size: 12px;
        font-weight: bold;
        color: #ffffff;
        background-color: #444444;
        border: 0px solid #555555;
        border-radius: 6px;
        padding: 2px 5px;
        min-height: 10px;
    }
    QComboBox:hover {
        background-color: #4d4d4d;
        border: 0px solid #666666;
    }
    QComboBox:focus {
        border: 0px solid #888888;
    }
    QComboBox QAbstractItemView {
        background-color: #3a3a3a;
        color: #ffffff;
        border: 1px solid #555555;
        selection-background-color: #555555;
    }
"""

# Redundant alias; enabled style equals main combobox style
STYLE_COMBOBOX_ENABLED = STYLE_COMBOBOX_MAIN

STYLE_COMBOBOX_DISABLED = """
    QComboBox {
        font-family: 'Segoe UI';
        font-size: 12px;
        font-weight: bold;
        color: #666666;
        background-color: #3a3a3a;
        border: 0px solid #444444;
        border-radius: 6px;
        padding: 2px 5px;
        min-height: 10px;
    }
    QComboBox QAbstractItemView {
        background-color: #3a3a3a;
        color: #ffffff;
        border: 0px solid #555555;
        selection-background-color: #555555;
    }
"""

# Buttons
STYLE_BUTTON_SMALL = """
            QPushButton {
                font-family: 'Segoe UI';
        font-size: 12px;
                color: #ffffff;
        background-color: #666666;
        border: 0px solid #444444;
        border-radius: 8px;
        padding: 2px 5px;
            }
            QPushButton:hover {
                background-color: #888888;
            }
            QPushButton:pressed {
                background-color: #1a1a1a;
            }
            QPushButton:disabled {
                color: #666666;
        border: 0px solid #555555;
        background-color: #4a4a4a;
    }
"""

STYLE_BUTTON_ICON = """
            QPushButton {
                font-family: 'Segoe UI';
        font-size: 12px;
                color: #ffffff;
        background-color: #666666;
        border: 0px solid #444444;
        border-radius: 8px;
        padding: 2px 5px;
            }
            QPushButton:hover {
                background-color: #888888;
            }
            QPushButton:pressed {
                background-color: #1a1a1a;
            }
            QPushButton:disabled {
                color: #666666;
        border: 0px solid #555555;
        background-color: #4a4a4a;
    }
"""

# Checkboxes
STYLE_CHECKBOX = """
            QCheckBox {
                font-family: 'Segoe UI';
                font-size: 11px;
                color: #dddddd;
        background-color: #333333;
                border: none;
                border-radius: 6px;
                padding: 2px 6px;
        margin: 1px 0;
            }
    QCheckBox:checked { color: #00f7c8; }
            QCheckBox::indicator {
        width: 12px; height: 12px;
        border: 1px solid #444444; border-radius: 3px; background-color: #2b2b2b;
    }
    QCheckBox::indicator:checked { background-color: #ffffff; border: 1px solid #2b2b2b; }
    QCheckBox::indicator:unchecked { background-color: #2b2b2b; border: 1px solid #444444; }
    QCheckBox::indicator:checked:hover, QCheckBox::indicator:unchecked:hover { border: 1px solid #ffffff; }
    QCheckBox::indicator:checked:pressed, QCheckBox::indicator:unchecked:pressed { background-color: #ffffff; border: 1px solid #ffffff; }
    QCheckBox:disabled { color: #666666; background-color: #3a3a3a; border-radius: 6px; padding: 2px 6px; }
"""

# Image viewer container and label
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

class ShaderTextureViewer(QtWidgets.QDialog):
    """Window that displays textures from materials/shaders or from a file node (same Material/Attribute UI in both cases)."""
    
    def __init__(self, shader_node=None, parent=None, file_node=None, context='material'):
        # Get Maya main window as parent
        if parent is None:
            maya_main_window = omui.MQtUtil.mainWindow()
            if maya_main_window:
                parent = wrapInstance(int(maya_main_window), QtWidgets.QWidget)
        
        super(ShaderTextureViewer, self).__init__(parent)
        
        self._context = context  # 'material' or 'file'
        self.shader_node = shader_node
        self.texture_node = file_node if context == 'file' else None
        self.texture_path = None
        
        # Store material attribute -> file node mapping
        self._material_attributes = {}  # {material_name: {attr_name: file_node}}
        self._materials_with_textures = []  # List of materials that have textures
        
        # Set window properties
        if self._context == 'file' and file_node:
            self.setWindowTitle(f"Texture Viewer - {file_node}")
        elif shader_node:
            self.setWindowTitle(f"Shader Texture Viewer - {shader_node}")
        else:
            self.setWindowTitle("Shader Texture Viewer")
        
        self.setMinimumSize(400, 400)
        self.resize(1024, 1135)
        
        # Set dark theme style matching material manager
        self.setStyleSheet(STYLE_APP)
        
        # Main layout
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(4)
        
        # Header with texture info (wrapped in a non-expanding widget)
        header_layout = QtWidgets.QVBoxLayout()

        # Top row: left-aligned Material and Attribute combo boxes
        top_center_row = QtWidgets.QHBoxLayout()
        combo_layout = QtWidgets.QHBoxLayout()
        # Material combo box
        material_label = QtWidgets.QLabel("Material:")
        material_label.setStyleSheet(STYLE_LABEL_FIELD)
        combo_layout.addWidget(material_label)
        self.material_combo = QtWidgets.QComboBox()
        self.material_combo.setMinimumWidth(150)
        self.material_combo.setStyleSheet(STYLE_COMBOBOX_MAIN)
        self.material_combo.currentTextChanged.connect(self._on_material_changed)
        combo_layout.addWidget(self.material_combo)
        combo_layout.addSpacing(10)
        # Attribute combo box
        attribute_label = QtWidgets.QLabel("Attribute:")
        attribute_label.setStyleSheet(STYLE_LABEL_FIELD)
        combo_layout.addWidget(attribute_label)
        self.attribute_combo = QtWidgets.QComboBox()
        self.attribute_combo.setMinimumWidth(150)
        self.attribute_combo.setStyleSheet(STYLE_COMBOBOX_MAIN)
        self.attribute_combo.currentTextChanged.connect(self._on_attribute_changed)
        combo_layout.addWidget(self.attribute_combo)
        top_center_row.addLayout(combo_layout)
        top_center_row.addStretch()
        header_layout.addLayout(top_center_row)

        # Second row: Info label (left) and Select Node button (right)
        name_row_layout = QtWidgets.QHBoxLayout()
        self.select_node_button = QtWidgets.QPushButton("Select Node")
        self.select_node_button.setToolTip("Select current texture node in Maya")
        self.select_node_button.setStyleSheet(STYLE_BUTTON_SMALL)
        self.select_node_button.clicked.connect(self._select_current_node)
        name_row_layout.addWidget(self.select_node_button)
        name_row_layout.addSpacing(8)
        self.info_label = QtWidgets.QLabel("Loading texture...")
        self.info_label.setStyleSheet(STYLE_LABEL_INFO)
        self.info_label.setTextFormat(QtCore.Qt.RichText)  # Enable HTML formatting
        name_row_layout.addWidget(self.info_label)
        name_row_layout.addStretch()
        header_layout.addLayout(name_row_layout)

        # Third row: controls under the name row (UDIM controls left, zoom/reset right)
        controls_layout = QtWidgets.QHBoxLayout()
        
        # UDIM navigation and toggles (recreated)
        self.udim_nav_layout = QtWidgets.QHBoxLayout()
        self.udim_nav_layout.setSpacing(0)
        
        # "UDIM:" label
        self.udim_prefix_label = QtWidgets.QLabel("UDIM:")
        self.udim_prefix_label.setStyleSheet(STYLE_LABEL_UDIM_PREFIX)
        self.udim_prefix_label.setAlignment(QtCore.Qt.AlignCenter | QtCore.Qt.AlignVCenter)
        self.udim_prefix_label.hide()
        
        # Navigation buttons and current tile label
        self.udim_prev_button = QtWidgets.QPushButton("←")
        self.udim_prev_button.setToolTip("Previous UDIM tile")
        self.udim_prev_button.setFixedWidth(22)
        self.udim_prev_button.setFixedHeight(22)
        self.udim_prev_button.setStyleSheet(STYLE_BUTTON_ICON)
        self.udim_prev_button.clicked.connect(self._previous_udim_tile)
        self.udim_prev_button.hide()
        
        self.udim_label = QtWidgets.QLabel("")
        self.udim_label.setStyleSheet(STYLE_LABEL_UDIM)
        self.udim_label.setAlignment(QtCore.Qt.AlignCenter)
        self.udim_label.hide()
        
        self.udim_next_button = QtWidgets.QPushButton("→")
        self.udim_next_button.setToolTip("Next UDIM tile")
        self.udim_next_button.setFixedWidth(22)
        self.udim_next_button.setFixedHeight(22)
        self.udim_next_button.setStyleSheet(STYLE_BUTTON_ICON)
        self.udim_next_button.clicked.connect(self._next_udim_tile)
        self.udim_next_button.hide()
        
        # Checkboxes: Display All UDIMs, Display All Textures
        self.udim_display_all_checkbox = QtWidgets.QCheckBox("Display All UDIMs")
        self.udim_display_all_checkbox.setFixedHeight(22)
        self.udim_display_all_checkbox.setToolTip("Display all UDIM tiles arranged in a grid")
        self.udim_display_all_checkbox.setStyleSheet(STYLE_CHECKBOX)
        self.udim_display_all_checkbox.stateChanged.connect(self._on_display_all_changed)
        self.udim_display_all_checkbox.hide()
        
        self.display_all_textures_checkbox = QtWidgets.QCheckBox("Display All Textures (Slow to Process)")
        self.display_all_textures_checkbox.setFixedHeight(22)
        self.display_all_textures_checkbox.setToolTip("Display all UDIM tiles from all material attributes in one grid")
        self.display_all_textures_checkbox.setStyleSheet(STYLE_CHECKBOX)
        self.display_all_textures_checkbox.stateChanged.connect(self._on_display_all_textures_changed)
        self.display_all_textures_checkbox.hide()
        
        # Add widgets in order: Display All UDIMs, label, buttons, then Display All Textures
        self.udim_nav_layout.addWidget(self.udim_display_all_checkbox)
        self.udim_nav_layout.addSpacing(8)
        self.udim_nav_layout.addWidget(self.udim_prefix_label)
        self.udim_nav_layout.addSpacing(1)
        self.udim_nav_layout.addWidget(self.udim_prev_button)
        self.udim_nav_layout.addSpacing(-2)  # Negative spacing to tighten gap before label
        self.udim_nav_layout.addWidget(self.udim_label)  # Tight spacing
        self.udim_nav_layout.addSpacing(-2)  # Negative spacing to tighten gap after label
        self.udim_nav_layout.addWidget(self.udim_next_button)  # Tight spacing
        self.udim_nav_layout.addSpacing(10)
        self.udim_nav_layout.addWidget(self.display_all_textures_checkbox)
        self.udim_nav_layout.addSpacing(10)
        controls_layout.addLayout(self.udim_nav_layout)
        
        # Back-compat: display labels are now always on; provide a no-op checkbox to satisfy legacy calls
        class _NoopCheckbox(object):
            def show(self): 
                return
            def hide(self): 
                return
            def setChecked(self, _): 
                return
        self.display_labels_checkbox = _NoopCheckbox()
        
        # Add stretch then zoom +/- and reset to the right
        controls_layout.addStretch()
        # Zoom buttons layout (explicit spacing: 2px between - and +, 4px before Reset)
        zoom_buttons_layout = QtWidgets.QHBoxLayout()
        zoom_buttons_layout.setSpacing(0)
        # Zoom out button
        self.zoom_out_button = QtWidgets.QPushButton("-")
        self.zoom_out_button.setToolTip("Zoom out 10%")
        self.zoom_out_button.setFixedWidth(22)
        self.zoom_out_button.setFixedHeight(22)
        self.zoom_out_button.setStyleSheet(STYLE_BUTTON_ICON)
        self.zoom_in_button = QtWidgets.QPushButton("+")
        self.zoom_in_button.setToolTip("Zoom in 10%")
        self.zoom_in_button.setFixedWidth(22)
        self.zoom_in_button.setFixedHeight(22)
        self.zoom_in_button.setStyleSheet(STYLE_BUTTON_ICON)
        self.zoom_out_button.clicked.connect(self._zoom_out)
        self.zoom_in_button.clicked.connect(self._zoom_in)
        self.reset_button = QtWidgets.QPushButton("Reset")
        self.reset_button.setToolTip("Reset zoom to fit window (Scroll to zoom)")
        self.reset_button.clicked.connect(self._reset_zoom)
        zoom_buttons_layout.addWidget(self.zoom_out_button)
        zoom_buttons_layout.addSpacing(8)
        zoom_buttons_layout.addWidget(self.zoom_in_button)
        zoom_buttons_layout.addSpacing(16)
        zoom_buttons_layout.addWidget(self.reset_button)
        controls_layout.addLayout(zoom_buttons_layout)
        
        header_layout.addLayout(controls_layout)
        header_widget = QtWidgets.QWidget()
        header_widget.setLayout(header_layout)
        header_widget.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
        main_layout.addWidget(header_widget)
        
        # Custom widget for image display (no scroll bars, just zoom)
        self.image_viewer = QtWidgets.QWidget()
        self.image_viewer.setStyleSheet(STYLE_IMAGE_VIEWER)
        self.image_viewer.setMinimumSize(400, 400)
        
        # Image label (directly in the viewer widget)
        self.image_label = QtWidgets.QLabel(self.image_viewer)
        self.image_label.setAlignment(QtCore.Qt.AlignCenter)
        self.image_label.setStyleSheet(STYLE_IMAGE_LABEL)
        self.image_label.setText("Select a material and attribute to view texture...")
        
        main_layout.addWidget(self.image_viewer)
        
        # Store original pixmap for reference
        self._original_pixmap = None
        
        # Manual zoom scale factor (None = fit to UI, otherwise a multiplier)
        self._manual_zoom = None
        
        # UDIM tile information
        self._udim_tiles = []  # List of UDIM tile numbers (e.g., [1001, 1002, 1003])
        self._current_udim_index = 0  # Current tile index in the list
        self._udim_base_path = None  # Base path pattern for constructing tile paths
        self._udim_prefix = None  # Filename prefix before UDIM number
        self._udim_extension = None  # File extension
        self._udim_dir_path = None  # Directory containing UDIM tiles
        self._display_all_udims = False  # Flag for displaying all UDIM tiles in grid
        self._display_all_textures = False  # Flag for displaying all textures from all attributes
        self._display_labels = True  # Labels are always displayed by default
        
        # Setup resize timer for throttling resize events (very short for live scaling)
        self._resize_timer = QtCore.QTimer()
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._on_resize_timer)
        self._pending_resize = False
        
        # Connect resize events to scale image
        self.image_viewer.installEventFilter(self)
        self.image_label.installEventFilter(self)
        # Drag/pan state
        self._is_dragging = False
        self._drag_last_pos = None
        
        # Context-specific setup
        if self._context == 'file' and file_node:
            # Same UI as material mode: combos stay visible; preselect a material/attribute if this file is wired up
            self._prepare_ui_opened_from_texture_list(file_node)
        else:
            self._populate_materials()
            self.material_combo.blockSignals(True)
            try:
                if shader_node:
                    index = self.material_combo.findText(shader_node)
                    if index >= 0:
                        self.material_combo.setCurrentIndex(index)
                        self._populate_attributes(shader_node)
                elif self._materials_with_textures:
                    self.material_combo.setCurrentIndex(0)
                    self._populate_attributes(self._materials_with_textures[0])
            finally:
                self.material_combo.blockSignals(False)
    
    def _scan_scene_material_textures(self):
        """Find materials with file textures and fill self._material_attributes / self._materials_with_textures."""
        self._materials_with_textures = []
        self._material_attributes = {}
        
        # Find all materials (shadingEngine nodes connected to materials)
        all_materials = []
        
        # Get all shading engines
        shading_engines = cmds.ls(type='shadingEngine')
        for sg in shading_engines:
            # Get material connected to surfaceShader
            materials = cmds.listConnections(f"{sg}.surfaceShader", source=True, destination=False) or []
            all_materials.extend(materials)
        
        # Also check for materials directly
        material_types = ['lambert', 'blinn', 'phong', 'phongE', 'anisotropic', 
                         'standardSurface', 'aiStandardSurface', 'surfaceShader']
        for mat_type in material_types:
            mats = cmds.ls(type=mat_type)
            all_materials.extend(mats)
        
        # Remove duplicates
        all_materials = list(set(all_materials))
        
        # For each material, find attributes with file texture nodes
        for material in all_materials:
            try:
                # Get all attributes of the material
                attrs = cmds.listAttr(material, connectable=True) or []
                material_attrs = {}
                
                for attr in attrs:
                    try:
                        # Check if this attribute has a connection
                        connections = cmds.listConnections(f"{material}.{attr}", source=True, destination=False, plugs=True) or []
                        
                        # Follow connections to find file nodes
                        for conn in connections:
                            # Get the source node
                            source_node = conn.split('.')[0]
                            node_type = cmds.nodeType(source_node)
                            
                            # Check if it's a file node or if we need to traverse further
                            if node_type == 'file':
                                material_attrs[attr] = source_node
                                break
                            else:
                                # Check if this node connects to a file node
                                file_nodes = cmds.listConnections(source_node, type='file', source=True, destination=False) or []
                                if file_nodes:
                                    material_attrs[attr] = file_nodes[0]
                                    break
                    except:
                        continue
                
                # If material has any texture attributes, add it
                if material_attrs:
                    self._materials_with_textures.append(material)
                    self._material_attributes[material] = material_attrs
            except:
                continue
    
    def _fill_material_combo(self):
        """Fill material combo from self._materials_with_textures (sorted)."""
        self.material_combo.clear()
        for material in sorted(self._materials_with_textures):
            self.material_combo.addItem(material)
    
    def _populate_materials(self):
        """Scan scene and populate material combo (does not load a texture)."""
        self._scan_scene_material_textures()
        self._fill_material_combo()
    
    def _find_first_material_attribute_for_file(self, file_node):
        """Pick one (material, attribute) pair that uses this file node (deterministic if multiple)."""
        if not file_node:
            return None
        for material in sorted(self._material_attributes.keys()):
            attrs = self._material_attributes[material]
            for attr in sorted(attrs.keys()):
                if attrs[attr] == file_node:
                    return (material, attr)
        return None
    
    def _prepare_ui_opened_from_texture_list(self, file_node):
        """
        Texture-tab / single-file open: same combos as material mode.
        Preselect one material+attribute hook-up when possible; otherwise leave combos blank (index -1).
        Image always loads from the file node passed in from the list.
        """
        self._scan_scene_material_textures()
        self._fill_material_combo()
        pair = self._find_first_material_attribute_for_file(file_node)
        
        self.material_combo.blockSignals(True)
        self.attribute_combo.blockSignals(True)
        
        if pair:
            mat_name, attr_name = pair
            mi = self.material_combo.findText(mat_name)
            if mi >= 0:
                self.material_combo.setCurrentIndex(mi)
            self._populate_attributes(mat_name, preferred_attr=attr_name, skip_load=True)
        else:
            try:
                self.material_combo.setCurrentIndex(-1)
            except Exception:
                pass
            self.attribute_combo.clear()
        
        self.material_combo.blockSignals(False)
        self.attribute_combo.blockSignals(False)
        
        self.load_texture(file_node)
    
    def _populate_attributes(self, material_name, preferred_attr=None, skip_load=False):
        """Populate the attribute combo for the given material and optionally load that texture."""
        self.attribute_combo.clear()
        
        if not material_name or material_name not in self._material_attributes:
            return
        
        attrs = self._material_attributes[material_name]
        attr_keys = sorted(attrs.keys())
        for attr in attr_keys:
            self.attribute_combo.addItem(attr)
        
        if not attr_keys:
            return
        
        if preferred_attr and preferred_attr in attrs:
            idx = attr_keys.index(preferred_attr)
            self.attribute_combo.setCurrentIndex(idx)
        else:
            self.attribute_combo.setCurrentIndex(0)
        
        sel_attr = self.attribute_combo.currentText()
        if sel_attr and not skip_load:
            self._load_texture_from_attribute(material_name, sel_attr)
    
    def _on_material_changed(self, material_name):
        """Handle material combo box change."""
        if not material_name:
            return
        
        # Preserve checkbox states before changing material
        was_display_all_udims = self._display_all_udims
        was_display_all_textures = self._display_all_textures
        was_display_labels = self._display_labels
        
        self._populate_attributes(material_name)
        
        # Restore checkbox states after loading new material
        if was_display_all_udims:
            self.udim_display_all_checkbox.setChecked(True)
            self._display_all_udims = True
            self.display_all_textures_checkbox.show()
            
            # Restore "Display All Textures" state if it was checked
            if was_display_all_textures:
                self.display_all_textures_checkbox.setChecked(True)
                self._display_all_textures = True
                self.attribute_combo.setEnabled(False)
                self.attribute_combo.setStyleSheet(STYLE_COMBOBOX_DISABLED)
                self.display_labels_checkbox.show()
                # Hide Select Node button when displaying all textures
                self.select_node_button.hide()
                
                # Restore "Display Labels" state if it was checked
                if was_display_labels:
                    self.display_labels_checkbox.setChecked(True)
                    self._display_labels = True
                else:
                    self.display_labels_checkbox.setChecked(False)
                    self._display_labels = False
                
                # Load all textures from all attributes
                self._load_all_textures()
            else:
                # Load all UDIM tiles for current texture
                self._load_all_udim_tiles()
        else:
            # Update info label with formatted HTML
            filename = os.path.basename(self.texture_path)
            self._update_info_label(self.texture_node, filename, self._count_udim_tiles(self.texture_node, self.texture_path, self.texture_path), self._get_colorspace(self.texture_node), show_warning=bool(self._display_all_udims))
            
            # Load and display image
            self._load_image(self.texture_path)
            
            # Scale image after a short delay to ensure UI is fully rendered
            QtCore.QTimer.singleShot(100, self._scale_image_to_fit)
    
    def _on_attribute_changed(self, attribute_name):
        """Handle attribute combo box change."""
        material_name = self.material_combo.currentText()
        if not material_name or not attribute_name:
            return
        
        # Preserve checkbox states before changing attribute
        was_display_all_udims = self._display_all_udims
        was_display_all_textures = self._display_all_textures
        was_display_labels = self._display_labels
        
        self._load_texture_from_attribute(material_name, attribute_name)
        
        # Restore checkbox states after loading new attribute
        if was_display_all_udims:
            self.udim_display_all_checkbox.setChecked(True)
            self._display_all_udims = True
            self.display_all_textures_checkbox.show()
            
            # Restore "Display All Textures" state if it was checked
            if was_display_all_textures:
                self.display_all_textures_checkbox.setChecked(True)
                self._display_all_textures = True
                self.attribute_combo.setEnabled(False)
                self.attribute_combo.setStyleSheet(STYLE_COMBOBOX_DISABLED)
                self.display_labels_checkbox.show()
                # Hide Select Node button when displaying all textures
                self.select_node_button.hide()
                
                # Restore "Display Labels" state if it was checked
                if was_display_labels:
                    self.display_labels_checkbox.setChecked(True)
                    self._display_labels = True
                else:
                    self.display_labels_checkbox.setChecked(False)
                    self._display_labels = False
                
                # Load all textures from all attributes
                self._load_all_textures()
            else:
                # Load all UDIM tiles for current texture
                self._load_all_udim_tiles()
    
    def _load_texture_from_attribute(self, material_name, attribute_name):
        """Load texture from the specified material attribute."""
        if material_name not in self._material_attributes:
            return
        
        attrs = self._material_attributes[material_name]
        if attribute_name not in attrs:
            return
        
        file_node = attrs[attribute_name]
        self.texture_node = file_node
        self.load_texture(file_node)
    
    def _on_resize_timer(self):
        """Handle resize timer timeout - scale image and reset pending flag."""
        self._scale_image_to_fit()
        self._pending_resize = False
    
    def eventFilter(self, obj, event):
        """Filter events to handle resize and re-scale image."""
        if event.type() == QtCore.QEvent.Resize:
            # Ignore label resizes when manual zoom is active to preserve pan position
            if obj is self.image_label and self._manual_zoom is not None:
                return False
            # Throttle viewer resize events with very short delay for live scaling
            if obj is self.image_viewer:
                if not self._pending_resize:
                    # Scale immediately on first resize
                    self._scale_image_to_fit()
                    self._pending_resize = True
                # Then throttle subsequent resizes
                self._resize_timer.stop()
                self._resize_timer.start(10)  # Very short throttle (10ms) for smooth live scaling
        elif event.type() == QtCore.QEvent.MouseButtonPress:
            if obj == self.image_viewer or obj == self.image_label:
                if event.button() == QtCore.Qt.LeftButton:
                    # Begin dragging; record position in viewer coordinates
                    self._is_dragging = True
                    # Map event position to image_viewer coordinates
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
            if self._is_dragging and (obj == self.image_viewer or obj == self.image_label):
                # Compute delta in viewer coordinates
                try:
                    pos = event.position().toPoint()
                except AttributeError:
                    pos = event.pos()
                if obj is self.image_label:
                    pos = self.image_label.mapTo(self.image_viewer, pos)
                if self._drag_last_pos is not None:
                    delta = pos - self._drag_last_pos
                    # Move image label by delta
                    current_pos = self.image_label.pos()
                    self.image_label.move(current_pos.x() + delta.x(), current_pos.y() + delta.y())
                    self._drag_last_pos = pos
                    return True
        elif event.type() == QtCore.QEvent.MouseButtonRelease:
            if obj == self.image_viewer or obj == self.image_label:
                if event.button() == QtCore.Qt.LeftButton and self._is_dragging:
                    # End dragging
                    self._is_dragging = False
                    self._drag_last_pos = None
                    self.image_viewer.unsetCursor()
                    return True
        elif event.type() == QtCore.QEvent.Wheel:
            # Handle Scroll for zooming (no modifier required)
            if obj == self.image_viewer or obj == self.image_label:
                    delta = event.angleDelta().y()
                    if delta != 0:
                        # Zoom from center
                        viewer_size = self.image_viewer.size()
                        center_pos = QtCore.QPoint(viewer_size.width() // 2, viewer_size.height() // 2)
                        self._handle_zoom(delta, center_pos)
                        return True  # Consume the event
        
        # Pass unhandled events to the widget's default event handler
        if obj == self:
            # If event is for the dialog itself, call parent eventFilter
            return QtWidgets.QDialog.eventFilter(self, obj, event)
        else:
            # For other widgets, let them handle it normally
            return False
    
    def _get_event_position(self, event):
        """Get position from mouse event, handling deprecated pos() method."""
        try:
            # Try position() first (returns QPointF, convert to QPoint)
            return event.position().toPoint()
        except AttributeError:
            # Fallback to pos() for older versions
            return event.pos()
    
    def resizeEvent(self, event):
        """Handle window resize to scale image."""
        super(ShaderTextureViewer, self).resizeEvent(event)
        # Scale immediately for live feedback
        if not self._pending_resize:
            self._scale_image_to_fit()
            self._pending_resize = True
        # Throttle subsequent resizes
        self._resize_timer.stop()
        self._resize_timer.start(10)  # Very short throttle (10ms) for smooth live scaling
    
    def load_texture(self, texture_node):
        """Load and display the texture from the given file node."""
        try:
            self.texture_node = texture_node
            # Get the file path
            file_path = cmds.getAttr(f"{texture_node}.fileTextureName")
            
            if not file_path:
                self.info_label.setText(f"<i>{texture_node}</i>: No file path set")
                self.image_label.setText("No file path set")
                return
            
            # Resolve path (handle Maya path resolution)
            resolved_path = None
            
            # Check if path contains UDIM pattern
            if '<UDIM>' in file_path or '<udim>' in file_path:
                # For UDIM, try to find the first tile (1001)
                base_path = file_path.replace('<UDIM>', '1001').replace('<udim>', '1001')
                resolved_path = cmds.workspace(expandName=base_path)
                if not os.path.exists(resolved_path):
                    resolved_path = base_path
            else:
                # Try to resolve the path using Maya workspace
                try:
                    resolved_path = cmds.workspace(expandName=file_path)
                except:
                    resolved_path = file_path
                
                # If resolved path doesn't exist, try original
                if resolved_path and not os.path.exists(resolved_path):
                    if os.path.exists(file_path):
                        resolved_path = file_path
                    else:
                        resolved_path = None
            
            self.texture_path = resolved_path
            
            if not resolved_path or not os.path.exists(resolved_path):
                self.info_label.setText(f"<i>{texture_node}</i>: File not found")
                self.image_label.setText("File not found")
                return
            
            # Get texture info (UDIM count, colorspace)
            udim_count = self._count_udim_tiles(texture_node, file_path, resolved_path)
            colorspace = self._get_colorspace(texture_node)
            
            # Preserve checkbox state before setting up UDIM navigation
            was_display_all_checked = self._display_all_udims
            was_display_all_textures_checked = self._display_all_textures
            was_display_labels_checked = self._display_labels
            
            # Setup UDIM navigation if tiles are detected
            if udim_count > 1:
                self._setup_udim_navigation(texture_node, file_path, resolved_path)
                # Update UDIM label with current tile
                self._update_udim_label()
                # Restore checkbox state if it was checked
                if was_display_all_checked:
                    self.udim_display_all_checkbox.setChecked(True)
                    self._display_all_udims = True
                    self.display_all_textures_checkbox.show()
                    
                    # Restore "Display All Textures" state if it was checked
                    if was_display_all_textures_checked:
                        self.display_all_textures_checkbox.setChecked(True)
                        self._display_all_textures = True
                        self.attribute_combo.setEnabled(False)
                        self.attribute_combo.setStyleSheet("""
                            QComboBox {
                                font-family: 'Segoe UI';
                                font-size: 12px;
                                font-weight: bold;
                                color: #666666;
                                background-color: #3a3a3a;
                                border: 1px solid #444444;
                                border-radius: 6px;
                                padding: 2px 5px;
                                min-height: 22px;
                            }
                            QComboBox QAbstractItemView {
                                background-color: #3a3a3a;
                                color: #ffffff;
                                border: 1px solid #555555;
                                selection-background-color: #555555;
                            }
                        """)
                        self.display_labels_checkbox.show()
                        
                        # Restore "Display Labels" state if it was checked
                        if was_display_labels_checked:
                            self.display_labels_checkbox.setChecked(True)
                            self._display_labels = True
                        else:
                            self.display_labels_checkbox.setChecked(False)
                            self._display_labels = False
                        
                        # Load all textures from all attributes
                        self._load_all_textures()
                    else:
                        # Load all UDIM tiles for current texture
                        self._load_all_udim_tiles()
                else:
                    # Update info label with formatted HTML
                    filename = os.path.basename(resolved_path)
                    self._update_info_label(texture_node, filename, udim_count, colorspace, show_warning=bool(self._display_all_udims))
                    
                    # Load and display image
                    self._load_image(resolved_path)
                    
                    # Scale image after a short delay to ensure UI is fully rendered
                    QtCore.QTimer.singleShot(100, self._scale_image_to_fit)
            else:
                # No UDIM tiles - hide navigation buttons but keep checkbox visible
                self.udim_prev_button.hide()
                self.udim_label.hide()
                self.udim_next_button.hide()
                self.udim_prefix_label.hide()
                self.udim_display_all_checkbox.show()
                # Restore checkbox state (preserve it)
                self.udim_display_all_checkbox.setChecked(was_display_all_checked)
                self._display_all_udims = was_display_all_checked
                
                # Show/hide "Display All Textures" checkbox based on state
                if was_display_all_checked:
                    self.display_all_textures_checkbox.show()
                    self.display_all_textures_checkbox.setChecked(was_display_all_textures_checked)
                    self._display_all_textures = was_display_all_textures_checked
                else:
                    self.display_all_textures_checkbox.hide()
                
                self._udim_tiles = []
                self._current_udim_index = 0
            
                # Update info label with formatted HTML (show warning if no UDIM tiles)
                filename = os.path.basename(resolved_path)
                self._update_info_label(texture_node, filename, udim_count, colorspace, show_warning=bool(self._display_all_udims))
            
                # Load and display image
                self._load_image(resolved_path)
            
                # Scale image after a short delay to ensure UI is fully rendered
                QtCore.QTimer.singleShot(100, self._scale_image_to_fit)
            
        except Exception as e:
            error_msg = str(e)
            self.info_label.setText(f"<i>{texture_node}</i>: Error - {error_msg}")
            self.image_label.setText(f"Error: {error_msg}")
            print(f"[ShaderTextureViewer] Error loading texture: {e}")
    
    def _count_udim_tiles(self, texture_node, file_path, resolved_path):
        """Count UDIM tiles for the texture."""
        try:
            # Check if UDIM mode is enabled
            use_udim = cmds.getAttr(f"{texture_node}.uvTilingMode")
            if use_udim == 3:  # UDIM mode
                # Check if path contains UDIM pattern
                if '<UDIM>' in file_path or '<udim>' in file_path:
                    # Count tiles by checking directory
                    if resolved_path and os.path.exists(resolved_path):
                        dir_path = os.path.dirname(resolved_path)
                        base_name = os.path.basename(resolved_path)
                        
                        # Check if filename contains a UDIM pattern (1001-1999)
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
                                    return len(tiles)
                # Also check if resolved path has UDIM pattern in it
                elif resolved_path:
                    dir_path = os.path.dirname(resolved_path)
                    base_name = os.path.basename(resolved_path)
                    udim_match = re.search(r'\.(\d{4})\.', base_name)
                    if udim_match:
                        udim_num = udim_match.group(1)
                        parts = base_name.split(f'.{udim_num}.')
                        if len(parts) == 2:
                            prefix, ext = parts
                            if os.path.isdir(dir_path):
                                tiles = []
                                for f in os.listdir(dir_path):
                                    match = re.match(rf'^{re.escape(prefix)}\.(\d{{4}})\.{re.escape(ext)}$', f)
                                    if match:
                                        tile_num = int(match.group(1))
                                        if 1001 <= tile_num <= 1999:
                                            tiles.append(tile_num)
                                return len(tiles)
        except Exception as e:
            print(f"[ShaderTextureViewer] UDIM detection error: {e}")
        
        return 0
    
    def _get_colorspace(self, texture_node):
        """Get the colorspace of the texture node."""
        try:
            if cmds.attributeQuery('colorSpace', node=texture_node, exists=True):
                colorspace = cmds.getAttr(f"{texture_node}.colorSpace")
                if colorspace:
                    return colorspace
        except Exception as e:
            print(f"[ShaderTextureViewer] Colorspace detection error: {e}")
        
        return 'Raw'  # Default
    
    def _update_info_label(self, texture_node, filename, udim_count, colorspace, show_udim_count=True, current_udim_tile=None, show_warning=False):
        """Update the info label with formatted HTML text."""
        # Start with italic node name
        html_text = f'<i>{texture_node}</i> - <b><span style="font-size: 14px;">{filename}</span></b>'
        
        # Show UDIM tile count in blue, or red warning if no tiles
        if show_warning and udim_count <= 1:
            html_text += f' <span style="color: #ff4444;">(No UDIM tiles for this texture)</span>'
        elif udim_count > 1:
            html_text += f' <span style="color: #6fa3d8;">({udim_count} Tiles)</span>'
        
        # Add colorspace in grey
        html_text += f' <span style="color: #999999;">({colorspace})</span>'
        
        self.info_label.setText(html_text)
    
    def _setup_udim_navigation(self, texture_node, file_path, resolved_path):
        """Setup UDIM tile navigation by extracting tile information."""
        try:
            dir_path = os.path.dirname(resolved_path)
            base_name = os.path.basename(resolved_path)
            
            # Check if filename contains a UDIM pattern (1001-1999)
            udim_match = re.search(r'\.(\d{4})\.', base_name)
            if udim_match:
                udim_num = int(udim_match.group(1))
                # Split filename around the UDIM number
                parts = base_name.split(f'.{udim_num}.')
                if len(parts) == 2:
                    prefix, ext = parts
                    
                    # Store UDIM information
                    self._udim_dir_path = dir_path
                    self._udim_prefix = prefix
                    self._udim_extension = ext
                    
                    # Find all UDIM tiles
                    tiles = []
                    if os.path.isdir(dir_path):
                        for f in os.listdir(dir_path):
                            match = re.match(rf'^{re.escape(prefix)}\.(\d{{4}})\.{re.escape(ext)}$', f)
                            if match:
                                tile_num = int(match.group(1))
                                if 1001 <= tile_num <= 1999:
                                    tiles.append(tile_num)
                    
                    # Sort tiles
                    tiles.sort()
                    self._udim_tiles = tiles
                    
                    # Find current tile index
                    if udim_num in tiles:
                        self._current_udim_index = tiles.index(udim_num)
                    else:
                        self._current_udim_index = 0
                    
                    # Show navigation buttons
                    if len(tiles) > 1:
                        self.udim_prefix_label.show()
                        self.udim_prev_button.show()
                        self.udim_label.show()
                        self.udim_next_button.show()
                    # Always show checkbox (even if no tiles)
                        self.udim_display_all_checkbox.show()
                        # Enable/disable buttons based on position
                    if len(tiles) > 1:
                        self.udim_prev_button.setEnabled(self._current_udim_index > 0)
                        self.udim_next_button.setEnabled(self._current_udim_index < len(tiles) - 1)
                    # Update checkbox state (preserve state)
                        self.udim_display_all_checkbox.setChecked(self._display_all_udims)
                    # If "Display All UDIMs" is currently enabled, ensure nav buttons are hidden
                    if self._display_all_udims:
                        self.udim_prefix_label.hide()
                        self.udim_prev_button.hide()
                        self.udim_label.hide()
                        self.udim_next_button.hide()
                    # Show "Display All Textures" checkbox if "Display All UDIMs" is checked
                    if self._display_all_udims:
                        self.display_all_textures_checkbox.show()
                    else:
                        self.display_all_textures_checkbox.hide()
        except Exception as e:
            print(f"[ShaderTextureViewer] UDIM navigation setup error: {e}")
            self._udim_tiles = []
            self._current_udim_index = 0
    
    def _update_udim_label(self):
        """Update the UDIM label with current tile number."""
        if self._udim_tiles and 0 <= self._current_udim_index < len(self._udim_tiles):
            current_tile = self._udim_tiles[self._current_udim_index]
            self.udim_label.setText(str(current_tile))
        else:
            self.udim_label.setText("")
    
    def _previous_udim_tile(self):
        """Load the previous UDIM tile."""
        if self._display_all_udims or not self._udim_tiles or self._current_udim_index <= 0:
            return
        
        self._current_udim_index -= 1
        self._load_udim_tile()
    
    def _next_udim_tile(self):
        """Load the next UDIM tile."""
        if self._display_all_udims or not self._udim_tiles or self._current_udim_index >= len(self._udim_tiles) - 1:
            return
        
        self._current_udim_index += 1
        self._load_udim_tile()
    
    def _load_udim_tile(self):
        """Load the current UDIM tile based on current index."""
        if not self._udim_tiles or not (0 <= self._current_udim_index < len(self._udim_tiles)):
            return
        
        if not self._udim_dir_path or not self._udim_prefix or not self._udim_extension:
            return
        
        # Construct tile filename
        tile_num = self._udim_tiles[self._current_udim_index]
        tile_filename = f"{self._udim_prefix}.{tile_num}.{self._udim_extension}"
        tile_path = os.path.join(self._udim_dir_path, tile_filename)
        
        # Normalize path separators for cross-platform compatibility
        tile_path = os.path.normpath(tile_path)
        
        # Update label
        self._update_udim_label()
        
        # Enable/disable buttons (only if not in display-all mode)
        if not self._display_all_udims:
            self.udim_prev_button.setEnabled(self._current_udim_index > 0)
            self.udim_next_button.setEnabled(self._current_udim_index < len(self._udim_tiles) - 1)
        
        # Update info label with actual texture filename (with UDIM number)
        texture_node = self.texture_node
        # Use the actual filename with UDIM number
        filename = os.path.basename(tile_path)
        udim_count = len(self._udim_tiles)
        colorspace = self._get_colorspace(texture_node) if texture_node else 'Raw'
        self._update_info_label(texture_node, filename, udim_count, colorspace, show_udim_count=True)
        
        # Load and display new tile
        self._load_image(tile_path)
        
        # Reset zoom when switching tiles
        self._manual_zoom = None
        QtCore.QTimer.singleShot(100, self._scale_image_to_fit)
    
    def _on_display_all_changed(self, state):
        """Handle checkbox state change for displaying all UDIM tiles."""
        # Check if checkbox is checked (state is 2 for Checked, 0 for Unchecked in Qt)
        checked_state = QtCore.Qt.Checked
        if hasattr(checked_state, 'value'):
            checked_state = checked_state.value
        self._display_all_udims = (state == checked_state or state == 2)
        
        # If no UDIM tiles, update the info label warning state when toggled
        if not self._udim_tiles:
            try:
                texture_node = self.texture_node
                filename = os.path.basename(self.texture_path) if self.texture_path else ""
                colorspace = self._get_colorspace(texture_node) if texture_node else 'Raw'
                self._update_info_label(texture_node, filename, 0, colorspace, show_warning=bool(self._display_all_udims))
            except Exception:
                pass
            return
        
        # If turning off, also turn off Display All Textures and re-enable attribute combo
        if not self._display_all_udims:
            self.display_all_textures_checkbox.hide()
            self.display_all_textures_checkbox.setChecked(False)
            self._display_all_textures = False
            # Re-enable attribute combo box
            self.attribute_combo.setEnabled(True)
            self.attribute_combo.setStyleSheet(STYLE_COMBOBOX_MAIN)
            # Show Select Node button again
            self.select_node_button.show()
        
        # Show/hide "Display All Textures" checkbox based on "Display All UDIMs" state
        if self._display_all_udims:
            self.display_all_textures_checkbox.show()
        
        if not self._udim_tiles:
            # No UDIM tiles, but checkbox is visible - just don't change display
            return
        
        if self._display_all_udims and self._udim_tiles:
            # Hide navigation buttons when displaying all tiles
            self.udim_prefix_label.hide()
            self.udim_prev_button.hide()
            self.udim_label.hide()
            self.udim_next_button.hide()
            # Load all UDIM tiles in grid layout (or all textures if that checkbox is checked)
            if self._display_all_textures:
                self._load_all_textures()
            else:
                self._load_all_udim_tiles()
        else:
            # Show navigation buttons when displaying single tile
            if len(self._udim_tiles) > 1:
                self.udim_prefix_label.show()
                self.udim_prev_button.show()
                self.udim_label.show()
                self.udim_next_button.show()
            # Load single tile view
            self._load_udim_tile()
    
    def _on_display_all_textures_changed(self, state):
        """Handle checkbox state change for displaying all textures from all attributes."""
        if not self._display_all_udims:
            return  # Only works when Display All UDIMs is checked
        
        # Check if checkbox is checked
        checked_state = QtCore.Qt.Checked
        if hasattr(checked_state, 'value'):
            checked_state = checked_state.value
        self._display_all_textures = (state == checked_state or state == 2)
        
        # Enable/disable attribute combo box
        if self._display_all_textures:
            self.attribute_combo.setEnabled(False)
            self.attribute_combo.setStyleSheet(STYLE_COMBOBOX_DISABLED)
            # Show "Display Labels" checkbox
            self.display_labels_checkbox.show()
            # Hide Select Node button when displaying all textures
            self.select_node_button.hide()
        else:
            self.display_all_textures_checkbox.hide()
            self.display_labels_checkbox.hide()
            self.display_labels_checkbox.setChecked(False)
            self._display_labels = False
            # Re-enable attribute combo box
            self.attribute_combo.setEnabled(True)
            self.attribute_combo.setStyleSheet(STYLE_COMBOBOX_MAIN)
            # Show Select Node button again
            self.select_node_button.show()
        
        if self._display_all_textures:
            # Load all textures from all attributes
            self._load_all_textures()
        else:
            # Load just the current texture's UDIM tiles
            if self._udim_tiles:
                self._load_all_udim_tiles()
            else:
                # No UDIM tiles, just load the single texture
                if self.texture_node:
                    self.load_texture(self.texture_node)
    
    def _on_display_labels_changed(self, state):
        """Handle checkbox state change for displaying attribute labels."""
        # Deprecated: labels are always displayed now
        self._display_labels = True
        if self._display_all_textures:
            self._load_all_textures()

    def _select_current_node(self):
        """Select the current file texture node in Maya."""
        try:
            if self.texture_node and cmds.objExists(self.texture_node):
                cmds.select(self.texture_node, r=True)
            else:
                cmds.warning("No current texture node to select.")
        except Exception as e:
            print(f"[ShaderTextureViewer] Select node error: {e}")
    
    def _load_all_udim_tiles(self):
        """Load all UDIM tiles and arrange them in a grid based on UDIM positions."""
        if not self._udim_tiles or not self._udim_dir_path or not self._udim_prefix or not self._udim_extension:
            return
        
        try:
            # Load all tile images
            tile_positions = {}  # Maps (U, V) to pixmap
            
            for tile_num in self._udim_tiles:
                # Calculate U and V from UDIM number
                # UDIM = 1001 + U + V*10
                # So: U = (UDIM - 1001) % 10, V = (UDIM - 1001) // 10
                base = tile_num - 1001
                u = base % 10
                v = base // 10
                
                # Construct tile path
                tile_filename = f"{self._udim_prefix}.{tile_num}.{self._udim_extension}"
                tile_path = os.path.join(self._udim_dir_path, tile_filename)
                tile_path = os.path.normpath(tile_path)
                
                # Load tile image
                if os.path.exists(tile_path):
                    # Check for unsupported formats
                    file_ext = os.path.splitext(tile_path)[1].lower()
                    unsupported_formats = ['.hdr', '.exr', '.tx', '.rat']
                    if file_ext in unsupported_formats:
                        continue  # Skip unsupported formats
                    
                    pixmap = QtGui.QPixmap(tile_path)
                    if not pixmap.isNull():
                        tile_positions[(u, v)] = pixmap
            
            if not tile_positions:
                self.image_label.setText("Error: Could not load any UDIM tiles")
                return
            
            # Calculate grid dimensions
            max_u = max(u for u, v in tile_positions.keys())
            max_v = max(v for u, v in tile_positions.keys())
            
            # Get tile size (assume all tiles are the same size, use first tile)
            first_pixmap = next(iter(tile_positions.values()))
            tile_width = first_pixmap.width()
            tile_height = first_pixmap.height()
            # Spacing between tiles (match Display All Textures mode)
            tile_spacing = 24
            
            # Create combined pixmap
            # Grid is (max_u + 1) tiles wide, (max_v + 1) tiles tall with spacing
            combined_width = (max_u + 1) * tile_width + max_u * tile_spacing
            combined_height = (max_v + 1) * tile_height + max_v * tile_spacing
            
            combined_pixmap = QtGui.QPixmap(combined_width, combined_height)
            combined_pixmap.fill(QtCore.Qt.transparent)
            
            # Paint tiles onto combined pixmap
            painter = QtGui.QPainter(combined_pixmap)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            
            for (u, v), pixmap in tile_positions.items():
                # Position: U increases left to right, V increases bottom to top
                # So: x = u * (tile_width + spacing), y = (max_v - v) * (tile_height + spacing)
                x = u * (tile_width + tile_spacing)
                y = (max_v - v) * (tile_height + tile_spacing)
                painter.drawPixmap(x, y, pixmap)
            
            painter.end()
            
            # Store as original pixmap
            self._original_pixmap = combined_pixmap
            
            # Update image label directly to show the combined pixmap
            self.image_label.setPixmap(combined_pixmap)
            self.image_label.resize(combined_pixmap.width(), combined_pixmap.height())
            
            # Center the image
            self._center_image()
            
            # Update info label - show base filename with tile count
            texture_node = self.texture_node
            udim_count = len(self._udim_tiles)
            colorspace = self._get_colorspace(texture_node) if texture_node else 'Raw'
            # Get base filename without UDIM number
            if self._udim_prefix and self._udim_extension:
                base_filename = f"{self._udim_prefix}.{self._udim_extension}"
            else:
                base_filename = "All UDIM Tiles"
            self._update_info_label(texture_node, base_filename, udim_count, colorspace, show_udim_count=True)
            
            # Hide navigation buttons when showing all tiles
            self.udim_prefix_label.hide()
            self.udim_prev_button.hide()
            self.udim_label.hide()
            self.udim_next_button.hide()
            
            # Reset zoom and scale to fit
            self._manual_zoom = None
            QtCore.QTimer.singleShot(100, self._scale_image_to_fit)
            
        except Exception as e:
            import traceback
            self.image_label.setText(f"Error loading UDIM tiles: {str(e)}")
            print(f"[ShaderTextureViewer] Error loading all UDIM tiles: {e}")
            print(f"[ShaderTextureViewer] Traceback: {traceback.format_exc()}")
    
    def _load_all_textures(self):
        """Load all UDIM tiles from all unique texture nodes in the current material and arrange them in one big grid."""
        material_name = self.material_combo.currentText()
        if not material_name or material_name not in self._material_attributes:
            return
        
        try:
            # Collect all unique file texture nodes from all attributes
            # Map texture_node -> list of attributes that use it
            texture_to_attributes = {}
            attrs = self._material_attributes[material_name]
            
            for attr_name, file_node in attrs.items():
                if file_node not in texture_to_attributes:
                    texture_to_attributes[file_node] = []
                texture_to_attributes[file_node].append(attr_name)
            
            unique_texture_nodes = list(texture_to_attributes.keys())
            
            if not unique_texture_nodes:
                self.image_label.setText("No texture nodes found")
                return
            
            # Collect all UDIM tiles from all textures
            all_tile_data = []  # List of (texture_node, tile_num, u, v, tile_path, pixmap)
            texture_meta = {}   # file_node -> { 'base_filename': str, 'tiles_count': int }
            
            for file_node in unique_texture_nodes:
                try:
                    # Get file path
                    file_path = cmds.getAttr(f"{file_node}.fileTextureName")
                    if not file_path:
                        continue
                    
                    # Resolve path
                    resolved_path = None
                    if '<UDIM>' in file_path or '<udim>' in file_path:
                        base_path = file_path.replace('<UDIM>', '1001').replace('<udim>', '1001')
                        resolved_path = cmds.workspace(expandName=base_path)
                        if not os.path.exists(resolved_path):
                            resolved_path = base_path
                    else:
                        try:
                            resolved_path = cmds.workspace(expandName=file_path)
                        except:
                            resolved_path = file_path
                        
                        if resolved_path and not os.path.exists(resolved_path):
                            if os.path.exists(file_path):
                                resolved_path = file_path
                            else:
                                resolved_path = None
                    
                    if not resolved_path or not os.path.exists(resolved_path):
                        continue
                    
                    # Check if UDIM mode is enabled
                    try:
                        use_udim = cmds.getAttr(f"{file_node}.uvTilingMode")
                        if use_udim != 3:  # Not UDIM mode
                            continue
                    except:
                        continue
                    
                    # Extract UDIM tile information
                    dir_path = os.path.dirname(resolved_path)
                    base_name = os.path.basename(resolved_path)
                    
                    udim_match = re.search(r'\.(\d{4})\.', base_name)
                    if not udim_match:
                        continue
                    
                    udim_num = int(udim_match.group(1))
                    parts = base_name.split(f'.{udim_num}.')
                    if len(parts) != 2:
                        continue
                    
                    prefix, ext = parts
                    
                    # Find all UDIM tiles for this texture
                    tiles = []
                    if os.path.isdir(dir_path):
                        for f in os.listdir(dir_path):
                            match = re.match(rf'^{re.escape(prefix)}\.(\d{{4}})\.{re.escape(ext)}$', f)
                            if match:
                                tile_num = int(match.group(1))
                                if 1001 <= tile_num <= 1999:
                                    tiles.append(tile_num)
                    
                    tiles.sort()
                    
                    # Record per-texture metadata for labels
                    texture_meta[file_node] = {
                        'base_filename': f"{prefix}.{ext}",
                        'tiles_count': len(tiles)
                    }
                    
                    # Load all tiles for this texture
                    for tile_num in tiles:
                        # Calculate U and V from UDIM number
                        base = tile_num - 1001
                        u = base % 10
                        v = base // 10
                        
                        # Construct tile path
                        tile_filename = f"{prefix}.{tile_num}.{ext}"
                        tile_path = os.path.join(dir_path, tile_filename)
                        tile_path = os.path.normpath(tile_path)
                        
                        # Load tile image
                        if os.path.exists(tile_path):
                            # Check for unsupported formats
                            file_ext = os.path.splitext(tile_path)[1].lower()
                            unsupported_formats = ['.hdr', '.exr', '.tx', '.rat']
                            if file_ext in unsupported_formats:
                                continue
                            
                            pixmap = QtGui.QPixmap(tile_path)
                            if not pixmap.isNull():
                                all_tile_data.append((file_node, tile_num, u, v, tile_path, pixmap))
                
                except Exception as e:
                    print(f"[ShaderTextureViewer] Error processing texture {file_node}: {e}")
                    continue
            
            if not all_tile_data:
                self.image_label.setText("No UDIM tiles found")
                return
            
            # Calculate grid dimensions
            # We'll arrange tiles in a grid, grouping by texture
            # First, find the max U and V across all tiles
            max_u = max(u for _, _, u, v, _, _ in all_tile_data)
            max_v = max(v for _, _, u, v, _, _ in all_tile_data)
            
            # Get tile size (assume all tiles are the same size, use first tile)
            first_pixmap = all_tile_data[0][5]  # pixmap is at index 5
            tile_width = first_pixmap.width()
            tile_height = first_pixmap.height()
            
            # Calculate grid size: we'll arrange textures vertically, each texture's tiles in its own grid
            num_textures = len(unique_texture_nodes)
            
            # Labels always enabled
            self._display_labels = True
            # Label height and spacing (increased for larger labels and more separation)
            label_height = 600
            spacing_between_textures = 48
            tile_spacing = 24
            
            # Each texture gets a grid of (max_u + 1) x (max_v + 1) tiles
            # Arrange textures vertically with labels and spacing
            combined_width = (max_u + 1) * tile_width + max_u * tile_spacing
            combined_height = num_textures * (((max_v + 1) * tile_height + max_v * tile_spacing) + label_height + spacing_between_textures) - spacing_between_textures
            
            combined_pixmap = QtGui.QPixmap(combined_width, combined_height)
            combined_pixmap.fill(QtCore.Qt.transparent)
            
            # Paint tiles onto combined pixmap
            painter = QtGui.QPainter(combined_pixmap)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            
            # Group tiles by texture node
            tiles_by_texture = {}
            for tile_data in all_tile_data:
                file_node = tile_data[0]
                if file_node not in tiles_by_texture:
                    tiles_by_texture[file_node] = []
                tiles_by_texture[file_node].append(tile_data)
            
            # Paint each texture's tiles in order
            texture_y_offset = 0
            for texture_idx, file_node in enumerate(unique_texture_nodes):
                if file_node not in tiles_by_texture:
                    continue
                
                # Draw label if enabled
                if file_node in texture_to_attributes:
                    attributes = texture_to_attributes[file_node]
                    # Build label: "attrs - base_filename (N tiles)"
                    meta = texture_meta.get(file_node, {'base_filename': '', 'tiles_count': 0})
                    tiles_count = meta.get('tiles_count', 0)
                    base_filename = meta.get('base_filename', '')
                    label_text = f"{', '.join(attributes)} - {base_filename} ({tiles_count} tiles)"
                    
                    # Set font for label (large but not cut off)
                    font = QtGui.QFont("Segoe UI", 480, QtGui.QFont.Bold)
                    painter.setFont(font)
                    painter.setPen(QtGui.QColor(255, 255, 255))
                    
                    # Draw label background (match UI bg #2b2b2b)
                    label_rect = QtCore.QRect(0, texture_y_offset, combined_width, label_height)
                    painter.fillRect(label_rect, QtGui.QColor(43, 43, 43, 255))  # #2b2b2b
                    
                    # Fit label text within available width/height by reducing font size as needed
                    padding_tb = 40
                    padding_lr = 20
                    max_width = combined_width - 2 * padding_lr
                    max_height = label_height - 2 * padding_tb
                    
                    # Start from current font and reduce until it fits
                    test_font = QtGui.QFont(font)
                    fm = QtGui.QFontMetrics(test_font)
                    text_width = fm.horizontalAdvance(f"  {label_text}")
                    text_height = fm.height()
                    
                    while (text_width > max_width or text_height > max_height) and test_font.pointSize() > 8:
                        test_font.setPointSize(int(test_font.pointSize() * 0.9))
                        fm = QtGui.QFontMetrics(test_font)
                        text_width = fm.horizontalAdvance(f"  {label_text}")
                        text_height = fm.height()
                    
                    painter.setFont(test_font)
                    
                    # Draw label text with padding
                    label_rect_inner = label_rect.adjusted(padding_lr, padding_tb, -padding_lr, -padding_tb)
                    painter.drawText(label_rect_inner, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, f"  {label_text}")
                    
                    texture_y_offset += label_height
                
                texture_tiles = tiles_by_texture[file_node]
                
                for tile_data in texture_tiles:
                    _, tile_num, u, v, _, pixmap = tile_data
                    # Position: U increases left to right, V increases bottom to top
                    x = u * (tile_width + tile_spacing)
                    y = texture_y_offset + (max_v - v) * (tile_height + tile_spacing)
                    painter.drawPixmap(x, y, pixmap)
                
                # Move to next texture's position
                texture_y_offset += (max_v + 1) * tile_height + spacing_between_textures
            
            painter.end()
            
            # Store as original pixmap
            self._original_pixmap = combined_pixmap
            
            # Update image label
            self.image_label.setPixmap(combined_pixmap)
            self.image_label.resize(combined_pixmap.width(), combined_pixmap.height())
            
            # Center the image
            self._center_image()
            
            # Update info label
            total_tiles = len(all_tile_data)
            num_textures = len(unique_texture_nodes)
            self.info_label.setText(f"<i>{material_name}</i> - <b><span style=\"font-size: 14px;\">All Textures ({num_textures} textures, {total_tiles} tiles)</span></b>")
            
            # Reset zoom and scale to fit
            self._manual_zoom = None
            QtCore.QTimer.singleShot(100, self._scale_image_to_fit)
            
        except Exception as e:
            import traceback
            self.image_label.setText(f"Error loading all textures: {str(e)}")
            print(f"[ShaderTextureViewer] Error loading all textures: {e}")
            print(f"[ShaderTextureViewer] Traceback: {traceback.format_exc()}")
    
    def _load_image(self, file_path):
        """Load image from file path and display it."""
        try:
            # Normalize path separators (Qt prefers forward slashes)
            normalized_path = file_path.replace('\\', '/')
            
            # Check file extension for unsupported formats
            file_ext = os.path.splitext(normalized_path)[1].lower()
            unsupported_formats = ['.hdr', '.exr', '.tx', '.rat']
            if file_ext in unsupported_formats:
                self.image_label.setText(f"Unsupported format: {file_ext.upper()}\nQt cannot display this format directly.")
                return
            
            # Load image using QPixmap
            pixmap = QtGui.QPixmap(normalized_path)
            
            if pixmap.isNull():
                # Try with original path in case normalization broke something
                pixmap = QtGui.QPixmap(file_path)
                if pixmap.isNull():
                    self.image_label.setText("Error: Could not load image")
                    return
            
            # Store original pixmap for reference
            self._original_pixmap = pixmap
            
        except Exception as e:
            self.image_label.setText(f"Error loading image: {str(e)}")
            print(f"[ShaderTextureViewer] Error loading image: {e}")
    
    def _handle_zoom(self, delta, pos):
        """Handle mouse wheel zoom with Ctrl modifier - zoom from center."""
        if self._original_pixmap is None or self._original_pixmap.isNull():
            return
        
        # Get current zoom or calculate fit-to-UI scale
        old_zoom = self._manual_zoom
        if self._manual_zoom is None:
            # Calculate current fit-to-UI scale
            viewer_size = self.image_viewer.size()
            available_width = viewer_size.width() - 20
            available_height = viewer_size.height() - 20
            
            if available_width <= 20 or available_height <= 20:
                available_width = 1004
                available_height = 1004
            
            original_size = self._original_pixmap.size()
            scale_x = available_width / original_size.width()
            scale_y = available_height / original_size.height()
            self._manual_zoom = min(scale_x, scale_y)
            old_zoom = self._manual_zoom
        
        # Zoom factor (1.1 = 10% per scroll step)
        zoom_factor = 1.1
        if delta > 0:
            # Zoom in
            self._manual_zoom *= zoom_factor
        else:
            # Zoom out
            self._manual_zoom /= zoom_factor
        
        # Clamp zoom between 0.01x and 10x (allow zooming way out)
        self._manual_zoom = max(0.01, min(10.0, self._manual_zoom))
        
        # Compute new image position to zoom about viewer center
        if old_zoom and old_zoom > 0:
            ratio = self._manual_zoom / old_zoom
            viewer_size = self.image_viewer.size()
            center_pos = QtCore.QPoint(viewer_size.width() // 2, viewer_size.height() // 2)
            current_top_left = self.image_label.pos()
            dx = center_pos.x() - current_top_left.x()
            dy = center_pos.y() - current_top_left.y()
            new_top_left_x = center_pos.x() - int(dx * ratio)
            new_top_left_y = center_pos.y() - int(dy * ratio)
        else:
            new_top_left_x = self.image_label.x()
            new_top_left_y = self.image_label.y()
        
        # Apply zoom; reposition to maintain center-anchored zoom
        self._apply_zoom(skip_position_update=True)
        self.image_label.move(new_top_left_x, new_top_left_y)
    
    def _zoom_in(self):
        """Zoom in by 10%."""
        if self._original_pixmap is None or self._original_pixmap.isNull():
            return
        
        # Get current zoom or calculate fit-to-UI scale
        old_zoom = self._manual_zoom
        if self._manual_zoom is None:
            viewer_size = self.image_viewer.size()
            available_width = viewer_size.width() - 20
            available_height = viewer_size.height() - 20
            if available_width <= 20 or available_height <= 20:
                available_width = 1004
                available_height = 1004
            original_size = self._original_pixmap.size()
            scale_x = available_width / original_size.width()
            scale_y = available_height / original_size.height()
            self._manual_zoom = min(scale_x, scale_y)
            old_zoom = self._manual_zoom
        
        # Zoom in by 10%
        self._manual_zoom *= 1.1
        self._manual_zoom = max(0.01, min(10.0, self._manual_zoom))
        
        # Compute new image position to zoom about viewer center
        if old_zoom and old_zoom > 0:
            ratio = self._manual_zoom / old_zoom
            viewer_size = self.image_viewer.size()
            center_pos = QtCore.QPoint(viewer_size.width() // 2, viewer_size.height() // 2)
            current_top_left = self.image_label.pos()
            dx = center_pos.x() - current_top_left.x()
            dy = center_pos.y() - current_top_left.y()
            new_top_left_x = center_pos.x() - int(dx * ratio)
            new_top_left_y = center_pos.y() - int(dy * ratio)
        else:
            new_top_left_x = self.image_label.x()
            new_top_left_y = self.image_label.y()
        
        # Apply zoom; reposition to maintain center-anchored zoom
        self._apply_zoom(skip_position_update=True)
        self.image_label.move(new_top_left_x, new_top_left_y)
    
    def _zoom_out(self):
        """Zoom out by 10%."""
        if self._original_pixmap is None or self._original_pixmap.isNull():
            return
        
        # Get current zoom or calculate fit-to-UI scale
        old_zoom = self._manual_zoom
        if self._manual_zoom is None:
            viewer_size = self.image_viewer.size()
            available_width = viewer_size.width() - 20
            available_height = viewer_size.height() - 20
            if available_width <= 20 or available_height <= 20:
                available_width = 1004
                available_height = 1004
            original_size = self._original_pixmap.size()
            scale_x = available_width / original_size.width()
            scale_y = available_height / original_size.height()
            self._manual_zoom = min(scale_x, scale_y)
            old_zoom = self._manual_zoom
        
        # Zoom out by 10%
        self._manual_zoom /= 1.1
        self._manual_zoom = max(0.01, min(10.0, self._manual_zoom))
        
        # Compute new image position to zoom about viewer center
        if old_zoom and old_zoom > 0:
            ratio = self._manual_zoom / old_zoom
            viewer_size = self.image_viewer.size()
            center_pos = QtCore.QPoint(viewer_size.width() // 2, viewer_size.height() // 2)
            current_top_left = self.image_label.pos()
            dx = center_pos.x() - current_top_left.x()
            dy = center_pos.y() - current_top_left.y()
            new_top_left_x = center_pos.x() - int(dx * ratio)
            new_top_left_y = center_pos.y() - int(dy * ratio)
        else:
            new_top_left_x = self.image_label.x()
            new_top_left_y = self.image_label.y()
        
        # Apply zoom; reposition to maintain center-anchored zoom
        self._apply_zoom(skip_position_update=True)
        self.image_label.move(new_top_left_x, new_top_left_y)
    
    def _reset_zoom(self):
        """Reset zoom to fit-to-UI scale."""
        self._manual_zoom = None
        self._scale_image_to_fit()
    
    def _apply_zoom(self, skip_position_update=False):
        """Apply the current zoom level to the image."""
        if self._original_pixmap is None or self._original_pixmap.isNull():
            return
        
        original_size = self._original_pixmap.size()
        
        # Calculate scaled size based on manual zoom
        scaled_width = int(original_size.width() * self._manual_zoom)
        scaled_height = int(original_size.height() * self._manual_zoom)
        
        # Scale the pixmap
        scaled_pixmap = self._original_pixmap.scaled(
            scaled_width, scaled_height,
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation
        )
        
        # Set the label size to match the scaled pixmap
        self.image_label.setPixmap(scaled_pixmap)
        self.image_label.resize(scaled_width, scaled_height)
        
        # Center the image in the viewer
        if not skip_position_update:
            self._center_image()
    
    def _center_image(self):
        """Center the image in the viewer widget."""
        viewer_size = self.image_viewer.size()
        image_size = self.image_label.size()
        
        # Calculate center position
        x = (viewer_size.width() - image_size.width()) / 2
        y = (viewer_size.height() - image_size.height()) / 2
        
        # Move image to center
        self.image_label.move(int(x), int(y))
    
    def _scale_image_to_fit(self):
        """Scale the original pixmap to fit within the viewer bounds and center it."""
        if self._original_pixmap is None or self._original_pixmap.isNull():
            return
        
        # If manual zoom is set, use it instead of fit-to-UI
        if self._manual_zoom is not None:
            # Apply zoom without altering current position
            self._apply_zoom(skip_position_update=True)
            return
        
        # Get the viewer size (use current size or fallback to default)
        viewer_size = self.image_viewer.size()
        
        # Account for margins and borders (approximately)
        available_width = viewer_size.width() - 20
        available_height = viewer_size.height() - 20
        
        # If viewer hasn't been sized yet, use a default
        if available_width <= 20 or available_height <= 20:
            available_width = 1004  # Default window width (1024) - margins
            available_height = 1004  # Default window height (1024) - header and margins
        
        # Get original image size
        original_size = self._original_pixmap.size()
        
        # Calculate scale to fit while maintaining aspect ratio
        scale_x = available_width / original_size.width()
        scale_y = available_height / original_size.height()
        scale = min(scale_x, scale_y)  # Use the smaller scale to fit within bounds
        
        # Calculate scaled size
        scaled_width = int(original_size.width() * scale)
        scaled_height = int(original_size.height() * scale)
        
        # Scale the pixmap
        scaled_pixmap = self._original_pixmap.scaled(
            scaled_width, scaled_height,
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation
        )
        
        # Set the label size to match the scaled pixmap
        self.image_label.setPixmap(scaled_pixmap)
        self.image_label.resize(scaled_width, scaled_height)
        
        # Center the image in the viewer
        self._center_image()


def show_shader_texture_viewer(shader_node=None):
    """Show the shader texture viewer window.
    
    Args:
        shader_node: Optional shader/material node name. If None, will try to get from selection.
    
    Returns:
        The ShaderTextureViewer window instance.
    """
    # If no shader node provided, try to get from selection
    if shader_node is None:
        selected = cmds.ls(selection=True)
        if not selected:
            cmds.warning("No shader/material selected. Please select a shader or material node.")
            return None
        
        # Check if selected node is a material/shader
        shader_node = selected[0]
        node_type = cmds.nodeType(shader_node)
        
        # Check if it's a material type
        material_types = ['lambert', 'blinn', 'phong', 'phongE', 'anisotropic', 
                         'standardSurface', 'aiStandardSurface', 'surfaceShader', 'shadingEngine']
        if node_type not in material_types:
            # Try to find if it's connected to a material
            connections = cmds.listConnections(shader_node, type='shadingEngine') or []
            if not connections:
                cmds.warning(f"Selected node '{shader_node}' is not a material/shader node (type: {node_type}).")
                return None
            else:
                # Use the first connected shading engine
                sg = connections[0]
                materials = cmds.listConnections(f"{sg}.surfaceShader", source=True, destination=False) or []
                if materials:
                    shader_node = materials[0]
                else:
                    cmds.warning(f"Could not find material for selected node '{shader_node}'.")
                    return None
    
    # Check if window already exists
    if hasattr(show_shader_texture_viewer, '_window') and show_shader_texture_viewer._window is not None:
        try:
            show_shader_texture_viewer._window.close()
        except:
            pass
    
    # Create and show window
    window = ShaderTextureViewer(shader_node, context='material')
    window.show()
    show_shader_texture_viewer._window = window
    
    return window

def show_texture_viewer_for_material(material_node):
    """Open viewer in material context for a given material node."""
    if hasattr(show_texture_viewer_for_material, '_window') and show_texture_viewer_for_material._window is not None:
        try:
            show_texture_viewer_for_material._window.close()
        except:
            pass
    window = ShaderTextureViewer(shader_node=material_node, context='material')
    window.show()
    show_texture_viewer_for_material._window = window
    return window

def show_texture_viewer_for_file_node(file_node):
    """Open viewer focused on a file node; material/attribute combos match material mode (preselect one hook-up if any)."""
    if hasattr(show_texture_viewer_for_file_node, '_window') and show_texture_viewer_for_file_node._window is not None:
        try:
            show_texture_viewer_for_file_node._window.close()
        except:
            pass
    window = ShaderTextureViewer(file_node=file_node, context='file')
    window.show()
    show_texture_viewer_for_file_node._window = window
    return window

# Main execution
if __name__ == "__main__":
    # This allows the script to be run directly in Maya
    show_shader_texture_viewer()

