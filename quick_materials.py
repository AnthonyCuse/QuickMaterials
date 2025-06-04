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
import importlib

import QuickMaterials.texture_importer as texture_importer
importlib.reload(texture_importer)
from QuickMaterials.texture_importer import ImportTxTool

# Global instance for the UI
quick_materials_ui_instance = None

from shiboken2 import isValid


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


class QuickMaterialsUI:
    def __init__(self):
        self.import_tx_tool = None
        # Store all UI elements in a dictionary
        self.ui_elements = {}
        self.initialize_ui()


# Initialize UI
    def initialize_ui(self):
        # Base stylesheet for the color display button, with a placeholder for dynamic color
        self.base_stylesheet = """
        QPushButton#colorDisplayButton {{
            background-color: {background_color};
            color: #ffffff;
            border: 0px solid #333333;
            border-radius: 15px;  /* Rounded corners */
            padding: 10px 20px;  /* Padding inside the button */
        }}

        QPushButton#colorDisplayButton:hover {{
            border: 2px solid #444444;
        }}

        QPushButton#colorDisplayButton:pressed {{
            border: 2px solid #888888;
        }}
        """

        # Stylesheet for grid‐style buttons (e.g., material swatches)
        self.grid_button_stylesheet = """
        QPushButton {{
            background-color: {background_color};
            border: 1px solid #333333;
            border-radius: 1px;  /* Slightly rounded corners */
            margin: 0px;  /* No margins to minimize spacing */
        }}

        QPushButton:hover {{
            border: 2px solid #ffffff;  /* Highlight border on hover */
        }}
        """

        # Stylesheet for the material list (buttons, line edits, labels)
        self.material_list_widget_style = """
        QPushButton {
            font-family: 'Segoe UI';
            font-size: 12px;
            color: #ffffff;
            background-color: #666666;
            border: 2px solid #666666;
            border-radius: 8px;
            padding: 3px 10px;
        }

        QPushButton:hover {
            background-color: #5a5a5a;
        }

        QPushButton:pressed {
            background-color: #4a4a4a;
        }

        QPushButton:disabled {
            color: #cccccc;
            border: 1px solid #555555;
            background-color: #7a7a7a;
        }

        QLineEdit {
            font-family: 'Segoe UI';
            font-size: 14px;
            color: #ffffff;
            background-color: #444444;
            border: 2px solid #444444;
            border-radius: 8px;
            padding: 3px 10px;
        }

        QLineEdit:hover {
            background-color: #3a3a3a;
        }

        QLineEdit:focus {
            border: 2px solid #555555;
            background-color: #4a4a4a;
        }

        QLabel {
            font-family: 'Segoe UI';
            font-size: 14px;
            color: #ffffff;
            background-color: transparent;
            border: none;
            padding: 3px 10px;
        }

        /* QLabel styles for default materials to prevent hover highlighting */
        QLabel[materialType="default"] {
            background-color: transparent;
            color: #aaaaaa;
            border: none;
        }

        QLabel[materialType="default"]:hover {
            background-color: transparent;
        }
        """

        # Stylesheet for the QColorDialog (color picker)
        self.qcolor_dialog_style = """
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

            # Load the .ui into a top‐level QWidget (no parent)
            uiInstance = loader.load(uiFile)

            # Close the .ui file now that it’s loaded
            uiFile.close()
        except Exception as e:
            print(f"Error loading UI file: {e}")
            return

        # Configure the window to behave as a normal standalone Qt window
        uiInstance.setWindowFlags(QtCore.Qt.Window)

        # Set the window’s title
        uiInstance.setWindowTitle("Quick Materials")

        # Collect all child widgets and layouts into ui_elements for easy lookup
        self.auto_initialize_ui_elements(uiInstance)

        # Store this top‐level window under the key 'quickMaterialsWindow'
        self.ui_elements['quickMaterialsWindow'] = uiInstance

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

        # Populate the materials list for the first time
        self.populate_materials_scroll_area()

        # Toggle each section’s initial visibility (all visible by default)
        self.toggle_layout_visibility(
            'materialCreatorLayout', 'toggleMaterialCreatorVis', 'Material Creator', force_hide=False
        )
        self.toggle_layout_visibility(
            'materialToolsLayout', 'toggleMaterialToolsVis', 'Material Tools', force_hide=False
        )
        self.toggle_layout_visibility(
            'materialListLayout', 'toggleMaterialListVis', 'Material List', force_hide=False
        )

        # Create the QColorDialog instance (hidden until needed)
        self.ui_elements['colorPicker'] = QtWidgets.QColorDialog()
        self.ui_elements['colorPicker'].setOptions(
            QtWidgets.QColorDialog.DontUseNativeDialog
        )

        # Set a fixed initial height, allow full width/height expansion
        uiInstance.setFixedHeight(375)
        uiInstance.setMinimumSize(0, 0)
        uiInstance.setMaximumSize(16777215, 16777215)

        # Make this window stay on top of Maya’s interface
        uiInstance.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.WindowStaysOnTopHint)

        # Show the window, then raise and activate it so it appears above other Maya dialogs
        uiInstance.show()
        uiInstance.raise_()
        uiInstance.activateWindow()

        # Give keyboard focus to the main UI
        uiInstance.setFocus()





# UI Functions
    def auto_initialize_ui_elements(self, parent_widget):
        """
        Recursively finds all children of the parent widget and stores them in the ui_elements dictionary.
        """
        for child in parent_widget.findChildren(QtCore.QObject):
            object_name = child.objectName()
            if object_name:  # Only store widgets that have an objectName
                self.ui_elements[object_name] = child
                # print(f"Initialized UI element: {object_name}")

    def setup_connections(self):
        """Set up all the necessary connections for the UI elements."""

        # Apply material button connection
        if self.ui_elements.get('createNewMaterialButton'):
            self.ui_elements['createNewMaterialButton'].clicked.connect(self.create_material)

        # Delete unused materials button connection
        if self.ui_elements.get('deleteUnusedMaterialsButton'):
            self.ui_elements['deleteUnusedMaterialsButton'].clicked.connect(self.delete_unused_materials)

        # Connect toggle default materials button to its function
        if self.ui_elements.get('toggleDefaultMaterialsButton'):
            self.ui_elements['toggleDefaultMaterialsButton'].clicked.connect(self.toggle_default_materials)

        # Connect delete selected materials button to delete_selected_materials function
        if self.ui_elements.get('deleteSelectedMaterialsButton'):
            self.ui_elements['deleteSelectedMaterialsButton'].clicked.connect(self.delete_selected_materials)

        # Connect toggle buttons for layouts with friendly names
        if self.ui_elements.get('toggleMaterialCreatorVis'):
            self.ui_elements['toggleMaterialCreatorVis'].clicked.connect(
                lambda: self.toggle_layout_visibility('materialCreatorLayout', 'toggleMaterialCreatorVis',
                                                      'Material Creator')
            )
        if self.ui_elements.get('toggleMaterialToolsVis'):
            self.ui_elements['toggleMaterialToolsVis'].clicked.connect(
                lambda: self.toggle_layout_visibility('materialToolsLayout', 'toggleMaterialToolsVis', 'Material Tools')
            )
        if self.ui_elements.get('toggleMaterialListVis'):
            self.ui_elements['toggleMaterialListVis'].clicked.connect(
                lambda: self.toggle_layout_visibility('materialListLayout', 'toggleMaterialListVis', 'Material List')
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

        # Populate the materialTypeComboBox with available materials
        material_type_combo_box = self.ui_elements.get('materialTypeComboBox')
        if material_type_combo_box:
            material_types = ['Blinn', 'Phong', 'Lambert', 'standardSurface']  # List of available material types
            material_type_combo_box.addItems(material_types)

        # self.ui_elements.get('materialPerMeshCheckbox').stateChanged.connect(self.update_random_hue_checkbox)

        self.ui_elements.get('materialPerMeshCheckbox').stateChanged.connect(self.update_create_material_button)

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

    def setup_color_sliders(self):
        """Set up the hue, saturation, and value sliders to update the color display button."""
        hue_slider = self.ui_elements.get('materialColorHueSlider')
        saturation_slider = self.ui_elements.get('materialColorSaturationSlider')
        value_slider = self.ui_elements.get('materialColorValueSlider')
        color_display_button = self.ui_elements.get('colorDisplayButton')

        # Ensure all sliders and button exist
        if not hue_slider or not saturation_slider or not value_slider or not color_display_button:
            print("Error: One or more sliders or the color display button are missing.")
            return

        # Set ranges for sliders
        hue_slider.setRange(0, 360)
        saturation_slider.setRange(0, 100)
        value_slider.setRange(0, 100)

        # Initialize sliders to default bright red color
        hue_slider.setValue(0)
        saturation_slider.setValue(100)
        value_slider.setValue(100)

        def update_color_from_sliders():
            """Update the color display and gradient when any slider value changes."""
            hue = hue_slider.value() / 360.0
            saturation = saturation_slider.value() / 100.0
            value = value_slider.value() / 100.0

            rgb_color = colorsys.hsv_to_rgb(hue, saturation, value)
            hex_color = "#{:02x}{:02x}{:02x}".format(
                int(rgb_color[0] * 255),
                int(rgb_color[1] * 255),
                int(rgb_color[2] * 255)
            )
            self.selected_color = QtGui.QColor(hex_color)
            self.update_button_color(color_display_button, self.selected_color)
            self.update_saturation_slider_gradient()

        # Connect each slider's value change to update the color
        hue_slider.valueChanged.connect(update_color_from_sliders)
        saturation_slider.valueChanged.connect(update_color_from_sliders)
        value_slider.valueChanged.connect(update_color_from_sliders)

        # Ensure the initial color is applied
        update_color_from_sliders()

        # Connect the color display button to open the color picker
        color_display_button.clicked.connect(self.open_and_sync_color_picker)

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
            border: 0px solid #999999;
            height: 10px;
            border-radius: 5px;
            background: qlineargradient(
                spread:pad,
                x1:0, y1:0,
                x2:1, y2:0,
                stop:0 hsv({hue_deg}, 0%, {value_percent}%),
                stop:1 hsv({hue_deg}, 100%, {value_percent}%)
            );
        }}
        QSlider::handle:horizontal {{
            background: #000000;
            border: 1px solid #555555;
            width: 3px;
            margin: -6px 0;
            border-radius: 10px;
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

    def setup_roughness_slider(self):
        """Configure roughness slider and spinbox behavior."""
        roughnessSlider = self.ui_elements.get('roughnessSlider')
        roughnessSpinBox = self.ui_elements.get('roughnessSpinBox')

        if roughnessSlider and roughnessSpinBox:
            roughnessSlider.setMinimum(0)  # Set slider range to 0 (for 0.0)
            roughnessSlider.setMaximum(1000)  # Set slider range to 1000 (for 1.0)

            # Sync slider changes to spinbox
            roughnessSlider.valueChanged.connect(lambda value: roughnessSpinBox.setValue(value / 1000.0))

            # Sync spinbox changes to slider
            roughnessSpinBox.valueChanged.connect(lambda value: roughnessSlider.setValue(int(value * 1000)))

            # Set initial values for the slider and spinbox
            initial_value = 0.75  # Example initial roughness value
            roughnessSlider.setValue(int(initial_value * 1000))
            roughnessSpinBox.setValue(initial_value)

    def toggle_layout_visibility(self, layout_name, button_name, friendly_name, force_hide=False):
        """
        Toggle the visibility of the specified layout's widgets and adjust the UI accordingly.
        Can be forced to hide elements on initialization by setting `force_hide=True`.

        Args:
            layout_name (str): The name of the layout to toggle.
            button_name (str): The name of the button controlling this layout's visibility.
            friendly_name (str): The friendly name to display on the toggle button.
            force_hide (bool): Whether to force-hide the layout on initialization.
        """
        # Get the target layout and button from ui_elements
        target_layout = self.ui_elements.get(layout_name)
        toggle_button = self.ui_elements.get(button_name)

        # Get the main UI window (quickMaterialsWindow)
        main_window = self.ui_elements.get('quickMaterialsWindow')

        # Ensure the target layout, button, and main window exist
        if not target_layout or not toggle_button or not main_window:
            print(f"Error: {layout_name}, {button_name}, or main window not found.")
            return

        # Helper function to find the first widget recursively, even within nested layouts
        def find_first_widget(layout):
            for i in range(layout.count()):
                item = layout.itemAt(i)
                widget = item.widget()

                # If we find a widget, return it
                if widget:
                    return widget

                # If it's a layout, recurse through it
                nested_layout = item.layout()
                if nested_layout:
                    widget = find_first_widget(nested_layout)
                    if widget:
                        return widget
            return None

        # Check for the first visible widget in the layout (including nested layouts)
        first_widget = find_first_widget(target_layout)

        if not first_widget:
            print(f"Error: No visible widget found in layout {layout_name}")
            return

        # Determine the visibility state
        layout_visible = first_widget.isVisible()

        # If force_hide is True, hide the layout (set visibility to False)
        if force_hide:
            layout_visible = True  # Force hide means we need to hide the layout

        # Recursively toggle visibility of all child widgets in the layout
        def toggle_visibility(parent_layout, visible):
            if isinstance(parent_layout, QtWidgets.QLayout):
                for i in range(parent_layout.count()):
                    item = parent_layout.itemAt(i)
                    layout = item.layout()
                    if layout:
                        toggle_visibility(layout, visible)
                    widget = item.widget()
                    if widget:
                        widget.setVisible(visible)

        # Start the toggle visibility process: hide if `layout_visible` is True, else show
        toggle_visibility(target_layout, not layout_visible)

        # Update the button text and checked state based on the visibility state
        toggle_button.setText(f"{friendly_name}" if not layout_visible else f"{friendly_name}")
        toggle_button.setChecked(not layout_visible)  # Set checked state

        # Call the resize_ui function to resize the window dynamically with delay
        self.resize_ui()

        # Debug: Print the current visibility state and button state
        # print(f"Toggling visibility for {friendly_name} (now {'hidden' if layout_visible else 'visible'})")

    def resize_ui(self, delay=5):
        """
        Resize the main UI window to 0 height after a slight delay to allow for proper event processing.
        Args:
            delay (int): Time in milliseconds to wait before resizing (default: 1ms).
        """

        # Get the main UI window (quickMaterialsWindow)+
        quick_materials_window = self.ui_elements.get('quickMaterialsWindow')  # Reference the correct main window

        # Ensure the main window exists
        if not quick_materials_window:
            print("Error: quickMaterialsWindow not found.")
            return

        # Use QTimer to delay the resizing action
        def perform_resize():
            # Check validity just before resizing
            if quick_materials_window and isValid(quick_materials_window):
                quick_materials_window.resize(quick_materials_window.width(), 0)
            else:
                print("DEBUG(resize_ui): Cannot resize—window is invalid or deleted.")

        QtCore.QTimer.singleShot(delay, perform_resize)


    def update_create_material_button(self):
        """
        Update the text on 'createNewMaterialButton' based on the state of 'materialPerMeshCheckbox'.
        Uncheck 'randomHueCheckbox' when 'materialPerMeshCheckbox' is unchecked.
        """
        material_per_mesh_checked = self.ui_elements.get('materialPerMeshCheckbox').isChecked()
        create_material_button = self.ui_elements.get('createNewMaterialButton')

        if material_per_mesh_checked:
            create_material_button.setText('Create New Material(s)')
        else:
            create_material_button.setText('Create New Material')
            self.ui_elements.get('randomHueCheckbox').setChecked(False)  # Uncheck randomHueCheckbox


# Material Creator Functions
    def create_material(self):
        """Create and apply materials with proper color handling."""
        print("Starting material creation...")  # Debug

        if not self.ensure_arnold_plugin():
            print("Failed to load Arnold plugin.")  # Debug
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
        """Ensure the Arnold plugin is loaded."""
        if not cmds.pluginInfo('mtoa', query=True, loaded=True):
            try:
                cmds.loadPlugin('mtoa')
            except RuntimeError:
                cmds.warning("Arnold plugin could not be loaded.")
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
        if self.ui_elements['randomHueCheckbox'].isChecked():
            # Generate a new random hue for the display (doesn't affect the material created)
            random_hue = random.uniform(0, 1)
            self.selected_color.setHsvF(random_hue, 1.0, 1.0)

        # Update the color display button with the new hue
        self.update_button_color(self.ui_elements['colorDisplayButton'], self.selected_color)

        # Update the sliders to reflect the new random hue
        hue = self.selected_color.hueF() * 360
        self.ui_elements['materialColorHueSlider'].setValue(int(hue))
        self.ui_elements['materialColorSaturationSlider'].setValue(int(self.get_current_saturation() * 100))
        self.ui_elements['materialColorValueSlider'].setValue(int(self.get_current_value() * 100))

        # Refresh the saturation slider gradient to match the new hue
        self.update_saturation_slider_gradient()

        # print(f"Updated color display to new random hue: {hue}")

    def generate_material(self, mesh_name, color_rgb, used_material_names):
        """
        Create and set up the material and shading group for the given mesh.
        """
        material_type = self.determine_material_type().lower()
        if material_type == "standardsurface":
            material_type = "standardSurface"

        # Generate a unique material name
        material_name = self.get_unique_material_name(mesh_name, material_type, used_material_names)

        try:
            material = cmds.shadingNode(material_type, asShader=True, name=material_name)
        except RuntimeError as e:
            cmds.warning(f"Failed to create material: {e}")
            return None

        # Set the material color attribute
        color_attr = ".baseColor" if material_type == "standardSurface" else ".color"
        try:
            cmds.setAttr(material + color_attr, *color_rgb, type="double3")
        except RuntimeError as e:
            cmds.warning(f"Error setting color: {e}")
            return None

        # Create and connect the shading group
        shading_group = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=f"{material_name}SG")
        try:
            cmds.connectAttr(material + ".outColor", shading_group + ".surfaceShader", force=True)
        except RuntimeError as e:
            cmds.warning(f"Failed to connect material: {e}")
            return None

        # Set material attributes like roughness
        roughness = self.ui_elements.get('roughnessSpinBox').value() if self.ui_elements.get(
            'roughnessSpinBox') else 0.75
        self.set_material_attributes(material, material_type, roughness)

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

    def update_random_hue_checkbox(self):
        """
        Enable or disable the 'randomHueCheckbox' based on the state of 'materialPerMeshCheckbox'.
        """
        material_per_mesh_checked = self.ui_elements.get('materialPerMeshCheckbox').isChecked()
        random_hue_checkbox = self.ui_elements.get('randomHueCheckbox')

        if material_per_mesh_checked:
            random_hue_checkbox.setEnabled(True)  # Enable if materialPerMeshCheckbox is checked
        else:
            random_hue_checkbox.setEnabled(False)  # Disable if materialPerMeshCheckbox is unchecked
            random_hue_checkbox.setChecked(False)  # Optionally, uncheck it when disabled

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

# Material List
    def populate_materials_scroll_area(self, hide_defaults=False, search_text="", saved_selection=None):
        self.selected_materials_list = []
        scrollArea = self.ui_elements.get('materialsListScrollArea')

        # Check if scrollArea is valid before proceeding
        if not scrollArea:
            return

        # Retrieve all materials in the scene and set of default materials to exclude
        default_materials = {'lambert1', 'standardSurface1', 'particleCloud1'}
        all_materials = cmds.ls(materials=True)

        # Determine which materials to display based on hide_defaults and search text
        materials_to_display = [
            mat for mat in all_materials
            if (not hide_defaults or mat not in default_materials) and (
                    not search_text or search_text.lower() in mat.lower())
        ]

        # Clear existing contents in the scroll area before repopulating
        if scrollArea.widget():
            scrollArea.widget().deleteLater()  # Remove existing widget to avoid overlapping

        # Create a new widget and layout for the scroll area contents
        scroll_content = QtWidgets.QWidget()
        scroll_layout = QtWidgets.QGridLayout(scroll_content)
        scroll_layout.setContentsMargins(5, 5, 5, 5)
        scroll_layout.setVerticalSpacing(2)
        scroll_layout.setHorizontalSpacing(5)

        # In populate_materials_scroll_area
        row = 0
        for material in materials_to_display:
            is_default = material in default_materials  # Determine if the material is default
            self.add_material_entry(material, row, scroll_layout, default_materials, saved_selection)
            self.add_material_buttons(material, row, scroll_layout, is_default)  # Pass the flag
            row += 2  # Increment row by 2 to leave space for buttons

        # Add a vertical spacer at the bottom of the scroll area
        spacer_item = QtWidgets.QSpacerItem(20, 40, QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Expanding)
        scroll_layout.addItem(spacer_item, row, 0, 1, 4)

        # Set the new widget as the content of the scroll area
        scrollArea.setWidget(scroll_content)

    def create_color_box(self, color_hex):
        """Create a small color swatch box for materials."""
        color_box = QtWidgets.QWidget()
        color_box.setFixedSize(20, 20)
        color_box.setStyleSheet(f"background-color: {color_hex}; border: 1px solid #333;")
        return color_box

    def on_checkbox_state_changed(self, state, material):
        """Handle checkbox state changes for material selection."""
        if state == QtCore.Qt.Checked:
            if material not in self.selected_materials_list:
                self.selected_materials_list.append(material)
        else:
            if material in self.selected_materials_list:
                self.selected_materials_list.remove(material)

        delete_btn = self.ui_elements.get('deleteSelectedMaterialsButton')
        if delete_btn:
            delete_btn.setText(f"Delete Selected ({len(self.selected_materials_list)} items)")
        else:
            print("Error: deleteSelectedMaterialsButton not found")

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
            is_attr_available = full_attr in [f"{material}.{a}" for a in available_attrs]
            # print(f"[DEBUG] Trying attribute '{attr}': Exists = {is_attr_available}")

            if is_attr_available:
                try:
                    val = cmds.getAttr(full_attr)
                    # print(f"[DEBUG] Value for {full_attr}: {val}")

                    # If val is a list with one tuple inside, extract that tuple
                    if isinstance(val, list) and len(val) == 1 and isinstance(val[0], (tuple, list)) and len(
                            val[0]) == 3:
                        # print(f"[DEBUG] Found valid color attribute: {attr}")
                        return attr
                    else:
                        return None
                        # print(f"[DEBUG] {attr} is not an RGB triple in the expected format.")
                except Exception as e:
                    return None
                    # print(f"[DEBUG] Error getting {full_attr}: {e}")

        # print("[DEBUG] No suitable color attribute found.")
        return None

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
        material_layout.setSpacing(4)

        # Only create a checkbox for non-default materials
        material_checkbox = None
        if material not in default_materials:
            material_checkbox = QtWidgets.QCheckBox()
            material_checkbox.setFixedSize(15, 15)  # Adjust size as needed
            # Connect checkbox state change to selection handler
            material_checkbox.stateChanged.connect(
                lambda state, mat=material: self.on_checkbox_state_changed(state, mat)
            )

            # Reapply the saved selection state if available
            if saved_selection and material in saved_selection:
                material_checkbox.setChecked(True)

        # Try to find a suitable color attribute
        color_attr = self.get_material_color_attribute(material)

        if color_attr:
            try:
                val = cmds.getAttr(f"{material}.{color_attr}")
                # Extract the first tuple
                if isinstance(val, list) and len(val) == 1 and isinstance(val[0], (tuple, list)) and len(val[0]) == 3:
                    color = val[0]
                    # Clamp the color values
                    r = max(min(color[0], 1.0), 0.0)
                    g = max(min(color[1], 1.0), 0.0)
                    b = max(min(color[2], 1.0), 0.0)

                    color_hex = "#{:02x}{:02x}{:02x}".format(
                        int(r * 255),
                        int(g * 255),
                        int(b * 255)
                    )
                else:
                    # Fallback if the format isn't as expected
                    color_hex = "#ffffff"
            except Exception:
                color_hex = "#ffffff"
        else:
            # If no valid color attribute found, fallback to white or a neutral color
            color_hex = "#ffffff"

        # Create the color box
        color_box = self.create_color_box(color_hex)

        # Create a label or editable line edit for the material name
        if material in default_materials:
            material_widget = QtWidgets.QLabel(material)
        else:
            material_widget = QtWidgets.QLineEdit(material)
            material_widget.setMinimumWidth(150)
            try:
                material_widget.editingFinished.connect(partial(self.rename_material, material_widget, material))
            except AttributeError:
                print("Error: rename_material function not found")

        # Apply styles and size policies
        material_widget.setStyleSheet(self.material_list_widget_style)
        material_widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        color_box.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)

        # Add the checkbox (if it exists), color box, and material name to the layout
        if material_checkbox:
            material_layout.addWidget(material_checkbox, 0)
        material_layout.addWidget(color_box, 0)
        material_layout.addWidget(material_widget, 1)

        # Create a container widget for the material entry and add it to the scroll layout
        entry_container = QtWidgets.QWidget()
        entry_container.setLayout(material_layout)
        scroll_layout.addWidget(entry_container, row, 0, 1, 4)

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
        button_layout.setSpacing(5)

        # Create and style common buttons
        assign_btn = QtWidgets.QPushButton("Assign")
        highlight_btn = QtWidgets.QPushButton("Highlight")
        select_btn = QtWidgets.QPushButton("Select")

        # Apply styles
        for btn in [assign_btn, highlight_btn, select_btn]:
            btn.setStyleSheet(self.material_list_widget_style)

        # Connect signals
        assign_btn.clicked.connect(partial(self.assign_material, material))
        highlight_btn.clicked.connect(partial(self.highlight_material, material))
        select_btn.clicked.connect(partial(self.select_material, material))

        # Add common buttons to the layout
        button_layout.addWidget(assign_btn)
        button_layout.addWidget(highlight_btn)
        button_layout.addWidget(select_btn)

        # Always create 'Import Tx' button
        import_tx_btn = QtWidgets.QPushButton("Import Tx")
        import_tx_btn.setStyleSheet(self.material_list_widget_style)

        if is_default:
            # Disable the button and apply a greyed-out style
            import_tx_btn.setEnabled(False)
            import_tx_btn.setStyleSheet(
                """
                QPushButton {
                    background-color: #888;
                    color: #ccc;
                }
                QPushButton:disabled {
                    color: #444444;
                    background-color: #666666;
                    border: 2px solid #666666;
                    border-radius: 8px;
                    padding: 3px 10px;
                }
                """
            )
            # Optionally, add a tooltip to explain why it's disabled
            import_tx_btn.setToolTip("Cannot import textures for default materials.")
        else:
            # Enable the button and connect its signal
            import_tx_btn.setEnabled(True)
            import_tx_btn.clicked.connect(partial(self.import_tx_material, material))

        # Add 'Import Tx' button to the layout
        button_layout.addWidget(import_tx_btn)

        # Create a container widget for the buttons and add it below the material entry
        button_container = QtWidgets.QWidget()
        button_container.setLayout(button_layout)
        scroll_layout.addWidget(button_container, row + 1, 0, 1, 4)

    # Material List Entry Button Functions
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

    def highlight_material(self, material):
        shading_group = cmds.listConnections(material + '.outColor', type='shadingEngine')
        if not shading_group:
            cmds.warning(f"No shading group found for material {material}.")
            return

        shading_group = shading_group[0]
        objects_with_material = cmds.sets(shading_group, q=True)
        if objects_with_material:
            cmds.select(objects_with_material, replace=True)
        else:
            cmds.warning(f"No objects found with material {material}.")

    def select_material(self, material):
        cmds.select(material, replace=True)

    def rename_material(self, material_name_edit, original_name):
        new_name = material_name_edit.text().strip()
        if new_name and new_name != original_name:
            try:
                cmds.rename(original_name, new_name)
                material_name_edit.setText(new_name)  # Update the QLineEdit with the new name
            except Exception as e:
                cmds.warning(f"Failed to rename material: {e}")

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

    # Material List Functions
    def filter_materials(self, search_text):
        scrollArea = self.ui_elements.get('materialsListScrollArea')
        hide_defaults = self.hide_defaults_state  # Assuming you track the state in the class
        self.populate_materials_scroll_area(hide_defaults=hide_defaults, search_text=search_text)

    def toggle_default_materials(self):
        toggleDefaultMaterialsButton = self.ui_elements.get('toggleDefaultMaterialsButton')
        scrollArea = self.ui_elements.get('materialsListScrollArea')
        materialSearchLineEdit = self.ui_elements.get('materialSearchLineEdit')

        if not toggleDefaultMaterialsButton or toggleDefaultMaterialsButton.signalsBlocked():
            return

        toggleDefaultMaterialsButton.blockSignals(True)

        saved_selection = set(self.selected_materials_list)
        self.hide_defaults_state = not self.hide_defaults_state
        toggleDefaultMaterialsButton.setText(
            "Show Default Materials" if self.hide_defaults_state else "Hide Default Materials")

        search_text = materialSearchLineEdit.text() if materialSearchLineEdit else ""
        self.populate_materials_scroll_area(hide_defaults=self.hide_defaults_state, search_text=search_text,
                                            saved_selection=saved_selection)

        if materialSearchLineEdit:
            materialSearchLineEdit.setFocus()

        toggleDefaultMaterialsButton.blockSignals(False)

    def refresh_materials_list(self):
        # Get the scroll area from the UI elements
        scrollArea = self.ui_elements.get('materialsListScrollArea')

        # Get the current search text from the materialSearchLineEdit
        materialSearchLineEdit = self.ui_elements.get('materialSearchLineEdit')
        search_text = materialSearchLineEdit.text() if materialSearchLineEdit else ""

        # If the scroll area is valid, refresh the materials list with the current search text and hide_defaults state
        if scrollArea:
            self.populate_materials_scroll_area(hide_defaults=self.hide_defaults_state, search_text=search_text)

    def toggle_material_selection(self, state, material):
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

    def set_material_attributes(self, material_name, material_type, roughness):
        # Blinn: Map roughness to Eccentricity and Specular Roll Off
        if material_type == 'blinn':
            eccentricity = 0.05 + (roughness * (0.8 - 0.05))
            specular_roll_off = 1 - roughness
            try:
                cmds.setAttr(f"{material_name}.eccentricity", eccentricity)
                cmds.setAttr(f"{material_name}.specularRollOff", specular_roll_off)
            except RuntimeError as e:
                cmds.warning(f"Error setting Blinn material attributes: {e}")

        # Phong: Map roughness to Cosine Power and Specular Color
        elif material_type == 'phong':
            cosine_power = 100 - (roughness * 98)
            specular_color = (1 - roughness, 1 - roughness, 1 - roughness)
            try:
                cmds.setAttr(f"{material_name}.cosinePower", cosine_power)
                cmds.setAttr(f"{material_name}.specularColor", *specular_color, type="double3")
            except RuntimeError as e:
                cmds.warning(f"Error setting Phong material attributes: {e}")

        # Lambert: No roughness effect
        elif material_type == 'lambert':
            print("Roughness has no effect on Lambert materials.")

        # Standard Surface (Arnold): Directly set the Roughness attribute
        elif material_type == 'standardSurface':
            try:
                cmds.setAttr(f"{material_name}.specularRoughness", roughness)
            except RuntimeError as e:
                cmds.warning(f"Error setting Standard Surface roughness attribute: {e}")

    def determine_material_type(self):
        """
        Determine the material type based on the current selection in the materialTypeComboBox.
        """
        material_type_combo_box = self.ui_elements.get('materialTypeComboBox')
        if material_type_combo_box:
            return material_type_combo_box.currentText()  # Return the selected material type
        else:
            return "Lambert"  # Default to Lambert if the combo box is not found

    def is_object_in_shading_group(self, obj, shading_group):
        current_shading_groups = cmds.listConnections(obj, type='shadingEngine')
        return current_shading_groups and shading_group in current_shading_groups

    def toggle_select_all_visible_materials(self):
        """
        Toggle between selecting and deselecting all visible materials.
        """
        button = self.ui_elements.get('selectAllVisibleMaterialsButton')
        scrollArea = self.ui_elements.get('materialsListScrollArea')

        # Ensure the scroll area and button are valid
        if not scrollArea or not scrollArea.widget() or not button:
            print("Error: Scroll area or button not found.")
            return

        # Determine whether to select or deselect all based on the current button text
        is_selecting_all = button.text() == "Select All"

        # Loop through all child widgets in the scroll area to find checkboxes
        scroll_content = scrollArea.widget()
        for material_entry_widget in scroll_content.findChildren(QtWidgets.QWidget):
            for checkbox in material_entry_widget.findChildren(QtWidgets.QCheckBox):
                # Set the checkbox based on the current mode
                checkbox.setChecked(is_selecting_all)

        # Update the button text based on the action performed
        button.setText("Deselect All" if is_selecting_all else "Select All")

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




# Load UI Function
def load_ui():
    global quick_materials_ui_instance
    if quick_materials_ui_instance:
        from shiboken2 import isValid
        old_win = quick_materials_ui_instance.ui_elements.get('quickMaterialsWindow')
        if old_win and isValid(old_win):
            old_win.close()


    quick_materials_ui_instance = QuickMaterialsUI()


