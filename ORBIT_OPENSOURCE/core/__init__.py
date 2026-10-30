"""ORBIT core: configuration, action registry, execution and OS integration.

The core layer has no dependency on the UI layer.  The UI talks to it through
:class:`core.action_registry.ActionRegistry` and
:class:`core.action_executor.ActionRunner`.
"""

__all__ = [
    "models",
    "config_manager",
    "action_registry",
    "action_executor",
    "shortcut_manager",
    "launcher",
    "media_controller",
    "keycodes",
    "paths",
    "startup",
]
