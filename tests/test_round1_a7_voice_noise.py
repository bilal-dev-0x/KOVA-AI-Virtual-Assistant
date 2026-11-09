import logging

import pytest

from desktop_agent import Agent
from ui.voice import VoiceLoop


class FakeController:
    def __init__(self):
        self.voice_state = "awake"
        self.muted = False
        self.busy = False
        self.submitted = []

    def submit(self, text, source="text", done=None):
        self.submitted.append((text, source))
        if done:
            done.set()
        return True


@pytest.mark.parametrize("fragment", ["camera", "cricket", "there is"])
def test_awake_voice_ignores_unmatched_noise_without_submitting(fragment, caplog):
    agent = Agent({"AGENT_TRACE": False, "AWAKE_TIMEOUT": 0})
    controller = FakeController()
    loop = VoiceLoop(agent, controller, listen=lambda **kwargs: fragment, speak=lambda line: None,
                     clock=lambda: 50)
    loop.awake = True
    loop.last_active = 50

    with caplog.at_level(logging.INFO):
        result = loop.step()

    assert result is None
    assert controller.submitted == []
    assert "Ignored:" in caplog.text


def test_real_one_word_command_still_reaches_controller():
    agent = Agent({"AGENT_TRACE": False, "AWAKE_TIMEOUT": 0})
    controller = FakeController()
    loop = VoiceLoop(agent, controller, listen=lambda **kwargs: "pause", speak=lambda line: None,
                     clock=lambda: 50)
    loop.awake = True
    loop.last_active = 50

    assert loop.step() == "pause"
    assert controller.submitted == [("pause", "voice")]


def test_voice_noise_classifier_marks_unknown_single_word():
    assert Agent({"AGENT_TRACE": False}).ignore_awake_noise("camera")
