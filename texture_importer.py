import os
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

# Import icons resource
try:
    from . import icons_rc  # type: ignore
except ImportError:
    import icons_rc  # type: ignore


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

        self.use_udim = True

        # Remember the last directory a texture was imported from
        self.last_texture_dir = ""

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
        self.setMinimumSize(400, 200)

        # Set default/initial size - Adjust these values as needed
        self.resize(515, 330)

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
            button = self._get_widget("showAdvTexturesButton", QtWidgets.QPushButton)  # safer
            if button:
                # try to disconnect only our slot; fall back to generic disconnect if needed
                try:
                    button.clicked.disconnect(self.toggle_adv_textures)
                except Exception:
                    try:
                        button.clicked.disconnect()
                    except Exception:
                        pass
                button.clicked.connect(self.toggle_adv_textures)
                self._debug_print("Connected showAdvTexturesButton -> toggle_adv_textures")
            else:
                self._debug_print("showAdvTexturesButton resolved to None")
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


        # Texture Importer Settings Button
        # Texture importer settings button removed - settings now handled by main quickMaterialsSettingsButton

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
        import_btn = self.ui_elements.get("importTexturesButton")
        if import_btn:
            try:
                import_btn.clicked.disconnect()
            except Exception:
                pass
            import_btn.clicked.connect(self._on_import_textures_clicked)
            self._debug_print("Connected importTexturesButton to _on_import_textures_clicked")

        # Clear All button  # <-- new
        clear_all_btn = self.ui_elements.get("clearAllButton")
        if clear_all_btn:
            try:
                clear_all_btn.clicked.disconnect()
            except Exception:
                pass
            clear_all_btn.clicked.connect(self.clear_all_textures)
            self._debug_print("Connected clearAllButton to clear_all_textures")




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
        Load per-texture-type keyword lists from Settings/texture_search_names.json
        (or legacy settings/texture_search_names.json). Falls back to {type:[type]}.
        """
        base_dir = os.path.dirname(__file__)
        candidates = [
            os.path.join(base_dir, "Settings", "texture_search_names.json"),
            os.path.join(base_dir, "settings", "texture_search_names.json")
        ]
        data = {}
        for path in candidates:
            try:
                with open(path, "r") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    data = raw
                    break
            except Exception:
                continue

        # Normalize: ensure every type exists and is a list of strings
        norm = {}
        for ttype in ALL_TEXTURE_TYPES:
            vals = data.get(ttype, [])
            if isinstance(vals, list) and vals:
                norm[ttype] = [str(v).strip() for v in vals if str(v).strip()]
            else:
                norm[ttype] = [ttype]  # minimal fallback to its own name
        return norm

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

        if material_name and material_name.strip() and material_name != "All Materials":
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
        btn = self.ui_elements.get("importTexturesButton")
        cb = self._get_widget("materialComboBox", QtWidgets.QComboBox)
        if not btn or not cb or not isValid(cb):
            return
        try:
            is_all = (str(cb.currentText()) == "All Materials")
        except RuntimeError:
            self._debug_print("[ImportBtn] currentText() failed (combo deleted).")
            return
        btn.setText("Preview Import Textures" if is_all else "Import Textures")
        self._debug_print(f"[ImportBtn] Label -> {btn.text()}")

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

        if current == "All Materials":
            if not getattr(self, "_bulk_match_cache", None):
                cmds.warning("No cached matches. Run Auto-Find-All first.")
                return
            dlg = PreviewImportDialog(self, self._bulk_match_cache)
            result = dlg.exec_()
            if result == QtWidgets.QDialog.Accepted:
                self._debug_print("[PreviewImport] Accepted -> performing bulk import")
                self._perform_bulk_import(self._bulk_match_cache)
            else:
                self._debug_print("[PreviewImport] Cancelled")
        else:
            self._debug_print(f"[SingleImport] Importing textures for '{current}'")
            self._perform_single_material_import(current)

    def _perform_single_material_import(self, material_name):
        """
        Import textures currently set in line edits (self.texture_data) for `material_name`.
        """
        count = 0
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

    def _perform_bulk_import(self, mat_map):
        """
        Bulk import using mat_map = { material: { type: path_or_None } }.
        """
        for mat, type_map in mat_map.items():
            # Skip materials with no matches
            if not any(type_map.values()):
                continue
            imp = 0
            for ttype, path in type_map.items():
                if not path:
                    continue
                if not os.path.isfile(path) and "<UDIM>" not in path:
                    # Allow UDIM pattern strings to pass (if pre-filter built them that way)
                    continue
                self._import_one_type(mat, ttype, path)
                imp += 1
            self._debug_print(f"[Import] Imported {imp} textures for {mat}")




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

    # -------- helper to pick a “representative” UDIM (prefer 1001, else lowest) --------
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
        token/required-keyword system, then populate the appropriate slots.
        This ALWAYS considers both standard and advanced types (visibility ignored).
        """
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
        kw_map = self._load_keyword_map()
        mat_combo = self._get_widget("materialComboBox", QtWidgets.QComboBox)
        material = mat_combo.currentText() if mat_combo else ""

        # Classify each file -> type
        classified = {}  # type -> [paths...]
        unmatched = []

        for path in files:
            ttype, score = self._classify_texture_type_for_file(path, kw_map, material)
            if ttype:
                classified.setdefault(ttype, []).append(path)
                self._debug_print(f"[SelectImport] '{os.path.basename(path)}' -> {ttype} (score={score})")
            else:
                unmatched.append(path)
                self._debug_print(f"[SelectImport] '{os.path.basename(path)}' -> (no match)")

        # For each type, pick a representative (prefer 1001), then process like a normal selection
        for ttype, paths in classified.items():
            rep = self._prefer_representative_udim(paths)
            if rep:
                self.process_selected_texture(rep, ttype)

        # Handle unmatched textures with dialog
        if unmatched:
            dlg = AssignTexturesDialog(self, unmatched)
            result = dlg.exec_()
            if result == QtWidgets.QDialog.Accepted:
                # Process each assigned texture
                for texture_path, texture_type in dlg.texture_assignments.items():
                    rep = self._prefer_representative_udim([texture_path])
                    if rep:
                        self.process_selected_texture(rep, texture_type)
                self._debug_print(f"[SelectImport] Assigned {len(dlg.texture_assignments)} unmatched texture(s)")
            else:
                self._debug_print(f"[SelectImport] Cancelled assignment of {len(unmatched)} unmatched texture(s)")

    def _pre_populate_textures(self, texture_files):
        """
        Pre-populate the texture importer with selected texture files.
        This is called when the texture importer is opened with pre-selected textures.
        """
        if not texture_files:
            return

        # Load keyword map & get current material name (safe)
        kw_map = self._load_keyword_map()
        mat_combo = self._get_widget("materialComboBox", QtWidgets.QComboBox)
        material = mat_combo.currentText() if mat_combo else ""

        # Classify each file -> type
        classified = {}  # type -> [paths...]
        unmatched = []

        for path in texture_files:
            ttype, score = self._classify_texture_type_for_file(path, kw_map, material)
            if ttype:
                classified.setdefault(ttype, []).append(path)
                self._debug_print(f"[PrePopulate] '{os.path.basename(path)}' -> {ttype} (score={score})")
            else:
                unmatched.append(path)
                self._debug_print(f"[PrePopulate] '{os.path.basename(path)}' -> (no match)")

        # For each type, pick a representative (prefer 1001), then process like a normal selection
        for ttype, paths in classified.items():
            rep = self._prefer_representative_udim(paths)
            if rep:
                self.process_selected_texture(rep, ttype)

        # Handle unmatched textures with dialog
        if unmatched:
            dlg = AssignTexturesDialog(self, unmatched)
            result = dlg.exec_()
            if result == QtWidgets.QDialog.Accepted:
                # Process each assigned texture
                for texture_path, texture_type in dlg.texture_assignments.items():
                    rep = self._prefer_representative_udim([texture_path])
                    if rep:
                        self.process_selected_texture(rep, texture_type)
                self._debug_print(f"[PrePopulate] Assigned {len(dlg.texture_assignments)} unmatched texture(s)")
            else:
                self._debug_print(f"[PrePopulate] Cancelled assignment of {len(unmatched)} unmatched texture(s)")

    def clear_all_textures(self):
        """
        Clear all texture line edits and in-memory selections.
        Also clears any cached bulk matches from Auto-Find-All.
        """
        # Clear UI fields
        for ttype in ALL_TEXTURE_TYPES:
            le = self.ui_elements.get(f"{ttype}LineEdit")
            if le:
                try:
                    le.clear()
                except Exception:
                    pass

        # Reset internal state
        self.texture_data.clear()
        if hasattr(self, "_bulk_match_cache"):
            try:
                self._bulk_match_cache.clear()
            except Exception:
                self._bulk_match_cache = {}

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
        """
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


    # ---------- Import helpers ----------
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
        color_space = rules["colorSpace"]
        kind = rules["kind"]

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
            # aiNormalMap between file and material.normalCamera
            nn = self._ensure_ai_normal_map(material)
            # file (Raw RGB) -> aiNormalMap.input
            try:
                if not cmds.isConnected(f"{file_node}.outColor", f"{nn}.input"):
                    cmds.connectAttr(f"{file_node}.outColor", f"{nn}.input", force=True)
            except Exception:
                pass
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

        if kind == "color":
            # Opacity is "color" in rules but we prefer alpha by default;
            # _get_channel_selection() will return "A" by default for opacity.
            if ch_pref == "A":
                self._connect_scalar_to_color(f"{file_node}.outAlpha", material, rules["attr"])
            elif ch_pref in ("R", "G", "B"):
                ch_src = {"R": "outColorR", "G": "outColorG", "B": "outColorB"}[ch_pref]
                self._connect_scalar_to_color(f"{file_node}.{ch_src}", material, rules["attr"])
            else:
                try:
                    cmds.connectAttr(f"{file_node}.outColor", f"{material}.{rules['attr']}", force=True)
                except Exception:
                    # fallback replicate from R
                    self._connect_scalar_to_color(f"{file_node}.outColorR", material, rules["attr"])
        else:
            # kind == "float": default prefers Alpha; _get_channel_selection() already did the defaulting
            src = f"{file_node}.outAlpha" if ch_pref == "A" else \
                f"{file_node}.outColorR" if ch_pref == "R" else \
                    f"{file_node}.outColorG" if ch_pref == "G" else \
                        f"{file_node}.outColorB"
            try:
                cmds.connectAttr(src, f"{material}.{rules['attr']}", force=True)
            except Exception:
                pass

        self._debug_print(f"[Import] {texture_type}: {file_node} -> {material}.{rules['attr']}")

    def open_texture_importer_settings(self):
        if not hasattr(self, "texture_importer_settings_ui") or self.texture_importer_settings_ui is None:
            self.texture_importer_settings_ui = TextureImporterSettingsUI(parent=self)
        else:
            # Ensure we re-read the latest settings off disk each time we open
            self.texture_importer_settings_ui.reload_from_disk()
        self.texture_importer_settings_ui.show()
        self.texture_importer_settings_ui.raise_()



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

    def auto_populate_search_folder(self):
        """
        Populate searchFolderLineEdit based on Settings JSON, with debug output.
        """
        search_line_edit = self.ui_elements.get("searchFolderLineEdit")
        if not search_line_edit:
            cmds.warning("searchFolderLineEdit not found in UI elements.")
            return

        # ---------------- read settings ----------------
        settings = self._load_settings()
        mode = settings.get("default_mode", "maya_file")
        custom_path = settings.get("custom_path", "")
        use_relative = settings.get("relative", False)
        proj_root = self._project_root()

        self._debug_print(f"[SETTINGS] mode={mode}, custom_path='{custom_path}', relative={use_relative}")
        self._debug_print(f"[PROJECT]  root='{proj_root}'")

        # ---------------- resolve absolute folder ----------------
        abs_folder = ""
        if mode == "maya_file":
            scene = cmds.file(q=True, sceneName=True)
            if scene:
                abs_folder = os.path.dirname(scene)
            self._debug_print(f"[RESOLVE] maya_file ➜ '{abs_folder}'")
        elif mode == "sourceimages":
            abs_folder = os.path.join(proj_root, "sourceimages") if proj_root else ""
            self._debug_print(f"[RESOLVE] sourceimages ➜ '{abs_folder}'")
        elif mode == "custom" and custom_path:
            # Handle key substitution for custom path
            resolved_path = self._resolve_custom_path_keys(custom_path)
            abs_folder = resolved_path if resolved_path else ""
            self._debug_print(f"[RESOLVE] custom ➜ '{abs_folder}'")

        # ---------------- decide display path ----------------
        display_path = ""
        if use_relative and abs_folder and proj_root:
            try:
                display_path = os.path.relpath(abs_folder, proj_root)
                self._debug_print(f"[DISPLAY] relative requested; showing '{display_path}'")
            except ValueError:
                display_path = abs_folder
                self._debug_print(f"[DISPLAY] relpath failed; showing absolute path '{display_path}'")
        else:
            self._debug_print(f"[DISPLAY] relative not requested; showing absolute path '{display_path}'")

        # ---------------- apply to UI ----------------
        search_line_edit.setText(display_path)
        self.search_folder_path = abs_folder

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
        kw_map = self._load_keyword_map() if hasattr(self, "_load_keyword_map") else {}
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


        # Standard textures container
        self.standard_textures_container = QtWidgets.QWidget()
        self.standard_textures_layout = QtWidgets.QVBoxLayout(self.standard_textures_container)
        self.standard_textures_layout.setContentsMargins(0, 0, 0, 0)
        self.standard_textures_layout.setSpacing(2)
        scroll_layout.addWidget(self.standard_textures_container)

        # Create a horizontal layout for the "Show Adv Textures" button with spacer
        adv_textures_button_layout = QtWidgets.QHBoxLayout()
        adv_textures_button_layout.setContentsMargins(0, 0, 0, 0)
        adv_textures_button_layout.setSpacing(2)

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

        checkboxes_layout.addStretch(1)
        container_layout.addLayout(checkboxes_layout)

        # Store references for later use
        self.ui_elements[f"{texture_type}ChannelRedCheckbox"] = red_cb
        self.ui_elements[f"{texture_type}ChannelGreenCheckbox"] = green_cb
        self.ui_elements[f"{texture_type}ChannelBlueCheckbox"] = blue_cb
        self.ui_elements[f"{texture_type}ChannelAlphaCheckbox"] = alpha_cb

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
            print(f"[DEBUG] {container_name} visibility set to {not currently_visible}")
        else:
            print(f"[DEBUG] Missing {container_name} or {button_name}")

    def toggle_adv_textures(self):
        self._debug_print("toggle_adv_textures called")
        adv_container = self._get_widget("advTexturesContainer", QtWidgets.QWidget)
        btn = self._get_widget("showAdvTexturesButton", QtWidgets.QPushButton)
        if not adv_container or not btn:
            self._debug_print("Missing advTexturesContainer or showAdvTexturesButton")
            return

        currently_visible = adv_container.isVisible()
        adv_container.setVisible(not currently_visible)
        btn.setText("Hide Adv Textures -" if not currently_visible else "Show Adv Textures +")
        self._debug_print(f"Advanced Textures visibility set to {not currently_visible}")

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
    font-size: 11px;              /* Slightly smaller text */
    color: #f2f2f2;
    background-color: #444444;
    border: none;
    border-radius: 6px;           /* Softer, smaller rounding */
    padding: 2px 2px;             /* Less padding = tighter fit */
    margin: 1px 0;                /* Small vertical margin */
}
QCheckBox::indicator {
    width: 12px;                  /* Smaller checkbox */
    height: 12px;
    border: 1px solid #444444;
    border-radius: 3px;
    background-color: #2b2b2b;
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
QCheckBox::indicator:checked:pressed,
QCheckBox::indicator:unchecked:pressed {
    background-color: #ffffff;
    border: 1px solid #ffffff;
}
/* Disabled state */
QCheckBox:disabled {
    color: #666666;
    background-color: #3a3a3a;
    border-radius: 6px;
    padding: 2px 6px;
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
    font-size: 12px;
    color: #ffffff;
    background-color: #444444;
    border: 2px solid #444444;
    border-radius: 4px;
    /* Tighter vertical padding: reduce bottom to minimize gap above checkboxes */
    padding: -2px -2px -2px -2px;  /* top right bottom left */
}
"""

