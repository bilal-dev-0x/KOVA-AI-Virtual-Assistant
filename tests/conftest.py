import logging

import pytest

from modules import system_control
from modules.logger import get_logger

# Keep test runs out of the real logs/kova.log
_logger = get_logger()
for _handler in list(_logger.handlers):
    _logger.removeHandler(_handler)
_logger.addHandler(logging.NullHandler())


@pytest.fixture(autouse=True)
def no_start_menu(monkeypatch):
    """Tests must not depend on which apps this PC has installed."""
    monkeypatch.setattr(system_control, "start_menu_apps", lambda: {})
