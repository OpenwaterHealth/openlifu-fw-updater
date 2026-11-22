# openlifu-fw-updater

Takes a connected Open-LIFU unit (console + transmitter chain) to one diathermy release: the
console and transmitter firmware, then the presets, then a read-back of everything.

All the update logic is in the SDK, `openlifu_sdk.io.LIFUReleaseUpdate`. This package only
finds the release and runs it:

- A **build with an embedded release** installs that release.
- **`--firmware` / `--presets`** install the given files instead. These are a diathermy
  release's `firmware-<tag>.zip` and `research-presets-<tag>.zip`, zipped or unpacked.

## What it does

1. **Load the release.** Every `SHA256SUMS` is checked, and every image and preset package
   is checked against the SDK's trusted keys. The key that signed both applications sets
   the release's domain: research or clinical.
2. **Plan, without entering DFU.** For the console and each transmitter module it reads:
   - the firmware version;
   - the key that signed the firmware;
   - the signed header (FwVersion and body hash);
   - every preset package header.

   A board already on the release build is left alone. An older one is updated. A newer one,
   a clinical unit given a research release, or a board whose signer it can't identify is
   refused before anything is written.
3. **Run.**
   1. The firmware goes console first, then the transmitter slaves over I²C, then the master
      over USB. A 1.0.x bootloader is replaced with the bootloader updater on the way.
   2. The presets that differ are written to every physical module, and transmitter slots
      past the released set are emptied.
   3. If a transmitter update fails, the chain is brought back to its application. That means
      leaving DFU, and if the master still doesn't answer, power-cycling it through the
      console's 12 V rail.
4. **Read back** firmware and presets, and compare them with the release.

