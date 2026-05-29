import os
import sys
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
import maya.cmds as cmds
import maya.OpenMayaUI as omui
import maya.mel as mel
import random
import re
import json
import importlib
from collections import Counter

import QuickMaterials.help_doc_viewer
importlib.reload(QuickMaterials.help_doc_viewer)


def _load_quick_materials_all_settings():
    """
    Same resolution as quick_materials.load_quick_materials_settings_full_dict
    (kept here to avoid import cycle: quick_materials imports this module).
    """
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings")
    for fn in ("quick_materials_settings.json", "quick_materials_settings_default.json"):
        p = os.path.join(d, fn)
        try:
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            continue
    return {}


def _texture_search_names_user_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings", "texture_search_names.json")


def _texture_search_names_default_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings", "texture_search_names_default.json")


def _texture_search_names_legacy_path():
    """Older installs may only have this path; still read it before falling back to default."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "Settings", "texture_search_names.json")


def load_texture_search_names_raw_dict():
    """
    Resolution order: user (settings/) → legacy (Settings/) → packaged default.
    save_texture_names() writes only the user path; default JSON is never overwritten.
    """
    for path in (
        _texture_search_names_user_path(),
        _texture_search_names_legacy_path(),
        _texture_search_names_default_path(),
    ):
        try:
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            continue
    return {}


try:
    from .material_converter import resolve_texture_import_mapping
except ImportError:
    try:
        from material_converter import resolve_texture_import_mapping
    except ImportError:
        resolve_texture_import_mapping = None

# Import icons resource (relative / package / or by path for Maya 2026+ when submodule is file-loaded)
icons_rc = None
try:
    from . import icons_rc as _icons_rc  # type: ignore
    icons_rc = _icons_rc
except ImportError:
    try:
        from QuickMaterials import icons_rc as _icons_rc  # type: ignore
        icons_rc = _icons_rc
    except ImportError:
        try:
            import icons_rc as _icons_rc  # type: ignore
            icons_rc = _icons_rc
        except ImportError:
            # Fallback: load by path (Maya 2026 when this module is loaded via spec_from_file_location)
            import importlib.util
            _icons_dir = os.path.dirname(os.path.abspath(__file__))
            _icons_path = os.path.join(_icons_dir, "icons_rc.py")
            if os.path.isfile(_icons_path):
                _spec = importlib.util.spec_from_file_location("icons_rc", _icons_path)
                if _spec and _spec.loader:
                    icons_rc = importlib.util.module_from_spec(_spec)
                    _spec.loader.exec_module(icons_rc)
                    sys.modules["icons_rc"] = icons_rc
            if icons_rc is None:
                raise ImportError("icons_rc not found (tried . import, QuickMaterials, top-level, and %s)" % _icons_path)


# --------------------------------------------------------------------------------
# Global texture type definitions
# --------------------------------------------------------------------------------

STANDARD_TEXTURE_TYPES = [
    "baseColor",
    "roughness",
    "normal",
    "opacity",
    "metallic"
]

ADVANCED_TEXTURE_TYPES = [
    "emission",          # Raw (weight)
    "emissionClr",     # sRGB (color)
    "subsurface",        # Raw (weight)
    "subsurfaceClr",   # sRGB (color)
    "specular",          # Raw (weight)
    "specularClr",     # sRGB (color)
    "transmission",      # Raw (weight)
    "transmissionClr", # sRGB (color)
    "coat",              # Raw (weight)
    "coatRoughness",     # Raw (float)
    "displacement"       # Raw (special)
]

# All texture types in one combined list (standard first, then advanced)
ALL_TEXTURE_TYPES = STANDARD_TEXTURE_TYPES + ADVANCED_TEXTURE_TYPES


# Import rules per texture type: colorSpace, target attribute (for standardSurface),
# expected kind ("color" or "float"), and special flags.
TEXTURE_RULES = {
    # STANDARD
    "baseColor":        {"colorSpace": "sRGB", "attr": "baseColor",          "kind": "color"},
    "roughness":        {"colorSpace": "Raw",  "attr": "specularRoughness",  "kind": "float"},
    "normal":           {"colorSpace": "Raw",  "attr": "normalCamera",       "kind": "normal"},   # special
    "opacity":          {"colorSpace": "Raw",  "attr": "opacity",            "kind": "color"},
    "metallic":         {"colorSpace": "Raw",  "attr": "metalness",          "kind": "float"},

    # ADVANCED
    "emission":         {"colorSpace": "Raw",  "attr": "emission",           "kind": "float"},
    "emissionClr":    {"colorSpace": "sRGB", "attr": "emissionColor",      "kind": "color"},
    "subsurface":       {"colorSpace": "Raw",  "attr": "subsurface",         "kind": "float"},
    "subsurfaceClr":  {"colorSpace": "sRGB", "attr": "subsurfaceColor",    "kind": "color"},
    "specular":         {"colorSpace": "Raw",  "attr": "specular",           "kind": "float"},
    "specularClr":    {"colorSpace": "sRGB", "attr": "specularColor",      "kind": "color"},
    "transmission":     {"colorSpace": "Raw",  "attr": "transmission",       "kind": "float"},
    "transmissionClr":{"colorSpace": "sRGB", "attr": "transmissionColor",  "kind": "color"},
    "coat":             {"colorSpace": "Raw",  "attr": "coat",               "kind": "float"},
    "coatRoughness":    {"colorSpace": "Raw",  "attr": "coatRoughness",      "kind": "float"},
    "displacement":     {"colorSpace": "Raw",  "attr": None,                 "kind": "displacement"}  # special
}

# Bulk folder import (All Materials mode)
BULK_ALL_MATERIALS_LABEL = (
    "All Materials (Import all matching textures to all materials)"
)
BULK_FOLDER_SKIP_DIRS = frozenset({"old", "archive"})
BULK_FOLDER_MAX_DEPTH = 6
# Bulk material group headers + review dialog (orange for separation from cyan controls)
BULK_MATERIAL_HEADER_COLOR = "#ff9330"
# Shared accent for texture-attribute labels / combos (matches importer UDIM + combo styling)
UI_ACCENT_CYAN = "#00f7c8"


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

        self.texture_data = {}  # Keep for backward compatibility with old texture type rows
        
        # NEW: Unified texture selection data structure
        self.selected_textures = {
            "unassigned": [],  # List of texture info dicts
            "assigned": []     # List of texture info dicts
        }
        
        # NEW: Widget references for each texture entry
        self.texture_entry_widgets = {}  # Maps texture_path -> widget info dict

        # Minimum sizing + settings toggle state
        self._settings_toggle_initialized = False
        self._minimum_width_baseline = 700
        self._base_min_height = 330
        self._settings_frame_extra_height = 220
        # DEPRECATED: Textures folder default location functionality removed.
        # The file dialog remembers the last-used folder, making this redundant.
        # self._search_mode_object_names = {
        #     "maya_file": "textureSearchMayaFileCheckbox",
        #     "sourceimages": "textureSearchMayaSourceimagesCheckbox",
        #     "custom": "textureSearchCustomPathCheckbox",
        # }
        # self._search_mode_checkboxes = {}
        # self._search_mode_lock = False
        # self._current_search_mode = "maya_file"

        # Bulk folder scan (All Materials mode) — must exist before init_ui/setup_connections
        self._bulk_match_cache = {}
        self._bulk_packed_entries = []
        self._bulk_unmatched_entries = []
        self._bulk_folder_root = None
        self._bulk_type_counts = Counter()
        self._bulk_packed_file_count = 0
        self._combo_was_bulk_mode = False

        self.init_ui()

        self.use_udim = True

        # Remember the last directory a texture was imported from
        self.last_texture_dir = ""
        
        # Cache for UDIM counts per directory+pattern (so all textures from same path show same count)
        self._udim_count_cache = {}  # Key: (directory, base_pattern, ext) -> count

        self._import_warnings = []

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

        # Initialize scroll area
        scroll_area = self.ui_elements["texturesScrollArea"]
        scroll_area.setWidgetResizable(True)

        self.populate_material_combo_box()

        # DEPRECATED: Textures folder default location
        # self.auto_populate_search_folder()

        self.setup_scroll_area_ui()


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

        # Initialize dynamic minimum sizing based on visible sections
        self.refresh_minimum_size()
        self.resize(
            max(self.minimumWidth(), self._minimum_width_baseline),
            max(self.minimumHeight(), self._base_min_height)
        )

        self.setup_connections()
        self._combo_was_bulk_mode = self._is_all_materials_bulk_mode()
        self._update_select_textures_button_label()
        if self._is_all_materials_bulk_mode():
            self._populate_texture_selection_ui()

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
        # Advanced Textures Toggle Button (deprecated - no longer used)
        # if "showAdvTexturesButton" in self.ui_elements:
        #     button = self._get_widget("showAdvTexturesButton", QtWidgets.QPushButton)
        #     if button:
        #         button.clicked.connect(self.toggle_adv_textures)

        # OLD UI: Show Channels Buttons & "Set" Buttons (all texture types) - DISABLED
        # These buttons are from the old UI and are not needed with the unified texture selection UI
        # Keeping code commented out in case we need to restore old UI functionality
        # for texture_type in ALL_TEXTURE_TYPES:
        #     show_btn_name = f"{texture_type}ShowChannelsButton"
        #     if show_btn_name in self.ui_elements:
        #         btn = self.ui_elements[show_btn_name]
        #         try:
        #             btn.clicked.disconnect()
        #         except Exception:
        #             pass
        #         btn.clicked.connect(partial(self.toggle_channel_container, texture_type))
        #         print(f"[DEBUG] Connected {show_btn_name} to toggle_channel_container")
        #     else:
        #         print(f"[DEBUG] {show_btn_name} not found in ui_elements")
        #
        #     set_btn_name = f"{texture_type}SetButton"
        #     if set_btn_name in self.ui_elements:
        #         btn = self.ui_elements[set_btn_name]
        #         try:
        #             btn.clicked.disconnect()
        #         except Exception:
        #             pass
        #         btn.clicked.connect(partial(self.select_texture_file, texture_type))
        #         print(f"[DEBUG] Connected {set_btn_name} to select_texture_file")
        #     else:
        #         print(f"[DEBUG] {set_btn_name} not found in ui_elements")

        # Auto Set Button for Texture Types - REMOVED (functionality simplified)
        # for texture_type in ALL_TEXTURE_TYPES:
        #     auto_btn_name = f"{texture_type}AutoButton"
        #     auto_btn = self.ui_elements.get(auto_btn_name)
        #     if auto_btn:
        #         auto_btn.setVisible(False)  # Hide the button
        #         self._debug_print(f"Hid {auto_btn_name} (auto-set functionality removed)")
        #     else:
        #         self._debug_print(f"{auto_btn_name} not found")


        # Search Folder Set Button - REMOVED (functionality simplified)
        # if "searchFolderSetButton" in self.ui_elements:
        #     button = self.ui_elements["searchFolderSetButton"]
        #     button.setVisible(False)  # Hide the button


        # Texture Importer Settings Button toggle
        self._setup_settings_toggle_button()

        # Auto-Find-All Button - REMOVED (functionality simplified)
        # auto_all_btn = self.ui_elements.get("autoFindAllButton")
        # if auto_all_btn:
        #     auto_all_btn.setVisible(False)  # Hide the button

        # Select Textures For Import (multi-pick)
        select_btn = (self.ui_elements.get("selectTextureForImportButton")
                      or self.ui_elements.get("selectTexturesForImportButton"))
        if select_btn:
            try:
                select_btn.clicked.disconnect()
            except Exception:
                pass
            select_btn.clicked.connect(self.select_textures_for_import)  # <-- new
            print("[DEBUG] Connected SelectTexturesForImport button to select_textures_for_import")
        else:
            print("[DEBUG] SelectTexturesForImport button not found in ui_elements")


        # Import / Preview Import button  # <-- new
        import_btn = self._get_widget("importTexturesButton", QtWidgets.QPushButton)
        if import_btn and isValid(import_btn):
            try:
                import_btn.clicked.disconnect()
            except Exception:
                pass
            import_btn.clicked.connect(self._on_import_textures_clicked)
            self._debug_print("Connected importTexturesButton to _on_import_textures_clicked")
            self._update_import_button_label()

        # Clear All button  # <-- new
        clear_all_btn = self.ui_elements.get("clearAllButton")
        if clear_all_btn:
            try:
                clear_all_btn.clicked.disconnect()
            except Exception:
                pass
            clear_all_btn.clicked.connect(self.clear_all_textures)
            self._debug_print("Connected clearAllButton to clear_all_textures")




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

        # Help doc button
        help_btn = self.ui_elements.get("textureImporterHelpButton")
        if help_btn:
            try:
                help_btn.clicked.disconnect()
            except Exception:
                pass
            help_btn.clicked.connect(
                lambda: QuickMaterials.help_doc_viewer.show_help_doc("textureImporterHelpButton")
            )

        # Hook up Use UDIM checkbox (objectName must be 'useUdimCheckbox' in the .ui)
        use_udim_cb = self.ui_elements.get("useUdimCheckbox")  # <-- new
        if use_udim_cb:
            try:
                use_udim_cb.stateChanged.disconnect()
            except Exception:
                pass
            use_udim_cb.setChecked(True)  # default ON
            use_udim_cb.stateChanged.connect(self.on_use_udim_toggled)  # <-- new
            self._debug_print("Connected useUdimCheckbox to on_use_udim_toggled")
        else:
            self._debug_print("useUdimCheckbox not found in UI elements.")  # <-- new


        # Update import button label on material change  # <-- new
        mat_cb = self._get_widget("materialComboBox", QtWidgets.QComboBox)
        if mat_cb:
            try:
                mat_cb.currentIndexChanged.disconnect()
            except Exception:
                pass
            mat_cb.currentIndexChanged.connect(self._on_material_combo_changed)
            self._debug_print("Connected materialComboBox to _on_material_combo_changed")

        # DEPRECATED: Textures folder default location
        # self._init_search_mode_checkboxes()

        # set_btn = self.ui_elements.get("textureSearchCustomPathSetButton")
        # if set_btn and isValid(set_btn):
        #     try:
        #         set_btn.clicked.disconnect()
        #     except Exception:
        #         pass
        #     set_btn.clicked.connect(self._on_custom_path_set_button_clicked)

        self.connections_initialized = True  # Mark connections as initialized

    def _setup_settings_toggle_button(self):
        """Wire textureImporterSettingsButton to show/hide its frame and update sizing."""
        if getattr(self, "_settings_toggle_initialized", False):
            return

        settings_btn, settings_frame = self._get_settings_widgets()
        if not settings_btn or not settings_frame:
            return

        try:
            settings_btn.setCheckable(True)
        except Exception:
            pass

        settings_btn.blockSignals(True)
        settings_btn.setChecked(False)
        settings_btn.blockSignals(False)
        settings_frame.setVisible(False)
        settings_btn.toggled.connect(self._on_settings_frame_toggled)

        self._settings_toggle_initialized = True
        self.refresh_minimum_size()

    def _on_settings_frame_toggled(self, checked):
        _, settings_frame = self._get_settings_widgets()
        if settings_frame and isValid(settings_frame):
            settings_frame.setVisible(bool(checked))
        self.refresh_minimum_size()
        if checked:
            self._ensure_within_minimum_bounds()

    def refresh_minimum_size(self):
        """Adjust the tool's minimum size based on which sections are visible."""
        min_w = max(int(self._minimum_width_baseline), 300)
        min_h = int(self._base_min_height)

        _, settings_frame = self._get_settings_widgets()
        if settings_frame and isValid(settings_frame) and settings_frame.isVisible():
            hint = settings_frame.sizeHint().height()
            if hint <= 0:
                hint = settings_frame.minimumSizeHint().height()
            if hint <= 0:
                hint = self._settings_frame_extra_height
            min_h += max(int(self._settings_frame_extra_height), int(hint))

        self.setMinimumSize(min_w, min_h)

    def _ensure_within_minimum_bounds(self):
        """Resize the window if it's currently smaller than the enforced minimum."""
        min_size = self.minimumSize()
        target_w = max(self.width(), min_size.width())
        target_h = max(self.height(), min_size.height())
        try:
            self.resize(target_w, target_h)
        except Exception:
            pass

    def _get_settings_widgets(self):
        """Return (button, frame) for the settings toggle, refreshing stale refs."""
        btn = self.ui_elements.get("textureImporterSettingsButton")
        if not btn or not isValid(btn):
            btn = self.findChild(QtWidgets.QPushButton, "textureImporterSettingsButton")
            if btn:
                self.ui_elements["textureImporterSettingsButton"] = btn

        frame = self.ui_elements.get("textureImporterSettingsFrame")
        if not frame or not isValid(frame):
            frame = self.findChild(QtWidgets.QFrame, "textureImporterSettingsFrame")
            if frame:
                self.ui_elements["textureImporterSettingsFrame"] = frame

        return btn, frame

    # DEPRECATED: Textures folder default location functionality removed.
    # The file dialog remembers the last-used folder, making this redundant.
    #
    # def _on_custom_path_set_button_clicked(self):
    #     """Replicate legacy behavior for the custom-path Set button."""
    #     line_edit = self.ui_elements.get("textureSearchCustomPathLineEdit")
    #     if not line_edit or not isValid(line_edit):
    #         return
    #     current_path = line_edit.text().strip()
    #     if current_path:
    #         resolved_path = self._resolve_custom_path_keys(current_path)
    #         if resolved_path:
    #             if os.path.exists(resolved_path):
    #                 self._open_folder(resolved_path)
    #             else:
    #                 create_cb = self.ui_elements.get("createIfDoesntExistCheckbox")
    #                 should_create = bool(create_cb and isValid(create_cb) and create_cb.isChecked())
    #                 if should_create:
    #                     try:
    #                         os.makedirs(resolved_path, exist_ok=True)
    #                         self._open_folder(resolved_path)
    #                     except Exception as exc:
    #                         cmds.warning(f"Failed to create folder '{resolved_path}': {exc}")
    #                 else:
    #                     cmds.warning(
    #                         f"Folder does not exist: {resolved_path}\n"
    #                         "Enable 'Create if doesn't exist' to create it automatically."
    #                     )
    #         else:
    #             cmds.warning(f"Invalid path template: {current_path}")
    #     else:
    #         start_dir = cmds.workspace(q=True, rootDirectory=True) or ""
    #         folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Texture Folder", start_dir)
    #         if folder:
    #             line_edit.setText(folder)
    #             self._set_search_mode("custom")
    #
    # def _open_folder(self, path):
    #     """Open the given path in the OS file browser."""
    #     if os.name == 'nt':
    #         os.startfile(path)
    #     elif os.name == 'posix':
    #         try:
    #             if hasattr(os, "uname") and os.uname().sysname == 'Darwin':
    #                 os.system(f'open "{path}"')
    #             else:
    #                 os.system(f'xdg-open "{path}"')
    #         except Exception:
    #             pass
    #
    # def _resolve_custom_path_keys(self, path_template):
    #     """Resolve key substitution in custom path template."""
    #     if not path_template:
    #         return None
    #     try:
    #         scene_path = cmds.file(q=True, sn=True) or ""
    #         scene_dir = os.path.dirname(scene_path) if scene_path else ""
    #         project_path = cmds.workspace(q=True, rootDirectory=True) or ""
    #         project_dir = project_path.rstrip("/\\") if project_path else ""
    #         resolved = path_template
    #         resolved = resolved.replace("(scene)", scene_dir)
    #         resolved = resolved.replace("(project)", project_dir)
    #         resolved = os.path.normpath(resolved)
    #         return resolved
    #     except Exception as e:
    #         print(f"[DEBUG] Error resolving path template '{path_template}': {e}")
    #         return None

    # DEPRECATED: Textures folder default location — search mode checkboxes and
    # custom-path enable/disable logic removed.
    #
    # def _init_search_mode_checkboxes(self):
    #     """Ensure the three search-path checkboxes behave exclusively."""
    #     for mode in list(self._search_mode_object_names.keys()):
    #         cb = self._get_search_checkbox(mode)
    #         if not cb:
    #             continue
    #         try:
    #             cb.toggled.disconnect()
    #         except Exception:
    #             pass
    #         cb.toggled.connect(lambda checked, m=mode: self._on_search_mode_checkbox_toggled(m, checked))
    #     initial_mode = None
    #     for mode in self._search_mode_object_names.keys():
    #         cb = self._get_search_checkbox(mode)
    #         if cb and cb.isChecked():
    #             initial_mode = mode
    #             break
    #     if not initial_mode:
    #         initial_mode = "maya_file"
    #     self._set_search_mode(initial_mode, force=True)
    #
    # def _on_search_mode_checkbox_toggled(self, mode, checked):
    #     if self._search_mode_lock:
    #         return
    #     if checked:
    #         self._set_search_mode(mode)
    #         return
    #     any_other_checked = any(
    #         cb.isChecked()
    #         for m, cb in self._search_mode_checkboxes.items()
    #         if m != mode and cb and isValid(cb)
    #     )
    #     if not any_other_checked:
    #         self._set_search_mode(mode)
    #
    # def _set_search_mode(self, mode, force=False):
    #     target_cb = self._get_search_checkbox(mode)
    #     if not target_cb:
    #         return
    #     if not force and self._current_search_mode == mode:
    #         self._update_custom_path_widgets()
    #         return
    #     self._search_mode_lock = True
    #     for m in self._search_mode_object_names.keys():
    #         checkbox = self._get_search_checkbox(m)
    #         if checkbox:
    #             checkbox.setChecked(m == mode)
    #     self._search_mode_lock = False
    #     self._current_search_mode = mode
    #     self._update_custom_path_widgets()
    #
    # def _get_search_checkbox(self, mode):
    #     """Return the checkbox widget for a search mode, refreshing stale refs."""
    #     obj_name = self._search_mode_object_names.get(mode)
    #     if not obj_name:
    #         return None
    #     cb = self._search_mode_checkboxes.get(mode)
    #     if not cb or not isValid(cb):
    #         cb = self.ui_elements.get(obj_name)
    #         if not cb or not isValid(cb):
    #             cb = self.findChild(QtWidgets.QCheckBox, obj_name)
    #             if cb:
    #                 self.ui_elements[obj_name] = cb
    #         if cb and isValid(cb):
    #             self._search_mode_checkboxes[mode] = cb
    #         else:
    #             cb = None
    #     return cb
    #
    # def _update_custom_path_widgets(self):
    #     """Enable/disable custom-path widgets based on current search mode."""
    #     custom_on = (self._current_search_mode == "custom")
    #     widget_names = [
    #         "textureSearchCustomPathLineEdit",
    #         "textureSearchCustomPathSetButton",
    #         "customSearchFolderPathLabel",
    #         "createIfDoesntExistCheckbox",
    #     ]
    #     for widget_name in widget_names:
    #         w = self.ui_elements.get(widget_name)
    #         if not w or not isValid(w):
    #             w = self.findChild(QtWidgets.QWidget, widget_name)
    #             if w:
    #                 self.ui_elements[widget_name] = w
    #         if w and isValid(w):
    #             w.setEnabled(custom_on)
    #     if not custom_on:
    #         line_edit = self.ui_elements.get("textureSearchCustomPathLineEdit")
    #         if line_edit and isValid(line_edit):
    #             line_edit.clearFocus()

    def _load_settings(self):
        """Return dict from settings/texture_importer_settings.json or {}."""
        path = os.path.join(os.path.dirname(__file__), "settings", "texture_importer_settings.json")
        try:
            with open(path, "r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            self._debug_print(f"[SETTINGS] Failed to read: {e}")
        return {}

    # --- keyword map & file search helpers ---

    def _load_keyword_map(self):
        """
        Load per-texture-type keyword lists from settings/texture_search_names.json (user),
        else legacy Settings/ path, else settings/texture_search_names_default.json.
        Falls back to {type:[type]}. Also returns packed textures if present.
        """
        data = load_texture_search_names_raw_dict()

        # Normalize: ensure every type exists and is a list of strings
        norm = {}
        for ttype in ALL_TEXTURE_TYPES:
            vals = data.get(ttype, [])
            if isinstance(vals, list) and vals:
                norm[ttype] = [str(v).strip() for v in vals if str(v).strip()]
            else:
                norm[ttype] = [ttype]  # minimal fallback to its own name
        
        # Also return packed textures if present
        packed_textures = data.get("packedTextures", [])
        return norm, packed_textures

    def _build_type_tokens(self, texture_type, material_name=None, kw_map=None):
        """
        Build tokens used to score filenames for a given texture type.
        NOTE: We will treat the type's JSON keywords as REQUIRED (see _score_filename).
        """
        tokens = set()
        kw_map = kw_map or {}
        for k in kw_map.get(texture_type, []):
            tokens.add(k.lower())

        # Keep the raw type name available too (acts like a keyword if user included it)
        tokens.add(texture_type.lower())

        if (
            material_name
            and material_name.strip()
            and material_name != BULK_ALL_MATERIALS_LABEL
        ):
            m = material_name.strip()
            tokens.add(m.lower())
            # Common variation: drop common prefixes
            for prefix in ("m_", "mat_", "mtl_"):
                if m.lower().startswith(prefix):
                    tokens.add(m[len(prefix):].lower())
        return list(tokens)

    def _type_required_keywords(self, texture_type, kw_map):
        """
        JSON-provided keywords for this type (lowercased), filtered.
        Blanket allow single-letter entries (e.g., 'e', 'm') if they’re explicitly
        present in texture_search_names.json. Separator-aware matching elsewhere
        keeps these from firing on substrings.
        """
        vals = kw_map.get(texture_type, []) or []
        out = []
        for v in vals:
            s = str(v).strip().lower()
            if len(s) >= 1:  # allow single letters when specified
                out.append(s)
        return out

    def _other_types_keywords(self, texture_type, kw_map):
        """
        Map of other_type -> [keywords...] (lowercased) used to penalize collisions.
        """
        others = {}
        for t in ALL_TEXTURE_TYPES:
            if t == texture_type:
                continue
            vals = kw_map.get(t, []) or []
            if vals:
                others[t] = [str(v).lower() for v in vals if str(v).strip()]
        return others

    def _has_boundary_token(self, name_lc, token_lc):
        """
        Require explicit separators around the token:
        (^|[._-])token([._-]|$)
        """
        if not token_lc:
            return False
        try:
            pattern = rf"(^|[._-]){re.escape(token_lc)}([._-]|$)"
            return re.search(pattern, name_lc) is not None
        except Exception:
            return False

    def _has_channel_tag(self, name_lc, letter):
        """
        Return True if a single-letter tag (e.g., 'm', 'e', 'r', 'g', 'b', 'a') appears
        as its own token NOT at the very start of the basename and separated by [._-].
        Accepts endings like ..._M.1001.tif or ..._E_extra.png (separator-aware).
        """
        if not letter:
            return False
        try:
            # Not at string start: (?<!^)
            # Own token with separators and optional UDIM immediately after:
            # (^|[._-]) l ( $ | [._-] | 10\d{2} followed by . or end )
            pat = rf"(?<!^)(?:^|[._-]){re.escape(letter)}(?=(?:$|[._-]|10\d{{2}}(?:[._-]|$)))"
            return re.search(pat, name_lc) is not None
        except Exception:
            return False


    def _strip_material_from_name(self, name_lc, material_name):
        """
        Return a version of name_lc with the material-name segment removed (separator-aware),
        so required-keyword hits inside the material segment don't count.
        """
        try:
            if not material_name:
                return name_lc
            m = material_name.strip().lower()
            if not m:
                return name_lc

            variants = {m}
            for prefix in ("m_", "mat_", "mtl_"):
                if m.startswith(prefix):
                    variants.add(m[len(prefix):])

            parts = [re.escape(v) for v in variants if v]
            if not parts:
                return name_lc

            # Replace any boundary-wrapped material variant with a single underscore
            pat = rf"(^|[._-])(?:{'|'.join(parts)})($|[._-])"
            return re.sub(pat, "_", name_lc)
        except Exception:
            return name_lc


    def _required_kw_bonus(self, kw):
        """
        Specificity bonus for a required keyword:
        - Single letter: 0 bonus (still allowed to pass gate)
        - Longer terms: bonus grows with length, capped to avoid runaway scores
        """
        s = (kw or "").strip().lower()
        if len(s) <= 1:
            return 0
        # e.g., 'subsurfaceclr' (13) gets +10 cap; 'subsurface' (10) gets +9
        return min(10, len(s) - 1)

    def _iter_candidate_files(self, start_folder, recurse=True, max_depth=5, name_predicate=None):  # <-- updated
        """
        Yield candidate image files under start_folder (bounded recursion).
        Skips .tx (compiled textures) to avoid false positives.
        If name_predicate is provided, only yield files where name_predicate(filename) is True.
        """
        if not os.path.isdir(start_folder):
            return
        exts = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".tga", ".bmp"}  # no .tx
        base_depth = start_folder.rstrip(os.sep).count(os.sep)
        try:
            for root, dirs, files in os.walk(start_folder):
                if not recurse:
                    dirs.clear()
                else:
                    depth = root.count(os.sep) - base_depth
                    if depth > max_depth:
                        dirs.clear()
                        continue
                for filename in files:
                    _, ext = os.path.splitext(filename)
                    if ext.lower() not in exts:
                        continue
                    if name_predicate and not name_predicate(filename):  # <-- new
                        continue
                    yield os.path.join(root, filename)
        except Exception as e:
            self._debug_print(f"[Scan] Error scanning {start_folder}: {e}")

    def _build_material_name_predicate(self, material_name):
        """
        Return a predicate(filename) -> bool that accepts only names containing the material
        with separators (^|[._-])mat([._-]|$). Falls back to substring if material is empty.
        """
        if not material_name:
            return None
        m = material_name.strip()
        m_lc = m.lower()
        # also accept common prefix-stripped variants
        variants = {m_lc}
        for prefix in ("m_", "mat_", "mtl_"):
            if m_lc.startswith(prefix):
                variants.add(m_lc[len(prefix):])
        patterns = [re.compile(rf"(^|[._-]){re.escape(v)}([._-]|$)") for v in variants if v]
        def _pred(fname):
            n = os.path.basename(fname).lower()
            for pat in patterns:
                if pat.search(n):
                    return True
            return False
        return _pred

    def _on_material_combo_changed(self):
        prev_bulk = getattr(self, "_combo_was_bulk_mode", False)
        cur_bulk = self._is_all_materials_bulk_mode()
        self._combo_was_bulk_mode = cur_bulk
        if prev_bulk and not cur_bulk:
            self._clear_bulk_folder_state()
            self.selected_textures = {"unassigned": [], "assigned": []}
            self.texture_entry_widgets.clear()
        elif not prev_bulk and cur_bulk:
            self.selected_textures = {"unassigned": [], "assigned": []}
            self.texture_entry_widgets.clear()
            self.texture_data.clear()
        self._update_select_textures_button_label()
        self._populate_texture_selection_ui()

    def _get_import_button_base_text(self):
        cb = self._get_widget("materialComboBox", QtWidgets.QComboBox)
        is_all = False
        if cb and isValid(cb):
            try:
                is_all = self._is_all_materials_bulk_mode(str(cb.currentText()))
            except RuntimeError:
                self._debug_print("[ImportBtn] currentText() failed (combo deleted).")
        return "Preview Import Textures" if is_all else "Import Textures"

    def _count_importable_textures(self):
        if self._is_all_materials_bulk_mode():
            if not self._bulk_folder_root:
                return 0
            n = 0
            for status in ("unassigned", "assigned"):
                for texture_info in self.selected_textures.get(status, []):
                    if not texture_info.get("target_material"):
                        continue
                    if any(
                        a.get("attribute") and a.get("attribute") != "skip"
                        for a in texture_info.get("assignments", [])
                    ):
                        n += 1
            return n
        if not hasattr(self, "selected_textures"):
            return 0
        count = 0
        for status in ["unassigned", "assigned"]:
            for texture_info in self.selected_textures.get(status, []):
                assignments = texture_info.get("assignments", [])
                if any(a.get("attribute") and a.get("attribute") != "skip" for a in assignments):
                    count += 1
        return count

    def _update_import_button_label(self):
        btn = self._get_widget("importTexturesButton", QtWidgets.QPushButton)
        if not btn or not isValid(btn):
            self._debug_print("[ImportBtn] Button not available for label update.")
            return
        base_text = self._get_import_button_base_text()
        count = self._count_importable_textures()
        try:
            btn.setText(f"{base_text} ({count})")
            self._debug_print(f"[ImportBtn] Label -> {btn.text()}")
        except RuntimeError:
            self._debug_print("[ImportBtn] Failed to update button text (widget deleted).")

    def _on_import_textures_clicked(self):
        cb = self._get_widget("materialComboBox", QtWidgets.QComboBox)
        try:
            if not cb or not isValid(cb):
                self._debug_print("[ImportBtn] materialComboBox not available (deleted?).")
                return
            current = str(cb.currentText())
        except RuntimeError:
            self._debug_print("[ImportBtn] materialComboBox access raised RuntimeError (deleted).")
            return

        if current == BULK_ALL_MATERIALS_LABEL:
            if not self._bulk_folder_root:
                cmds.warning("Select a textures folder first.")
                return
            self._sync_all_texture_entries()
            if self._count_importable_textures() == 0:
                cmds.warning(
                    "No matching textures to import. Check filenames include material names, "
                    "or restore assignments that are set to skip import."
                )
                return
            dlg = BulkImportReviewDialog(self)
            result = dlg.exec_()
            if result == QtWidgets.QDialog.Accepted:
                self._debug_print("[PreviewImport] Accepted -> performing bulk import")
                self._perform_bulk_import()
            else:
                self._debug_print("[PreviewImport] Cancelled")
        else:
            self._debug_print(f"[SingleImport] Importing textures for '{current}'")
            self._perform_single_material_import(current)

    def _perform_single_material_import(self, material_name):
        """
        Import textures from self.selected_textures structure for `material_name`.
        Reads attribute selections, channels, and colorspaces from UI.
        """
        count = 0
        self._import_warnings = []

        # Sync UI state to data structure first
        for texture_path in list(self.texture_entry_widgets.keys()):
            self._sync_texture_entry_to_data(texture_path)
        
        # Process both unassigned and assigned textures
        for status in ["unassigned", "assigned"]:
            for texture_info in self.selected_textures[status]:
                texture_path = texture_info.get("path")
                if not texture_path or not os.path.isfile(texture_path):
                    continue
                
                assignments = texture_info.get("assignments", [])
                if not assignments:
                    continue
                
                # Filter out "skip" assignments
                valid_assignments = [a for a in assignments if a.get("attribute") and a.get("attribute") != "skip"]
                
                if not valid_assignments:
                    continue
                
                # If single assignment, use _import_one_type
                if len(valid_assignments) == 1:
                    assignment = valid_assignments[0]
                    texture_type = assignment.get("attribute")
                    channel = assignment.get("channel")
                    colorspace = assignment.get("colorspace", "default")
                    
                    # Import with channel and colorspace
                    self._import_one_type_with_channel(material_name, texture_type, texture_path, channel, colorspace, texture_info)
                    count += 1
                else:
                    # Multiple assignments (packed texture)
                    self._import_packed_texture(texture_path, valid_assignments)
                    count += 1
        
        # Also check old texture_data for backward compatibility
        for ttype in ALL_TEXTURE_TYPES:
            data = self.texture_data.get(ttype)
            if not data:
                continue
            path = data.get("path")
            if not path or not os.path.isfile(path):
                continue
            self._import_one_type(material_name, ttype, path)
            count += 1
        
        self._debug_print(f"[Import] Done single-material import: {material_name} ({count} textures)")
        self._flush_import_warnings()

    def _import_one_type_with_channel(self, material, texture_type, file_path, channel, colorspace, texture_info):
        """
        Import a single texture type with specific channel and colorspace.
        Similar to _import_one_type but accepts channel and colorspace parameters.
        """
        rules = TEXTURE_RULES.get(texture_type, {})
        kind = rules.get("kind")
        std_attr = rules.get("attr")

        mapping = self._resolve_texture_mapping(material, texture_type)
        if mapping.get("warning"):
            self._record_import_warning(mapping["warning"])
        if mapping.get("skip"):
            return

        target_attr = mapping.get("target_attr")
        opacity_mode = mapping.get("opacity_mode")
        normal_utility = mapping.get("normal_utility") or "aiNormalMap"

        # Displacement (no surface shader attribute)
        if kind == "displacement" or texture_type == "displacement":
            use_udim = self.use_udim and texture_info.get("udim_count", 0) > 1
            file_node, path_to_set = self._ensure_file_node(
                material, texture_type, file_path, colorspace, use_udim
            )
            disp_node, sg = self._ensure_displacement_network(material)
            ch = channel
            if ch:
                cl = ch.lower()
                src = (
                    f"{file_node}.outAlpha"
                    if cl == "a"
                    else f"{file_node}.outColorR"
                    if cl == "r"
                    else f"{file_node}.outColorG"
                    if cl == "g"
                    else f"{file_node}.outColorB"
                )
            else:
                default_ch = self._default_channel_for_type(texture_type)
                src = (
                    f"{file_node}.outAlpha"
                    if default_ch == "A"
                    else f"{file_node}.outColorR"
                    if default_ch == "R"
                    else f"{file_node}.outColorG"
                    if default_ch == "G"
                    else f"{file_node}.outColorB"
                )
            try:
                incoming = cmds.listConnections(f"{disp_node}.displacement", plugs=True) or []
                for plug in incoming:
                    try:
                        cmds.disconnectAttr(plug, f"{disp_node}.displacement")
                    except Exception:
                        pass
                cmds.connectAttr(src, f"{disp_node}.displacement", force=True)
            except Exception:
                pass
            self._debug_print(
                f"[Import] displacement: {file_node} -> {disp_node}.displacement (SG={sg})"
            )
            return

        # Get UDIM info from texture_info
        use_udim = self.use_udim and texture_info.get("udim_count", 0) > 1
        udim_pattern = texture_info.get("udim_pattern")
        
        # Create file node
        file_node, path_to_set = self._ensure_file_node(material, texture_type, file_path, colorspace, use_udim)

        try:
            if kind in ("float", "displacement") or texture_type == "opacity":
                if cmds.attributeQuery("alphaIsLuminance", node=file_node, exists=True):
                    cmds.setAttr("%s.alphaIsLuminance" % file_node, 1)
            if kind == "normal":
                if cmds.attributeQuery("alphaIsLuminance", node=file_node, exists=True):
                    cmds.setAttr("%s.alphaIsLuminance" % file_node, 0)
        except Exception:
            pass
        
        attr = target_attr
        if not attr:
            return
        
        # Determine source channel
        if channel:
            if channel.lower() == "a":
                src = f"{file_node}.outAlpha"
            elif channel.lower() == "r":
                src = f"{file_node}.outColorR"
            elif channel.lower() == "g":
                src = f"{file_node}.outColorG"
            elif channel.lower() == "b":
                src = f"{file_node}.outColorB"
            else:
                src = f"{file_node}.outColorR"
        else:
            # Use default channel for this type
            default_ch = self._default_channel_for_type(texture_type)
            if default_ch == "A":
                src = f"{file_node}.outAlpha"
            elif default_ch == "R":
                src = f"{file_node}.outColorR"
            elif default_ch == "G":
                src = f"{file_node}.outColorG"
            elif default_ch == "B":
                src = f"{file_node}.outColorB"
            else:
                src = f"{file_node}.outColor"
        
        # Connect based on kind
        if kind == "normal":
            nn = self._ensure_normal_map_for_mapping(material, normal_utility)
            self._connect_file_to_normal_utility(file_node, nn, normal_utility)
            self._debug_print(
                f"[Import] {file_node}.outColor -> {nn} -> {material}.normalCamera "
                f"(channel={channel or 'default'}, colorspace={colorspace})"
            )
            return

        if kind == "color" and opacity_mode == "reverse_to_transparency":
            ch_upper = channel.upper() if channel else None
            cp = ch_upper if ch_upper in ("A", "R", "G", "B") else (
                "A" if texture_type == "opacity" else None
            )
            self._connect_file_to_reversed_transparency(file_node, material, cp)
            self._debug_print(
                f"[Import] {file_node} -> reverse -> {material}.transparency "
                f"(channel={channel or 'default'}, colorspace={colorspace})"
            )
            return

        if kind == "color":
            # If src is outColor (full color), connect directly; otherwise replicate scalar to RGB
            if ".outColor" in src and src.endswith(".outColor"):
                try:
                    cmds.connectAttr(src, f"{material}.{attr}", force=True)
                except Exception:
                    # fallback to scalar replicate on failure
                    self._connect_scalar_to_color(f"{file_node}.outColorR", material, attr)
            else:
                self._connect_scalar_to_color(src, material, attr)
        elif kind == "float":
            self._connect_float_source_to_attr(src, material, attr)
        
        self._debug_print(f"[Import] {file_node}.{src} -> {material}.{attr} (channel={channel or 'default'}, colorspace={colorspace})")

    def _sync_all_texture_entries(self):
        """Push all open texture rows from the UI into selected_textures (bulk + single)."""
        for texture_path in list(self.texture_entry_widgets.keys()):
            self._sync_texture_entry_to_data(texture_path)

    def _perform_bulk_import(self):
        """
        Bulk import from the main scroll area: each texture_info with target_material,
        honoring assignments, channels, colorspace, and skip rows (same as single-material flow).
        """
        self._import_warnings = []
        self._sync_all_texture_entries()
        for status in ("unassigned", "assigned"):
            for texture_info in self.selected_textures.get(status, []):
                mat = texture_info.get("target_material")
                if not mat:
                    continue
                texture_path = texture_info.get("path")
                if not texture_path:
                    continue
                if not os.path.isfile(texture_path) and "<UDIM>" not in str(texture_path):
                    continue
                assignments = texture_info.get("assignments", [])
                valid_assignments = [
                    a for a in assignments
                    if a.get("attribute") and a.get("attribute") != "skip"
                ]
                if not valid_assignments:
                    continue
                if len(valid_assignments) == 1:
                    a0 = valid_assignments[0]
                    self._import_one_type_with_channel(
                        mat,
                        a0.get("attribute"),
                        texture_path,
                        a0.get("channel"),
                        a0.get("colorspace", "default"),
                        texture_info,
                    )
                else:
                    self._import_packed_texture(
                        texture_path, valid_assignments, material=mat
                    )
        self._flush_import_warnings()




    def _score_filename(self, filename, tokens, required_keywords, other_types_kw, material_name=None):
        """
        Scoring with strict separators:
          - REQUIRED: at least one required keyword must match with separators
                      (^|[._-])token([._-]|$) *outside* the material-name segment.
            • Single-letter required keywords are allowed but must appear as channel-like tags
              (not in the first token) via _has_channel_tag().
            • Among all matched required keywords, we only credit the most specific one
              (longest), adding a length-based bonus so 'subsurfaceclr' outranks 'subsurface'.
          - POSITIVE: +3 per required keyword hit (+2 boundary bonus) — applied once using the
                      most specific required keyword; +2 for other tokens with +1 boundary bonus.
          - NEGATIVE: -3 per collision with *other* types' keywords ( -1 extra if boundary hit ).
          - BONUS:    +1 if UDIM pattern present.
        Always returns a (score:int, passed:bool) tuple.
        """
        try:
            name = os.path.basename(filename).lower()
            score = 0
            passed_required = False

            # Use stripped name only for required-keyword checks
            name_for_required = self._strip_material_from_name(name, material_name)

            # Guard: treat None as empty lists
            required_keywords = required_keywords or []
            tokens            = tokens or []
            other_types_kw    = other_types_kw or {}

            # REQUIRED gate (separator-aware, outside material segment) with specificity
            best_required_len   = -1
            best_required_bonus = 0
            for rk in required_keywords:
                rk = (rk or "").strip().lower()
                if not rk:
                    continue
                matched = False
                if len(rk) == 1:
                    matched = self._has_channel_tag(name_for_required, rk)
                else:
                    matched = self._has_boundary_token(name_for_required, rk)

                if matched:
                    passed_required = True
                    # Keep only the most specific (longest) required keyword for scoring
                    if len(rk) > best_required_len:
                        best_required_len   = len(rk)
                        best_required_bonus = self._required_kw_bonus(rk)

            if not passed_required:
                return (-9999, False)

            # Base credit for passing the required gate, plus specificity bonus (once)
            score += 5 + best_required_bonus

            # POSITIVE: other tokens (material/raw type etc.)
            for t in tokens:
                t = (t or "").strip().lower()
                if not t or t in required_keywords:
                    continue
                # Only award if the token appears as a boundary-separated term.
                # This avoids accidental substring bumps (e.g., 'ss' inside 'sss').
                if self._has_boundary_token(name, t):
                    score += 3  # fold the old (2 + boundary +1) into a single clean award

            # NEGATIVE: collisions with other types’ keywords
            # Apply ONLY to length>=2 and ONLY when boundary-matched.
            # This prevents single-letter noise (e.g., 's') from hammering 'sss'.
            for _, kw_list in (other_types_kw or {}).items():
                for k in (kw_list or []):
                    k = (k or "").strip().lower()
                    if not k or len(k) < 2:
                        continue
                    if self._has_boundary_token(name, k):
                        score -= 4  # equivalent to old (-3 -1) but only when truly boundary-matched


            # BONUS
            if re.search(r"10\d{2}", name):
                score += 1

            return (score, True)

        except Exception as e:
            self._debug_print(f"[Score] Error scoring '{filename}': {e}")
            return (-9999, False)


    # -------- classify a single file into a texture type --------
    def _classify_texture_type_for_file(self, file_path, kw_map, material_name):
        """
        Return (best_type, best_score) if the file matches any type with required-keyword rule,
        otherwise (None, None).
        """
        name = os.path.basename(file_path)
        tokens_cache = {}
        best_type, best_score = None, -9999

        for ttype in ALL_TEXTURE_TYPES:
            tokens = tokens_cache.get(ttype)
            if tokens is None:
                tokens = self._build_type_tokens(ttype, material_name, kw_map)
                tokens_cache[ttype] = tokens
            required = self._type_required_keywords(ttype, kw_map)
            other_map = self._other_types_keywords(ttype, kw_map)
            # pass material_name so required checks ignore matches inside the material segment
            score, passed = self._score_filename(name, tokens, required, other_map, material_name)
            if passed and score > best_score:
                best_type, best_score = ttype, score


        return (best_type, best_score if best_type else None)

    def _check_packed_textures(self, file_path, packed_textures, material_name=None):
        """
        Check if a file matches any packed texture entry.
        Returns a list of assignments: [{"attribute": "roughness", "channel": "b"}, ...]
        or empty list if no match.
        
        IMPORTANT: This should NOT match normal maps or other specific texture types
        that have their own classification. Normal maps should be classified as "normal" first.
        """
        if not packed_textures:
            return []
        
        name = os.path.basename(file_path).lower()
        
        # First check if this is a normal map - if so, don't treat as packed texture
        # Normal maps should be classified as "normal" type, not packed
        normal_indicators = ["normal", "nrm", "nrml", "norm"]
        for indicator in normal_indicators:
            if self._has_boundary_token(name, indicator):
                return []  # Let it be classified as normal map instead
        
        matches = []
        
        for packed_entry in packed_textures:
            search_names = packed_entry.get("searchNames", "")
            if not search_names:
                continue
            
            # Check if any search name matches the filename
            keywords = [kw.strip().lower() for kw in search_names.split(",") if kw.strip()]
            matched = False
            for keyword in keywords:
                # Use similar matching logic as _score_filename but simpler
                if self._has_boundary_token(name, keyword) or keyword in name:
                    matched = True
                    break
            
            if matched:
                # Return all assignments for this packed texture
                assignments = packed_entry.get("assignments", [])
                if assignments:
                    matches.extend(assignments)
        
        return matches

    # -------- helper to pick a "representative" UDIM (prefer 1001, else lowest) --------
    def _prefer_representative_udim(self, paths):
        """
        Given a list of file paths that belong to the same texture type, return the best single path
        to feed into process_selected_texture() for display/UDIM counting.
        """
        if not paths:
            return None
        # prefer tile 1001
        for p in paths:
            if re.search(r"1001(?!\d)", os.path.basename(p)):
                return p
        # else lowest tile number
        tiles = []
        for p in paths:
            m = re.search(r"10(\d{2})", os.path.basename(p))
            if m:
                try:
                    tiles.append((int(m.group(0)), p))
                except Exception:
                    pass
        if tiles:
            tiles.sort(key=lambda x: x[0])
            return tiles[0][1]
        # fallback: first
        return paths[0]

    # -------- handler for the UI button (multi-file import/classify) --------
    def select_textures_for_import(self):
        """
        Open a multi-file dialog, classify each selection into a texture type using the
        token/required-keyword system, then populate the unified texture selection UI.
        This ALWAYS considers both standard and advanced types (visibility ignored).
        In All Materials bulk mode, selects one folder and scans it recursively.
        """
        if self._is_all_materials_bulk_mode():
            self._select_folder_for_bulk_import()
            return

        options = QtWidgets.QFileDialog.Options()
        start_dir = self.search_folder_path if self.search_folder_path else ""
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Select Textures For Import",
            start_dir,
            "Image Files (*.png *.jpg *.jpeg *.tif *.tiff *.exr *.tga *.bmp)"
        )
        if not files:
            return

        # Load keyword map & get current material name (safe)
        kw_map, packed_textures = self._load_keyword_map()
        mat_combo = self._get_widget("materialComboBox", QtWidgets.QComboBox)
        material = mat_combo.currentText() if mat_combo else ""

        # Find already-connected textures
        connected_textures = self._find_connected_textures()

        # Don't clear previous selections - add to existing (or skip if already exists)
        # Initialize if doesn't exist
        if not hasattr(self, 'selected_textures') or not self.selected_textures:
            self.selected_textures = {"unassigned": [], "assigned": []}
        
        # Get existing texture paths to avoid duplicates
        existing_paths = set()
        for status in ["unassigned", "assigned"]:
            for tex_info in self.selected_textures[status]:
                existing_paths.add(tex_info.get("path"))

        # Group UDIM tiles together - collect all files and group by base name
        udim_groups = {}  # base_name_without_udim -> [paths]
        regular_files = []  # files without UDIM pattern

        for path in files:
            file_name = os.path.basename(path)
            file_base, file_ext = os.path.splitext(file_name)
            
            # Check for UDIM pattern
            udim_pattern = self.detect_udim_pattern(file_base)
            if udim_pattern and self.use_udim:
                # Remove UDIM number to get base name
                udim_regex = re.compile(udim_pattern)
                base_without_udim = udim_regex.sub("", file_base)
                key = f"{base_without_udim}{file_ext}"
                if key not in udim_groups:
                    udim_groups[key] = []
                udim_groups[key].append(path)
            else:
                regular_files.append(path)
        
        # Process UDIM groups - use representative tile (prefer 1001)
        for base_key, tile_paths in udim_groups.items():
            # Pick representative tile (prefer 1001)
            rep_path = self._prefer_representative_udim(tile_paths)
            if not rep_path:
                continue
            
            # Skip if representative path is already in selection
            if rep_path in existing_paths:
                self._debug_print(f"[SelectImport] '{os.path.basename(rep_path)}' -> already selected, skipping")
                continue
            
            # Count all tiles for this texture
            file_dir, file_name = os.path.split(rep_path)
            file_base, file_ext = os.path.splitext(file_name)
            udim_pattern = self.detect_udim_pattern(file_base)
            udim_count = len(tile_paths) if udim_pattern else 0
            
            # Classify the representative tile
            # IMPORTANT: Check packed textures FIRST (even if already connected)
            # Packed textures should show all their assignments, not just the first connected one
            packed_matches = self._check_packed_textures(rep_path, packed_textures, material)
            if packed_matches:
                # Packed texture - use packed assignments (override connected texture data if exists)
                assignments = []
                for match in packed_matches:
                    attr = match.get("attribute")
                    channel = match.get("channel")
                    colorspace = TEXTURE_RULES.get(attr, {}).get("colorSpace", "default") if attr else "default"
                    assignments.append({"attribute": attr, "channel": channel, "colorspace": colorspace})
                    self._debug_print(f"[SelectImport] Packed match: attribute={attr}, channel={channel}, colorspace={colorspace}")
                
                texture_info = self._build_texture_info(rep_path, {
                    "status": "packed",
                    "assignments": assignments
                })
                # Don't overwrite udim_count - _build_texture_info already counted all tiles correctly
                # Use the count from texture_info which was calculated by scanning the directory
                actual_udim_count = texture_info.get("udim_count", 0)
                self.selected_textures["assigned"].append(texture_info)
                self._debug_print(f"[SelectImport] '{os.path.basename(rep_path)}' -> packed texture ({len(packed_matches)} assignments, {actual_udim_count} tiles)")
                self._debug_print(f"[SelectImport] Final assignments: {assignments}")
                continue
            
            # Check if already connected (only if not a packed texture)
            if rep_path in connected_textures:
                texture_info = self._build_texture_info(rep_path, connected_textures[rep_path])
                # Don't overwrite udim_count - _build_texture_info already counted all tiles correctly
                actual_udim_count = texture_info.get("udim_count", 0)
                self.selected_textures["assigned"].append(texture_info)
                self._debug_print(f"[SelectImport] '{os.path.basename(rep_path)}' -> already connected ({actual_udim_count} tiles)")
                continue
            
            # First check regular texture types (BEFORE packed textures to avoid normal maps matching packed)
            ttype, score = self._classify_texture_type_for_file(rep_path, kw_map, material)
            if ttype:
                # Matched texture - add to assigned section
                texture_info = self._build_texture_info(rep_path, {
                    "status": "matched",
                    "assignments": [{"attribute": ttype, "channel": None, "colorspace": TEXTURE_RULES.get(ttype, {}).get("colorSpace", "default")}]
                })
                # Don't overwrite udim_count - _build_texture_info already counted all tiles correctly
                actual_udim_count = texture_info.get("udim_count", 0)
                self.selected_textures["assigned"].append(texture_info)
                self._debug_print(f"[SelectImport] '{os.path.basename(rep_path)}' -> {ttype} (score={score}, {actual_udim_count} tiles)")
                continue
            if packed_matches:
                # Packed texture - add to assigned section with multiple assignments
                assignments = []
                for match in packed_matches:
                    attr = match.get("attribute")
                    channel = match.get("channel")
                    colorspace = TEXTURE_RULES.get(attr, {}).get("colorSpace", "default") if attr else "default"
                    assignments.append({"attribute": attr, "channel": channel, "colorspace": colorspace})
                    self._debug_print(f"[SelectImport] Packed match: attribute={attr}, channel={channel}, colorspace={colorspace}")
                
                texture_info = self._build_texture_info(rep_path, {
                    "status": "packed",
                    "assignments": assignments
                })
                texture_info["udim_count"] = udim_count
                self.selected_textures["assigned"].append(texture_info)
                self._debug_print(f"[SelectImport] '{os.path.basename(rep_path)}' -> packed texture ({len(packed_matches)} assignments, {udim_count} tiles)")
                self._debug_print(f"[SelectImport] Final assignments: {assignments}")
                continue
            
            # Unmatched texture - add to unassigned section
            texture_info = self._build_texture_info(rep_path, {
                "status": "unmatched",
                "assignments": [{"attribute": None, "channel": None, "colorspace": "default"}]
            })
            texture_info["udim_count"] = udim_count
            self.selected_textures["unassigned"].append(texture_info)
            self._debug_print(f"[SelectImport] '{os.path.basename(rep_path)}' -> (no match, {udim_count} tiles)")
        
        # Process regular (non-UDIM) files
        for path in regular_files:
            # Skip if already in selection
            if path in existing_paths:
                self._debug_print(f"[SelectImport] '{os.path.basename(path)}' -> already selected, skipping")
                continue
            
            # Check if already connected
            if path in connected_textures:
                texture_info = self._build_texture_info(path, connected_textures[path])
                self.selected_textures["assigned"].append(texture_info)
                self._debug_print(f"[SelectImport] '{os.path.basename(path)}' -> already connected")
                continue

            # First check regular texture types (BEFORE packed textures to avoid normal maps matching packed)
            ttype, score = self._classify_texture_type_for_file(path, kw_map, material)
            if ttype:
                # Matched texture - add to assigned section
                texture_info = self._build_texture_info(path, {
                    "status": "matched",
                    "assignments": [{"attribute": ttype, "channel": None, "colorspace": TEXTURE_RULES.get(ttype, {}).get("colorSpace", "default")}]
                })
                self.selected_textures["assigned"].append(texture_info)
                self._debug_print(f"[SelectImport] '{os.path.basename(path)}' -> {ttype} (score={score})")
                continue
            
            # Check packed textures (only if not classified as regular type)
            packed_matches = self._check_packed_textures(path, packed_textures, material)
            if packed_matches:
                # Packed texture - add to assigned section with multiple assignments
                assignments = []
                for match in packed_matches:
                    attr = match.get("attribute")
                    channel = match.get("channel")
                    colorspace = TEXTURE_RULES.get(attr, {}).get("colorSpace", "default") if attr else "default"
                    assignments.append({"attribute": attr, "channel": channel, "colorspace": colorspace})
                
                texture_info = self._build_texture_info(path, {
                    "status": "packed",
                    "assignments": assignments
                })
                self.selected_textures["assigned"].append(texture_info)
                self._debug_print(f"[SelectImport] '{os.path.basename(path)}' -> packed texture ({len(packed_matches)} assignments)")
                continue
            
            # Unmatched texture - add to unassigned section
            texture_info = self._build_texture_info(path, {
                "status": "unmatched",
                "assignments": [{"attribute": None, "channel": None, "colorspace": "default"}]
            })
            self.selected_textures["unassigned"].append(texture_info)
            self._debug_print(f"[SelectImport] '{os.path.basename(path)}' -> (no match)")

        # Populate UI with selected textures
        self._populate_texture_selection_ui()

    def _build_texture_info(self, file_path, info_dict):
        """
        Build a texture info dict with UDIM detection.
        
        Args:
            file_path: Path to texture file
            info_dict: Dict with "status" and "assignments" keys
        
        Returns:
            Dict with keys: path, name, udim_pattern, udim_count, assignments
        """
        file_dir, file_name = os.path.split(file_path)
        file_base, file_ext = os.path.splitext(file_name)
        
        # UDIM detection - always count tiles if pattern is detected
        # The use_udim checkbox only controls display, not detection
        udim_pattern = self.detect_udim_pattern(file_base)
        udim_count = 0
        if udim_pattern:
            udim_count = self.count_udim_tiles(file_dir, file_base, file_ext, udim_pattern)
            self._debug_print(f"[BuildTextureInfo] UDIM detected: pattern={udim_pattern}, count={udim_count} for {file_name}")
        
        result = {
            "path": file_path,
            "name": file_name,
            "udim_pattern": udim_pattern,
            "udim_count": udim_count,
            "assignments": info_dict.get("assignments", [])
        }
        self._debug_print(f"[BuildTextureInfo] Built texture info with {len(result['assignments'])} assignments, udim_count={udim_count}: {result['assignments']}")
        return result

    def _pre_populate_textures(self, texture_files):
        """
        Pre-populate the texture importer with selected texture files.
        This is called when the texture importer is opened with pre-selected textures.
        Uses the same logic as select_textures_for_import() but without file dialog.
        """
        if not texture_files:
            return

        # Load keyword map & get current material name (safe)
        kw_map, packed_textures = self._load_keyword_map()
        mat_combo = self._get_widget("materialComboBox", QtWidgets.QComboBox)
        material = mat_combo.currentText() if mat_combo else ""

        # Find already-connected textures
        connected_textures = self._find_connected_textures()

        # Clear previous selections
        self.selected_textures = {"unassigned": [], "assigned": []}

        # Group UDIM tiles together - collect all files and group by base name
        udim_groups = {}  # base_name_without_udim -> [paths]
        regular_files = []  # files without UDIM pattern

        for path in texture_files:
            file_name = os.path.basename(path)
            file_base, file_ext = os.path.splitext(file_name)
            
            # Check for UDIM pattern
            udim_pattern = self.detect_udim_pattern(file_base)
            if udim_pattern and self.use_udim:
                # Remove UDIM number to get base name
                udim_regex = re.compile(udim_pattern)
                base_without_udim = udim_regex.sub("", file_base)
                key = f"{base_without_udim}{file_ext}"
                if key not in udim_groups:
                    udim_groups[key] = []
                udim_groups[key].append(path)
            else:
                regular_files.append(path)
        
        # Process UDIM groups - use representative tile (prefer 1001)
        for base_key, tile_paths in udim_groups.items():
            # Pick representative tile (prefer 1001)
            rep_path = self._prefer_representative_udim(tile_paths)
            if not rep_path:
                continue
            
            # Count all tiles for this texture
            file_dir, file_name = os.path.split(rep_path)
            file_base, file_ext = os.path.splitext(file_name)
            udim_pattern = self.detect_udim_pattern(file_base)
            udim_count = len(tile_paths) if udim_pattern else 0
            
            # Classify the representative tile
            # IMPORTANT: Check packed textures FIRST (even if already connected)
            # Packed textures should show all their assignments, not just the first connected one
            packed_matches = self._check_packed_textures(rep_path, packed_textures, material)
            if packed_matches:
                # Packed texture - use packed assignments (override connected texture data if exists)
                assignments = []
                for match in packed_matches:
                    attr = match.get("attribute")
                    channel = match.get("channel")
                    colorspace = TEXTURE_RULES.get(attr, {}).get("colorSpace", "default") if attr else "default"
                    assignments.append({"attribute": attr, "channel": channel, "colorspace": colorspace})
                    self._debug_print(f"[PrePopulate] Packed match: attribute={attr}, channel={channel}, colorspace={colorspace}")
                
                texture_info = self._build_texture_info(rep_path, {
                    "status": "packed",
                    "assignments": assignments
                })
                # Don't overwrite udim_count - _build_texture_info already counted all tiles correctly
                # Use the count from texture_info which was calculated by scanning the directory
                actual_udim_count = texture_info.get("udim_count", 0)
                self.selected_textures["assigned"].append(texture_info)
                self._debug_print(f"[PrePopulate] '{os.path.basename(rep_path)}' -> packed texture ({len(packed_matches)} assignments, {actual_udim_count} tiles)")
                self._debug_print(f"[PrePopulate] Final assignments: {assignments}")
                continue
            
            # Check if already connected (only if not a packed texture)
            if rep_path in connected_textures:
                texture_info = self._build_texture_info(rep_path, connected_textures[rep_path])
                # Don't overwrite udim_count - _build_texture_info already counted all tiles correctly
                actual_udim_count = texture_info.get("udim_count", 0)
                self.selected_textures["assigned"].append(texture_info)
                self._debug_print(f"[PrePopulate] '{os.path.basename(rep_path)}' -> already connected ({actual_udim_count} tiles)")
                continue
            
            # First check regular texture types (before packed textures)
            ttype, score = self._classify_texture_type_for_file(rep_path, kw_map, material)
            if ttype:
                # Matched texture - add to assigned section
                texture_info = self._build_texture_info(rep_path, {
                    "status": "matched",
                    "assignments": [{"attribute": ttype, "channel": None, "colorspace": TEXTURE_RULES.get(ttype, {}).get("colorSpace", "default")}]
                })
                # Don't overwrite udim_count - _build_texture_info already counted all tiles correctly
                actual_udim_count = texture_info.get("udim_count", 0)
                self.selected_textures["assigned"].append(texture_info)
                self._debug_print(f"[PrePopulate] '{os.path.basename(rep_path)}' -> {ttype} (score={score}, {actual_udim_count} tiles)")
                continue
            if packed_matches:
                # Packed texture - add to assigned section with multiple assignments
                assignments = []
                for match in packed_matches:
                    attr = match.get("attribute")
                    channel = match.get("channel")
                    colorspace = TEXTURE_RULES.get(attr, {}).get("colorSpace", "default") if attr else "default"
                    assignments.append({"attribute": attr, "channel": channel, "colorspace": colorspace})
                    self._debug_print(f"[PrePopulate] Packed match: attribute={attr}, channel={channel}, colorspace={colorspace}")
                
                texture_info = self._build_texture_info(rep_path, {
                    "status": "packed",
                    "assignments": assignments
                })
                texture_info["udim_count"] = udim_count
                self.selected_textures["assigned"].append(texture_info)
                self._debug_print(f"[PrePopulate] '{os.path.basename(rep_path)}' -> packed texture ({len(packed_matches)} assignments, {udim_count} tiles)")
                self._debug_print(f"[PrePopulate] Final assignments: {assignments}")
                continue
            
            # Unmatched texture - add to unassigned section
            texture_info = self._build_texture_info(rep_path, {
                "status": "unmatched",
                "assignments": [{"attribute": None, "channel": None, "colorspace": "default"}]
            })
            texture_info["udim_count"] = udim_count
            self.selected_textures["unassigned"].append(texture_info)
            self._debug_print(f"[PrePopulate] '{os.path.basename(rep_path)}' -> (no match, {udim_count} tiles)")
        
        # Process regular (non-UDIM) files
        for path in regular_files:
            # Check if already connected
            if path in connected_textures:
                texture_info = self._build_texture_info(path, connected_textures[path])
                self.selected_textures["assigned"].append(texture_info)
                self._debug_print(f"[PrePopulate] '{os.path.basename(path)}' -> already connected")
                continue

            # First check regular texture types (BEFORE packed textures to avoid normal maps matching packed)
            ttype, score = self._classify_texture_type_for_file(path, kw_map, material)
            if ttype:
                # Matched texture - add to assigned section
                texture_info = self._build_texture_info(path, {
                    "status": "matched",
                    "assignments": [{"attribute": ttype, "channel": None, "colorspace": TEXTURE_RULES.get(ttype, {}).get("colorSpace", "default")}]
                })
                self.selected_textures["assigned"].append(texture_info)
                self._debug_print(f"[PrePopulate] '{os.path.basename(path)}' -> {ttype} (score={score})")
                continue
            
            # Check packed textures (only if not classified as regular type)
            packed_matches = self._check_packed_textures(path, packed_textures, material)
            if packed_matches:
                # Packed texture - add to assigned section with multiple assignments
                assignments = []
                for match in packed_matches:
                    attr = match.get("attribute")
                    channel = match.get("channel")
                    colorspace = TEXTURE_RULES.get(attr, {}).get("colorSpace", "default") if attr else "default"
                    assignments.append({"attribute": attr, "channel": channel, "colorspace": colorspace})
                
                texture_info = self._build_texture_info(path, {
                    "status": "packed",
                    "assignments": assignments
                })
                self.selected_textures["assigned"].append(texture_info)
                self._debug_print(f"[PrePopulate] '{os.path.basename(path)}' -> packed texture ({len(packed_matches)} assignments)")
                continue
            
            # Unmatched texture - add to unassigned section
            texture_info = self._build_texture_info(path, {
                "status": "unmatched",
                "assignments": [{"attribute": None, "channel": None, "colorspace": "default"}]
            })
            self.selected_textures["unassigned"].append(texture_info)
            self._debug_print(f"[PrePopulate] '{os.path.basename(path)}' -> (no match)")

        # Populate UI with selected textures
        self._populate_texture_selection_ui()

    def clear_all_textures(self):
        """
        Clear all texture line edits and in-memory selections.
        Also clears any cached bulk matches from Auto-Find-All.
        """
        # Clear UI fields (old texture type rows)
        for ttype in ALL_TEXTURE_TYPES:
            le = self.ui_elements.get(f"{ttype}LineEdit")
            if le:
                try:
                    le.clear()
                except Exception:
                    pass

        # Reset internal state
        self.texture_data.clear()
        self._clear_bulk_folder_state()

        # Clear new unified texture selection structure
        if hasattr(self, 'selected_textures'):
            self.selected_textures = {"unassigned": [], "assigned": []}
        
        # Clear widget references
        if hasattr(self, 'texture_entry_widgets'):
            self.texture_entry_widgets.clear()
        
        # Clear UI entries
        self._clear_texture_entries()
        self._update_import_button_label()

        self._debug_print("[ClearAll] Cleared all texture entries and caches.")

    def _find_best_match_for_type(self, texture_type, material_name, kw_map, recurse, max_depth,
                                  max_levels=3, debug=True, name_predicate=None):  # <-- updated
        """
        Stricter matching:
          - Requires at least one JSON keyword for this type to appear in the filename.
          - Penalizes filenames containing other types' keywords.
          - Still climbs up parent folders and respects recursion bounds.
          - Optional name_predicate to prefilter candidate filenames (e.g., must contain material).
        """
        # Search folder functionality removed - this function is no longer used for auto-search
        return None

    # Set frame style for texture atributes
    def _apply_texture_entry_style(self, widget, status):
        """Apply consistent styling for assigned/unassigned texture entry frames."""
        if not widget:
            return
        widget.setObjectName("textureEntryContainer")
        if status == "assigned":
            style = """
                QWidget#textureEntryContainer {
                    background-color: #2a2a2a;
                    border: 0px solid #666666;
                    border-radius: 6px;
                    padding: 4px;
                    margin: 2px;
                }
            """
        else:
            style = """
                QWidget#textureEntryContainer {
                    background-color: #3a3a3a;
                    border: 0px solid #555555;
                    border-radius: 6px;
                    padding: 4px;
                    margin: 2px;
                }
            """
        widget.setStyleSheet(style)

    def _get_combo_stylesheet(self, color="#00f7c8"):
        """Return the shared combobox stylesheet with a specific text color."""
        return combo_stylesheet_template.format(color=color)

    # -------- UDIM --------
    def detect_udim_pattern(self, file_base):
        """
        Detects potential UDIM patterns in the file base name.

        Returns:
            str: The detected UDIM pattern (e.g., r"10\\d{2}") or None if no pattern is found.
        """
        udim_patterns = [
            r"10\d{2}",          # Standard UDIM tiles: 1001..1999 (commonly 1001..1100+)
            # Future: add MARI-style here, e.g., r"u\d+_v\d+"
        ]
        for pattern in udim_patterns:
            if re.search(pattern, file_base):
                return pattern
        return None

    def on_use_udim_toggled(self, state):  # <-- new
        """
        Checkbox handler: updates self.use_udim and refreshes all line edit displays
        to add/remove '(N Tiles)' immediately.
        """
        self.use_udim = bool(state)
        self._debug_print(f"[UseUDIM] Toggled -> {self.use_udim}")
        self._apply_udim_display_to_lineedits()

    def _apply_udim_display_to_lineedits(self):
        """
        Re-render every texture type line edit text based on self.use_udim
        and the stored self.texture_data entries.
        Also updates the unified texture selection UI line edits.
        """
        # Update old texture type line edits
        for ttype, data in self.texture_data.items():
            le = self.ui_elements.get(f"{ttype}LineEdit")
            if not le:
                continue
            fname = data.get("name", "")
            udim_count = data.get("udim_count", 0)
            if self.use_udim and udim_count > 1:
                le.setText(f"{fname} ({udim_count} Tiles)")
            else:
                le.setText(fname)
        
        # Update unified texture selection UI line edits
        if hasattr(self, 'texture_entry_widgets'):
            path_to_info = {}
            for status in ["unassigned", "assigned"]:
                for tex_info in self.selected_textures.get(status, []):
                    tex_path = tex_info.get("path")
                    if tex_path:
                        path_to_info[tex_path] = tex_info
            
            for texture_path, widget_info in self.texture_entry_widgets.items():
                texture_info = path_to_info.get(texture_path)
                if texture_info:
                    self._update_texture_entry_widget_display(widget_info, texture_info)

    def _update_texture_entry_widget_display(self, widget_info, texture_info):
        """Update the line edit text and UDIM label for a texture entry."""
        if not widget_info or not texture_info:
            return
        
        texture_path = texture_info.get("path", "")
        texture_name = texture_info.get("name", os.path.basename(texture_path))
        udim_count = texture_info.get("udim_count", 0)
        
        line_edit = widget_info.get("line_edit")
        if line_edit:
            line_edit.blockSignals(True)
            line_edit.setText(texture_name)
            line_edit.blockSignals(False)
        
        udim_label = widget_info.get("udim_label")
        if udim_label:
            if self.use_udim and udim_count > 1:
                udim_label.setText(f"({udim_count} Tiles)")
                udim_label.setVisible(True)
            else:
                udim_label.clear()
                udim_label.setVisible(False)

    def _get_widget(self, name, cls=None):
        """
        Robustly fetch a widget by objectName. Never trust cached PySide objects
        if they are invalid; always re-find from the live dialog.
        """
        w = self.ui_elements.get(name)
        if not w or not isValid(w):
            # Re-find from this dialog
            w = self.findChild(QtWidgets.QWidget, name)
            if w and (cls is None or isinstance(w, cls)):
                self.ui_elements[name] = w
            else:
                return None
        if cls and not isinstance(w, cls):
            return None
        return w

    def _find_connected_textures(self):
        """
        Find textures that are already connected to the current material.
        Returns dict: {texture_path: {"status": "connected", "assignments": [{"attribute": "...", "channel": "..."}]}}
        """
        mat_combo = self._get_widget("materialComboBox", QtWidgets.QComboBox)
        material = mat_combo.currentText() if mat_combo else ""
        
        if (
            not material
            or material == BULK_ALL_MATERIALS_LABEL
            or not cmds.objExists(material)
        ):
            return {}
        
        connected = {}
        
        # Check each texture type's attribute
        for texture_type in ALL_TEXTURE_TYPES:
            if texture_type not in TEXTURE_RULES:
                continue
            
            rules = TEXTURE_RULES[texture_type]
            attr = rules.get("attr")
            if not attr:
                continue
            
            # Check if attribute has a connection
            try:
                connections = cmds.listConnections(f"{material}.{attr}", source=True, destination=False, plugs=True) or []
                for conn_plug in connections:
                    # Extract file node from connection
                    source_node = conn_plug.split(".")[0] if "." in conn_plug else conn_plug
                    
                    # Check if it's a file node
                    if cmds.nodeType(source_node) == "file":
                        file_path = cmds.getAttr(f"{source_node}.fileTextureName")
                        if file_path:
                            # Normalize path
                            file_path = file_path.replace("\\", "/")
                            # Extract channel from connection
                            channel = None
                            if ".outColorR" in conn_plug:
                                channel = "r"
                            elif ".outColorG" in conn_plug:
                                channel = "g"
                            elif ".outColorB" in conn_plug:
                                channel = "b"
                            elif ".outAlpha" in conn_plug:
                                channel = "a"
                            
                            if file_path not in connected:
                                connected[file_path] = {"status": "connected", "assignments": []}
                            
                            connected[file_path]["assignments"].append({
                                "attribute": texture_type,
                                "channel": channel
                            })
            except Exception as e:
                pass  # Attribute might not exist or not connected
        
        return connected

    # ---------- Import helpers ----------
    def _resolve_texture_mapping(self, material, texture_type):
        """Map logical texture slot to target plugs; uses material_converter on legacy shaders."""
        rules = TEXTURE_RULES.get(texture_type, {})
        std_attr = rules.get("attr")
        kind = rules.get("kind")
        if resolve_texture_import_mapping:
            return resolve_texture_import_mapping(
                material, texture_type, std_attr, kind
            )
        return {
            "target_attr": std_attr,
            "opacity_mode": None,
            "normal_utility": "aiNormalMap",
            "warning": None,
            "skip": bool(not std_attr and kind != "displacement"),
        }

    def _record_import_warning(self, msg):
        if msg and msg not in self._import_warnings:
            self._import_warnings.append(msg)

    def _flush_import_warnings(self):
        if not self._import_warnings:
            return
        combined = "\n".join(self._import_warnings)
        cmds.warning(combined)
        self._import_warnings = []

    # --- Bulk folder import (All Materials) ---
    @staticmethod
    def _strip_namespace_from_material(name):
        if not name:
            return ""
        if ":" in name:
            return name.split(":")[-1]
        return name

    def _is_all_materials_bulk_mode(self, combo_text=None):
        if combo_text is None:
            cb = self._get_widget("materialComboBox", QtWidgets.QComboBox)
            try:
                combo_text = str(cb.currentText()) if cb else ""
            except RuntimeError:
                combo_text = ""
        return combo_text == BULK_ALL_MATERIALS_LABEL

    def _clear_bulk_folder_state(self):
        self._bulk_match_cache = {}
        self._bulk_packed_entries = []
        self._bulk_unmatched_entries = []
        self._bulk_folder_root = None
        self._bulk_type_counts = Counter()
        self._bulk_packed_file_count = 0

    def _walk_bulk_image_files(self, root):
        """Yield (abs_path, depth_from_root) skipping old/archive dirs; depth capped."""
        root = os.path.normpath(os.path.abspath(root))
        if not os.path.isdir(root):
            return
        exts = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".tga", ".bmp"}
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d.lower() not in BULK_FOLDER_SKIP_DIRS]
            rel = os.path.relpath(dirpath, root)
            if rel == ".":
                depth_here = 0
            else:
                depth_here = len(rel.split(os.sep))
            if depth_here > BULK_FOLDER_MAX_DEPTH:
                dirnames[:] = []
                continue
            for fn in filenames:
                ext = os.path.splitext(fn)[1].lower()
                if ext not in exts:
                    continue
                yield os.path.join(dirpath, fn), depth_here

    def _pick_matching_material(self, basename_lc, mat_pairs):
        """Longest material base name contained in basename (namespaces ignored on material)."""
        for base, mfull in mat_pairs:
            b = (base or "").strip().lower()
            if b and b in basename_lc:
                return mfull
        return None

    def _run_bulk_folder_scan(self, root):
        """Scan folder tree, match files to scene materials + texture types; fills bulk caches."""
        self._bulk_match_cache = {}
        self._bulk_packed_entries = []
        self._bulk_unmatched_entries = []
        self._bulk_type_counts = Counter()
        self._bulk_packed_file_count = 0
        abs_root = os.path.abspath(root)
        self._bulk_folder_root = abs_root
        kw_map, packed_textures = self._load_keyword_map()
        materials = self.get_all_materials_sorted()
        mat_pairs = [
            (self._strip_namespace_from_material(m), m) for m in materials
        ]
        mat_pairs.sort(key=lambda x: len(x[0] or ""), reverse=True)

        file_depths = list(self._walk_bulk_image_files(root))
        if not file_depths:
            self.selected_textures = {"unassigned": [], "assigned": []}
            self._populate_texture_selection_ui()
            self._update_import_button_label()
            return

        paths_only = [x[0] for x in file_depths]
        depth_by_path = dict(file_depths)

        udim_groups = {}
        regular_files = []
        for path in paths_only:
            file_name = os.path.basename(path)
            file_base, file_ext = os.path.splitext(file_name)
            udim_pattern = self.detect_udim_pattern(file_base)
            if udim_pattern and self.use_udim:
                udim_regex = re.compile(udim_pattern)
                base_without_udim = udim_regex.sub("", file_base)
                gk = (os.path.dirname(path), "%s%s" % (base_without_udim, file_ext))
                udim_groups.setdefault(gk, []).append(path)
            else:
                regular_files.append(path)

        slot_candidates = {}
        packed_tmp = []
        material_matched_unclassified = []

        def add_slot(mat, ttype, path, depth):
            slot_candidates.setdefault((mat, ttype), []).append((path, depth))

        def process_rep(rep_path, tile_paths):
            depth = depth_by_path.get(rep_path, 0)
            basename_lc = os.path.basename(rep_path).lower()
            mat_full = self._pick_matching_material(basename_lc, mat_pairs)
            if not mat_full:
                return
            packed_matches = self._check_packed_textures(
                rep_path, packed_textures, mat_full
            )
            if packed_matches:
                assignments = []
                for match in packed_matches:
                    attr = match.get("attribute")
                    channel = match.get("channel")
                    cs = (
                        TEXTURE_RULES.get(attr, {}).get("colorSpace", "default")
                        if attr
                        else "default"
                    )
                    assignments.append(
                        {
                            "attribute": attr,
                            "channel": channel,
                            "colorspace": cs,
                        }
                    )
                packed_tmp.append(
                    {
                        "material": mat_full,
                        "path": rep_path,
                        "assignments": assignments,
                        "depth": depth,
                    }
                )
                return
            ttype, _sc = self._classify_texture_type_for_file(
                rep_path, kw_map, mat_full
            )
            if not ttype:
                material_matched_unclassified.append(
                    {"material": mat_full, "path": rep_path, "depth": depth}
                )
                return
            add_slot(mat_full, ttype, rep_path, depth)

        for _gk, tile_paths in udim_groups.items():
            rep_path = self._prefer_representative_udim(tile_paths)
            if rep_path:
                process_rep(rep_path, tile_paths)

        for path in regular_files:
            process_rep(path, [path])

        mat_map = {}
        for (mat, ttype), lst in slot_candidates.items():
            best = min(lst, key=lambda x: (x[1], x[0]))
            mat_map.setdefault(mat, {})[ttype] = best[0]

        packed_by_key = {}
        for entry in packed_tmp:
            k = (entry["material"], entry["path"])
            d = entry["depth"]
            if k not in packed_by_key or d < packed_by_key[k]["depth"]:
                packed_by_key[k] = entry

        self._bulk_match_cache = mat_map
        self._bulk_packed_entries = [
            {
                "material": v["material"],
                "path": v["path"],
                "assignments": v["assignments"],
            }
            for v in packed_by_key.values()
        ]

        classified_paths = set()
        for tm in mat_map.values():
            for pth in tm.values():
                if pth:
                    classified_paths.add(os.path.normcase(os.path.normpath(pth)))
        for v in packed_by_key.values():
            pth = v.get("path")
            if pth:
                classified_paths.add(os.path.normcase(os.path.normpath(pth)))

        best_uncls = {}
        for row in material_matched_unclassified:
            pth = row.get("path")
            if not pth:
                continue
            pn = os.path.normcase(os.path.normpath(pth))
            if pn in classified_paths:
                continue
            d = row.get("depth", 0)
            mat = row.get("material")
            if pn not in best_uncls or d < best_uncls[pn][0]:
                best_uncls[pn] = (d, mat, pth)
        self._bulk_unmatched_entries = [
            {"material": m, "path": pth, "depth": d}
            for _pn, (d, m, pth) in best_uncls.items()
        ]

        self._rebuild_bulk_selected_textures_from_scan(
            self._bulk_match_cache, self._bulk_packed_entries, self._bulk_unmatched_entries
        )

        counts = Counter()
        for _mat, tm in mat_map.items():
            for tt, pth in tm.items():
                if pth:
                    counts[tt] += 1
        self._bulk_packed_file_count = len(self._bulk_packed_entries)
        for entry in self._bulk_packed_entries:
            for a in entry.get("assignments", []):
                att = a.get("attribute")
                if att:
                    counts[att] += 1

        self._bulk_type_counts = counts
        self._populate_texture_selection_ui()
        self._update_import_button_label()

    def _rebuild_bulk_selected_textures_from_scan(self, mat_map, packed_entries, unmatched_entries=None):
        """Fill selected_textures with one row per matched file; each row has target_material for bulk import."""
        self.selected_textures = {"unassigned": [], "assigned": []}
        for mat, tm in (mat_map or {}).items():
            for ttype, path in tm.items():
                if not path:
                    continue
                ti = self._build_texture_info(
                    path,
                    {
                        "assignments": [{
                            "attribute": ttype,
                            "channel": None,
                            "colorspace": TEXTURE_RULES.get(ttype, {}).get("colorSpace", "default"),
                        }]
                    },
                )
                ti["target_material"] = mat
                self.selected_textures["assigned"].append(ti)
        for entry in packed_entries or []:
            path = entry.get("path")
            mat = entry.get("material")
            if not path or not mat:
                continue
            assigns = entry.get("assignments") or []
            ti = self._build_texture_info(
                path,
                {"assignments": [dict(a) for a in assigns]},
            )
            ti["target_material"] = mat
            self.selected_textures["assigned"].append(ti)
        for row in unmatched_entries or []:
            pth = row.get("path")
            mat = row.get("material")
            if not pth or not mat:
                continue
            ti = self._build_texture_info(
                pth,
                {
                    "assignments": [{
                        "attribute": "skip",
                        "channel": None,
                        "colorspace": "default",
                    }]
                },
            )
            ti["target_material"] = mat
            self.selected_textures["assigned"].append(ti)
        self.selected_textures["assigned"].sort(
            key=lambda x: (
                (x.get("target_material") or "").lower(),
                os.path.basename(x.get("path") or "").lower(),
            )
        )

    def _create_bulk_material_collapsible(self, material_name):
        """Header row (blue name + collapse) and inner vertical layout for texture rows."""
        frame = QtWidgets.QFrame()
        frame.setFrameShape(QtWidgets.QFrame.NoFrame)
        outer = QtWidgets.QVBoxLayout(frame)
        outer.setContentsMargins(0, 6, 0, 2)
        outer.setSpacing(2)

        header = QtWidgets.QWidget()
        hl = QtWidgets.QHBoxLayout(header)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(6)

        toggle = QtWidgets.QToolButton()
        toggle.setCheckable(True)
        toggle.setChecked(True)
        toggle.setArrowType(QtCore.Qt.DownArrow)
        toggle.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        toggle.setStyleSheet("QToolButton { border: none; background: transparent; }")
        hl.addWidget(toggle)

        title = QtWidgets.QLabel(material_name)
        title.setStyleSheet(
            "font-family: 'Segoe UI'; font-size: 14px; font-weight: bold; color: %s; "
            "background: transparent; border: none; padding: 2px;"
            % BULK_MATERIAL_HEADER_COLOR
        )
        hl.addWidget(title, 1)

        body = QtWidgets.QWidget()
        bl = QtWidgets.QVBoxLayout(body)
        bl.setContentsMargins(12, 0, 0, 4)
        bl.setSpacing(4)

        outer.addWidget(header)
        outer.addWidget(body)

        def on_clicked(checked):
            body.setVisible(checked)
            toggle.setArrowType(QtCore.Qt.DownArrow if checked else QtCore.Qt.RightArrow)

        toggle.clicked.connect(on_clicked)
        return {"frame": frame, "body_layout": bl}

    def _populate_bulk_mode_scroll(self):
        """Bulk mode: same per-texture rows as single-material mode, grouped under collapsible material headers."""
        hint_folder = QtWidgets.QLabel(
            "Select a textures folder to match files to scene materials (material name must appear in each filename)."
        )
        hint_folder.setWordWrap(True)
        hint_folder.setStyleSheet(
            "font-family: 'Segoe UI'; font-size: 13px; color: #cccccc; padding: 4px; background: transparent;"
        )
        if not self._bulk_folder_root:
            self.textures_layout.addWidget(hint_folder)
            return

        folder_lbl = QtWidgets.QLabel("Folder: %s" % self._bulk_folder_root)
        folder_lbl.setWordWrap(True)
        folder_lbl.setStyleSheet(
            "font-family: 'Segoe UI'; font-size: 13px; color: #aaaaaa; background: transparent;"
        )
        self.textures_layout.addWidget(folder_lbl)

        bulk_items = []
        for status in ("unassigned", "assigned"):
            for ti in self.selected_textures.get(status, []):
                if ti.get("target_material"):
                    bulk_items.append(ti)

        if not bulk_items:
            empty = QtWidgets.QLabel(
                "No matching textures found. Filenames must include the material name; "
                "folders named old or archive are skipped."
            )
            empty.setWordWrap(True)
            empty.setStyleSheet("color: #bbbbbb; padding: 6px; background: transparent;")
            self.textures_layout.addWidget(empty)
            return

        by_mat = {}
        for ti in bulk_items:
            by_mat.setdefault(ti["target_material"], []).append(ti)
        for m in by_mat:
            by_mat[m].sort(key=lambda x: os.path.basename(x.get("path") or "").lower())

        for mat in sorted(by_mat.keys(), key=lambda s: s.lower()):
            section = self._create_bulk_material_collapsible(mat)
            self.textures_layout.addWidget(section["frame"])
            body_layout = section["body_layout"]
            for ti in by_mat[mat]:
                has_imp = any(
                    a.get("attribute") and a.get("attribute") != "skip"
                    for a in ti.get("assignments", [])
                )
                visual = "assigned" if has_imp else "unassigned"
                w = self._create_texture_entry_widget(ti, visual)
                if w:
                    body_layout.addWidget(w)

    def _update_select_textures_button_label(self):
        btn = self.ui_elements.get(
            "selectTextureForImportButton"
        ) or self.ui_elements.get("selectTexturesForImportButton")
        if not btn or not isValid(btn):
            return
        try:
            if self._is_all_materials_bulk_mode():
                btn.setText("Select Textures Folder for Import")
            else:
                btn.setText("Select Textures For Import")
        except RuntimeError:
            pass

    def _select_folder_for_bulk_import(self):
        start = self._bulk_folder_root or self.search_folder_path or ""
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select Textures Folder for Import",
            start,
        )
        if not folder:
            return
        self.search_folder_path = folder
        self._run_bulk_folder_scan(folder)

    def _ensure_normal_map_for_mapping(self, material, normal_utility):
        """aiNormalMap for standardSurface / aiStandardSurface; bump2d for all other shaders."""
        if normal_utility == "bump2d":
            return self._ensure_legacy_bump2d(material)
        return self._ensure_ai_normal_map(material)

    def _ensure_legacy_bump2d(self, material):
        """
        Maya bump2d → normalCamera for Lambert / Blinn / Phong (and other non–Standard Surface shaders).
        Uses tangent-space normal interpretation (bumpInterp) so RGB normal maps display correctly.
        """
        bn = "%s_bump2d" % material
        if not cmds.objExists(bn):
            bn = cmds.shadingNode("bump2d", asUtility=True, name=bn)
        try:
            if cmds.attributeQuery("bumpInterp", node=bn, exists=True):
                # 0 = bump height, 1 = tangent space normals (Maya default enum order)
                cmds.setAttr("%s.bumpInterp" % bn, 1)
        except Exception as e:
            self._debug_print("[Import] bump2d bumpInterp: %s" % e)
        try:
            if not cmds.isConnected("%s.outNormal" % bn, "%s.normalCamera" % material):
                cmds.connectAttr("%s.outNormal" % bn, "%s.normalCamera" % material, force=True)
        except Exception:
            pass
        return bn

    def _connect_file_to_normal_utility(self, file_node, utility_node, normal_utility):
        """Wire file texture into aiNormalMap.input or bump2d.bumpValue."""
        try:
            if normal_utility == "bump2d":
                dst_plug = "%s.bumpValue" % utility_node
            else:
                dst_plug = "%s.input" % utility_node
            if not cmds.isConnected("%s.outColor" % file_node, dst_plug):
                cmds.connectAttr("%s.outColor" % file_node, dst_plug, force=True)
        except Exception as e:
            self._debug_print("[Import] normal file -> utility failed: %s" % e)

    def _connect_src_to_reversed_transparency(self, src_plug, material):
        """
        Single or triple channel source → reverse → material.transparency (legacy shaders).
        src_plug: e.g. file.outAlpha or file.outColorR
        """
        rev = "%s_opacityRev" % material
        if not cmds.objExists(rev):
            rev = cmds.shadingNode("reverse", asUtility=True, name=rev)
        try:
            if src_plug.endswith(".outColor"):
                cmds.connectAttr(src_plug, "%s.input" % rev, force=True)
            else:
                for ax in ("R", "G", "B"):
                    cmds.connectAttr(src_plug, "%s.input%s" % (rev, ax), force=True)
            cmds.connectAttr("%s.output" % rev, "%s.transparency" % material, force=True)
        except Exception as e:
            self._debug_print("[Import] reverse transparency chain failed: %s" % e)

    def _connect_file_to_reversed_transparency(self, file_node, material, ch_pref):
        """
        Opacity map (opaque = white) → reverse → transparency (transparent = white), for legacy shaders.
        ch_pref: "A", "R", "G", "B", or None (prefer full outColor, else alpha on RGB inputs).
        """
        cp = ch_pref.upper() if (ch_pref and isinstance(ch_pref, str)) else None
        try:
            if cp in (None, "A"):
                try:
                    self._connect_src_to_reversed_transparency(
                        "%s.outColor" % file_node, material
                    )
                    return
                except Exception:
                    self._connect_src_to_reversed_transparency(
                        "%s.outAlpha" % file_node, material
                    )
                    return
            if cp == "R":
                self._connect_src_to_reversed_transparency(
                    "%s.outColorR" % file_node, material
                )
            elif cp == "G":
                self._connect_src_to_reversed_transparency(
                    "%s.outColorG" % file_node, material
                )
            elif cp == "B":
                self._connect_src_to_reversed_transparency(
                    "%s.outColorB" % file_node, material
                )
            else:
                self._connect_src_to_reversed_transparency(
                    "%s.outColor" % file_node, material
                )
        except Exception as e:
            self._debug_print("[Import] reverse transparency chain failed: %s" % e)

    def _get_channel_selection(self, texture_type):
        """
        Return preferred source channel for connections based on checkboxes for this type.
        Returns: "A", "R", "G", "B", or None.
        Priority:
          1) If exactly one box is checked, use it
          2) Else use the per-type default (A for float/displacement, None for color/normal)
        """
        a = self.ui_elements.get(f"{texture_type}ChannelAlphaCheckbox")
        r = self.ui_elements.get(f"{texture_type}ChannelRedCheckbox")
        g = self.ui_elements.get(f"{texture_type}ChannelGreenCheckbox")
        b = self.ui_elements.get(f"{texture_type}ChannelBlueCheckbox")

        a_on = bool(a.isChecked()) if a else False
        r_on = bool(r.isChecked()) if r else False
        g_on = bool(g.isChecked()) if g else False
        b_on = bool(b.isChecked()) if b else False

        # If exactly one checked, that wins
        picks = [("A", a_on), ("R", r_on), ("G", g_on), ("B", b_on)]
        picks_on = [p[0] for p in picks if p[1]]
        if len(picks_on) == 1:
            return picks_on[0]

        # Otherwise, fall back to default per type
        return self._default_channel_for_type(texture_type)

    def _ensure_file_node(self, material, texture_type, file_path, color_space, use_udim):
        """
        Create or reuse a Maya 'file' node configured with colorSpace and (optionally) UDIM pattern.
        Enforces color management overrides so our explicit colorSpace (e.g., 'Raw') is respected.
        Returns the file node name and the actual file texture path/pattern set on it.
        """
        safe_tt = re.sub(r"[^A-Za-z0-9_]", "_", texture_type)
        node_name = f"{material}_{safe_tt}_file"
        if not cmds.objExists(node_name):
            node_name = cmds.shadingNode("file", asTexture=True, name=node_name)

        # --- Color management overrides (make node respect our explicit colorSpace) ---
        try:
            if cmds.attributeQuery("ignoreColorSpaceFileRules", node=node_name, exists=True):
                cmds.setAttr(f"{node_name}.ignoreColorSpaceFileRules", 1)
        except Exception as e:
            self._debug_print(f"[ColorSpace] ignoreColorSpaceFileRules set failed on {node_name}: {e}")

        try:
            # Some Maya versions expose this flag; if present, disable the default rules.
            if cmds.attributeQuery("useDefaultColorSpace", node=node_name, exists=True):
                cmds.setAttr(f"{node_name}.useDefaultColorSpace", 0)
        except Exception as e:
            self._debug_print(f"[ColorSpace] useDefaultColorSpace set failed on {node_name}: {e}")

        try:
            if cmds.attributeQuery("colorSpace", node=node_name, exists=True) and color_space:
                cmds.setAttr(f"{node_name}.colorSpace", color_space, type="string")
                self._debug_print(f"[ColorSpace] {node_name}.colorSpace -> '{color_space}'")
            else:
                self._debug_print(f"[ColorSpace] {node_name} missing colorSpace attr or no color_space provided")
        except Exception as e:
            self._debug_print(f"[ColorSpace] Failed to set {node_name}.colorSpace='{color_space}': {e}")

        # --- UDIM handling ---
        file_dir, file_name = os.path.split(file_path)
        file_base, file_ext = os.path.splitext(file_name)
        path_to_set = file_path

        if use_udim:
            # Replace UDIM digits with <UDIM> if detected
            udim_regex = re.compile(r"10\d{2}")
            if udim_regex.search(file_name):
                path_to_set = os.path.join(file_dir, udim_regex.sub("<UDIM>", file_name))
                try:
                    if cmds.attributeQuery("uvTilingMode", node=node_name, exists=True):
                        cmds.setAttr(f"{node_name}.uvTilingMode", 3)  # 3 = UDIM
                except Exception as e:
                    self._debug_print(f"[UDIM] Failed setting uvTilingMode on {node_name}: {e}")
        else:
            # When UDIM is turned off, replace <UDIM> with the actual UDIM number from the original path
            if "<UDIM>" in path_to_set:
                # Try to get the original path from texture_data to extract the UDIM number
                # Look for texture_data entry that matches this texture_type
                texture_data_entry = self.texture_data.get(texture_type, {})
                original_path = texture_data_entry.get("path", file_path)
                
                # Extract UDIM number from original path (prefer 1001, else first found)
                udim_match = re.search(r"10\d{2}", os.path.basename(original_path))
                if udim_match:
                    udim_number = udim_match.group(0)
                    path_to_set = path_to_set.replace("<UDIM>", udim_number)
                    self._debug_print(f"[UDIM] Replaced <UDIM> with {udim_number} in path: {path_to_set}")
                else:
                    # Fallback: if we can't find UDIM in original, try to find it in the directory
                    # Look for files matching the pattern
                    if os.path.isdir(file_dir):
                        pattern_base = path_to_set.replace("<UDIM>", "").replace("\\", "/")
                        pattern_base = os.path.basename(pattern_base)
                        # Try common UDIM numbers
                        for udim_num in ["1001", "1002", "1003"]:
                            test_path = os.path.join(file_dir, pattern_base.replace(file_ext, f".{udim_num}{file_ext}"))
                            if os.path.exists(test_path):
                                path_to_set = path_to_set.replace("<UDIM>", udim_num)
                                self._debug_print(f"[UDIM] Found UDIM file, replaced <UDIM> with {udim_num}")
                                break
            
            try:
                if cmds.attributeQuery("uvTilingMode", node=node_name, exists=True):
                    cmds.setAttr(f"{node_name}.uvTilingMode", 0)  # 0 = off/single
            except Exception as e:
                self._debug_print(f"[UDIM] Failed disabling uvTilingMode on {node_name}: {e}")

        # Set file path (pattern or single)
        try:
            cmds.setAttr(f"{node_name}.fileTextureName", path_to_set.replace("\\", "/"), type="string")
        except Exception as e:
            self._debug_print(f"[FilePath] Failed to set fileTextureName on {node_name}: {e}")

        # Create and connect place2dTexture node (like Maya does automatically)
        place2d_name = f"{node_name}_place2dTexture"
        if not cmds.objExists(place2d_name):
            place2d_name = cmds.shadingNode("place2dTexture", asUtility=True, name=place2d_name)
            self._debug_print(f"[Place2D] Created {place2d_name} for {node_name}")
        
        # Connect place2dTexture to file node (standard connections)
        try:
            # Connect outUV to uvCoord
            if not cmds.isConnected(f"{place2d_name}.outUV", f"{node_name}.uvCoord"):
                cmds.connectAttr(f"{place2d_name}.outUV", f"{node_name}.uvCoord", force=True)
            # Connect outUvFilterSize to uvFilterSize
            if not cmds.isConnected(f"{place2d_name}.outUvFilterSize", f"{node_name}.uvFilterSize"):
                cmds.connectAttr(f"{place2d_name}.outUvFilterSize", f"{node_name}.uvFilterSize", force=True)
            self._debug_print(f"[Place2D] Connected {place2d_name} to {node_name}")
        except Exception as e:
            self._debug_print(f"[Place2D] Failed to connect {place2d_name} to {node_name}: {e}")

        return node_name, path_to_set

    def _ensure_ai_normal_map(self, material):
        """
        Ensure an aiNormalMap node exists and is connected to <material>.normalCamera.
        Returns aiNormalMap node name.
        """
        nn = f"{material}_aiNormalMap"
        if not cmds.objExists(nn):
            nn = cmds.shadingNode("aiNormalMap", asUtility=True, name=nn)
        # Connect its output to material.normalCamera
        try:
            if not cmds.isConnected(f"{nn}.outValue", f"{material}.normalCamera"):
                cmds.connectAttr(f"{nn}.outValue", f"{material}.normalCamera", force=True)
        except Exception:
            pass
        return nn

    def _ensure_displacement_network(self, material):
        """
        Ensure a displacementShader is connected to the material's shading group.
        Returns (displacementShaderNode, shadingEngine).
        """
        sgs = cmds.listConnections(material, type="shadingEngine") or []
        if not sgs:
            # Create a new SG if none (rare, but safe)
            sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=f"{material}SG")
            try:
                cmds.connectAttr(f"{material}.outColor", f"{sg}.surfaceShader", force=True)
            except Exception:
                pass
        else:
            sg = sgs[0]

        disp_node = None
        existing = cmds.listConnections(f"{sg}.displacementShader") or []
        if existing:
            disp_node = existing[0]
        else:
            disp_node = cmds.shadingNode("displacementShader", asUtility=True, name=f"{material}_displacementShader")
            cmds.connectAttr(f"{disp_node}.displacement", f"{sg}.displacementShader", force=True)

        return disp_node, sg

    def _connect_scalar_to_color(self, src_attr, dst_node, dst_attr):
        """
        Connect a scalar (float) source to a color3 destination by wiring to .R .G .B.
        """
        for ch in ("R", "G", "B"):
            try:
                cmds.connectAttr(src_attr, f"{dst_node}.{dst_attr}{ch}", force=True)
            except Exception:
                pass

    def _connect_float_source_to_attr(self, src_plug, material, target_attr):
        """Connect a single-channel source to a float or color3 attribute (e.g. transmission → transparency)."""
        if not target_attr:
            return
        try:
            nch = cmds.attributeQuery(target_attr, node=material, numberOfChildren=True)
            if nch:
                self._connect_scalar_to_color(src_plug, material, target_attr)
            else:
                cmds.connectAttr(src_plug, f"{material}.{target_attr}", force=True)
        except Exception:
            try:
                cmds.connectAttr(src_plug, f"{material}.{target_attr}", force=True)
            except Exception:
                pass

    def _connect_texture(self, material, texture_type, file_node, rules, channel_pref):
        """
        Wire the file_node to the material according to rules and channel preference.
        Handles color/float, opacity, and special cases are handled elsewhere.
        """
        attr = rules.get("attr")
        kind = rules.get("kind")

        if kind == "color":
            # If user picked a single channel, replicate it to RGB; otherwise use outColor.
            if channel_pref == "A":
                self._connect_scalar_to_color(f"{file_node}.outAlpha", material, attr)
            elif channel_pref in ("R", "G", "B"):
                ch = {"R": "outColorR", "G": "outColorG", "B": "outColorB"}[channel_pref]
                self._connect_scalar_to_color(f"{file_node}.{ch}", material, attr)
            else:
                # full color
                try:
                    cmds.connectAttr(f"{file_node}.outColor", f"{material}.{attr}", force=True)
                except Exception:
                    # fallback to scalar replicate on failure
                    self._connect_scalar_to_color(f"{file_node}.outColorR", material, attr)

        elif kind == "float":
            # Choose a single component
            src = f"{file_node}.outAlpha" if channel_pref == "A" else \
                  f"{file_node}.outColorR" if channel_pref in (None, "R") else \
                  f"{file_node}.outColorG" if channel_pref == "G" else \
                  f"{file_node}.outColorB"
            try:
                cmds.connectAttr(src, f"{material}.{attr}", force=True)
            except Exception:
                pass

    def _import_one_type(self, material, texture_type, file_path):
        """
        Import/connect a single texture type for a single material using TEXTURE_RULES,
        honoring UDIM checkbox and channel preferences. Creates special networks for
        'normal' and 'displacement'. Also sets per-type file node flags like alphaIsLuminance.
        """
        if texture_type not in TEXTURE_RULES:
            self._debug_print(f"[Import] No rules for texture type '{texture_type}'")
            return

        rules = TEXTURE_RULES[texture_type]
        # Get colorspace from combobox, or use default from rules
        colorspace_combo = self.ui_elements.get(f"{texture_type}ColorspaceComboBox")
        if colorspace_combo:
            selected_colorspace = colorspace_combo.currentText()
            if selected_colorspace and selected_colorspace != "default":
                color_space = selected_colorspace
            else:
                color_space = rules["colorSpace"]  # Use default from rules
        else:
            color_space = rules["colorSpace"]  # Fallback to default
        kind = rules["kind"]

        mapping = self._resolve_texture_mapping(material, texture_type)
        if mapping.get("warning"):
            self._record_import_warning(mapping["warning"])
        if mapping.get("skip"):
            return

        target_attr = mapping.get("target_attr")
        opacity_mode = mapping.get("opacity_mode")
        normal_utility = mapping.get("normal_utility") or "aiNormalMap"

        # Create or reuse file node
        file_node, path_set = self._ensure_file_node(material, texture_type, file_path, color_space, self.use_udim)

        # --- File node flags by type/kind ---
        try:
            # Default all linear/float maps to alphaIsLuminance = True so alpha works as luminance if needed
            if kind in ("float", "displacement") or texture_type == "opacity":
                if cmds.attributeQuery("alphaIsLuminance", node=file_node, exists=True):
                    cmds.setAttr(f"{file_node}.alphaIsLuminance", 1)
            # Normal maps explicitly should NOT use alpha-as-luminance
            if kind == "normal":
                if cmds.attributeQuery("alphaIsLuminance", node=file_node, exists=True):
                    cmds.setAttr(f"{file_node}.alphaIsLuminance", 0)
        except Exception:
            pass

        # --- Special: NORMAL ---
        if kind == "normal":
            nn = self._ensure_normal_map_for_mapping(material, normal_utility)
            self._connect_file_to_normal_utility(file_node, nn, normal_utility)
            self._debug_print(f"[Import] {texture_type}: {file_node} -> {nn} -> {material}.normalCamera")
            return

        # --- Special: DISPLACEMENT ---
        if kind == "displacement":
            disp_node, sg = self._ensure_displacement_network(material)
            ch_pref = self._get_channel_selection(texture_type)  # will default to "A"
            src = f"{file_node}.outAlpha" if ch_pref == "A" else \
                f"{file_node}.outColorR" if ch_pref == "R" else \
                    f"{file_node}.outColorG" if ch_pref == "G" else \
                        f"{file_node}.outColorB"
            try:
                if not cmds.isConnected(src, f"{disp_node}.displacement"):
                    incoming = cmds.listConnections(f"{disp_node}.displacement", plugs=True) or []
                    for plug in incoming:
                        try:
                            cmds.disconnectAttr(plug, f"{disp_node}.displacement")
                        except Exception:
                            pass
                    cmds.connectAttr(src, f"{disp_node}.displacement", force=True)
            except Exception:
                pass
            self._debug_print(f"[Import] displacement: {file_node} -> {disp_node}.displacement (SG={sg})")
            return

        # --- Regular color/float connections ---
        ch_pref = self._get_channel_selection(texture_type)
        tname = target_attr

        if kind == "color" and opacity_mode == "reverse_to_transparency":
            self._connect_file_to_reversed_transparency(file_node, material, ch_pref)
            self._debug_print(
                f"[Import] {texture_type}: {file_node} -> reverse -> {material}.transparency"
            )
            return

        if kind == "color":
            # Opacity is "color" in rules but we prefer alpha by default;
            # _get_channel_selection() will return "A" by default for opacity.
            if ch_pref == "A":
                self._connect_scalar_to_color(f"{file_node}.outAlpha", material, tname)
            elif ch_pref in ("R", "G", "B"):
                ch_src = {"R": "outColorR", "G": "outColorG", "B": "outColorB"}[ch_pref]
                self._connect_scalar_to_color(f"{file_node}.{ch_src}", material, tname)
            else:
                try:
                    cmds.connectAttr(f"{file_node}.outColor", f"{material}.{tname}", force=True)
                except Exception:
                    # fallback replicate from R
                    self._connect_scalar_to_color(f"{file_node}.outColorR", material, tname)
        else:
            # kind == "float": default prefers Alpha; _get_channel_selection() already did the defaulting
            src = f"{file_node}.outAlpha" if ch_pref == "A" else \
                f"{file_node}.outColorR" if ch_pref == "R" else \
                    f"{file_node}.outColorG" if ch_pref == "G" else \
                        f"{file_node}.outColorB"
            self._connect_float_source_to_attr(src, material, tname)

        self._debug_print(f"[Import] {texture_type}: {file_node} -> {material}.{tname}")

    def _import_packed_texture(self, file_path, assignments, material=None):
        """
        Import a packed texture with multiple attribute assignments and channel selections.
        assignments: [{"attribute": "roughness", "channel": "b"}, {"attribute": "metallic", "channel": "g"}, ...]
        If material is None, uses the material combo box current text.
        """
        if not assignments:
            return
        
        if material is None:
            mat_combo = self._get_widget("materialComboBox", QtWidgets.QComboBox)
            material = mat_combo.currentText() if mat_combo else ""
        if not material:
            self._debug_print("[Import] No material selected for packed texture import")
            return
        
        # Extract texture filename for node naming (use base name without extension)
        file_dir, file_name = os.path.split(file_path)
        file_base, file_ext = os.path.splitext(file_name)
        # Clean the filename for Maya node naming
        safe_texture_name = re.sub(r"[^A-Za-z0-9_]", "_", file_base)
        safe_mat = re.sub(r"[^A-Za-z0-9_]", "_", material)
        file_node_name = f"{safe_mat}_{safe_texture_name}_file"
        if not cmds.objExists(file_node_name):
            file_node_name = cmds.shadingNode("file", asTexture=True, name=file_node_name)
        
        # Get colorspace from first assignment (or use default)
        colorspace = assignments[0].get("colorspace", "default") if assignments else "default"
        if colorspace == "-colorspace-" or not colorspace:
            colorspace = "default"
        
        # --- Color management overrides (make node respect our explicit colorSpace) ---
        try:
            if cmds.attributeQuery("ignoreColorSpaceFileRules", node=file_node_name, exists=True):
                cmds.setAttr(f"{file_node_name}.ignoreColorSpaceFileRules", 1)
        except Exception as e:
            self._debug_print(f"[ColorSpace] ignoreColorSpaceFileRules set failed on {file_node_name}: {e}")
        
        try:
            if cmds.attributeQuery("useDefaultColorSpace", node=file_node_name, exists=True):
                cmds.setAttr(f"{file_node_name}.useDefaultColorSpace", 0)
        except Exception as e:
            self._debug_print(f"[ColorSpace] useDefaultColorSpace set failed on {file_node_name}: {e}")
        
        try:
            if cmds.attributeQuery("colorSpace", node=file_node_name, exists=True) and colorspace:
                cmds.setAttr(f"{file_node_name}.colorSpace", colorspace, type="string")
                self._debug_print(f"[ColorSpace] {file_node_name}.colorSpace -> '{colorspace}'")
        except Exception as e:
            self._debug_print(f"[ColorSpace] Failed to set {file_node_name}.colorSpace='{colorspace}': {e}")
        
        # --- UDIM handling ---
        path_to_set = file_path
        
        if self.use_udim:
            # Replace UDIM digits with <UDIM> if detected
            udim_regex = re.compile(r"10\d{2}")
            if udim_regex.search(file_name):
                path_to_set = os.path.join(file_dir, udim_regex.sub("<UDIM>", file_name))
                try:
                    if cmds.attributeQuery("uvTilingMode", node=file_node_name, exists=True):
                        cmds.setAttr(f"{file_node_name}.uvTilingMode", 3)  # 3 = UDIM
                        self._debug_print(f"[UDIM] Set uvTilingMode=3 (UDIM) on {file_node_name}")
                except Exception as e:
                    self._debug_print(f"[UDIM] Failed setting uvTilingMode on {file_node_name}: {e}")
        else:
            # When UDIM is turned off, replace <UDIM> with the actual UDIM number from the original path
            if "<UDIM>" in path_to_set:
                udim_match = re.search(r"10\d{2}", os.path.basename(file_path))
                if udim_match:
                    udim_number = udim_match.group(0)
                    path_to_set = path_to_set.replace("<UDIM>", udim_number)
                    self._debug_print(f"[UDIM] Replaced <UDIM> with {udim_number} in path: {path_to_set}")
            
            try:
                if cmds.attributeQuery("uvTilingMode", node=file_node_name, exists=True):
                    cmds.setAttr(f"{file_node_name}.uvTilingMode", 0)  # 0 = off/single
            except Exception as e:
                self._debug_print(f"[UDIM] Failed disabling uvTilingMode on {file_node_name}: {e}")
        
        # Set file path (pattern or single)
        try:
            cmds.setAttr(f"{file_node_name}.fileTextureName", path_to_set.replace("\\", "/"), type="string")
            self._debug_print(f"[FilePath] Set {file_node_name}.fileTextureName -> '{path_to_set}'")
        except Exception as e:
            self._debug_print(f"[FilePath] Failed to set fileTextureName on {file_node_name}: {e}")
            return
        
        # Create and connect place2dTexture node (like Maya does automatically)
        place2d_name = f"{file_node_name}_place2dTexture"
        if not cmds.objExists(place2d_name):
            place2d_name = cmds.shadingNode("place2dTexture", asUtility=True, name=place2d_name)
            self._debug_print(f"[Place2D] Created {place2d_name} for {file_node_name}")
        
        # Connect place2dTexture to file node (standard connections)
        try:
            if not cmds.isConnected(f"{place2d_name}.outUV", f"{file_node_name}.uvCoord"):
                cmds.connectAttr(f"{place2d_name}.outUV", f"{file_node_name}.uvCoord", force=True)
            if not cmds.isConnected(f"{place2d_name}.outUvFilterSize", f"{file_node_name}.uvFilterSize"):
                cmds.connectAttr(f"{place2d_name}.outUvFilterSize", f"{file_node_name}.uvFilterSize", force=True)
            self._debug_print(f"[Place2D] Connected {place2d_name} to {file_node_name}")
        except Exception as e:
            self._debug_print(f"[Place2D] Failed to connect {place2d_name} to {file_node_name}: {e}")
        
        # Process each assignment
        for assignment in assignments:
            texture_type = assignment.get("attribute")
            channel = assignment.get("channel")
            
            if not texture_type or texture_type not in TEXTURE_RULES:
                continue
            
            rules = TEXTURE_RULES[texture_type]
            kind = rules["kind"]

            mapping = self._resolve_texture_mapping(material, texture_type)
            if mapping.get("warning"):
                self._record_import_warning(mapping["warning"])
            if mapping.get("skip"):
                continue

            target_attr = mapping.get("target_attr")
            opacity_mode = mapping.get("opacity_mode")
            normal_utility = mapping.get("normal_utility") or "aiNormalMap"

            # Determine source channel (JSON / UI may use lower case; match _import_one_type_with_channel)
            if channel:
                cl = channel.lower()
                if cl == "a":
                    src = f"{file_node_name}.outAlpha"
                elif cl == "r":
                    src = f"{file_node_name}.outColorR"
                elif cl == "g":
                    src = f"{file_node_name}.outColorG"
                elif cl == "b":
                    src = f"{file_node_name}.outColorB"
                else:
                    src = f"{file_node_name}.outColorR"
            else:
                ch_pref = self._default_channel_for_type(texture_type)
                if ch_pref == "A":
                    src = f"{file_node_name}.outAlpha"
                elif ch_pref == "R":
                    src = f"{file_node_name}.outColorR"
                elif ch_pref == "G":
                    src = f"{file_node_name}.outColorG"
                elif ch_pref == "B":
                    src = f"{file_node_name}.outColorB"
                else:
                    src = f"{file_node_name}.outColorR"

            # Connect based on kind
            if kind == "normal":
                nn = self._ensure_normal_map_for_mapping(material, normal_utility)
                self._connect_file_to_normal_utility(file_node_name, nn, normal_utility)
            elif kind == "displacement":
                disp_node, sg = self._ensure_displacement_network(material)
                try:
                    incoming = cmds.listConnections(f"{disp_node}.displacement", plugs=True) or []
                    for plug in incoming:
                        try:
                            cmds.disconnectAttr(plug, f"{disp_node}.displacement")
                        except Exception:
                            pass
                    cmds.connectAttr(src, f"{disp_node}.displacement", force=True)
                    self._debug_print(
                        f"[Import] Packed displacement: {file_node_name}.{src} -> "
                        f"{disp_node}.displacement (SG={sg})"
                    )
                except Exception as e:
                    self._debug_print(f"[Import] Failed to connect displacement: {e}")
            elif kind == "color" and opacity_mode == "reverse_to_transparency":
                self._connect_src_to_reversed_transparency(src, material)
            elif kind == "color":
                self._connect_scalar_to_color(src, material, target_attr)
            elif kind == "float":
                self._connect_float_source_to_attr(src, material, target_attr)
            
            self._debug_print(
                f"[Import] Packed texture: {file_node_name}.{src} -> {material}.{target_attr} "
                f"(channel={channel or 'default'}, kind={kind})"
            )

    def open_texture_importer_settings(self):
        if not hasattr(self, "texture_importer_settings_ui") or self.texture_importer_settings_ui is None:
            self.texture_importer_settings_ui = TextureImporterSettingsUI(parent=self)
        else:
            # Ensure we re-read the latest settings off disk each time we open
            self.texture_importer_settings_ui.reload_from_disk()
        self.texture_importer_settings_ui.show()
        self.texture_importer_settings_ui.raise_()



    def populate_material_combo_box(self):
        """Populates the material combo box with all materials in the scene and an All Materials bulk option."""
        cb = self.ui_elements.get("materialComboBox")
        if not cb:
            return
        cb.clear()

        # Bulk folder import mode (first entry)
        cb.addItem(BULK_ALL_MATERIALS_LABEL)

        all_materials = self.get_all_materials_sorted()
        cb.addItems(all_materials)

        # Pre-select the material passed during initialization, if any
        if self.material:
            index = cb.findText(self.material)
            if index >= 0:
                cb.setCurrentIndex(index)
            else:
                # If the material is not found, default to bulk All Materials entry
                cb.setCurrentIndex(0)
        else:
            # If no material is specified, default to bulk All Materials entry
            cb.setCurrentIndex(0)

        # Make the active material name pop (same cyan/green accent as UI)
        try:
            cb.setStyleSheet(self._get_combo_stylesheet(UI_ACCENT_CYAN))
        except Exception:
            pass

    def get_all_materials_sorted(self):
        """Gets all materials in the scene, sorted alphabetically, excluding specific default materials."""
        default_materials = {'lambert1', 'standardSurface1', 'particleCloud1', 'openPBR_shader1'}
        all_materials = cmds.ls(materials=True)
        filtered_materials = sorted([mat for mat in all_materials if mat not in default_materials])
        return filtered_materials

    # def auto_populate_search_folder(self):  # REMOVED (functionality simplified)
    #     """Auto-populate functionality removed - use file dialog instead."""
    #     pass






    # <Helper to get current Maya project root>
    def _project_root(self):
        """Return the active project folder with no trailing slash, or '' if unset."""
        root = cmds.workspace(q=True, rootDirectory=True) or ""
        return root.rstrip("/\\")


    # def select_search_folder(self):  # REMOVED (functionality simplified)
    #     """Search folder functionality removed - use file dialog instead."""
    #     pass

    # def auto_set_texture(self, texture_type):  # REMOVED (functionality simplified)
    #     """Auto-search functionality removed - use file dialog instead."""
    #     pass

    # DEPRECATED: Textures folder default location — auto_populate_search_folder removed.
    # def auto_populate_search_folder(self):
    #     """Populate searchFolderLineEdit based on Settings JSON, with debug output."""
    #     search_line_edit = self.ui_elements.get("searchFolderLineEdit")
    #     if not search_line_edit:
    #         return
    #     settings = self._load_settings()
    #     mode = settings.get("default_mode", "maya_file")
    #     custom_path = settings.get("custom_path", "")
    #     use_relative = settings.get("relative", False)
    #     proj_root = self._project_root()
    #     abs_folder = ""
    #     if mode == "maya_file":
    #         scene = cmds.file(q=True, sceneName=True)
    #         if scene:
    #             abs_folder = os.path.dirname(scene)
    #     elif mode == "sourceimages":
    #         abs_folder = os.path.join(proj_root, "sourceimages") if proj_root else ""
    #     elif mode == "custom" and custom_path:
    #         resolved_path = self._resolve_custom_path_keys(custom_path)
    #         abs_folder = resolved_path if resolved_path else ""
    #     display_path = ""
    #     if use_relative and abs_folder and proj_root:
    #         try:
    #             display_path = os.path.relpath(abs_folder, proj_root)
    #         except ValueError:
    #             display_path = abs_folder
    #     search_line_edit.setText(display_path)
    #     self.search_folder_path = abs_folder

    def _project_root(self):
        """Return the active project folder with no trailing slash, or '' if unset."""
        root = cmds.workspace(q=True, rootDirectory=True) or ""
        return root.rstrip("/\\")

    def recursive_search_for_texture(self, start_folder, pattern, max_levels=3, max_subdir_depth=1):
        current_folder = start_folder
        found_files = []
        search_steps = []
        levels = 0

        while True:
            if levels >= max_levels:
                self._debug_print(f"Reached max search levels ({max_levels}); aborting search.")
                return found_files, search_steps

            self._debug_print(f"Searching level {levels} in folder: {current_folder}")
            search_steps.append(current_folder)

            base_depth = current_folder.rstrip(os.sep).count(os.sep)
            try:
                for root, dirs, files in os.walk(current_folder):
                    # prevent descending too deep
                    depth = root.count(os.sep) - base_depth
                    if depth > max_subdir_depth:
                        dirs.clear()
                        continue
                    for filename in files:
                        if pattern.lower() in filename.lower():
                            path = os.path.join(root, filename)
                            found_files.append(path)
                            self._debug_print(f"  Found file: {path}")
            except Exception as e:
                self._debug_print(f"Error scanning {current_folder}: {e}")

            if found_files:
                return found_files, search_steps

            parent_folder = os.path.dirname(current_folder)
            if parent_folder == current_folder or not parent_folder:
                break

            current_folder = parent_folder
            levels += 1
            self._debug_print(f"No matches; moving up to: {current_folder}")

        self._debug_print("Reached top of directory hierarchy; no matches found.")
        return found_files, search_steps

    def _regexes_for_keywords(self, keywords):
        """
        Build case-insensitive regexes that require a separator (start, end, dot, underscore, hyphen)
        around each keyword to avoid accidental substrings.
        """
        regs = []
        for kw in (keywords or []):
            safe = re.escape(kw)
            regs.append(re.compile(rf"(?i)(?:^|[._-]){safe}(?:$|[._-])"))
        return regs

    def _auto_find_single_type(self, start_folder, material, texture_type, max_levels, max_subdir_depth):
        """
        Order-agnostic, keyword-driven search for a single texture_type.
        Uses user keyword map (JSON). Scores candidates and returns the best path or None.
        """
        # Load keywords for texture types
        kw_map, _ = self._load_keyword_map() if hasattr(self, "_load_keyword_map") else ({}, [])
        type_keywords = kw_map.get(texture_type, [texture_type])
        type_regexes = self._regexes_for_keywords(type_keywords)

        # Material name variants (some exports use underscores)
        mat_variants = {material, material.lower(), re.sub(r"\W+", "_", material).strip("_").lower()}
        mat_regexes = self._regexes_for_keywords(list(mat_variants))

        # File extensions to consider
        exts = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".tx", ".tga", ".bmp"}

        def score_name(fname_lower):
            # Require at least one type keyword to appear
            has_type = any(r.search(fname_lower) for r in type_regexes)
            if not has_type:
                return None
            s = 0
            # Strong credit for material presence (any variant)
            if any(r.search(fname_lower) for r in mat_regexes):
                s += 10
            # Credit for 1001 tile (representative UDIM)
            if "1001" in fname_lower:
                s += 2
            # Small credit if filename contains exact texture_type string with separator
            # (helps when user didn't configure JSON keywords yet)
            basic_type_re = re.compile(rf"(?i)(?:^|[._-]){re.escape(texture_type)}(?:$|[._-])")
            if basic_type_re.search(fname_lower):
                s += 1
            return s

        best = (None, -999, 999)  # (path, score, level_penalty)
        current_folder = start_folder
        level = 0

        while True:
            if level > max_levels or not current_folder:
                break

            base_depth = current_folder.rstrip(os.sep).count(os.sep)
            self._debug_print(f"[AutoSetOne][{texture_type}] Scanning root(level {level}): {current_folder}")

            try:
                for root, dirs, files in os.walk(current_folder):
                    depth = root.count(os.sep) - base_depth
                    if depth > max_subdir_depth:
                        dirs.clear()
                        continue
                    for fn in files:
                        ext = os.path.splitext(fn)[1].lower()
                        if ext not in exts:
                            continue
                        s = score_name(fn.lower())
                        if s is None:
                            continue
                        # prefer closer levels (smaller penalty)
                        penalty = level
                        # prefer the highest score; on ties prefer lower penalty (closer), then lexicographically
                        if (s > best[1]) or (s == best[1] and penalty < best[2]) or (s == best[1] and penalty == best[2] and fn < os.path.basename(best[0] or "")):
                            best = (os.path.join(root, fn), s, penalty)
            except Exception as e:
                self._debug_print(f"[AutoSetOne][{texture_type}] Error scanning {current_folder}: {e}")

            # climb up a level
            parent = os.path.dirname(current_folder)
            if not parent or parent == current_folder:
                break
            current_folder = parent
            level += 1

        return best[0]

    def _resolve_start_dir_for_type(self, texture_type):
        """
        Resolve the most intuitive start folder for the 'Set' dialog of a given type:
          1) Directory of the currently assigned file for that type (if known),
          2) self.last_texture_dir (most recent successful import),
          3) self.search_folder_path,
          4) '' (let OS pick).
        Also attempts to use an absolute path typed directly into the line edit, if valid.
        """
        # 1) If we already stored a path for this type, use its folder
        data = self.texture_data.get(texture_type)
        if data:
            p = data.get("path")
            if p and os.path.isfile(p):
                return os.path.dirname(p)

        # Try to parse absolute path typed by user in the line edit
        le = self.ui_elements.get(f"{texture_type}LineEdit")
        if le:
            typed = le.text().strip()
            # If user pasted an absolute path, prefer that
            if typed and os.path.isabs(typed) and os.path.isfile(typed):
                return os.path.dirname(typed)

        # 2) Fallback to last known good directory
        if self.last_texture_dir and os.path.isdir(self.last_texture_dir):
            return self.last_texture_dir

        # 3) Let OS decide (search folder functionality removed)
        return ""

    def select_texture_file(self, texture_type):
        """Opens a file dialog to select a texture file and updates the corresponding line edit."""
        options = QtWidgets.QFileDialog.Options()
        start_dir = self._resolve_start_dir_for_type(texture_type)  # <-- new

        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            f"Select {texture_type} Texture",
            start_dir,
            "Image Files (*.png *.jpg *.jpeg *.tif *.tiff *.exr *.tga *.bmp)",
            options=options
        )

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

        # UDIM Detection Logic: try regex on the base name directly
        udim_pattern = self.detect_udim_pattern(file_base)
        if udim_pattern:
            udim_count = self.count_udim_tiles(file_dir, file_base, file_ext, udim_pattern)


        # Update the Line Edit (safe)
        line_edit = self._get_widget(f"{texture_type}LineEdit", QtWidgets.QLineEdit)
        if line_edit:
            if udim_count > 1 and self.use_udim:
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
        # If checkbox changed after we set earlier fields, allow reflow:
        # (safe no-op when just set above)
        self._apply_udim_display_to_lineedits()

        # Remember last directory for future Set dialogs
        if os.path.isdir(file_dir):
            self.last_texture_dir = file_dir

    def detect_udim_pattern(self, file_base):
        """
        Detects potential UDIM patterns in the file base name.

        Returns:
            str: The detected UDIM pattern (e.g., r"10\\d{2}") or None if no pattern is found.
        """
        udim_patterns = [
            r"10\d{2}",          # Standard UDIM tiles: 1001..1999 (commonly 1001..1100+)
            # Future: add MARI-style here, e.g., r"u\d+_v\d+"
        ]
        for pattern in udim_patterns:
            if re.search(pattern, file_base):
                return pattern
        return None

    def count_udim_tiles(self, file_dir, file_base, file_ext, udim_pattern):
        """
        Counts the number of UDIM tiles in the same directory that match the pattern.
        Uses a cache so all textures from the same path show the same UDIM count.

        Returns:
            int: The number of UDIM tiles found.
        """
        # Create cache key based on directory, base pattern (without UDIM), and extension
        udim_regex = re.compile(udim_pattern)
        base_without_udim = udim_regex.sub("", file_base)
        cache_key = (file_dir, base_without_udim, file_ext)
        
        # Check cache first
        if cache_key in self._udim_count_cache:
            cached_count = self._udim_count_cache[cache_key]
            self._debug_print(f"[UDIM] Using cached count {cached_count} for pattern '{base_without_udim}' in '{file_dir}'")
            return cached_count
        
        # Count tiles if not in cache
        count = 0
        if os.path.isdir(file_dir):
            for f in os.listdir(file_dir):
                if os.path.splitext(f)[1] == file_ext:
                    f_base_without_udim = udim_regex.sub("", os.path.splitext(f)[0])
                    if base_without_udim == f_base_without_udim:
                        count += 1
        
        # Store in cache
        self._udim_count_cache[cache_key] = count
        self._debug_print(f"[UDIM] Counted {count} tiles for pattern '{base_without_udim}' in '{file_dir}' (cached)")
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



    def setup_scroll_area_ui(self):
        """
        Create empty scroll area for texture entries.
        Textures are populated dynamically when selected, with unassigned at the top.
        """
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

        # Single container for all textures (unassigned will be ordered at top)
        self.textures_container = QtWidgets.QWidget()
        self.textures_layout = QtWidgets.QVBoxLayout(self.textures_container)
        self.textures_layout.setContentsMargins(0, 0, 0, 0)
        self.textures_layout.setSpacing(2)
        scroll_layout.addWidget(self.textures_container)

        # Keep old texture type containers for backward compatibility (hidden by default)
        # Standard textures container
        self.standard_textures_container = QtWidgets.QWidget()
        self.standard_textures_layout = QtWidgets.QVBoxLayout(self.standard_textures_container)
        self.standard_textures_layout.setContentsMargins(0, 0, 0, 0)
        self.standard_textures_layout.setSpacing(2)
        scroll_layout.addWidget(self.standard_textures_container)
        self.standard_textures_container.setVisible(False)  # Hidden by default

        # Advanced textures container
        self.adv_textures_container = QtWidgets.QWidget()
        self.adv_textures_layout = QtWidgets.QVBoxLayout(self.adv_textures_container)
        self.adv_textures_layout.setContentsMargins(0, 0, 0, 0)
        self.adv_textures_layout.setSpacing(0)
        scroll_layout.addWidget(self.adv_textures_container)
        self.adv_textures_container.setVisible(False)  # Hidden by default
        self.ui_elements["advTexturesContainer"] = self.adv_textures_container

        # Populate standard and advanced texture types (for backward compatibility)
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
        row_layout.setContentsMargins(1, 0, 1, 0)   # tighter margins
        row_layout.setSpacing(2)                    # tighter gaps


        # 1) Show Channels button (“+”)
        show_channels_button = QtWidgets.QPushButton("+")
        show_channels_button.setObjectName(f"{texture_type}ShowChannelsButton")
        show_channels_button.clicked.connect(partial(self.toggle_channel_container, texture_type))
        show_channels_button.setFixedWidth(22)
        show_channels_button.setMinimumHeight(20)
        row_layout.addWidget(show_channels_button)

        # 2) Label
        label = QtWidgets.QLabel(texture_type)
        label.setObjectName(f"{texture_type}Label")
        label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        # Let label grow just enough, never crop; keep it compact
        label.setMinimumWidth(110)
        label.setMinimumHeight(22)  # allow for stylesheet padding; prevents cropping
        label.setSizePolicy(QtWidgets.QSizePolicy.MinimumExpanding, QtWidgets.QSizePolicy.Fixed)
        row_layout.addWidget(label)

        # 3) Line Edit
        line_edit = QtWidgets.QLineEdit()
        line_edit.setObjectName(f"{texture_type}LineEdit")
        line_edit.setMinimumHeight(20)
        row_layout.addWidget(line_edit)

        # 4) Set Button
        set_button = QtWidgets.QPushButton()
        set_button.setObjectName(f"{texture_type}SetButton")
        set_button.setMinimumHeight(20)
        set_button.setFixedWidth(28)  # Narrower width
        # Set folder icon instead of text
        folder_icon = QtGui.QIcon(":/icons/folder_icon.png")
        set_button.setIcon(folder_icon)
        set_button.setToolTip("Set folder path")
        # Style: transparent background with 0px border
        set_button.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: 0px solid white;
                border-radius: 0px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.1);
            }
            QPushButton:pressed {
                background-color: rgba(255, 255, 255, 0.2);
            }
        """)
        row_layout.addWidget(set_button)

        # 5) Auto Button - REMOVED (functionality simplified)

        # Make the line edit take the slack space; keeps label visible without cropping
        row_layout.setStretch(0, 0)  # [+]
        row_layout.setStretch(1, 0)  # label (min-expanding)
        row_layout.setStretch(2, 1)  # line edit expands
        row_layout.setStretch(3, 0)  # [Set]

        # Add this row layout to the vertical container layout
        container_layout.addLayout(row_layout)




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
        # self.ui_elements[f"{texture_type}AutoButton"] = auto_button  # REMOVED
        self.ui_elements[f"{texture_type}ChannelsContainer"] = channels_container

        return container

    def populate_channel_container(self, container, texture_type):
        """
        Ensure the container uses a vertical layout, add a label, checkboxes,
        and wire them to be mutually exclusive. Apply per-type default selection.
        """
        # Ensure the container has a vertical layout
        if not container.layout():
            container.setLayout(QtWidgets.QVBoxLayout())
        container_layout = container.layout()
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(1)  # smaller gap between label and checkboxes

        # Add the label
        label = QtWidgets.QLabel("Select channel to connect:")
        label.setStyleSheet(channel_label_stylesheet)
        label.setMinimumHeight(18)
        label.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        container_layout.addWidget(label)

        # Add the checkboxes in a horizontal layout
        checkboxes_layout = QtWidgets.QHBoxLayout()
        checkboxes_layout.setContentsMargins(1, 0, 1, 0)
        checkboxes_layout.setSpacing(2)

        red_cb = QtWidgets.QCheckBox("R")
        green_cb = QtWidgets.QCheckBox("G")
        blue_cb = QtWidgets.QCheckBox("B")
        alpha_cb = QtWidgets.QCheckBox("A")

        for cb in (red_cb, green_cb, blue_cb, alpha_cb):
            cb.setStyleSheet(checkbox_stylesheet)
            cb.setMinimumHeight(18)
            cb.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
            checkboxes_layout.addWidget(cb)

        # Add colorspace combobox
        colorspace_label = QtWidgets.QLabel("Colorspace:")
        colorspace_label.setStyleSheet(channel_label_stylesheet)
        colorspace_label.setMinimumHeight(18)
        colorspace_label.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        checkboxes_layout.addWidget(colorspace_label)
        
        colorspace_combo = QtWidgets.QComboBox()
        colorspace_combo.addItems(["default", "sRGB", "Raw", "ACEScg"])
        colorspace_combo.setCurrentText("default")  # Default to "default"
        colorspace_combo.setStyleSheet(self._get_combo_stylesheet())
        colorspace_combo.setMinimumHeight(18)
        colorspace_combo.setMinimumWidth(80)
        colorspace_combo.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        checkboxes_layout.addWidget(colorspace_combo)

        checkboxes_layout.addStretch(1)
        container_layout.addLayout(checkboxes_layout)

        # Store references for later use
        self.ui_elements[f"{texture_type}ChannelRedCheckbox"] = red_cb
        self.ui_elements[f"{texture_type}ChannelGreenCheckbox"] = green_cb
        self.ui_elements[f"{texture_type}ChannelBlueCheckbox"] = blue_cb
        self.ui_elements[f"{texture_type}ChannelAlphaCheckbox"] = alpha_cb
        self.ui_elements[f"{texture_type}ColorspaceComboBox"] = colorspace_combo

        # Wire mutual exclusivity
        red_cb.toggled.connect(lambda state, t=texture_type: self._on_channel_checkbox_toggled(t, "R"))
        green_cb.toggled.connect(lambda state, t=texture_type: self._on_channel_checkbox_toggled(t, "G"))
        blue_cb.toggled.connect(lambda state, t=texture_type: self._on_channel_checkbox_toggled(t, "B"))
        alpha_cb.toggled.connect(lambda state, t=texture_type: self._on_channel_checkbox_toggled(t, "A"))

        # Apply default selection per type
        default_ch = self._default_channel_for_type(texture_type)
        # For color/normal textures default_ch is None: leave all unchecked
        # For linear/float-like textures default_ch == "A": check Alpha by default
        if default_ch == "A":
            alpha_cb.setChecked(True)

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

    # ---------- NEW: Unified texture selection UI functions ----------
    
    def _get_display_name(self, texture_type):
        """Helper function to get display name for texture types."""
        display_names = {
            "baseColor": "Base Color",
            "emissionClr": "Emission Color",
            "subsurfaceClr": "Subsurface Color",
            "specularClr": "Specular Color",
            "transmissionClr": "Transmission Color",
            "coatRoughness": "Coat Roughness",
        }
        if texture_type in display_names:
            return display_names[texture_type]
        result = texture_type[0].upper()
        for char in texture_type[1:]:
            if char.isupper():
                result += " " + char
            else:
                result += char
        return result

    def _populate_texture_selection_ui(self):
        """
        Populate the scroll area with texture entries from self.selected_textures.
        Clears current entries and creates new ones.
        Unassigned textures are ordered at the top.
        In All Materials bulk mode, shows the same per-file rows grouped under collapsible material headers.
        """
        # Clear existing texture entries
        self._clear_texture_entries()

        if self._is_all_materials_bulk_mode():
            self._populate_bulk_mode_scroll()
            self._update_import_button_label()
            return
        
        # Create entries for unassigned textures first (at top)
        for texture_info in self.selected_textures["unassigned"]:
            # Check if it has assignments to determine visual status
            has_assignment = any(a.get("attribute") and a.get("attribute") != "skip" 
                               for a in texture_info.get("assignments", []))
            visual_status = "assigned" if has_assignment else "unassigned"
            widget = self._create_texture_entry_widget(texture_info, visual_status)
            if widget:
                self.textures_layout.addWidget(widget)
        
        # Create entries for assigned textures (below unassigned)
        for texture_info in self.selected_textures["assigned"]:
            # Check if it has assignments to determine visual status
            has_assignment = any(a.get("attribute") and a.get("attribute") != "skip" 
                               for a in texture_info.get("assignments", []))
            visual_status = "assigned" if has_assignment else "unassigned"
            widget = self._create_texture_entry_widget(texture_info, visual_status)
            if widget:
                self.textures_layout.addWidget(widget)
        
        self._update_import_button_label()

    def _clear_texture_entries(self):
        """Clear all texture entry widgets from the UI."""
        # Clear all textures from single container
        while self.textures_layout.count():
            item = self.textures_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        # Clear widget references
        self.texture_entry_widgets.clear()

    def _create_texture_entry_widget(self, texture_info, status):
        """
        Create a texture entry widget with appropriate background color and controls.
        
        Args:
            texture_info: Dict with keys: path, name, udim_pattern, udim_count, assignments
            status: "unassigned" or "assigned"
        
        Returns:
            QWidget: The container widget for this texture entry
        """
        texture_path = texture_info.get("path")
        if not texture_path:
            return None
        
        # Create main container widget
        container = QtWidgets.QWidget()
        container_layout = QtWidgets.QVBoxLayout(container)
        container_layout.setContentsMargins(8, 8, 8, 8)  # 2px padding around each material attribute
        container_layout.setSpacing(4)
        
        # Apply border color based on status (assigned/unassigned)
        self._apply_texture_entry_style(container, status)
        
        # First row: Del button + Line edit + Colorspace combobox
        first_row = QtWidgets.QHBoxLayout()
        first_row.setContentsMargins(0, 0, 0, 0)
        first_row.setSpacing(4)
        
        # Delete button (to the left of line edit)
        del_btn = QtWidgets.QPushButton("Del")
        del_btn.setFixedWidth(30)
        del_btn.setMinimumHeight(20)
        del_btn.setStyleSheet("""
            QPushButton {
                font-family: 'Segoe UI';
                font-size: 11px;
                color: #cccccc;
                background-color: #444444;
                border: none;
                border-radius: 4px;
                padding: 2px 4px;
            }
            QPushButton:hover {
                background-color: #5a2a2a;
                border: none;
            }
            QPushButton:pressed {
                background-color: #4a1a1a;
            }
        """)
        del_btn.clicked.connect(lambda: self._delete_texture_entry(texture_path))
        first_row.addWidget(del_btn)
        
        # Texture name line edit (read-only display of filename)
        texture_name = texture_info.get("name", os.path.basename(texture_path))
        udim_count = texture_info.get("udim_count", 0)
        self._debug_print(f"[CreateWidget] Display: texture_name={texture_name}, udim_count={udim_count}, use_udim={self.use_udim}")
        
        name_line_edit = QtWidgets.QLineEdit(texture_name)
        name_line_edit.setReadOnly(True)
        name_line_edit.setStyleSheet(scroll_area_stylesheet)
        name_line_edit.setMinimumHeight(20)
        first_row.addWidget(name_line_edit)
        
        # UDIM count label (blue text)
        udim_label = QtWidgets.QLabel("")
        udim_label.setStyleSheet(udim_label_stylesheet)
        udim_label.setMinimumHeight(18)
        udim_label.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        udim_label.setVisible(False)
        first_row.addWidget(udim_label)
        
        # Folder icon button (to select texture file for this entry)
        folder_btn = QtWidgets.QPushButton()
        folder_btn.setFixedWidth(28)
        folder_btn.setMinimumHeight(20)
        folder_icon = QtGui.QIcon(":/icons/folder_icon.png")
        folder_btn.setIcon(folder_icon)
        folder_btn.setToolTip("Select texture file for this entry")
        folder_btn.setStyleSheet("""
            QPushButton {
                font-family: 'Segoe UI';
                font-size: 12px;
                color: #ffffff;
                background-color: transparent;
                border: none;
                border-radius: 8px;
                padding: 0px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.1);
            }
            QPushButton:pressed {
                background-color: rgba(255, 255, 255, 0.2);
            }
        """)
        folder_btn.clicked.connect(lambda: self._select_texture_for_entry(texture_path))
        first_row.addWidget(folder_btn)
        
        # Colorspace combobox (updates based on attribute selection, disable wheel scrolling)
        class NoWheelComboBox(QtWidgets.QComboBox):
            def wheelEvent(self, event):
                event.ignore()
        
        colorspace_combo = NoWheelComboBox()
        colorspace_combo.addItem("-colorspace-", "default")
        colorspace_combo.addItems(["sRGB", "Raw", "ACEScg"])
        # Set default colorspace based on first assignment if available
        default_colorspace = "-colorspace-"
        assignments = texture_info.get("assignments", [])
        if assignments and assignments[0].get("attribute"):
            attr = assignments[0].get("attribute")
            if attr and attr != "skip" and attr in TEXTURE_RULES:
                default_colorspace = TEXTURE_RULES[attr].get("colorSpace", "-colorspace-")
        colorspace_combo.setCurrentText(default_colorspace)
        
        def update_colorspace_combo_color():
            selected_text = colorspace_combo.currentText()
            color = "#b0b0b0" if selected_text == "-colorspace-" else "#00f7c8"
            colorspace_combo.setStyleSheet(self._get_combo_stylesheet(color))
        
        # Apply initial color state
        update_colorspace_combo_color()
        
        # Connect signal to update color when selection changes
        colorspace_combo.currentIndexChanged.connect(lambda: update_colorspace_combo_color())
        colorspace_combo.currentTextChanged.connect(lambda: update_colorspace_combo_color())
        
        colorspace_combo.setMinimumHeight(20)
        colorspace_combo.setMinimumWidth(80)
        first_row.addWidget(colorspace_combo)
        
        # Store colorspace combo reference
        container_layout.addLayout(first_row)
        
        # Get assignments (packed textures may have multiple)
        assignments = texture_info.get("assignments", [])
        if not assignments:
            # Add one empty assignment
            assignments = [{"attribute": None, "channel": None, "colorspace": "default"}]
        
        # Store widget references for this texture
        widget_info = {
            "widget": container,
            "line_edit": name_line_edit,
            "udim_label": udim_label,
            "del_button": del_btn,
            "colorspace_combo": colorspace_combo,
            "attribute_combos": [],
            "plus_button": None,
            "channel_checkboxes": [],  # List of dicts with R/G/B/A checkboxes per assignment
            "status": status
        }
        
        # Create assignment rows (one per assignment, or one empty row)
        self._debug_print(f"[CreateWidget] Texture: {texture_name}, assignments count: {len(assignments)}")
        for idx, assignment in enumerate(assignments):
            self._debug_print(f"[CreateWidget]   Assignment {idx}: {assignment}")
            self._debug_print(f"[CreateWidget] Creating {len(assignments)} assignment rows for {texture_path}")
            self._debug_print(f"[CreateWidget] Assignment {idx}: attribute={assignment.get('attribute')}, channel={assignment.get('channel')}")
            assignment_widget = self._create_assignment_row(
                container_layout, texture_path, assignment, idx == 0, len(assignments) > 1, widget_info
            )
            if assignment_widget:
                widget_info["attribute_combos"].append(assignment_widget.get("combo"))
                widget_info["channel_checkboxes"].append(assignment_widget.get("checkboxes", {}))
                if idx == 0:
                    widget_info["plus_button"] = assignment_widget.get("plus_button")
        
        # Store widget info
        self._update_texture_entry_widget_display(widget_info, texture_info)
        self.texture_entry_widgets[texture_path] = widget_info
        
        return container

    def _create_assignment_row(self, parent_layout, texture_path, assignment, is_first, has_multiple, widget_info):
        """
        Create a single assignment row with combobox, plus/minus buttons, and inline R/G/B/A checkboxes.
        
        Returns:
            Dict with keys: "combo", "plus_button", "minus_button", "checkboxes"
        """
        row_widget = QtWidgets.QWidget()
        row_layout = QtWidgets.QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(4)
        
        result = {}
        
        # Plus button (only on first row)
        if is_first:
            plus_btn = QtWidgets.QPushButton("+")
            plus_btn.setFixedWidth(18)
            plus_btn.setMinimumHeight(18)
            plus_btn.setStyleSheet(scroll_area_stylesheet)
            plus_btn.clicked.connect(lambda: self._add_assignment_row_to_texture(texture_path))
            row_layout.addWidget(plus_btn)
            result["plus_button"] = plus_btn
            
            # If multiple assignments, add 1px spacer then minus button
            if has_multiple:
                spacer_1px = QtWidgets.QSpacerItem(1, 1, QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
                row_layout.addItem(spacer_1px)
                
                minus_btn = QtWidgets.QPushButton("-")
                minus_btn.setFixedWidth(18)
                minus_btn.setMinimumHeight(18)
                minus_btn.setStyleSheet(scroll_area_stylesheet)
                assignment_idx = len(widget_info.get("attribute_combos", []))
                minus_btn.clicked.connect(lambda: self._remove_assignment_row_from_texture(texture_path, assignment_idx))
                row_layout.addWidget(minus_btn)
                result["minus_button"] = minus_btn
        else:
            # Spacer for alignment (same width as plus button)
            spacer = QtWidgets.QSpacerItem(22, 20, QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
            row_layout.addItem(spacer)
            
            # If multiple assignments, add more spacing then minus button (for second+ attributes)
            # ADJUST THIS VALUE TO CHANGE SPACING BEFORE MINUS BUTTON FOR 2ND+ ATTRIBUTES:
            if has_multiple:
                spacer_width = 1  # <-- CHANGE THIS VALUE (currently 8px) to adjust spacing before minus button
                spacer = QtWidgets.QSpacerItem(spacer_width, 1, QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
                row_layout.addItem(spacer)
                
                minus_btn = QtWidgets.QPushButton("-")
                minus_btn.setFixedWidth(18)
                minus_btn.setMinimumHeight(18)
                minus_btn.setStyleSheet(scroll_area_stylesheet)
                assignment_idx = len(widget_info.get("attribute_combos", []))
                minus_btn.clicked.connect(lambda: self._remove_assignment_row_from_texture(texture_path, assignment_idx))
                row_layout.addWidget(minus_btn)
                result["minus_button"] = minus_btn
        
        # Attribute combobox (disable wheel scrolling)
        class NoWheelComboBox(QtWidgets.QComboBox):
            def wheelEvent(self, event):
                event.ignore()
        
        attribute_combo = NoWheelComboBox()
        attribute_combo.setStyleSheet(self._get_combo_stylesheet())
        attribute_combo.setMinimumHeight(20)
        attribute_combo.setMinimumWidth(120)
        
        # Add "-skip import-" as first item
        attribute_combo.addItem("-skip import-", "skip")
        
        # Add all texture types
        for ttype in ALL_TEXTURE_TYPES:
            display = self._get_display_name(ttype)
            attribute_combo.addItem(display, ttype)
        
        # Set current selection based on assignment
        attribute = assignment.get("attribute")
        if attribute:
            for i in range(attribute_combo.count()):
                if attribute_combo.itemData(i) == attribute:
                    attribute_combo.setCurrentIndex(i)
                    break
        else:
            attribute_combo.setCurrentIndex(0)

        def update_attribute_combo_color():
            selected_data = attribute_combo.currentData()
            color = "#ff3b3b" if selected_data == "skip" else "#00f7c8"
            attribute_combo.setStyleSheet(self._get_combo_stylesheet(color))
        
        # Apply initial color state
        update_attribute_combo_color()
        
        row_layout.addWidget(attribute_combo)
        result["combo"] = attribute_combo
        
        # Channel selection UI (checkboxes + label)
        channel_label = QtWidgets.QLabel("(RGB Channels)")
        channel_label.setStyleSheet(channel_label_stylesheet)
        channel_label.setMinimumHeight(18)
        channel_label.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        
        channel_names = {
            "R": "(Red Channel)",
            "G": "(Green Channel)",
            "B": "(Blue Channel)",
            "A": "(Alpha Channel)",
        }
        
        red_cb = QtWidgets.QCheckBox("R")
        green_cb = QtWidgets.QCheckBox("G")
        blue_cb = QtWidgets.QCheckBox("B")
        alpha_cb = QtWidgets.QCheckBox("A")
        checkboxes = {"R": red_cb, "G": green_cb, "B": blue_cb, "A": alpha_cb}
        
        def update_channel_label_text():
            selected_key = next((k for k, cb in checkboxes.items() if cb.isChecked()), None)
            channel_label.setText(channel_names.get(selected_key, "(RGB Channels)"))
        
        def handle_checkbox_toggle(channel_key):
            cb = checkboxes.get(channel_key)
            if not cb:
                return
            if cb.isChecked():
                for other_key, other_cb in checkboxes.items():
                    if other_key != channel_key and other_cb.isChecked():
                        other_cb.blockSignals(True)
                        other_cb.setChecked(False)
                        other_cb.blockSignals(False)
            update_channel_label_text()
            self._sync_texture_entry_to_data(texture_path)
        
        for key, cb in checkboxes.items():
            cb.setStyleSheet(checkbox_stylesheet)
            cb.setMinimumHeight(18)
            cb.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
            row_layout.addWidget(cb)
            cb.toggled.connect(lambda checked, chan_key=key: handle_checkbox_toggle(chan_key))
        
        row_layout.addSpacing(4)
        row_layout.addWidget(channel_label)
        
        result["checkboxes"] = checkboxes
        
        # Set initial enabled state based on attribute
        initial_attr = attribute_combo.itemData(attribute_combo.currentIndex())
        is_skip_initial = (initial_attr == "skip" or initial_attr is None)
        for cb in checkboxes.values():
            cb.setEnabled(not is_skip_initial)
        
        # Set channel checkbox based on assignment
        channel = assignment.get("channel")
        attribute = assignment.get("attribute")
        self._debug_print(f"[CreateRow] Creating row for attribute={attribute}, channel={channel}")
        
        if channel:
            channel_upper = channel.upper()
            if channel_upper in checkboxes:
                checkboxes[channel_upper].blockSignals(True)
                checkboxes[channel_upper].setChecked(True)
                checkboxes[channel_upper].blockSignals(False)
                self._debug_print(f"[CreateRow] Set channel {channel_upper} checkbox for {attribute}")
        elif attribute and attribute in TEXTURE_RULES:
            default_ch = self._default_channel_for_type(attribute)
            if default_ch and default_ch.upper() in checkboxes:
                checkboxes[default_ch.upper()].blockSignals(True)
                checkboxes[default_ch.upper()].setChecked(True)
                checkboxes[default_ch.upper()].blockSignals(False)
                self._debug_print(f"[CreateRow] Set default channel {default_ch.upper()} checkbox for {attribute}")
        
        update_channel_label_text()
        
        # Connect signal to update data structure and colorspace (after checkboxes exist)
        def on_attribute_changed(idx, path=texture_path):
            self._sync_texture_entry_to_data(path)
            attr = attribute_combo.itemData(idx)
            if attr and attr != "skip" and attr in TEXTURE_RULES:
                colorspace = TEXTURE_RULES[attr].get("colorSpace", "-colorspace-")
                widget_info["colorspace_combo"].setCurrentText(colorspace)
            else:
                widget_info["colorspace_combo"].setCurrentText("-colorspace-")
            
            update_attribute_combo_color()
            
            is_skip = (attr == "skip" or attr is None)
            for cb in checkboxes.values():
                cb.setEnabled(not is_skip)
                if is_skip and cb.isChecked():
                    cb.blockSignals(True)
                    cb.setChecked(False)
                    cb.blockSignals(False)
            update_channel_label_text()
        
        attribute_combo.currentIndexChanged.connect(on_attribute_changed)
        
        row_layout.addStretch(1)
        parent_layout.addWidget(row_widget)
        
        return result

    def _add_assignment_row_to_texture(self, texture_path):
        """Add another assignment row to a packed texture entry."""
        widget_info = self.texture_entry_widgets.get(texture_path)
        if not widget_info:
            return
        
        # Find texture info in selected_textures
        texture_info = None
        for status in ["unassigned", "assigned"]:
            for tex_info in self.selected_textures[status]:
                if tex_info.get("path") == texture_path:
                    texture_info = tex_info
                    break
            if texture_info:
                break
        
        if not texture_info:
            return
        
        # Add new assignment
        assignments = texture_info.get("assignments", [])
        assignments.append({"attribute": None, "channel": None, "colorspace": "default"})
        
        # Recreate UI
        self._populate_texture_selection_ui()

    def _remove_assignment_row_from_texture(self, texture_path, assignment_index):
        """Remove an assignment row from a packed texture entry."""
        # Find texture info
        texture_info = None
        for status in ["unassigned", "assigned"]:
            for tex_info in self.selected_textures[status]:
                if tex_info.get("path") == texture_path:
                    texture_info = tex_info
                    break
            if texture_info:
                break
        
        if not texture_info:
            return
        
        assignments = texture_info.get("assignments", [])
        if 0 <= assignment_index < len(assignments):
            assignments.pop(assignment_index)
            # If no assignments left, add one empty
            if not assignments:
                assignments.append({"attribute": None, "channel": None, "colorspace": "default"})
            
            # Recreate UI
            self._populate_texture_selection_ui()

    def _sync_texture_entry_to_data(self, texture_path):
        """
        Read widget states and update self.selected_textures data structure.
        Called when user changes any UI element.
        """
        widget_info = self.texture_entry_widgets.get(texture_path)
        if not widget_info:
            return
        
        # Find texture info in selected_textures
        texture_info = None
        status_key = None
        for status in ["unassigned", "assigned"]:
            for idx, tex_info in enumerate(self.selected_textures[status]):
                if tex_info.get("path") == texture_path:
                    texture_info = tex_info
                    status_key = status
                    break
            if texture_info:
                break
        
        if not texture_info:
            return
        
        # Update assignments from UI
        assignments = []
        colorspace = widget_info["colorspace_combo"].currentText() if widget_info.get("colorspace_combo") else "default"
        
        for idx, combo in enumerate(widget_info["attribute_combos"]):
            attribute = combo.itemData(combo.currentIndex())
            checkboxes = widget_info["channel_checkboxes"][idx] if idx < len(widget_info["channel_checkboxes"]) else {}
            channel = None
            for ch_name, cb in checkboxes.items():
                if cb and cb.isChecked():
                    channel = ch_name.lower()
                    break
            assignments.append({
                "attribute": attribute,
                "channel": channel,
                "colorspace": colorspace
            })
        
        # Update texture info
        texture_info["assignments"] = assignments if assignments else [{"attribute": None, "channel": None, "colorspace": "default"}]
        
        # Update status based on assignments (but don't move between lists - just update visual)
        has_assignment = any(a.get("attribute") and a.get("attribute") != "skip" for a in texture_info["assignments"])
        
        # Update the visual status (background color) without moving the entry
        widget = widget_info.get("widget")
        if widget:
            new_status = "assigned" if has_assignment else "unassigned"
            self._apply_texture_entry_style(widget, new_status)
            widget_info["status"] = new_status
        
        # Update the status in the data structure for import purposes, but don't move between lists
        # This way the visual updates but position stays the same
        if has_assignment and status_key == "unassigned":
            # Update status in data but don't move - just mark for import
            texture_info["_visual_status"] = "assigned"
        elif not has_assignment and status_key == "assigned":
            # Update status in data but don't move - just mark for import
            texture_info["_visual_status"] = "unassigned"
        
        self._update_import_button_label()


    def _default_channel_for_type(self, texture_type):
        """
        Return the default channel for this texture type:
          - Linear/float-like textures (kind in {"float", "displacement"}) => "A"
          - Opacity maps default to "A" as well (mask behavior)
          - Color/normal textures => None (use full outColor)
        """
        if texture_type == "opacity":
            return "A"
        rules = TEXTURE_RULES.get(texture_type, {})
        kind = rules.get("kind")
        if kind in ("float", "displacement"):
            return "A"
        return None

    def _on_channel_checkbox_toggled(self, texture_type, which):
        """
        Enforce single selection among RGBA for this texture_type.
        If one is checked, uncheck the others. If it is unchecked manually,
        we allow 'none' (the default behavior will apply on connect).
        """
        red_cb = self.ui_elements.get(f"{texture_type}ChannelRedCheckbox")
        green_cb = self.ui_elements.get(f"{texture_type}ChannelGreenCheckbox")
        blue_cb = self.ui_elements.get(f"{texture_type}ChannelBlueCheckbox")
        alpha_cb = self.ui_elements.get(f"{texture_type}ChannelAlphaCheckbox")

        boxes = {
            "R": red_cb,
            "G": green_cb,
            "B": blue_cb,
            "A": alpha_cb,
        }
        picked = boxes.get(which)
        if not picked:
            return

        try:
            if picked.isChecked():
                # uncheck all others
                for k, cb in boxes.items():
                    if cb and k != which:
                        cb.blockSignals(True)
                        cb.setChecked(False)
                        cb.blockSignals(False)
        except RuntimeError:
            # Widget might be gone; ignore safely
            pass

    def _delete_texture_entry(self, texture_path):
        """Delete a texture entry from the list entirely."""
        # Remove from selected_textures
        for status in ["unassigned", "assigned"]:
            for tex_info in self.selected_textures[status][:]:
                if tex_info.get("path") == texture_path:
                    self.selected_textures[status].remove(tex_info)
                    break
        
        # Remove widget reference
        if texture_path in self.texture_entry_widgets:
            widget_info = self.texture_entry_widgets[texture_path]
            widget = widget_info.get("widget")
            if widget:
                widget.deleteLater()
            del self.texture_entry_widgets[texture_path]
        
        # Recreate UI to reflect changes
        self._populate_texture_selection_ui()
        
        self._debug_print(f"[Delete] Deleted texture entry: {os.path.basename(texture_path)}")

    def _select_texture_for_entry(self, texture_path):
        """Open file dialog to select a new texture file for this entry."""
        # Get the directory of the current texture as starting point
        current_dir = os.path.dirname(texture_path) if texture_path else ""
        
        options = QtWidgets.QFileDialog.Options()
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select Texture File",
            current_dir,
            "Image Files (*.png *.jpg *.jpeg *.tif *.tiff *.exr *.tga *.bmp)",
            options=options
        )
        
        if not file_path:
            return
        
        # Find the texture entry in selected_textures
        texture_info = None
        status_key = None
        for status in ["unassigned", "assigned"]:
            for idx, tex_info in enumerate(self.selected_textures[status]):
                if tex_info.get("path") == texture_path:
                    texture_info = tex_info
                    status_key = status
                    break
            if texture_info:
                break
        
        if not texture_info:
            return
        
        # Get current assignments to preserve them
        assignments = texture_info.get("assignments", [])
        if not assignments:
            assignments = [{"attribute": None, "channel": None, "colorspace": "-colorspace-"}]
        
        # Build new texture info with the new path
        new_texture_info = self._build_texture_info(file_path, {
            "status": texture_info.get("status", "unmatched"),
            "assignments": assignments
        })
        
        # Replace the old texture info with the new one
        for status in ["unassigned", "assigned"]:
            for idx, tex_info in enumerate(self.selected_textures[status]):
                if tex_info.get("path") == texture_path:
                    self.selected_textures[status][idx] = new_texture_info
                    break
        
        # Refresh UI
        self._populate_texture_selection_ui()
        self._debug_print(f"[SelectTexture] Updated texture entry: {os.path.basename(file_path)}")

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
    font-family: 'Segoe UI';
    font-size: 10px;              /* Smaller text */
    color: #dddddd;               /* Neutral text when unchecked */
    background-color: transparent;
    border: none;
    border-radius: 0px;
    padding: 1px 4px;             /* Reduced padding */
    margin: 0px;                  /* No margin */
}
QCheckBox:checked {
    color: #00f7c8;               /* Highlight color only when checked */
    font-weight: bold;            /* Make text bold when checked */
}
QCheckBox::indicator {
    width: 10px;                  /* Smaller checkbox */
    height: 10px;
    border: 1px solid #555555;    /* Dark grey border for unchecked */
    border-radius: 2px;
    background-color: #2b2b2b;
}
QCheckBox::indicator:checked {
    background-color: #ffffff;
    border: 1px solid #ffffff;    /* White border for checked */
}
QCheckBox::indicator:unchecked {
    background-color: #2b2b2b;
    border: 1px solid #555555;    /* Dark grey border for unchecked */
}
QCheckBox::indicator:checked:hover,
QCheckBox::indicator:unchecked:hover {
    border: 1px solid #ffffff;    /* White border on hover */
}
QCheckBox::indicator:checked:pressed,
QCheckBox::indicator:unchecked:pressed {
    background-color: #ffffff;
    border: 1px solid #ffffff;
}
/* Disabled state */
QCheckBox:disabled {
    color: #666666;
    background-color: transparent;
    border-radius: 0px;
    padding: 1px 4px;
}
QCheckBox:disabled::indicator {
    background-color: transparent;
    border: none;
}
"""

