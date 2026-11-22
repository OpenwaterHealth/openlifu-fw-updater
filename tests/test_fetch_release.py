# SPDX-License-Identifier: Apache-2.0
"""scripts/fetch_release.py --clinical: the bucket download (gcloud mocked)
and the checks before a release.json is written."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess  # nosec B404 - mocked here, nothing is run
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fetch_release.py"
TAG = "v0.2.0-rc10"
FIRMWARE, PRESETS = f"clinical-firmware-{TAG}.zip", f"clinical-presets-{TAG}.zip"


@pytest.fixture
def fetch_release():
    spec = importlib.util.spec_from_file_location("fetch_release", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _bucket(*, sums_firmware: bytes = b"fw", variant: str = "clinical") -> dict[str, bytes]:
    """The objects of the diathermy clinical build of TAG."""
    blobs = {FIRMWARE: b"fw", PRESETS: b"presets"}
    sums = {FIRMWARE: _sha(sums_firmware), PRESETS: _sha(blobs[PRESETS])}
    blobs["SHA256SUMS"] = "".join(f"{h}  {n}\n" for n, h in sums.items()).encode()
    blobs["clinical-build-manifest.json"] = json.dumps(
        {"variant": variant, "tag": TAG, "files": sums}).encode()
    return blobs


@pytest.fixture
def gcloud(fetch_release, monkeypatch):
    """A fake ``gcloud storage cp gs://... <dest>`` over *objects*."""
    state = {"objects": _bucket(), "calls": []}

    def run(args, **_):
        state["calls"].append(args)
        assert args[:3] == ["/bin/gcloud", "storage", "cp"]
        source, dest = args[3], args[4]
        name = source.rsplit("/", 1)[1]
        if name not in state["objects"]:
            raise subprocess.CalledProcessError(1, args, stderr="No URLs matched")
        Path(dest).write_bytes(state["objects"][name])
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(fetch_release.shutil, "which",
                        lambda name: "/bin/gcloud" if name == "gcloud" else None)
    monkeypatch.setattr(fetch_release.subprocess, "run", run)
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    return state


def test_a_clinical_release_is_embedded_with_its_domain(fetch_release, gcloud, tmp_path):
    dest = fetch_release.fetch_clinical(TAG, tmp_path / "release")
    info = json.loads((dest / "release.json").read_text())
    assert info == {"tag": TAG, "firmware": FIRMWARE, "presets": PRESETS,
                    "domain": "clinical", "manifest": "clinical-build-manifest.json"}
    sources = [c[3] for c in gcloud["calls"]]
    assert sources[0] == (f"gs://openwater-firmware-artifacts/openlifu-diathermy-application/"
                          f"{TAG}/{FIRMWARE}")
    assert len(sources) == 4

    from openlifu_fw_updater.release import find_release

    assert find_release(dest).domain == "clinical"


def test_the_bucket_can_be_overridden(fetch_release, gcloud, tmp_path, monkeypatch):
    monkeypatch.setenv("GCS_BUCKET", "other-bucket")
    fetch_release.fetch_clinical(TAG, tmp_path / "release")
    assert gcloud["calls"][0][3].startswith("gs://other-bucket/openlifu-diathermy-application/")


def test_a_sum_mismatch_is_refused(fetch_release, gcloud, tmp_path):
    gcloud["objects"] = _bucket(sums_firmware=b"another build")
    with pytest.raises(SystemExit, match=f"{FIRMWARE}: SHA-256 .* does not match SHA256SUMS"):
        fetch_release.fetch_clinical(TAG, tmp_path / "release")
    assert not (tmp_path / "release" / "release.json").exists()


def test_a_manifest_of_another_build_is_refused(fetch_release, gcloud, tmp_path):
    gcloud["objects"] = _bucket(variant="research")
    with pytest.raises(SystemExit, match="is not the clinical build"):
        fetch_release.fetch_clinical(TAG, tmp_path / "release")


def test_a_missing_build_says_to_run_it(fetch_release, gcloud, tmp_path):
    del gcloud["objects"][PRESETS]
    with pytest.raises(SystemExit, match="has the diathermy clinical build"):
        fetch_release.fetch_clinical(TAG, tmp_path / "release")


def test_a_bad_tag_is_refused_before_gcloud(fetch_release, gcloud, tmp_path):
    with pytest.raises(SystemExit, match="not a diathermy release tag"):
        fetch_release.fetch_clinical("v1/../../x", tmp_path / "release")
    assert gcloud["calls"] == []


def test_the_research_release_json_says_research(fetch_release, monkeypatch, tmp_path):
    names = [f"firmware-{TAG}.zip", f"research-presets-{TAG}.zip"]

    def gh(*args):
        if args[:2] == ("release", "view"):
            return json.dumps({"assets": [{"name": n, "digest": "sha256:" + _sha(b"z")}
                                          for n in names]})
        (Path(args[args.index("-D") + 1]) / args[args.index("-p") + 1]).write_bytes(b"z")
        return ""

    monkeypatch.setattr(fetch_release, "_gh", gh)
    info = json.loads((fetch_release.fetch(TAG, tmp_path / "r") / "release.json").read_text())
    assert info == {"tag": TAG, "firmware": names[0], "presets": names[1], "domain": "research"}
