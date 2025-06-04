# Import necessary modules at the beginning of the script
import os
import colorsys  # For HSV to RGB conversion
from PySide2 import QtCore, QtUiTools, QtWidgets, QtGui
from shiboken2 import wrapInstance
from functools import partial
import maya.cmds as cmds
import maya.OpenMayaUI as omui
import maya.mel as mel
import random
import re
import json


# --------------------------------------------------------------------------------
# Global texture type definitions
# --------------------------------------------------------------------------------

STANDARD_TEXTURE_TYPES = [
    "baseColor",
    "roughness",
    "normal",
    "emission",
    "opacity",
    "metallic"
]

ADVANCED_TEXTURE_TYPES = [
    "subsurface",
    "subsurfaceColor",
    "specularColor",
    "transmission",
    "displacement",
    "coat",
    "sheen"
]

# All texture types in one combined list (standard first, then advanced)
ALL_TEXTURE_TYPES = STANDARD_TEXTURE_TYPES + ADVANCED_TEXTURE_TYPES


def maya_main_window():
    """Get the Maya main window as a QtWidgets.QWidget."""
    main_window_ptr = omui.MQtUtil.mainWindow()
    if main_window_ptr is not None:
        return wrapInstance(int(main_window_ptr), QtWidgets.QWidget)  # For Python 3
    return None

