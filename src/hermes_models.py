"""Compatibility import for source integrations; implementation ships as a plugin."""

import sys
from plugin_backend import seed_module

sys.modules[__name__] = seed_module("hermes", "hermes_models")
