# SPDX-License-Identifier: Apache-2.0
"""Fixtures: headless Qt, and a release signed with keys the SDK trusts for
the test (see release_fakes)."""

from __future__ import annotations

import os

import pytest

# Before anything imports Qt: no display, no window on the desktop.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def keys(monkeypatch, tmp_path):
    from openlifu_sdk.io import LIFUCrypto
    from openlifu_sdk.io import LIFUReleaseUpdate as ru
    from release_fakes import make_keys

    out = make_keys(tmp_path)
    monkeypatch.setattr(LIFUCrypto, "trusted_public_key",
                        lambda b, d: out[b, d].private_key.public_key())
    monkeypatch.setattr(ru.time, "sleep", lambda _s: None)
    return out


@pytest.fixture
def release_dirs(tmp_path, keys):
    """(firmware folder, presets folder) of the test release."""
    from release_fakes import make_firmware, make_presets

    return make_firmware(tmp_path, keys), make_presets(tmp_path, keys)


@pytest.fixture
def release(tmp_path, release_dirs):
    from openlifu_sdk.io import LIFUReleaseUpdate as ru

    return ru.ReleaseBundle.load(*release_dirs, workdir=tmp_path / "work")
