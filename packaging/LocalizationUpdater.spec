from pathlib import Path
import sysconfig

base = Path(SPECPATH).parent / "src"
ssl_policy = Path(sysconfig.get_path("stdlib")) / "codex-rejected-cas.pem"
runtime_data = [(str(ssl_policy), ".")] if ssl_policy.is_file() else []
a = Analysis(
    [str(base / "updater.py")],
    pathex=[str(base)],
    binaries=[],
    datas=runtime_data,
    hiddenimports=[],
    excludes=["webview", "lingua", "tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="LocalizationUpdater",
    console=False,
    debug=False,
)
