"""Build small independent plugin ZIPs, checksums and filtered initial app seeds."""

import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from plugin_package import guard, manifest


def package(source, output):
    guard(source)
    value = manifest(json.loads((source / "plugin.json").read_text(encoding="utf-8")))
    files = {}
    for path in sorted(source.rglob("*")):
        guard(path)
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            files[path.relative_to(source).as_posix()] = path.read_bytes()
    value["files"] = {
        name: hashlib.sha256(data).hexdigest()
        for name, data in files.items()
        if name != "plugin.json"
    }
    files["plugin.json"] = (
        json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    ).encode()
    output.mkdir(parents=True, exist_ok=True)
    target = output / f"plugin-{value['id']}-v{value['version']}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    (output / "SHA256SUMS.txt").write_text(
        hashlib.sha256(target.read_bytes()).hexdigest() + "  " + target.name + "\n",
        encoding="ascii",
    )
    return files


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("identity", nargs="?", default="hermes")
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--example", action="store_true")
    args = parser.parse_args()
    from plugin_package import identity

    key = identity(args.identity)
    source = ROOT / ("examples/plugins" if args.example else "plugins") / key
    files = package(source, ROOT / "release/plugin-packages" / key)
    if args.seed:
        target = guard(ROOT / "build/plugin-seeds" / key)
        if target.exists():
            if not target.resolve().is_relative_to(ROOT / "build/plugin-seeds"):
                raise ValueError("Invalid seed path")
            for path in target.rglob("*"):
                guard(path)
            shutil.rmtree(target)
        for name, data in files.items():
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)


if __name__ == "__main__":
    main()
