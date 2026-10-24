from pathlib import Path
import sysconfig

project = Path(SPECPATH).parent
base = project / "src"
# Some Python distributions load this runtime trust-policy file inside SSLContext.
ssl_policy = Path(sysconfig.get_path("stdlib")) / "codex-rejected-cas.pem"
runtime_data = [(str(ssl_policy), ".")] if ssl_policy.is_file() else []
a = Analysis(
    [str(base / "desktop.py")],
    pathex=[str(base), str(base / "translation_tools")],
    binaries=[],
    datas=[
        (str(base / "ui"), "ui"),
        (str(project / "docs/TRANSLATION_POLICY.md"), "."),
        (
            str(base / "translation_tools/local_literary_profile.json"),
            "translation_tools",
        ),
        (str(base / "translation_tools/run_translation.ps1"), "translation_tools"),
    ]
    + runtime_data,
    hiddenimports=[
        "lingua",
        "worker",
        "mcp_server",
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
        "localize",
        "local_editor",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide6", "cefpython3"],
    noarchive=False,
)
pyz = PYZ(a.pure)
desktop = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LocalizationStudio",
    console=False,
    debug=False,
)
worker = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LocalizationWorker",
    console=True,
    debug=False,
)
package = COLLECT(
    desktop,
    worker,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="LocalizationStudio",
)
