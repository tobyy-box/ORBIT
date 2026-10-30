"""Execution: commands, sequences, cancellation, visibility and closing rules."""

from __future__ import annotations

import sys
import threading
import time

import pytest

from core.action_executor import ActionExecutor
from core.action_registry import Cancelled, Result, get_registry
from core.models import Action, Config, Menu


@pytest.fixture()
def config() -> Config:
    from core.config_manager import default_config

    return default_config()


@pytest.fixture()
def executor(config: Config) -> ActionExecutor:
    return ActionExecutor(lambda: config, registry=get_registry())


def _command(action: Action, executor: ActionExecutor) -> Result:
    return executor.run(action)


def test_captured_command_reports_stdout(executor: ActionExecutor) -> None:
    action = Action(
        name="Echo",
        type="command",
        target=sys.executable,
        args=["-c", "print('orbit ok')"],
        options={"capture_output": True, "timeout_seconds": 20},
    )
    result = _command(action, executor)
    assert result.ok is True
    assert "orbit ok" in (result.message + result.detail)


def test_failing_command_reports_exit_code(executor: ActionExecutor) -> None:
    action = Action(
        name="Boom",
        type="command",
        target=sys.executable,
        args=["-c", "import sys; sys.stderr.write('bad things'); sys.exit(4)"],
        options={"capture_output": True, "timeout_seconds": 20},
    )
    result = _command(action, executor)
    assert result.ok is False
    assert "4" in result.message
    assert "bad things" in result.detail


def test_missing_program_is_reported_clearly(executor: ActionExecutor) -> None:
    action = Action(name="Ghost", type="command", target="definitely-not-a-real-program.exe")
    result = _command(action, executor)
    assert result.ok is False
    assert "not found" in result.detail.lower() or "not found" in result.message.lower()


def test_command_timeout_is_cancellable_and_bounded(executor: ActionExecutor) -> None:
    action = Action(
        name="Slow",
        type="command",
        target=sys.executable,
        args=["-c", "import time; time.sleep(3)"],
        options={"capture_output": True, "timeout_seconds": 0.4},
    )
    started = time.monotonic()
    result = _command(action, executor)
    assert result.ok is False
    assert "Timed out" in result.detail
    assert time.monotonic() - started < 3.0


def test_cancellation_stops_a_running_command(config: Config) -> None:
    executor = ActionExecutor(lambda: config, registry=get_registry())
    cancel = threading.Event()
    context = executor.build_context(cancel)
    action = Action(
        name="Long",
        type="command",
        target=sys.executable,
        args=["-c", "import time; time.sleep(10)"],
        options={"capture_output": True},
    )

    def cancel_soon() -> None:
        time.sleep(0.35)
        cancel.set()

    threading.Thread(target=cancel_soon, daemon=True).start()
    started = time.monotonic()
    with pytest.raises(Cancelled):
        executor.run(action, ctx=context)
    assert time.monotonic() - started < 5.0


def test_sequence_runs_every_step(executor: ActionExecutor) -> None:
    action = Action(
        name="Seq",
        type="sequence",
        options={
            "continue_on_error": True,
            "steps": [
                {"action": {"name": "One", "type": "command", "target": sys.executable,
                            "args": ["-c", "print(1)"], "options": {"capture_output": True}}},
                {"delay_ms": 60},
                {"action": {"name": "Two", "type": "command", "target": sys.executable,
                            "args": ["-c", "print(2)"], "options": {"capture_output": True}}},
            ],
        },
    )
    result = executor.run(action)
    assert result.ok is True
    assert "2 of 2" in result.message
    assert "waited 60 ms" in result.detail


def test_sequence_stops_on_failure_when_asked(executor: ActionExecutor) -> None:
    action = Action(
        name="Seq",
        type="sequence",
        options={
            "continue_on_error": False,
            "steps": [
                {"action": {"name": "Bad", "type": "url", "target": "not-a-url"}},
                {"action": {"name": "Never", "type": "url", "target": "https://example.com"}},
            ],
        },
    )
    result = executor.run(action)
    assert result.ok is False
    assert "stopped at step 1" in result.message.lower()
    assert "Never" not in result.detail


def test_sequence_reports_nested_failure_but_continues(executor: ActionExecutor) -> None:
    action = Action(
        name="Seq",
        type="sequence",
        options={
            "continue_on_error": True,
            "steps": [
                {"action": {"name": "Bad", "type": "url", "target": "nope"}},
                {"action": {"name": "Good", "type": "command", "target": sys.executable,
                            "args": ["-c", "print(3)"], "options": {"capture_output": True}}},
            ],
        },
    )
    result = executor.run(action)
    assert result.ok is False
    assert "1 of 2" in result.message
    assert "failed" in result.detail


def test_sequence_depth_is_bounded(executor: ActionExecutor) -> None:
    # A chain of nested sequences deeper than ExecContext.max_depth must stop
    # instead of recursing forever.
    step = {"name": "Level 5", "type": "url", "target": "https://example.com"}
    for level in range(4, 0, -1):
        step = {
            "name": f"Level {level}",
            "type": "sequence",
            "options": {"steps": [{"action": step}], "continue_on_error": True},
        }
    result = executor.run(Action(name="Outer", type="sequence", options={"steps": [{"action": step}]}))
    assert result.ok is False
    assert "depth" in (result.detail + result.message).lower()