label_stylesheet = """
    QLabel {
        font-family: 'Segoe UI';
        font-size: 14px;  /* Same larger font size as QLineEdit */
        color: #ffffff;
        background-color: #444444;  /* Matching background color */
        border: 2px solid #444444;
        border-radius: 8px;
        padding: 4px 5px;
}

    """

channel_label_stylesheet = """
QLabel {
    font-family: 'Segoe UI';
    font-size: 10px;
    font-style: italic;
    color: #9da3a8;
    background-color: transparent;
    border: none;
    padding: 0px 4px;
}
"""

udim_label_stylesheet = """
QLabel {
    font-family: 'Segoe UI';
    font-size: 11px;
    color: #00f7c8;
    background-color: transparent;
    border: none;
    padding: 0px 4px;
}
"""

# Use default combobox styling (no custom stylesheet)
combo_stylesheet_template = """
QComboBox {{
    font-family: 'Segoe UI';
    font-size: 12px;
    color: {color};
    background-color: #444444;
    border: none;
    border-radius: 4px;
    padding: 2px 6px;
}}
QComboBox QAbstractItemView {{
    background-color: #1e1e1e;
    border: 1px solid #3a3a3a;
    color: {color};
    selection-background-color: #2f3f3f;
    selection-color: #ffffff;
}}
"""

