
import maya.OpenMayaUI as omui

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

import maya.cmds as cmds
import os
import re
import json

# Stylesheet matching material converter
FALLBACK_STYLESHEET = """
/* ---- Base font ---- */
* {
    font-family: 'Segoe UI';
    font-size: 12px;
}

/* ---------------------------------------------
   Main Dialog
   --------------------------------------------- */
QDialog {
    background-color: #333333;
    color: #ffffff;
    font-family: 'Segoe UI';
    font-size: 12px;
}

/* ---------------------------------------------
   Labels
   --------------------------------------------- */
QLabel {
    color: #ffffff;
    font-family: 'Segoe UI';
    font-size: 12px;
}

/* ---------------------------------------------
   Buttons
   --------------------------------------------- */
QPushButton {
    font-family: 'Segoe UI';
    font-size: 12px;
    color: #ffffff;
    background-color: #666666;
    border: 1px solid #666666;
    border-radius: 6px;
    padding: 3px 6px;
    margin: 2px;
}

QPushButton:hover {
    background-color: #777777;
    border: 1px solid #777777;
}

QPushButton:pressed {
    background-color: #555555;
    border: 1px solid #555555;
}

QPushButton:disabled {
    color: #bbbbbb;
    background-color: #555555;
    border: 1px solid #666666;
}

/* ---------------------------------------------
   Line Edit
   --------------------------------------------- */
QLineEdit {
    font-family: 'Segoe UI';
    font-size: 12px;
    color: #ffffff;
    background-color: #333333;
    border: 1px solid #666666;
    border-radius: 6px;
    padding: 4px 8px;
}

QLineEdit:focus {
    border: 1px solid #00f7c8;
}

QLineEdit:disabled {
    color: #888888;
    background-color: #3a3a3a;
    border: 1px solid #555555;
}

/* ---------------------------------------------
   Checkboxes
   --------------------------------------------- */
QCheckBox {
    font-family: 'Segoe UI';
    font-size: 11px;
    color: #dddddd;
    border: none;
    border-radius: 6px;
    padding: 2px 6px;
    margin: 1px 0;
}

QCheckBox:checked {
    color: #00f7c8;
}

QCheckBox::indicator {
    width: 12px;
    height: 12px;
    border: 1px solid #444444;
    border-radius: 3px;
}

QCheckBox::indicator:checked {
    background-color: #ffffff;
    border: 1px solid #2b2b2b;
}

QCheckBox::indicator:unchecked {
    background-color: #2b2b2b;
    border: 1px solid #444444;
}

QCheckBox::indicator:checked:hover,
QCheckBox::indicator:unchecked:hover {
    border: 1px solid #ffffff;
}

/* ---------------------------------------------
   Radio Buttons
   --------------------------------------------- */
QRadioButton {
    font-family: 'Segoe UI';
    font-size: 11px;
    color: #dddddd;
    border: none;
    padding: 2px 6px;
}

QRadioButton:checked {
    color: #00f7c8;
}

QRadioButton::indicator {
    width: 12px;
    height: 12px;
    border: 1px solid #444444;
    border-radius: 6px;
}

QRadioButton::indicator:checked {
    background-color: #ffffff;
    border: 1px solid #2b2b2b;
}

QRadioButton::indicator:unchecked {
    background-color: #2b2b2b;
    border: 1px solid #444444;
}

/* ---------------------------------------------
   ComboBox
   --------------------------------------------- */
QComboBox {
    font-family: 'Segoe UI';
    font-size: 12px;
    color: #ffffff;
    background-color: #666666;
    border: 1px solid #555555;
    border-radius: 6px;
    padding: 4px 8px;
}

QComboBox:hover {
    background-color: #777777;
}

QComboBox::drop-down {
    border: none;
    width: 20px;
}

QComboBox::down-arrow {
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid #ffffff;
    width: 0px;
    height: 0px;
}

QComboBox QAbstractItemView {
    background-color: #666666;
    border: 1px solid #555555;
    selection-background-color: #888888;
    color: #ffffff;
}

/* ---------------------------------------------
   Tooltips
   --------------------------------------------- */
QToolTip {
    font-family: 'Segoe UI';
    font-size: 11px;
    color: #ffffff;
    background-color: #1e1e1e;
    border: 1px solid #666666;
    border-radius: 4px;
    padding: 4px 8px;
}
"""

