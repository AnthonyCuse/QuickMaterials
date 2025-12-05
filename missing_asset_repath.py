"""
Missing Asset Repath Tool
---------------------------------

Tool for finding and repathing missing file assets in Maya scenes.
Opens a UI to select a search folder and attempts to repath all missing files.
"""

import os
import glob

import maya.cmds as cmds

try:
    # Maya 2025+
    from PySide6 import QtCore, QtWidgets
    from shiboken6 import wrapInstance
    PYSIDE6 = True
except Exception:
    from PySide2 import QtCore, QtWidgets
    from shiboken2 import wrapInstance
    PYSIDE6 = False

import maya.OpenMayaUI as omui


# Stylesheet matching Material Manager
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
    background-color: #444444;
    border: 1px solid #666666;
    border-radius: 6px;
    padding: 3px 6px;
    margin: 2px;
}

QPushButton:hover {
    background-color: #555555;
    border: 1px solid #777777;
}

QPushButton:pressed {
    background-color: #333333;
    border: 1px solid #555555;
}

QPushButton:disabled {
    color: #888888;
    background-color: #3a3a3a;
    border: 1px solid #555555;
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


def _maya_main_window():
    """Get the Maya main window as a QWidget."""
    ptr = omui.MQtUtil.mainWindow()
    return wrapInstance(int(ptr), QtWidgets.QWidget)


def _get_current_maya_file_dir():
    """Get the directory of the current Maya file, or workspace root if no file is open."""
    try:
        scene_path = cmds.file(q=True, sn=True) or ""
        if scene_path:
            return os.path.dirname(scene_path)
    except Exception:
        pass
    
    try:
        workspace_root = cmds.workspace(q=True, rootDirectory=True) or ""
        if workspace_root:
            return workspace_root.rstrip("/\\")
    except Exception:
        pass
    
    return ""


def _find_all_file_nodes():
    """Find all file texture nodes in the scene."""
    try:
        file_nodes = cmds.ls(type="file") or []
        return file_nodes
    except Exception:
        return []


def _get_file_node_path(file_node):
    """Get the file path from a file node."""
    try:
        if cmds.attributeQuery("fileTextureName", node=file_node, exists=True):
            path = cmds.getAttr(f"{file_node}.fileTextureName")
            return path if path else ""
    except Exception:
        pass
    return ""


def _is_file_missing(file_path):
    """Check if a file path is missing (doesn't exist)."""
    if not file_path:
        return True
    
    # Handle UDIM patterns (e.g., texture.<UDIM>.exr)
    if "<UDIM>" in file_path.upper():
        # For UDIM, check if the pattern itself exists or if any UDIM tile exists
        pattern = file_path.upper().replace("<UDIM>", "*")
        dir_path = os.path.dirname(file_path)
        if dir_path and os.path.isdir(dir_path):
            matches = glob.glob(os.path.join(dir_path, pattern))
            if matches:
                return False
        return True
    
    # Normal file path
    return not os.path.exists(file_path)


def _find_file_in_folder(file_name, search_folder):
    """Search for a file in the given folder (recursively)."""
    if not os.path.isdir(search_folder):
        return None
    
    file_name_lower = file_name.lower()
    
    # Walk through the directory tree
    for root, dirs, files in os.walk(search_folder):
        for f in files:
            if f.lower() == file_name_lower:
                return os.path.join(root, f)
    
    return None


def _repath_file_node(file_node, new_path):
    """Repath a file node to a new path."""
    try:
        if cmds.attributeQuery("fileTextureName", node=file_node, exists=True):
            cmds.setAttr(f"{file_node}.fileTextureName", new_path, type="string")
            return True
    except Exception as e:
        print(f"[MissingAssetRepath] Failed to repath {file_node}: {e}")
        return False
    return False


class MissingAssetRepathDialog(QtWidgets.QDialog):
    WINDOW_OBJECT = "MissingAssetRepathWindow"

    def __init__(self, parent=None):
        old = omui.MQtUtil.findControl(self.WINDOW_OBJECT)
        if old:
            try:
                QtWidgets.QWidget.find(old).close()
            except Exception:
                pass

        super().__init__(parent or _maya_main_window())
        self.setObjectName(self.WINDOW_OBJECT)
        self.setWindowTitle("Missing Asset Repath")
        self.setMinimumWidth(500)
        self.setMinimumHeight(200)

        self._build_ui()
        self._connect_signals()
        self.setStyleSheet(FALLBACK_STYLESHEET)

    def _build_ui(self):
        """Build the UI layout."""
        title = QtWidgets.QLabel("Missing Asset Repath")
        title.setAlignment(QtCore.Qt.AlignCenter)
        title.setStyleSheet("font-weight:600; font-size:16px; padding:2px;")

        # Path selection row
        path_label = QtWidgets.QLabel("Search Folder:")
        self.path_edit = QtWidgets.QLineEdit()
        self.path_edit.setPlaceholderText("Select folder to search for missing assets...")
        self.path_edit.setToolTip("Folder path to search for missing asset files")
        
        self.set_btn = QtWidgets.QPushButton("Set")
        self.set_btn.setFixedWidth(60)
        self.set_btn.setToolTip("Open file explorer to select search folder")

        path_row = QtWidgets.QHBoxLayout()
        path_row.setContentsMargins(0, 0, 0, 0)
        path_row.setSpacing(6)
        path_row.addWidget(path_label)
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(self.set_btn)

        # Buttons row
        buttons_row = QtWidgets.QHBoxLayout()
        buttons_row.setContentsMargins(0, 0, 0, 0)
        buttons_row.setSpacing(6)
        buttons_row.addStretch(1)
        
        self.repath_btn = QtWidgets.QPushButton("Repath Files")
        self.repath_btn.setFixedHeight(28)
        self.repath_btn.setToolTip("Search for missing files and repath them")
        self.repath_btn.setStyleSheet("color: #00f7c8; font-weight:600;")
        
        self.cancel_btn = QtWidgets.QPushButton("Cancel")
        self.cancel_btn.setFixedHeight(28)
        self.cancel_btn.setToolTip("Close the dialog")
        
        buttons_row.addWidget(self.repath_btn)
        buttons_row.addWidget(self.cancel_btn)

        # Main layout
        main_layout = QtWidgets.QVBoxLayout()
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)
        main_layout.addWidget(title)
        main_layout.addLayout(path_row)
        main_layout.addStretch(1)
        main_layout.addLayout(buttons_row)

        outer_frame = QtWidgets.QFrame()
        outer_frame.setObjectName("outerFrame")
        outer_frame.setStyleSheet("QFrame#outerFrame { border: 1px solid #444444; border-radius: 8px; background-color: #333333; }")
        outer_layout = QtWidgets.QVBoxLayout(outer_frame)
        outer_layout.setContentsMargins(10, 10, 10, 10)
        outer_layout.setSpacing(10)
        outer_layout.addLayout(main_layout)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)
        lay.addWidget(title)
        lay.addWidget(outer_frame)

    def _connect_signals(self):
        """Connect UI signals."""
        self.set_btn.clicked.connect(self._select_folder)
        self.repath_btn.clicked.connect(self._repath_files)
        self.cancel_btn.clicked.connect(self.close)

    def _select_folder(self):
        """Open file dialog to select search folder."""
        # Default to current Maya file directory
        start_dir = self.path_edit.text().strip()
        if not start_dir or not os.path.isdir(start_dir):
            start_dir = _get_current_maya_file_dir()
        
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select Folder to Search for Missing Assets",
            start_dir
        )
        
        if folder:
            self.path_edit.setText(folder)

    def _repath_files(self):
        """Find missing files and attempt to repath them."""
        search_folder = self.path_edit.text().strip()
        
        if not search_folder:
            cmds.warning("Please select a search folder first.")
            return
        
        if not os.path.isdir(search_folder):
            cmds.warning(f"Selected folder does not exist: {search_folder}")
            return
        
        # Find all file nodes
        file_nodes = _find_all_file_nodes()
        if not file_nodes:
            cmds.inViewMessage(
                amg="<hl>No file texture nodes found in scene</hl>",
                pos="topCenter",
                fade=True
            )
            return
        
        # Find missing files
        missing_files = []
        for file_node in file_nodes:
            file_path = _get_file_node_path(file_node)
            if file_path and _is_file_missing(file_path):
                missing_files.append((file_node, file_path))
        
        if not missing_files:
            cmds.inViewMessage(
                amg="<hl>All files successfully repathed, check log for details</hl>",
                pos="topCenter",
                fade=True
            )
            print("[MissingAssetRepath] No missing files found in scene.")
            return
        
        # Attempt to repath missing files
        success_count = 0
        failed_count = 0
        failed_files = []
        
        print(f"[MissingAssetRepath] Found {len(missing_files)} missing file(s). Searching in: {search_folder}")
        
        for file_node, old_path in missing_files:
            file_name = os.path.basename(old_path)
            
            # Try to find the file in the search folder
            found_path = _find_file_in_folder(file_name, search_folder)
            
            if found_path:
                # Repath the file node
                if _repath_file_node(file_node, found_path):
                    print(f"[MissingAssetRepath] ✓ Repathed {file_node}: {old_path} → {found_path}")
                    success_count += 1
                else:
                    print(f"[MissingAssetRepath] ✗ Failed to repath {file_node}")
                    failed_count += 1
                    failed_files.append((file_node, old_path))
            else:
                print(f"[MissingAssetRepath] ✗ File not found: {file_name} (from {file_node})")
                failed_count += 1
                failed_files.append((file_node, old_path))
        
        # Display result message
        if failed_count == 0:
            message = f"<hl>All files successfully repathed, check log for details</hl>"
        else:
            message = f"<hl>{failed_count} file(s) failed to repath, check log for details</hl>"
        
        cmds.inViewMessage(
            amg=message,
            pos="topCenter",
            fade=True
        )
        
        # Print summary
        print(f"[MissingAssetRepath] Summary: {success_count} repathed, {failed_count} failed")
        if failed_files:
            print("[MissingAssetRepath] Failed files:")
            for file_node, old_path in failed_files:
                print(f"  - {file_node}: {old_path}")


def show():
    """Show the Missing Asset Repath dialog."""
    dlg = MissingAssetRepathDialog(parent=_maya_main_window())
    dlg.show()
    return dlg

