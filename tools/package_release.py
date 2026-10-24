"""Create the verified GitHub release archive from the staged Windows build."""

import hashlib
import gzip
import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from version import VERSION, REPOSITORY
from update_package import validate_bundle
from update_files import file_hash, validate_index


def package_files(bundle, output, version):
    output.mkdir(parents=True, exist_ok=True)
    files = {}
    for path in sorted(bundle.rglob("*")):
        if not path.is_file():
            continue
        checksum = file_hash(path)
        asset = output / f"file-{checksum}.gz"
        if not asset.exists():
            with path.open("rb") as source, asset.open("wb") as packed:
                with gzip.GzipFile(
                    filename="", mode="wb", fileobj=packed, mtime=0
                ) as stream:
                    shutil.copyfileobj(source, stream)
        files[path.relative_to(bundle).as_posix()] = {
            "sha256": checksum,
            "size": path.stat().st_size,
            "asset": asset.name,
            "download_size": asset.stat().st_size,
            "download_sha256": file_hash(asset),
        }
    index = {"schema": 1, "version": version, "repository": REPOSITORY, "files": files}
    validate_index(index, version)
    manifest = output / "update-files.json"
    manifest.write_text(json.dumps(index, indent=2), encoding="utf-8")
    return manifest, [
        output / name for name in sorted({entry["asset"] for entry in files.values()})
    ]


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
    index, parts = package_files(bundle, output / f"v{VERSION}-files", VERSION)
    (output / f"SHA256SUMS-v{VERSION}.txt").write_text(
        f"{checksum}  {archive.name}\n{file_hash(index)}  {index.name}\n",
        encoding="ascii",
    )
    print(
        json.dumps(
            {
                "archive": str(archive),
                "sha256": checksum,
                "version": VERSION,
                "file_assets": len(parts),
                "index": str(index),
            }
        )
    )


if __name__ == "__main__":
    main()
