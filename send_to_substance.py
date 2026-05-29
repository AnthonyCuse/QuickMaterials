# send_to_substance.py
# Maya — export selection to FBX and push to Adobe Substance 3D Painter via
# remote scripting (preferred) or command-line fallback.
#
# Remote scripting requires Painter to be launched with: --enable-remote-scripting
# Optional env: SUBSTANCE_PAINTER_EXE = full path to Adobe Substance 3D Painter.exe

from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
import time
import http.client

import maya.cmds as cmds

try:
    from PySide6 import QtCore, QtWidgets
except Exception:
    from PySide2 import QtCore, QtWidgets


def _stylesheet():
    try:
        from QuickMaterials.material_converter import FALLBACK_STYLESHEET as base
    except Exception:
        try:
            from .material_converter import FALLBACK_STYLESHEET as base
        except Exception:
            base = ""
    # Extra polish for primary actions (matches Material Tools grey / teal accents elsewhere)
    extra = """
    QPushButton#sendToSubstancePrimaryButton {
        background-color: #00f7c8;
        color: #1a1a1a;
        font-weight: 600;
        padding: 8px 14px;
        border-radius: 8px;
        border: none;
    }
    QPushButton#sendToSubstancePrimaryButton:hover { background-color: #33facf; }
    QPushButton#sendToSubstancePrimaryButton:pressed { background-color: #00c9a3; }
    QPushButton#sendToSubstanceCloseButton {
        padding: 8px 18px;
        border-radius: 8px;
    }
    QPushButton#sendToSubstanceLaunchRemoteButton {
        padding: 8px 14px;
        border-radius: 8px;
        background-color: #444444;
        color: #ffffff;
        font-weight: 600;
    }
    QPushButton#sendToSubstanceLaunchRemoteButton:hover { background-color: #555555; }
    QLabel#sendToSubstanceLaunchRemoteNote {
        color: #ff9933;
        font-size: 11px;
        font-weight: 600;
        padding-left: 8px;
    }
    QGroupBox#sendToSubstanceImportGroup {
        font-weight: 600;
        border: 1px solid #555555;
        border-radius: 8px;
        margin-top: 10px;
        padding-top: 10px;
    }
    QGroupBox#sendToSubstanceImportGroup::title {
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 6px;
    }
    """
    return base + extra


QM_SETTINGS_ORG = "QuickMaterials"
QM_SETTINGS_APP = "SendToSubstance"


def _workflow_combo_entries():
    """(label, ProjectWorkflow member name) for substance_painter.project.ProjectWorkflow."""
    return (
        ("Default (no UDIM workflow)", "Default"),
        ("UV Tiles — preserve tile layout (shared stack per material)", "UVTile"),
        ("UV Tiles — one Texture Set per UV Tile (legacy)", "TextureSetPerUVTile"),
    )


_VALID_PROJECT_WORKFLOW_KEYS = frozenset(m for _, m in _workflow_combo_entries())


def _normalize_workflow_member(member):
    """Ensure we pass a valid ProjectWorkflow attribute name into the Painter script."""
    if member is None:
        return "Default"
    s = str(member).strip()
    return s if s in _VALID_PROJECT_WORKFLOW_KEYS else "Default"


def _selected_mesh_transforms():
    """Return transform roots for selected polygon meshes (deduped, stable order)."""
    sel = cmds.ls(sl=True, long=True, objectsOnly=True) or []
    roots = []
    for node in sel:
        ntype = cmds.nodeType(node)
        if ntype == "transform":
            if cmds.listRelatives(node, shapes=True, type="mesh", fullPath=True):
                roots.append(node)
        elif ntype == "mesh":
            parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
            if parents:
                roots.append(parents[0])
    # Dedupe preserving order
    seen = set()
    out = []
    for r in roots:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def _ensure_fbx_plugin():
    try:
        if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
            cmds.loadPlugin("fbxmaya", quiet=True)
    except Exception:
        pass


def _export_selection_fbx(path):
    """Export current selection to FBX at path. Caller ensures selection is valid."""
    _ensure_fbx_plugin()
    norm = os.path.normpath(path)
    # Maya expects forward slashes for file command on all platforms
    export_path = norm.replace("\\", "/")
    cmds.file(
        export_path,
        force=True,
        type="FBX export",
        exportSelected=True,
        preserveReferences=False,
    )


