"""Format UI sources with pinned Prettier; cache tooling outside application data."""

import argparse
import io
import subprocess
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "3.6.2"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    cache = ROOT / ".venv" / "tools" / ("prettier-" + VERSION)
    executable = cache / "package" / "bin" / "prettier.cjs"
    if not executable.exists():
        cache.mkdir(parents=True, exist_ok=True)
        url = f"https://registry.npmjs.org/prettier/-/prettier-{VERSION}.tgz"
        with urllib.request.urlopen(url, timeout=30) as response:
            payload = response.read()
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            archive.extractall(cache, filter="data")
    files = [
        str(p.relative_to(ROOT))
        for p in (ROOT / "src" / "ui").iterdir()
        if p.suffix in {".js", ".html", ".css"}
    ]
    subprocess.run(
        ["node", str(executable), "--check" if args.check else "--write", *files],
        cwd=ROOT,
        check=True,
    )


if __name__ == "__main__":
    main()
