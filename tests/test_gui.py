# SPDX-License-Identifier: Apache-2.0
"""The window (gui.py), headless, against a fake unit: what it shows in
each state and what the operator can do there."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QMessageBox  # noqa: E402  # reason: after importorskip
from release_fakes import (  # noqa: E402  # reason: after importorskip
    OLD_COMMIT,
    FakeInterface,
    FakeUpdaters,
    unit,
)

from openlifu_fw_updater import gui  # noqa: E402  # reason: after importorskip
from openlifu_fw_updater.release import ReleaseFiles  # noqa: E402  # reason: after importorskip

WAIT_MS = 20_000


@pytest.fixture
def make_window(qtbot, release_dirs):
    """A started window on the test release whose unit is *interface*
    (or what the factory raises)."""
    windows = []

    def make(factory, files=None):
        fw, presets = release_dirs
        files = files or ReleaseFiles(tag="vTest", firmware=fw, presets=presets)
        window = gui.Window(lambda: files, None, interface_factory=factory)
        qtbot.addWidget(window)
        window.start()
        windows.append(window)
        return window

    yield make
    for window in windows:
        window._updating = False
        window.close()


def _status(window):
    return {key: cell.text() for key, cell in window._status_cells.items()}


def _idle(window):
    return window._primary.isEnabled() and not window._bar.isVisible()


def test_a_unit_on_the_release_is_up_to_date(qtbot, make_window, release, keys):
    interface = FakeInterface(*unit(keys, release))
    window = make_window(lambda: interface)
    qtbot.waitUntil(lambda: _idle(window) and bool(window._status_cells), timeout=WAIT_MS)
    assert "vTest (research)" in window._subtitle.text()
    assert set(_status(window).values()) == {"Up to date"}
    assert window._message.text() == "The unit already runs this release."
    assert window._primary.text() == "Close"
    assert not window._poll.isActive()


def test_an_update_is_confirmed_once_and_ends_up_to_date(qtbot, make_window, release, keys,
                                                         monkeypatch):
    from openlifu_sdk.io import LIFUFirmwareUpdate

    hv, tx = unit(keys, release, commit=OLD_COMMIT, tx_version="2.0.8", hv_body=b"hv-rc7")
    fakes = FakeUpdaters(release, hv, tx)
    monkeypatch.setattr(LIFUFirmwareUpdate, "LIFUFirmwareUpdate", fakes.Console)
    monkeypatch.setattr(LIFUFirmwareUpdate, "LIFUTransmitterFirmwareUpdate", fakes.Transmitter)
    interface = FakeInterface(hv, tx)
    window = make_window(lambda: interface)
    qtbot.waitUntil(lambda: _idle(window) and window._primary.text() == "Update",
                    timeout=WAIT_MS)
    status = _status(window)
    assert status["console"] == "Update"
    assert status["transmitter master"] == "Update (replaces bootloader)"
    assert status["presets module 1"] == "Update (writes 3)"
    assert window._message.text().startswith("An update is available for: console, ")

    asked = []

    def confirm(box):
        asked.append((box.text(), box.informativeText(), box.defaultButton().text()))
        return next(b for b in box.buttons() if b.text() == "Update")

    monkeypatch.setattr(QMessageBox, "exec", lambda box: None)
    monkeypatch.setattr(QMessageBox, "clickedButton", confirm)
    window._primary.click()

    assert len(asked) == 1
    text, informative, default = asked[0]
    assert text == "Update the unit now?" and default == "Cancel"
    assert "bootloader of transmitter master, transmitter module 1 is replaced" in informative
    assert "cannot be undone" in informative and "DO NOT POWER OFF" in informative

    qtbot.waitUntil(lambda: _idle(window) and not window._updating, timeout=WAIT_MS)
    assert fakes.order == ["console", "module 1", "master"]
    assert set(_status(window).values()) == {"Up to date"}
    assert window._message.text().startswith("Update finished.")
    assert window._primary.text() == "Close"


def test_cancel_writes_nothing(qtbot, make_window, release, keys, monkeypatch):
    hv, tx = unit(keys, release, commit=OLD_COMMIT)
    window = make_window(lambda: FakeInterface(hv, tx))
    qtbot.waitUntil(lambda: _idle(window) and window._primary.text() == "Update",
                    timeout=WAIT_MS)
    monkeypatch.setattr(QMessageBox, "exec", lambda box: None)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda box: box.defaultButton())
    window._primary.click()
    assert not window._updating and hv.log == [] and tx.log == []


def test_a_refusal_is_red_and_offers_no_update(qtbot, make_window, release, keys):
    interface = FakeInterface(*unit(keys, release, tx_version="2.1.1", commit=OLD_COMMIT))
    window = make_window(lambda: interface)
    qtbot.waitUntil(lambda: _idle(window) and bool(window._status_cells), timeout=WAIT_MS)
    cell = window._status_cells["transmitter master"]
    assert cell.text() == "Refused" and gui.RED in cell.styleSheet()
    assert "downgrade" in window._message.text()
    assert gui.RED in window._message.styleSheet()
    assert window._primary.text() == "Close"


def test_hardware_in_use_says_so_and_waits_for_check_again(qtbot, make_window, release, keys):
    from openlifu_sdk.io.exceptions import LIFUHardwareInUseError

    interface = FakeInterface(*unit(keys, release))
    calls = []

    def factory():
        calls.append(1)
        if len(calls) == 1:
            raise LIFUHardwareInUseError(pid=1234)
        return interface

    window = make_window(factory)
    qtbot.waitUntil(lambda: _idle(window) and bool(window._message.text()), timeout=WAIT_MS)
    assert window._message.text() == gui.HARDWARE_IN_USE
    assert not window._poll.isActive() and window._check_button.isEnabled()

    window._check_button.click()
    qtbot.waitUntil(lambda: _idle(window) and bool(window._status_cells), timeout=WAIT_MS)
    assert window._message.text() == "The unit already runs this release."


def test_a_missing_transmitter_is_named_and_polled_for(qtbot, make_window, release, keys):
    hv, tx = unit(keys, release)
    tx.connected = False
    window = make_window(lambda: FakeInterface(hv, tx))
    qtbot.waitUntil(lambda: _idle(window) and window._message.text().startswith("The console"),
                    timeout=WAIT_MS)
    assert "transmitter is not" in window._message.text()
    assert window._poll.isActive()

    tx.connected = True                     # plugged in: the next poll finds it
    qtbot.waitUntil(lambda: window._message.text() == "The unit already runs this release.",
                    timeout=WAIT_MS)
    assert not window._poll.isActive()


def test_nothing_connected(qtbot, make_window, release, keys):
    hv, tx = unit(keys, release)
    hv.connected = tx.connected = False
    window = make_window(lambda: FakeInterface(hv, tx))
    qtbot.waitUntil(lambda: _idle(window) and window._message.text().startswith("No unit"),
                    timeout=WAIT_MS)
    assert window._poll.isActive()


def test_a_bad_release_is_fatal(qtbot, make_window, tmp_path):
    files = ReleaseFiles(tag="vBad", firmware=tmp_path / "missing.zip", presets=None)
    window = make_window(lambda: pytest.fail("the unit must not be opened"), files)
    qtbot.waitUntil(lambda: window._message.text().startswith("The release cannot be used"),
                    timeout=WAIT_MS)
    assert not window._check_button.isEnabled() and window._primary.text() == "Close"


def test_closing_is_refused_while_updating(qtbot, make_window, release, keys, monkeypatch):
    interface = FakeInterface(*unit(keys, release))
    window = make_window(lambda: interface)
    qtbot.waitUntil(lambda: _idle(window) and bool(window._status_cells), timeout=WAIT_MS)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warned.append(a[2]))
    window._updating = True
    assert not window.close()
    assert "An update is running" in warned[0]
    window._updating = False
    assert window.close()
    assert interface.closed             # the hardware lock is released


def test_self_test_loads_the_release_without_the_unit(qtbot, release_dirs, capsys):
    fw, presets = release_dirs
    files = ReleaseFiles(tag="vTest", firmware=fw, presets=presets)
    window = gui.Window(lambda: files, None,
                        interface_factory=lambda: pytest.fail("the unit must not be opened"))
    qtbot.addWidget(window)
    assert gui.self_test(window, lambda: files) == 0
    assert "self-test passed: vTest (research)" in capsys.readouterr().out


def test_self_test_fails_without_a_release(qtbot, tmp_path, capsys):
    from openlifu_fw_updater.release import find_release

    window = gui.Window(lambda: None, None)
    qtbot.addWidget(window)
    assert gui.self_test(window, lambda: find_release(tmp_path)) == 1
    assert "self-test FAILED: no release" in capsys.readouterr().out


def test_the_command_line(tmp_path):
    args = gui.parse_cli(["--firmware", str(tmp_path / "fw.zip")])
    assert gui.release_locator(args)().firmware.name == "fw.zip"
    with pytest.raises(SystemExit):
        gui.parse_cli(["--presets", "p.zip"])
