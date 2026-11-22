# SPDX-License-Identifier: Apache-2.0
"""The updater shell: which release it picks and how it reports. The
update itself is the SDK's (openlifu-sdk unit-test/test_release_update.py)."""

from __future__ import annotations

import json

import pytest

from openlifu_fw_updater import cli, release


def _embed(folder, presets=True):
    folder.mkdir()
    (folder / "firmware-vX.zip").write_bytes(b"zip")
    info = {"tag": "vX", "firmware": "firmware-vX.zip"}
    if presets:
        (folder / "research-presets-vX.zip").write_bytes(b"zip")
        info["presets"] = "research-presets-vX.zip"
    (folder / "release.json").write_text(json.dumps(info))
    return folder


def test_the_embedded_release_is_found(tmp_path):
    files = release.find_release(_embed(tmp_path / "release"))
    assert files.tag == "vX"
    assert files.firmware.name == "firmware-vX.zip"
    assert files.presets.name == "research-presets-vX.zip"


def test_a_build_without_a_release_says_so(tmp_path):
    with pytest.raises(FileNotFoundError, match="embeds none"):
        release.find_release(tmp_path)


def test_a_missing_file_is_named(tmp_path):
    folder = _embed(tmp_path / "release")
    (folder / "research-presets-vX.zip").unlink()
    with pytest.raises(FileNotFoundError, match="research-presets-vX.zip"):
        release.find_release(folder)


def test_presets_without_firmware_are_refused(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--presets", "p.zip"])
    assert "--presets needs --firmware" in capsys.readouterr().err


def test_files_on_the_command_line_replace_the_embedded_release(monkeypatch, tmp_path):
    from openlifu_sdk.io import LIFUReleaseUpdate as ru

    seen = {}
    monkeypatch.setattr(ru.ReleaseBundle, "load",
                        classmethod(lambda cls, fw, presets: seen.update(fw=fw, presets=presets)
                                    or "bundle"))
    monkeypatch.setattr(ru, "run_cli", lambda bundle, plan_only, yes: seen.update(
        bundle=bundle, plan_only=plan_only, yes=yes) or 0)
    assert cli.main(["--firmware", str(tmp_path / "fw.zip"), "--presets",
                     str(tmp_path / "p.zip"), "--plan"]) == 0
    assert seen["fw"].name == "fw.zip" and seen["presets"].name == "p.zip"
    assert seen["plan_only"] is True and seen["yes"] is False


def test_a_bad_release_exits_with_its_reason(monkeypatch, tmp_path, capsys):
    from openlifu_sdk.io import LIFUReleaseUpdate as ru

    def refuse(cls, fw, presets):
        raise ru.ReleaseError("firmware-vX.zip: does not match its SHA-256")

    monkeypatch.setattr(ru.ReleaseBundle, "load", classmethod(refuse))
    assert cli.main(["--firmware", str(tmp_path / "fw.zip")]) == 2
    assert "does not match its SHA-256" in capsys.readouterr().out
