"""Source and packaged resource locations."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_HOME = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else ROOT.parent
)
UI_ROOT = ROOT / "ui"
SHARED = ROOT / "translation_tools"
DEFAULT_DB = APP_HOME / "data" / "studio.sqlite3"
POLICY_PATH = (
    ROOT / "TRANSLATION_POLICY.md"
    if getattr(sys, "frozen", False)
    else APP_HOME / "docs" / "TRANSLATION_POLICY.md"
)
# The separately usable Ren'Py CLI retains its original absolute imports.
if str(SHARED) not in sys.path:
    sys.path.insert(0, str(SHARED))
