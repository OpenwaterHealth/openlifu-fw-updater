# SPDX-License-Identifier: Apache-2.0
"""The window's table from the SDK's plan (rows.py): no Qt."""

from __future__ import annotations

from openlifu_sdk.io import LIFUReleaseUpdate as ru
from release_fakes import COMMIT, OLD_COMMIT, RELEASED, preset_package, unit

from openlifu_fw_updater import rows
from openlifu_fw_updater.rows import Status


def _table(release, hv, tx):
    return {r.key: r for r in rows.plan_rows(ru.ReleaseUpdate(release, hv, tx).plan())}


def test_a_unit_on_the_release_is_up_to_date(release, keys):
    table = _table(release, *unit(keys, release))
    assert list(table) == ["console", "transmitter master", "transmitter module 1",
                           "presets console", "presets module 0", "presets module 1"]
    assert {r.status for r in table.values()} == {Status.UP_TO_DATE}
    assert [r.label for r in table.values()] == [
        "Console", "Transmitter master", "Transmitter module 1", "Console presets",
        "Transmitter presets (master)", "Transmitter presets (module 1)"]
    assert table["presets module 1"].installed == f"{RELEASED} of {RELEASED}"
    assert rows.pending(list(table.values())) == []


def test_this_release_is_the_lock_tag_and_the_presets_commit(release, keys):
    table = _table(release, *unit(keys, release))
    assert table["console"].release == "1.3.0-rc.4"
    assert table["transmitter module 1"].release == "2.1.0-rc.7"
    assert table["presets console"].release == f"{COMMIT[:7]} (research)"


def test_another_build_of_the_same_version_is_an_update(release, keys):
    table = _table(release, *unit(keys, release, tx_body=b"tx-rc7"))
    assert table["console"].status is Status.UP_TO_DATE
    assert table["transmitter master"].status is Status.UPDATE
    assert table["transmitter master"].status_text == "Update"


def test_a_board_on_the_old_bootloader_line_replaces_it(release, keys):
    table = _table(release, *unit(keys, release, tx_version="2.0.8", hv_version="1.2.6"))
    for key in ("console", "transmitter master", "transmitter module 1"):
        assert table[key].status is Status.REPLACES_BOOTLOADER
        assert table[key].status_text == "Update (replaces bootloader)"
    assert table["transmitter master"].installed == "2.0.8"


def test_preset_rows_aggregate_their_slots(release, keys):
    hv, tx = unit(keys, release, commit=OLD_COMMIT)
    tx.slots[1][13] = preset_package(keys, "transmitter", body=b"stray")
    table = _table(release, hv, tx)
    assert table["presets console"].installed == "0 of 1"
    assert table["presets console"].status_text == "Update (writes 1)"
    assert table["presets module 0"].status_text == f"Update (writes {RELEASED})"
    assert table["presets module 1"].status_text == f"Update (writes {RELEASED}, clears 1)"
    assert table["presets module 1"].installed == f"0 of {RELEASED}"


def test_only_a_stray_slot_is_a_clear(release, keys):
    hv, tx = unit(keys, release)
    tx.slots[0][14] = preset_package(keys, "transmitter", body=b"stray")
    table = _table(release, hv, tx)
    assert table["presets module 0"].installed == f"{RELEASED} of {RELEASED}"
    assert table["presets module 0"].status_text == "Update (clears 1)"


def test_a_newer_board_is_refused_with_the_sdks_reason(release, keys):
    table = list(_table(release, *unit(keys, release, tx_version="2.1.1")).values())
    refused = [r for r in table if r.status is Status.REFUSED]
    assert [r.key for r in refused] == ["transmitter master", "transmitter module 1"]
    assert "downgrade" in refused[0].refusal
    assert rows.refusals(table).splitlines()[0].startswith("Transmitter master: runs 2.1.1")


def test_a_clinical_unit_is_refused(release, keys):
    table = _table(release, *unit(keys, release, domain="clinical"))
    assert table["console"].status is Status.REFUSED
    assert "clinical" in table["console"].refusal


def test_a_preset_refusal_covers_every_preset_row(release, keys, monkeypatch):
    plan = ru.ReleaseUpdate(release, *unit(keys, release)).plan()
    plan.presets = [ru.Step("presets", "refuse", "the presets are for 3 modules")]
    table = rows.plan_rows(plan)
    preset_rows = [r for r in table if r.key.startswith("presets")]
    assert len(preset_rows) == 3
    assert all(r.status is Status.REFUSED and "3 modules" in r.refusal for r in preset_rows)


