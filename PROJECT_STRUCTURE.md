# QuickMaterials Project Structure

## Recommended Structure for Development

### Current Structure (Keep This):
```
QuickMaterials/                    # Your main project folder (Git repo)
├── quick_materials.py
├── material_manager.py
├── texture_importer.py
├── installer.py                   # Installer logic (NEW)
├── installer_ui.py                # Installer UI (NEW)
├── QuickMaterialsDragnDropInstaller.py  # Main installer entry point (NEW)
├── icons/
├── QtDesigner/
├── __init__.py
└── (all other source files)
```

### Why This Works:
- ✅ Everything in one place for development
- ✅ Installer is part of the project (version controlled)
- ✅ Easy to work with in Cursor
- ✅ Simple file paths

## Development Workflow

### Working in Cursor:
1. **Open QuickMaterials folder** as your project
2. **Create installer files** in the same folder:
   - `installer.py` - Core installer logic
   - `installer_ui.py` - UI class
   - `QuickMaterialsDragnDropInstaller.py` - Entry point (what user drags)
3. **Test installer** by dragging `QuickMaterialsDragnDropInstaller.py` into Maya
4. **Commit to git** - Installer is part of source code

### Installer Entry Point:
```python
# QuickMaterialsDragnDropInstaller.py
"""
QuickMaterials Drag-and-Drop Installer
Drop this file into Maya viewport to install QuickMaterials
"""

import os
import sys

# Get directory where this installer file is located
INSTALLER_DIR = os.path.dirname(os.path.abspath(__file__))

# The QuickMaterials package should be in the same directory
PACKAGE_DIR = os.path.join(INSTALLER_DIR, "QuickMaterials")

# Import installer UI
sys.path.insert(0, INSTALLER_DIR)
from installer_ui import QuickMaterialsInstallerUI

# Show installer UI
if __name__ == "__main__":
    app = QuickMaterialsInstallerUI()
    app.show()
```

## Distribution Package Creation

### Create a Packaging Script:
```python
# create_release_package.py
"""
Script to create distribution package
Run this when ready to release a version
"""

import os
import shutil
import zipfile

def create_release_package(version="1.0.0"):
    """Create distribution zip file."""
    
    # Source directory (your project)
    source_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Output directory
    dist_dir = os.path.join(source_dir, "dist")
    os.makedirs(dist_dir, exist_ok=True)
    
    # Package name
    package_name = f"QuickMaterials_v{version}"
    package_dir = os.path.join(dist_dir, package_name)
    
    # Create package directory
    if os.path.exists(package_dir):
        shutil.rmtree(package_dir)
    os.makedirs(package_dir)
    
    # Files to include
    files_to_copy = [
        "quick_materials.py",
        "material_manager.py",
        "texture_importer.py",
        "texture_viewer.py",
        "material_swatch_icon.py",
        "icons_rc.py",
        "__init__.py",
        "QuickMaterialsDragnDropInstaller.py",  # Installer entry point
    ]
    
    # Copy files
    for file in files_to_copy:
        src = os.path.join(source_dir, file)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(package_dir, file))
    
    # Copy directories
    dirs_to_copy = ["icons", "QtDesigner"]
    for dir_name in dirs_to_copy:
        src = os.path.join(source_dir, dir_name)
        dst = os.path.join(package_dir, dir_name)
        if os.path.exists(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
    
    # Create QuickMaterials subdirectory (what gets installed)
    quick_materials_dir = os.path.join(package_dir, "QuickMaterials")
    os.makedirs(quick_materials_dir, exist_ok=True)
    
    # Copy all tool files to QuickMaterials subdirectory
    for file in files_to_copy:
        if file != "QuickMaterialsDragnDropInstaller.py":  # Don't copy installer
            src = os.path.join(source_dir, file)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(quick_materials_dir, file))
    
    # Copy directories to QuickMaterials subdirectory
    for dir_name in dirs_to_copy:
        src = os.path.join(source_dir, dir_name)
        dst = os.path.join(quick_materials_dir, dir_name)
        if os.path.exists(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
    
    # Create README.txt
    readme_content = f"""QuickMaterials v{version}
=====================

INSTALLATION:
1. Drag "QuickMaterialsDragnDropInstaller.py" into Maya viewport
2. Follow the installer instructions
3. Restart Maya

REQUIREMENTS:
- Maya 2024 or later
- Python 2.7 or 3.x (included with Maya)

For more information, visit: https://github.com/yourname/QuickMaterials
"""
    
    with open(os.path.join(package_dir, "README.txt"), "w") as f:
        f.write(readme_content)
    
    # Create zip file
    zip_path = os.path.join(dist_dir, f"{package_name}.zip")
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(package_dir):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, package_dir)
                zipf.write(file_path, arcname)
    
    print(f"Created release package: {zip_path}")
    return zip_path

if __name__ == "__main__":
    create_release_package("1.0.0")
```

### Final Distribution Structure:
```
dist/
└── QuickMaterials_v1.0.0.zip
    └── QuickMaterials_v1.0.0/
        ├── QuickMaterialsDragnDropInstaller.py  ← User drags this
        ├── QuickMaterials/                      ← Gets installed to Maya
        │   ├── quick_materials.py
        │   ├── material_manager.py
        │   ├── icons/
        │   └── QtDesigner/
        └── README.txt
```

## Recommended Approach

### For Development (What We'll Do):
1. **Keep everything in QuickMaterials/** folder
2. **Create installer files** in QuickMaterials/:
   - `installer.py`
   - `installer_ui.py`
   - `QuickMaterialsDragnDropInstaller.py`
3. **Test installer** directly from project folder
4. **Commit to git** - All files version controlled

### For Distribution (Later):
1. **Run packaging script** (`create_release_package.py`)
2. **Creates clean package** in `dist/` folder
3. **Upload zip** to GitHub/Gumroad
4. **Keep dist/ out of git** (add to .gitignore)

## .gitignore Addition

```
# Distribution packages
dist/
*.zip
!*.zip.example
```

## Workflow Summary

### Development:
```
QuickMaterials/ (your project)
├── installer.py                    ← We'll create this
├── installer_ui.py                 ← We'll create this
├── QuickMaterialsDragnDropInstaller.py  ← We'll create this
└── (all source files)
```

### Testing:
- Drag `QuickMaterialsDragnDropInstaller.py` into Maya
- Installer finds `QuickMaterials/` folder in same directory
- Works perfectly for testing

### Distribution:
- Run `create_release_package.py`
- Creates `dist/QuickMaterials_v1.0.0.zip`
- Upload zip to GitHub/Gumroad
- Users extract and drag installer

## Answer to Your Question

**Should installer be in QuickMaterials folder?**
✅ **YES** - Keep it in the project folder for development

**Should we reorganize?**
❌ **NO** - Current structure is fine, no need to reorganize

**Where to generate installer?**
✅ **In QuickMaterials/** - Same folder as your source code

**Why this works:**
- Installer is part of your project (version controlled)
- Easy to develop and test
- Packaging script creates clean distribution later
- No need to reorganize existing structure

## Next Steps

1. I'll create installer files in your QuickMaterials/ folder
2. Installer will look for QuickMaterials/ folder in same directory
3. You test by dragging installer into Maya
4. Later, create packaging script for distribution
5. Keep everything simple and in one place

