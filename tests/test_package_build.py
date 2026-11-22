# SPDX-License-Identifier: Apache-2.0
"""scripts/package_build.py: names, sums, manifest and bucket folder."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "package_build.py"
ENV = {"GITHUB_SHA": "ab" * 20, "GITHUB_ACTOR": "builder", "GITHUB_SERVER_URL": "https://github.com",
       "GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": "7"}


@pytest.fixture(scope="module")
def pb():
    spec = importlib.util.spec_from_file_location("package_build", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build(tmp_path: Path, domain: str) -> tuple[Path, Path]:
    release, dist = tmp_path / "release", tmp_path / "dist"
    release.mkdir()
    dist.mkdir()
    info = {"tag": "v0.2.0-rc11", "firmware": "fw.zip", "presets": "p.zip", "domain": domain}
    if domain == "clinical":
        info["manifest"] = "clinical-build-manifest.json"
        (release / info["manifest"]).write_text(json.dumps({"commit": "c0ffee"}))
    (release / "release.json").write_text(json.dumps(info))
    (dist / "OpenLIFUUpdater.exe").write_bytes(b"window")
    (dist / "openlifu-fw-updater.exe").write_bytes(b"cli")
    return release, dist


@pytest.mark.parametrize("variant", ["research", "clinical"])
def test_package_names_sums_and_manifest(pb, tmp_path, variant):
    release, dist = _build(tmp_path, variant)
    out = tmp_path / "package"
    manifest = pb.package(out, variant=variant, tag="0.1.0-rc.4", sdk="2.1.0rc17", env=ENV,
                          release=release, dist=dist)
    names = sorted(p.name for p in out.iterdir())
    assert names == sorted([f"OpenLIFUUpdater-{variant}-0.1.0-rc.4.exe",
                            f"openlifu-fw-updater-{variant}-0.1.0-rc.4.exe",
                            "release.json", "SHA256SUMS", "build-manifest.json"])
    lines = (out / "SHA256SUMS").read_text().splitlines()
    sums = dict(line.split("  ")[::-1] for line in lines)
    window = hashlib.sha256(b"window").hexdigest()
    assert sums[f"OpenLIFUUpdater-{variant}-0.1.0-rc.4.exe"] == window
    assert set(sums) == set(manifest["files"])
    assert "build-manifest.json" not in sums
    assert manifest["diathermy"]["release"] == "v0.2.0-rc11"
    assert manifest["run"] == "https://github.com/o/r/actions/runs/7"
    assert ("environment" in manifest) == (variant == "clinical")
    assert (manifest["diathermy"].get("commit") == "c0ffee") == (variant == "clinical")
    assert json.loads((out / "build-manifest.json").read_text()) == manifest


def test_a_release_of_the_other_domain_is_refused(pb, tmp_path):
    release, dist = _build(tmp_path, "research")
    with pytest.raises(SystemExit, match="research release, not clinical"):
        pb.package(tmp_path / "out", variant="clinical", tag="0.1.0", sdk="x", env=ENV,
                   release=release, dist=dist)


def test_bucket_folder_sits_next_to_the_diathermy_release(pb):
    assert pb.bucket_folder("b", "v0.2.0-rc11", "0.1.0-rc.4", "research") == (
        "gs://b/openlifu-diathermy-application/v0.2.0-rc11/openlifu-fw-updater-0.1.0-rc.4-research/")


def test_a_branch_ref_is_a_safe_file_name(pb, tmp_path):
    release, dist = _build(tmp_path, "research")
    manifest = pb.package(tmp_path / "out", variant="research", tag="feature/x", sdk="x", env={},
                          release=release, dist=dist)
    assert manifest["tag"] == "feature-x" and manifest["run"] is None
    assert (tmp_path / "out" / "OpenLIFUUpdater-research-feature-x.exe").exists()