A **research** build takes research units to a research release. A **clinical** build (see
[Clinical build](#clinical-build)) converts a research unit to clinical: the clinical bootloader
replaces the research one and locks each board (RDP1), and the clinical presets are written
through the bootloaders. That conversion comes from the SDK (`action="convert"` and
`"write-presets-dfu"` plan steps); an SDK without it refuses clinical releases with a message.

## The window: `OpenLIFUUpdater`

```
pip install -e .[gui]
OpenLIFUUpdater                                   # the embedded release
OpenLIFUUpdater --firmware firmware-v0.2.0-rc8.zip --presets research-presets-v0.2.0-rc8.zip
OpenLIFUUpdater --release-dir <folder with release.json>
OpenLIFUUpdater --self-test                       # build the window, verify the release, load libusb; no unit
```

A strip across the top of the window says which kind of updater it is, from the domain of the
loaded release (the key that signed it):

- **Clinical**: dark red, "CLINICAL UPDATER — converts the unit to clinical and locks its
  bootloaders (RDP1)"; the window title is "Open-LIFU Updater — Clinical".
- **Research**: blue, "RESEARCH UPDATER"; the title is "Open-LIFU Updater — Research".
- No usable release: no strip, "Release: none".

`release.json` names the domain it was built for; a release signed with the other domain's keys
is refused.

One window, one table:

| Device | Installed | This release | Status |
|---|---|---|---|
| Console, Transmitter master, Transmitter module N | the version the board reports | the release's firmware tag (`firmware-lock.json`) | Up to date / Update / Update (replaces bootloader) / Convert to clinical (locks the bootloader) / Refused |
| Console presets, Transmitter presets (master, module N) | how many of the released packages it holds ("13 of 13") | diathermy commit and context | Up to date / Update (writes 13, clears 1) / Write 13 (through the bootloader) / Refused |

- **Update (replaces bootloader)**: the board runs firmware from before its current bootloader
  (console below 1.3.0, transmitter below 2.1.0), so its 1.0.x bootloader is replaced on the
  way. The confirmation says so; it cannot be undone.
- **Convert to clinical (locks the bootloader)**: the clinical bootloader replaces the research
  one and the board is locked (RDP1). The confirmation says so: going back to research needs
  SWD recovery (RMA).
- A step the updater does not know is shown by the SDK's action name.
- **Refused**: the SDK's reason is shown under the table, and there is no Update button.
- Update asks once (Cancel is the default), then shows each step and the DFU progress. After
  the run the unit is read again, and the table and the read-back result show where it is.
- If another Open-LIFU application holds the unit, the window says so; close it and Check
  again. While the console or the transmitter is missing, the window looks for it every 2 s.
- The window cannot be closed during an update. Closing it releases the unit for other
  applications.
- The log (`update-<time>.log`, next to the exe, or in the temp folder) has every step and
  the read-back; Open log shows it.

Qt is Qt for Python (PySide6, LGPL-3.0), an optional extra so the command line runs without
it. PyQt (GPL) and `openlifu-sdk[ui]` are never used.

## The command line: `openlifu-fw-updater`

```
pip install -e .[test]
openlifu-fw-updater --firmware firmware-v0.2.0-rc8.zip --presets research-presets-v0.2.0-rc8.zip --plan
openlifu-fw-updater --firmware firmware-v0.2.0-rc8.zip --presets research-presets-v0.2.0-rc8.zip
```

- `--plan` reads the unit, shows each step and writes nothing.
- Without `-y`, it asks before writing.
- It prints `Release <tag> (<domain>)` first.
- Exit status: 0 for done or nothing to do, 1 for a failure or read-back mismatch, 2 for a
  refusal or a bad release.

### Embedding a release

```
python scripts/fetch_release.py v0.2.0-rc8
```

This downloads the release's firmware and research preset zips with the GitHub CLI and checks
them against the release's published digests. It writes them to `src/openlifu_fw_updater/release/`,
which is git-ignored, with `release.json` (`domain: "research"`). `openlifu-fw-updater` with no
arguments then installs that release.

```
gcloud auth login
python scripts/fetch_release.py --clinical v0.2.0-rc10
```

`--clinical` embeds the diathermy **clinical** build of the tag instead, from the private bucket
(`gs://openwater-firmware-artifacts/openlifu-diathermy-application/<tag>/`; `GCS_BUCKET`
overrides the bucket) with `gcloud storage cp`: `clinical-firmware-<tag>.zip`,
`clinical-presets-<tag>.zip`, `SHA256SUMS` and `clinical-build-manifest.json`. Both zips must
match `SHA256SUMS` and the manifest; `release.json` then says `domain: "clinical"`.

## Building the exes

```
pip install -e .[build]
python scripts/fetch_release.py v0.2.0-rc8
.\build.ps1
```

`OpenLIFUUpdater.spec` builds `dist\OpenLIFUUpdater.exe` (window) and
`dist\openlifu-fw-updater.exe` (command line, no Qt), each carrying the embedded release, the
SDK's trusted public keys and its libusb. `build.ps1` then runs `openlifu-fw-updater.exe --help`
and `OpenLIFUUpdater.exe --self-test`; neither opens a unit.

CI (`.github/workflows/build-updater.yml`, on demand with a release tag, or on a tag push with
the `DIATHERMY_RELEASE` variable) fetches the release, runs the tests, builds both exes and
keeps them as a workflow artifact. The exes are not code-signed yet.

## Clinical build

`.github/workflows/clinical-build.yml` builds the clinical updater. It needs the diathermy
clinical build of the release first (diathermy's `clinical-build.yml`, which writes the clinical
firmware and presets to the bucket). Then tag this repository and run it **on the tag**:

```
git tag 0.2.0-rc.1 && git push origin 0.2.0-rc.1
gh workflow run clinical-build.yml --ref 0.2.0-rc.1 -f diathermy_release=v0.2.0-rc10
```

- It refuses to run on a branch or on a tag that is not `X.Y.Z` / `X.Y.Z-rc.N`, and runs in the
  `clinical` environment (reader and uploader identities by Workload Identity Federation).
- It fetches the clinical release (`fetch_release.py --clinical`), runs the tests, builds both
  exes and self-tests them, then stores, write-once and **only in the private bucket**:

  ```
  gs://openwater-firmware-artifacts/openlifu-fw-updater/<tag>/
    OpenLIFUUpdater-clinical-<tag>.exe
    openlifu-fw-updater-clinical-<tag>.exe
    release.json
    clinical-build-manifest.json   updater tag and commit, diathermy release and its manifest's
                                   SHA-256, SDK version, build time, run and requester, file sums
    SHA256SUMS
  ```

  Nothing is kept as a workflow artifact or published. A second run for the same tag fails at
  the upload; another release is a new tag. To download it: `gcloud storage cp -r
  gs://openwater-firmware-artifacts/openlifu-fw-updater/<tag> .`

## Development

```
pre-commit install
python -m pytest          # the window's tests run headless (QT_QPA_PLATFORM=offscreen)
```

The SDK pin in `pyproject.toml` must be a tag that has `LIFUReleaseUpdate`. To work against a
local SDK checkout:

```
pip install --no-deps -e ../openlifu-sdk[crypto]
```
