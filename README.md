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

This first version handles **research releases on research units**. Converting a research
unit to clinical, and clinical presets (which are written through the bootloaders), are
refused with a message for now.

## The window: `OpenLIFUUpdater`

```
pip install -e .[gui]
OpenLIFUUpdater                                   # the embedded release
OpenLIFUUpdater --firmware firmware-v0.2.0-rc8.zip --presets research-presets-v0.2.0-rc8.zip
OpenLIFUUpdater --release-dir <folder with release.json>
OpenLIFUUpdater --self-test                       # build the window, verify the release, load libusb; no unit
```

One window, one table:

| Device | Installed | This release | Status |
|---|---|---|---|
| Console, Transmitter master, Transmitter module N | the version the board reports | the release's firmware tag (`firmware-lock.json`) | Up to date / Update / Update (replaces bootloader) / Refused |
| Console presets, Transmitter presets (master, module N) | how many of the released packages it holds ("13 of 13") | diathermy commit and context | Up to date / Update (writes 13, clears 1) / Refused |

- **Update (replaces bootloader)**: the board runs firmware from before its current bootloader
  (console below 1.3.0, transmitter below 2.1.0), so its 1.0.x bootloader is replaced on the
  way. The confirmation says so; it cannot be undone.
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
- Exit status: 0 for done or nothing to do, 1 for a failure or read-back mismatch, 2 for a
  refusal or a bad release.

### Embedding a release

```
python scripts/fetch_release.py v0.2.0-rc8
```

This downloads the release's firmware and research preset zips with the GitHub CLI and checks
them against the release's published digests. It writes them to `src/openlifu_fw_updater/release/`,
which is git-ignored. `openlifu-fw-updater` with no arguments then installs that release.

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
