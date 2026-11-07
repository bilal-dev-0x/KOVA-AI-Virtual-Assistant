"""Wire the dashboard to the existing KOVA agent and voice pipeline."""

from modules.logger import get_logger
from modules.result import Result
from ui.bridge import Bridge
from ui.controller import Controller
from ui.hotkey import DEFAULT_HOTKEY, Hotkey
from ui.tray import Tray

log = get_logger()


def run_ui(agent, dashboard_factory=None, tray_factory=Tray, hotkey_factory=Hotkey,
           listen=None, speak=None, is_yes=None, voice_factory=None):
    """Run the dashboard around `agent`, returning its stop/restart Result.

    Factories are injectable so lifecycle wiring can be tested without opening
    a real window, installing a keyboard hook, or showing a tray icon.
    """
    from modules import alerts, reminders, voice_handler

    voice_handler.configure(agent.config)
    agent.min_ai_words = 3
    listen = listen or _listen_once
    speak = speak or voice_handler.speak

    bridge = Bridge()
    controller = Controller(agent, bridge, listen=listen)
    controller.attach()
    controller.speak = speak

    # Voice mode runs all the time beside the window, like `python main.py`
    voice = None
    if voice_factory is None and is_yes is not None:
        voice_factory = _voice_loop
    if voice_factory is not None:
        voice = voice_factory(agent, controller, speak)
        controller.voice_prompt = lambda kind, question: voice.prompt(kind, question, is_yes)
        controller.on_sleep = voice.went_to_sleep
    hotkey = hotkey_factory(voice.wake if voice else controller.push_to_talk)
    tray = None
    dashboard = None
    result = Result(True, "KOVA dashboard closed.", stop=True)
    old_alert, old_reminder = alerts.on_alert, reminders.on_fire

    def show_notice(message):
        if dashboard is not None:
            bridge.post(dashboard.show_notice, message)

    def quit_app(value=None):
        nonlocal result
        if value is not None:
            result = value
        if dashboard is not None:
            dashboard.destroy()

    try:
        if dashboard_factory is None:
            from ui.app import Dashboard
            dashboard_factory = Dashboard

        dashboard = dashboard_factory(
            controller, agent, speak=speak,
            on_hide=lambda: dashboard.withdraw(),
            on_quit=lambda: quit_app(),
            on_hotkey=hotkey.start,
        )
        dashboard.on_exit = quit_app
        controller.prompt = dashboard.prompt
        visible = {"value": True}
        dashboard.bind("<Map>", lambda event: visible.update(value=True), add="+")
        dashboard.bind("<Unmap>", lambda event: visible.update(value=False), add="+")

        def ui_call(callback, *args):
            bridge.post(callback, *args)

        tray = tray_factory(
            on_show=lambda: ui_call(dashboard.show),
            on_hide=lambda: ui_call(dashboard.withdraw),
            on_mute=lambda: ui_call(dashboard._toggle_mute),
            on_quit=lambda: bridge.post(quit_app),
            is_visible=lambda: visible["value"],
            is_muted=lambda: controller.muted,
        )
        tray_available = tray.start()
        if not tray_available:
            dashboard.on_hide = lambda: quit_app()

        hotkey.start(agent.config.get("PTT_HOTKEY", DEFAULT_HOTKEY))
        alerts.on_alert = lambda message: show_notice(f"Heads up, {agent.title}. {message}")
        reminders.on_fire = lambda message: show_notice(f"Reminder, {agent.title}: {message}")
        if agent.config.get("ALERTS_ON"):
            alerts.start(agent.config)

        if voice is not None:
            voice.start()
        dashboard.mainloop()
    finally:
        if voice is not None:
            voice.stop()
        controller.stop()
        hotkey.stop()
        if tray is not None:
            tray.stop()
        alerts.stop()
        alerts.on_alert = old_alert
        reminders.on_fire = old_reminder

    log.info("KOVA dashboard stopped")
    return result


def _voice_loop(agent, controller, speak):
    from modules import voice_handler
    from ui.voice import VoiceLoop

    def listen(wait_seconds=5, max_seconds=10):
        heard = voice_handler.listen_text(wait_seconds=wait_seconds, max_seconds=max_seconds)
        if heard:
            log.info("Heard: %s (sure %.2f)", heard, voice_handler.last_confidence)
        return heard

    return VoiceLoop(agent, controller, listen, speak, unsure=voice_handler.unsure,
                     finish_cut_off=voice_handler.finish_cut_off)


def _listen_once():
    """One mic-button recording, with the same confidence and cut-off checks as voice mode."""
    from modules import voice_handler

    heard = voice_handler.listen_text(wait_seconds=5, max_seconds=10)
    if not heard:
        return None
    log.info("Heard: %s (sure %.2f)", heard, voice_handler.last_confidence)
    if voice_handler.unsure():
        log.info("Not acted on, speech recognition was unsure: %s", heard)
        return None
    return voice_handler.finish_cut_off(
        heard,
        lambda wait_seconds=3, max_seconds=8: voice_handler.listen_text(
            wait_seconds=wait_seconds, max_seconds=max_seconds),
    )
