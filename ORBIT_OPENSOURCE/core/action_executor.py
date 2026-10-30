"""Action execution: visibility rules, dispatch, cancellation, error reporting.

Two layers:

:class:`ActionExecutor`
    Pure logic.  Given an action it decides whether it is currently visible,
    runs it through the registry and reports a :class:`Result`.  Usable from a
    worker thread and fully unit-testable.

:class:`ActionRunner`
    Qt wrapper that owns a small thread pool, never blocks the interface, keeps
    one cancellation token per run and forwards results as signals.
"""

from __future__ import annotations

import concurrent.futures
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, Signal, Slot

from core.action_registry import (
    ActionRegistry,
    Cancelled,
    ExecContext,
    Result,
    ValidationContext,
    get_registry,
)
from core.media_controller import MediaController
from core.models import Action, Config
from core.paths import expand_path

UNKNOWN_VISIBILITY_KEYS: set[str] = set()


@dataclass(slots=True)
class ExecutionRecord:
    """One completed (or cancelled) run, used for feedback and diagnostics."""

    action_id: str
    action_name: str
    ok: bool
    message: str
    detail: str = ""
    run_id: str = ""
    cancelled: bool = False
    started_at: float = 0.0
    duration_ms: int = 0
    result: Result | None = None

    def to_result(self) -> Result:
        return Result(self.ok, self.message, self.detail)


@dataclass(slots=True)
class RunHandle:
    """Token for a run that is still in flight."""

    run_id: str
    action_id: str
    action_name: str
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def cancel(self) -> None:
        self.cancel_event.set()

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()


