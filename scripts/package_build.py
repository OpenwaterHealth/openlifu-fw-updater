# SPDX-License-Identifier: Apache-2.0
"""Package a built updater for the artifact bucket.

Copies ``dist/OpenLIFUUpdater.exe`` and ``dist/openlifu-fw-updater.exe`` into
*out* as ``OpenLIFUUpdater-<variant>-<tag>.exe`` and
``openlifu-fw-updater-<variant>-<tag>.exe``, with the embedded
``release.json``, a ``SHA256SUMS`` over those three, and a
``build-manifest.json`` (what it was built from, by whom). Prints the bucket
folder it belongs in, next to the diathermy release it carries:

    gs://<bucket>/openlifu-diathermy-application/<release>/openlifu-fw-updater-<tag>-<variant>/

A subfolder, so the updater's SHA256SUMS and manifest never collide with the
diathermy release's own write-once files, and another updater tag for the
same diathermy release sits beside it.

    python scripts/package_build.py --variant research --tag 0.1.0-rc.4
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASE = ROOT / "src" / "openlifu_fw_updater" / "release"
DIST = ROOT / "dist"
DIATHERMY = "openlifu-diathermy-application"
PRODUCT = "openlifu-fw-updater"
EXES = (("OpenLIFUUpdater.exe", "OpenLIFUUpdater"),
        ("openlifu-fw-updater.exe", "openlifu-fw-updater"))
OWN = ("SHA256SUMS", "build-manifest.json")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bucket_folder(bucket: str, release: str, tag: str, variant: str) -> str:
    """Where a build of updater *tag* around diathermy *release* is stored."""
    return f"gs://{bucket}/{DIATHERMY}/{release}/{PRODUCT}-{tag}-{variant}/"


def package(out: Path, *, variant: str, tag: str, sdk: str, env: dict[str, str],
            release: Path = RELEASE, dist: Path = DIST) -> dict:
    """Fill *out* and return the build manifest."""
    tag = tag.replace("/", "-")  # a branch build: feature/x
    info = json.loads((release / "release.json").read_text(encoding="utf-8"))
    if info.get("domain") != variant:
        raise SystemExit(f"release.json is a {info.get('domain')} release, not {variant}")
    out.mkdir(parents=True, exist_ok=True)
    for src, stem in EXES:
        shutil.copyfile(dist / src, out / f"{stem}-{variant}-{tag}.exe")
    shutil.copyfile(release / "release.json", out / "release.json")
    files = sorted(p for p in out.iterdir() if p.is_file() and p.name not in OWN)
    sums = {p.name: _sha256(p) for p in files}
    # Same format as sha256sum writes, LF line ends.
    text = "".join(f"{h}  {n}\n" for n, h in sums.items())
    (out / "SHA256SUMS").write_bytes(text.encode())
    diathermy: dict = {"release": info["tag"], "firmware": info["firmware"],
                       "presets": info["presets"]}
    if info.get("manifest"):
        raw = (release / info["manifest"]).read_bytes()
        diathermy.update(manifest_sha256=hashlib.sha256(raw).hexdigest(),
                         commit=json.loads(raw).get("commit"))
    run = None
    if env.get("GITHUB_RUN_ID"):
        run = (f"{env['GITHUB_SERVER_URL']}/{env['GITHUB_REPOSITORY']}"
               f"/actions/runs/{env['GITHUB_RUN_ID']}")
    manifest = {
        "product": PRODUCT,
        "variant": variant,
        "tag": tag,
        "commit": env.get("GITHUB_SHA"),
        "built_utc": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "diathermy": diathermy,
        "openlifu_sdk": sdk,
        "requested_by": env.get("GITHUB_ACTOR"),
        "run": run,
        "files": sums,
    }
    if variant == "clinical":
        manifest["environment"] = "clinical"
    (out / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                             encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--variant", choices=("research", "clinical"), required=True)
    ap.add_argument("--tag", required=True, help="this updater's tag (or ref name)")
    ap.add_argument("--out", type=Path, default=ROOT / "package")
    ap.add_argument("--bucket",
                    default=os.environ.get("GCS_BUCKET", "openwater-firmware-artifacts"))
    args = ap.parse_args(argv)
    from importlib.metadata import version

    manifest = package(args.out, variant=args.variant, tag=args.tag,
                       sdk=version("openlifu-sdk"), env=dict(os.environ))
    print((args.out / "SHA256SUMS").read_text(), end="")
    dest = bucket_folder(args.bucket, manifest["diathermy"]["release"], manifest["tag"],
                         args.variant)
    print(f"DEST={dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
