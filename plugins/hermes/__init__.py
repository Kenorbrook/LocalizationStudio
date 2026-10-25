"""Hermes package entry point, Studio Plugin API 1."""

from .hermes_plugin import HermesPlugin
from . import hermes_tools


def create(store):
    return HermesPlugin(store)


def invoke(controller, command, args):
    commands = {
        "state": controller.snapshot,
        "models": controller.models.listing,
        "prepare_model": controller.prepare_model,
        "install": controller.install,
        "check": controller.check,
        "cancel": controller.cancel,
        "run": controller.run,
    }
    if command not in commands:
        raise ValueError("Неизвестная команда плагина")
    return commands[command](*args)
