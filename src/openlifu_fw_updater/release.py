# SPDX-License-Identifier: Apache-2.0
"""Where the release comes from: the files given on the command line, or
the release embedded in this build (``release/``, filled by
``scripts/fetch_release.py``)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

#: The embedded release's description, written by scripts/fetch_release.py.
RELEASE_INFO = "release.json"


@dataclass(frozen=True)
class ReleaseFiles:
    tag: str
    firmware: Path
    presets: Path | None
    #: The domain release.json claims ("research" or "clinical"); None for
    #: files given on the command line, whose domain only the bundle tells.
    domain: str | None = None


def embedded_release_dir() -> Path:
    return Path(str(resources.files("openlifu_fw_updater").joinpath("release")))


def find_release(folder: Path) -> ReleaseFiles:
    """The release in *folder*: ``release.json`` names its files.

    Raises:
        FileNotFoundError: *folder* holds no release, or a file it names is
            missing.
    """
    info_path = folder / RELEASE_INFO
    if not info_path.is_file():
        raise FileNotFoundError(f"no release in {folder} (no {RELEASE_INFO}): this build "
                                "embeds none; pass --firmware and --presets")
    info = json.loads(info_path.read_text(encoding="utf-8"))
    firmware = folder / info["firmware"]
    presets = folder / info["presets"] if info.get("presets") else None
    for path in (firmware, presets):
        if path is not None and not path.is_file():
            raise FileNotFoundError(f"{info_path} names {path.name}, which is missing")
    return ReleaseFiles(tag=info["tag"], firmware=firmware, presets=presets,
                        domain=info.get("domain", "research"))


def check_domain(files: ReleaseFiles, bundle_domain: str) -> None:
    """The loaded bundle is the domain release.json claims.

    Raises:
        ValueError: release.json says one domain, the signatures another.
    """
    if files.domain is not None and files.domain != bundle_domain:
        raise ValueError(f"release.json says {files.domain}, but the release is signed "
                         f"with the {bundle_domain} keys")
