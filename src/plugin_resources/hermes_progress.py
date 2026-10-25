"""Compatibility import for plugin progress helpers."""

import sys
from plugin_backend import seed_module

sys.modules[__name__] = seed_module("hermes", "resources.hermes_progress")
