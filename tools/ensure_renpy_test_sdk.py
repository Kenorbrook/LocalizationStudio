"""Download a pinned official SDK for neutral engine tests, outside release files."""

import hashlib
import shutil
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "8.5.3"
SHA256 = "ff57648f9c04f27e381c48af6d8e3ee3cdec296bed4d3831f47f09b0a71b505e"


def ensure():
    folder = ROOT / ".venv/tools" / ("renpy-" + VERSION)
    sdk = folder / ("renpy-" + VERSION + "-sdk")
    if (sdk / "lib/python3.12/encodings").is_dir():
        return sdk
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / "sdk.zip"
    if not archive.is_file():
        request = urllib.request.Request(
            f"https://www.renpy.org/dl/{VERSION}/renpy-{VERSION}-sdk.zip",
            headers={"User-Agent": "LocalizationStudio-EngineTests"},
        )
        with (
            urllib.request.urlopen(request, timeout=90) as response,
            archive.open("wb") as output,
        ):
            shutil.copyfileobj(response, output)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise ValueError("Official Ren'Py SDK checksum mismatch")
    with zipfile.ZipFile(archive) as package:
        for item in package.infolist():
            path = folder / item.filename
            if (
                not path.resolve().is_relative_to(folder.resolve())
                or "\\" in item.filename
            ):
                raise ValueError("Unsafe SDK entry")
            relative = "/".join(item.filename.split("/")[1:])
            if not (
                relative.startswith(
                    (
                        "renpy/",
                        "lib/py3-windows-x86_64/",
                        "lib/python3.12/",
                        "lib/python3.12.zip",
                    )
                )
                or relative in {"renpy.py", "LICENSE.txt"}
            ):
                continue
            if item.is_dir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with package.open(item) as source, path.open("wb") as output:
                    shutil.copyfileobj(source, output)
    # Keep the verified archive for repeatable offline test setup.
    return sdk


if __name__ == "__main__":
    print(ensure())
