"""Action types: registration, schema-driven editing, validation and execution.

The radial menu, the studio editor and the executor all read from this registry,
so adding an action type means adding one :class:`ActionTypeSpec` here — no
changes to the UI are required.

Field kinds understood by the editor (``ui/widgets.py``):

``text``, ``program``, ``path_file``, ``path_dir``, ``path_any``, ``url``,
``args``, ``choice``, ``bool``, ``int``, ``float``, ``color``, ``submenu``,
``action_list``, ``keystrokes``
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from core import urls
from core.keycodes import KeyNotationError, format_hotkey, parse_keystrokes, validate_hotkey
from core.launcher import LaunchError, launch_program, open_url, open_with_default, resolve_executable
from core.media_controller import MediaController, TRANSPORT_LABELS, send_keystroke_sequence
from core.models import Action, Config, Menu, new_id
from core.paths import expand_path

# ------------------------------------------------------------------- field model
SHELL_PROGRAMS = {
    "cmd.exe", "cmd", "powershell.exe", "powershell", "pwsh.exe", "pwsh",
    "wt.exe", "wt", "bash.exe", "wsl.exe", "python.exe", "python", "py.exe",
    "node.exe", "npm.cmd", "conhost.exe",
}

# Every image format ORBIT accepts for a custom icon or an image action.  This
# mirrors what Qt can decode on this machine (see ui/icons.py); keep the list
# broad so a phone photo or a downloaded logo always works.
IMAGE_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".jpe", ".jfif", ".gif", ".bmp", ".dib", ".webp",
    ".tif", ".tiff", ".tga", ".ico", ".icns", ".svg", ".svgz", ".avif",
    ".cur", ".wbmp", ".pbm", ".pgm", ".ppm", ".xbm", ".xpm",
)

IMAGE_FILE_FILTER = "Images (" + " ".join(f"*{suffix}" for suffix in IMAGE_SUFFIXES) + ")"


@dataclass(frozen=True, slots=True)
class Field:
    """Describes one editable property of an action."""

    key: str
    label: str
    kind: str = "text"
    store: str = "option"
    default: Any = ""
    help: str = ""
    placeholder: str = ""
    required: bool = False
    choices: tuple[tuple[str, str], ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    group: str = ""
    depends_on: tuple[str, Any] | None = None
    file_filter: str = ""
    advanced: bool = False

    @property
    def is_target(self) -> bool:
        return self.store == "target"


@dataclass(slots=True)
class Result:
    """Outcome of running an action."""

    ok: bool
    message: str
    detail: str = ""
    keep_menu_open: bool = False

    @staticmethod
    def success(message: str, detail: str = "", *, keep_menu_open: bool = False) -> "Result":
        return Result(True, message, detail, keep_menu_open)

    @staticmethod
    def failure(message: str, detail: str = "") -> "Result":
        return Result(False, message, detail)


class Cancelled(Exception):
    """Raised internally when a running action is cancelled."""


@dataclass
class ExecContext:
    """Everything an action runner may need while executing."""

    config: Config
    media: MediaController = field(default_factory=MediaController)
    cancel: threading.Event = field(default_factory=threading.Event)
    log: Callable[[str], None] = field(default=lambda message: None)
    invoke_main: Callable[[Callable[[], Any], bool], Any] | None = None
    services: dict[str, Callable[..., Any]] = field(default_factory=dict)
    runner: Callable[[Action, "ExecContext"], Result] | None = None
    depth: int = 0
    stack: tuple[str, ...] = ()
    max_depth: int = 4

    # ------------------------------------------------------------------ helpers
    def cancelled(self) -> bool:
        return self.cancel.is_set()

    def check_cancel(self) -> None:
        if self.cancelled():
            raise Cancelled()

    def child(self, action: Action) -> "ExecContext":
        return ExecContext(
            config=self.config,
            media=self.media,
            cancel=self.cancel,
            log=self.log,
            invoke_main=self.invoke_main,
            services=self.services,
            runner=self.runner,
            depth=self.depth + 1,
            stack=(*self.stack, action.id),
            max_depth=self.max_depth,
        )

    def run_nested(self, action: Action) -> Result:
        if self.runner is None:
            return Result.failure("Nested actions are not available in this context.")
        if self.depth + 1 > self.max_depth:
            return Result.failure(
                f"Action nesting stopped at depth {self.max_depth} to prevent a runaway loop."
            )
        if action.id in self.stack:
            return Result.failure(
                f'"{action.display_name()}" already ran inside this sequence; stopped to avoid a loop.'
            )
        return self.runner(action, self.child(action))

    def service(self, name: str) -> Callable[..., Any] | None:
        return self.services.get(name)

    def call_main(self, function: Callable[[], Any], wait: bool = True) -> Any:
        """Run ``function`` on the GUI thread and optionally wait for its result."""
        if self.invoke_main is None:
            return function()
        return self.invoke_main(function, wait)


# ------------------------------------------------------------------ spec model
@dataclass(frozen=True, slots=True)
class ActionTypeSpec:
    key: str
    label: str
    description: str
    icon: str
    fields: tuple[Field, ...] = ()
    group: str = "Basics"
    keeps_menu_open: bool = False
    hide_before_run: bool = False
    risky: bool = False
    default_confirm: bool = False
    validate: Callable[[Action, "ValidationContext"], list[str]] | None = None
    run: Callable[[Action, ExecContext], Result] | None = None

    def target_field(self) -> Field | None:
        for candidate in self.fields:
            if candidate.is_target:
                return candidate
        return None

    def option_fields(self) -> list[Field]:
        return [item for item in self.fields if item.store == "option"]


@dataclass(slots=True)
class ValidationContext:
    config: Config | None = None
    registry: "ActionRegistry | None" = None


# ----------------------------------------------------------------------- helpers
def needs_console(program: str) -> bool:
    return os.path.basename(program.strip().strip('"')).lower() in SHELL_PROGRAMS


def program_missing_problem(program: str) -> str | None:
    """Return a message when a configured program cannot be resolved at all."""
    expanded = expand_path(program)
    if not expanded:
        return "No program is configured."
    if os.path.isabs(expanded) and not Path(expanded).exists():
        return f"This program does not exist:\n{expanded}"
    if resolve_executable(expanded) is None:
        return f'"{expanded}" was not found on this PC. Choose the executable with Browse.'
    return None


def path_exists_problem(target: str, *, kind: str = "path") -> str | None:
    expanded = expand_path(target)
    if not expanded:
        return "No target is configured."
    path = Path(expanded)
    if not path.exists():
        return f"This {kind} does not exist:\n{expanded}"
    return None


def _start_detached(program: str, args: Sequence[str], working_dir: str, *, console: bool) -> subprocess.Popen:
    return launch_program(program, args, working_dir=working_dir, console=console)


def _cancellable_run(
    program: str,
    args: Sequence[str],
    working_dir: str,
    *,
    ctx: ExecContext,
    timeout: float | None,
) -> tuple[int, str]:
    """Run a command with pipes while remaining cancellable."""
    process = launch_program(
        program, args, working_dir=working_dir, capture=True, wait=False
    )
    assert isinstance(process, subprocess.Popen)
    deadline = time.monotonic() + timeout if timeout else None
    while True:
        if ctx.cancelled():
            process.kill()
            raise Cancelled()
        try:
            stdout, stderr = process.communicate(timeout=0.2)
            break
        except subprocess.TimeoutExpired:
            if deadline is not None and time.monotonic() > deadline:
                process.kill()
                return -1, f"Timed out after {timeout:g} seconds."
    output = "\n".join(part for part in ((stdout or "").strip(), (stderr or "").strip()) if part)
    return process.returncode if process.returncode is not None else -1, output


def _first_line(text: str, limit: int = 160) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line[:limit]
    return ""


# --------------------------------------------------------------------- validators
def _validate_app(action: Action, ctx: ValidationContext) -> list[str]:
    problems: list[str] = []
    if not action.target.strip():
        problems.append("Choose the program or file to launch.")
    else:
        missing = program_missing_problem(action.target)
        if missing:
            problems.append(missing)
    problems.extend(_validate_working_dir(action))
    return problems


def _validate_working_dir(action: Action) -> list[str]:
    if action.working_dir.strip():
        expanded = expand_path(action.working_dir)
        if not Path(expanded).is_dir():
            return [f"The working folder does not exist:\n{expanded}"]
    return []


def _validate_path(action: Action, ctx: ValidationContext) -> list[str]:
    problem = path_exists_problem(action.target, kind="file or folder")
    return [problem] if problem else []


def _validate_url(action: Action, ctx: ValidationContext) -> list[str]:
    problem = urls.validate_url(action.target)
    return [problem] if problem else []


def _validate_image(action: Action, ctx: ValidationContext) -> list[str]:
    problems: list[str] = []
    problem = path_exists_problem(action.target, kind="image")
    if problem:
        problems.append(problem)
    elif Path(expand_path(action.target)).suffix.lower() not in IMAGE_SUFFIXES:
        problems.append(
            "This file does not look like an image. Supported: "
            + ", ".join(suffix.lstrip(".") for suffix in IMAGE_SUFFIXES[:8])
            + " and other formats Qt can read (webp, tiff, svg, ico, bmp…)."
        )
    return problems


def _validate_media(action: Action, ctx: ValidationContext) -> list[str]:
    problems: list[str] = []
    if action.target not in TRANSPORT_LABELS:
        problems.append("Choose which transport control this button performs.")
    method = str(action.option("method", "keys"))
    if method == "command" and not str(action.option("player_command", "")).strip():
        problems.append("Pick the player executable, or switch the method back to Keys.")
    if method == "command":
        missing = program_missing_problem(str(action.option("player_command", "")))
        if missing:
            problems.append(missing)
        problems.extend(_validate_working_dir(action))
    return problems


def _validate_command(action: Action, ctx: ValidationContext) -> list[str]:
    problems: list[str] = []
    if not action.target.strip():
        problems.append("Enter the program or script this action should run.")
    elif not action.option("run_through_shell", False):
        missing = program_missing_problem(action.target)
        if missing:
            problems.append(missing)
    problems.extend(_validate_working_dir(action))
    return problems


def _validate_sequence(action: Action, ctx: ValidationContext) -> list[str]:
    problems: list[str] = []
    steps = action.option("steps", []) or []
    if not steps:
        return ["This sequence has no steps yet. Add at least one."]
    registry = ctx.registry or get_registry()
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            problems.append(f"Step {index} is not valid.")
            continue
        raw_action = step.get("action")
        if not raw_action:
            continue  # pure delay step
        nested = Action.from_dict(raw_action)
        spec = registry.get(nested.type)
        if spec is None:
            problems.append(f"Step {index} uses the unknown type \"{nested.type}\".")
            continue
        for message in registry.validate(nested, ctx):
            problems.append(f"Step {index} ({nested.display_name()}): {message}")
    return problems


def _validate_submenu(action: Action, ctx: ValidationContext) -> list[str]:
    if not action.submenu_id:
        return ["Choose which menu this button opens."]
    if ctx.config is not None:
        menu = ctx.config.menu_by_id(action.submenu_id)
        if menu is None:
            return ["That menu no longer exists."]
        containing = next(
            (menu_item for menu_item in ctx.config.menus if any(act.id == action.id for act in menu_item.actions)),
            None,
        )
        if containing is not None and menu.id == containing.id:
            return ["A menu cannot open itself. Pick a different menu."]
    return []


def _validate_keystroke(action: Action, ctx: ValidationContext) -> list[str]:
    try:
        parse_keystrokes(action.target)
    except KeyNotationError as exc:
        return [str(exc)]
    return []


def _validate_orbit(action: Action, ctx: ValidationContext) -> list[str]:
    if action.target not in ORBIT_COMMAND_KEYS:
        return ["Choose which ORBIT function this button performs."]
    return []


# ----------------------------------------------------------------------- runners
def _run_app(action: Action, ctx: ExecContext) -> Result:
    program = expand_path(action.target)
    window = str(action.option("window", "auto"))
    console = window == "console" or (window == "auto" and needs_console(program))
    working_dir = expand_path(action.working_dir) or (str(Path(program).parent) if Path(program).parent.is_dir() else "")
    try:
        _start_detached(program, action.args, working_dir, console=console)
    except LaunchError as exc:
        return Result.failure(f"Could not launch {action.display_name()}.", str(exc))
    return Result.success(f"Launched {action.display_name()}")


def _run_path(action: Action, ctx: ExecContext) -> Result:
    target = expand_path(action.target)
    if not Path(target).exists():
        return Result.failure("That path no longer exists.", target)
    if action.option("reveal_in_explorer", False):
        try:
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,", target], close_fds=True)
            else:
                open_with_default(str(Path(target).parent))
        except OSError as exc:
            return Result.failure("Explorer could not open that location.", str(exc))
        return Result.success(f"Revealed {Path(target).name}")
    try:
        open_with_default(target)
    except LaunchError as exc:
        return Result.failure("Nothing could open that path.", str(exc))
    return Result.success(f"Opened {action.display_name()}")


def _run_url(action: Action, ctx: ExecContext) -> Result:
    problem = urls.validate_url(action.target)
    if problem:
        return Result.failure("This button has an invalid address.", problem)
    try:
        open_url(action.target)
    except LaunchError as exc:
        return Result.failure("The browser could not be opened.", str(exc))
    return Result.success(f"Opened {action.display_name()}")


def _run_image(action: Action, ctx: ExecContext) -> Result:
    target = expand_path(action.target)
    if not Path(target).exists():
        return Result.failure("That image no longer exists.", target)
    viewer = str(action.option("viewer", "default"))
    if viewer == "orbit":
        opener = ctx.service("open_image")
        if opener is None:
            return Result.failure("The built-in viewer is unavailable in this session.")
        try:
            ctx.call_main(lambda: opener(target), True)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            return Result.failure("The ORBIT viewer could not open that image.", str(exc))
        return Result.success(f"Viewing {Path(target).name}")
    try:
        open_with_default(target)
    except LaunchError as exc:
        return Result.failure("No application could open that image.", str(exc))
    return Result.success(f"Opened {Path(target).name}")


def _run_media(action: Action, ctx: ExecContext) -> Result:
    outcome = ctx.media.control(
        action.target,
        method=str(action.option("method", "keys")),
        command_line=expand_path(str(action.option("player_command", ""))),
        args=[expand_path(str(item)) for item in (action.option("player_args", []) or [])],
        working_dir=expand_path(action.working_dir),
    )
    if outcome.ok:
        return Result.success(outcome.message, outcome.detail)
    return Result.failure(outcome.message, outcome.detail)


def _run_command(action: Action, ctx: ExecContext) -> Result:
    program = expand_path(action.target)
    args = [expand_path(str(item)) for item in action.args if str(item).strip()]
    working_dir = expand_path(action.working_dir)
    run_through_shell = bool(action.option("run_through_shell", False))
    capture = bool(action.option("capture_output", False))
    show_console = bool(action.option("show_console", False))

    if capture:
        timeout_value = float(action.option("timeout_seconds", 30) or 0) or None
        try:
            code, output = _cancellable_run(
                program,
                args,
                working_dir,
                ctx=ctx,
                timeout=timeout_value,
            )
        except LaunchError as exc:
            return Result.failure("The command could not be started.", str(exc))
        except Cancelled:
            raise
        if code == 0:
            detail = output or "The command finished without output."
            summary = _first_line(output) or "Finished successfully"
            return Result.success(f"{action.display_name()}: {summary}", detail)
        return Result.failure(
            f"{action.display_name()} finished with exit code {code}.",
            output or "Windows reported a failure and no output.",
        )

    try:
        launch_program(
            program,
            args,
            working_dir=working_dir,
            shell=run_through_shell,
            hidden=not show_console,
            console=show_console,
        )
    except LaunchError as exc:
        return Result.failure("The command could not be started.", str(exc))
    return Result.success(
        f"Started {action.display_name()}",
        "Started in a separate process. Enable “Wait and capture output” to see the result.",
    )


def _run_sequence(action: Action, ctx: ExecContext) -> Result:
    steps = action.option("steps", []) or []
    continue_on_error = bool(action.option("continue_on_error", True))
    if not steps:
        return Result.failure("This sequence has no steps configured.")

    lines: list[str] = []
    action_steps = 0
    succeeded = 0
    failed = 0
    for index, step in enumerate(steps, start=1):
        ctx.check_cancel()
        if not isinstance(step, dict):
            lines.append(f"{index}. skipped — invalid step")
            failed += 1
            if not continue_on_error:
                return Result.failure("A step was not valid; the sequence stopped.", "\n".join(lines))
            continue

        delay_ms = int(step.get("delay_ms", 0) or 0)
        if delay_ms > 0:
            waited = 0
            while waited < delay_ms:
                ctx.check_cancel()
                time.sleep(min(0.05, (delay_ms - waited) / 1000))
                waited += 50

        raw_action = step.get("action")
        if not raw_action:
            lines.append(f"{index}. waited {delay_ms} ms")
            continue

        action_steps += 1
        nested = Action.from_dict(raw_action)
        outcome = ctx.run_nested(nested)
        mark = "ok" if outcome.ok else "failed"
        lines.append(f"{index}. {nested.display_name()} — {mark}: {outcome.message}")
        if outcome.detail:
            lines.extend(f"    {line}" for line in outcome.detail.splitlines()[:4])
        if outcome.ok:
            succeeded += 1
        else:
            failed += 1
            if not continue_on_error:
                return Result.failure(
                    f"Sequence stopped at step {index} ({nested.display_name()}).", "\n".join(lines)
                )

    summary = f"{action.display_name()}: {succeeded} of {action_steps} steps completed"
    if failed:
        summary += f" · {failed} failed"
    return Result(ok=failed == 0, message=summary, detail="\n".join(lines))


def _run_submenu(action: Action, ctx: ExecContext) -> Result:
    if not action.submenu_id:
        return Result.failure("This button has no menu selected.")
    menu = ctx.config.menu_by_id(action.submenu_id)
    if menu is None:
        return Result.failure("That menu no longer exists.", f"Missing menu id: {action.submenu_id}")
    # Recursion is prevented in two places: configuration validation refuses a
    # submenu loop, and the overlay refuses to push a menu that is already open.
    opener = ctx.service("open_menu")
    if opener is None:
        return Result.failure("Menu navigation is unavailable in this session.")
    ctx.call_main(lambda: opener(menu.id), False)
    return Result.success(f"Opened {menu.name}", keep_menu_open=True)


def _run_keystroke(action: Action, ctx: ExecContext) -> Result:
    try:
        codes = parse_keystrokes(action.target)
    except KeyNotationError as exc:
        return Result.failure("This shortcut cannot be read.", str(exc))
    delay = float(action.option("settle_ms", 180) or 0) / 1000
    try:
        # Let the previously focused window regain focus before typing.
        waited = 0.0
        while waited < delay:
            ctx.check_cancel()
            time.sleep(min(0.05, delay - waited))
            waited += 0.05
        send_keystroke_sequence(codes)
    except LaunchError as exc:
        return Result.failure("The shortcut could not be sent.", str(exc))
    return Result.success(f"Sent {format_hotkey(action.target)}")


def _run_orbit(action: Action, ctx: ExecContext) -> Result:
    handler = ctx.service("orbit_command")
    if handler is None:
        return Result.failure("ORBIT commands are unavailable in this session.")
    ok, message = ctx.call_main(lambda: handler(action.target), True)
    if ok:
        return Result.success(message)
    return Result.failure(message)


# ------------------------------------------------------------------- registry
ORBIT_COMMANDS: tuple[tuple[str, str], ...] = (
    ("open_search", "Universal Search"),
    ("open_quick_controls", "Quick Controls"),
    ("open_music", "Music Hub"),
    ("open_widgets", "Productivity Widgets"),
    ("search_settings", "Search Settings"),
    ("quick_controls_settings", "Quick Controls Settings"),
    ("music_settings", "Music Settings"),
    ("widgets_settings", "Widget Settings"),
    ("open_quick_controls", "Quick Controls"),
    ("open_music", "Music Hub"),
    ("open_widgets", "Contextual Widgets"),
    ("open_studio", "Open ORBIT Studio"),
    ("open_config_folder", "Open the configuration folder"),
    ("reload_config", "Reload the configuration from disk"),
    ("toggle_overlay", "Show or hide the radial menu"),
    ("quit", "Quit ORBIT"),
)
ORBIT_COMMAND_KEYS: tuple[str, ...] = tuple(key for key, _label in ORBIT_COMMANDS)


class ActionRegistry:
    """Holds every action type ORBIT understands."""

    def __init__(self) -> None:
        self._types: dict[str, ActionTypeSpec] = {}

    # -------------------------------------------------------------- registration
    def register(self, spec: ActionTypeSpec) -> ActionTypeSpec:
        self._types[spec.key] = spec
        return spec

    def get(self, key: str) -> ActionTypeSpec | None:
        return self._types.get(key)

    def require(self, key: str) -> ActionTypeSpec:
        spec = self._types.get(key)
        if spec is None:
            raise KeyError(f"Unknown action type: {key}")
        return spec

    def keys(self) -> list[str]:
        return list(self._types)

    def all(self) -> list[ActionTypeSpec]:
        return list(self._types.values())

    def grouped(self) -> dict[str, list[ActionTypeSpec]]:
        groups: dict[str, list[ActionTypeSpec]] = {}
        for spec in self._types.values():
            groups.setdefault(spec.group, []).append(spec)
        for items in groups.values():
            items.sort(key=lambda item: item.label)
        return dict(sorted(groups.items()))

    # ------------------------------------------------------------------ editing
    def build_action(self, type_key: str, *, target: str = "", name: str = "") -> Action:
        spec = self.require(type_key)
        action = Action(type=spec.key, name=name or spec.label, icon=spec.icon, target=target)
        for item in spec.option_fields():
            if item.kind == "action_list":
                action.options[item.key] = []
            elif item.kind == "args":
                action.options[item.key] = []
            else:
                action.options[item.key] = item.default
        action.confirm = spec.default_confirm
        return action

    def default_icon(self, action: Action) -> str:
        if action.icon:
            return action.icon
        spec = self.get(action.type)
        return spec.icon if spec else "circle-dot"

    def icon_for(self, action: Action, config: Config | None = None) -> str:
        """Icon name for an action, following submenus to their menu icon."""
        if action.icon:
            return action.icon
        if action.type == "submenu" and config is not None:
            menu = config.menu_by_id(action.submenu_id)
            if menu is not None and menu.icon:
                return menu.icon
        spec = self.get(action.type)
        return spec.icon if spec else "circle-dot"

    # ------------------------------------------------------------------ queries
    def is_known(self, action: Action) -> bool:
        return action.type in self._types

    def keeps_menu_open(self, action: Action) -> bool:
        if action.close_menu is not None:
            return not action.close_menu
        spec = self.get(action.type)
        return bool(spec and spec.keeps_menu_open)

    def hide_before_run(self, action: Action) -> bool:
        spec = self.get(action.type)
        return bool(spec and spec.hide_before_run)

    def risk_label(self, action: Action) -> str:
        spec = self.get(action.type)
        if spec is None:
            return "Unknown type"
        if action.type == "command" and action.option("run_through_shell", False):
            return "Runs through cmd.exe — can change files or settings"
        if spec.risky:
            return "Can change files or system settings"
        return "Safe"

    def needs_confirmation(self, action: Action, config: Config) -> bool:
        if not action.confirm:
            return False
        return bool(config.settings.confirm_destructive)

    def validate(self, action: Action, ctx: ValidationContext | None = None) -> list[str]:
        context = ctx or ValidationContext()
        spec = self.get(action.type)
        if spec is None:
            return [f'Unknown action type "{action.type}".']
        problems: list[str] = []
        if not action.name.strip():
            problems.append("Give this button a name.")
        hotkey = action.shortcut.strip()
        if hotkey:
            error = validate_hotkey(hotkey)
            if error:
                problems.append(f"Shortcut: {error}")
        if spec.validate is not None:
            problems.extend(spec.validate(action, context))
        else:
            problems.extend(_validate_generic(action))
        return problems

    def validate_soft(self, action: Action, ctx: ValidationContext | None = None) -> list[str]:
        """Validation that only warns (used for the 'has issues' badge)."""
        context = ctx or ValidationContext()
        if action.type != "submenu":
            return []
        return self.validate(action, context)

    def execute(self, action: Action, ctx: ExecContext) -> Result:
        spec = self.get(action.type)
        if spec is None or spec.run is None:
            return Result.failure(f'ORBIT does not know how to run "{action.type}" actions.')
        if not action.enabled:
            return Result.failure("This button is disabled.")
        try:
            return spec.run(action, ctx)
        except Cancelled:
            raise
        except LaunchError as exc:
            return Result.failure("The action could not be completed.", str(exc))
        except Exception as exc:  # noqa: BLE001 - never let a broken action kill ORBIT
            ctx.log(f"Action {action.id} failed: {exc!r}")
            return Result.failure(
                f"{action.display_name()} could not be run.",
                f"{type(exc).__name__}: {exc}",
            )

    # ---------------------------------------------------------------- summaries
    def summary(self, action: Action, config: Config | None = None) -> str:
        spec = self.get(action.type)
        label = spec.label if spec else action.type
        if action.type == "submenu":
            target = config.menu_name(action.submenu_id) if config else action.submenu_id
            return f"{label} → {target or 'unset'}"
        if action.type == "media":
            return f"{label} · {TRANSPORT_LABELS.get(action.target, action.target or 'unset')}"
        if action.type == "sequence":
            steps = action.option("steps", []) or []
            return f"{label} · {len(steps)} step{'s' if len(steps) != 1 else ''}"
        if action.type == "command":
            args = " ".join(action.args)
            return f"{label} · {action.target} {args}".strip()
        if action.type == "orbit":
            command = dict(ORBIT_COMMANDS).get(action.target, action.target or "unset")
            return f"{label} · {command}"
        if action.type == "app":
            args = " ".join(action.args)
            return f"{label} · {action.target} {args}".strip()
        return f"{label} · {action.target or 'unset'}"


def _validate_generic(action: Action) -> list[str]:
    if not action.target.strip():
        return ["Choose a target for this button."]
    return []


def build_default_registry() -> ActionRegistry:
    """Create the registry with every built-in action type."""
    registry = ActionRegistry()

    registry.register(
        ActionTypeSpec(
            key="app",
            label="Application",
            description="Launch a program or executable, with optional arguments.",
            icon="app-window",
            group="Launch",
            validate=_validate_app,
            run=_run_app,
            fields=(
                Field("target", "Program", "program", "target", required=True,
                      help="Executable path, or a name on your PATH such as notepad.exe."),
                Field("args", "Arguments", "args", "args",
                      help="Each argument is passed separately, so spaces are safe."),
                Field("working_dir", "Working folder", "path_dir", "working_dir",
                      help="Optional. Defaults to the program's own folder."),
                Field("window", "Console window", "choice", default="auto", group="Advanced",
                      choices=(("auto", "Automatic"), ("console", "Show a console"), ("hidden", "Hidden")),
                      help="Command-line programs like cmd.exe need a console to be usable."),
            ),
        )
    )

    registry.register(
        ActionTypeSpec(
            key="path",
            label="File or folder",
            description="Open a file or folder with your default application.",
            icon="folder",
            group="Launch",
            validate=_validate_path,
            run=_run_path,
            fields=(
                Field("target", "File or folder", "path_any", "target", required=True),
                Field("reveal_in_explorer", "Show in Explorer instead of opening", "bool",
                      default=False,
                      help="Opens the parent folder with the item selected."),
            ),
        )
    )

    registry.register(
        ActionTypeSpec(
            key="url",
            label="Website",
            description="Open a web address in your default browser.",
            icon="globe",
            group="Launch",
            validate=_validate_url,
            run=_run_url,
            fields=(
                Field("target", "Address", "url", "target", required=True,
                      placeholder="https://example.com",
                      help="http, https, mailto and app links such as ms-settings: are allowed."),
            ),
        )
    )

    registry.register(
        ActionTypeSpec(
            key="image",
            label="Image",
            description="Open a picture in your default viewer or in ORBIT's own viewer.",
            icon="image",
            group="Launch",
            validate=_validate_image,
            run=_run_image,
            fields=(
                Field("target", "Image file", "path_file", "target", required=True,
                      file_filter=IMAGE_FILE_FILTER),
                Field("viewer", "Open with", "choice", default="default",
                      choices=(("default", "Default image viewer"), ("orbit", "ORBIT viewer")),
                      help="The ORBIT viewer is a frameless window you can pan and zoom."),
            ),
        )
    )

    registry.register(
        ActionTypeSpec(
            key="media",
            label="Media control",
            description="Play, pause or skip in the media player you are using.",
            icon="play",
            group="Automation",
            validate=_validate_media,
            run=_run_media,
            keeps_menu_open=False,
            hide_before_run=False,
            fields=(
                Field("target", "Control", "choice", "target", default="play_pause", required=True,
                      choices=tuple((key, TRANSPORT_LABELS[key]) for key in TRANSPORT_LABELS)),
                Field("method", "Method", "choice", default="keys",
                      choices=(("keys", "System media keys"), ("smtc", "Windows media session"), ("command", "Player command")),
                      help="Keys work with any player that handles the system media keys."),
                Field("player_command", "Player executable", "program", default="",
                      depends_on=("method", "command"),
                      help="For players with a command line, e.g. vlc.exe."),
                Field("player_args", "Player arguments", "args",
                      depends_on=("method", "command"),
                      help="For example: --play-pause"),
            ),
        )
    )

    registry.register(
        ActionTypeSpec(
            key="command",
            label="Command or script",
            description="Run a program, script or batch file with arguments.",
            icon="terminal",
            group="Automation",
            risky=True,
            validate=_validate_command,
            run=_run_command,
            fields=(
                Field("target", "Program or script", "program", "target", required=True,
                      help="Run directly, without a shell, unless you enable the shell option below."),
                Field("args", "Arguments", "args", "args",
                      help="Passed one by one. %USERPROFILE% and other variables are expanded."),
                Field("working_dir", "Working folder", "path_dir", "working_dir"),
                Field("capture_output", "Wait and capture the output", "bool", default=False,
                      help="ORBIT reports the exit code and output when the command finishes."),
                Field("timeout_seconds", "Timeout (seconds)", "int", default=30, minimum=0, maximum=3600,
                      depends_on=("capture_output", True),
                      help="0 means no timeout."),
                Field("show_console", "Show a console window", "bool", default=False, group="Advanced",
                      help="Useful for interactive scripts."),
                Field("run_through_shell", "Run through cmd.exe", "bool", default=False, group="Advanced",
                      advanced=True,
                      help="Off by default. Only needed for shell built-ins, pipes and redirection."),
            ),
        )
    )

    registry.register(
        ActionTypeSpec(
            key="sequence",
            label="Action sequence",
            description="Run several actions in order, with optional delays.",
            icon="layers",
            group="Automation",
            validate=_validate_sequence,
            run=_run_sequence,
            fields=(
                Field("steps", "Steps", "action_list", default=[]),
                Field("continue_on_error", "Keep going if a step fails", "bool", default=True),
            ),
        )
    )

    registry.register(
        ActionTypeSpec(
            key="submenu",
            label="Submenu",
            description="Open another radial menu, nested as deep as you like.",
            icon="layers",
            group="Navigation",
            keeps_menu_open=True,
            validate=_validate_submenu,
            run=_run_submenu,
            fields=(
                Field("submenu_id", "Menu", "submenu", "submenu_id", required=True,
                      help="The overlay transitions straight into this menu."),
            ),
        )
    )

    registry.register(
        ActionTypeSpec(
            key="keystroke",
            label="Keystroke",
            description="Send a key combination to whichever app is focused.",
            icon="keyboard",
            group="Automation",
            hide_before_run=True,
            validate=_validate_keystroke,
            run=_run_keystroke,
            fields=(
                Field("target", "Shortcut to send", "keystrokes", "target", required=True,
                      placeholder="ctrl+shift+s",
                      help="Use + inside a chord and , between presses: ctrl+c, alt+tab"),
                Field("settle_ms", "Wait before sending (ms)", "int", default=180, minimum=0, maximum=5000,
                      help="Gives the previous window time to take focus back."),
            ),
        )
    )

    registry.register(
        ActionTypeSpec(
            key="orbit",
            label="ORBIT",
            description="Control ORBIT itself: studio, configuration, reload or quit.",
            icon="orbit",
            group="Navigation",
            validate=_validate_orbit,
            run=_run_orbit,
            fields=(
                Field("target", "ORBIT function", "choice", "target", default="open_studio", required=True,
                      choices=ORBIT_COMMANDS),
            ),
        )
    )

    return registry


_REGISTRY: ActionRegistry | None = None


def get_registry() -> ActionRegistry:
    """The process-wide registry."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = build_default_registry()
    return _REGISTRY


def iter_field_stores(spec: ActionTypeSpec) -> Iterable[Field]:
    return spec.fields