class ImportTxTool(QtWidgets.QWidget):
    def __init__(self, material=None, material_type=None, parent=None):
        super(ImportTxTool, self).__init__(parent)

        self.material = material
        self.material_type = material_type

        # Holds references to the dynamically created channel containers by texture_type
        self.channel_containers = {}

        self.ui_elements = {}

        self.connections_initialized = False

        self.search_folder_path = ""  # Initialize the search folder path variable

        self.texture_data = {}

        self.init_ui()

    def init_ui(self):
        loader = QtUiTools.QUiLoader()

        script_dir = os.path.dirname(__file__)
        ui_file_path = os.path.join(script_dir, "QtDesigner", "textureImporter.ui")

        ui_file = QtCore.QFile(ui_file_path)
        ui_file.open(QtCore.QFile.ReadOnly)
        self.ui_instance = loader.load(ui_file, parentWidget=None)
        ui_file.close()

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.ui_instance)

        self.auto_initialize_ui_elements(self.ui_instance)
        print(f"[DEBUG] ui_elements: {self.ui_elements}")

        self.hide_adv_textures()

        # Initialize scroll area
        scroll_area = self.ui_elements["texturesScrollArea"]
        scroll_area.setWidgetResizable(True)

        self.populate_material_combo_box()

        self.auto_populate_search_folder()

        self.setup_scroll_area_ui()

        # Ensure scroll area content widget and layout are flexible
        scroll_area_content = scroll_area.widget()
        if not scroll_area_content:
            scroll_area_content = QtWidgets.QWidget()
            scroll_area.setWidget(scroll_area_content)

        scroll_layout = QtWidgets.QVBoxLayout(scroll_area_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(4)
        scroll_area_content.setLayout(scroll_layout)

        # Add a spacer to allow dynamic shrinking
        spacer = QtWidgets.QSpacerItem(20, 40, QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Expanding)
        scroll_layout.addItem(spacer)

        # Populate channel containers for every texture type (all standard + advanced)
        for ttype in ALL_TEXTURE_TYPES:
            container_name = f"{ttype}ChannelsContainer"
            container = self.ui_elements.get(container_name)
            if container:
                self.populate_channel_container(container, ttype)
                container.setVisible(False)  # Hide by default
            else:
                # If a channel container is missing, bail out to avoid partial initialization
                return

        self.setWindowTitle("Import Textures")
        self.setWindowFlags(
            self.windowFlags() |
            QtCore.Qt.Window |
            QtCore.Qt.WindowMaximizeButtonHint |
            QtCore.Qt.WindowMinimizeButtonHint
        )

        # Set minimum size - Adjust these values as needed
        self.setMinimumSize(400, 300)

        # Set default/initial size - Adjust these values as needed
        self.resize(515, 535)

        self.setup_connections()

    def auto_initialize_ui_elements(self, parent_widget):
        for child in parent_widget.findChildren(QtWidgets.QWidget):
            obj_name = child.objectName()
            if obj_name:
                # Check if the element has already been initialized
                if obj_name not in self.ui_elements:
                    self.ui_elements[obj_name] = child
            # Only recurse if the child is a container type and hasn't been processed
            if isinstance(child, (QtWidgets.QGroupBox, QtWidgets.QWidget, QtWidgets.QFrame, QtWidgets.QScrollArea)):
                self.auto_initialize_ui_elements(child)

    def setup_connections(self):
        if self.connections_initialized:
            print("[DEBUG] Connections already initialized. Skipping.")
            return
        # Advanced Textures Toggle Button
        if "showAdvTexturesButton" in self.ui_elements:
            button = self.ui_elements["showAdvTexturesButton"]
            button.clicked.disconnect()  # Disconnect any existing connection
            button.clicked.connect(self.toggle_adv_textures)
            print("[DEBUG] Connected showAdvTexturesButton to toggle_adv_textures")
        else:
            print("[DEBUG] showAdvTexturesButton not found in ui_elements")

        # Show Channels Buttons & “Set” Buttons (all texture types)
        for texture_type in ALL_TEXTURE_TYPES:
            show_btn_name = f"{texture_type}ShowChannelsButton"
            if show_btn_name in self.ui_elements:
                btn = self.ui_elements[show_btn_name]
                try:
                    btn.clicked.disconnect()
                except Exception:
                    pass
                btn.clicked.connect(partial(self.toggle_channel_container, texture_type))
                print(f"[DEBUG] Connected {show_btn_name} to toggle_channel_container")
            else:
                print(f"[DEBUG] {show_btn_name} not found in ui_elements")

            set_btn_name = f"{texture_type}SetButton"
            if set_btn_name in self.ui_elements:
                btn = self.ui_elements[set_btn_name]
                try:
                    btn.clicked.disconnect()
                except Exception:
                    pass
                btn.clicked.connect(partial(self.select_texture_file, texture_type))
                print(f"[DEBUG] Connected {set_btn_name} to select_texture_file")
            else:
                print(f"[DEBUG] {set_btn_name} not found in ui_elements")

        # Auto Set Button for Texture Types
        for texture_type in ALL_TEXTURE_TYPES:
            auto_btn_name = f"{texture_type}AutoButton"
            auto_btn = self.ui_elements.get(auto_btn_name)
            if auto_btn:
                auto_btn.clicked.connect(partial(self.auto_set_texture, texture_type))
                self._debug_print(f"Connected {auto_btn_name} to auto_set_texture")
            else:
                self._debug_print(f"{auto_btn_name} not found")


        # Search Folder Set Button
        if "searchFolderSetButton" in self.ui_elements:
            button = self.ui_elements["searchFolderSetButton"]

            # Disconnect only if connected (PySide2 and PySide6 compatible)
            button.blockSignals(True)  # Block signals temporarily

            # Check for existing connections using QMetaMethod (PySide2 compatible)
            index = button.metaObject().indexOfSignal("clicked()")
            if index != -1:  # Signal found
                method = button.metaObject().method(index)
                if button.isSignalConnected(method):
                    button.clicked.disconnect()
                    print("[DEBUG] Disconnected existing signals from searchFolderSetButton")

            button.blockSignals(False)  # Unblock signals

            button.clicked.connect(self.select_search_folder)
            print("[DEBUG] Connected searchFolderSetButton to select_search_folder")
        else:
            print("[DEBUG] searchFolderSetButton not found in ui_elements")


        self.connections_initialized = True  # Mark connections as initialized

        # Connect the editTextureSearchNamesButton to open the TextureSearchNamesUI
        if "editTextureSearchNamesButton" in self.ui_elements:
            button = self.ui_elements["editTextureSearchNamesButton"]
            try:
                button.clicked.disconnect()
            except Exception:
                pass
            button.clicked.connect(self.open_texture_search_names_ui)
            self._debug_print("Connected editTextureSearchNamesButton to open_texture_search_names_ui")
        else:
            self._debug_print("editTextureSearchNamesButton not found in UI elements.")

    def populate_material_combo_box(self):
        """Populates the material combo box with all materials in the scene and an 'All Materials' option."""
        self.ui_elements["materialComboBox"].clear()

        # Add "All Materials" option first
        self.ui_elements["materialComboBox"].addItem("All Materials")

        all_materials = self.get_all_materials_sorted()
        self.ui_elements["materialComboBox"].addItems(all_materials)

        # Pre-select the material passed during initialization, if any
        if self.material:
            index = self.ui_elements["materialComboBox"].findText(self.material)
            if index >= 0:
                self.ui_elements["materialComboBox"].setCurrentIndex(index)
            else:
                # If the material is not found, default to "All Materials"
                self.ui_elements["materialComboBox"].setCurrentIndex(0)
        else:
            # If no material is specified, default to "All Materials"
            self.ui_elements["materialComboBox"].setCurrentIndex(0)

    def get_all_materials_sorted(self):
        """Gets all materials in the scene, sorted alphabetically, excluding specific default materials."""
        default_materials = {'lambert1', 'standardSurface1', 'particleCloud1'}
        all_materials = cmds.ls(materials=True)
        filtered_materials = sorted([mat for mat in all_materials if mat not in default_materials])
        return filtered_materials

    def auto_populate_search_folder(self):
        """
        Auto-populate the search folder line edit with the directory of the current Maya scene file.
        """
        search_line_edit = self.ui_elements.get("searchFolderLineEdit")
        if not search_line_edit:
            cmds.warning("searchFolderLineEdit not found in UI elements.")
            return

        # Use the directory of the current Maya scene file as the starting folder.
        scene_path = cmds.file(query=True, sceneName=True)
        if scene_path:
            scene_dir = os.path.dirname(scene_path)
            search_line_edit.setText(scene_dir)
            self.search_folder_path = scene_dir
        else:
            # If the scene isn't saved yet, leave the field empty or set a default.
            search_line_edit.setText("")
            self.search_folder_path = ""

    def select_search_folder(self):
        """Opens a file dialog to select a directory using the current line edit text as the start directory,
        and updates the searchFolderLineEdit."""
        options = QtWidgets.QFileDialog.Options()
        options |= QtWidgets.QFileDialog.ShowDirsOnly

        # Get the current path from the line edit, if available
        search_line_edit = self.ui_elements.get("searchFolderLineEdit")
        start_dir = ""
        if search_line_edit:
            start_dir = search_line_edit.text()

        folder_path = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Directory", start_dir, options=options)

        if folder_path:
            if search_line_edit:
                search_line_edit.setText(folder_path)
            self.search_folder_path = folder_path
            print(f"[DEBUG] Search folder path updated to: {folder_path}")


    def recursive_search_for_texture(self, start_folder, pattern):
        """
        Recursively search for files whose names contain the given pattern (case-insensitive).
        If nothing is found in the current folder (and its subfolders), move upward one folder,
        then search again.

        Args:
            start_folder (str): The folder to start the search from.
            pattern (str): A substring pattern to search for in file names.

        Returns:
            found_files (list): A list of matching file paths.
            search_steps (list): A list of folder paths that were searched.
        """
        current_folder = start_folder
        found_files = []
        search_steps = []

        while True:
            self._debug_print("Searching in folder: {}".format(current_folder))
            search_steps.append(current_folder)

            for root, dirs, files in os.walk(current_folder):
                for filename in files:
                    if pattern.lower() in filename.lower():
                        full_path = os.path.join(root, filename)
                        found_files.append(full_path)
                        self._debug_print("  Found file: {}".format(full_path))

            if found_files:
                self._debug_print("Match(es) found in folder '{}':".format(current_folder))
                for f in found_files:
                    self._debug_print("  " + f)
                return found_files, search_steps
            else:
                parent_folder = os.path.dirname(current_folder)
                if parent_folder == current_folder or not parent_folder:
                    self._debug_print("Reached top of directory hierarchy; no matching textures found.")
                    return found_files, search_steps
                self._debug_print("No matches in '{}'. Moving up to: {}\n".format(current_folder, parent_folder))
                current_folder = parent_folder

    def auto_set_texture(self, texture_type):
        """
        Auto-search for a texture file matching the given texture type.
        The search uses:
          - The current search folder (self.search_folder_path),
          - The current material name from materialComboBox,
          - A naming pattern: '{material}_{texture_type}'.

        On finding a match, update the corresponding line edit (e.g. 'baseColorLineEdit').

        Args:
            texture_type (str): The texture type to search for (e.g., 'baseColor').
        """
        # Ensure we have a search folder
        if not self.search_folder_path or not os.path.isdir(self.search_folder_path):
            cmds.warning("Search folder is not valid.")
            return

        # Get the current material name from the combo box
        material = self.ui_elements.get("materialComboBox").currentText() if self.ui_elements.get(
            "materialComboBox") else ""
        if not material:
            cmds.warning("Material not selected.")
            return

        # Build the search pattern (e.g. "M_pants_red_BaseColor")
        # You can later enhance this using additional keywords or a custom naming pattern.
        search_pattern = "{}_{}".format(material, texture_type)
        self._debug_print("Auto-searching for texture pattern: '{}' starting at: '{}'".format(search_pattern,
                                                                                              self.search_folder_path))

        # Call the recursive search function
        found_files, steps = self.recursive_search_for_texture(self.search_folder_path, search_pattern)
        if found_files:
            # For now, take the first match
            file_path = found_files[0]
            self._debug_print("Auto-set found texture: {}".format(file_path))
            # Process the file as if it was selected via the normal file dialog.
            self.process_selected_texture(file_path, texture_type)
        else:
            cmds.warning("No matching texture found for '{}' using pattern '{}'.".format(texture_type, search_pattern))

    def select_texture_file(self, texture_type):
        """Opens a file dialog to select a texture file and updates the corresponding line edit."""
        options = QtWidgets.QFileDialog.Options()
        # options |= QtWidgets.QFileDialog.DontUseNativeDialog  # Consider using if you have issues with the native dialog

        # Start in the search folder if it's set
        start_dir = self.search_folder_path if self.search_folder_path else ""

        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(self, f"Select {texture_type} Texture", start_dir,
                                                            "Image Files (*.png *.jpg *.jpeg *.tif *.tiff *.exr)",
                                                            options=options)

        if file_path:
            self.process_selected_texture(file_path, texture_type)

    def process_selected_texture(self, file_path, texture_type):
        """
        Processes the selected texture, detects UDIMs, and updates the corresponding line edit.
        """
        # Extract the file name and directory
        file_dir, file_name = os.path.split(file_path)
        file_base, file_ext = os.path.splitext(file_name)

        # Assume no UDIMs by default
        udim_count = 0
        udim_pattern = ""

        # UDIM Detection Logic (more robust version in next step)
        if ".10" in file_base:
            udim_pattern = self.detect_udim_pattern(file_base)
            if udim_pattern:
                udim_count = self.count_udim_tiles(file_dir, file_base, file_ext, udim_pattern)

        # Update the Line Edit
        line_edit_name = f"{texture_type}LineEdit"
        if line_edit_name in self.ui_elements:
            line_edit = self.ui_elements[line_edit_name]
            if udim_count > 1:
                line_edit.setText(f"{file_name} ({udim_count} Tiles)")
            else:
                line_edit.setText(file_name)

        # Store the selected texture information (for later import)
        texture_data = {
            "path": file_path,
            "name": file_name,
            "udim_pattern": udim_pattern,
            "udim_count": udim_count,
        }
        self.texture_data[texture_type] = texture_data

    def detect_udim_pattern(self, file_base):
        """
        Detects potential UDIM patterns in the file base name.

        Returns:
            str: The detected UDIM pattern (e.g., "10\d{2}") or None if no pattern is found.
        """
        udim_patterns = [
            r"10\d{2}",  # Standard UDIM pattern (1001, 1002, ...)
            # Add more patterns if needed, e.g., r"u\d+_v\d+" for MARI, etc.
        ]

        for pattern in udim_patterns:
            if re.search(pattern, file_base):
                return pattern

        return None  # No pattern found

    def count_udim_tiles(self, file_dir, file_base, file_ext, udim_pattern):
        """
        Counts the number of UDIM tiles in the same directory that match the pattern.

        Returns:
            int: The number of UDIM tiles found.
        """
        udim_regex = re.compile(udim_pattern)
        count = 0
        for f in os.listdir(file_dir):
            if os.path.splitext(f)[1] == file_ext:
                base_without_udim = udim_regex.sub("", file_base)
                f_base_without_udim = udim_regex.sub("", os.path.splitext(f)[0])
                if base_without_udim == f_base_without_udim:
                    count += 1
        return count


    def open_texture_search_names_ui(self):
        """
        Instantiates and shows the TextureSearchNamesUI.
        """
        # Since the class is in the same file, just reference it directly.
        if not hasattr(self, 'texture_search_names_ui') or self.texture_search_names_ui is None:
            self.texture_search_names_ui = TextureSearchNamesUI(parent=self)
        self.texture_search_names_ui.show()
        self.texture_search_names_ui.raise_()  # Bring it to the front
        self._debug_print("TextureSearchNamesUI opened.")


    def hide_adv_textures(self):
        print("DEBUG: Called hide_adv_textures()")
        if "advTexturesContainer" in self.ui_elements:
            print("DEBUG: advTexturesContainer found, hiding it now.")
            self.ui_elements["advTexturesContainer"].setVisible(False)

    def setup_scroll_area_ui(self):
        scroll_area = self.ui_elements["texturesScrollArea"]
        scroll_area.setWidgetResizable(True)
        scroll_area.setMinimumSize(0, 0)
        scroll_area.setSizePolicy(QtWidgets.QSizePolicy.MinimumExpanding, QtWidgets.QSizePolicy.MinimumExpanding)

        scroll_area_content = QtWidgets.QWidget()
        scroll_layout = QtWidgets.QVBoxLayout(scroll_area_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(4)
        scroll_area.setWidget(scroll_area_content)

        # Apply the stylesheet to the scroll area and its children
        scroll_area.setStyleSheet(scroll_area_stylesheet)

        # Create a container widget for the scroll area content
        scroll_area_content = QtWidgets.QWidget()
        scroll_layout = QtWidgets.QVBoxLayout(scroll_area_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(0)
        scroll_area.setWidget(scroll_area_content)

        # Standard textures container
        self.standard_textures_container = QtWidgets.QWidget()
        self.standard_textures_layout = QtWidgets.QVBoxLayout(self.standard_textures_container)
        self.standard_textures_layout.setContentsMargins(0, 0, 0, 0)
        self.standard_textures_layout.setSpacing(0)
        scroll_layout.addWidget(self.standard_textures_container)

        # Create a horizontal layout for the "Show Adv Textures" button with spacer
        adv_textures_button_layout = QtWidgets.QHBoxLayout()
        adv_textures_button_layout.setContentsMargins(0, 0, 0, 0)
        adv_textures_button_layout.setSpacing(0)

        # Show advanced textures button
        self.ui_elements["showAdvTexturesButton"] = QtWidgets.QPushButton("Show Adv Textures +")
        self.ui_elements["showAdvTexturesButton"].clicked.connect(self.toggle_adv_textures)
        adv_textures_button_layout.addWidget(self.ui_elements["showAdvTexturesButton"])

        # Add a spacer to push the button to the left
        spacer = QtWidgets.QSpacerItem(40, 20, QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum)
        adv_textures_button_layout.addItem(spacer)

        # Add the button layout to the scroll area layout
        scroll_layout.addLayout(adv_textures_button_layout)

        # Advanced textures container
        self.adv_textures_container = QtWidgets.QWidget()
        self.adv_textures_layout = QtWidgets.QVBoxLayout(self.adv_textures_container)
        self.adv_textures_layout.setContentsMargins(0, 0, 0, 0)
        self.adv_textures_layout.setSpacing(0)
        scroll_layout.addWidget(self.adv_textures_container)
        self.adv_textures_container.setVisible(False)  # Hide by default
        self.ui_elements["advTexturesContainer"] = self.adv_textures_container

        # Populate standard and advanced texture types
        self.populate_texture_types()

        # Add a spacer to allow dynamic shrinking
        spacer = QtWidgets.QSpacerItem(20, 40, QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Expanding)
        scroll_layout.addItem(spacer)

    def populate_texture_types(self):
        """
        Populate the standard and advanced texture containers dynamically
        based on the global texture type lists.
        """
        # Populate standard textures
        for texture_type in STANDARD_TEXTURE_TYPES:
            container = self.create_texture_type_container(texture_type)
            self.standard_textures_layout.addWidget(container)

        # Populate advanced textures
        for texture_type in ADVANCED_TEXTURE_TYPES:
            container = self.create_texture_type_container(texture_type)
            self.adv_textures_layout.addWidget(container)

    def create_texture_type_container(self, texture_type):
        """
        Create a container widget for a given texture type with reduced spacing.
        Now includes an Auto button next to the Set button.
        """
        # Create the main container with a vertical layout
        container = QtWidgets.QWidget()
        container_layout = QtWidgets.QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)  # No outer margins
        container_layout.setSpacing(2)  # Small spacing between rows

        # Create the horizontal layout for the top row: [ShowChannels] [Label] [LineEdit] [Set] [Auto]
        row_layout = QtWidgets.QHBoxLayout()
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)

        # 1) Show Channels button (“+”)
        show_channels_button = QtWidgets.QPushButton("+")
        show_channels_button.setObjectName(f"{texture_type}ShowChannelsButton")
        show_channels_button.clicked.connect(partial(self.toggle_channel_container, texture_type))
        row_layout.addWidget(show_channels_button)

        # 2) Label
        label = QtWidgets.QLabel(texture_type)
        label.setObjectName(f"{texture_type}Label")
        label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignBottom)
        label.setFixedWidth(120)  # Keeps all labels the same width
        row_layout.addWidget(label)

        # 3) Line Edit
        line_edit = QtWidgets.QLineEdit()
        line_edit.setObjectName(f"{texture_type}LineEdit")
        row_layout.addWidget(line_edit)

        # 4) Set Button
        set_button = QtWidgets.QPushButton("Set")
        set_button.setObjectName(f"{texture_type}SetButton")
        row_layout.addWidget(set_button)

        # 5) Auto Button (new)
        auto_button = QtWidgets.QPushButton("Auto")
        auto_button.setObjectName(f"{texture_type}AutoButton")
        row_layout.addWidget(auto_button)

        # Add this row layout to the vertical container layout
        container_layout.addLayout(row_layout)

        # Channels container (hidden by default)
        channels_container = QtWidgets.QWidget()
        channels_container.setObjectName(f"{texture_type}ChannelsContainer")
        channels_container_layout = QtWidgets.QVBoxLayout(channels_container)
        channels_container_layout.setContentsMargins(0, 0, 0, 0)
        channels_container_layout.setSpacing(2)

        channels_container.setVisible(False)  # Start hidden

        # Alignments
        container_layout.setAlignment(QtCore.Qt.AlignBottom)
        channels_container_layout.setAlignment(QtCore.Qt.AlignTop)

        # Add the channels container below the row
        container_layout.addWidget(channels_container)

        # Store references in ui_elements so setup_connections() can find them
        self.ui_elements[f"{texture_type}ShowChannelsButton"] = show_channels_button
        self.ui_elements[f"{texture_type}Label"] = label
        self.ui_elements[f"{texture_type}LineEdit"] = line_edit
        self.ui_elements[f"{texture_type}SetButton"] = set_button
        self.ui_elements[f"{texture_type}AutoButton"] = auto_button
        self.ui_elements[f"{texture_type}ChannelsContainer"] = channels_container

        return container

    def populate_channel_container(self, container, texture_type):
        """
        Ensure the container uses a vertical layout, add a label, checkboxes, and a spacer to align them to the left.
        """
        # Ensure the container has a vertical layout
        if not container.layout():
            container.setLayout(QtWidgets.QVBoxLayout())
        container_layout = container.layout()
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)  # Adjust spacing as needed

        # Add the label
        label = QtWidgets.QLabel("Select channel(s) to connect to material:")
        label.setStyleSheet(label_stylesheet)
        label.setAlignment(QtCore.Qt.AlignLeft)
        container_layout.addWidget(label)

        # Add the checkboxes in a horizontal layout
        checkboxes_layout = QtWidgets.QHBoxLayout()
        checkboxes_layout.setContentsMargins(0, 0, 0, 0)
        checkboxes_layout.setSpacing(4)

        red_cb = QtWidgets.QCheckBox("R")
        green_cb = QtWidgets.QCheckBox("G")
        blue_cb = QtWidgets.QCheckBox("B")
        alpha_cb = QtWidgets.QCheckBox("A")

        # Apply the checkbox stylesheet and add checkboxes to the layout
        for cb in (red_cb, green_cb, blue_cb, alpha_cb):
            cb.setStyleSheet(checkbox_stylesheet)
            checkboxes_layout.addWidget(cb)

        # Add a spacer to push the checkboxes to the left
        spacer = QtWidgets.QSpacerItem(40, 20, QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum)
        checkboxes_layout.addItem(spacer)

        # Add the checkboxes layout to the vertical container layout
        container_layout.addLayout(checkboxes_layout)

        # Optionally store references for later use
        self.ui_elements[f"{texture_type}ChannelRedCheckbox"] = red_cb
        self.ui_elements[f"{texture_type}ChannelGreenCheckbox"] = green_cb
        self.ui_elements[f"{texture_type}ChannelBlueCheckbox"] = blue_cb
        self.ui_elements[f"{texture_type}ChannelAlphaCheckbox"] = alpha_cb

    def toggle_channel_container(self, texture_type):
        print(f"[DEBUG] toggle_channel_container called for {texture_type}")
        container_name = f"{texture_type}ChannelsContainer"
        button_name = f"{texture_type}ShowChannelsButton"

        container = self.ui_elements.get(container_name)
        button = self.ui_elements.get(button_name)

        if container and button:
            currently_visible = container.isVisible()
            container.setVisible(not currently_visible)
            button.setText("-" if container.isVisible() else "+")
            print(f"[DEBUG] {container_name} visibility set to {not currently_visible}")
        else:
            print(f"[DEBUG] Missing {container_name} or {button_name}")

    def toggle_adv_textures(self):
        print("[DEBUG] toggle_adv_textures called")
        adv_container = self.ui_elements.get("advTexturesContainer")
        button = self.ui_elements.get("showAdvTexturesButton")

        if adv_container and button:
            currently_visible = adv_container.isVisible()
            adv_container.setVisible(not currently_visible)
            button.setText("Hide Adv Textures -" if not currently_visible else "Show Adv Textures +")
            print(f"[DEBUG] Advanced Textures visibility set to {not currently_visible}")
        else:
            print("[DEBUG] Missing advTexturesContainer or showAdvTexturesButton")


    def _debug_print(self, msg):
        """
        Helper method to print debug messages.
        You can later replace or extend this with a logging mechanism.
        """
        print("[DEBUG] " + msg)


    def show_import_tx_tool(self):
        self.show()