scroll_area_stylesheet = """
/* Buttons */
QPushButton {
    font-family: 'Segoe UI';  /* Sets the font to Segoe UI */
    font-size: 12px;          /* Adjust the font size as needed */
    color: #ffffff;           /* White text color */
    background-color: #666666;/* Dark background color */
    border: 2px solid #444444;/* Optional border with dark grey color */
    border-radius: 8px;       /* Rounded corners */
    padding: 2px 5px;         /* Padding around the text */
}
QPushButton:hover {
    background-color: #888888;  /* Slightly lighter background on hover */
}
QPushButton:pressed {
    background-color: #1a1a1a;  /* Darker background when pressed */
}
QPushButton:disabled {
    color: #666666;             /* Muted text to indicate disabled */
    border: 1px solid #555555;  /* Optional border with dark grey color */
    background-color: #4a4a4a;  /* Lighter grey background color for disabled state */
}

/* Line edits */
QLineEdit {
    font-family: 'Segoe UI';
    font-size: 12px;
    color: #ffffff;
    background-color: #333333;  /* Retaining the original background color */
    border: 2px solid #444444;
    border-radius: 8px;
    padding: 2px 3px;
}
QLineEdit:hover {
    background-color: #222222;
}
QLineEdit:focus {
    border: 2px solid #555555;
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


class AssignTexturesDialog(QtWidgets.QDialog):
    """
    Dialog to manually assign unmatched textures to material attributes.
    Shows a list of textures with comboboxes for selecting the attribute type.
    """
    def __init__(self, parent, unmatched_textures):
        """
        Initialize the dialog.
        
        Args:
            parent: Parent widget
            unmatched_textures: List of file paths that couldn't be matched
        """
        super(AssignTexturesDialog, self).__init__(parent)
        self.setWindowTitle("Assign Textures")
        self.setModal(True)
        self.resize(600, 500)
        
        # Store the mapping: texture_path -> selected_texture_type
        self.texture_assignments = {}
        
        main = QtWidgets.QVBoxLayout(self)
        main.setContentsMargins(8, 8, 8, 8)
        main.setSpacing(8)
        
        # Title
        title = QtWidgets.QLabel("Assign Textures")
        title.setStyleSheet("font-family: 'Segoe UI'; font-size: 18px; font-weight: 600; color: #ffffff;")
        main.addWidget(title)
        
        # Scroll area for texture assignments
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(scroll_area_stylesheet)
        main.addWidget(scroll, 1)
        
        content = QtWidgets.QWidget()
        scroll.setWidget(content)
        v = QtWidgets.QVBoxLayout(content)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(8)
        
        # Store comboboxes for each texture path
        self.comboboxes = {}
        
        # Helper function to get display name for texture types
        def get_display_name(texture_type):
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
        
        # Create a row for each unmatched texture
        for texture_path in unmatched_textures:
            texture_name = os.path.basename(texture_path)
            
            # Horizontal layout for label + combobox
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(10)
            
            # Label with texture name
            label = QtWidgets.QLabel(texture_name)
            label.setStyleSheet("font-family: 'Segoe UI'; font-size: 13px; color: #d6d6d6; min-width: 200px;")
            label.setWordWrap(False)
            row.addWidget(label)
            
            # Combobox with all texture types
            combo = QtWidgets.QComboBox()
            combo.setStyleSheet("""
                QComboBox {
                    font-family: 'Segoe UI';
                    font-size: 12px;
                    color: #ffffff;
                    background-color: #555555;
                    border: 1px solid #444444;
                    border-radius: 4px;
                    padding: 4px;
                    min-width: 200px;
                }
                QComboBox:hover {
                    background-color: #666666;
                }
                QComboBox::drop-down {
                    border: none;
                }
                QComboBox QAbstractItemView {
                    background-color: #555555;
                    color: #ffffff;
                    selection-background-color: #666666;
                    border: 1px solid #444444;
                }
            """)
            
            # Add "None" option first (default - user must select)
            combo.addItem("-- Select Attribute --", None)
            
            # Add all texture types with display names
            for ttype in ALL_TEXTURE_TYPES:
                display = get_display_name(ttype)
                combo.addItem(display, ttype)
            
            row.addWidget(combo)
            row.addStretch(1)
            
            # Store the combobox reference
            self.comboboxes[texture_path] = combo
            
            # Add row to layout
            v.addLayout(row)
        
        v.addStretch(1)
        
        # Buttons
        btns = QtWidgets.QHBoxLayout()
        btns.addStretch(1)
        continue_btn = QtWidgets.QPushButton("Continue")
        cancel_btn = QtWidgets.QPushButton("Cancel")
        btns.addWidget(continue_btn)
        btns.addWidget(cancel_btn)
        main.addLayout(btns)
        
        continue_btn.clicked.connect(self.on_continue)
        cancel_btn.clicked.connect(self.reject)
    
    def on_continue(self):
        """Called when Continue button is clicked. Validates selections and accepts if valid."""
        # Check that all textures have been assigned
        unassigned = []
        assignments = {}
        
        for texture_path, combo in self.comboboxes.items():
            selected_type = combo.currentData()
            if selected_type is None:
                unassigned.append(os.path.basename(texture_path))
            else:
                assignments[texture_path] = selected_type
        
        # If some textures are unassigned, warn but still allow proceeding with assigned ones
        if unassigned:
            response = QtWidgets.QMessageBox.warning(
                self,
                "Unassigned Textures",
                f"The following textures have not been assigned:\n" + 
                "\n".join(f"• {name}" for name in unassigned) +
                "\n\nDo you want to continue with only the assigned textures?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No
            )
            if response == QtWidgets.QMessageBox.No:
                return  # User cancelled
        
        # Store assignments and accept
        self.texture_assignments = assignments
        self.accept()


class TextureSearchNamesUI(QtWidgets.QWidget):
    """
    Loads textureSearchnames.ui, then dynamically builds the scroll area content
    for each texture type (label + line edit). Finally, it saves the entered keywords
    into a JSON file under <script_dir>/settings/texture_search_names.json.
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
        content_layout.setSpacing(4)
        content_layout.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)

        # 4) For each texture type, create a row: [Label] [LineEdit]
        for ttype in self.texture_types:
            row_widget = QtWidgets.QWidget()
            row_layout = QtWidgets.QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)

            # 4a) A QLabel: e.g. "Base Color:" or "Emission Color:"
            display_name = self.get_display_name(ttype)
            label = QtWidgets.QLabel(f"{display_name}:")
            label.setFixedWidth(140)
            label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
            label.setStyleSheet("""
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
            row_layout.addWidget(label)

            # 4b) A QLineEdit with objectName "<textureType>TextureNameLineEdit"
            line_edit = QtWidgets.QLineEdit()
            line_edit.setObjectName(f"{ttype}TextureNameLineEdit")
            line_edit.setPlaceholderText(f"{display_name}, {ttype[:3].upper()}")
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

            content_layout.addWidget(row_widget)

        # 5) Add an expanding spacer at the bottom
        spacer = QtWidgets.QSpacerItem(20, 40,
                                       QtWidgets.QSizePolicy.Minimum,
                                       QtWidgets.QSizePolicy.Expanding)
        content_layout.addItem(spacer)


    def _load_texture_search_names(self):
        """
        Return a dict of {texture_type: [keywords,...]} from Settings/texture_search_names.json.
        Falls back to legacy 'settings' path. Returns {} if not present or malformed.
        """
        base_dir = os.path.dirname(__file__)
        candidates = [
            os.path.join(base_dir, "Settings", "texture_search_names.json"),
            os.path.join(base_dir, "settings", "texture_search_names.json"),  # legacy fallback
        ]
        for path in candidates:
            try:
                with open(path, "r") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
                else:
                    print(f"[DEBUG] texture_search_names.json contained non-dict data ({path}). Using defaults.")
                    return {}
            except Exception:
                continue
        return {}

    def _apply_saved_texture_names(self):
        """
        Fill each <ttype>TextureNameLineEdit with the saved, comma-separated keywords.
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


    def setup_connections(self):
        """
        Connect the Save Texture Names button to save_texture_names().
        """
        save_btn = self.ui_elements.get("saveTextureNamesButton")
        if save_btn:
            save_btn.clicked.connect(self.save_texture_names)
        else:
            cmds.warning("saveTextureNamesButton not found in UI.")
    
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
            background-color: #444444;
            border: none;
            border-radius: 8px;
        }
        
        QScrollArea QWidget {
            background-color: #444444;
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
                    background-color: #444444;
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
                    background-color: #444444;
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
        Then writes it out to "<script_dir>/Settings/texture_search_names.json".
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

        # Ensure Settings folder exists (match other readers/writers)
        script_dir = os.path.dirname(__file__)
        settings_folder = os.path.join(script_dir, "Settings")
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
        self._apply_saved_settings()
        self._update_custom_path_widgets()  # ensure enable/disable matches state


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
        # Connect each default-path checkbox to our toggle handler
        for name in (
            "textureSearchMayaFileCheckbox",
            "textureSearchMayaSourceimagesCheckbox",
            "textureSearchCustomPathCheckbox"
        ):
            cb = self.ui_elements.get(name)
            if cb:
                cb.toggled.connect(self._update_custom_path_widgets)

        # Initial enable/disable pass
        self._update_custom_path_widgets()
        
        # Set up tooltip for custom path line edit
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


        # Save button
        save_btn = self.ui_elements.get("textureImporterSaveSettings")
        if save_btn:
            save_btn.clicked.connect(self._save_settings)

        # Set-button (browse for custom folder)
        set_btn = self.ui_elements.get("textureSearchCustomPathSetButton")
        if set_btn:
            set_btn.clicked.connect(self._choose_custom_path)

        # Open Texture Search Names from Settings
        names_btn = self.ui_elements.get("editTextureSearchNamesButton")
        if names_btn:
            try:
                names_btn.clicked.disconnect()
            except Exception:
                pass
            names_btn.clicked.connect(self.open_texture_search_names_ui)


    def _update_custom_path_widgets(self):
        """Enable/disable custom-path widgets based on checkbox state."""
        custom_on = self.ui_elements["textureSearchCustomPathCheckbox"].isChecked()
        for widget_name in (
            "textureSearchCustomPathLineEdit",
            "textureSearchCustomPathSetButton",
            "customSearchFolderPathLabel",
            "createIfDoesntExistCheckbox"
        ):
            w = self.ui_elements.get(widget_name)
            if w:
                w.setEnabled(custom_on)

        # If custom path is off, remove focus so the cursor isn't blinking
        if not custom_on:
            self.ui_elements["textureSearchCustomPathLineEdit"].clearFocus()


    def _choose_custom_path(self):
        """
        Enhanced custom path handling with key substitution and folder creation.
        
        If there's a custom path with keys, resolve it and open/create the folder.
        If no custom path, open folder dialog to select a new path.
        """
        current_path = self.ui_elements["textureSearchCustomPathLineEdit"].text().strip()
        
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
                self.ui_elements["textureSearchCustomPathLineEdit"].setText(folder)

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

    def open_texture_search_names_ui(self):
        """Launch the TextureSearchNamesUI from the Settings window."""
        if not hasattr(self, "_texture_search_names_ui") or self._texture_search_names_ui is None:
            self._texture_search_names_ui = TextureSearchNamesUI(parent=self)
        self._texture_search_names_ui.show()
        self._texture_search_names_ui.raise_()

    def reload_from_disk(self):
        """Re-read JSON and re-apply to widgets (call before showing the window)."""
        self._apply_saved_settings()
        self._update_custom_path_widgets()

    def _load_settings(self):
        """Read JSON from main quick materials settings and return texture_importer section."""
        path = os.path.join(os.path.dirname(__file__), "settings", "quick_materials_settings.json")
        try:
            with open(path, "r") as f:
                all_settings = json.load(f)
            if isinstance(all_settings, dict) and 'texture_importer' in all_settings:
                print(f"[DEBUG] Loaded Texture Importer settings from main settings: {path}")
                return all_settings['texture_importer']
            else:
                print(f"[DEBUG] Main settings JSON missing texture_importer section at {path}; using defaults.")
                return {}
        except Exception as e:
            print(f"[DEBUG] Failed to read main settings at {path}: {e}")
            return {}



    def _apply_saved_settings(self):
        """Tick checkboxes / line-edits from stored JSON."""
        s = self._load_settings() or {}
        mode = s.get("default_mode", "maya_file")
        self.ui_elements["textureSearchMayaFileCheckbox"].setChecked(mode == "maya_file")
        self.ui_elements["textureSearchMayaSourceimagesCheckbox"].setChecked(mode == "sourceimages")
        self.ui_elements["textureSearchCustomPathCheckbox"].setChecked(mode == "custom")
        self.ui_elements["textureSearchCustomPathLineEdit"].setText(s.get("custom_path", ""))
        
        # Create if doesn't exist checkbox
        create_if_not_exists = self.ui_elements.get("createIfDoesntExistCheckbox")
        if create_if_not_exists:
            create_if_not_exists.setChecked(s.get("create_if_doesnt_exist", False))



    def _save_settings(self):
        """Write settings to the main quick materials settings JSON."""
        mode = "maya_file" if self.ui_elements["textureSearchMayaFileCheckbox"].isChecked() else \
            "sourceimages" if self.ui_elements["textureSearchMayaSourceimagesCheckbox"].isChecked() else \
                "custom"
        data = {
            "default_mode": mode,  # maya_file | sourceimages | custom
            "custom_path": self.ui_elements["textureSearchCustomPathLineEdit"].text(),
            # Recursive search settings removed - no longer needed without auto texture pathing
        }
        
        # Add create if doesn't exist setting
        create_if_not_exists = self.ui_elements.get("createIfDoesntExistCheckbox")
        if create_if_not_exists:
            data["create_if_doesnt_exist"] = create_if_not_exists.isChecked()
        
        # Save to main quick materials settings JSON
        try:
            # Import the main quick materials module to access the settings file path
            import os
            script_dir = os.path.dirname(__file__)
            settings_dir = os.path.join(script_dir, "settings")
            os.makedirs(settings_dir, exist_ok=True)
            settings_path = os.path.join(settings_dir, "quick_materials_settings.json")
            
            # Load existing settings or create new structure
            import json
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
            
            # Save back to file
            with open(settings_path, "w") as f:
                json.dump(all_settings, f, indent=2)
                
            # Show yellow notification instead of dialog
            cmds.inViewMessage(amg="<hl>✔ Quick Materials Settings Saved</hl>", pos="topCenter", fade=True)
        except Exception as e:
            cmds.confirmDialog(title="Error", message=f"Failed to save settings: {e}", button=["OK"])