def _expr_remote(inner_source):
    """Wrap Painter-side Python so the HTTP API returns a string (not JSON null)."""
    inner_lit = repr(inner_source)
    return (
        "(lambda __qm_ns: (exec("
        + inner_lit
        + ", __qm_ns) or __qm_ns.get('_qm_result', 'missing')))({})"
    )


def _build_remote_script(mesh_path, workflow_member, import_cameras, preserve_strokes, auto_unwrap):
    """
    If a Painter project is open: reload mesh (preserve strokes / cameras options).
    Otherwise: create a new project from the mesh (workflow / unwrap options).
    """
    mp_lit = repr(os.path.normpath(mesh_path))
    wf_key = _normalize_workflow_member(workflow_member)
    wf_key_lit = repr(wf_key)
    ic_lit = repr(bool(import_cameras))
    ps_lit = repr(bool(preserve_strokes))
    au_lit = repr(bool(auto_unwrap))

    inner = (
        "import substance_painter as sp\n"
        "import inspect as _insp\n"
        "mesh_path = "
        + mp_lit
        + "\n"
        "_ic = "
        + ic_lit
        + "\n"
        "_ps = "
        + ps_lit
        + "\n"
        "_au_try = "
        + au_lit
        + "\n"
        "try:\n"
        "    if sp.project.is_open():\n"
        "        sp.project.reload_mesh(\n"
        "            mesh_path,\n"
        "            sp.project.MeshReloadingSettings(\n"
        "                import_cameras=_ic,\n"
        "                preserve_strokes=_ps,\n"
        "            ),\n"
        "            lambda _status: None,\n"
        "        )\n"
        "        _qm_result = 'reload_started'\n"
        "    else:\n"
        "        _pw = {\n"
        "            'Default': sp.project.ProjectWorkflow.Default,\n"
        "            'UVTile': sp.project.ProjectWorkflow.UVTile,\n"
        "            'TextureSetPerUVTile': sp.project.ProjectWorkflow.TextureSetPerUVTile,\n"
        "        }\n"
        "        _wf_key = "
        + wf_key_lit
        + "\n"
        "        if _wf_key not in _pw:\n"
        "            _wf_key = 'Default'\n"
        "        _wf = _pw[_wf_key]\n"
        "        _kw = dict(import_cameras=_ic, project_workflow=_wf)\n"
        "        if _au_try:\n"
        "            sig = _insp.signature(sp.project.Settings.__init__)\n"
        "            for _k in (\n"
        "                'automatic_uv_unwrapping',\n"
        "                'auto_uv_unwrap',\n"
        "                'enable_automatic_uv_unwrapping',\n"
        "            ):\n"
        "                if _k in sig.parameters:\n"
        "                    _kw[_k] = True\n"
        "                    break\n"
        "        sp.project.create(\n"
        "            mesh_file_path=mesh_path,\n"
        "            settings=sp.project.Settings(**_kw),\n"
        "        )\n"
        "        _qm_result = 'project_created wf=' + _wf_key\n"
        "except Exception as _exc:\n"
        "    _qm_result = 'error:' + str(_exc)\n"
    )
    return _expr_remote(inner)


def _log_send_substance(message):
    print("[QuickMaterials · Send to Substance] {}".format(message))


def _viewport_notify_exported_to_substance():
    """Short yellow hint at top of viewport; detail goes to script editor log."""
    msg = "Mesh(es) exported to Substance"
    try:
        cmds.inViewMessage(
            amg="<yellow>{}</yellow>".format(msg),
            pos="topCenter",
            fade=True,
            fts=12,
        )
    except Exception:
        try:
            cmds.inViewMessage(amg=msg, pos="topCenter", fade=True, fts=12)
        except Exception:
            pass


def _normalize_remote_response(raw):
    """Turn Painter HTTP body into a user-facing string; detect null/ambiguous."""
    if raw is None:
        return None, True
    t = raw.strip()
    if not t:
        return None, True
    tl = t.lower()
    if tl in ("null", "none"):
        return None, True
    # Some builds may return JSON-encoded string
    if (t.startswith('"') and t.endswith('"')) or (t.startswith("'") and t.endswith("'")):
        try:
            return json.loads(t), False
        except Exception:
            pass
    return t, False