checkbox_stylesheet = """
QCheckBox {
    font-family: 'Segoe UI';  /* Sets the font to Segoe UI */
    font-size: 12px;  /* Adjust the font size as needed */
    color: #ffffff;  /* White text color */
    background-color: #444444;  /* Dark background color */
    border: 0px solid #444444;  /* Optional border with dark grey color */
    border-radius: 8px;  /* Rounded corners */
    padding-top: 0px;
    padding-bottom: 10px;
    padding-right: 0px;
    padding-left: 0px;
}
    QCheckBox::indicator {
        width: 16px;  /* Width of the checkbox indicator */
        height: 16px;  /* Height of the checkbox indicator */
        border: 2px solid #444444;  /* Dark grey border color when unchecked */
        border-radius: 4px;  /* Rounded corners for the checkbox indicator */
        background-color: #2b2b2b;  /* Darker grey background when unchecked */
    }
    QCheckBox::indicator:checked {
        background-color: #ffffff;  /* White background when checked */
        border: 2px solid #ffffff;  /* White border when checked */
    }
    QCheckBox::indicator:unchecked {
        background-color: #2b2b2b;  /* Darker grey background when unchecked */
        border: 2px solid #444444;  /* Dark grey border when unchecked */
    }
    QCheckBox::indicator:checked:pressed {
        background-color: #ffffff;  /* Darker grey background when unchecked */
        border: 2px solid #ffffff;  /* Dark grey border when unchecked */
        border: 2px solid #444444;  /* Dark grey border color when unchecked */
    }
    QCheckBox::indicator:checked:hover {
        background-color: #ffffff;  /* White background when checked */
        border: 2px solid #ffffff;  /* White border when checked */
    }
    QCheckBox::indicator:unchecked:pressed {
        background-color: #ffffff;  /* White background when checked */
        border: 2px solid #ffffff;  /* White border when checked */
    }
    QCheckBox::indicator:unchecked:hover {
        background-color: #555555;  /* White background when checked */
        border: 2px solid #ffffff;  /* White border when checked */
    }

    """

