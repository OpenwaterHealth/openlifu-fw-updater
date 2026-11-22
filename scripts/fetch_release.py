# SPDX-License-Identifier: Apache-2.0
"""Embed a diathermy release (research or clinical) in this build.

Research (default): downloads ``firmware-<tag>.zip`` and
``research-presets-<tag>.zip`` from the openlifu-diathermy-application GitHub
release (with the GitHub CLI, so the user's own login is used) and checks
them against the release's published digests.

Clinical (``--clinical``): downloads ``clinical-firmware-<tag>.zip``,
``clinical-presets-<tag>.zip``, ``SHA256SUMS`` and
``clinical-build-manifest.json`` from the private artifact bucket
(``gs://<GCS_BUCKET>/openlifu-diathermy-application/<tag>/``, written by the
diathermy clinical-build.yml) with ``gcloud storage cp`` (the caller's gcloud
login), and checks the zips against ``SHA256SUMS`` and the manifest.

Either way it writes ``release.json`` next to the files in
``src/openlifu_fw_updater/release/``.

    python scripts/fetch_release.py v0.2.0-rc8
    python scripts/fetch_release.py --clinical v0.2.0-rc10
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess  # nosec B404
from pathlib import Path

REPO = "OpenwaterHealth/openlifu-diathermy-application"
PRODUCT = "openlifu-diathermy-application"
DEFAULT_BUCKET = "openwater-firmware-artifacts"
MANIFEST = "clinical-build-manifest.json"
DEST = Path(__file__).resolve().parents[1] / "src" / "openlifu_fw_updater" / "release"
#: A diathermy release tag (vX.Y.Z or vX.Y.Z-rcN); it also names bucket paths.
TAG_RE = re.compile(r"^v\d+\.\d+\.\d+(-rc\d+)?$")
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]$")


def _run(tool: str, *args: str) -> str:
    """Run *tool* (an absolute path from shutil.which) with fixed arguments."""
    return subprocess.run([tool, *args], check=True, capture_output=True,  # nosec B603
                          text=True).stdout


def _gh(*args: str) -> str:
    """Run the GitHub CLI (found on PATH) with fixed arguments."""
    gh = shutil.which("gh")
    if gh is None:
        raise SystemExit("the GitHub CLI (gh) is needed: https://cli.github.com")
    return _run(gh, *args)


def _gcloud(*args: str) -> str:
    """Run the Google Cloud CLI (found on PATH; gcloud.cmd on Windows)."""
    gcloud = shutil.which("gcloud") or shutil.which("gcloud.cmd")
    if gcloud is None:
        raise SystemExit("the Google Cloud CLI (gcloud) is needed: "
                         "https://cloud.google.com/sdk/docs/install")
    return _run(gcloud, *args)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check_tag(tag: str) -> None:
    if not TAG_RE.match(tag):
        raise SystemExit(f"'{tag}' is not a diathermy release tag (vX.Y.Z or vX.Y.Z-rcN)")


def _fresh(dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)


def _write_info(dest: Path, info: dict) -> None:
    (dest / "release.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")


def fetch(tag: str, dest: Path = DEST) -> Path:
    """The research release *tag*, from its GitHub release."""
    _check_tag(tag)
    names = {"firmware": f"firmware-{tag}.zip", "presets": f"research-presets-{tag}.zip"}
    assets = json.loads(_gh("release", "view", tag, "-R", REPO, "--json", "assets"))["assets"]
    digests = {a["name"]: a.get("digest", "") for a in assets}
    for name in names.values():
        if name not in digests:
            raise SystemExit(f"{REPO} {tag} has no asset {name}")
    _fresh(dest)
    for name in names.values():
        _gh("release", "download", tag, "-R", REPO, "-p", name, "-D", str(dest))
        want = digests[name].removeprefix("sha256:")
        got = _sha256(dest / name)
        if want and got != want:
            raise SystemExit(f"{name}: SHA-256 {got} does not match the release's {want}")
    _write_info(dest, {"tag": tag, **names, "domain": "research"})
    return dest


def parse_sums(text: str) -> dict[str, str]:
    """``{name: sha256}`` from sha256sum output (``<hex>  <name>`` or
    ``<hex> *<name>``)."""
    sums = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        digest, _, name = line.partition(" ")
        sums[name.strip().removeprefix("*")] = digest.lower()
    return sums


def _check_clinical(dest: Path, tag: str, names: dict[str, str]) -> None:
    """The zips match SHA256SUMS, and the manifest is this tag's clinical
    build and names the same sums."""
    sums = parse_sums((dest / "SHA256SUMS").read_text(encoding="utf-8"))
    manifest = json.loads((dest / MANIFEST).read_text(encoding="utf-8"))
    if manifest.get("variant") != "clinical" or manifest.get("tag") != tag:
        raise SystemExit(f"{MANIFEST} is not the clinical build of {tag} "
                         f"(variant {manifest.get('variant')!r}, tag {manifest.get('tag')!r})")
    listed = manifest.get("files", {})
    for name in names.values():
        if name not in sums:
            raise SystemExit(f"SHA256SUMS does not list {name}")
        got = _sha256(dest / name)
        if got != sums[name]:
            raise SystemExit(f"{name}: SHA-256 {got} does not match SHA256SUMS {sums[name]}")
        if listed.get(name) != got:
            raise SystemExit(f"{name}: SHA-256 {got} does not match {MANIFEST} "
                             f"{listed.get(name)}")


def fetch_clinical(tag: str, dest: Path = DEST, bucket: str | None = None) -> Path:
    """The clinical release *tag*, from the private artifact bucket."""
    _check_tag(tag)
    bucket = bucket or os.environ.get("GCS_BUCKET") or DEFAULT_BUCKET
    if not _BUCKET_RE.match(bucket):
        raise SystemExit(f"'{bucket}' is not a bucket name")
    names = {"firmware": f"clinical-firmware-{tag}.zip",
             "presets": f"clinical-presets-{tag}.zip"}
    source = f"gs://{bucket}/{PRODUCT}/{tag}/"
    _fresh(dest)
    for name in (*names.values(), "SHA256SUMS", MANIFEST):
        try:
            _gcloud("storage", "cp", source + name, str(dest / name))
        except subprocess.CalledProcessError as exc:
            raise SystemExit(f"{source}{name} could not be downloaded (has the diathermy "
                             f"clinical build of {tag} run?): {exc.stderr or exc}") from exc
    _check_clinical(dest, tag, names)
    _write_info(dest, {"tag": tag, **names, "domain": "clinical", "manifest": MANIFEST})
    return dest


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tag", help="the diathermy release tag, e.g. v0.2.0-rc8")
    parser.add_argument("--clinical", action="store_true",
                        help="the clinical build of the tag, from the artifact bucket "
                             "(gcloud; bucket from GCS_BUCKET)")
    args = parser.parse_args(argv)
    folder = fetch_clinical(args.tag) if args.clinical else fetch(args.tag)
    print(f"embedded {args.tag} ({'clinical' if args.clinical else 'research'}) in {folder}")


if __name__ == "__main__":
    main()
