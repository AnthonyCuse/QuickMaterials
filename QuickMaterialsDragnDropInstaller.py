"""
QuickMaterials Drag-and-Drop Installer
=======================================

Drop this file into Maya viewport to install QuickMaterials.

This installer will:
1. Detect the QuickMaterials package location (works in development and distribution)
2. Copy files to Maya scripts directory
3. Create a shelf button
4. Optionally update userSetup.py for auto-loading

Usage:
- Development: Place installer in QuickMaterials project folder
- Distribution: Place installer in package root with QuickMaterials/ subfolder
"""

import os
import sys
import shutil
import re
import time

# Qt compatibility
try:
    from PySide6 import QtCore, QtWidgets, QtGui
    QT_LIB = 6
except ImportError:
    from PySide2 import QtCore, QtWidgets, QtGui
    QT_LIB = 2

# Global reference to installer UI instance (for singleton pattern)
_installer_ui_instance = None

# Maya drag-and-drop entry point - MUST be defined at module level
# Maya looks for this exact function name when a Python file is dragged in
def onMayaDroppedPythonFile(*args, **kwargs):
    """
    Maya drag-and-drop entry point.
    This function is called automatically when the file is dragged into Maya viewport.
    Handles module reloading to ensure latest code runs on repeated drags.
    """
    global _installer_ui_instance
    
    # Close existing UI if it exists
    if _installer_ui_instance is not None:
        try:
            if hasattr(_installer_ui_instance, 'isVisible') and _installer_ui_instance.isVisible():
                _installer_ui_instance.close()
            _installer_ui_instance = None
        except Exception:
            _installer_ui_instance = None
    
    # Force reload of this module to handle multiple drag-and-drop operations
    # Maya caches modules, so we need to reload it each time
    try:
        import maya.utils
        import importlib.util
        
        if '__file__' in globals():
            file_path = __file__
        else:
            # Try to get file path from the function's module
            file_path = os.path.abspath(kwargs.get('filePath', '') if kwargs else '')
            if not file_path or not os.path.exists(file_path):
                # Fallback: try to get from args
                if args and len(args) > 0:
                    file_path = args[0]
        
        if file_path and os.path.exists(file_path):
            module_name = os.path.splitext(os.path.basename(file_path))[0]
            
            # Remove from cache
            modules_to_remove = [k for k in sys.modules.keys() if k == module_name or k.startswith(module_name + '.')]
            for mod_name in modules_to_remove:
                try:
                    del sys.modules[mod_name]
                except Exception:
                    pass
            
            # Reload the module
            spec = importlib.util.spec_from_file_location(module_name, file_path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                
                # Call _dropped_install from the reloaded module using executeDeferred
                # This ensures the UI is created after Maya's event loop is ready
                def _run_installer():
                    try:
                        if hasattr(module, '_dropped_install'):
                            module._dropped_install()
                    except Exception as e:
                        import traceback
                        traceback.print_exc()
                        show_error_dialog(f"Failed to open installer: {str(e)}")
                
                maya.utils.executeDeferred(_run_installer)
                return
    except Exception as e:
        import traceback
        traceback.print_exc()
    
    # Fallback: If reload fails, try to call _dropped_install directly with deferred execution
    try:
        import maya.utils
        maya.utils.executeDeferred(_dropped_install)
    except Exception:
        import traceback
        traceback.print_exc()


def find_package_directory():
    """
    Find QuickMaterials package directory.
    Works in both development and distribution scenarios.
    
    Returns:
        str: Path to QuickMaterials package directory, or None if not found
    """
    # Get directory where installer is located
    installer_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Scenario 1: Check if QuickMaterials/ subfolder exists (distribution)
    package_subfolder = os.path.join(installer_dir, "QuickMaterials")
    main_file = os.path.join(package_subfolder, "quick_materials.py")
    if os.path.exists(main_file):
        return package_subfolder
    
    # Scenario 2: Check if source files are in same directory (development)
    main_file = os.path.join(installer_dir, "quick_materials.py")
    if os.path.exists(main_file):
        return installer_dir
    
    return None


def show_error_dialog(message):
    """Show error dialog if running in Maya."""
    try:
        import maya.cmds as cmds
        cmds.confirmDialog(
            title="QuickMaterials Installer Error",
            message=message,
            button=["OK"],
            defaultButton="OK"
        )
    except Exception:
        print(f"ERROR: {message}")


def maya_main_window():
    """Return the Maya main window widget as a Python object."""
    try:
        import maya.OpenMayaUI as omui
        try:
            from shiboken6 import wrapInstance
        except ImportError:
            from shiboken2 import wrapInstance
        
        main_window_ptr = omui.MQtUtil.mainWindow()
        if sys.version_info.major >= 3:
            return wrapInstance(int(main_window_ptr), QtWidgets.QWidget)
        else:
            return wrapInstance(int(main_window_ptr), QtWidgets.QWidget)
    except Exception:
        return None


# ============================================================================
# Core Installer Logic
# ============================================================================

class QuickMaterialsInstaller:
    """Core installer logic for QuickMaterials."""
    
    # QuickMaterials loader code for userSetup.py
    LOADER_CODE = '''
# QuickMaterials Auto-Loader (Added by QuickMaterials Installer)
# The installer adds this to userSetup.py so the package is available.
# Quick Materials restores automatically when workspace control exists (from saved workspace).
import maya.utils
import maya.cmds as cmds

def _load_quick_materials():
    """Restore QuickMaterials if workspace control exists (from saved workspace)."""
    try:
        import QuickMaterials.quick_materials as qm
        import importlib
        importlib.reload(qm)
        
        # Check if workspace control exists (means it was saved in workspace)
        control_name = qm.QuickMaterialsUI.workspace_control_name
        if cmds.workspaceControl(control_name, exists=True):
            # Check if it's docked (not floating) - only restore if docked
            is_floating = False
            try:
                is_floating = cmds.workspaceControl(control_name, query=True, floating=True)
            except Exception:
                pass
            
            if not is_floating:
                # Workspace control exists and is docked - restore the UI
                qm.QuickMaterialsUI.restore_from_workspace()
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("[QuickMaterials] Failed to initialize QuickMaterials:", e)

maya.utils.executeDeferred(_load_quick_materials)
'''
    
    LOADER_MARKER = "# QuickMaterials Auto-Loader (Added by QuickMaterials Installer)"
    
    def __init__(self, package_dir, log_callback=None):
        """
        Initialize installer.
        
        Args:
            package_dir: Directory containing QuickMaterials source files
            log_callback: Optional callback function for logging (message, level)
        """
        self.package_dir = package_dir
        self.log_callback = log_callback or self._default_log
        self.maya_version = self._get_maya_version()
        self.user_scripts_dir = self._get_user_scripts_dir()
        self.quick_materials_dir = os.path.join(self.user_scripts_dir, "QuickMaterials") if self.user_scripts_dir else None
        self.user_setup_path = os.path.join(self.user_scripts_dir, "userSetup.py") if self.user_scripts_dir else None
        
    def _default_log(self, message, level="INFO"):
        """Default logging function."""
        print(f"[{level}] {message}")
    
    def log(self, message, level="INFO"):
        """Log a message."""
        self.log_callback(message, level)
    
    def _get_maya_version(self):
        """Get Maya version string."""
        try:
            import maya.cmds as cmds
            version = cmds.about(version=True)
            # Extract version number (e.g., "2024" from "Maya 2024")
            version_short = version.split()[0] if version.split()[0].isdigit() else version.split()[1]
            return version_short
        except Exception:
            return "2024"  # Default
    
    def _get_user_scripts_dir(self):
        """Get user Maya scripts directory (global folder for all versions)."""
        try:
            user_docs = os.path.expanduser("~")
            # Use global scripts folder (works for all Maya versions)
            scripts_dir = os.path.join(user_docs, "Documents", "maya", "scripts")
            return scripts_dir
        except Exception:
            self.log("Failed to determine Maya scripts directory", "ERROR")
            return None
    
    def _get_version_specific_scripts_dir(self):
        """Get version-specific scripts directory (for checking existing installations)."""
        try:
            maya_version = self.maya_version
            user_docs = os.path.expanduser("~")
            scripts_dir = os.path.join(user_docs, "Documents", "maya", maya_version, "scripts")
            return scripts_dir
        except Exception:
            return None
    
    def is_installed(self):
        """Check if QuickMaterials is already installed (in global or version-specific folder)."""
        # Check global folder first
        if self.quick_materials_dir:
            main_file = os.path.join(self.quick_materials_dir, "quick_materials.py")
            if os.path.exists(main_file):
                return True
        
        # Also check version-specific folder (for migration detection)
        version_dir = self._get_version_specific_scripts_dir()
        if version_dir:
            version_quick_materials_dir = os.path.join(version_dir, "QuickMaterials")
            main_file = os.path.join(version_quick_materials_dir, "quick_materials.py")
            if os.path.exists(main_file):
                # Update our path to point to the version-specific installation
                self.quick_materials_dir = version_quick_materials_dir
                self.user_scripts_dir = version_dir
                self.user_setup_path = os.path.join(version_dir, "userSetup.py")
                return True
        
        return False
    
    def install_files(self):
        """Copy QuickMaterials package to Maya scripts directory."""
        if not self.user_scripts_dir:
            self.log("Cannot determine Maya scripts directory", "ERROR")
            return False
        
        try:
            # Create scripts directory if it doesn't exist
            os.makedirs(self.user_scripts_dir, exist_ok=True)
            
            # Create QuickMaterials directory
            if os.path.exists(self.quick_materials_dir):
                self.log("Removing existing installation...", "INFO")
                try:
                    # Try to remove individual files/dirs first, skipping locked ones
                    self._remove_directory_graceful(self.quick_materials_dir)
                except Exception as e:
                    self.log(f"Warning: Could not fully remove existing installation: {str(e)}", "WARNING")
                    # Continue anyway - we'll overwrite files during copy
            
            os.makedirs(self.quick_materials_dir, exist_ok=True)
            
            # Files to copy
            files_to_copy = [
                "quick_materials.py",
                "material_converter.py",
                "texture_importer.py",
                "texture_viewer.py",
                "material_swatch_icon.py",
                "icons_rc.py",
                "__init__.py",
            ]
            
            # Copy files
            copied_count = 0
            for file_name in files_to_copy:
                src = os.path.join(self.package_dir, file_name)
                if os.path.exists(src):
                    dst = os.path.join(self.quick_materials_dir, file_name)
                    shutil.copy2(src, dst)
                    copied_count += 1
                    self.log(f"Copied {file_name}", "INFO")
                else:
                    self.log(f"Warning: {file_name} not found in package", "WARNING")
            
            # Copy directories (settings/ and Settings/ ship defaults: quick_materials_settings_default.json, texture names, etc.)
            dirs_to_copy = ["icons", "QtDesigner", "settings", "Settings"]
            for dir_name in dirs_to_copy:
                src = os.path.join(self.package_dir, dir_name)
                if os.path.exists(src):
                    dst = os.path.join(self.quick_materials_dir, dir_name)
                    try:
                        # Try to remove destination first if it exists
                        if os.path.exists(dst):
                            try:
                                shutil.rmtree(dst)
                            except (PermissionError, OSError):
                                # If locked, try to remove individual files
                                self._remove_directory_graceful(dst)
                        shutil.copytree(src, dst, dirs_exist_ok=True)
                        self.log(f"Copied {dir_name}/ directory", "INFO")
                    except Exception as e:
                        self.log(f"Warning: Could not fully copy {dir_name}/: {str(e)}", "WARNING")
                        # Try to copy anyway with dirs_exist_ok
                        try:
                            shutil.copytree(src, dst, dirs_exist_ok=True)
                            self.log(f"Copied {dir_name}/ directory (with existing files)", "INFO")
                        except Exception as e2:
                            self.log(f"Error copying {dir_name}/: {str(e2)}", "ERROR")
            
            self.log(f"Installed {copied_count} files to {self.quick_materials_dir}", "SUCCESS")
            return True
            
        except Exception as e:
            self.log(f"Error installing files: {str(e)}", "ERROR")
            import traceback
            self.log(traceback.format_exc(), "ERROR")
            return False
    
    def get_current_shelf(self):
        """Get the name of the currently active shelf using the working method."""
        try:
            import maya.cmds as cmds
            import maya.mel as mel
            
            # Get shelf tab layout using MEL (the working method)
            shelf_tab = mel.eval('$tmp=$gShelfTopLevel')
            if not cmds.tabLayout(shelf_tab, exists=True):
                self.log("Couldn't find the main shelf tab layout", "WARNING")
                return None
            
            # Get current shelf name
            current_shelf = cmds.tabLayout(shelf_tab, query=True, selectTab=True)
            if current_shelf and cmds.shelfLayout(current_shelf, exists=True):
                return current_shelf
            
            # Fallback: Get first shelf
            shelves = cmds.tabLayout(shelf_tab, query=True, tabLabel=True) or []
            if shelves:
                return shelves[0]
            
            return None
        except Exception as e:
            self.log(f"Error getting current shelf: {str(e)}", "WARNING")
            return None
    
    def create_shelf_button(self, shelf_name=None, button_label="Quick Materials"):
        """Create shelf button for QuickMaterials using the working method."""
        try:
            import maya.cmds as cmds
            import maya.mel as mel
            
            # Get icon path
            icon_path = os.path.join(self.quick_materials_dir, "icons", "quickMaterialsIcon.png")
            if not os.path.exists(icon_path):
                icon_path = ""  # Use default icon if not found
            
            # Button command
            button_command = """
import QuickMaterials.quick_materials as qm
import importlib
importlib.reload(qm)
qm.QuickMaterialsUI.show_ui()
"""
            
            # Get shelf tab layout using MEL (the working method)
            shelf_tab = mel.eval('$tmp=$gShelfTopLevel')
            if not cmds.tabLayout(shelf_tab, exists=True):
                self.log("Couldn't find the main shelf tab layout", "ERROR")
                return False
            
            # Get or create shelf control
            shelf_ctrl = None
            
            if shelf_name:
                # Check if shelf exists by matching tab labels with controls
                shelves = cmds.tabLayout(shelf_tab, query=True, childArray=True) or []
                labels = cmds.tabLayout(shelf_tab, query=True, tabLabel=True) or []
                
                # Find existing shelf by label
                for shelf_control, tab_label in zip(shelves, labels):
                    if tab_label == shelf_name:
                        shelf_ctrl = shelf_control
                        break
                
                # If not found, create new shelf using addNewShelfTab MEL command
                if not shelf_ctrl:
                    try:
                        safe_label = shelf_name.replace('"', '\\"')
                        mel.eval(f'addNewShelfTab "{safe_label}"')
                        self.log(f"Created new shelf: {shelf_name}", "INFO")
                        
                        # Re-query to get the newly created shelf control
                        shelves = cmds.tabLayout(shelf_tab, query=True, childArray=True) or []
                        labels = cmds.tabLayout(shelf_tab, query=True, tabLabel=True) or []
                        
                        for shelf_control, tab_label in zip(shelves, labels):
                            if tab_label == shelf_name:
                                shelf_ctrl = shelf_control
                                break
                        
                        if not shelf_ctrl:
                            self.log(f"Failed to find newly created shelf control for '{shelf_name}'", "ERROR")
                            return False
                    except Exception as e:
                        self.log(f"Failed to create new shelf: {str(e)}", "ERROR")
                        return False
            else:
                # Use current shelf - get the currently active shelf control
                current_shelf = self.get_current_shelf()
                if current_shelf:
                    # Find the control for the current shelf
                    shelves = cmds.tabLayout(shelf_tab, query=True, childArray=True) or []
                    labels = cmds.tabLayout(shelf_tab, query=True, tabLabel=True) or []
                    
                    for shelf_control, tab_label in zip(shelves, labels):
                        if tab_label == current_shelf:
                            shelf_ctrl = shelf_control
                            shelf_name = current_shelf
                            break
                
                if not shelf_ctrl:
                    # No shelf selected, use first shelf or create one
                    shelves = cmds.tabLayout(shelf_tab, query=True, childArray=True) or []
                    labels = cmds.tabLayout(shelf_tab, query=True, tabLabel=True) or []
                    
                    if shelves and labels:
                        shelf_ctrl = shelves[0]
                        shelf_name = labels[0]
                    else:
                        # Create a default shelf
                        shelf_name = "Custom"
                        try:
                            safe_label = shelf_name.replace('"', '\\"')
                            mel.eval(f'addNewShelfTab "{safe_label}"')
                            shelves = cmds.tabLayout(shelf_tab, query=True, childArray=True) or []
                            labels = cmds.tabLayout(shelf_tab, query=True, tabLabel=True) or []
                            for shelf_control, tab_label in zip(shelves, labels):
                                if tab_label == shelf_name:
                                    shelf_ctrl = shelf_control
                                    break
                        except Exception as e:
                            self.log(f"Failed to create default shelf: {str(e)}", "ERROR")
                            return False
            
            # Verify shelf control exists
            if not shelf_ctrl or not cmds.shelfLayout(shelf_ctrl, exists=True):
                self.log(f"Shelf control '{shelf_ctrl}' does not exist", "ERROR")
                return False
            
            # Remove existing button if it exists
            self.remove_shelf_button(button_label)
            
            # Create button using the shelf control (not the label)
            try:
                button = cmds.shelfButton(
                    parent=shelf_ctrl,
                    label=button_label,
                    annotation=button_label,
                    image=icon_path if icon_path else "commandButton.png",
                    command=button_command,
                    sourceType="python"
                )
                self.log(f"Created shelf button '{button_label}' on shelf '{shelf_name}'", "SUCCESS")
                return True
            except Exception as e:
                self.log(f"Error creating shelf button: {str(e)}", "ERROR")
                import traceback
                self.log(traceback.format_exc(), "ERROR")
                return False
            
        except Exception as e:
            self.log(f"Error creating shelf button: {str(e)}", "ERROR")
            import traceback
            self.log(traceback.format_exc(), "ERROR")
            return False
    
    def remove_shelf_button(self, button_label="Quick Materials"):
        """Remove shelf button from all shelves using the working method."""
        try:
            import maya.cmds as cmds
            import maya.mel as mel
            
            # Get shelf tab layout using MEL
            shelf_tab = mel.eval('$tmp=$gShelfTopLevel')
            if not cmds.tabLayout(shelf_tab, exists=True):
                return False
            
            shelves = cmds.tabLayout(shelf_tab, query=True, tabLabel=True) or []
            removed_count = 0
            
            for shelf in shelves:
                try:
                    # Use shelf name directly (not shelf|ShelfLayout)
                    if not cmds.shelfLayout(shelf, exists=True):
                        continue
                    
                    buttons = cmds.shelfLayout(shelf, query=True, childArray=True) or []
                    for button in buttons:
                        try:
                            annotation = cmds.shelfButton(button, query=True, annotation=True)
                            label = cmds.shelfButton(button, query=True, label=True)
                            # Check both annotation and label
                            if (button_label in annotation or "QuickMaterials" in annotation or
                                button_label in label or "QuickMaterials" in label):
                                cmds.deleteUI(button)
                                removed_count += 1
                                self.log(f"Removed button from shelf '{shelf}'", "INFO")
                        except Exception:
                            continue
                except Exception as e:
                    # Skip shelves that don't exist or can't be queried
                    continue
            
            if removed_count > 0:
                self.log(f"Removed {removed_count} shelf button(s)", "INFO")
            return removed_count > 0
            
        except Exception as e:
            self.log(f"Error removing shelf button: {str(e)}", "WARNING")
            return False
    
    def update_user_setup(self, install=True):
        """Add or remove QuickMaterials loader from userSetup.py."""
        try:
            if not self.user_setup_path:
                self.log("Cannot determine userSetup.py path", "ERROR")
                return False
            
            if install:
                # Read existing file
                existing_content = ""
                if os.path.exists(self.user_setup_path):
                    with open(self.user_setup_path, 'r', encoding='utf-8') as f:
                        existing_content = f.read()
                
                # Check if already present
                if self.LOADER_MARKER in existing_content:
                    # Replace existing loader
                    pattern = r'# QuickMaterials Auto-Loader.*?maya\.utils\.executeDeferred\(_load_quick_materials\)'
                    new_content = re.sub(pattern, self.LOADER_CODE.strip(), existing_content, flags=re.DOTALL)
                    if new_content != existing_content:
                        with open(self.user_setup_path, 'w', encoding='utf-8') as f:
                            f.write(new_content)
                        self.log("Updated existing QuickMaterials loader in userSetup.py", "SUCCESS")
                        return True
                    else:
                        self.log("QuickMaterials loader already present in userSetup.py", "INFO")
                        return True
                else:
                    # Append loader
                    with open(self.user_setup_path, 'a', encoding='utf-8') as f:
                        if existing_content and not existing_content.endswith('\n'):
                            f.write('\n')
                        f.write(self.LOADER_CODE)
                    self.log("Added QuickMaterials loader to userSetup.py", "SUCCESS")
                    return True
            else:
                # Remove loader
                if not os.path.exists(self.user_setup_path):
                    self.log("userSetup.py does not exist", "INFO")
                    return True
                
                with open(self.user_setup_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                if self.LOADER_MARKER not in content:
                    self.log("QuickMaterials loader not found in userSetup.py", "INFO")
                    return True
                
                # Remove loader code
                pattern = r'\n?# QuickMaterials Auto-Loader.*?maya\.utils\.executeDeferred\(_load_quick_materials\)\n?'
                new_content = re.sub(pattern, '', content, flags=re.DOTALL)
                
                with open(self.user_setup_path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                
                self.log("Removed QuickMaterials loader from userSetup.py", "SUCCESS")
                return True
                
        except Exception as e:
            self.log(f"Error updating userSetup.py: {str(e)}", "ERROR")
            import traceback
            self.log(traceback.format_exc(), "ERROR")
            return False
    
    def uninstall(self):
        """Uninstall QuickMaterials from both global and version-specific locations."""
        try:
            success = True
            
            # Remove shelf button first (before removing files)
            self.remove_shelf_button()
            
            # Remove from userSetup.py in both global and version-specific locations
            # Global userSetup.py
            self.update_user_setup(install=False)
            
            # Version-specific userSetup.py (if exists)
            version_dir = self._get_version_specific_scripts_dir()
            if version_dir:
                version_user_setup = os.path.join(version_dir, "userSetup.py")
                if os.path.exists(version_user_setup):
                    # Temporarily switch paths to remove from version-specific userSetup.py
                    original_path = self.user_setup_path
                    self.user_setup_path = version_user_setup
                    self.update_user_setup(install=False)
                    self.user_setup_path = original_path
            
            # Remove files from global location
            if os.path.exists(self.quick_materials_dir):
                self.log("Removing QuickMaterials files from global folder...", "INFO")
                # Try to unlock modules first
                self.log("Attempting to unlock QuickMaterials modules...", "INFO")
                self._try_unlock_modules()
                time.sleep(0.3)  # Brief pause to let file handles release
                try:
                    # Try to remove with error handling for locked files
                    self._remove_directory_safe(self.quick_materials_dir)
                    
                    # Check if directory still exists (might be partially removed)
                    if os.path.exists(self.quick_materials_dir):
                        # Try one more time with graceful removal
                        self._remove_directory_graceful(self.quick_materials_dir)
                        if os.path.exists(self.quick_materials_dir):
                            self.log("Some files/directories could not be removed (locked by Maya)", "WARNING")
                            self.log("You may need to restart Maya to complete uninstallation", "INFO")
                        else:
                            self.log("Removed QuickMaterials directory from global folder", "SUCCESS")
                    else:
                        self.log("Removed QuickMaterials directory from global folder", "SUCCESS")
                except Exception as e:
                    self.log(f"Warning: Could not remove all files (some may be locked): {str(e)}", "WARNING")
                    self.log("You may need to restart Maya to complete uninstallation", "INFO")
            else:
                self.log("QuickMaterials directory not found in global folder", "INFO")
            
            # Also check and remove from version-specific location
            if version_dir:
                version_quick_materials_dir = os.path.join(version_dir, "QuickMaterials")
                if os.path.exists(version_quick_materials_dir):
                    self.log("Removing QuickMaterials files from version-specific folder...", "INFO")
                    try:
                        self._remove_directory_safe(version_quick_materials_dir)
                        if os.path.exists(version_quick_materials_dir):
                            self._remove_directory_graceful(version_quick_materials_dir)
                        if not os.path.exists(version_quick_materials_dir):
                            self.log("Removed QuickMaterials directory from version-specific folder", "SUCCESS")
                    except Exception as e:
                        self.log(f"Warning: Could not remove version-specific installation: {str(e)}", "WARNING")
            
            self.log("Uninstallation complete", "SUCCESS")
            return success
            
        except Exception as e:
            self.log(f"Error during uninstallation: {str(e)}", "ERROR")
            import traceback
            self.log(traceback.format_exc(), "ERROR")
            return False
    
    def _try_unlock_modules(self):
        """Try to unlock QuickMaterials modules before deletion."""
        """Attempts to close windows, unload modules, and release file handles."""
        try:
            import maya.cmds as cmds
            import gc
            
            def _candidate_scripts_dirs():
                dirs = []
                if self.user_scripts_dir:
                    dirs.append(self.user_scripts_dir)
                home = os.path.expanduser("~")
                maya_root = os.path.join(home, "Documents", "maya")
                for variant in ("scripts", "Scripts"):
                    path = os.path.join(maya_root, variant)
                    if os.path.exists(path):
                        dirs.append(path)
                if os.path.exists(maya_root):
                    for entry in os.listdir(maya_root):
                        entry_path = os.path.join(maya_root, entry)
                        if not os.path.isdir(entry_path) or not entry.isdigit():
                            continue
                        for variant in ("scripts", "Scripts"):
                            path = os.path.join(entry_path, variant)
                            if os.path.exists(path):
                                dirs.append(path)
                # Deduplicate
                unique = []
                seen = set()
                for path in dirs:
                    if path not in seen:
                        seen.add(path)
                        unique.append(path)
                return unique
            
            for scripts_dir in _candidate_scripts_dirs():
                if scripts_dir not in sys.path:
                    sys.path.insert(0, scripts_dir)
            
            install_dirs = []
            if self.quick_materials_dir and os.path.exists(self.quick_materials_dir):
                install_dirs.append(self.quick_materials_dir)
            version_dir = self._get_version_specific_scripts_dir()
            if version_dir:
                version_qm = os.path.join(version_dir, "QuickMaterials")
                if os.path.exists(version_qm):
                    install_dirs.append(version_qm)
            for scripts_dir in _candidate_scripts_dirs():
                qm_candidate = os.path.join(scripts_dir, "QuickMaterials")
                if os.path.exists(qm_candidate):
                    install_dirs.append(qm_candidate)
            # dedupe
            seen_dirs = set()
            unique_install_dirs = []
            for path in install_dirs:
                if path not in seen_dirs:
                    seen_dirs.add(path)
                    unique_install_dirs.append(path)
            
            default_workspace_name = "QuickMaterialsWorkspaceControl"
            
            # Try to close QuickMaterials windows
            try:
                import QuickMaterials.quick_materials as qm
                if hasattr(qm, 'QuickMaterialsUI'):
                    if hasattr(qm.QuickMaterialsUI, '_instance') and qm.QuickMaterialsUI._instance is not None:
                        try:
                            qm.QuickMaterialsUI._instance.close()
                            qm.QuickMaterialsUI._instance = None
                            self.log("Closed QuickMaterials window", "INFO")
                        except Exception:
                            pass
                    
                    # Close workspace control
                    workspace_control_name = getattr(qm.QuickMaterialsUI, 'workspace_control_name', default_workspace_name)
                    if cmds.workspaceControl(workspace_control_name, exists=True):
                        try:
                            cmds.deleteUI(workspace_control_name)
                            self.log("Closed workspace control", "INFO")
                        except Exception:
                            pass
            except Exception:
                pass
            
            # Fallback: close default workspace control if it still exists
            if cmds.workspaceControl(default_workspace_name, exists=True):
                try:
                    cmds.deleteUI(default_workspace_name)
                    self.log("Closed default workspace control", "INFO")
                except Exception:
                    try:
                        cmds.workspaceControl(default_workspace_name, edit=True, visible=False)
                        self.log("Hid default workspace control", "INFO")
                    except Exception:
                        pass
            
            # Unregister Qt resources
            try:
                try:
                    from PySide6 import QtCore
                except ImportError:
                    from PySide2 import QtCore
                
                rcc_paths = []
                for install_dir in unique_install_dirs:
                    candidate = os.path.join(install_dir, "QtDesigner", "icons.rcc")
                    if os.path.exists(candidate):
                        rcc_paths.append(candidate)
                if not rcc_paths:
                    try:
                        import QuickMaterials.quick_materials as qm
                        module_dir = os.path.dirname(os.path.abspath(qm.__file__))
                        candidate = os.path.join(module_dir, "QtDesigner", "icons.rcc")
                        if os.path.exists(candidate):
                            rcc_paths.append(candidate)
                    except Exception:
                        pass
                
                for rcc_path in rcc_paths:
                    try:
                        QtCore.QResource.unregisterResource(rcc_path)
                        self.log(f"Unregistered Qt resource: {rcc_path}", "INFO")
                    except Exception:
                        pass
            except Exception:
                pass
            
            # Unload modules
            modules_to_remove = [k for k in sys.modules.keys() if k.startswith('QuickMaterials')]
            for module_name in modules_to_remove:
                try:
                    del sys.modules[module_name]
                except Exception:
                    pass
            
            if modules_to_remove:
                self.log(f"Unloaded {len(modules_to_remove)} module(s)", "INFO")
            
            # Force garbage collection
            for _ in range(3):
                gc.collect()
            
            return True
        except Exception as e:
            self.log(f"Could not unlock modules: {str(e)}", "WARNING")
            return False
    
    def _remove_directory_graceful(self, path):
        """Gracefully remove directory, skipping locked files/directories."""
        """This is used during installation to clear space, skipping locked items."""
        if not os.path.exists(path):
            return
        
        # Try to unlock modules first if this is the QtDesigner folder
        if "QtDesigner" in path:
            self.log("Attempting to unlock QtDesigner folder...", "INFO")
            self._try_unlock_modules()
            time.sleep(0.2)  # Brief pause to let file handles release
        
        try:
            # Try normal removal first
            shutil.rmtree(path)
            return
        except (PermissionError, OSError):
            # If that fails, try to remove what we can
            pass
        # Remove files and directories we can, skip locked ones
        removed_any = False
        for root, dirs, files in os.walk(path, topdown=False):
            # Remove files
            for name in files:
                file_path = os.path.join(root, name)
                try:
                    os.remove(file_path)
                    removed_any = True
                except (PermissionError, OSError):
                    pass  # Skip locked files
            
            # Remove directories
            for name in dirs:
                dir_path = os.path.join(root, name)
                try:
                    os.rmdir(dir_path)
                    removed_any = True
                except (PermissionError, OSError):
                    pass  # Skip locked directories
        
        # Try to remove root directory
        try:
            os.rmdir(path)
            removed_any = True
        except (PermissionError, OSError):
            pass  # Root directory might be locked, that's okay
        
        if not removed_any:
            # If we couldn't remove anything, log a warning
            self.log(f"Could not remove {path} (may be locked by Maya)", "WARNING")
    
    def _remove_directory_safe(self, path):
        """Safely remove directory, handling locked files with retries."""
        """This is used during uninstallation and tries harder to remove everything."""
        max_retries = 3
        retry_delay = 0.5
        
        for attempt in range(max_retries):
            try:
                shutil.rmtree(path)
                return
            except (PermissionError, OSError) as e:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                else:
                    # Last attempt failed - try to remove files individually
                    removed_any = False
                    try:
                        for root, dirs, files in os.walk(path, topdown=False):
                            for name in files:
                                file_path = os.path.join(root, name)
                                try:
                                    os.remove(file_path)
                                    removed_any = True
                                except (PermissionError, OSError):
                                    pass  # Skip locked files
                            for name in dirs:
                                dir_path = os.path.join(root, name)
                                try:
                                    os.rmdir(dir_path)
                                    removed_any = True
                                except (PermissionError, OSError):
                                    pass  # Skip locked directories
                        
                        # Try to remove the root directory
                        try:
                            os.rmdir(path)
                            removed_any = True
                        except (PermissionError, OSError):
                            pass  # Root might still be locked
                        
                        # If we removed some things but not all, that's okay for uninstall
                        # The user can restart Maya to fully clean up
                        if not removed_any:
                            raise e  # Re-raise if we couldn't remove anything
                    except Exception:
                        raise e  # Re-raise original error
    
    def get_installation_info(self):
        """Get information about current installation."""
        info = {
            "installed": self.is_installed(),
            "location": self.quick_materials_dir if self.is_installed() else None,
            "maya_version": self.maya_version,
            "scripts_dir": self.user_scripts_dir,
        }
        return info


# ============================================================================
# Installer UI
# ============================================================================

class QuickMaterialsInstallerUI(QtWidgets.QDialog):
    """Installer UI for QuickMaterials."""
    
    def __init__(self, package_dir, parent=None):
        # Use parent from parameter or get Maya main window (like mGear installer)
        if parent is None:
            parent = maya_main_window()
        super(QuickMaterialsInstallerUI, self).__init__(parent)
        self.package_dir = package_dir
        self.installer = None
        self.setup_ui()
        self.update_ui_state()
        
        # Connect close event to cleanup
        self.finished.connect(self._on_finished)
    
    def _on_finished(self, result):
        """Clean up when dialog is closed."""
        global _installer_ui_instance
        if _installer_ui_instance is self:
            _installer_ui_instance = None
        
    def setup_ui(self):
        """Create the installer UI."""
        self.setWindowTitle("Install Quick Materials")
        # Use setFixedSize like mGear installer (makes it properly draggable)
        self.setFixedSize(500, 600)
        
        # Use WindowType.Window like mGear installer - this makes it draggable
        self.setWindowFlags(QtCore.Qt.WindowType.Window)
        self.setModal(False)
        
        # Apply dark theme
        self.setStyleSheet(self.get_style_sheet())
        
        # Main layout
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setSpacing(6)
        main_layout.setContentsMargins(12, 12, 12, 12)
        
        # Title
        title_label = QtWidgets.QLabel("Install Quick Materials")
        title_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #00f7c8;")
        main_layout.addWidget(title_label)
        
        # Info text
        info_text = QtWidgets.QLabel(
            "QuickMaterials is a professional material management tool for Autodesk Maya.\n"
            "This installer will copy files to the global Maya scripts directory (works for all Maya versions) and create a shelf button."
        )
        info_text.setWordWrap(True)
        info_text.setStyleSheet("color: #cccccc; padding: 6px;")
        main_layout.addWidget(info_text)
        
        # Separator
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setFrameShadow(QtWidgets.QFrame.Sunken)
        line.setStyleSheet("color: #444444;")
        main_layout.addWidget(line)
        
        # Installation options group
        options_group = QtWidgets.QGroupBox("Installation Options")
        options_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 1px solid #555555;
                border-radius: 5px;
                margin-top: 8px;
                padding-top: 8px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
        """)
        options_layout = QtWidgets.QVBoxLayout()
        options_layout.setSpacing(6)
        
        # Shelf installation options
        shelf_group = QtWidgets.QWidget()
        shelf_layout = QtWidgets.QVBoxLayout(shelf_group)
        shelf_layout.setSpacing(4)
        
        self.current_shelf_radio = QtWidgets.QRadioButton("Install to Current Shelf")
        self.current_shelf_radio.setChecked(True)
        self.current_shelf_radio.toggled.connect(self.on_shelf_option_changed)
        shelf_layout.addWidget(self.current_shelf_radio)
        
        self.new_shelf_radio = QtWidgets.QRadioButton("Install to New Shelf")
        self.new_shelf_radio.toggled.connect(self.on_shelf_option_changed)
        shelf_layout.addWidget(self.new_shelf_radio)
        
        shelf_name_layout = QtWidgets.QHBoxLayout()
        self.shelf_name_label = QtWidgets.QLabel("Shelf Name:")
        self.shelf_name_label.setStyleSheet("font-size: 11px; color: #666666;")
        self.shelf_name_edit = QtWidgets.QLineEdit("QuickMaterials")
        self.shelf_name_edit.setEnabled(False)
        shelf_name_layout.addWidget(self.shelf_name_label)
        shelf_name_layout.addWidget(self.shelf_name_edit)
        shelf_layout.addLayout(shelf_name_layout)
        
        options_layout.addWidget(shelf_group)
        
        # userSetup.py option
        self.user_setup_checkbox = QtWidgets.QCheckBox("Update userSetup.py to be able to load Quick Materials on launch")
        self.user_setup_checkbox.setChecked(True)
        options_layout.addWidget(self.user_setup_checkbox)
        
        options_group.setLayout(options_layout)
        main_layout.addWidget(options_group)
        
        # Buttons
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.setSpacing(6)
        
        self.install_button = QtWidgets.QPushButton("Install")
        self.install_button.setStyleSheet("""
            QPushButton {
                background-color: #00f7c8;
                color: #000000;
                font-weight: bold;
                padding: 8px 20px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #00d4a8;
            }
            QPushButton:pressed {
                background-color: #00b894;
            }
        """)
        self.install_button.clicked.connect(self.on_install)
        button_layout.addWidget(self.install_button)
        
        self.uninstall_button = QtWidgets.QPushButton("Uninstall")
        self.uninstall_button.clicked.connect(self.on_uninstall)
        button_layout.addWidget(self.uninstall_button)
        
        # Add Open Install Directory button
        self.open_dir_button = QtWidgets.QPushButton("Open Install Directory")
        self.open_dir_button.clicked.connect(self.on_open_install_directory)
        button_layout.addWidget(self.open_dir_button)
        
        # Add Close button
        self.cancel_button = QtWidgets.QPushButton("Close")
        self.cancel_button.clicked.connect(self._on_cancel)
        button_layout.addWidget(self.cancel_button)
        
        main_layout.addLayout(button_layout)
        
        # Log area
        log_label = QtWidgets.QLabel("Installation Log:")
        log_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        main_layout.addWidget(log_label)
        
        self.log_text = QtWidgets.QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet("""
            QTextEdit {
                background-color: #1a1a1a;
                border: 1px solid #444444;
                border-radius: 4px;
                padding: 5px;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 10px;
            }
        """)
        self.log_text.setMinimumHeight(150)
        main_layout.addWidget(self.log_text)
        
        # Initial log message
        self.log("Ready to install QuickMaterials...", "INFO")
        self.log(f"Package directory: {self.package_dir}", "INFO")
    
    def _on_cancel(self):
        """Handle cancel button click."""
        self.close()
    
    def on_open_install_directory(self):
        """Open the scripts directory (where QuickMaterials is installed) in file explorer."""
        try:
            if not self.installer:
                self.installer = QuickMaterialsInstaller(self.package_dir, self.log)
            
            # Open the scripts directory, not the QuickMaterials subfolder
            scripts_dir = self.installer.user_scripts_dir
            
            if scripts_dir and os.path.exists(scripts_dir):
                import subprocess
                import platform
                
                # Open file explorer to the scripts directory
                if platform.system() == "Windows":
                    subprocess.Popen(f'explorer "{scripts_dir}"')
                elif platform.system() == "Darwin":  # macOS
                    subprocess.Popen(["open", scripts_dir])
                else:  # Linux
                    subprocess.Popen(["xdg-open", scripts_dir])
                
                self.log(f"Opened scripts directory: {scripts_dir}", "INFO")
            else:
                self.log("Scripts directory not available", "WARNING")
        except Exception as e:
            self.log(f"Error opening scripts directory: {str(e)}", "ERROR")
            import traceback
            self.log(traceback.format_exc(), "ERROR")
        
    def get_style_sheet(self):
        """Get the dark theme stylesheet."""
        return """
            QDialog {
                background-color: #2a2a2a;
                color: #ffffff;
            }
            QFrame {
                background-color: #333333;
                border: 0px solid #333333;
                border-radius: 10px;
                padding: 2px;
                margin: 2px;
                color: #ffffff;
            }
            QLabel {
                font-family: 'Segoe UI';
                font-size: 14px;
                color: #ffffff;
                background-color: transparent;
                border: none;
                padding: 0px;
            }
            QRadioButton {
                font-family: 'Segoe UI';
                font-size: 11px;
                color: #dddddd;
                background-color: transparent;
                border: none;
                border-radius: 6px;
                padding: 2px 6px;
                margin: 1px 0;
            }
            QRadioButton:checked {
                color: #00f7c8;
                background-color: transparent;
            }
            QRadioButton::indicator {
                width: 12px;
                height: 12px;
                border: 1px solid #444444;
                border-radius: 3px;
                background-color: #2b2b2b;
            }
            QRadioButton::indicator:checked {
                background-color: #ffffff;
                border: 1px solid #2b2b2b;
            }
            QRadioButton::indicator:unchecked {
                background-color: #2b2b2b;
                border: 1px solid #444444;
            }
            QRadioButton::indicator:checked:hover,
            QRadioButton::indicator:unchecked:hover {
                border: 1px solid #ffffff;
            }
            QRadioButton::indicator:checked:pressed,
            QRadioButton::indicator:unchecked:pressed {
                background-color: #ffffff;
                border: 1px solid #ffffff;
            }
            QRadioButton:disabled {
                color: #666666;
                background-color: transparent;
                border-radius: 6px;
                padding: 2px 6px;
            }
            QCheckBox {
                font-family: 'Segoe UI';
                font-size: 11px;
                color: #dddddd;
                background-color: transparent;
                border: none;
                border-radius: 6px;
                padding: 2px 6px;
                margin: 1px 0;
            }
            QCheckBox:checked {
                color: #00f7c8;
                background-color: transparent;
            }
            QCheckBox::indicator {
                width: 12px;
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
            QCheckBox:disabled {
                color: #666666;
                background-color: transparent;
                border-radius: 6px;
                padding: 2px 6px;
            }
            QLineEdit {
                font-family: 'Segoe UI';
                font-size: 12px;
                color: #ffffff;
                background-color: #222222;
                border: 0px solid #444444;
                border-radius: 8px;
                padding: 2px 3px;
            }
            QLineEdit:hover {
                background-color: #222222;
            }
            QLineEdit:focus {
                border: 0px solid #555555;
                background-color: #1a1a1a;
            }
            QLineEdit:disabled {
                background-color: #333333;
                border: 0px solid #444444;
                color: #888888;
            }
            QPushButton {
                background-color: #444444;
                color: #ffffff;
                border: 1px solid #666666;
                border-radius: 4px;
                padding: 6px 15px;
            }
            QPushButton:hover {
                background-color: #555555;
            }
            QPushButton:pressed {
                background-color: #333333;
            }
            QPushButton:disabled {
                background-color: #2a2a2a;
                color: #666666;
                border: 1px solid #444444;
            }
        """
    
    def on_shelf_option_changed(self):
        """Handle shelf option radio button change."""
        is_enabled = self.new_shelf_radio.isChecked()
        self.shelf_name_edit.setEnabled(is_enabled)
        # Update label color based on enabled state
        if is_enabled:
            self.shelf_name_label.setStyleSheet("font-size: 11px; color: #ffffff;")
        else:
            self.shelf_name_label.setStyleSheet("font-size: 11px; color: #666666;")
    
    def log(self, message, level="INFO"):
        """Add message to log."""
        timestamp = time.strftime("%H:%M:%S")
        level_colors = {
            "INFO": "#cccccc",
            "SUCCESS": "#00f7c8",
            "WARNING": "#ffaa00",
            "ERROR": "#ff4444"
        }
        color = level_colors.get(level, "#cccccc")
        formatted = f'<span style="color: {color}">[{timestamp}] [{level}] {message}</span>'
        self.log_text.append(formatted)
        # Auto-scroll to bottom
        self.log_text.verticalScrollBar().setValue(
            self.log_text.verticalScrollBar().maximum()
        )
        QtWidgets.QApplication.processEvents()
    
    def update_ui_state(self):
        """Update UI based on installation status."""
        if not self.installer:
            self.installer = QuickMaterialsInstaller(self.package_dir, self.log)
        
        info = self.installer.get_installation_info()
        is_installed = info["installed"]
        
        if is_installed:
            self.install_button.setText("Reinstall")
        else:
            self.install_button.setText("Install")
    
    def on_install(self):
        """Handle install/reinstall button click."""
        is_reinstall = self.install_button.text() == "Reinstall"
        
        if is_reinstall:
            # Reinstall: uninstall first, then install
            self.log("Starting reinstallation...", "INFO")
            reply = QtWidgets.QMessageBox.question(
                self,
                "Reinstall QuickMaterials",
                "This will uninstall and reinstall QuickMaterials. Continue?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No
            )
            
            if reply != QtWidgets.QMessageBox.Yes:
                return
            
            # Uninstall first
            self.set_buttons_enabled(False)
            try:
                if not self.installer:
                    self.installer = QuickMaterialsInstaller(self.package_dir, self.log)
                self.installer.uninstall()
                QtWidgets.QApplication.processEvents()
                time.sleep(0.5)  # Brief pause between uninstall and install
            except Exception as e:
                self.log(f"Error during uninstall step: {str(e)}", "ERROR")
                self.set_buttons_enabled(True)
                return
        
        # Continue with installation
        self.log("Starting installation...", "INFO")
        
        # Disable buttons during installation
        self.set_buttons_enabled(False)
        
        try:
            # Initialize installer
            if not self.installer:
                self.installer = QuickMaterialsInstaller(self.package_dir, self.log)
            
            # Get options
            shelf_name = None
            if self.new_shelf_radio.isChecked():
                shelf_name = self.shelf_name_edit.text().strip()
                if not shelf_name:
                    self.log("Shelf name cannot be empty", "ERROR")
                    self.set_buttons_enabled(True)
                    return
            
            update_user_setup = self.user_setup_checkbox.isChecked()
            
            # Install files
            if not self.installer.install_files():
                self.log("Installation failed", "ERROR")
                self.set_buttons_enabled(True)
                return
            
            # Create shelf button
            self.installer.create_shelf_button(shelf_name)
            
            # Update userSetup.py
            if update_user_setup:
                self.installer.update_user_setup(install=True)
            else:
                self.log("Skipping userSetup.py update (unchecked)", "INFO")
            
            self.log("Installation complete! Restart Maya before uninstalling.", "SUCCESS")
            
        except Exception as e:
            self.log(f"Installation error: {str(e)}", "ERROR")
            import traceback
            self.log(traceback.format_exc(), "ERROR")
        finally:
            self.set_buttons_enabled(True)
            self.update_ui_state()
    
    def on_uninstall(self):
        """Handle uninstall button click."""
        reply = QtWidgets.QMessageBox.question(
            self,
            "Uninstall QuickMaterials",
            "Are you sure you want to uninstall QuickMaterials?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No
        )
        
        if reply != QtWidgets.QMessageBox.Yes:
            return
        
        self.log("Starting uninstallation...", "INFO")
        self.set_buttons_enabled(False)
        
        try:
            if not self.installer:
                self.installer = QuickMaterialsInstaller(self.package_dir, self.log)
            
            self.installer.uninstall()
            self.log("Uninstallation complete", "SUCCESS")
            
        except Exception as e:
            self.log(f"Uninstallation error: {str(e)}", "ERROR")
            import traceback
            self.log(traceback.format_exc(), "ERROR")
        finally:
            self.set_buttons_enabled(True)
            self.update_ui_state()
    
    def set_buttons_enabled(self, enabled):
        """Enable or disable all buttons."""
        self.install_button.setEnabled(enabled)
        self.uninstall_button.setEnabled(enabled)


# ============================================================================
# Main Entry Point
# ============================================================================

def _dropped_install():
    """Main installer function called when file is dragged into Maya."""
    global _installer_ui_instance
    
    try:
        # Close existing UI if it exists
        if _installer_ui_instance is not None:
            try:
                if hasattr(_installer_ui_instance, 'isVisible') and _installer_ui_instance.isVisible():
                    _installer_ui_instance.close()
            except Exception:
                pass
            _installer_ui_instance = None
        
        # Find package directory
        package_dir = find_package_directory()
        
        if package_dir is None:
            error_msg = (
                "QuickMaterials package not found!\n\n"
                f"Installer location: {os.path.dirname(os.path.abspath(__file__))}\n\n"
                "Please ensure QuickMaterials files are either:\n"
                "1. In the same directory as this installer (development), or\n"
                "2. In a 'QuickMaterials/' subfolder (distribution)"
            )
            show_error_dialog(error_msg)
            return
        
        # Create and show installer UI (parent is set in __init__)
        _installer_ui_instance = QuickMaterialsInstallerUI(package_dir)
        _installer_ui_instance.show()
        
    except Exception as e:
        error_msg = f"Installer error: {str(e)}"
        show_error_dialog(error_msg)
        import traceback
        traceback.print_exc()


# Check if we're running in Maya and execute installer
# This runs when the module is first imported (initial drag, not reloads)
try:
    import maya.cmds as cmds
    import maya.utils
    is_maya = True
    
    # Check if this is a fresh import (not a reload)
    # If the module name is already in sys.modules, it's likely a reload
    module_name = os.path.splitext(os.path.basename(__file__))[0] if '__file__' in globals() else None
    is_reload = module_name and module_name in sys.modules
    
    # Only execute on initial import, not on reloads
    # Reloads are handled by onMayaDroppedPythonFile
    if not is_reload:
        # Use executeDeferred to ensure UI is created after Maya's event loop is ready
        def _execute_installer():
            """Deferred execution of installer."""
            try:
                _dropped_install()
            except Exception as e:
                import traceback
                traceback.print_exc()
                show_error_dialog(f"Failed to open installer: {str(e)}")
        
        maya.utils.executeDeferred(_execute_installer)
        
except ImportError:
    is_maya = False