label_stylesheet = """
    QLabel {
        font-family: 'Segoe UI';  /* Sets the font to Segoe UI */
        font-size: 14px;  /* Adjust the font size as needed */
        color: #777777;  /* Grey text color */
        border: 0px solid #666666;  /* Optional border with dark grey color */
        border-radius: 8px;  /* Rounded corners */
        padding-top: 0px;
        padding-bottom: 0px;
        padding-right: 0px;
        padding-left: 0px;
}
    """

scroll_area_stylesheet = """
QLabel {
    font-family: 'Segoe UI';
    font-size: 16px;
    color: #d6d6d6;
    border: 0px solid #555555;
    border-radius: 8px;
    padding: 0px 0px;
}

QPushButton {
    font-family: 'Segoe UI';
    font-size: 14px;
    color: #ffffff;
    background-color: #666666;
    border: 2px solid #444444;
    border-radius: 0px;
    padding: 2px 5px;
}

QPushButton:hover {
    background-color: #777777;
}

QPushButton:pressed {
    background-color: #1a1a1a;
}

QPushButton:disabled {
    color: #cccccc;
    border: 1px solid #555555;
    background-color: #4a4a4a;
}

QLineEdit {
    font-family: 'Segoe UI';
    font-size: 14px;
    color: #ffffff;
    background-color: #333333;  /* Retaining the original background color */
    border: 2px solid #444444;
    border-radius: 8px;
    padding: 2px 10px;
}

QLineEdit:hover {
    background-color: #222222;
}

QLineEdit:focus {
    border: 2px solid #555555;
    background-color: #4a4a4a;
}
"""



