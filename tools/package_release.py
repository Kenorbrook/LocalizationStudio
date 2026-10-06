"""Create the verified GitHub release archive from the staged Windows build."""

import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from version import VERSION, REPOSITORY
from update_package import validate_bundle


def main():
    bundle = ROOT / "release/LocalizationStudio"
    for name in ["README.md", "THIRD_PARTY_NOTICES.md"]:
        shutil.copy2(ROOT / name, bundle / name)
    files = {
        p.relative_to(bundle).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(bundle.rglob("*"))
        if p.is_file() and p.name != "release-manifest.json"
    }
    manifest = {"version": VERSION, "repository": REPOSITORY, "files": files}
    (bundle / "release-manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    validate_bundle(bundle, VERSION)
    output = ROOT / "release-assets"
    output.mkdir(exist_ok=True)
    archive = output / f"LocalizationStudio-v{VERSION}-windows-x64.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                package.write(
                    path, "LocalizationStudio/" + path.relative_to(bundle).as_posix()
                )
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / f"SHA256SUMS-v{VERSION}.txt").write_text(
        f"{checksum}  {archive.name}\n", encoding="ascii"
    )
    print(json.dumps({"archive": str(archive), "sha256": checksum, "version": VERSION}))


if __name__ == "__main__":
    main()
