"""Data structures for ORBIT configuration.

Everything the radial menu draws comes from these models, so new action types
never require changes to the UI: the UI renders :class:`Action` records and the
registry describes how each type is edited and executed.
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Mapping, MutableMapping

CONFIG_VERSION = 2

VALID_ACTION_STORES = ("target", "args", "working_dir", "submenu_id", "option")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, Iterable) and not isinstance(value, Mapping):
        return [_as_str(item) for item in value]
    return []


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): copy.deepcopy(val) for key, val in value.items()}
    return {}


@dataclass(slots=True)
class Action:
    """One configurable button in a radial menu."""

    id: str = field(default_factory=lambda: new_id("act"))
    name: str = ""
    type: str = "command"
    target: str = ""
    args: list[str] = field(default_factory=list)
    working_dir: str = ""
    icon: str = ""
    image: str = ""
    description: str = ""
    shortcut: str = ""
    options: dict[str, Any] = field(default_factory=dict)
    visible_when: dict[str, Any] = field(default_factory=dict)
    confirm: bool = False
    close_menu: bool | None = None
    submenu_id: str = ""
    order: float = 0.0
    accent: str = ""
    enabled: bool = True
    extra: dict[str, Any] = field(default_factory=dict)

    # ---------------------------------------------------------------- values
    def get_field(self, store: str, key: str, default: Any = "") -> Any:
        """Read a value addressed the way registry fields address it."""
        if store == "target":
            return self.target
        if store == "args":
            return list(self.args)
        if store == "working_dir":
            return self.working_dir
        if store == "submenu_id":
            return self.submenu_id
        if store == "option":
            return self.options.get(key, default)
        raise KeyError(f"Unknown field store: {store}")

    def set_field(self, store: str, key: str, value: Any) -> None:
        """Write a value addressed the way registry fields address it."""
        if store == "target":
            self.target = _as_str(value)
        elif store == "args":
            self.args = _as_str_list(value)
        elif store == "working_dir":
            self.working_dir = _as_str(value)
        elif store == "submenu_id":
            self.submenu_id = _as_str(value)
        elif store == "option":
            self.options[key] = value
        else:
            raise KeyError(f"Unknown field store: {store}")

    def option(self, key: str, default: Any = None) -> Any:
        return self.options.get(key, default)

    def display_name(self) -> str:
        return self.name or self.target or self.type

    def clone(self, *, new_identifier: bool = True, name_suffix: str = " copy") -> "Action":
        data = self.to_dict()
        if new_identifier:
            data["id"] = new_id("act")
            data["name"] = f"{self.display_name()}{name_suffix}"
        return Action.from_dict(data)

    def apply_from(self, other: "Action") -> None:
        """Adopt every property of ``other`` while keeping this action's id.

        Used by the editor: the dialog edits a copy and writes it back into the
        action that already lives inside the configuration.
        """
        data = other.to_dict()
        data["id"] = self.id
        fresh = Action.from_dict(data)
        for slot in _ACTION_SLOTS:
            setattr(self, slot, getattr(fresh, slot))

    # ----------------------------------------------------------- (de)serialise
    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "target": self.target,
            "args": list(self.args),
            "working_dir": self.working_dir,
            "icon": self.icon,
            "image": self.image,
            "description": self.description,
            "shortcut": self.shortcut,
            "options": copy.deepcopy(self.options),
            "visible_when": copy.deepcopy(self.visible_when),
            "confirm": self.confirm,
            "close_menu": self.close_menu,
            "submenu_id": self.submenu_id,
            "order": self.order,
            "accent": self.accent,
            "enabled": self.enabled,
        }
        data.update(copy.deepcopy(self.extra))
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "Action":
        raw = _as_dict(data)
        known = {
            "id", "name", "type", "target", "args", "working_dir", "icon", "image",
            "description", "shortcut", "options", "visible_when", "confirm",
            "close_menu", "submenu_id", "order", "accent", "enabled",
        }
        close_menu: bool | None
        if raw.get("close_menu") is None:
            close_menu = None
        else:
            close_menu = _as_bool(raw.get("close_menu"), True)
        action = cls(
            id=_as_str(raw.get("id")) or new_id("act"),
            name=_as_str(raw.get("name")),
            type=_as_str(raw.get("type"), "command") or "command",
            target=_as_str(raw.get("target")),
            args=_as_str_list(raw.get("args")),
            working_dir=_as_str(raw.get("working_dir")),
            icon=_as_str(raw.get("icon")),
            image=_as_str(raw.get("image")),
            description=_as_str(raw.get("description")),
            shortcut=_as_str(raw.get("shortcut")),
            options=_as_dict(raw.get("options")),
            visible_when=_as_dict(raw.get("visible_when")),
            confirm=_as_bool(raw.get("confirm"), False),
            close_menu=close_menu,
            submenu_id=_as_str(raw.get("submenu_id")),
            order=_as_float(raw.get("order"), 0.0),
            accent=_as_str(raw.get("accent")),
            enabled=_as_bool(raw.get("enabled"), True),
            extra={key: copy.deepcopy(value) for key, value in raw.items() if key not in known},
        )
        return action


_ACTION_SLOTS: tuple[str, ...] = (
    "name", "type", "target", "args", "working_dir", "icon", "image",
    "description", "shortcut", "options", "visible_when", "confirm",
    "close_menu", "submenu_id", "order", "accent", "enabled", "extra",
)


@dataclass(slots=True)
class Menu:
    """A radial menu: a named, ordered collection of actions."""

    id: str = field(default_factory=lambda: new_id("menu"))
    name: str = "New Menu"
    description: str = ""
    icon: str = "circle-dot"
    actions: list[Action] = field(default_factory=list)
    layout: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def sorted_actions(self, *, include_disabled: bool = False) -> list[Action]:
        items = [action for action in self.actions if include_disabled or action.enabled]
        return sorted(items, key=lambda action: (action.order, action.name.lower()))

    def action_by_id(self, action_id: str) -> Action | None:
        for action in self.actions:
            if action.id == action_id:
                return action
        return None

    def remove_action(self, action_id: str) -> bool:
        for index, action in enumerate(self.actions):
            if action.id == action_id:
                del self.actions[index]
                return True
        return False

    def clone(self, *, name_suffix: str = " copy") -> "Menu":
        data = self.to_dict()
        data["id"] = new_id("menu")
        data["name"] = f"{self.name}{name_suffix}"
        for raw_action in data.get("actions", []):
            raw_action["id"] = new_id("act")
        return Menu.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "actions": [action.to_dict() for action in self.actions],
            "layout": copy.deepcopy(self.layout),
        }
        data.update(copy.deepcopy(self.extra))
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "Menu":
        raw = _as_dict(data)
        known = {"id", "name", "description", "icon", "actions", "layout"}
        return cls(
            id=_as_str(raw.get("id")) or new_id("menu"),
            name=_as_str(raw.get("name"), "Menu") or "Menu",
            description=_as_str(raw.get("description")),
            icon=_as_str(raw.get("icon")) or "circle-dot",
            actions=[Action.from_dict(item) for item in (raw.get("actions") or [])],
            layout=_as_dict(raw.get("layout")),
            extra={key: copy.deepcopy(value) for key, value in raw.items() if key not in known},
        )


@dataclass(slots=True)
class Settings:
    """Global appearance and behaviour preferences."""

    # --- behaviour
    hotkey: str = "alt+space"
    fallback_hotkey: str = "alt+shift+space"
    close_after_execution: bool = True
    dismiss_on_escape: bool = True
    dismiss_on_click_outside: bool = True
    auto_hide_seconds: int = 0
    confirm_destructive: bool = True
    start_on_login: bool = False
    show_tray_icon: bool = True
    show_toasts: bool = True
    monitor: str = "cursor"          # cursor | primary | index:<n> | name:<name>
    monitor_index: int = 0

    # --- appearance
    theme: str = "orbit-dark"        # orbit-dark | orbit-light | custom
    accent: str = "#6C8CFF"
    radius: int = 196
    button_size: int = 84
    icon_size: int = 30
    ring_gap: int = 96
    max_per_ring: int = 8
    overlay_opacity: float = 0.42
    menu_opacity: float = 0.96
    screen_shader_enabled: bool = False
    screen_shader: str = "vignette"   # vignette | soft | flat | aurora | particles | ripple
    screen_shader_strength: float = 0.75
    animation_duration: int = 190
    easing: str = "out_cubic"
    show_labels: str = "hover"       # always | hover | never
    show_descriptions: bool = True
    show_shortcut_badges: bool = True
    button_shadow: bool = True
    button_glow: bool = True
    reduced_motion: bool = False

    # --- feedback
    feedback_pulse: bool = True
    feedback_sound: bool = False

    # --- ORBIT productivity features
    search_hotkey: str = "ctrl+alt+space"
    quick_controls_hotkey: str = "ctrl+alt+c"
    music_hotkey: str = "ctrl+alt+m"
    widgets_hotkey: str = "ctrl+alt+w"
    search_folders: list[str] = field(default_factory=list)
    search_excluded: list[str] = field(default_factory=list)
    search_favorites: list[str] = field(default_factory=list)
    quick_controls: list[str] = field(default_factory=lambda: ["audio", "wifi", "bluetooth", "display", "power", "network"])
    music_folders: list[str] = field(default_factory=list)
    music_auto_next: bool = True
    music_repeat: str = "off"
    music_shuffle: bool = False
    music_volume: int = 72
    music_queue: list[str] = field(default_factory=list)
    music_playlists: dict[str, list[str]] = field(default_factory=dict)
    music_resume: bool = True
    widgets_enabled: list[str] = field(default_factory=lambda: ["timer", "notes", "tasks", "system"])
    widgets_order: list[str] = field(default_factory=lambda: ["timer", "notes", "tasks", "system"])
    widget_data: dict[str, Any] = field(default_factory=dict)
    search_enabled: bool = True
    quick_controls_enabled: bool = True
    music_enabled: bool = True
    music_hotkeys_enabled: bool = False
    search_index_apps: bool = True
    search_index_files: bool = True

    # --- custom theme colours (used when theme == "custom")
    custom_background: str = "#101216"
    custom_surface: str = "#191C22"
    custom_surface_alt: str = "#22262E"
    custom_text: str = "#EDEFF5"
    custom_text_muted: str = "#9AA1B2"
    custom_border: str = "#2C313B"
    custom_danger: str = "#F0605F"

    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "hotkey": self.hotkey,
            "fallback_hotkey": self.fallback_hotkey,
            "close_after_execution": self.close_after_execution,
            "dismiss_on_escape": self.dismiss_on_escape,
            "dismiss_on_click_outside": self.dismiss_on_click_outside,
            "auto_hide_seconds": self.auto_hide_seconds,
            "confirm_destructive": self.confirm_destructive,
            "start_on_login": self.start_on_login,
            "show_tray_icon": self.show_tray_icon,
            "show_toasts": self.show_toasts,
            "monitor": self.monitor,
            "monitor_index": self.monitor_index,
            "theme": self.theme,
            "accent": self.accent,
            "radius": self.radius,
            "button_size": self.button_size,
            "icon_size": self.icon_size,
            "ring_gap": self.ring_gap,
            "max_per_ring": self.max_per_ring,
            "overlay_opacity": self.overlay_opacity,
            "menu_opacity": self.menu_opacity,
            "screen_shader_enabled": self.screen_shader_enabled,
            "screen_shader": self.screen_shader,
            "screen_shader_strength": self.screen_shader_strength,
            "animation_duration": self.animation_duration,
            "easing": self.easing,
            "show_labels": self.show_labels,
            "show_descriptions": self.show_descriptions,
            "show_shortcut_badges": self.show_shortcut_badges,
            "button_shadow": self.button_shadow,
            "button_glow": self.button_glow,
            "reduced_motion": self.reduced_motion,
            "feedback_pulse": self.feedback_pulse,
            "feedback_sound": self.feedback_sound,
            "search_hotkey": self.search_hotkey,
            "quick_controls_hotkey": self.quick_controls_hotkey,
            "music_hotkey": self.music_hotkey,
            "widgets_hotkey": self.widgets_hotkey,
            "search_folders": list(self.search_folders),
            "search_excluded": list(self.search_excluded),
            "search_favorites": list(self.search_favorites),
            "quick_controls": list(self.quick_controls),
            "music_folders": list(self.music_folders),
            "music_auto_next": self.music_auto_next,
            "music_repeat": self.music_repeat,
            "music_shuffle": self.music_shuffle,
            "music_volume": self.music_volume,
            "music_queue": list(self.music_queue),
            "music_playlists": copy.deepcopy(self.music_playlists),
            "music_resume": self.music_resume,
            "widgets_enabled": list(self.widgets_enabled),
            "widgets_order": list(self.widgets_order),
            "widget_data": copy.deepcopy(self.widget_data),
            "search_enabled": self.search_enabled,
            "quick_controls_enabled": self.quick_controls_enabled,
            "music_enabled": self.music_enabled,
            "music_hotkeys_enabled": self.music_hotkeys_enabled,
            "search_index_apps": self.search_index_apps,
            "search_index_files": self.search_index_files,
            "custom_background": self.custom_background,
            "custom_surface": self.custom_surface,
            "custom_surface_alt": self.custom_surface_alt,
            "custom_text": self.custom_text,
            "custom_text_muted": self.custom_text_muted,
            "custom_border": self.custom_border,
            "custom_danger": self.custom_danger,
        }
        data.update(copy.deepcopy(self.extra))
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "Settings":
        raw = _as_dict(data)
        defaults = cls()
        known = set(defaults.to_dict())
        theme = _as_str(raw.get("theme"), defaults.theme)
        if theme not in {"orbit-dark", "orbit-light", "custom"}:
            theme = defaults.theme
        show_labels = _as_str(raw.get("show_labels"), defaults.show_labels)
        if show_labels not in {"always", "hover", "never"}:
            show_labels = defaults.show_labels
        return cls(
            hotkey=_as_str(raw.get("hotkey"), defaults.hotkey) or defaults.hotkey,
            fallback_hotkey=_as_str(raw.get("fallback_hotkey"), defaults.fallback_hotkey),
            close_after_execution=_as_bool(raw.get("close_after_execution"), defaults.close_after_execution),
            dismiss_on_escape=_as_bool(raw.get("dismiss_on_escape"), defaults.dismiss_on_escape),
            dismiss_on_click_outside=_as_bool(
                raw.get("dismiss_on_click_outside"), defaults.dismiss_on_click_outside
            ),
            auto_hide_seconds=max(0, _as_int(raw.get("auto_hide_seconds"), defaults.auto_hide_seconds)),
            confirm_destructive=_as_bool(raw.get("confirm_destructive"), defaults.confirm_destructive),
            start_on_login=_as_bool(raw.get("start_on_login"), defaults.start_on_login),
            show_tray_icon=_as_bool(raw.get("show_tray_icon"), defaults.show_tray_icon),
            show_toasts=_as_bool(raw.get("show_toasts"), defaults.show_toasts),
            monitor=_as_str(raw.get("monitor"), defaults.monitor) or defaults.monitor,
            monitor_index=max(0, _as_int(raw.get("monitor_index"), defaults.monitor_index)),
            theme=theme,
            accent=_as_str(raw.get("accent"), defaults.accent) or defaults.accent,
            radius=max(90, _as_int(raw.get("radius"), defaults.radius)),
            button_size=max(48, _as_int(raw.get("button_size"), defaults.button_size)),
            icon_size=max(12, _as_int(raw.get("icon_size"), defaults.icon_size)),
            ring_gap=max(60, _as_int(raw.get("ring_gap"), defaults.ring_gap)),
            max_per_ring=max(3, _as_int(raw.get("max_per_ring"), defaults.max_per_ring)),
            overlay_opacity=min(1.0, max(0.0, _as_float(raw.get("overlay_opacity"), defaults.overlay_opacity))),
            menu_opacity=min(1.0, max(0.2, _as_float(raw.get("menu_opacity"), defaults.menu_opacity))),
            screen_shader_enabled=_as_bool(raw.get("screen_shader_enabled"), defaults.screen_shader_enabled),
            screen_shader=_as_str(raw.get("screen_shader"), defaults.screen_shader) if raw.get("screen_shader") in {"vignette", "soft", "flat", "aurora", "particles", "ripple"} else defaults.screen_shader,
            screen_shader_strength=min(1.0, max(0.1, _as_float(raw.get("screen_shader_strength"), defaults.screen_shader_strength))),
            animation_duration=max(0, _as_int(raw.get("animation_duration"), defaults.animation_duration)),
            easing=_as_str(raw.get("easing"), defaults.easing) or defaults.easing,
            show_labels=show_labels,
            show_descriptions=_as_bool(raw.get("show_descriptions"), defaults.show_descriptions),
            show_shortcut_badges=_as_bool(raw.get("show_shortcut_badges"), defaults.show_shortcut_badges),
            button_shadow=_as_bool(raw.get("button_shadow"), defaults.button_shadow),
            button_glow=_as_bool(raw.get("button_glow"), defaults.button_glow),
            reduced_motion=_as_bool(raw.get("reduced_motion"), defaults.reduced_motion),
            feedback_pulse=_as_bool(raw.get("feedback_pulse"), defaults.feedback_pulse),
            feedback_sound=_as_bool(raw.get("feedback_sound"), defaults.feedback_sound),
            search_hotkey=_as_str(raw.get("search_hotkey"), defaults.search_hotkey) or defaults.search_hotkey,
            quick_controls_hotkey=_as_str(raw.get("quick_controls_hotkey"), defaults.quick_controls_hotkey) or defaults.quick_controls_hotkey,
            music_hotkey=_as_str(raw.get("music_hotkey"), defaults.music_hotkey) or defaults.music_hotkey,
            widgets_hotkey=_as_str(raw.get("widgets_hotkey"), defaults.widgets_hotkey) or defaults.widgets_hotkey,
            search_folders=_as_str_list(raw.get("search_folders")), 
            search_excluded=_as_str_list(raw.get("search_excluded")),
            search_favorites=_as_str_list(raw.get("search_favorites")),
            quick_controls=[item for item in _as_str_list(raw.get("quick_controls", defaults.quick_controls)) if item in {"audio", "wifi", "bluetooth", "display", "power", "network", "sound"}],
            music_folders=_as_str_list(raw.get("music_folders")),
            music_auto_next=_as_bool(raw.get("music_auto_next"), defaults.music_auto_next),
            music_repeat=raw.get("music_repeat") if raw.get("music_repeat") in {"off", "all", "one"} else defaults.music_repeat,
            music_shuffle=_as_bool(raw.get("music_shuffle"), defaults.music_shuffle),
            music_volume=min(100, max(0, _as_int(raw.get("music_volume"), defaults.music_volume))),
            music_queue=_as_str_list(raw.get("music_queue")),
            music_playlists={str(key): _as_str_list(value) for key, value in _as_dict(raw.get("music_playlists", _as_dict(raw.get("playlists")))).items()},
            music_resume=_as_bool(raw.get("music_resume"), defaults.music_resume),
            widgets_enabled=[item for item in _as_str_list(raw.get("widgets_enabled", defaults.widgets_enabled)) if item in {"timer", "notes", "tasks", "system"}],
            widgets_order=[item for item in dict.fromkeys(_as_str_list(raw.get("widgets_order", defaults.widgets_order))) if item in {"timer", "notes", "tasks", "system"}],
            widget_data=_as_dict(raw.get("widget_data")),
            search_enabled=_as_bool(raw.get("search_enabled"), defaults.search_enabled),
            quick_controls_enabled=_as_bool(raw.get("quick_controls_enabled"), defaults.quick_controls_enabled),
            music_enabled=_as_bool(raw.get("music_enabled"), defaults.music_enabled),
            music_hotkeys_enabled=_as_bool(raw.get("music_hotkeys_enabled"), defaults.music_hotkeys_enabled),
            search_index_apps=_as_bool(raw.get("search_index_apps"), defaults.search_index_apps),
            search_index_files=_as_bool(raw.get("search_index_files"), defaults.search_index_files),
            custom_background=_as_str(raw.get("custom_background"), defaults.custom_background),
            custom_surface=_as_str(raw.get("custom_surface"), defaults.custom_surface),
            custom_surface_alt=_as_str(raw.get("custom_surface_alt"), defaults.custom_surface_alt),
            custom_text=_as_str(raw.get("custom_text"), defaults.custom_text),
            custom_text_muted=_as_str(raw.get("custom_text_muted"), defaults.custom_text_muted),
            custom_border=_as_str(raw.get("custom_border"), defaults.custom_border),
            custom_danger=_as_str(raw.get("custom_danger"), defaults.custom_danger),
            extra={key: copy.deepcopy(value) for key, value in raw.items() if key not in known},
        )


@dataclass(slots=True)
class Config:
    """The complete ORBIT configuration document."""

    version: int = CONFIG_VERSION
    root_menu_id: str = "main"
    menus: list[Menu] = field(default_factory=list)
    settings: Settings = field(default_factory=Settings)
    extra: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ menus
    def menu_by_id(self, menu_id: str | None) -> Menu | None:
        if not menu_id:
            return None
        for menu in self.menus:
            if menu.id == menu_id:
                return menu
        return None

    def require_menu(self, menu_id: str | None) -> Menu:
        menu = self.menu_by_id(menu_id)
        if menu is None:
            if not self.menus:
                raise KeyError("Configuration contains no menus.")
            return self.menus[0]
        return menu

    def root_menu(self) -> Menu:
        return self.require_menu(self.root_menu_id)

    def add_menu(self, menu: Menu) -> Menu:
        self.menus.append(menu)
        return menu

    def remove_menu(self, menu_id: str) -> list[str]:
        """Remove a menu; returns ids of menus that referenced it."""
        self.menus = [menu for menu in self.menus if menu.id != menu_id]
        dangling: list[str] = []
        if self.root_menu_id == menu_id and self.menus:
            self.root_menu_id = self.menus[0].id
        for menu in self.menus:
            for action in menu.actions:
                if action.submenu_id == menu_id:
                    action.submenu_id = ""
                    dangling.append(action.id)
        return dangling

    def iter_actions(self) -> Iterator[tuple[Menu, Action]]:
        for menu in self.menus:
            for action in menu.actions:
                yield menu, action

    def used_action_ids(self) -> set[str]:
        return {action.id for _menu, action in self.iter_actions()}

    def menu_name(self, menu_id: str) -> str:
        menu = self.menu_by_id(menu_id)
        return menu.name if menu else "(missing menu)"

    # ---------------------------------------------------------- (de)serialise
    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "version": self.version,
            "root_menu_id": self.root_menu_id,
            "settings": self.settings.to_dict(),
            "menus": [menu.to_dict() for menu in self.menus],
        }
        data.update(copy.deepcopy(self.extra))
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "Config":
        raw = _as_dict(data)
        known = {"version", "root_menu_id", "settings", "menus"}
        menus = [Menu.from_dict(item) for item in (raw.get("menus") or [])]
        return cls(
            version=_as_int(raw.get("version"), CONFIG_VERSION),
            root_menu_id=_as_str(raw.get("root_menu_id"), "main") or "main",
            menus=menus,
            settings=Settings.from_dict(raw.get("settings")),
            extra={key: copy.deepcopy(value) for key, value in raw.items() if key not in known},
        )

    def clone(self) -> "Config":
        return Config.from_dict(self.to_dict())


def find_submenu_cycle(menus: list[Menu], root_id: str) -> list[str] | None:
    """Return a cycle path when submenu references form a loop, else ``None``.

    Self references (``a -> a``) and deeper loops (``a -> b -> a``) are both
    reported so the executor and editor can refuse to descend into them.
    """
    lookup: MutableMapping[str, Menu] = {menu.id: menu for menu in menus}
    state: dict[str, int] = {}
    stack: list[str] = []

    def walk(menu_id: str) -> list[str] | None:
        if menu_id in state and state[menu_id] == 1:
            if menu_id in stack:
                return stack[stack.index(menu_id):] + [menu_id]
            return [menu_id, menu_id]
        if state.get(menu_id) == 2:
            return None
        state[menu_id] = 1
        stack.append(menu_id)
        menu = lookup.get(menu_id)
        if menu is not None:
            for action in menu.actions:
                if action.type == "submenu" and action.submenu_id and action.enabled:
                    found = walk(action.submenu_id)
                    if found:
                        return found
        stack.pop()
        state[menu_id] = 2
        return None

    return walk(root_id)
