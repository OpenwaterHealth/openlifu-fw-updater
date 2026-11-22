# SPDX-License-Identifier: Apache-2.0
"""The window's table, from the SDK's plan. Pure: no Qt, no USB.

One row per board (the console, the transmitter master, each further
transmitter module), then one per preset store (the console page, then each
transmitter module, aggregated over its slots). Each row says what the unit
has, what the release has, and what the update will do to it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

#: A board whose application is below this version still runs the 1.0.x
#: bootloader, which the update replaces (with the bootloader updater) on the
#: way to the release. That cannot be undone.
BOOTLOADER_REPLACED_BELOW = {"console": (1, 3, 0), "transmitter": (2, 1, 0)}

#: The firmware-lock.json entry that names each board's release tag.
LOCK_KEYS = {"console": "console-fw", "transmitter": "transmitter-fw"}

_VERSION_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")
_SLOT_RE = re.compile(r"transmitter module (\d+) slot \d+$")


class Status(Enum):
    UP_TO_DATE = "Up to date"
    UPDATE = "Update"
    REPLACES_BOOTLOADER = "Update (replaces bootloader)"
    REFUSED = "Refused"


#: Statuses that write to the unit.
PENDING = frozenset({Status.UPDATE, Status.REPLACES_BOOTLOADER})


@dataclass(frozen=True)
class Row:
    key: str
    label: str
    installed: str
    release: str
    status: Status
    #: What an update writes ("writes 13, clears 1"), shown with the status.
    note: str = ""
    #: Why the row is refused (the SDK's words).
    refusal: str = ""

    @property
    def status_text(self) -> str:
        if self.status is Status.UPDATE and self.note:
            return f"Update ({self.note})"
        return self.status.value

    @property
    def pending(self) -> bool:
        return self.status in PENDING


def parse_version(text: str | None) -> tuple[int, int, int] | None:
    """(major, minor, patch) from '2.0.8', 'v2.1.0-rc.7' or '2.1.0+fda'."""
    m = _VERSION_RE.match((text or "").strip())
    return tuple(int(g) for g in m.groups()) if m else None


def replaces_bootloader(board: Any) -> bool:
    """True if updating *board* (an SDK ``Board``) replaces its bootloader:
    its installed FwVersion predates the release's bootloader line."""
    installed = parse_version(board.header.fw_version_str if board.header else board.version)
    return installed is not None and installed < BOOTLOADER_REPLACED_BELOW[board.board]


def firmware_tag(bundle: Any, board: str) -> str:
    """The release's tag for *board*'s firmware (firmware-lock.json), or the
    signed image's version if the lock does not name one."""
    entry = bundle.lock.get("firmware", {}).get(LOCK_KEYS[board], {})
    return entry.get("tag") or bundle.apps[board].version


def presets_release(presets: Any) -> str:
    """'7676767 (research)': the diathermy commit and context of the presets."""
    return f"{presets.commit[:7]} ({presets.context})"


def plan_rows(plan: Any) -> list[Row]:
    """Every row of the table for an SDK ``ReleasePlan``."""
    rows = [_firmware_row(step, plan.bundle) for step in plan.firmware]
    if plan.bundle.presets is not None:
        rows += _preset_rows(plan.presets, plan.bundle.presets, len(plan.boards) - 1)
    return rows


def _firmware_row(step: Any, bundle: Any) -> Row:
    board = step.board
    status = {"none": Status.UP_TO_DATE, "refuse": Status.REFUSED}.get(step.action)
    if status is None:
        status = Status.REPLACES_BOOTLOADER if replaces_bootloader(board) else Status.UPDATE
    return Row(key=board.name, label=board.name.capitalize(), installed=board.version,
               release=firmware_tag(bundle, board.board), status=status,
               refusal=step.detail if status is Status.REFUSED else "")


def _preset_label(module: int | None) -> str:
    if module is None:
        return "Console presets"
    return f"Transmitter presets ({'master' if module == 0 else f'module {module}'})"


def preset_key(module: int | None) -> str:
    return "presets console" if module is None else f"presets module {module}"


def _preset_rows(steps: list[Any], presets: Any, modules: int) -> list[Row]:
    release = presets_release(presets)
    targets = [None, *range(modules)]
    refused = [s for s in steps if s.action == "refuse"]
    if refused:
        # One refusal covers every preset store (clinical presets, or a set
        # for another number of modules).
        return [Row(preset_key(m), _preset_label(m), "-", release, Status.REFUSED,
                    refusal=refused[0].detail) for m in targets]
    return [_preset_row(m, [s for s in steps if s.module == m],
                        1 if m is None else len(presets.transmitter), release)
            for m in targets]


def _preset_row(module: int | None, steps: list[Any], released: int, release: str) -> Row:
    """One preset store's row: how many of the *released* packages it
    holds, and what the update writes and clears there."""
    writes = sum(s.action == "write-presets" for s in steps)
    clears = sum(s.action == "clear-slot" for s in steps)
    note = ", ".join(([f"writes {writes}"] if writes else [])
                     + ([f"clears {clears}"] if clears else []))
    return Row(preset_key(module), _preset_label(module), f"{released - writes} of {released}",
               release, Status.UPDATE if note else Status.UP_TO_DATE, note=note)


def row_key_for_report(line: str) -> str | None:
    """The row an SDK report line is about ('transmitter module 1: updating
    the firmware over I2C', 'transmitter module 0 slot 03: write-presets
    done'), or None."""
    target = line.split(":", 1)[0].strip()
    if target == "console page":
        return preset_key(None)
    m = _SLOT_RE.match(target)
    if m:
        return preset_key(int(m.group(1)))
    if target == "console" or target.startswith("transmitter m"):
        return target
    return None


def refusals(rows: list[Row]) -> str:
    """One line per refused row, for the operator."""
    return "\n".join(f"{r.label}: {r.refusal}" for r in rows if r.status is Status.REFUSED)


def pending(rows: list[Row]) -> list[Row]:
    return [r for r in rows if r.pending]


def release_line(tag: str, bundle: Any) -> str:
    """'v0.2.0-rc8 (research): console 1.3.0-rc.4, transmitter 2.1.0-rc.7,
    presets 7676767'"""
    parts = [f"{board} {firmware_tag(bundle, board)}" for board in LOCK_KEYS]
    if bundle.presets is not None:
        parts.append(f"presets {bundle.presets.commit[:7]}")
    return f"{tag} ({bundle.domain}): " + ", ".join(parts)
