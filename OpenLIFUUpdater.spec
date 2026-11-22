# SPDX-License-Identifier: Apache-2.0
# OpenLIFUUpdater.spec: one-file builds of the updater.
#
#   .\build.ps1      ->  dist\OpenLIFUUpdater.exe       (window)
#                        dist\openlifu-fw-updater.exe   (command line)
#
# Each exe carries the release embedded in src/openlifu_fw_updater/release
# (scripts/fetch_release.py), the SDK's trusted public keys and its libusb.
# They are analysed separately so the command-line exe carries no Qt. Qt is
# Qt for Python (PySide6, LGPL-3.0); PyQt (GPL) is excluded from both.
from pathlib import Path

import openlifu_fw_updater
import openlifu_sdk
from PyInstaller.building.build_main import EXE, PYZ, Analysis
from PyInstaller.utils.hooks import collect_submodules, copy_metadata

HERE = Path(SPECPATH)  # noqa: F821 - set by PyInstaller
SDK = Path(openlifu_sdk.__file__).parent
PKG = Path(openlifu_fw_updater.__file__).parent

datas = []
binaries = []

# --- the release: its zips and release.json, where release.find_release looks ---
release = sorted((PKG / "release").glob("*"))
if not (PKG / "release" / "release.json").is_file():
    raise SystemExit("[spec] src/openlifu_fw_updater/release holds no release.json: nothing "
                     "to update to. Embed one with: python scripts/fetch_release.py <tag>")
datas += [(str(p), "openlifu_fw_updater/release") for p in release if p.is_file()]
print("[spec] Bundling the release: " + ", ".join(p.name for p in release))

# --- the keys every image and preset package is verified against ---
keys = sorted((SDK / "keys").glob("*/*.pem"))
if len(keys) < 4:
    raise SystemExit(f"[spec] the installed openlifu-sdk ships {len(keys)} trusted key(s), "
                     "expected 4 (console and transmitter, research and clinical).")
datas += [(str(p), f"openlifu_sdk/keys/{p.parent.name}") for p in keys]
datas += [(str(SDK / "keys" / "PROVENANCE.txt"), "openlifu_sdk/keys")]

# --- libusb: LIFUDFU looks in <openlifu_sdk>/libusb/win64 (the SDK's package data) ---
libusb = SDK / "libusb" / "win64" / "libusb-1.0.dll"
if not libusb.is_file():
    raise SystemExit(f"[spec] the SDK's libusb is missing: {libusb}")
binaries.append((str(libusb), "openlifu_sdk/libusb/win64"))

# So the log can name the versions the exe was built from.
datas += copy_metadata("openlifu-sdk") + copy_metadata("openlifu-fw-updater")

# LIFUReleaseUpdate imports the DFU modules inside its functions.
hidden = collect_submodules("openlifu_sdk.io") + [
    "serial", "serial.tools", "serial.tools.list_ports",
    "usb", "usb.core", "usb.util", "usb.backend.libusb1",
]

# Never GPL Qt; never the SDK's ui package (it follows PyQt6 when present).
NEVER = ["PyQt6", "PyQt5", "PySide2", "openlifu_sdk.ui",
         "tkinter", "matplotlib", "IPython", "pytest", "PIL"]

ASSETS = [(str(p), "openlifu_fw_updater/assets") for p in sorted((PKG / "assets").glob("favicon.*"))]
if len(ASSETS) < 2:
    raise SystemExit("[spec] assets/favicon.ico and assets/favicon.png are required.")
ICON = str(PKG / "assets" / "favicon.ico")


def build(entry, name, console, extra_datas, excludes):
    a = Analysis(
        [str(HERE / "packaging" / entry)],
        # SDK.parent: an editable SDK install is not found by analysis otherwise.
        pathex=[str(PKG.parent), str(SDK.parent)],
        binaries=binaries,
        datas=datas + extra_datas,
        hiddenimports=hidden,
        excludes=NEVER + excludes,
        noarchive=False,
        optimize=0,
    )
    return EXE(
        PYZ(a.pure), a.scripts, a.binaries, a.datas, [],
        name=name,
        console=console,
        icon=ICON,
        upx=False,   # Qt and libusb byte for byte as shipped upstream
    )


gui = build("gui_entry.py", "OpenLIFUUpdater", False, ASSETS, [])
cli = build("cli_entry.py", "openlifu-fw-updater", True, [], ["PySide6", "shiboken6"])