class TextureSearchNamesUI(QtWidgets.QWidget):
    """
    Loads textureSearchnames.ui, then dynamically builds the scroll area content
    for each texture type (label + line edit). Finally, it saves the entered keywords
    into a JSON file under <script_dir>/settings/texture_search_names.json.
    """
    def __init__(self, parent=None):
        super(TextureSearchNamesUI, self).__init__(parent)
        self.ui_elements = {}
        # Use the global list instead of redefining:
        self.texture_types = ALL_TEXTURE_TYPES

        # 1) Load the existing .ui file
        self.load_ui_file()

        # 2) Turn this QWidget into a standalone window
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.Window)
        self.setWindowTitle("Edit Texture Search Names")

        # 3) Populate the scroll area with dynamic rows
        self.populate_texture_names_scroll_area()

        # 4) Connect signals (e.g., Save button)
        self.setup_connections()

    def load_ui_file(self):
        """
        Loads textureSearchnames.ui and auto-initializes ui_elements dict.
        Assumes the .ui file has at least:
          - texturesNameEditScrollArea      (QScrollArea)
          - saveTextureNamesButton          (QPushButton)
        """
        loader = QtUiTools.QUiLoader()

        script_dir = os.path.dirname(__file__)
        ui_path = os.path.join(script_dir, "QtDesigner", "textureSearchnames.ui")

        ui_file = QtCore.QFile(ui_path)
        if not ui_file.open(QtCore.QFile.ReadOnly):
            cmds.warning(f"Cannot open UI file: {ui_path}")
            return

        self.ui_instance = loader.load(ui_file, parentWidget=self)
        ui_file.close()

        # Place the loaded UI into this widget’s layout
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.ui_instance)

        # Auto-initialize all named child widgets into self.ui_elements
        self.auto_initialize_ui_elements(self.ui_instance)

    def auto_initialize_ui_elements(self, parent_widget):
        """
        Recursively finds all child widgets with an objectName and stores them in self.ui_elements.
        """
        for child in parent_widget.findChildren(QtWidgets.QWidget):
            name = child.objectName()
            if name:
                self.ui_elements[name] = child
            # Recurse into container widgets
            if isinstance(child, (QtWidgets.QWidget, QtWidgets.QFrame, QtWidgets.QScrollArea, QtWidgets.QGroupBox)):
                self.auto_initialize_ui_elements(child)

    def populate_texture_names_scroll_area(self):
        """
        Finds texturesNameEditScrollArea in self.ui_elements, clears it, and populates with:
          - One row widget per texture type containing [QLabel] + [QLineEdit].
        Each line edit is stored as "<textureType>TextureNameLineEdit" in self.ui_elements.
        """
        # 1) Find the scroll area
        scroll_area = self.ui_elements.get("texturesNameEditScrollArea")
        if not scroll_area:
            cmds.warning("texturesNameEditScrollArea not found in UI.")
            return

        # 2) Remove any existing widget inside it (if re-populating)
        existing = scroll_area.widget()
        if existing:
            existing.deleteLater()

        # 3) Create a new container widget and layout for the scroll area
        content_widget = QtWidgets.QWidget()
        content_widget.setObjectName("texturesNameEditScrollAreaWidgetContents")
        scroll_area.setWidget(content_widget)
        content_layout = QtWidgets.QVBoxLayout(content_widget)
        content_layout.setContentsMargins(5, 5, 5, 5)
        content_layout.setSpacing(8)

        # 4) For each texture type, create a row: [Label] [LineEdit]
        for ttype in self.texture_types:
            row_widget = QtWidgets.QWidget()
            row_layout = QtWidgets.QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(10)

            # 4a) A QLabel: e.g. "Basecolor:"
            label = QtWidgets.QLabel(f"{ttype.capitalize()}:")
            label.setFixedWidth(100)
            label.setStyleSheet("font-size: 12px;")
            row_layout.addWidget(label)

            # 4b) A QLineEdit with objectName "<textureType>TextureNameLineEdit"
            line_edit = QtWidgets.QLineEdit()
            line_edit.setObjectName(f"{ttype}TextureNameLineEdit")
            line_edit.setPlaceholderText(f"{ttype.capitalize()}, {ttype[:3].upper()}")
            row_layout.addWidget(line_edit, 1)  # stretch = 1

            # Store reference for later (saving/loading)
            self.ui_elements[f"{ttype}TextureNameLineEdit"] = line_edit

            content_layout.addWidget(row_widget)

        # 5) Add an expanding spacer at the bottom
        spacer = QtWidgets.QSpacerItem(20, 40,
                                       QtWidgets.QSizePolicy.Minimum,
                                       QtWidgets.QSizePolicy.Expanding)
        content_layout.addItem(spacer)

    def setup_connections(self):
        """
        Connect the Save Texture Names button to save_texture_names().
        """
        save_btn = self.ui_elements.get("saveTextureNamesButton")
        if save_btn:
            save_btn.clicked.connect(self.save_texture_names)
        else:
            cmds.warning("saveTextureNamesButton not found in UI.")

    def save_texture_names(self):
        """
        Gathers keywords from each "<textureType>TextureNameLineEdit", builds a dict:
          { "baseColor": [...], "roughness": [...], ... }
        Then writes it out to "<script_dir>/settings/texture_search_names.json".
        """
        texture_names = {}
        for ttype in self.texture_types:
            key = f"{ttype}TextureNameLineEdit"
            le = self.ui_elements.get(key)
            if le:
                text = le.text()
                keywords = [kw.strip() for kw in text.split(",") if kw.strip()]
                texture_names[ttype] = keywords
            else:
                cmds.warning(f"{key} not found in UI elements.")

        # Ensure settings folder exists
        script_dir = os.path.dirname(__file__)
        settings_folder = os.path.join(script_dir, "settings")
        if not os.path.isdir(settings_folder):
            os.makedirs(settings_folder)

        save_path = os.path.join(settings_folder, "texture_search_names.json")
        try:
            with open(save_path, "w") as f:
                json.dump(texture_names, f, indent=4)
            cmds.confirmDialog(title="Success",
                               message="Texture search names saved successfully.",
                               button=["OK"])
        except Exception as e:
            cmds.confirmDialog(title="Error",
                               message=f"Failed to save texture search names:\n{e}",
                               button=["OK"])