def maya_main_window():
    """
    Return the Maya main window widget as a Python object.
    """
    main_window_ptr = omui.MQtUtil.mainWindow()
    return wrapInstance(int(main_window_ptr), QtWidgets.QWidget)


class MeshExporterUI(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(MeshExporterUI, self).__init__(parent or maya_main_window())
        self.setWindowTitle("Mesh Exporter")
        self.setMinimumWidth(450)
        self.setMinimumHeight(550)
        self.setWindowFlags(self.windowFlags() ^ QtCore.Qt.WindowContextHelpButtonHint)
        self.previous_export_path = ""
        self.presets_file = os.path.join(os.path.dirname(__file__), 'settings', 'mesh_exporter_presets.json')
        self.init_ui()
        self.load_settings()   # restore values
        self.load_presets()    # load presets list
        self.sync_all()        # force UI to reflect restored states
        self.setStyleSheet(FALLBACK_STYLESHEET)

    def init_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(8)

        # Title
        title = QtWidgets.QLabel("Mesh Exporter")
        title.setAlignment(QtCore.Qt.AlignCenter)
        title.setStyleSheet("font-weight:600; font-size:16px; padding:2px;")
        main_layout.addWidget(title)

        def make_separator(height=1):
            line = QtWidgets.QFrame()
            line.setFrameShape(QtWidgets.QFrame.HLine)
            line.setFrameShadow(QtWidgets.QFrame.Plain)
            line.setLineWidth(1)
            line.setFixedHeight(height)
            line.setStyleSheet("background-color:#333333; border:none;")
            return line

        # Format and Presets Row
        format_presets_layout = QtWidgets.QHBoxLayout()
        format_presets_layout.setContentsMargins(0, 0, 0, 0)
        format_presets_layout.setSpacing(6)

        # Export Format (FBX / OBJ) - Left side
        format_label = QtWidgets.QLabel("Format:")
        self.fbx_radio = QtWidgets.QRadioButton("FBX")
        self.obj_radio = QtWidgets.QRadioButton("OBJ")
        self.fbx_radio.setChecked(True)
        self.fbx_radio.toggled.connect(self.sync_export_type)

        format_presets_layout.addWidget(format_label)
        format_presets_layout.addWidget(self.fbx_radio)
        format_presets_layout.addWidget(self.obj_radio)
        format_presets_layout.addSpacing(20)

        # Presets Section - Right side of format row
        presets_label = QtWidgets.QLabel("Preset:")
        self.presets_combo = QtWidgets.QComboBox()
        self.presets_combo.setMinimumWidth(120)
        self.presets_combo.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self.presets_combo.currentIndexChanged.connect(self.on_preset_changed)
        self.presets_combo.setToolTip("Select a preset to load")

        self.load_preset_button = QtWidgets.QPushButton("Load")
        self.load_preset_button.setToolTip("Load the selected preset")
        self.load_preset_button.clicked.connect(self.load_selected_preset)

        self.save_preset_button = QtWidgets.QPushButton("Save")
        self.save_preset_button.setToolTip("Save current settings as a preset")
        self.save_preset_button.clicked.connect(self.save_current_as_preset)

        format_presets_layout.addWidget(presets_label)
        format_presets_layout.addWidget(self.presets_combo)
        format_presets_layout.addWidget(self.load_preset_button)
        format_presets_layout.addWidget(self.save_preset_button)
        format_presets_layout.addStretch(1)

        main_layout.addLayout(format_presets_layout)

        # Export Path Section
        path_frame = QtWidgets.QFrame()
        path_frame.setFrameShape(QtWidgets.QFrame.NoFrame)
        path_frame.setObjectName("pathFrame")
        path_frame.setStyleSheet(
            "QFrame#pathFrame {"
            " background-color:#3a3a3a;"
            " border: 2px solid #444444;"
            " border-radius: 8px;"
            " padding: 8px;"
            " margin: 3px;"
            "}"
        )
        path_layout = QtWidgets.QVBoxLayout(path_frame)
        path_layout.setContentsMargins(8, 8, 8, 8)
        path_layout.setSpacing(6)

        self.export_path_label = QtWidgets.QLabel("Export Path:")
        self.export_path_lineedit = QtWidgets.QLineEdit()
        self.export_path_set_button = QtWidgets.QPushButton("Set")
        self.export_path_set_button.clicked.connect(self.set_export_path)

        path_button_layout = QtWidgets.QHBoxLayout()
        path_button_layout.setContentsMargins(0, 0, 0, 0)
        path_button_layout.setSpacing(6)
        path_button_layout.addWidget(self.export_path_lineedit)
        path_button_layout.addWidget(self.export_path_set_button)

        path_layout.addWidget(self.export_path_label)
        path_layout.addLayout(path_button_layout)

        # Set as Current Project Directory
        self.current_project_checkbox = QtWidgets.QCheckBox("Set as Current File Directory")
        self.current_project_checkbox.toggled.connect(self.on_current_project_toggled)
        path_layout.addWidget(self.current_project_checkbox)

        main_layout.addWidget(path_frame)

        # Export Options Section
        options_frame = QtWidgets.QFrame()
        options_frame.setFrameShape(QtWidgets.QFrame.NoFrame)
        options_frame.setObjectName("optionsFrame")
        options_frame.setStyleSheet(
            "QFrame#optionsFrame {"
            " background-color:#3a3a3a;"
            " border: 2px solid #444444;"
            " border-radius: 8px;"
            " padding: 8px;"
            " margin: 3px;"
            "}"
        )
        options_layout = QtWidgets.QVBoxLayout(options_frame)
        options_layout.setContentsMargins(8, 8, 8, 8)
        options_layout.setSpacing(6)

        # Make Sub-folder
        self.subfolder_checkbox = QtWidgets.QCheckBox("Make Sub-folder")
        self.subfolder_checkbox.toggled.connect(self.on_subfolder_toggled)
        options_layout.addWidget(self.subfolder_checkbox)

        subfolder_row = QtWidgets.QHBoxLayout()
        subfolder_row.setContentsMargins(20, 0, 0, 0)
        subfolder_row.setSpacing(6)
        self.subfolder_name_label = QtWidgets.QLabel("Sub-folder Name:")
        self.subfolder_name_lineedit = QtWidgets.QLineEdit("mesh_export")
        self.subfolder_name_label.setEnabled(False)
        self.subfolder_name_lineedit.setEnabled(False)
        subfolder_row.addWidget(self.subfolder_name_label)
        subfolder_row.addWidget(self.subfolder_name_lineedit)
        options_layout.addLayout(subfolder_row)

        # Export as Separate Meshes
        self.separate_meshes_checkbox = QtWidgets.QCheckBox("Export as Separate Meshes")
        options_layout.addWidget(self.separate_meshes_checkbox)

        # Animated Export
        self.animated_checkbox = QtWidgets.QCheckBox("Animated")
        self.animated_checkbox.toggled.connect(self.on_animated_toggled)
        options_layout.addWidget(self.animated_checkbox)

        # Bake Animation
        self.bake_animation_checkbox = QtWidgets.QCheckBox("Bake Animation")
        self.bake_animation_checkbox.setEnabled(False)  # Initially disabled
        options_layout.addWidget(self.bake_animation_checkbox)

        # Smooth Exported Mesh Checkbox
        self.smooth_checkbox = QtWidgets.QCheckBox("Smooth Exported Mesh")
        options_layout.addWidget(self.smooth_checkbox)

        main_layout.addWidget(options_frame)

        # Name Pattern Section
        pattern_frame = QtWidgets.QFrame()
        pattern_frame.setFrameShape(QtWidgets.QFrame.NoFrame)
        pattern_frame.setObjectName("patternFrame")
        pattern_frame.setStyleSheet(
            "QFrame#patternFrame {"
            " background-color:#3a3a3a;"
            " border: 2px solid #444444;"
            " border-radius: 8px;"
            " padding: 8px;"
            " margin: 3px;"
            "}"
        )
        pattern_layout = QtWidgets.QVBoxLayout(pattern_frame)
        pattern_layout.setContentsMargins(8, 8, 8, 8)
        pattern_layout.setSpacing(6)

        self.name_pattern_label = QtWidgets.QLabel("Name Pattern:")
        self.name_pattern_lineedit = QtWidgets.QLineEdit("{object_name}_{version}")

        pattern_layout.addWidget(self.name_pattern_label)
        pattern_layout.addWidget(self.name_pattern_lineedit)

        # Add token buttons
        add_layout = QtWidgets.QHBoxLayout()
        add_layout.setContentsMargins(0, 0, 0, 0)
        add_layout.setSpacing(4)

        lbl = QtWidgets.QLabel("Add:")
        add_layout.addWidget(lbl)

        btn_obj = QtWidgets.QPushButton("Object Name")
        btn_obj.clicked.connect(lambda: self.insert_token("{object_name}"))
        btn_obj.setMaximumWidth(100)
        add_layout.addWidget(btn_obj)

        btn_scene = QtWidgets.QPushButton("Scene Name")
        btn_scene.clicked.connect(lambda: self.insert_token("{scene}"))
        btn_scene.setMaximumWidth(100)
        add_layout.addWidget(btn_scene)

        btn_ver = QtWidgets.QPushButton("Version")
        btn_ver.clicked.connect(lambda: self.insert_token("{version}"))
        btn_ver.setMaximumWidth(80)
        add_layout.addWidget(btn_ver)

        add_layout.addStretch(1)
        pattern_layout.addLayout(add_layout)
        main_layout.addWidget(pattern_frame)

        # Export Button and Version Up Checkbox
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(6)
        
        # Version Up Checkbox
        self.version_up_checkbox = QtWidgets.QCheckBox("Version Up")
        button_layout.addWidget(self.version_up_checkbox)
        
        self.export_button = QtWidgets.QPushButton("Export")
        self.export_button.clicked.connect(self.export_mesh)
        self.export_button.setStyleSheet("color: #00f7c8; font-weight:600;")
        self.export_button.setFixedHeight(32)
        button_layout.addWidget(self.export_button)
        button_layout.addStretch(1)
        
        main_layout.addLayout(button_layout)

        # Debugging Output
        self.debug_output_label = QtWidgets.QLabel()
        self.debug_output_label.setWordWrap(True)
        self.debug_output_label.setVisible(False)  # Hidden by default
        main_layout.addWidget(self.debug_output_label)

        # Open File Location button
        self.open_folder_button = QtWidgets.QPushButton("Open File Location")
        self.open_folder_button.setVisible(False)
        self.open_folder_button.clicked.connect(self.open_file_location)
        main_layout.addWidget(self.open_folder_button)

        main_layout.addStretch(1)

    def get_presets_file_path(self):
        """Get the path to the presets JSON file."""
        settings_dir = os.path.join(os.path.dirname(__file__), 'settings')
        os.makedirs(settings_dir, exist_ok=True)
        return os.path.join(settings_dir, 'mesh_exporter_presets.json')

    def load_presets(self):
        """Load the list of presets from the JSON file."""
        presets_file = self.get_presets_file_path()
        try:
            with open(presets_file, 'r') as f:
                presets_data = json.load(f)
                preset_names = list(presets_data.keys())
                self.presets_combo.clear()
                self.presets_combo.addItem("-- No Preset --", None)
                for name in sorted(preset_names):
                    self.presets_combo.addItem(name, name)
        except Exception:
            # File doesn't exist or is invalid, start with empty list
            self.presets_combo.clear()
            self.presets_combo.addItem("-- No Preset --", None)

    def save_current_as_preset(self):
        """Save the current settings (except export_path) as a new preset."""
        # Get preset name from user
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Save Preset", "Preset Name:", text=""
        )
        if not ok or not name.strip():
            return

        preset_name = name.strip()

        # Collect current settings (everything except export_path)
        preset_data = {
            'export_type': 'OBJ' if self.obj_radio.isChecked() else 'FBX',
            'current_project': self.current_project_checkbox.isChecked(),
            'make_subfolder': self.subfolder_checkbox.isChecked(),
            'subfolder_name': self.subfolder_name_lineedit.text(),
            'export_separate': self.separate_meshes_checkbox.isChecked(),
            'animated': self.animated_checkbox.isChecked(),
            'bake_animation': self.bake_animation_checkbox.isChecked(),
            'name_pattern': self.name_pattern_lineedit.text().replace('{mesh}', '{object_name}'),
            'version_up': self.version_up_checkbox.isChecked(),
            'smooth_export': self.smooth_checkbox.isChecked()
        }

        # Load existing presets
        presets_file = self.get_presets_file_path()
        presets_data = {}
        try:
            with open(presets_file, 'r') as f:
                presets_data = json.load(f)
        except Exception:
            pass

        # Add or update preset
        presets_data[preset_name] = preset_data

        # Save to file
        try:
            with open(presets_file, 'w') as f:
                json.dump(presets_data, f, indent=4)
            self.load_presets()  # Reload presets list
            # Select the newly saved preset
            idx = self.presets_combo.findData(preset_name)
            if idx != -1:
                self.presets_combo.setCurrentIndex(idx)
            cmds.warning(f"Preset '{preset_name}' saved successfully.")
        except Exception as e:
            cmds.warning(f"Failed to save preset: {e}")

    def load_selected_preset(self):
        """Load the currently selected preset."""
        preset_name = self.presets_combo.currentData()
        if not preset_name:
            cmds.warning("No preset selected.")
            return

        presets_file = self.get_presets_file_path()
        try:
            with open(presets_file, 'r') as f:
                presets_data = json.load(f)
                if preset_name not in presets_data:
                    cmds.warning(f"Preset '{preset_name}' not found.")
                    return

                preset = presets_data[preset_name]

                # Apply preset settings (everything except export_path)
                self.fbx_radio.setChecked(preset.get('export_type', 'FBX') == 'FBX')
                self.obj_radio.setChecked(preset.get('export_type') == 'OBJ')
                self.current_project_checkbox.setChecked(preset.get('current_project', False))
                self.subfolder_checkbox.setChecked(preset.get('make_subfolder', False))
                self.subfolder_name_lineedit.setText(preset.get('subfolder_name', 'mesh_export'))
                self.separate_meshes_checkbox.setChecked(preset.get('export_separate', False))
                self.animated_checkbox.setChecked(preset.get('animated', False))
                self.bake_animation_checkbox.setChecked(preset.get('bake_animation', False))
                self.name_pattern_lineedit.setText(preset.get('name_pattern', '{object_name}_{version}'))
                self.version_up_checkbox.setChecked(preset.get('version_up', False))
                self.smooth_checkbox.setChecked(preset.get('smooth_export', False))

                # Sync UI state
                self.sync_all()
                cmds.warning(f"Preset '{preset_name}' loaded successfully.")
        except Exception as e:
            cmds.warning(f"Failed to load preset: {e}")

    def on_preset_changed(self, index):
        """Handle preset dropdown change - auto-load if a preset is selected."""
        preset_name = self.presets_combo.currentData()
        if preset_name:  # Only auto-load if a valid preset is selected (not "-- No Preset --")
            self.load_selected_preset()

    def load_settings(self):
        """Load UI state from settings/mesh_exporter_settings.json if it exists."""
        settings_dir = os.path.join(os.path.dirname(__file__), 'settings')
        settings_path = os.path.join(settings_dir, 'mesh_exporter_settings.json')
        try:
            with open(settings_path, 'r') as f:
                s = json.load(f)
        except Exception:
            return
        # apply settings to widgets
        self.fbx_radio.setChecked(s.get('export_type', 'FBX') == 'FBX')
        self.obj_radio.setChecked(s.get('export_type') == 'OBJ')
        self.export_path_lineedit.setText(s.get('export_path', ''))
        self.current_project_checkbox.setChecked(s.get('current_project', False))
        self.subfolder_checkbox.setChecked(s.get('make_subfolder', False))
        self.subfolder_name_lineedit.setText(s.get('subfolder_name', 'mesh_export'))
        self.separate_meshes_checkbox.setChecked(s.get('export_separate', False))
        self.animated_checkbox.setChecked(s.get('animated', False))
        self.bake_animation_checkbox.setChecked(s.get('bake_animation', False))
        self.name_pattern_lineedit.setText(s.get('name_pattern', '{object_name}_{version}'))
        self.version_up_checkbox.setChecked(s.get('version_up', False))
        self.smooth_checkbox.setChecked(s.get('smooth_export', False))

    def save_settings(self):
        """Write current UI state to settings/mesh_exporter_settings.json."""
        settings = {
            'export_type': 'OBJ' if self.obj_radio.isChecked() else 'FBX',
            'export_path': self.export_path_lineedit.text(),
            'current_project': self.current_project_checkbox.isChecked(),
            'make_subfolder': self.subfolder_checkbox.isChecked(),
            'subfolder_name': self.subfolder_name_lineedit.text(),
            'export_separate': self.separate_meshes_checkbox.isChecked(),
            'animated': self.animated_checkbox.isChecked(),
            'bake_animation': self.bake_animation_checkbox.isChecked(),
            'name_pattern': self.name_pattern_lineedit.text().replace('{mesh}', '{object_name}'),
            'version_up': self.version_up_checkbox.isChecked(),
            'smooth_export': self.smooth_checkbox.isChecked()
        }
        settings_dir = os.path.join(os.path.dirname(__file__), 'settings')
        os.makedirs(settings_dir, exist_ok=True)
        settings_path = os.path.join(settings_dir, 'mesh_exporter_settings.json')
        with open(settings_path, 'w') as f:
            json.dump(settings, f, indent=4)

    def insert_token(self, token):
        """Insert a naming token at the cursor, adding '_' if needed."""
        le = self.name_pattern_lineedit
        text = le.text()
        pos = le.cursorPosition()
        prefix = text[:pos]
        insert = token
        if pos > 0 and not prefix.endswith('_'):
            insert = '_' + token
        new_text = prefix + insert + text[pos:]
        le.setText(new_text)
        le.setCursorPosition(pos + len(insert))

    def set_export_path(self):
        """Open a file dialog to select the export path."""
        selected_path = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Export Path")
        if selected_path:
            self.export_path_lineedit.setText(selected_path)
            self.previous_export_path = selected_path
            print(f"Export Path Set: {selected_path}")

    def on_current_project_toggled(self, checked: bool):
        """Set export path to current *scene* directory if checked, else revert."""
        if checked:
            # Use the canonical flag; fall back to workspace root if unsaved
            current_file = cmds.file(q=True, sn=True)  # 'sn' is reliable in Maya 2025
            project_directory = os.path.dirname(current_file) if current_file else cmds.workspace(q=True, rd=True)
            self.previous_export_path = self.export_path_lineedit.text()
            self.export_path_lineedit.setText(os.path.normpath(project_directory))
            print(f"Set Current Project Directory: {project_directory}")
        else:
            self.export_path_lineedit.setText(self.previous_export_path)
            print(f"Reverted to Previous Export Path: {self.previous_export_path}")

    def on_subfolder_toggled(self, checked: bool):
        self.subfolder_name_label.setEnabled(checked)
        self.subfolder_name_lineedit.setEnabled(checked)
        print(f"Sub-folder Name {'Enabled' if checked else 'Disabled'}")

    def _build_nested_subdir(self, base_path: str, subfolder_text: str) -> str:
        # Normalize base path and ensure it exists
        base_path = os.path.normpath(base_path)
        os.makedirs(base_path, exist_ok=True)

        # Clean and split user input by both "/" and "\"
        raw = (subfolder_text or "").strip().strip("/\\")
        if not raw:
            return base_path

        # Split on either separator; drop empty and "."; block ".."
        parts = [p for p in re.split(r"[\\/]+", raw) if p and p != "." and p != ".."]

        # Sanitize illegal filename characters on Windows
        parts = [re.sub(r'[<>:"|?*]', "_", p) for p in parts]

        full_path = os.path.normpath(os.path.join(base_path, *parts))
        os.makedirs(full_path, exist_ok=True)
        return full_path

    def on_animated_toggled(self, checked: bool):
        self.bake_animation_checkbox.setEnabled(checked)
        print(f"Animation Settings {'Enabled' if checked else 'Disabled'}")

    def sync_export_type(self, _checked: bool = False):
        """Ensure FBX/OBJ choice properly enables/disables animation controls."""
        is_obj = self.obj_radio.isChecked()
        if is_obj:
            # OBJ: no animation export
            self.animated_checkbox.setEnabled(False)
            self.animated_checkbox.setChecked(False)
            self.bake_animation_checkbox.setEnabled(False)
            self.bake_animation_checkbox.setChecked(False)
        else:
            # FBX
            self.animated_checkbox.setEnabled(True)
            self.bake_animation_checkbox.setEnabled(self.animated_checkbox.isChecked())

    def sync_all(self):
        """Call after load_settings() and anywhere you need to refresh dependent UI."""
        self.sync_export_type()
        self.on_subfolder_toggled(self.subfolder_checkbox.isChecked())
        self.on_animated_toggled(self.animated_checkbox.isChecked())
        # If the "current project" box is checked on load, re-apply the path:
        if self.current_project_checkbox.isChecked():
            self.on_current_project_toggled(True)

    def update_debug_output(self, exported_files, error=None):
        """Update the debug output section with exported file details."""
        if error:
            output_text = f"<p style='color: red;'><b>Error:</b> {error}</p>"
        else:
            output_text = "<p style='font-size: 14px;'><b>Output:</b></p>"
            for file_path, mesh_name in exported_files:
                directory, fbx_name = os.path.split(file_path)
                formatted_path = f"<i style='color: grey;'>{directory}/</i>"
                formatted_name = f"<i style='color: white;'>{fbx_name}</i>"
                output_text += f"<p>{formatted_path}{formatted_name}</p>"

        self.debug_output_label.setText(output_text)
        self.debug_output_label.setVisible(True)
        self.adjustSize()  # Resize the UI to fit the content

    def export_mesh(self):
        """Export selected meshes to FBX or OBJ files, with optional temporary smoothing."""
        # Gather UI state
        is_obj = self.obj_radio.isChecked()
        version_up = self.version_up_checkbox.isChecked()
        do_smooth = self.smooth_checkbox.isChecked()
        bake_animation = self.bake_animation_checkbox.isChecked() if self.animated_checkbox.isChecked() else False
        name_pattern = self.name_pattern_lineedit.text()
        export_path = os.path.normpath(self.export_path_lineedit.text())
        if self.subfolder_checkbox.isChecked():
            # Allows "a/b/c" style nested subfolders
            export_path = self._build_nested_subdir(export_path, self.subfolder_name_lineedit.text())
        else:
            os.makedirs(export_path, exist_ok=True)

        original_selection = cmds.ls(selection=True)
        selected_meshes = cmds.ls(selection=True, type="transform")
        if not selected_meshes:
            self.update_debug_output([], error="No meshes selected for export.")
            return

        exported_files = []

        # ---- BEGIN: temporary smooth chunk ----
        if do_smooth:
            cmds.undoInfo(openChunk=True)
            for m in selected_meshes:
                cmds.select(m, replace=True)
                cmds.polySmooth(m, dv=1, mth=0)
        # ---- END: temporary smooth chunk ----

        # Run your existing per-mesh export loop
        if self.separate_meshes_checkbox.isChecked():
            for i, mesh in enumerate(selected_meshes, start=1):
                file_path = self.export_single_mesh(
                    mesh, export_path, self.animated_checkbox.isChecked(),
                    bake_animation, version_up, name_pattern,
                    is_obj=is_obj, custom_index=str(i)
                )
                exported_files.append((file_path, mesh))
        else:
            meshes = selected_meshes if len(selected_meshes) > 1 else None
            name = selected_meshes[0] if meshes is None else "combined_mesh"
            file_path = self.export_single_mesh(
                name, export_path, self.animated_checkbox.isChecked(),
                bake_animation, version_up, name_pattern,
                is_obj=is_obj, meshes=meshes
            )
            exported_files.append((file_path, name))

        # ---- UNDO the smooth ----
        if do_smooth:
            cmds.undoInfo(closeChunk=True)
            cmds.undo()

        # Persist UI settings
        self.save_settings()

        # restore UI, debug, selection
        self.update_debug_output(exported_files)
        if exported_files:
            self.last_export_path = os.path.dirname(exported_files[-1][0])
            self.open_folder_button.setVisible(True)
        cmds.select(original_selection, replace=True)

    def get_meshes_from_group(self, group):
        """Retrieve all child transforms with shape nodes from a group."""
        if cmds.listRelatives(group, shapes=True):
            return [group]  # It's a mesh, not a group
        return cmds.listRelatives(group, allDescendents=True, type="transform") or []

    def export_single_mesh(self, mesh_name, export_path, is_animated, bake_animation, version_up,
                    name_pattern, is_obj=False, meshes=None, custom_index=""):
        """Export a single mesh or a group of meshes to FBX or OBJ file."""
        # 1. Gather scene name (no extension)
        full_scene = cmds.file(q=True, sceneName=True)
        scene_name = os.path.splitext(os.path.basename(full_scene))[0]

        # 2. Debug info
        print("\n--- DEBUG: Starting export_single_mesh ---")
        print(f"Format: {'OBJ' if is_obj else 'FBX'}")
        print(f"Mesh Name: {mesh_name}")
        print(f"Export Path: {export_path}")
        print(f"Name Pattern: {name_pattern}")
        print(f"Version Up: {version_up}")
        print(f"Custom Index: {custom_index}")
        print(f"Scene Name: {scene_name}\n")

        # 3. Normalize tokens, then inject values
        pattern = (name_pattern
                   .replace("{mesh}", "{object_name}")
                   .replace("{object}", "{object_name}")
                   .replace("{object_name}", mesh_name)
                   .replace("{file_name}", "{scene}")
                   .replace("{scene}", scene_name)
                   .replace("{Mesh}", mesh_name)
                   .replace("{version}", "{VERSION}")
                   .replace("{Version}", "{VERSION}"))
        print(f"Normalized Pattern: {pattern}\n")

        # 4. Base name for debug
        base_name = pattern.replace("{VERSION}", ".*")
        if base_name.endswith("_"):
            base_name = base_name[:-1]
        if "{mesh}" not in name_pattern.lower() and custom_index:
            base_name = f"{base_name}{custom_index}"
        print(f"Base Name for Matching: {base_name}\n")

        # 5. Determine extension and list files
        ext = "obj" if is_obj else "fbx"
        existing = os.listdir(export_path)

        # 6. Build anchored regex: literal part + v### + .ext
        literal = re.escape(pattern.replace("{VERSION}", ""))
        version_regex = re.compile(rf"^{literal}v(\d{{3}})\.{ext}$")

        # 7. Collect existing version numbers
        found_versions = []
        for f in existing:
            m = version_regex.match(f)
            if m:
                found_versions.append(int(m.group(1)))

        # 8. Pick version string
        if found_versions:
            latest = max(found_versions)
            if version_up:
                version_number = f"v{latest + 1:03d}"
            else:
                version_number = f"v{latest:03d}"
        else:
            version_number = "v001"

        # 9. Final filename + debug
        export_name = pattern.replace("{VERSION}", version_number)
        export_file = os.path.join(export_path, f"{export_name}.{ext}")
        print(f"Final Export Path: {export_file}\n")

        # 10. Select meshes
        if meshes:
            cmds.select(meshes, replace=True)
        else:
            cmds.select(mesh_name, replace=True)

        # 11. Perform export
        if is_obj:
            cmds.file(
                export_file,
                force=True,
                options="groups=1;ptgroups=1;materials=1;smoothing=1;normals=1",
                type="OBJexport",
                exportSelected=True
            )
        else:
            opts = "v=0;"
            if is_animated:
                opts += "animation=1;"
                opts += "bake=1;" if bake_animation else "bake=0;"
            else:
                opts += "animation=0;"
            print(f"FBX Options: {opts}\n")
            cmds.file(
                export_file,
                force=True,
                options=opts,
                type="FBX export",
                exportSelected=True,
                preserveReferences=False
            )

        print(f"Exported: {export_file}\n--- DEBUG Complete ---\n")
        return export_file

    def open_file_location(self):
        if hasattr(self, "last_export_path") and os.path.isdir(self.last_export_path):
            url = QtCore.QUrl.fromLocalFile(self.last_export_path)
            QtGui.QDesktopServices.openUrl(url)
        else:
            cmds.warning("No valid export path to open.")


def show_export_ui():
    """
    Show the Mesh Exporter UI in Maya.
    """
    global export_ui
    try:
        export_ui.close()  # Close the UI if it already exists
        export_ui.deleteLater()
    except:
        pass

    export_ui = MeshExporterUI()
    export_ui.show()
