"""Appearance and behaviour settings pages."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from core.keycodes import format_hotkey, validate_hotkey
from core.startup import describe_support, set_enabled as set_startup_enabled
from ui.preview_panel import PreviewPanel
from ui.theme_manager import EASING_CHOICES, build_theme
from ui.widgets import (
    Card,
    ColorButton,
    FloatSlider,
    HotkeyEdit,
    LabeledSlider,
    Page,
    Row,
    ScrollPage,
    combo,
    muted_label,
    section_label,
    small_spin,
)

THEME_CHOICES = (
    ("orbit-dark", "ORBIT Dark"),
    ("orbit-light", "ORBIT Light"),
    ("custom", "Custom colours"),
)
ACCENT_PRESETS = (
    ("#6C8CFF", "Orbit blue"),
    ("#A78BFA", "Violet"),
    ("#35C7B0", "Mint"),
    ("#F59E72", "Sunset"),
    ("#F06A93", "Rose"),
    ("#E6B85C", "Gold"),
)
SHADER_CHOICES = (
    ("vignette", "Vignette"),
    ("soft", "Soft focus"),
    ("flat", "Flat tint"),
    ("aurora", "Aurora motion"),
    ("particles", "Mouse particles"),
    ("ripple", "Mouse ripples"),
)
LABEL_CHOICES = (
    ("hover", "On hover and selection"),
    ("always", "Always"),
    ("never", "Never"),
)


class AppearancePage(Page):
    title = "Appearance"
    description = "Shape, colour, motion and transparency. The preview updates as you change things."

    def __init__(self, studio, parent: QWidget | None = None) -> None:
        super().__init__(studio, parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addWidget(self.header())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        self.page = ScrollPage()
        splitter.addWidget(self.page)
        body = self.page.body()

        self._build_theme(body)
        self._build_layout(body)
        self._build_overlay(body)
        self._build_labels(body)
        self._build_motion(body)
        self.page.finish()

        self._preview = PreviewPanel(
            lambda: self.working,
            theme_provider=lambda: build_theme(self.working.settings),
            executor=self.executor,
        )
        splitter.addWidget(self._preview)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([560, 430])
        layout.addWidget(splitter, 1)
        self.load()

    # ------------------------------------------------------------------- cards
    def _build_theme(self, body: QVBoxLayout) -> None:
        settings = self.working.settings
        self._card_theme = Card()
        self._card_theme.add(section_label("Theme"))
        self._theme_combo = combo(THEME_CHOICES, settings.theme, on_change=self._on_theme)
        self._card_theme.add_row(Row("Theme", self._theme_combo))

        self._accent_preset = combo(ACCENT_PRESETS, settings.accent, on_change=self._on_accent_preset)
        self._card_theme.add_row(Row("Accent preset", self._accent_preset, help_text="A quick starting colour. You can still choose any colour below."))

        self._accent_button = ColorButton(settings.accent, title="Accent colour")
        self._accent_button.colourChanged.connect(self._on_accent)
        self._card_theme.add_row(
            Row("Accent colour", self._accent_button, help_text="Used for highlights, selection and glow.")
        )
        body.addWidget(self._card_theme)

        self._card_custom = Card()
        self._card_custom.add(section_label("Custom palette"))
        self._custom_colours: dict[str, ColorButton] = {}
        for key, label in (
            ("custom_background", "Background"),
            ("custom_surface", "Surface"),
            ("custom_surface_alt", "Surface (raised)"),
            ("custom_text", "Text"),
            ("custom_text_muted", "Muted text"),
            ("custom_border", "Border"),
            ("custom_danger", "Danger"),
        ):
            button = ColorButton(getattr(settings, key), title=label)
            button.colourChanged.connect(lambda value, name=key: self._on_custom_colour(name, value))
            self._custom_colours[key] = button
            self._card_custom.add_row(Row(label, button))
        body.addWidget(self._card_custom)

    def _build_layout(self, body: QVBoxLayout) -> None:
        settings = self.working.settings
        card = Card()
        card.add(section_label("Layout"))
        self._sliders: dict[str, LabeledSlider] = {}
        for key, label, value, low, high, suffix in (
            ("radius", "Radius", settings.radius, 90, 320, " px"),
            ("button_size", "Button size", settings.button_size, 48, 140, " px"),
            ("icon_size", "Icon size", settings.icon_size, 14, 64, " px"),
            ("ring_gap", "Ring gap", settings.ring_gap, 60, 200, " px"),
        ):
            slider = LabeledSlider(value, minimum=low, maximum=high, suffix=suffix)
            slider.valueChanged.connect(lambda _v, name=key: self._on_int_setting(name))
            self._sliders[key] = slider
            card.add_row(Row(label, slider))

        self._per_ring = small_spin(settings.max_per_ring, minimum=3, maximum=12)
        self._per_ring.setSuffix(" per ring")
        self._per_ring.valueChanged.connect(lambda _v: self._on_int_setting("max_per_ring"))
        card.add_row(
            Row(
                "Buttons per ring",
                self._per_ring,
                help_text="Extra buttons move onto a second and third ring.",
            )
        )
        body.addWidget(card)

    def _build_overlay(self, body: QVBoxLayout) -> None:
        settings = self.working.settings
        card = Card()
        card.add(section_label("Overlay"))
        self._overlay_opacity = FloatSlider(settings.overlay_opacity, minimum=0.0, maximum=1.0, step=0.01)
        self._overlay_opacity.valueChanged.connect(lambda _v: self._on_float_setting("overlay_opacity"))
        card.add_row(
            Row(
                "Background dimming",
                self._overlay_opacity,
                help_text="How much the desktop is dimmed behind the menu. 0 disables the dimming.",
            )
        )
        self._menu_opacity = FloatSlider(settings.menu_opacity, minimum=0.3, maximum=1.0, step=0.01)
        self._menu_opacity.valueChanged.connect(lambda _v: self._on_float_setting("menu_opacity"))
        card.add_row(Row("Menu surface", self._menu_opacity, help_text="Opacity of the buttons and hub."))

        self._shader_enabled = QCheckBox("Use a screen shader")
        self._shader_enabled.setChecked(settings.screen_shader_enabled)
        self._shader_enabled.toggled.connect(lambda value: self._on_bool_setting("screen_shader_enabled", value))
        card.add(self._shader_enabled)
        self._shader_combo = combo(SHADER_CHOICES, settings.screen_shader, on_change=self._on_shader)
        card.add_row(Row("Shader style", self._shader_combo, help_text="Optional desktop tint behind the wheel. Disable it for a clear screen."))
        self._shader_strength = FloatSlider(settings.screen_shader_strength, minimum=0.1, maximum=1.0, step=0.05)
        self._shader_strength.valueChanged.connect(lambda _v: self._on_float_setting("screen_shader_strength"))
        card.add_row(Row("Shader strength", self._shader_strength, help_text="Controls how visible the optional shader is."))

        self._shadow = QCheckBox("Soft shadows")
        self._shadow.setChecked(settings.button_shadow)
        self._shadow.toggled.connect(lambda value: self._on_bool_setting("button_shadow", value))
        card.add(self._shadow)

        self._glow = QCheckBox("Accent glow on the selected button")
        self._glow.setChecked(settings.button_glow)
        self._glow.toggled.connect(lambda value: self._on_bool_setting("button_glow", value))
        card.add(self._glow)
        body.addWidget(card)

    def _build_labels(self, body: QVBoxLayout) -> None:
        settings = self.working.settings
        card = Card()
        card.add(section_label("Labels"))
        self._labels_combo = combo(LABEL_CHOICES, settings.show_labels, on_change=self._on_labels)
        card.add_row(Row("Show button names", self._labels_combo))
        self._descriptions = QCheckBox("Show the selected action's description")
        self._descriptions.setChecked(settings.show_descriptions)
        self._descriptions.toggled.connect(lambda value: self._on_bool_setting("show_descriptions", value))
        card.add(self._descriptions)

        self._badges = QCheckBox("Show keyboard shortcut badges")
        self._badges.setChecked(settings.show_shortcut_badges)
        self._badges.toggled.connect(lambda value: self._on_bool_setting("show_shortcut_badges", value))
        card.add(self._badges)

        self._pulse = QCheckBox("Visual feedback when an action runs")
        self._pulse.setChecked(settings.feedback_pulse)
        self._pulse.toggled.connect(lambda value: self._on_bool_setting("feedback_pulse", value))
        card.add(self._pulse)
        body.addWidget(card)

    def _build_motion(self, body: QVBoxLayout) -> None:
        settings = self.working.settings
        card = Card()
        card.add(section_label("Motion"))
        self._duration = LabeledSlider(
            settings.animation_duration, minimum=0, maximum=600, suffix=" ms", step=10
        )
        self._duration.valueChanged.connect(lambda _v: self._on_int_setting("animation_duration"))
        card.add_row(
            Row(
                "Animation duration",
                self._duration,
                help_text="Opening, closing and hover transitions. 0 makes the menu appear instantly.",
            )
        )
        self._easing_combo = combo(EASING_CHOICES, settings.easing, on_change=self._on_easing)
        card.add_row(Row("Easing", self._easing_combo))
        self._reduced = QCheckBox("Reduce motion (skip open and close animations)")
        self._reduced.setChecked(settings.reduced_motion)
        self._reduced.toggled.connect(lambda value: self._on_bool_setting("reduced_motion", value))
        card.add(self._reduced)
        body.addWidget(card)

    # --------------------------------------------------------------------- load
    def load(self) -> None:
        settings = self.working.settings
        self._theme_combo.setCurrentIndex(max(0, self._theme_combo.findData(settings.theme)))
        self._accent_preset.setCurrentIndex(max(0, self._accent_preset.findData(settings.accent)))
        self._accent_button.set_colour(settings.accent)
        for key, button in self._custom_colours.items():
            button.set_colour(getattr(settings, key))
        self._card_custom.setVisible(settings.theme == "custom")
        for key, slider in self._sliders.items():
            slider.set_value(int(getattr(settings, key)))
        self._per_ring.setValue(int(settings.max_per_ring))
        self._overlay_opacity.set_value(settings.overlay_opacity)
        self._menu_opacity.set_value(settings.menu_opacity)
        self._shader_enabled.setChecked(settings.screen_shader_enabled)
        self._shader_combo.setCurrentIndex(max(0, self._shader_combo.findData(settings.screen_shader)))
        self._shader_strength.set_value(settings.screen_shader_strength)
        self._shadow.setChecked(settings.button_shadow)
        self._glow.setChecked(settings.button_glow)
        self._labels_combo.setCurrentIndex(max(0, self._labels_combo.findData(settings.show_labels)))
        self._descriptions.setChecked(settings.show_descriptions)
        self._badges.setChecked(settings.show_shortcut_badges)
        self._pulse.setChecked(settings.feedback_pulse)
        self._duration.set_value(int(settings.animation_duration))
        self._easing_combo.setCurrentIndex(max(0, self._easing_combo.findData(settings.easing)))
        self._reduced.setChecked(settings.reduced_motion)
        self._preview.refresh()

    # ------------------------------------------------------------------ handlers
    def _apply(self) -> None:
        self._preview.refresh()
        self.notify_changed()

    def _on_theme(self, value: str) -> None:
        self.working.settings.theme = value
        self._card_custom.setVisible(value == "custom")
        self._apply()

    def _on_accent(self, value: str) -> None:
        self.working.settings.accent = value
        self._apply()

    def _on_accent_preset(self, value: str) -> None:
        self.working.settings.accent = value
        self._accent_button.set_colour(value)
        self._apply()

    def _on_custom_colour(self, key: str, value: str) -> None:
        setattr(self.working.settings, key, value)
        self._apply()

    def _on_int_setting(self, key: str) -> None:
        source = self._per_ring if key == "max_per_ring" else self._sliders[key]
        setattr(self.working.settings, key, int(source.value()))
        self._apply()

    def _on_float_setting(self, key: str) -> None:
        source = {
            "overlay_opacity": self._overlay_opacity,
            "menu_opacity": self._menu_opacity,
            "screen_shader_strength": self._shader_strength,
        }[key]
        setattr(self.working.settings, key, float(source.value()))
        self._apply()

    def _on_bool_setting(self, key: str, value: bool) -> None:
        setattr(self.working.settings, key, bool(value))
        self._apply()

    def _on_labels(self, value: str) -> None:
        self.working.settings.show_labels = value
        self._apply()

    def _on_easing(self, value: str) -> None:
        self.working.settings.easing = value
        self._apply()

    def _on_shader(self, value: str) -> None:
        self.working.settings.screen_shader = value
        self._apply()


class BehaviourPage(Page):
    title = "Behaviour"
    description = "Activation, dismissal, startup and multi-monitor behaviour."

    def __init__(self, studio, parent: QWidget | None = None) -> None:
        super().__init__(studio, parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.page = ScrollPage()
        layout.addWidget(self.page)
        body = self.page.body()
        body.addWidget(self.header())

        self._build_activation(body)
        self._build_dismiss(body)
        self._build_integration(body)
        self._build_monitor(body)
        self.page.finish()
        self.load()

    # ------------------------------------------------------------------- cards
    def _build_activation(self, body: QVBoxLayout) -> None:
        settings = self.working.settings
        card = Card()
        card.add(section_label("Activation"))
        self._hotkey = HotkeyEdit(settings.hotkey)
        self._hotkey.captured.connect(self._on_hotkey_captured)
        hotkey_row = QHBoxLayout()
        hotkey_row.setSpacing(8)
        hotkey_row.addWidget(self._hotkey, 1)
        self._apply_hotkey_button = QPushButton("Apply now")
        self._apply_hotkey_button.setToolTip("Register this shortcut immediately, without saving.")
        self._apply_hotkey_button.clicked.connect(self._apply_hotkey)
        hotkey_row.addWidget(self._apply_hotkey_button)
        holder = QWidget()
        holder.setLayout(hotkey_row)
        card.add_row(
            Row(
                "Activation shortcut",
                holder,
                help_text="Click the field and press the combination. It works while other apps are focused.",
            )
        )
        self._hotkey_status = muted_label("")
        card.add(self._hotkey_status)

        self._fallback = HotkeyEdit(settings.fallback_hotkey)
        self._fallback.captured.connect(self._on_fallback_captured)
        card.add_row(
            Row(
                "Backup shortcut",
                self._fallback,
                help_text="Used automatically when the shortcut above is already taken by another app.",
            )
        )
        card.add(
            muted_label(
                "Windows tells ORBIT when a shortcut is already registered, so a conflict is "
                "reported here instead of failing silently."
            )
        )
        body.addWidget(card)

    def _build_dismiss(self, body: QVBoxLayout) -> None:
        settings = self.working.settings
        card = Card()
        card.add(section_label("Menu behaviour"))
        self._close_after = QCheckBox("Close the menu after an action runs")
        self._close_after.setChecked(settings.close_after_execution)
        self._close_after.toggled.connect(lambda value: self._set("close_after_execution", value))
        card.add(self._close_after)

        self._esc = QCheckBox("Escape closes the menu")
        self._esc.setChecked(settings.dismiss_on_escape)
        self._esc.toggled.connect(lambda value: self._set("dismiss_on_escape", value))
        card.add(self._esc)

        self._outside = QCheckBox("Clicking outside the menu closes it")
        self._outside.setChecked(settings.dismiss_on_click_outside)
        self._outside.toggled.connect(lambda value: self._set("dismiss_on_click_outside", value))
        card.add(self._outside)

        self._auto_hide = small_spin(settings.auto_hide_seconds, minimum=0, maximum=600)
        self._auto_hide.setSuffix(" s")
        self._auto_hide.valueChanged.connect(lambda value: self._set("auto_hide_seconds", int(value)))
        card.add_row(
            Row(
                "Close automatically after",
                self._auto_hide,
                help_text="0 means the menu stays open until you dismiss it.",
            )
        )
        self._confirm = QCheckBox("Ask before running actions marked as needing confirmation")
        self._confirm.setChecked(settings.confirm_destructive)
        self._confirm.toggled.connect(lambda value: self._set("confirm_destructive", value))
        card.add(self._confirm)
        body.addWidget(card)

    def _build_integration(self, body: QVBoxLayout) -> None:
        settings = self.working.settings
        card = Card()
        card.add(section_label("Windows integration"))
        self._startup = QCheckBox("Start ORBIT when I sign in")
        self._startup.setChecked(settings.start_on_login)
        self._startup.toggled.connect(self._on_startup)
        card.add(self._startup)
        card.add(muted_label(describe_support()))

        self._tray = QCheckBox("Show the tray icon")
        self._tray.setChecked(settings.show_tray_icon)
        self._tray.toggled.connect(lambda value: self._set("show_tray_icon", value))
        card.add(self._tray)

        self._toasts = QCheckBox("Show notifications for finished actions")
        self._toasts.setChecked(settings.show_toasts)
        self._toasts.toggled.connect(lambda value: self._set("show_toasts", value))
        card.add(self._toasts)

        self._pulse = QCheckBox("Flash the tray icon when an action fails")
        self._pulse.setChecked(settings.feedback_pulse)
        self._pulse.toggled.connect(lambda value: self._set("feedback_pulse", value))
        card.add(self._pulse)

        self._sound = QCheckBox("Play a short system sound on failure")
        self._sound.setChecked(settings.feedback_sound)
        self._sound.toggled.connect(lambda value: self._set("feedback_sound", value))
        card.add(self._sound)
        body.addWidget(card)

    def _build_monitor(self, body: QVBoxLayout) -> None:
        card = Card()
        card.add(section_label("Display"))
        self._monitor_combo = combo(self._monitor_options(), self.working.settings.monitor,
                                    on_change=self._on_monitor)
        card.add_row(Row("Show the menu on", self._monitor_combo))
        card.add(
            muted_label(
                "The overlay covers one monitor only, so the rest of your desktop stays usable. "
                "It rescales automatically when screen resolution or DPI changes."
            )
        )
        body.addWidget(card)

    def _monitor_options(self) -> list[tuple[str, str]]:
        options: list[tuple[str, str]] = [("cursor", "Follow the cursor"), ("primary", "Primary monitor")]
        for index, screen in enumerate(QGuiApplication.screens()):
            geometry = screen.geometry()
            label = f"Monitor {index + 1}: {screen.name()} ({geometry.width()}×{geometry.height()})"
            options.append((f"index:{index}", label))
        return options

    # --------------------------------------------------------------------- load
    def load(self) -> None:
        settings = self.working.settings
        self._hotkey.set_value(settings.hotkey)
        self._fallback.set_value(settings.fallback_hotkey)
        self._close_after.setChecked(settings.close_after_execution)
        self._esc.setChecked(settings.dismiss_on_escape)
        self._outside.setChecked(settings.dismiss_on_click_outside)
        self._auto_hide.setValue(int(settings.auto_hide_seconds))
        self._confirm.setChecked(settings.confirm_destructive)
        self._startup.setChecked(settings.start_on_login)
        self._tray.setChecked(settings.show_tray_icon)
        self._toasts.setChecked(settings.show_toasts)
        self._pulse.setChecked(settings.feedback_pulse)
        self._sound.setChecked(settings.feedback_sound)
        self._monitor_combo.blockSignals(True)
        self._monitor_combo.clear()
        for value, label in self._monitor_options():
            self._monitor_combo.addItem(label, value)
        index = self._monitor_combo.findData(settings.monitor)
        self._monitor_combo.setCurrentIndex(index if index >= 0 else 0)
        self._monitor_combo.blockSignals(False)
        self._hotkey_status.setText(self._shortcut_status_text())

    def _shortcut_status_text(self) -> str:
        state = self.controller.shortcut.state
        if state.ok:
            return f"Currently registered: {state.display}"
        if state.message:
            return state.message
        return "No shortcut is registered at the moment."

    # ------------------------------------------------------------------ handlers
    def _set(self, key: str, value) -> None:
        setattr(self.working.settings, key, value)
        self.notify_changed()

    def _on_hotkey_captured(self, value: str) -> None:
        problem = validate_hotkey(value)
        if problem:
            self._hotkey_status.setText(problem)
            self._hotkey_status.setStyleSheet(f"color: {self.controller.theme.warning};")
            return
        self.working.settings.hotkey = value
        self.notify_changed()
        self._apply_hotkey()

    def _on_fallback_captured(self, value: str) -> None:
        self.working.settings.fallback_hotkey = value
        self.notify_changed()

    def _apply_hotkey(self) -> None:
        value = self._hotkey.value().strip()
        problem = validate_hotkey(value)
        if problem:
            self._hotkey_status.setText(problem)
            self._hotkey_status.setStyleSheet(f"color: {self.controller.theme.danger};")
            return
        state = self.controller.apply_hotkey(value)
        self.working.settings.hotkey = state.hotkey or value
        self._hotkey_status.setText(state.message)
        colour = self.controller.theme.success if state.ok else self.controller.theme.danger
        self._hotkey_status.setStyleSheet(f"color: {colour};")
        self.studio.set_status(state.message.replace("\n", " "))
        self.notify_changed()

    def _on_startup(self, checked: bool) -> None:
        ok, message = set_startup_enabled(checked)
        self.working.settings.start_on_login = checked if ok else not checked
        if not ok:
            self._startup.blockSignals(True)
            self._startup.setChecked(not checked)
            self._startup.blockSignals(False)
        self.studio.set_status(message.replace("\n", " "))
        self.notify_changed()

    def _on_monitor(self, value: str) -> None:
        if not value:
            return
        self.working.settings.monitor = value
        if value.startswith("index:"):
            try:
                self.working.settings.monitor_index = int(value.split(":", 1)[1])
            except ValueError:
                pass
        self.studio.set_status("Display preference saved. The overlay follows it the next time it opens.")
        self.notify_changed()
