from pathlib import Path

base = Path(SPECPATH).parent / "src"
a = Analysis(
    [str(base / "updater.py")],
    pathex=[str(base)],
    binaries=[],
    datas=[],
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