class ActionExecutor:
    """Runs actions and answers questions about them."""

    def __init__(
        self,
        config_provider: Callable[[], Config] | None = None,
        *,
        registry: ActionRegistry | None = None,
        media: MediaController | None = None,
        services: dict[str, Callable[..., Any]] | None = None,
        invoke_main: Callable[[Callable[[], Any], bool], Any] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._config_provider = config_provider or (lambda: Config())
        self.registry = registry or get_registry()
        self.media = media or MediaController()
        self.services = services if services is not None else {}
        self.invoke_main = invoke_main
        self._log = log or (lambda message: None)

    # ------------------------------------------------------------------ context
    @property
    def config(self) -> Config:
        return self._config_provider()

    def build_context(self, cancel_event: threading.Event | None = None) -> ExecContext:
        context = ExecContext(
            config=self.config,
            media=self.media,
            cancel=cancel_event or threading.Event(),
            log=self._log,
            invoke_main=self.invoke_main,
            services=self.services,
        )
        context.runner = lambda action, ctx: self.run(action, ctx=ctx)
        return context

    def validation_context(self) -> ValidationContext:
        return ValidationContext(config=self.config, registry=self.registry)

    # --------------------------------------------------------------- visibility
    def is_visible(self, action: Action) -> bool:
        """Evaluate an action's visibility conditions.

        Unknown conditions never hide a button: it is safer to show something
        the user configured than to silently swallow it.
        """
        rules = action.visible_when or {}
        if not rules:
            return action.enabled
        if not action.enabled:
            return False

        always = rules.get("always")
        if isinstance(always, bool) and not always:
            return False

        for key in ("file_exists", "folder_exists"):
            value = rules.get(key)
            if value in (None, "", []):
                continue
            candidates = value if isinstance(value, (list, tuple)) else [value]
            for candidate in candidates:
                if not candidate:
                    continue
                path = Path(expand_path(str(candidate)))
                if key == "file_exists" and not path.exists():
                    return False
                if key == "folder_exists" and not path.is_dir():
                    return False

        app_running = rules.get("app_running")
        if app_running:
            from core.launcher import is_process_running

            if not is_process_running(str(app_running)):
                return False

        for key in rules:
            if key not in {"always", "file_exists", "folder_exists", "app_running"}:
                if key not in UNKNOWN_VISIBILITY_KEYS:
                    UNKNOWN_VISIBILITY_KEYS.add(key)
                    self._log(f"Ignoring unknown visibility condition: {key}")
        return True

    def visible_actions(self, actions: list[Action]) -> list[Action]:
        return [action for action in actions if self.is_visible(action)]

    # ------------------------------------------------------------------ execution
    def run(self, action: Action, *, ctx: ExecContext | None = None) -> Result:
        """Execute ``action`` synchronously (call this from a worker thread)."""
        context = ctx or self.build_context()
        return self.registry.execute(action, context)

    def should_close_menu(self, action: Action, result: Result | None = None) -> bool:
        """Decide whether the overlay closes after an action ran."""
        if result is not None and result.keep_menu_open:
            return False
        if action.close_menu is not None:
            return bool(action.close_menu)
        if self.registry.keeps_menu_open(action):
            return False
        return bool(self.config.settings.close_after_execution)

    def describe_failure(self, action: Action, result: Result) -> str:
        """User-facing failure text (never empty, never just an error code)."""
        name = action.display_name()
        parts = [result.message or f"{name} could not be completed."]
        if result.detail:
            parts.append(result.detail)
        parts.append(f"Button: {name}  ·  Type: {action.type}")
        return "\n".join(parts)

    def validate(self, action: Action) -> list[str]:
        return self.registry.validate(action, self.validation_context())


class ActionRunner(QObject):
    """Threaded front end for :class:`ActionExecutor`."""

    started = Signal(object)          # RunHandle
    finished = Signal(object)         # ExecutionRecord
    _main_invoke = Signal(object)

    def __init__(
        self,
        config_provider: Callable[[], Config],
        *,
        registry: ActionRegistry | None = None,
        media: MediaController | None = None,
        services: dict[str, Callable[..., Any]] | None = None,
        log: Callable[[str], None] | None = None,
        max_workers: int = 3,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._main_thread = threading.get_ident()
        self.executor = ActionExecutor(
            config_provider,
            registry=registry,
            media=media,
            services=services,
            invoke_main=self.invoke_main,
            log=log,
        )
        self._pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="orbit-action"
        )
        self._runs: dict[str, RunHandle] = {}
        self._lock = threading.Lock()
        self._counter = 0
        self._main_invoke.connect(self._on_main_invoke, Qt.ConnectionType.QueuedConnection)

    # --------------------------------------------------------------- main thread
    def invoke_main(self, function: Callable[[], Any], wait: bool = True) -> Any:
        """Run ``function`` on the GUI thread.

        Called from worker threads it queues the call and optionally waits;
        called from the GUI thread it runs immediately.
        """
        if threading.get_ident() == self._main_thread:
            return function()

        box: dict[str, Any] = {}
        done = threading.Event()

        def wrapper() -> None:
            try:
                box["value"] = function()
            except Exception as exc:  # noqa: BLE001 - re-raised in the caller
                box["error"] = exc
            finally:
                done.set()

        self._main_invoke.emit(wrapper)
        if not wait:
            return None
        if not done.wait(timeout=20):
            raise RuntimeError("The interface did not respond in time.")
        if "error" in box:
            raise box["error"]
        return box.get("value")

    @Slot(object)
    def _on_main_invoke(self, wrapper: Callable[[], None]) -> None:
        wrapper()

    # -------------------------------------------------------------------- running
    def submit(self, action: Action) -> RunHandle:
        with self._lock:
            self._counter += 1
            run_id = f"run{self._counter}"
            handle = RunHandle(run_id=run_id, action_id=action.id, action_name=action.display_name())
            self._runs[run_id] = handle
        self.started.emit(handle)
        self._pool.submit(self._work, handle, action)
        return handle

    def _work(self, handle: RunHandle, action: Action) -> None:
        started = time.monotonic()
        context = self.executor.build_context(handle.cancel_event)
        cancelled = False
        try:
            result = self.executor.run(action, ctx=context)
        except Cancelled:
            result = Result(False, "Cancelled.", "You stopped this action before it finished.")
            cancelled = True
        except Exception as exc:  # noqa: BLE001 - defensive, registry already guards
            result = Result(False, f"{action.display_name()} failed unexpectedly.", repr(exc))
        record = ExecutionRecord(
            action_id=action.id,
            action_name=action.display_name(),
            ok=result.ok and not cancelled,
            message=result.message,
            detail=result.detail,
            run_id=handle.run_id,
            cancelled=cancelled,
            started_at=started,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        record.result = result
        with self._lock:
            self._runs.pop(handle.run_id, None)
        self.finished.emit(record)

    # ----------------------------------------------------------------- cancelling
    def cancel(self, run_id: str) -> bool:
        with self._lock:
            handle = self._runs.get(run_id)
        if handle is None:
            return False
        handle.cancel()
        return True

    def cancel_all(self) -> None:
        with self._lock:
            handles = list(self._runs.values())
        for handle in handles:
            handle.cancel()

    def active_runs(self) -> list[RunHandle]:
        with self._lock:
            return list(self._runs.values())

    def shutdown(self, *, wait: bool = False) -> None:
        self.cancel_all()
        self._pool.shutdown(wait=wait, cancel_futures=True)