def _remote_painter_exec_python(script, host="localhost", port=60041, timeout=120):
    payload = {"python": base64.b64encode(script.encode("utf-8")).decode("ascii")}
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-type": "application/json", "Accept": "application/json"}
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("POST", "/run.json", body, headers)
        resp = conn.getresponse()
        raw = resp.read()
        if resp.status != 200:
            raise RuntimeError(
                "Painter HTTP {}: {}".format(resp.status, raw.decode("utf-8", errors="replace"))
            )
        return raw.decode("utf-8", errors="replace").strip()
    finally:
        conn.close()


def _remote_painter_ping(host="localhost", port=60041, timeout=2):
    """True if Painter remote scripting HTTP server is accepting connections."""
    conn = None
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.connect()
        return True
    except Exception:
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _wait_for_remote_painter(timeout_seconds=120, poll_interval=1.0):
    """
    Block until Painter --enable-remote-scripting is reachable, or timeout.
    Processes Qt events so Maya stays responsive.
    """
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if _remote_painter_ping():
            return True
        try:
            app = QtWidgets.QApplication.instance()
            if app:
                app.processEvents()
        except Exception:
            pass
        time.sleep(poll_interval)
    return False


def _find_painter_exe():
    env = os.environ.get("SUBSTANCE_PAINTER_EXE", "").strip()
    if env and os.path.isfile(env):
        return env
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    candidates = [
        os.path.join(pf, "Adobe", "Adobe Substance 3D Painter", "Adobe Substance 3D Painter.exe"),
        os.path.join(pf86, "Adobe", "Adobe Substance 3D Painter", "Adobe Substance 3D Painter.exe"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def _launch_painter_cli_mesh(mesh_path, spp_path=None):
    """Launch Painter with --mesh. If spp_path is set, mesh is applied to that project."""
    exe = _find_painter_exe()
    if not exe:
        return False, None
    mp = os.path.normpath(mesh_path)
    args = [exe, "--mesh", mp]
    if spp_path and os.path.isfile(spp_path):
        args.append(os.path.normpath(spp_path))
    subprocess.Popen(args, close_fds=(os.name != "nt"))
    return True, exe


def _launch_painter_remote_scripting_only():
    """Start Painter with --enable-remote-scripting (no mesh). Returns (ok, exe_or_none)."""
    exe = _find_painter_exe()
    if not exe:
        return False, None
    subprocess.Popen([exe, "--enable-remote-scripting"], close_fds=(os.name != "nt"))
    return True, exe


class SendToSubstanceDialog(QtWidgets.QDialog):
    _instance = None

    @classmethod
    def show_dialog(cls):
        if cls._instance and cls._instance.isVisible():
            cls._instance.raise_()
            cls._instance.activateWindow()
            return cls._instance
        cls._instance = cls()
        cls._instance.show()
        cls._instance.raise_()
        cls._instance.activateWindow()
        return cls._instance

    def __init__(self, parent=None):
        try:
            import maya.OpenMayaUI as omui
            mw_ptr = omui.MQtUtil.mainWindow()
            if mw_ptr:
                try:
                    from shiboken6 import wrapInstance
                except ImportError:
                    from shiboken2 import wrapInstance
                parent = wrapInstance(int(mw_ptr), QtWidgets.QWidget)
        except Exception:
            pass

        super(SendToSubstanceDialog, self).__init__(parent)
        self.setObjectName("SendToSubstanceDialog")
        self.setWindowTitle("Send to Substance Painter")
        self.setMinimumWidth(540)

        self._temp_fbx = None
        self._settings = QtCore.QSettings(QM_SETTINGS_ORG, QM_SETTINGS_APP)

        title = QtWidgets.QLabel("Send to Substance Painter")
        title.setStyleSheet("font-weight: 600; font-size: 16px; padding: 4px 0 8px 0;")

        desc = QtWidgets.QLabel(
            "<p><b>What this does</b><br/>"
            "Exports your Maya selection to a temporary FBX and sends it to Substance Painter "
            "via the remote API.</p>"
            "<p><b>Behaviour</b><br/>"
            "If a Painter project is already open, the mesh is <b>reloaded</b> into that scene "
            "(see preserve strokes below). "
            "If nothing is open in Painter, a <b>new project</b> is created from the FBX using "
            "the settings in the box below.</p>"
            "<p><b>Launch Painter</b><br/>"
            "Use <b>Launch Substance with remote scripting</b> (or start Painter with "
            "<code>--enable-remote-scripting</code>) so Maya can reach the API on localhost.</p>"
            "<p><b>If Painter is not open</b><br/>"
            "Send starts Painter with <code>--enable-remote-scripting</code>, waits for it to load, "
            "then creates the project with your settings (including UV Tiles).</p>"
        )
        desc.setWordWrap(True)
        desc.setOpenExternalLinks(False)
        desc.setTextFormat(QtCore.Qt.RichText)

        import_group = QtWidgets.QGroupBox(
            "New Substance File Settings (Only applicable when creating a new file)"
        )
        import_group.setObjectName("sendToSubstanceImportGroup")

        self._workflow_combo = QtWidgets.QComboBox()
        for label, member in _workflow_combo_entries():
            self._workflow_combo.addItem(label)
            idx = self._workflow_combo.count() - 1
            self._workflow_combo.setItemData(idx, member, QtCore.Qt.UserRole)
        self._workflow_combo.setToolTip(
            "Maps to substance_painter.project.ProjectWorkflow. Used when creating a new project."
        )

        self._import_cameras_cb = QtWidgets.QCheckBox("Import cameras from FBX")
        self._import_cameras_cb.setChecked(True)
        self._import_cameras_cb.setToolTip("Passed to project.Settings and MeshReloadingSettings.")

        self._auto_unwrap_cb = QtWidgets.QCheckBox("Auto unwrap UVs (when supported)")
        self._auto_unwrap_cb.setChecked(False)
        self._auto_unwrap_cb.setToolTip(
            "Official Python docs do not list this on Settings for all versions. "
            "When checked, new-project creation tries optional Settings parameters if your Painter build exposes them; "
            "otherwise Painter uses its defaults. Reload mesh always uses unwrap settings stored in the project."
        )

        ig_layout = QtWidgets.QVBoxLayout(import_group)
        ig_layout.addWidget(QtWidgets.QLabel("UV / UDIM workflow:"))
        ig_layout.addWidget(self._workflow_combo)
        ig_layout.addWidget(self._import_cameras_cb)
        ig_layout.addWidget(self._auto_unwrap_cb)

        self._launch_remote_btn = QtWidgets.QPushButton("Launch Substance with remote scripting")
        self._launch_remote_btn.setObjectName("sendToSubstanceLaunchRemoteButton")
        self._launch_remote_btn.setToolTip(
            "Starts Adobe Substance 3D Painter with --enable-remote-scripting "
            "so this tool can call the remote API on localhost (default port 60041). "
            "Use this when updating mesh in an open .spp."
        )

        self._send_btn = QtWidgets.QPushButton("Send Mesh to Substance Painter")
        self._send_btn.setObjectName("sendToSubstancePrimaryButton")
        self._send_btn.setToolTip(
            "Reload mesh if a Painter project is open; otherwise create a new project from the FBX."
        )

        self._preserve_strokes_cb = QtWidgets.QCheckBox("Preserve strokes on mesh reload")
        self._preserve_strokes_cb.setChecked(True)
        self._preserve_strokes_cb.setToolTip(
            "Used when a Painter project is already open (reload_mesh). "
            "Ignored when Painter creates a new project."
        )
        self._preserve_strokes_cb.setStyleSheet("margin-left: 10px;")

        send_section = QtWidgets.QWidget()
        send_layout = QtWidgets.QVBoxLayout(send_section)
        send_layout.setContentsMargins(0, 0, 0, 0)
        send_layout.setSpacing(6)
        send_layout.addWidget(self._send_btn)
        send_layout.addWidget(self._preserve_strokes_cb)

        self._close_btn = QtWidgets.QPushButton("Close")
        self._close_btn.setObjectName("sendToSubstanceCloseButton")

        self._launch_remote_note = QtWidgets.QLabel("(Required if loading existing files)")
        self._launch_remote_note.setObjectName("sendToSubstanceLaunchRemoteNote")

        launch_row = QtWidgets.QHBoxLayout()
        launch_row.setSpacing(0)
        launch_row.addWidget(self._launch_remote_btn, 0, QtCore.Qt.AlignVCenter)
        launch_row.addWidget(self._launch_remote_note, 0, QtCore.Qt.AlignVCenter)
        launch_row.addStretch(1)

        action_layout = QtWidgets.QVBoxLayout()
        action_layout.setSpacing(10)
        action_layout.addWidget(send_section)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(self._close_btn)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(desc)
        layout.addSpacing(8)
        layout.addWidget(import_group)
        layout.addLayout(launch_row)
        layout.addSpacing(6)
        layout.addLayout(action_layout)
        layout.addLayout(btn_row)

        self._launch_remote_btn.clicked.connect(self._on_launch_painter_remote)
        self._send_btn.clicked.connect(self._on_send_mesh)
        self._close_btn.clicked.connect(self.close)

        self._workflow_combo.currentIndexChanged.connect(self._save_send_settings)
        self._import_cameras_cb.toggled.connect(self._save_send_settings)
        self._preserve_strokes_cb.toggled.connect(self._save_send_settings)
        self._auto_unwrap_cb.toggled.connect(self._save_send_settings)

        self._load_send_settings()

        self.setStyleSheet(_stylesheet())

    def _load_send_settings(self):
        idx = int(self._settings.value("workflow_index", 0) or 0)
        idx = max(0, min(idx, self._workflow_combo.count() - 1))
        self._workflow_combo.setCurrentIndex(idx)
        self._import_cameras_cb.setChecked(
            self._settings.value("import_cameras", True) not in (False, "false", 0, "0")
        )
        self._preserve_strokes_cb.setChecked(
            self._settings.value("preserve_strokes", True) not in (False, "false", 0, "0")
        )
        self._auto_unwrap_cb.setChecked(
            self._settings.value("auto_unwrap", False) in (True, "true", 1, "1")
        )

    def _save_send_settings(self):
        self._settings.setValue("workflow_index", self._workflow_combo.currentIndex())
        self._settings.setValue("import_cameras", self._import_cameras_cb.isChecked())
        self._settings.setValue("preserve_strokes", self._preserve_strokes_cb.isChecked())
        self._settings.setValue("auto_unwrap", self._auto_unwrap_cb.isChecked())

    def _gather_import_options(self):
        idx = self._workflow_combo.currentIndex()
        member = self._workflow_combo.itemData(idx, QtCore.Qt.UserRole)
        if member is None:
            member = self._workflow_combo.itemData(idx)
        if member is None:
            entries = _workflow_combo_entries()
            if 0 <= idx < len(entries):
                member = entries[idx][1]
        member = _normalize_workflow_member(member)
        return {
            "workflow_member": member,
            "import_cameras": self._import_cameras_cb.isChecked(),
            "preserve_strokes": self._preserve_strokes_cb.isChecked(),
            "auto_unwrap": self._auto_unwrap_cb.isChecked(),
        }

    def _on_launch_painter_remote(self):
        ok, exe = _launch_painter_remote_scripting_only()
        if not ok:
            QtWidgets.QMessageBox.critical(
                self,
                "Launch Substance Painter",
                "Could not find Adobe Substance 3D Painter.exe.\n\n"
                "Install Substance Painter or set the environment variable "
                "SUBSTANCE_PAINTER_EXE to the full path of the executable.",
            )
            return
        QtWidgets.QMessageBox.information(
            self,
            "Launch Substance Painter",
            "Started Painter with remote scripting enabled:\n\n{}\n\n"
            "Wait until Painter has finished loading, then press Send Mesh.".format(exe),
        )

    def _confirm_save_painter_work(self, informative_html):
        """Warn user to save .spp before destructive / mesh operations. Returns True to continue."""
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("Save Substance Painter work")
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setTextFormat(QtCore.Qt.RichText)
        box.setText(
            "Before continuing, save your Substance Painter project (.spp) if you have unsaved work. "
            "Otherwise you may lose changes."
        )
        box.setInformativeText(informative_html)
        cont = box.addButton("Continue", QtWidgets.QMessageBox.AcceptRole)
        box.addButton(QtWidgets.QMessageBox.Cancel)
        box.setDefaultButton(QtWidgets.QMessageBox.Cancel)
        box.exec_()
        return box.clickedButton() == cont

    def _export_selection_to_temp_fbx(self):
        """Return path to temp FBX or None on failure."""
        roots = _selected_mesh_transforms()
        if not roots:
            cmds.warning("Send to Substance: select at least one polygon mesh.")
            return None

        try:
            cmds.select(roots, r=True)
        except Exception as exc:
            cmds.warning("Send to Substance: could not use selection: {}".format(exc))
            return None

        fd, tmp_path = tempfile.mkstemp(suffix=".fbx", prefix="qm_send_to_substance_")
        os.close(fd)
        try:
            _export_selection_fbx(tmp_path)
        except Exception as exc:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            cmds.warning("Send to Substance: FBX export failed: {}".format(exc))
            return None

        self._temp_fbx = tmp_path
        return tmp_path

    def _handle_remote_response(self, raw):
        text, ambiguous = _normalize_remote_response(raw)
        if ambiguous:
            _log_send_substance(
                "Painter returned no clear status (null/empty). "
                "The mesh operation may still have run — check Painter. Raw: {!r}".format(raw)
            )
            _viewport_notify_exported_to_substance()
            return
        if text and text.startswith("error:"):
            _log_send_substance("Painter remote API error: {}".format(text))
            cmds.warning("Send to Substance: {}".format(text))
            return

        _log_send_substance(
            "Painter remote API: {}".format(text or "OK")
        )
        _viewport_notify_exported_to_substance()

    def _on_send_mesh(self):
        detail = (
            "<p>If a Substance Painter project is open, the mesh will be <b>reloaded</b> into it.</p>"
            "<p>If nothing is open, Painter will <b>create a new project</b> from the exported FBX.</p>"
            "<p>Save your .spp first if you need to keep unsaved work safe.</p>"
        )
        if not self._confirm_save_painter_work(detail):
            return

        tmp_path = self._export_selection_to_temp_fbx()
        if not tmp_path:
            return

        opts = self._gather_import_options()

        try:
            script = _build_remote_script(
                tmp_path,
                opts["workflow_member"],
                opts["import_cameras"],
                opts["preserve_strokes"],
                opts["auto_unwrap"],
            )
            raw = _remote_painter_exec_python(script)
            self._handle_remote_response(raw)
            self._save_send_settings()
            return
        except Exception as remote_err:
            remote_msg = str(remote_err)

        # Painter not running: launch with remote scripting, wait, then create via API
        # (--mesh CLI alone cannot apply UV workflow / Settings from this dialog).
        launched, exe_used = _launch_painter_remote_scripting_only()
        if launched:
            _log_send_substance(
                "Painter was not reachable ({}). Starting with --enable-remote-scripting: {}".format(
                    remote_msg, exe_used
                )
            )
            _log_send_substance("Waiting for Substance Painter remote API (up to 120s)...")
            if _wait_for_remote_painter(timeout_seconds=120):
                try:
                    script = _build_remote_script(
                        tmp_path,
                        opts["workflow_member"],
                        opts["import_cameras"],
                        opts["preserve_strokes"],
                        opts["auto_unwrap"],
                    )
                    raw = _remote_painter_exec_python(script)
                    self._handle_remote_response(raw)
                    self._save_send_settings()
                    return
                except Exception as launch_send_err:
                    remote_msg = "{} | after launch: {}".format(remote_msg, launch_send_err)
                    _log_send_substance(
                        "Remote send after launch failed: {}".format(launch_send_err)
                    )
            else:
                _log_send_substance(
                    "Timed out waiting for Painter remote API after launch."
                )
                cmds.warning(
                    "Send to Substance: Painter started but remote API did not respond in time. "
                    "Wait for Painter to finish loading, then Send again."
                )

        # Last resort: --mesh (no UV workflow / dialog settings)
        launched_mesh, exe_used = _launch_painter_cli_mesh(tmp_path)
        if launched_mesh:
            _log_send_substance(
                "Started Painter with --mesh only (no dialog settings): {}. Reason: {}".format(
                    exe_used, remote_msg
                )
            )
            _viewport_notify_exported_to_substance()
            self._save_send_settings()
            return

        _log_send_substance(
            "Remote API failed ({}). Could not find Painter.exe. "
            "Set SUBSTANCE_PAINTER_EXE or install Painter. FBX: {}".format(
                remote_msg, tmp_path
            )
        )
        cmds.warning(
            "Send to Substance: could not reach or start Painter. "
            "See Script Editor log for FBX path."
        )

    def closeEvent(self, event):
        self._save_send_settings()
        super(SendToSubstanceDialog, self).closeEvent(event)


def show():
    return SendToSubstanceDialog.show_dialog()