def test_the_bootloader_threshold_is_per_board():
    def board(kind, version):
        return ru.Board(name=kind, board=kind, module=0, version=version, domain="research",
                        header=None)

    assert rows.replaces_bootloader(board("transmitter", "2.0.31"))
    assert not rows.replaces_bootloader(board("transmitter", "v2.1.0-rc.7"))
    assert rows.replaces_bootloader(board("console", "1.2.6"))
    assert not rows.replaces_bootloader(board("console", "1.3.0"))
    assert not rows.replaces_bootloader(board("console", "garbage"))


def test_report_lines_name_their_row():
    assert rows.row_key_for_report("console: updating the firmware") == "console"
    assert rows.row_key_for_report("transmitter module 2: updating the firmware over I2C") \
        == "transmitter module 2"
    assert rows.row_key_for_report("transmitter master: master updated") == "transmitter master"
    assert rows.row_key_for_report("console page: write-presets done") == "presets console"
    assert rows.row_key_for_report("transmitter module 1 slot 13: clear-slot done") \
        == "presets module 1"
    assert rows.row_key_for_report("transmitter: no answer; turning on 12 V") is None


def test_the_release_line(release):
    assert rows.release_line("v9", release) == (
        f"v9 (research): console 1.3.0-rc.4, transmitter 2.1.0-rc.7, presets {COMMIT[:7]}")


def _plan(release, keys):
    return ru.ReleaseUpdate(release, *unit(keys, release)).plan()


def test_a_conversion_is_pending_and_says_it_locks(release, keys):
    plan = _plan(release, keys)
    plan.firmware = [ru.Step(s.target, "convert", "research -> clinical", s.board)
                     for s in plan.firmware]
    table = {r.key: r for r in rows.plan_rows(plan)}
    for key in ("console", "transmitter master", "transmitter module 1"):
        assert table[key].status is Status.CONVERT
        assert table[key].status_text == "Convert to clinical (locks the bootloader)"
        assert table[key].pending
    assert [r.key for r in rows.converts(list(table.values()))] == [
        "console", "transmitter master", "transmitter module 1"]


def test_presets_through_the_bootloader(release, keys):
    plan = _plan(release, keys)
    plan.presets = [ru.Step("console page", "write-presets-dfu", "empty")]
    plan.presets += [ru.Step(f"transmitter module {m} slot {s:02d}", "write-presets-dfu",
                             "empty", slot=s, module=m) for m in (0, 1) for s in range(RELEASED)]
    plan.presets.append(ru.Step("transmitter module 1 slot 13", "clear-slot", "stray",
                                slot=13, module=1))
    table = {r.key: r for r in rows.plan_rows(plan)}
    assert table["presets console"].status_text == "Write 1 (through the bootloader)"
    assert table["presets module 0"].status_text == f"Write {RELEASED} (through the bootloader)"
    assert table["presets module 1"].status_text == (
        f"Write {RELEASED} (through the bootloader), clears 1")
    assert table["presets module 1"].installed == f"0 of {RELEASED}"
    assert all(table[k].pending for k in table if k.startswith("presets"))


def test_an_unknown_action_shows_its_name(release, keys):
    plan = _plan(release, keys)
    first = plan.firmware[0]
    plan.firmware[0] = ru.Step(first.target, "frobnicate", "?", first.board)
    plan.firmware.append(ru.Step("bridge", "convert", "?"))      # no board
    plan.presets.append(ru.Step("transmitter module 0 slot 05", "reticulate", "?",
                                slot=5, module=0))
    table = {r.key: r for r in rows.plan_rows(plan)}
    assert table["console"].status is Status.OTHER
    assert table["console"].status_text == "frobnicate"
    assert table["bridge"].status is Status.CONVERT and table["bridge"].installed == "-"
    assert table["presets module 0"].status_text == "reticulate 1"
    assert table["presets module 0"].pending


def test_the_banner_per_domain():
    clinical = rows.banner("clinical")
    assert clinical.text == ("CLINICAL UPDATER \u2014 converts the unit to clinical and locks "
                             "its bootloaders (RDP1)")
    assert clinical.visible and clinical.title == "Clinical"
    assert clinical.background == "#8B0000" and clinical.foreground == "#FFFFFF"
    research = rows.banner("research")
    assert research.text == "RESEARCH UPDATER" and research.visible
    assert research.title == "Research" and research.background != clinical.background
    none = rows.banner(None)
    assert not none.visible and none.text == "Release: none" and none.title == ""
    assert rows.banner("vet").text == "VET UPDATER"