# Main stylesheet for dialogs (matching texture importer style)
main_stylesheet = """
QWidget {
    background-color: #555555;
    font-family: 'Segoe UI';
    font-size: 14px;
    color: #ffffff;
}

/* Scroll area styling */
QScrollArea {
    background-color: #3a3a3a;
    border: none;
    border-radius: 8px;
}

QScrollArea QWidget {
    background-color: #3a3a3a;
}

/* Scrollbar styling */
QScrollBar:vertical {
    background-color: #555555;
    width: 12px;
    border: none;
}

QScrollBar::handle:vertical {
    background-color: #666666;
    border-radius: 6px;
    min-height: 20px;
}

QScrollBar::handle:vertical:hover {
    background-color: #777777;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

/* Buttons */
QPushButton {
    font-family: 'Segoe UI';
    font-size: 13px;
    color: #ffffff;
    background-color: #666666;
    border: 2px solid #444444;
    border-radius: 6px;
    padding: 4px 10px;
}

QPushButton:hover {
    background-color: #888888;
}

QPushButton:pressed {
    background-color: #1a1a1a;
}

QPushButton:disabled {
    color: #666666;
    background-color: #4a4a4a;
}
"""

scroll_area_stylesheet = """
/* Buttons */
QPushButton {
    font-family: 'Segoe UI';  /* Sets the font to Segoe UI */
    font-size: 11px;          /* Slightly smaller for inline controls */
    color: #ffffff;           /* White text color */
    background-color: #444444;/* Darker background color */
    border: none;             /* No border */
    border-radius: 4px;       /* Rounded corners */
    padding: 0px 4px;         /* Tighter padding for compact buttons */
}
QPushButton:hover {
    background-color: #555555;  /* Slightly lighter background on hover */
}
QPushButton:pressed {
    background-color: #2a2a2a;  /* Darker background when pressed */
}
QPushButton:disabled {
    color: #666666;             /* Muted text to indicate disabled */
    border: none;               /* No border */
    background-color: #3a3a3a;  /* Lighter grey background color for disabled state */
}

/* Line edits */
QLineEdit {
    font-family: 'Segoe UI';
    font-size: 14px;
    color: #ffffff;
    background-color: #333333;  /* Retaining the original background color */
    border: none;               /* No border */
    border-radius: 8px;
    padding: 2px 3px;
}
QLineEdit:hover {
    background-color: #222222;
}
QLineEdit:focus {
    border: none;               /* No border */
    background-color: #333333;  /* fixed typo from #44444 */
}

/* Labels */
QLabel {
    font-family: 'Segoe UI';
    font-size: 14px;            /* Larger to pair with QLineEdit */
    color: #ffffff;
    background-color: #444444;
    border: 2px solid #444444;
    border-radius: 8px;
    padding: 0px 0px;
    margin: -5px -5px;
}

"""


