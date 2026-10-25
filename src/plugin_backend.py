"""Load immutable plugin generations without adding them to the process search path."""

import hashlib
import importlib
import importlib.util
import sys
from pathlib import Path

from paths import ROOT


def seed_root(identity):
    parent = (
        ROOT / "builtin_plugins"
        if getattr(sys, "frozen", False)
        else ROOT.parent / "plugins"
    )
    return parent / identity


def load_backend(root):
    root = Path(root).resolve()
    name = "_studio_plugin_" + hashlib.sha256(str(root).encode()).hexdigest()[:24]
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, root / "__init__.py", submodule_search_locations=[str(root)]
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            for key in list(sys.modules):
                if key == name or key.startswith(name + "."):
                    del sys.modules[key]
            raise
    return sys.modules[name]


def seed_module(identity, module):
    package = load_backend(seed_root(identity))
    return importlib.import_module(package.__name__ + "." + module)
