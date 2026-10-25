"""Ren'Py engine adapter, independently versioned Studio plugin."""

from .controller import RenPyPlugin


def create(store):
    return RenPyPlugin(store)


def invoke(controller, command, args):
    from providers import models

    commands = {
        "state": controller.snapshot,
        "inspect": controller.inspect,
        "prepare": controller.prepare,
        "build": controller.build,
        "rollback": controller.rollback,
        "launch": controller.launch_game,
        "pipeline": controller.start_pipeline,
        "cancel": controller.cancel,
        "models": models,
        "log": controller.log,
    }
    if command not in commands:
        raise ValueError("Неизвестная команда модуля Ren’Py")
    return commands[command](*args)