class BulkImportReviewDialog(QtWidgets.QDialog):
    """
    Confirm bulk import from the main texture list (reflects current UI: slots, skip, channels).
    Material names use the tool accent blue; rows with nothing to import (skip / no slot) are red.
    """

    def __init__(self, parent):
        super(BulkImportReviewDialog, self).__init__(parent)
        self.setWindowTitle("Review bulk texture import")
        self.setModal(True)
        self.resize(760, 620)

        main = QtWidgets.QVBoxLayout(self)
        main.setContentsMargins(10, 10, 10, 10)
        main.setSpacing(8)

        title = QtWidgets.QLabel(
            "Review assignments from the list above (edits in the main window are included). "
            "Red rows are skipped or have no import target. Confirm to import onto scene materials."
        )
        title.setWordWrap(True)
        title.setStyleSheet(
            "font-family: 'Segoe UI'; font-size: 14px; color: #d6d6d6;"
        )
        main.addWidget(title)

        tree = QtWidgets.QTreeWidget()
        tree.setAlternatingRowColors(True)
        tree.setHeaderLabels(["Slot / packed", "File"])
        tree.setStyleSheet(scroll_area_stylesheet)
        tree.header().setStretchLastSection(True)

        tool = parent
        bulk_items = []
        for status in ("unassigned", "assigned"):
            for ti in tool.selected_textures.get(status, []):
                if ti.get("target_material"):
                    bulk_items.append(ti)

        by_mat = {}
        for ti in bulk_items:
            by_mat.setdefault(ti["target_material"], []).append(ti)
        for m in by_mat:
            by_mat[m].sort(key=lambda x: os.path.basename(x.get("path") or "").lower())

        mat_header_brush = QtGui.QBrush(QtGui.QColor(BULK_MATERIAL_HEADER_COLOR))
        skip_red = QtGui.QBrush(QtGui.QColor("#ff3b3b"))

        for mat in sorted(by_mat.keys(), key=lambda s: s.lower()):
            parent_it = QtWidgets.QTreeWidgetItem([mat, ""])
            parent_it.setFirstColumnSpanned(False)
            font = parent_it.font(0)
            font.setBold(True)
            parent_it.setFont(0, font)
            parent_it.setForeground(0, mat_header_brush)
            tree.addTopLevelItem(parent_it)

            for ti in by_mat[mat]:
                assigns = ti.get("assignments", [])
                active = [
                    a for a in assigns
                    if a.get("attribute") and a.get("attribute") != "skip"
                ]
                skipped_only = not active
                if skipped_only:
                    label = "— skip import —"
                elif len(active) > 1:
                    label = "Packed (%s)" % ", ".join(
                        TextureSearchNamesUI.get_display_name(a["attribute"])
                        for a in active
                    )
                else:
                    label = TextureSearchNamesUI.get_display_name(active[0]["attribute"])
                path = ti.get("path") or ""
                child = QtWidgets.QTreeWidgetItem(
                    parent_it,
                    [label, os.path.basename(path)],
                )
                if skipped_only:
                    for col in (0, 1):
                        child.setForeground(col, skip_red)

            parent_it.setExpanded(True)

        main.addWidget(tree, 1)

        btns = QtWidgets.QHBoxLayout()
        btns.addStretch(1)
        import_btn = QtWidgets.QPushButton("Import Textures")
        cancel_btn = QtWidgets.QPushButton("Cancel")
        btns.addWidget(import_btn)
        btns.addWidget(cancel_btn)
        main.addLayout(btns)

        import_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)


