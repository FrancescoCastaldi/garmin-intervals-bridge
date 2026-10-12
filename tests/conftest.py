import logging
import os
from logging.handlers import RotatingFileHandler

import pytest


@pytest.fixture(autouse=True)
def _empty_home(tmp_path_factory, monkeypatch):
    """No test may find a real ~/GarminBridge/.env of the machine it runs on."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("BRIDGE_ENV_FILE", raising=False)
    # no test talks to pypi.org or puts notes in a calendar unless it says so itself
    monkeypatch.setenv("BRIDGE_UPDATE_CHECK", "false")
    monkeypatch.setenv("BRIDGE_INTERVALS_NOTES", "false")
    return home


@pytest.fixture(autouse=True)
def _isolated_process():
    """`main` and `setup` set variables and add a log file handler, as they should in a real process."""
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)
    root = logging.getLogger()
    for handler in [h for h in root.handlers if isinstance(h, RotatingFileHandler)]:
        root.removeHandler(handler)
        handler.close()
