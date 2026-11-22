# SPDX-License-Identifier: Apache-2.0
"""Embed a diathermy research release in this build.

Downloads ``firmware-<tag>.zip`` and ``research-presets-<tag>.zip`` from the
openlifu-diathermy-application GitHub release (with the GitHub CLI, so the
user's own login is used), checks them against the release's published
digests, and writes ``release.json`` next to them in
``src/openlifu_fw_updater/release/``.

    python scripts/fetch_release.py v0.2.0-rc8

Clinical releases come from the clinical bucket and are not handled here yet.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess  # nosec B404
from pathlib import Path

REPO = "OpenwaterHealth/openlifu-diathermy-application"
DEST = Path(__file__).resolve().parents[1] / "src" / "openlifu_fw_updater" / "release"


def _gh(*args: str) -> str:
    """Run the GitHub CLI (found on PATH) with fixed arguments."""
    gh = shutil.which("gh")
    if gh is None:
        raise SystemExit("the GitHub CLI (gh) is needed: https://cli.github.com")
    return subprocess.run([gh, *args], check=True, capture_output=True,  # nosec B603
                          text=True).stdout


def fetch(tag: str, dest: Path = DEST) -> Path:
    names = {"firmware": f"firmware-{tag}.zip", "presets": f"research-presets-{tag}.zip"}
    assets = json.loads(_gh("release", "view", tag, "-R", REPO, "--json", "assets"))["assets"]
    digests = {a["name"]: a.get("digest", "") for a in assets}
    for name in names.values():
        if name not in digests:
            raise SystemExit(f"{REPO} {tag} has no asset {name}")
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for name in names.values():
        _gh("release", "download", tag, "-R", REPO, "-p", name, "-D", str(dest))
        want = digests[name].removeprefix("sha256:")
        got = hashlib.sha256((dest / name).read_bytes()).hexdigest()
        if want and got != want:
            raise SystemExit(f"{name}: SHA-256 {got} does not match the release's {want}")
    (dest / "release.json").write_text(json.dumps({"tag": tag, **names}, indent=2) + "\n",
                                       encoding="utf-8")
    return dest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tag", help="the diathermy release tag, e.g. v0.2.0-rc8")
    tag = parser.parse_args().tag
    print(f"embedded {tag} in {fetch(tag)}")


if __name__ == "__main__":
    main()