class PreviewImportDialog(QtWidgets.QDialog):
    """
    Simple scrollable preview of what will be imported:
      - Material name (bold)
      - Under it: texture type : filename (non-bold)
    Two buttons: Import (accept) / Cancel (reject)
    """
    def __init__(self, parent, bulk_map):
        super(PreviewImportDialog, self).__init__(parent)
        self.setWindowTitle("Preview Import Textures")
        self.setModal(True)
        self.resize(560, 520)

        main = QtWidgets.QVBoxLayout(self)
        main.setContentsMargins(8, 8, 8, 8)
        main.setSpacing(8)

        # Title
        title = QtWidgets.QLabel("The following textures will be imported:")
        title.setStyleSheet("font-family: 'Segoe UI'; font-size: 16px; color: #d6d6d6;")
        main.addWidget(title)

        # Scroll area
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(scroll_area_stylesheet)  # reuse app style
        main.addWidget(scroll, 1)

        content = QtWidgets.QWidget()
        scroll.setWidget(content)
        v = QtWidgets.QVBoxLayout(content)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(6)

        # Populate list
        mats_sorted = sorted(bulk_map.keys(), key=lambda s: s.lower())
        for mat in mats_sorted:
            type_map = bulk_map.get(mat, {})
            # Only show materials that have at least one match
            if not any(type_map.values()):
                continue

            # Material header
            mat_lbl = QtWidgets.QLabel(mat)
            mat_lbl.setStyleSheet("font-family: 'Segoe UI'; font-size: 15px; font-weight: 600; color: #ffffff;")
            v.addWidget(mat_lbl)

            # Types under it
            for ttype in ALL_TEXTURE_TYPES:
                p = type_map.get(ttype)
                if not p:
                    continue
                line = QtWidgets.QLabel(f"• {ttype}: {os.path.basename(p)}")
                line.setStyleSheet("font-family: 'Segoe UI'; font-size: 13px; color: #d6d6d6;")
                v.addWidget(line)

            # small separator
            sep = QtWidgets.QFrame()
            sep.setFrameShape(QtWidgets.QFrame.HLine)
            sep.setStyleSheet("color:#444444;")
            v.addWidget(sep)

        v.addStretch(1)

        # Buttons
        btns = QtWidgets.QHBoxLayout()
        btns.addStretch(1)
        import_btn = QtWidgets.QPushButton("Import Textures")
        cancel_btn = QtWidgets.QPushButton("Cancel")
        btns.addWidget(import_btn)
        btns.addWidget(cancel_btn)
        main.addLayout(btns)

        import_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)