def test_visibility_conditions(executor: ActionExecutor, tmp_path) -> None:
    existing = tmp_path / "here.txt"
    existing.write_text("x", encoding="utf-8")

    visible = Action(name="V", type="url", target="https://example.com",
                     visible_when={"file_exists": str(existing)})
    assert executor.is_visible(visible) is True

    hidden = Action(name="H", type="url", target="https://example.com",
                    visible_when={"file_exists": str(tmp_path / "gone.txt")})
    assert executor.is_visible(hidden) is False

    folder_rule = Action(name="D", type="url", target="https://example.com",
                         visible_when={"folder_exists": str(tmp_path)})
    assert executor.is_visible(folder_rule) is True

    disabled = Action(name="X", type="url", target="https://example.com", enabled=False)
    assert executor.is_visible(disabled) is False

    unknown_rule = Action(name="U", type="url", target="https://example.com",
                          visible_when={"moon_phase": "full"})
    assert executor.is_visible(unknown_rule) is True  # never hide by accident


def test_visible_actions_filters_the_menu(executor: ActionExecutor, tmp_path) -> None:
    actions = [
        Action(name="A", type="url", target="https://a.example", order=0),
        Action(name="B", type="url", target="https://b.example", order=1,
               visible_when={"file_exists": str(tmp_path / "missing")}),
        Action(name="C", type="url", target="https://c.example", order=2),
    ]
    assert [a.name for a in executor.visible_actions(actions)] == ["A", "C"]


def test_close_menu_rules(executor: ActionExecutor) -> None:
    plain = Action(name="P", type="url", target="https://example.com")
    assert executor.should_close_menu(plain) is True

    keep = Action(name="K", type="url", target="https://example.com", close_menu=False)
    assert executor.should_close_menu(keep) is False

    submenu = Action(name="S", type="submenu", submenu_id="x")
    assert executor.should_close_menu(submenu) is False

    override = Action(name="O", type="submenu", submenu_id="x", close_menu=True)
    assert executor.should_close_menu(override) is True

    executor.config.settings.close_after_execution = False
    assert executor.should_close_menu(plain) is False

    keep_open_result = Result(True, "opened", keep_menu_open=True)
    executor.config.settings.close_after_execution = True
    assert executor.should_close_menu(plain, keep_open_result) is False


def test_failure_text_is_always_useful(executor: ActionExecutor) -> None:
    action = Action(name="Broken", type="command", target="nope.exe")
    result = executor.run(action)
    text = executor.describe_failure(action, result)
    assert "Broken" in text
    assert "command" in text.lower()
    assert len(text.splitlines()) >= 2


def test_submenu_action_asks_the_overlay_for_a_menu(config: Config) -> None:
    opened: list[str] = []
    executor = ActionExecutor(
        lambda: config,
        registry=get_registry(),
        services={"open_menu": lambda menu_id: opened.append(menu_id)},
    )
    action = Action(name="Sub", type="submenu", submenu_id="places")
    result = executor.run(action)
    assert result.ok is True
    assert result.keep_menu_open is True
    assert opened == ["places"]


def test_submenu_with_missing_menu_fails(executor: ActionExecutor) -> None:
    result = executor.run(Action(name="Gone", type="submenu", submenu_id="nope"))
    assert result.ok is False
    assert "no longer exists" in result.message


def test_unknown_action_type_never_raises(executor: ActionExecutor) -> None:
    result = executor.run(Action(name="Weird", type="quantum"))
    assert result.ok is False


def test_media_action_reports_missing_method_config(config: Config) -> None:
    executor = ActionExecutor(lambda: config, registry=get_registry())
    action = Action(name="Music", type="media", target="play_pause",
                    options={"method": "command", "player_command": ""})
    result = executor.run(action)
    assert result.ok is False
    assert "no player command" in result.message.lower()


def test_orbit_command_is_dispatched_through_the_service(config: Config) -> None:
    calls: list[str] = []

    def handler(target: str) -> tuple[bool, str]:
        calls.append(target)
        return True, "Studio opened."

    executor = ActionExecutor(lambda: config, registry=get_registry(),
                              services={"orbit_command": handler})
    result = executor.run(Action(name="Studio", type="orbit", target="open_studio"))
    assert result.ok is True
    assert calls == ["open_studio"]


def test_sequence_cannot_recurse_into_itself(config: Config) -> None:
    executor = ActionExecutor(lambda: config, registry=get_registry())
    action = Action(name="Loop", type="sequence", options={"steps": []})
    action.options["steps"] = [{"action": action.to_dict()}]
    result = executor.run(action)
    assert result.ok is False
    text = (result.detail + result.message).lower()
    assert "loop" in text or "depth" in text


def test_menu_ordering_used_by_the_wheel(config: Config) -> None:
    menu = Menu(name="M", actions=[
        Action(name="Second", order=1),
        Action(name="First", order=0),
    ])
    assert [a.name for a in menu.sorted_actions()] == ["First", "Second"]
