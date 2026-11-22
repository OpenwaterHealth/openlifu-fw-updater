# SPDX-License-Identifier: Apache-2.0
"""Window front end of the updater (``OpenLIFUUpdater``).

One window: what the unit's console, transmitter modules and preset stores
have, what the release has, and one button. A unit that already runs the
release is reported as up to date; otherwise Update asks once and takes the
unit to the release. The work is the SDK's
(:mod:`openlifu_sdk.io.LIFUReleaseUpdate`), done off the window's thread.

    OpenLIFUUpdater.exe
    OpenLIFUUpdater.exe --release-dir <dir>              another release.json folder
    OpenLIFUUpdater.exe --firmware <zip> [--presets <zip>]  these files instead
    OpenLIFUUpdater.exe --self-test                      build the window, check the bundle, exit
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from collections.abc import Callable
from datetime import datetime
from importlib import metadata, resources
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from openlifu_fw_updater import rows as tbl
from openlifu_fw_updater.release import (
    ReleaseFiles,
    check_domain,
    embedded_release_dir,
    find_release,
)
from openlifu_fw_updater.rows import Status

APP_NAME = "Open-LIFU Updater"
ORGANIZATION = "Openwater"

# How often an idle window looks for a console or transmitter that is missing.
POLL_MS = 2000
# How long closing waits for a read in progress before it gives up on it.
SHUTDOWN_WAIT_MS = 30_000

DO_NOT_POWER_OFF = "Do not power off or unplug the unit."
HARDWARE_IN_USE = ("Another Open-LIFU application is using the unit. Close it and "
                   "Check again.")
IRREVERSIBLE_BOOTLOADER = (
    "The bootloader of {boards} is replaced on the way: {they} run firmware from before "
    "the current bootloader. That cannot be undone.")
CLINICAL_CONVERSION = (
    "The bootloaders of {boards} are replaced with the clinical bootloader and the boards "
    "are locked (RDP1): the unit becomes a clinical unit. Returning it to research needs "
    "SWD recovery (RMA); this updater cannot undo it.")
POWER_WARNING = ("DO NOT POWER OFF OR UNPLUG the unit until the updater says it has "
                 "finished.")

GREEN, AMBER, RED, GREY, TEXT = "#34C759", "#FF9F0A", "#FF453A", "#8E8E93", "#F2F2F7"

_COLOUR = {Status.UP_TO_DATE: GREEN, Status.UPDATE: AMBER,
           Status.REPLACES_BOOTLOADER: AMBER, Status.CONVERT: AMBER,
           Status.WRITE_DFU: AMBER, Status.OTHER: AMBER, Status.REFUSED: RED}

STYLE = f"""
QWidget {{ background: #1C1C1E; color: {TEXT}; font-family: "Segoe UI"; font-size: 10pt; }}
QLabel#banner {{ font-weight: 700; font-size: 11pt; padding: 8px 16px; }}
QLabel#title {{ font-size: 17pt; font-weight: 600; }}
QLabel#subtitle, QLabel#detail {{ color: {GREY}; }}
QLabel#columnHead {{ color: {GREY}; font-size: 9pt; }}
QLabel#warning {{ color: {AMBER}; font-weight: 600; }}
QFrame#rule {{ background: #3A3A3C; max-height: 1px; min-height: 1px; }}
QPushButton {{ background: #3A3A3C; border: none; border-radius: 6px; padding: 8px 18px; }}
QPushButton:hover {{ background: #48484A; }}
QPushButton:disabled {{ background: #2C2C2E; color: #636366; }}
QPushButton#primary {{ background: #E8412B; font-weight: 600; }}
QPushButton#primary:hover {{ background: #F0553F; }}
QPushButton#primary:disabled {{ background: #2C2C2E; color: #636366; }}
QPushButton#link {{ background: transparent; color: {GREY}; padding: 8px 0px; }}
QPushButton#link:hover {{ color: {TEXT}; }}
QProgressBar {{ background: #2C2C2E; border: none; border-radius: 4px;
                max-height: 8px; min-height: 8px; }}
QProgressBar::chunk {{ background: #E8412B; border-radius: 4px; }}
"""

log = logging.getLogger("openlifu.updater")


def version() -> str:
    try:
        return metadata.version("openlifu-fw-updater")
    except metadata.PackageNotFoundError:
        return "unknown"


def asset(name: str) -> str:
    return str(resources.files("openlifu_fw_updater").joinpath("assets", name))


def start_log() -> Path | None:
    """Log the SDK's messages to a file (next to the exe when frozen); they
    would bury the operator's view."""
    name = f"update-{datetime.now():%Y%m%d_%H%M%S}.log"
    folders = [Path(tempfile.gettempdir())]
    if getattr(sys, "frozen", False):
        folders.insert(0, Path(sys.executable).parent)
    for folder in folders:
        try:
            handler = logging.FileHandler(folder / name, encoding="utf-8")
        except OSError:
            continue
        handler.setFormatter(logging.Formatter(
            "%(asctime)s - %(levelname)s - %(name)s - %(message)s"))
        root = logging.getLogger()
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        return folder / name
    return None


def open_interface():
    """The unit's console and transmitter master (research or clinical
    firmware), without the test-mode simulators."""
    from openlifu_sdk.io.LIFUInterface import LIFUInterface

    return LIFUInterface(TX_test_mode=False)


def _missing(tx_ok: bool, hv_ok: bool) -> str:
    if not tx_ok and not hv_ok:
        return ("No unit is connected. Connect the console and the transmitter over USB "
                "and power the unit on.")
    if not hv_ok:
        return "The transmitter is connected, the console is not. Connect the console over USB."
    return ("The console is connected, the transmitter is not. Connect the transmitter over "
            "USB and check that the console powers it.")


class Worker(QObject):
    """Owns the release and the LIFUInterface, and does everything that
    talks to the unit, off the window's thread."""

    fatal = Signal(str)                 # the release cannot be used
    releaseLoaded = Signal(str, str)    # its line for the subtitle, its domain
    searching = Signal()
    blocked = Signal(str)               # cannot read the unit; Check again
    notConnected = Signal(str)          # console and/or transmitter missing
    planReady = Signal(object)          # [rows.Row]
    reportLine = Signal(str)
    progress = Signal(int, int, str)    # written, total, label
    finished = Signal(bool, str, object)  # ok, message, [rows.Row]

    def __init__(self, locate: Callable[[], ReleaseFiles],
                 interface_factory: Callable[[], object] = open_interface):
        super().__init__()
        self._locate = locate
        self._factory = interface_factory
        self._workdir: tempfile.TemporaryDirectory | None = None
        self._bundle = None
        self._interface = None
        self._rows: list[tbl.Row] = []

    @Slot()
    def start(self):
        from openlifu_sdk.io.LIFUReleaseUpdate import ReleaseBundle, ReleaseError

        try:
            files = self._locate()
            self._workdir = tempfile.TemporaryDirectory(prefix="lifu-release-")
            self._bundle = ReleaseBundle.load(files.firmware, files.presets,
                                              workdir=self._workdir.name)
            check_domain(files, self._bundle.domain)
        except (FileNotFoundError, ReleaseError, OSError, ValueError) as exc:
            log.exception("the release cannot be used")
            self.fatal.emit(f"The release cannot be used: {exc}")
            return
        for line in self._bundle.describe():
            log.info("%s", line)
        self.releaseLoaded.emit(tbl.release_line(files.tag, self._bundle), self._bundle.domain)
        self.check()

    def _update(self):
        from openlifu_sdk.io.LIFUReleaseUpdate import ReleaseUpdate

        return ReleaseUpdate(self._bundle, self._interface.hvcontroller,
                             self._interface.txdevice, progress_callback=self.progress.emit,
                             report=self._report)

    def _report(self, line: str):
        log.info("%s", line)
        self.reportLine.emit(line)

    def _reconnect(self) -> tuple[bool, bool]:
        """Open the port of a console or transmitter that came since the last
        look; (tx, hv) connected."""
        tx_ok, hv_ok = self._interface.is_device_connected()
        for ok, device in ((tx_ok, self._interface.txdevice),
                           (hv_ok, self._interface.hvcontroller)):
            if not ok and device is not None:
                device.connect()
        return self._interface.is_device_connected()

    @Slot()
    def check(self):
        from openlifu_sdk.io.exceptions import LIFUHardwareInUseError

        if self._bundle is None:
            return
        self.searching.emit()
        try:
            if self._interface is None:
                self._interface = self._factory()
            tx_ok, hv_ok = self._reconnect()
            if not (tx_ok and hv_ok):
                self.notConnected.emit(_missing(tx_ok, hv_ok))
                return
            plan = self._update().plan()
            for line in plan.describe():
                log.info("plan: %s", line)
            self._rows = tbl.plan_rows(plan)
            self.planReady.emit(self._rows)
        except LIFUHardwareInUseError as exc:
            log.warning("hardware in use: %s", exc)
            self.blocked.emit(HARDWARE_IN_USE)
        except Exception as exc:  # noqa: BLE001 - shown to the operator, logged
            log.exception("check failed")
            self.blocked.emit(f"The unit could not be read: {exc}")

    @Slot()
    def poll(self):
        """Check again once a missing console or transmitter has come."""
        if self._interface is None:
            return
        try:
            if all(self._reconnect()):
                self.check()
        except Exception:  # noqa: BLE001 - the next poll or Check again retries
            log.exception("poll failed")

    @Slot()
    def update(self):
        from openlifu_sdk.io.LIFUReleaseUpdate import ReleaseError

        ok, message = False, ""
        try:
            update = self._update()
            plan = update.plan()
            if tbl.plan_rows(plan) != self._rows:
                # What the operator confirmed is not what is connected now.
                message = "The unit changed since it was read. Nothing was written. Check again."
            else:
                result = update.run(plan)
                for line in result.lines:
                    log.info("read-back: %s", line)
                ok = result.ok
                message = "\n".join(line for line in result.lines if line.startswith("[FAIL]"))
        except ReleaseError as exc:
            log.error("update failed: %s", exc)
            message = str(exc)
        except Exception as exc:  # noqa: BLE001 - shown to the operator, logged
            log.exception("update failed")
            message = f"Unexpected error: {exc}"
        self._rows = self._reread()
        self.finished.emit(ok, message, self._rows)

    def _reread(self) -> list[tbl.Row]:
        """The table after an update: what the unit reports now."""
        try:
            return tbl.plan_rows(self._update().plan())
        except Exception:  # noqa: BLE001 - the update's own message says more
            log.exception("re-reading the unit failed")
            return []

    @Slot()
    def shutdown(self):
        if self._interface is not None:
            try:
                self._interface.close()     # releases the cross-process hardware lock
            except Exception:  # noqa: BLE001 - leaving anyway
                log.exception("closing the interface failed")
            self._interface = None
        if self._workdir is not None:
            self._workdir.cleanup()
            self._workdir = None
        # Quit from here, after the interface is closed: a quit() from the
        # window could end the loop before this slot ran.
        self.thread().quit()


class Window(QWidget):
    requestCheck = Signal()
    requestPoll = Signal()
    requestUpdate = Signal()
    requestShutdown = Signal()

    def __init__(self, locate: Callable[[], ReleaseFiles], log_path: Path | None,
                 interface_factory: Callable[[], object] = open_interface):
        super().__init__()
        self._log_path = log_path
        self._updating = False
        self._can_update = False
        self._rows: list[tbl.Row] = []
        self._row_widgets: list[QLabel] = []
        self._status_cells: dict[str, QLabel] = {}

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(asset("favicon.ico")))
        self.setStyleSheet(STYLE)
        self._build()
        self.setFixedWidth(680)

        self._thread = QThread(self)
        self._worker = Worker(locate, interface_factory)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.start)
        self.requestCheck.connect(self._worker.check)
        self.requestPoll.connect(self._worker.poll)
        self.requestUpdate.connect(self._worker.update)
        self.requestShutdown.connect(self._worker.shutdown)
        self._worker.fatal.connect(self._on_fatal)
        self._worker.releaseLoaded.connect(self._on_release)
        self._worker.searching.connect(self._on_searching)
        self._worker.blocked.connect(self._on_blocked)
        self._worker.notConnected.connect(self._on_not_connected)
        self._worker.planReady.connect(self._on_plan)
        self._worker.reportLine.connect(self._on_report)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)

        self._poll = QTimer(self)
        self._poll.setInterval(POLL_MS)
        self._poll.timeout.connect(self.requestPoll)

    def start(self):
        self._on_searching()
        self._thread.start()

    # ------------------------------------------------------------------ layout

    def _build(self):
        # The banner spans the window edge to edge; the rest keeps its margins.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._banner = QLabel()
        self._banner.setObjectName("banner")
        self._banner.setWordWrap(True)
        self._banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self._banner)
        self._show_banner(None)
        root = QVBoxLayout()
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(14)
        outer.addLayout(root, 1)

        header = QHBoxLayout()
        header.setSpacing(16)
        mark = QLabel()
        mark.setPixmap(QPixmap(asset("favicon.png")).scaled(
            56, 56, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))
        header.addWidget(mark)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel(APP_NAME)
        title.setObjectName("title")
        self._subtitle = QLabel("Release: reading ...")
        self._subtitle.setObjectName("subtitle")
        self._subtitle.setWordWrap(True)
        titles.addWidget(title)
        titles.addWidget(self._subtitle)
        header.addLayout(titles, 1)
        root.addLayout(header)
        root.addWidget(self._rule())

        self._grid = QGridLayout()
        self._grid.setHorizontalSpacing(24)
        self._grid.setVerticalSpacing(10)
        for column, text in enumerate(("Device", "Installed", "This release", "Status")):
            head = QLabel(text)
            head.setObjectName("columnHead")
            self._grid.addWidget(head, 0, column)
        self._grid.setColumnStretch(3, 1)
        root.addLayout(self._grid)
        root.addWidget(self._rule())

        self._message = QLabel()
        self._message.setWordWrap(True)
        self._message.setMinimumHeight(60)
        self._message.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self._message, 1)
        self._warning = QLabel(DO_NOT_POWER_OFF)
        self._warning.setObjectName("warning")
        root.addWidget(self._warning)
        self._detail = QLabel()
        self._detail.setObjectName("detail")
        root.addWidget(self._detail)
        self._bar = QProgressBar()
        self._bar.setTextVisible(False)
        root.addWidget(self._bar)

        buttons = QHBoxLayout()
        self._log_button = QPushButton("Open log")
        self._log_button.setObjectName("link")
        self._log_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._log_button.setVisible(self._log_path is not None)
        self._log_button.clicked.connect(self._open_log)
        self._check_button = QPushButton("Check again")
        self._check_button.clicked.connect(self._check_again)
        self._primary = QPushButton("Close")
        self._primary.setObjectName("primary")
        self._primary.clicked.connect(self._primary_clicked)
        buttons.addWidget(self._log_button)
        buttons.addStretch(1)
        buttons.addWidget(self._check_button)
        buttons.addWidget(self._primary)
        root.addLayout(buttons)

    @staticmethod
    def _rule() -> QFrame:
        rule = QFrame()
        rule.setObjectName("rule")
        return rule

    # ------------------------------------------------------------------ pieces

    def _show_banner(self, domain: str | None):
        banner = tbl.banner(domain)
        self._banner.setText(banner.text)
        self._banner.setStyleSheet(f"background: {banner.background}; "
                                   f"color: {banner.foreground};")
        self._banner.setVisible(banner.visible)
        self.setWindowTitle(f"{APP_NAME} — {banner.title}" if banner.title else APP_NAME)

    def _say(self, text: str, colour: str = TEXT):
        self._message.setStyleSheet(f"color: {colour};")
        self._message.setText(text)

    def _set_status_cell(self, key: str, text: str, colour: str):
        cell = self._status_cells.get(key)
        if cell is not None:
            cell.setText(text)
            cell.setStyleSheet(f"color: {colour};")

    def _fill(self, rows: list[tbl.Row]):
        """Replace the table's rows; the unit decides how many there are."""
        for widget in self._row_widgets:
            self._grid.removeWidget(widget)
            widget.deleteLater()
        self._row_widgets, self._status_cells, self._rows = [], {}, rows
        for line, row in enumerate(rows, start=1):
            cells = [QLabel(row.label), QLabel(row.installed or "-"), QLabel(row.release),
                     QLabel()]
            for column, cell in enumerate(cells):
                self._grid.addWidget(cell, line, column)
            self._row_widgets += cells
            self._status_cells[row.key] = cells[3]
            self._set_status_cell(row.key, row.status_text, _COLOUR[row.status])
        self.adjustSize()

    def _idle(self, can_update: bool, can_check: bool = True, poll: bool = False):
        """Buttons and progress for a window that is waiting for the operator."""
        self._updating = False
        self._can_update = can_update
        self._bar.setVisible(False)
        self._warning.setVisible(False)
        self._detail.setText("")
        self._check_button.setEnabled(can_check)
        self._primary.setEnabled(True)
        self._primary.setText("Update" if can_update else "Close")
        if poll:
            self._poll.start()
        else:
            self._poll.stop()

    def _busy(self, warning: bool):
        self._poll.stop()
        self._bar.setVisible(True)
        self._bar.setRange(0, 0)
        self._warning.setVisible(warning)
        self._check_button.setEnabled(False)
        self._primary.setEnabled(False)

    # ------------------------------------------------------------------ worker

    def _on_release(self, line: str, domain: str):
        self._subtitle.setText(f"Release {line}")
        self._show_banner(domain)

    def _on_fatal(self, text: str):
        self._subtitle.setText("Release: none")
        self._show_banner(None)
        self._say(text, RED)
        self._idle(can_update=False, can_check=False)

    def _on_searching(self):
        if self._updating:
            return
        self._say("Reading the unit ...")
        self._busy(warning=False)

    def _on_blocked(self, text: str):
        self._say(text, RED)
        self._idle(can_update=False)

    def _on_not_connected(self, text: str):
        self._fill([])
        self._say(text)
        self._idle(can_update=False, poll=True)

    def _on_plan(self, rows: list[tbl.Row]):
        self._fill(rows)
        refused = tbl.refusals(rows)
        todo = tbl.pending(rows)
        if refused:
            self._say("This release cannot be installed on this unit. Nothing will be "
                      f"written.\n{refused}", RED)
        elif not todo:
            self._say("The unit already runs this release.", GREEN)
        else:
            self._say("An update is available for: "
                      + ", ".join(r.label.lower() for r in todo) + ".")
        self._idle(can_update=bool(todo) and not refused)

    def _on_report(self, line: str):
        self._detail.setText(line)
        key = tbl.row_key_for_report(line)
        if key is not None and not line.endswith(" done"):
            self._set_status_cell(key, "Updating ...", AMBER)
        if "FAILED" in line or "power-cycling" in line or "turning on 12 V" in line:
            self._say(line, AMBER)
        self._bar.setRange(0, 0)

    def _on_progress(self, written: int, total: int, label: str):
        if total <= 0:
            self._bar.setRange(0, 0)
            return
        percent = min(100, written * 100 // total)
        self._bar.setRange(0, 100)
        self._bar.setValue(percent)
        self._detail.setText(f"{label} {percent}%")

    def _on_finished(self, ok: bool, message: str, rows: list[tbl.Row]):
        self._fill(rows)
        if ok:
            self._say("Update finished. The unit runs this release; everything read back "
                      "matches it.", GREEN)
        else:
            self._say(f"The update did not finish.\n{message}", RED)
        self._idle(can_update=False)

    # ---------------------------------------------------------------- operator

    def _check_again(self):
        self._on_searching()
        self.requestCheck.emit()

    def _confirm(self) -> bool:
        todo = tbl.pending(self._rows)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(APP_NAME)
        converted = [r.label.lower() for r in tbl.converts(todo)]
        box.setText("Convert the unit to clinical now?" if converted else "Update the unit now?")
        parts = ["Updates: " + ", ".join(r.label.lower() for r in todo) + "."]
        if converted:
            parts.append(CLINICAL_CONVERSION.format(boards=", ".join(converted)))
        replaced = [r.label.lower() for r in todo if r.status is Status.REPLACES_BOOTLOADER]
        if replaced:
            parts.append(IRREVERSIBLE_BOOTLOADER.format(
                boards=", ".join(replaced), they="it" if len(replaced) == 1 else "they"))
        parts.append(POWER_WARNING)
        box.setInformativeText("\n\n".join(parts))
        update = box.addButton("Update", QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        return box.clickedButton() is update

    def _primary_clicked(self):
        if not self._can_update:
            self.close()
            return
        if not self._confirm():
            return
        self._updating = True
        self._say("Updating the unit ...")
        self._busy(warning=True)
        self.requestUpdate.emit()

    def _open_log(self):
        if self._log_path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._log_path)))

    def closeEvent(self, event):
        if self._updating:
            QMessageBox.warning(
                self, APP_NAME,
                "An update is running. Closing now could leave a board without working "
                "firmware.\n\nWait until the updater says it has finished.")
            event.ignore()
            return
        self._poll.stop()
        # The worker may still be reading the unit; do not leave a dead window up.
        self.hide()
        if self._thread.isRunning():
            self.requestShutdown.emit()
            if not self._thread.wait(SHUTDOWN_WAIT_MS):
                log.error("the worker did not stop; leaving without it")
        event.accept()


def parse_cli(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="OpenLIFUUpdater", description=APP_NAME)
    parser.add_argument("--release-dir", type=Path, default=None,
                        help="Use the release in this folder (release.json) instead of the "
                             "one built in.")
    parser.add_argument("--firmware", type=Path,
                        help="firmware-<tag>.zip (or its folder) instead of the built-in release")
    parser.add_argument("--presets", type=Path,
                        help="<context>-presets-<tag>.zip (or its folder); needs --firmware")
    parser.add_argument("--self-test", action="store_true",
                        help="Build the window, check the bundle, and exit: 0 if good. "
                             "Does not open the unit.")
    args = parser.parse_args(argv)
    if args.presets and not args.firmware:
        parser.error("--presets needs --firmware")
    if args.firmware and args.release_dir:
        parser.error("--firmware and --release-dir exclude each other")
    return args


def release_locator(args: argparse.Namespace) -> Callable[[], ReleaseFiles]:
    if args.firmware:
        files = ReleaseFiles(tag="(from the command line)", firmware=args.firmware,
                             presets=args.presets)
        return lambda: files
    folder = args.release_dir or embedded_release_dir()
    return lambda: find_release(folder)


def self_test(window: Window, locate: Callable[[], ReleaseFiles]) -> int:
    """What a broken build gets wrong: a missing icon, release, trusted key,
    SDK module or libusb. Opens nothing on USB."""
    from openlifu_sdk.io import LIFUFirmwareUpdate, LIFUInterface  # noqa: F401 - bundled?
    from openlifu_sdk.io.LIFUDFU import STM32USBDFU
    from openlifu_sdk.io.LIFUReleaseUpdate import ReleaseBundle

    try:
        if window.windowIcon().isNull() or QPixmap(asset("favicon.png")).isNull():
            raise RuntimeError("the Open-LIFU icon is missing from the bundle")
        files = locate()
        with tempfile.TemporaryDirectory(prefix="lifu-release-") as work:
            bundle = ReleaseBundle.load(files.firmware, files.presets, workdir=work)
        check_domain(files, bundle.domain)
        if STM32USBDFU()._get_backend() is None:   # loads libusb, enumerates nothing
            raise RuntimeError("libusb cannot be loaded: USB DFU would not work")
    except Exception as exc:  # noqa: BLE001 - the self-test reports every failure
        log.exception("self-test failed")
        print(f"self-test FAILED: {exc}", flush=True)
        return 1
    line = tbl.release_line(files.tag, bundle)
    log.info("self-test passed: %s", line)
    print(f"self-test passed: {line}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_cli(argv)
    log_path = start_log()
    try:
        sdk = metadata.version("openlifu-sdk")
    except metadata.PackageNotFoundError:
        sdk = "unknown"
    log.info("%s %s (SDK %s)", APP_NAME, version(), sdk)

    if sys.platform == "win32":
        # Without its own ID the taskbar shows the Python or bootloader icon.
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            f"{ORGANIZATION}.OpenLIFUUpdater")

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setOrganizationName(ORGANIZATION)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(version())
    app.setWindowIcon(QIcon(asset("favicon.ico")))

    locate = release_locator(args)
    window = Window(locate, log_path)
    if args.self_test:
        return self_test(window, locate)
    window.show()
    window.start()
    code = app.exec()
    # pyusb's finalizers can unref devices after libusb is gone when the
    # interpreter tears down. The interface is closed and everything logged by
    # now, so leave without running them.
    logging.shutdown()
    os._exit(code)


if __name__ == "__main__":
    sys.exit(main())