class TextureSearchNamesUI(QtWidgets.QWidget):
    """
    Loads textureSearchnames.ui, then dynamically builds the scroll area content
    for each texture type (label + line edit). Keywords load from user JSON, legacy
    Settings/, or texture_search_names_default.json; save writes only
    <script_dir>/settings/texture_search_names.json.
    """
    
    @staticmethod
    def get_display_name(texture_type):
        """Convert texture type key to user-friendly display name."""
        display_names = {
            "baseColor": "Base Color",
            "emissionClr": "Emission Color",
            "subsurfaceClr": "Subsurface Color",
            "specularClr": "Specular Color",
            "transmissionClr": "Transmission Color",
            "coatRoughness": "Coat Roughness",
        }
        # If we have a specific display name, use it
        if texture_type in display_names:
            return display_names[texture_type]
        # Otherwise, capitalize first letter and add spaces before capital letters
        result = texture_type[0].upper()
        for char in texture_type[1:]:
            if char.isupper():
                result += " " + char
            else:
                result += char
        return result
    
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
        
        # Initialize packed_texture_entries if not already done
        if not hasattr(self, 'packed_texture_entries'):
            self.packed_texture_entries = []

        # 3.5) Fill from saved JSON if present
        self._apply_saved_texture_names()

        # 4) Connect signals (e.g., Save button)
        self.setup_connections()
        
        # 5) Apply stylesheet matching quick_materials tool
        self._apply_matching_stylesheet()


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

        # Place the loaded UI into this widget's layout
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(2, 2, 2, 2)
        main_layout.setSpacing(2)
        main_layout.addWidget(self.ui_instance)

        # Auto-initialize all named child widgets into self.ui_elements
        self.auto_initialize_ui_elements(self.ui_instance)
        
        # Ensure title labels are centered
        title_label = self.ui_elements.get("textureImporterTitleLabel")
        if title_label:
            title_label.setAlignment(QtCore.Qt.AlignCenter | QtCore.Qt.AlignVCenter)
        desc_label = self.ui_elements.get("textureImporterTitleLabelDesc")
        if desc_label:
            desc_label.setAlignment(QtCore.Qt.AlignCenter | QtCore.Qt.AlignVCenter)
            desc_label.setText("These strings will be used to auto assign textures to the matching material attribute\n(Separate by commas, not case sensitive)")

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
        content_layout.setContentsMargins(3, 3, 3, 3)
        content_layout.setSpacing(2)  # Reduced spacing for tighter layout
        content_layout.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)

        # 4) Frame around texture attributes section
        attributes_frame = QtWidgets.QFrame()
        attributes_frame.setFrameShape(QtWidgets.QFrame.StyledPanel)
        attributes_frame.setStyleSheet("""
            QFrame {
                background-color: #3a3a3a;
                border: 1px solid #555555;
                border-radius: 6px;
                padding: 4px;
            }
        """)
        attributes_layout = QtWidgets.QVBoxLayout(attributes_frame)
        attributes_layout.setContentsMargins(4, 4, 4, 4)
        attributes_layout.setSpacing(2)
        
        # 4) For each texture type, create a row: [Label] [LineEdit]
        for ttype in self.texture_types:
            row_widget = QtWidgets.QWidget()
            row_layout = QtWidgets.QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(4)  # Reduced spacing

            # 4a) A QLabel: e.g. "Base Color:" or "Emission Color:"
            display_name = self.get_display_name(ttype)
            label = QtWidgets.QLabel(f"{display_name}:")
            label.setFixedWidth(140)
            label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
            label.setStyleSheet("""
                QLabel {
                    font-family: 'Segoe UI';
                    font-size: 12px;
                    color: %s;
                    background-color: #444444;
                    border: 2px solid #444444;
                    border-radius: 6px;
                    padding: 2px 6px;
                }
            """ % UI_ACCENT_CYAN)
            row_layout.addWidget(label)

            # 4b) A QLineEdit with objectName "<textureType>TextureNameLineEdit"
            line_edit = QtWidgets.QLineEdit()
            line_edit.setObjectName(f"{ttype}TextureNameLineEdit")
            # Set default text (will be overridden by _apply_saved_texture_names if JSON exists)
            line_edit.setText(ttype)  # Default to texture type name
            line_edit.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
            line_edit.setStyleSheet("""
                QLineEdit {
                    font-family: 'Segoe UI';
                    font-size: 11px;
                    color: #ffffff;
                    background-color: #333333;
                    border: 2px solid #444444;
                    border-radius: 6px;
                    padding: 2px 4px;
                }
                QLineEdit:hover {
                    background-color: #222222;
                }
                QLineEdit:focus {
                    border: 2px solid #555555;
                    background-color: #222222;
                }
                QLineEdit:disabled {
                    color: #777777;
                    background-color: #555555;
                    border: 2px solid #666666;
                }
            """)
            row_layout.addWidget(line_edit, 1)  # stretch = 1

            # Store reference for later (saving/loading)
            self.ui_elements[f"{ttype}TextureNameLineEdit"] = line_edit

            attributes_layout.addWidget(row_widget)
        
        content_layout.addWidget(attributes_frame)

        # 5) Add Packed Texture section
        self._add_packed_texture_section(content_layout)

        # 6) Add an expanding spacer at the bottom
        spacer = QtWidgets.QSpacerItem(20, 40,
                                       QtWidgets.QSizePolicy.Minimum,
                                       QtWidgets.QSizePolicy.Expanding)
        content_layout.addItem(spacer)

    def _add_packed_texture_section(self, parent_layout):
        """
        Add the Packed Texture section with:
        - Multiple attribute rows (combobox + plus button + R/G/B/A checkboxes)
        - Single line edit for search names
        """
        # Container for the entire packed texture section
        packed_section = QtWidgets.QWidget()
        packed_section_layout = QtWidgets.QVBoxLayout(packed_section)
        packed_section_layout.setContentsMargins(0, 4, 0, 4)
        packed_section_layout.setSpacing(4)
        
        # Title label with plus button
        title_row = QtWidgets.QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(4)
        
        title_label = QtWidgets.QLabel("Packed textures:")
        title_label.setStyleSheet("""
            QLabel {
                font-family: 'Segoe UI';
                font-size: 13px;
                font-weight: 600;
                color: #ffffff;
                background-color: #444444;
                border: 2px solid #444444;
                border-radius: 6px;
                padding: 2px 6px;
            }
        """)
        title_row.addWidget(title_label)
        
        # Plus button to add new packed texture entry
        add_packed_btn = QtWidgets.QPushButton("+")
        add_packed_btn.setFixedWidth(24)
        add_packed_btn.setFixedHeight(24)
        add_packed_btn.setStyleSheet("""
            QPushButton {
                font-family: 'Segoe UI';
                font-size: 14px;
                font-weight: bold;
                color: #ffffff;
                background-color: #666666;
                border: 2px solid #444444;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #888888;
            }
            QPushButton:pressed {
                background-color: #1a1a1a;
            }
        """)
        add_packed_btn.clicked.connect(lambda: self._add_packed_texture_entry())
        title_row.addWidget(add_packed_btn)
        title_row.addStretch(1)
        
        packed_section_layout.addLayout(title_row)
        
        # Container for packed texture entries
        self.packed_entries_container = QtWidgets.QWidget()
        self.packed_entries_layout = QtWidgets.QVBoxLayout(self.packed_entries_container)
        self.packed_entries_layout.setContentsMargins(0, 0, 0, 0)
        self.packed_entries_layout.setSpacing(4)
        
        # Store list of packed texture entries
        self.packed_texture_entries = []
        
        # Add initial entry (default ORM example)
        self._add_packed_texture_entry()
        
        packed_section_layout.addWidget(self.packed_entries_container)
        
        parent_layout.addWidget(packed_section)
    
    def _add_packed_texture_entry(self):
        """
        Add a new packed texture entry with:
        - Frame around the entry
        - Multiple attribute rows (combobox + plus button + R/G/B/A checkboxes)
        - Search names label and line edit inline
        """
        # Frame for this packed texture entry
        entry_frame = QtWidgets.QFrame()
        entry_frame.setFrameShape(QtWidgets.QFrame.StyledPanel)
        entry_frame.setStyleSheet("""
            QFrame {
                background-color: #3a3a3a;
                border: 1px solid #555555;
                border-radius: 6px;
                padding: 4px;
            }
        """)
        entry_layout = QtWidgets.QVBoxLayout(entry_frame)
        entry_layout.setContentsMargins(4, 4, 4, 4)
        entry_layout.setSpacing(4)
        
        # Delete button for entire packed texture entry (above first attribute)
        delete_entry_btn = QtWidgets.QPushButton("Delete Entry")
        delete_entry_btn.setStyleSheet("""
            QPushButton {
                font-family: 'Segoe UI';
                font-size: 11px;
                color: #ffffff;
                background-color: #666666;
                border: 2px solid #444444;
                border-radius: 4px;
                padding: 2px 6px;
                min-height: 20px;
            }
            QPushButton:hover {
                background-color: #888888;
            }
            QPushButton:pressed {
                background-color: #1a1a1a;
            }
        """)
        delete_entry_btn.clicked.connect(lambda: self._remove_packed_texture_entry(entry_frame))
        entry_layout.addWidget(delete_entry_btn)
        
        # Container for attribute assignments (will hold multiple rows)
        assignments_container = QtWidgets.QWidget()
        assignments_layout = QtWidgets.QVBoxLayout(assignments_container)
        assignments_layout.setContentsMargins(0, 0, 0, 0)
        assignments_layout.setSpacing(2)
        
        # Store list of assignment rows for this entry
        assignment_rows = []
        
        # Add initial row
        row_data = self._add_packed_assignment_row(assignments_layout, assignment_rows)
        
        entry_layout.addWidget(assignments_container)
        
        # Search names label and line edit inline
        search_row = QtWidgets.QHBoxLayout()
        search_row.setContentsMargins(0, 0, 0, 0)
        search_row.setSpacing(4)
        
        search_label = QtWidgets.QLabel("Search Names:")
        search_label.setStyleSheet("""
            QLabel {
                font-family: 'Segoe UI';
                font-size: 12px;
                color: #ffffff;
                background-color: #444444;
                border: 2px solid #444444;
                border-radius: 6px;
                padding: 2px 6px;
            }
        """)
        search_row.addWidget(search_label)
        
        search_line_edit = QtWidgets.QLineEdit()
        search_line_edit.setObjectName(f"packedTextureSearchNamesLineEdit_{len(self.packed_texture_entries)}")
        search_line_edit.setText("OcclusionRoughnessMetallic, ORM" if len(self.packed_texture_entries) == 0 else "")
        search_line_edit.setStyleSheet("""
            QLineEdit {
                font-family: 'Segoe UI';
                font-size: 11px;
                color: #ffffff;
                background-color: #333333;
                border: 2px solid #444444;
                border-radius: 6px;
                padding: 2px 4px;
            }
            QLineEdit:hover {
                background-color: #222222;
            }
            QLineEdit:focus {
                border: 2px solid #555555;
                background-color: #222222;
            }
        """)
        search_row.addWidget(search_line_edit, 1)
        
        entry_layout.addLayout(search_row)
        
        # Store entry data
        entry_data = {
            'frame': entry_frame,
            'delete_btn': delete_entry_btn,
            'assignments_container': assignments_container,
            'assignments_layout': assignments_layout,
            'assignment_rows': assignment_rows,
            'search_line_edit': search_line_edit
        }
        self.packed_texture_entries.append(entry_data)
        self.ui_elements[search_line_edit.objectName()] = search_line_edit
        
        self.packed_entries_layout.addWidget(entry_frame)
    
    def _remove_packed_texture_entry(self, entry_frame):
        """Remove an entire packed texture entry."""
        # Find and remove from list
        for entry_data in self.packed_texture_entries[:]:
            if entry_data['frame'] == entry_frame:
                self.packed_texture_entries.remove(entry_data)
                break
        
        # Remove from layout and delete widget
        self.packed_entries_layout.removeWidget(entry_frame)
        entry_frame.deleteLater()
    
    def _add_packed_assignment_row(self, parent_layout, assignment_rows_list, attribute=None, channel=None):
        """
        Add a row for packed texture assignment:
        - Combobox for attribute selection
        - Plus button to add another row
        - R/G/B/A checkboxes for channel selection
        """
        row_widget = QtWidgets.QWidget()
        row_layout = QtWidgets.QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(4)
        
        # Check if this is the first row
        is_first_row = len(assignment_rows_list) == 0
        
        # Plus button to add another row (only on first row)
        plus_btn = None
        if is_first_row:
            plus_btn = QtWidgets.QPushButton("+")
            plus_btn.setFixedWidth(24)
            plus_btn.setFixedHeight(24)
            plus_btn.setStyleSheet("""
                QPushButton {
                    font-family: 'Segoe UI';
                    font-size: 14px;
                    font-weight: bold;
                    color: #ffffff;
                    background-color: #666666;
                    border: 2px solid #444444;
                    border-radius: 4px;
                }
                QPushButton:hover {
                    background-color: #888888;
                }
                QPushButton:pressed {
                    background-color: #1a1a1a;
                }
            """)
            plus_btn.clicked.connect(lambda: self._add_packed_assignment_row(parent_layout, assignment_rows_list))
            row_layout.addWidget(plus_btn)
        else:
            # Match first row: [+] is 24px fixed + ~3px from frame/border vs. bare spacer
            spacer = QtWidgets.QSpacerItem(27, 24, QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
            row_layout.addItem(spacer)
        
        minus_btn = QtWidgets.QPushButton("-")
        minus_btn.setFixedWidth(24)
        minus_btn.setFixedHeight(24)
        minus_btn.setStyleSheet("""
            QPushButton {
                font-family: 'Segoe UI';
                font-size: 14px;
                font-weight: bold;
                color: #ffffff;
                background-color: #666666;
                border: 2px solid #444444;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #888888;
            }
            QPushButton:pressed {
                background-color: #1a1a1a;
            }
            QPushButton:disabled {
                color: #888888;
                background-color: #555555;
            }
        """)
        minus_btn.clicked.connect(
            lambda: self._remove_packed_assignment_row(row_widget, parent_layout, assignment_rows_list)
        )
        row_layout.addWidget(minus_btn)
        
        # Disable wheel scrolling on combobox (since we're in a scroll area)
        class NoWheelComboBox(QtWidgets.QComboBox):
            def wheelEvent(self, event):
                event.ignore()
        
        # Attribute combobox (same cyan accent as texture importer combos)
        attr_combo = NoWheelComboBox()
        attr_combo.setStyleSheet(combo_stylesheet_template.format(color=UI_ACCENT_CYAN))
        attr_combo.setMinimumWidth(120)
        
        # Add "Select Attribute" as first item
        attr_combo.addItem("-- Select Attribute --", None)
        
        # Add all texture types
        for ttype in ALL_TEXTURE_TYPES:
            display = self.get_display_name(ttype)
            attr_combo.addItem(display, ttype)
        
        # Set default attribute if provided
        if attribute:
            for i in range(attr_combo.count()):
                if attr_combo.itemData(i) == attribute:
                    attr_combo.setCurrentIndex(i)
                    break
        else:
            attr_combo.setCurrentIndex(0)
        
        row_layout.addWidget(attr_combo)
        
        # R/G/B/A checkboxes using checkbox_stylesheet
        channel_checkboxes = {}
        for channel_name in ['r', 'g', 'b', 'a']:
            cb = QtWidgets.QCheckBox(channel_name.upper())
            cb.setStyleSheet(checkbox_stylesheet)
            # Set default channel if provided
            if channel and channel_name == channel:
                cb.setChecked(True)
            row_layout.addWidget(cb)
            channel_checkboxes[channel_name] = cb
        
        row_layout.addStretch(1)
        
        # Store row data
        row_data = {
            'widget': row_widget,
            'combo': attr_combo,
            'plus_btn': plus_btn,
            'minus_btn': minus_btn,
            'checkboxes': channel_checkboxes
        }
        assignment_rows_list.append(row_data)
        
        parent_layout.addWidget(row_widget)
        
        self._update_packed_assignment_minus_enabled(assignment_rows_list)
        
        return row_data

    def _update_packed_assignment_minus_enabled(self, assignment_rows_list):
        """Minus is shown on every row; disabled when only one row remains."""
        can_remove = len(assignment_rows_list) > 1
        for row_data in assignment_rows_list:
            mb = row_data.get("minus_btn")
            if mb:
                mb.setEnabled(can_remove)
    
    def _remove_packed_assignment_row(self, row_widget, parent_layout, assignment_rows_list):
        """Remove a packed texture assignment row."""
        # Don't allow removing the last row
        if len(assignment_rows_list) <= 1:
            return
        
        # Find and remove from list
        for row_data in assignment_rows_list[:]:
            if row_data['widget'] == row_widget:
                assignment_rows_list.remove(row_data)
                break
        
        # Remove from layout and delete widget
        parent_layout.removeWidget(row_widget)
        row_widget.deleteLater()
        self._update_packed_assignment_minus_enabled(assignment_rows_list)

    def _load_texture_search_names(self):
        """
        Return raw dict from user JSON, legacy Settings path, or texture_search_names_default.json.
        """
        return load_texture_search_names_raw_dict()

    def _apply_saved_texture_names(self):
        """
        Fill each <ttype>TextureNameLineEdit with the saved, comma-separated keywords.
        Also load packed texture data if present.
        """
        data = self._load_texture_search_names() or {}
        for ttype in self.texture_types:
            le = self.ui_elements.get(f"{ttype}TextureNameLineEdit")
            if not le:
                continue
            keywords = data.get(ttype, [])
            if isinstance(keywords, list) and keywords:
                # Ensure everything is a string, then join by commas
                le.setText(", ".join(str(k) for k in keywords))
        
        # Load packed texture data
        packed_textures = data.get("packedTextures", [])
        if packed_textures and len(packed_textures) > 0 and hasattr(self, 'packed_texture_entries'):
            # Clear existing entries (except first)
            while len(self.packed_texture_entries) > 1:
                entry_data = self.packed_texture_entries.pop()
                entry_data['frame'].deleteLater()
            
            # Load each packed texture entry
            for idx, entry in enumerate(packed_textures):
                if idx == 0 and len(self.packed_texture_entries) > 0:
                    # Update first entry
                    entry_data = self.packed_texture_entries[0]
                else:
                    # Create new entry
                    self._add_packed_texture_entry()
                    entry_data = self.packed_texture_entries[-1]
                
                search_names = entry.get("searchNames", "")
                assignments = entry.get("assignments", [])
                
                # Set search names
                search_le = entry_data.get('search_line_edit')
                if search_le:
                    search_le.setText(search_names)
                
                # Load assignments
                if assignments:
                    # Clear existing rows (except first)
                    assignment_rows = entry_data.get('assignment_rows', [])
                    while len(assignment_rows) > 1:
                        row_data = assignment_rows.pop()
                        row_data['widget'].deleteLater()
                    
                    # Update first row
                    if len(assignment_rows) > 0:
                        first_row = assignment_rows[0]
                        first_assignment = assignments[0]
                        attr = first_assignment.get("attribute")
                        channel = first_assignment.get("channel")
                        if attr:
                            for i in range(first_row['combo'].count()):
                                if first_row['combo'].itemData(i) == attr:
                                    first_row['combo'].setCurrentIndex(i)
                                    break
                        if channel and channel in first_row['checkboxes']:
                            first_row['checkboxes'][channel].setChecked(True)
                    
                    # Add additional rows for remaining assignments
                    for assignment in assignments[1:]:
                        attr = assignment.get("attribute")
                        channel = assignment.get("channel")
                        self._add_packed_assignment_row(
                            entry_data['assignments_layout'],
                            assignment_rows,
                            attribute=attr,
                            channel=channel
                        )
                    self._update_packed_assignment_minus_enabled(assignment_rows)


    def setup_connections(self):
        """
        Connect the Save Texture Names button to save_texture_names().
        Also add "Open Settings Location" and "Cancel" buttons next to it.
        """
        save_btn = self.ui_elements.get("saveTextureNamesButton")
        if save_btn:
            save_btn.clicked.connect(self.save_texture_names)
            
            # Get the parent layout to add new buttons
            parent_layout = save_btn.parent().layout()
            if parent_layout:
                # Find the index of save button in the layout
                save_index = parent_layout.indexOf(save_btn)
                
                # Create "Open Settings Location" button
                open_settings_btn = QtWidgets.QPushButton("Open Settings Location")
                open_settings_btn.setStyleSheet("""
                    QPushButton {
                        font-family: 'Segoe UI';
                        font-size: 12px;
                        color: #ffffff;
                        background-color: #666666;
                        border: 2px solid #444444;
                        border-radius: 6px;
                        padding: 4px 10px;
                        min-height: 26px;
                    }
                    QPushButton:hover {
                        background-color: #888888;
                    }
                    QPushButton:pressed {
                        background-color: #1a1a1a;
                    }
                """)
                open_settings_btn.clicked.connect(self.open_settings_location)
                
                # Create "Cancel" button
                cancel_btn = QtWidgets.QPushButton("Cancel")
                cancel_btn.setStyleSheet("""
                    QPushButton {
                        font-family: 'Segoe UI';
                        font-size: 12px;
                        color: #ffffff;
                        background-color: #666666;
                        border: 2px solid #444444;
                        border-radius: 6px;
                        padding: 4px 10px;
                        min-height: 26px;
                    }
                    QPushButton:hover {
                        background-color: #888888;
                    }
                    QPushButton:pressed {
                        background-color: #1a1a1a;
                    }
                """)
                cancel_btn.clicked.connect(self.close)
                
                # Create horizontal button layout with all buttons matching sizes
                btn_layout = QtWidgets.QHBoxLayout()
                btn_layout.setSpacing(4)
                
                # Remove save button from parent if it's in a layout
                if isinstance(parent_layout, QtWidgets.QVBoxLayout):
                    parent_layout.removeWidget(save_btn)
                elif isinstance(parent_layout, QtWidgets.QHBoxLayout):
                    parent_layout.removeWidget(save_btn)
                
                # Style save button to match others
                save_btn.setStyleSheet("""
                    QPushButton {
                        font-family: 'Segoe UI';
                        font-size: 12px;
                        color: #ffffff;
                        background-color: #666666;
                        border: 2px solid #444444;
                        border-radius: 6px;
                        padding: 4px 10px;
                        min-height: 26px;
                    }
                    QPushButton:hover {
                        background-color: #888888;
                    }
                    QPushButton:pressed {
                        background-color: #1a1a1a;
                    }
                """)
                
                # Add all buttons with equal stretch
                save_btn.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
                open_settings_btn.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
                cancel_btn.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
                
                btn_layout.addWidget(save_btn, 1)
                btn_layout.addWidget(open_settings_btn, 1)
                btn_layout.addWidget(cancel_btn, 1)
                
                # Add button layout to parent
                if isinstance(parent_layout, QtWidgets.QVBoxLayout):
                    parent_layout.addLayout(btn_layout)
                elif isinstance(parent_layout, QtWidgets.QHBoxLayout):
                    parent_layout.insertLayout(save_index, btn_layout)
                else:
                    # Fallback: try to find a container
                    container = save_btn.parent()
                    if container:
                        container_layout = container.layout()
                        if container_layout:
                            if isinstance(container_layout, QtWidgets.QVBoxLayout):
                                container_layout.addLayout(btn_layout)
        else:
            cmds.warning("saveTextureNamesButton not found in UI.")
    
    def open_settings_location(self):
        """Open the Settings folder in the system file explorer."""
        script_dir = os.path.dirname(__file__)
        settings_folder = os.path.join(script_dir, "Settings")
        if not os.path.isdir(settings_folder):
            settings_folder = os.path.join(script_dir, "settings")  # legacy fallback
        
        if os.path.isdir(settings_folder):
            if os.name == 'nt':  # Windows
                os.startfile(settings_folder)
            elif os.name == 'posix':  # macOS/Linux
                import subprocess
                subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', settings_folder])
        else:
            cmds.warning(f"Settings folder not found: {settings_folder}")
    
    def _apply_matching_stylesheet(self):
        """
        Apply stylesheet that matches the quick_materials tool styling.
        This ensures visual consistency across the UI.
        """
        # Main widget background
        main_stylesheet = """
        QWidget {
            background-color: #555555;
            font-family: 'Segoe UI';
            font-size: 14px;
            color: #ffffff;
        }
        
        /* Scroll area styling */
        QScrollArea {
            background-color: #3a3a3a;
            border: none;
            border-radius: 8px;
        }
        
        QScrollArea QWidget {
            background-color: #3a3a3a;
        }
        
        /* Scrollbar styling */
        QScrollBar:vertical {
            background-color: #555555;
            width: 12px;
            border: none;
        }
        
        QScrollBar::handle:vertical {
            background-color: #666666;
            border-radius: 6px;
            min-height: 20px;
        }
        
        QScrollBar::handle:vertical:hover {
            background-color: #777777;
        }
        
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0px;
        }
        
        /* Title labels */
        QLabel#textureImporterTitleLabel {
            font-family: 'Segoe UI';
            font-size: 16px;
            color: #d6d6d6;
            background-color: transparent;
            border: none;
            padding: 2px 0px;
            text-align: center;
        }
        
        QLabel#textureImporterTitleLabelDesc {
            font-family: 'Segoe UI';
            font-size: 11px;
            color: #d6d6d6;
            background-color: transparent;
            border: none;
            padding: 2px 0px;
            text-align: center;
        }
        
        /* Ensure all other labels are left-aligned */
        QLabel:not(#textureImporterTitleLabel):not(#textureImporterTitleLabelDesc) {
            text-align: left;
        }
        
        /* Buttons */
        QPushButton {
            font-family: 'Segoe UI';
            font-size: 13px;
            color: #ffffff;
            background-color: #666666;
            border: 2px solid #444444;
            border-radius: 6px;
            padding: 4px 10px;
            min-height: 26px;
        }
        
        QPushButton:hover {
            background-color: #888888;
        }
        
        QPushButton:pressed {
            background-color: #1a1a1a;
        }
        
        QPushButton:disabled {
            color: #666666;
            background-color: #4a4a4a;
            border: 1px solid #555555;
        }
        """
        
        self.setStyleSheet(main_stylesheet)
        
        # Also apply to the main frame if it exists
        main_frame = self.ui_elements.get("mainUIFrame")
        if main_frame:
            main_frame.setStyleSheet("""
                QFrame {
                    background-color: #555555;
                    border: 0px solid #333333;
                    border-radius: 8px;
                    padding: 2px;
                    margin: 2px;
                }
            """)
        
        # Apply to inner frame
        texture_frame = self.ui_elements.get("textureImporterFrame")
        if texture_frame:
            texture_frame.setStyleSheet("""
                QFrame {
                    background-color: #333333;
                    border: 0px solid #333333;
                    border-radius: 8px;
                    padding: 4px;
                    margin: 2px;
                }
            """)
        
        # Apply to scroll area
        scroll_area = self.ui_elements.get("texturesNameEditScrollArea")
        if scroll_area:
            scroll_area.setStyleSheet("""
                QScrollArea {
                    background-color: #3a3a3a;
                    border: none;
                    border-radius: 6px;
                }
            """)
        
        # Get the vertical layout from the texture frame and reduce spacing
        if texture_frame:
            vlayout = texture_frame.findChild(QtWidgets.QVBoxLayout)
            if vlayout:
                vlayout.setSpacing(4)
                vlayout.setContentsMargins(4, 4, 4, 4)

    def save_texture_names(self):
        """
        Gathers keywords from each "<textureType>TextureNameLineEdit", builds a dict:
          { "baseColor": [...], "roughness": [...], ... }
        Also saves packed texture data.
        Writes "<script_dir>/settings/texture_search_names.json" only (never default JSON).
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

        # Gather packed texture data
        packed_textures = []
        if hasattr(self, 'packed_texture_entries'):
            for entry_data in self.packed_texture_entries:
                search_le = entry_data.get('search_line_edit')
                if search_le:
                    search_names = search_le.text().strip()
                    if search_names:
                        assignments = []
                        for row_data in entry_data.get('assignment_rows', []):
                            attr = row_data['combo'].currentData()
                            if attr:  # Only include rows with selected attributes
                                # Find checked channel
                                channel = None
                                for ch_name, cb in row_data['checkboxes'].items():
                                    if cb.isChecked():
                                        channel = ch_name
                                        break
                                assignments.append({
                                    "attribute": attr,
                                    "channel": channel
                                })
                        
                        if assignments:  # Only add if there are valid assignments
                            packed_textures.append({
                                "searchNames": search_names,
                                "assignments": assignments
                            })
        
        if packed_textures:
            texture_names["packedTextures"] = packed_textures

        script_dir = os.path.dirname(__file__)
        settings_folder = os.path.join(script_dir, "settings")
        if not os.path.isdir(settings_folder):
            os.makedirs(settings_folder)

        save_path = _texture_search_names_user_path()
        try:
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(texture_names, f, indent=4)
            cmds.confirmDialog(title="Success",
                               message="Texture search names saved successfully.",
                               button=["OK"])

        except Exception as e:
            cmds.confirmDialog(title="Error",
                               message=f"Failed to save texture search names:\n{e}",
                               button=["OK"])



class TextureImporterSettingsUI(QtWidgets.QWidget):
    """
    UI for choosing default texture-search settings:textureImporterSettings
     - Maya File / Sourceimages / Custom Path (exclusive)
     - Relative vs Absolute
     - Allow Recursive Searching
    Saves to JSON when you hit “Save Settings.”
    """
    def __init__(self, parent=None):
        super(TextureImporterSettingsUI, self).__init__(parent)
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
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.addWidget(self.ui_instance)
        self.ui_instance.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

        # 2) Grab all named widgets
        self.ui_elements = {}
        self.auto_initialize_ui_elements(self.ui_instance)

        self.setWindowFlags(self.windowFlags() | QtCore.Qt.Window)
        self.setWindowTitle("Texture Importer Settings")

        # Lock the window size so it cannot be scaled
        self.setFixedSize(self.sizeHint())

        # ---------- load saved settings and prime widgets ----------
        # DEPRECATED: Textures folder default location
        # self._apply_saved_settings()
        # self._update_custom_path_widgets()

        # 4) Wire up exclusivity & enabling logic
        self._setup_logic()

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

    def _setup_logic(self):
        """Wire up UI interactions (no exclusivity code—done in Qt Designer)."""
        # DEPRECATED: Textures folder default location — checkbox and custom path wiring removed.
        # for name in (
        #     "textureSearchMayaFileCheckbox",
        #     "textureSearchMayaSourceimagesCheckbox",
        #     "textureSearchCustomPathCheckbox"
        # ):
        #     cb = self.ui_elements.get(name)
        #     if cb:
        #         cb.toggled.connect(self._update_custom_path_widgets)
        # self._update_custom_path_widgets()
        #
        # custom_path_edit = self.ui_elements.get("textureSearchCustomPathLineEdit")
        # if custom_path_edit:
        #     tooltip_text = (
        #         "Custom texture search path with dynamic key substitution.\n\n"
        #         "Available keys:\n"
        #         "• (scene) - Current Maya file folder\n"
        #         "• (project) - Current Maya project folder\n\n"
        #         "Add any path after the key:\n"
        #         "• (scene)/textures\n"
        #         "• (scene)/assets/textures\n"
        #         "• (project)/sourceimages\n"
        #         "• (project)/sourceimages/materials"
        #     )
        #     custom_path_edit.setToolTip(tooltip_text)

        # Save button
        save_btn = self.ui_elements.get("textureImporterSaveSettings")
        if save_btn:
            save_btn.clicked.connect(self._save_settings)

        # DEPRECATED: Textures folder default location
        # set_btn = self.ui_elements.get("textureSearchCustomPathSetButton")
        # if set_btn:
        #     set_btn.clicked.connect(self._choose_custom_path)

        # Open Texture Search Names from Settings
        names_btn = self.ui_elements.get("editTextureSearchNamesButton")
        if names_btn:
            try:
                names_btn.clicked.disconnect()
            except Exception:
                pass
            names_btn.clicked.connect(self.open_texture_search_names_ui)


    # DEPRECATED: Textures folder default location — all custom path and search mode
    # widget methods removed from TextureImporterSettingsUI.
    #
    # def _update_custom_path_widgets(self):
    #     """Enable/disable custom-path widgets based on checkbox state."""
    #     ...
    #
    # def _choose_custom_path(self):
    #     """Enhanced custom path handling with key substitution and folder creation."""
    #     ...
    #
    # def _resolve_custom_path_keys(self, path_template):
    #     """Resolve key substitution in custom path template."""
    #     ...

    def open_texture_search_names_ui(self):
        """Launch the TextureSearchNamesUI from the Settings window."""
        if not hasattr(self, "_texture_search_names_ui") or self._texture_search_names_ui is None:
            self._texture_search_names_ui = TextureSearchNamesUI(parent=self)
        self._texture_search_names_ui.show()
        self._texture_search_names_ui.raise_()

    # DEPRECATED: Textures folder default location
    # def reload_from_disk(self):
    #     """Re-read JSON and re-apply to widgets (call before showing the window)."""
    #     self._apply_saved_settings()
    #     self._update_custom_path_widgets()

    # def _load_settings(self):
    #     """Read texture_importer section from user JSON, else packaged default JSON."""
    #     all_settings = _load_quick_materials_all_settings()
    #     if isinstance(all_settings, dict) and "texture_importer" in all_settings:
    #         return all_settings["texture_importer"]
    #     return {}

    # def _apply_saved_settings(self):
    #     """Tick checkboxes / line-edits from stored JSON."""
    #     s = self._load_settings() or {}
    #     mode = s.get("default_mode", "maya_file")
    #     self.ui_elements["textureSearchMayaFileCheckbox"].setChecked(mode == "maya_file")
    #     self.ui_elements["textureSearchMayaSourceimagesCheckbox"].setChecked(mode == "sourceimages")
    #     self.ui_elements["textureSearchCustomPathCheckbox"].setChecked(mode == "custom")
    #     self.ui_elements["textureSearchCustomPathLineEdit"].setText(s.get("custom_path", ""))
    #     create_if_not_exists = self.ui_elements.get("createIfDoesntExistCheckbox")
    #     if create_if_not_exists:
    #         create_if_not_exists.setChecked(s.get("create_if_doesnt_exist", False))

    # def _save_settings(self):
    #     """Write settings to the main quick materials settings JSON."""
    #     mode = "maya_file" if self.ui_elements["textureSearchMayaFileCheckbox"].isChecked() else \
    #         "sourceimages" if self.ui_elements["textureSearchMayaSourceimagesCheckbox"].isChecked() else \
    #             "custom"
    #     data = {
    #         "default_mode": mode,
    #         "custom_path": self.ui_elements["textureSearchCustomPathLineEdit"].text(),
    #     }
    #     create_if_not_exists = self.ui_elements.get("createIfDoesntExistCheckbox")
    #     if create_if_not_exists:
    #         data["create_if_doesnt_exist"] = create_if_not_exists.isChecked()
    #     try:
    #         import os
    #         script_dir = os.path.dirname(__file__)
    #         settings_dir = os.path.join(script_dir, "settings")
    #         os.makedirs(settings_dir, exist_ok=True)
    #         settings_path = os.path.join(settings_dir, "quick_materials_settings.json")
    #         if os.path.exists(settings_path):
    #             with open(settings_path, "r", encoding="utf-8") as f:
    #                 all_settings = json.load(f)
    #         else:
    #             merged = _load_quick_materials_all_settings()
    #             all_settings = merged if isinstance(merged, dict) and merged else {
    #                 'material_creator': {},
    #                 'material_list': {},
    #                 'texture_importer': {}
    #             }
    #         all_settings['texture_importer'] = data
    #         with open(settings_path, "w", encoding="utf-8") as f:
    #             json.dump(all_settings, f, indent=2)
    #         cmds.inViewMessage(amg="<hl>✔ Quick Materials Settings Saved</hl>", pos="topCenter", fade=True)
    #     except Exception as e:
    #         cmds.confirmDialog(title="Error", message=f"Failed to save settings: {e}", button=["OK"])

