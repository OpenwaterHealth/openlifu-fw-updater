# SPDX-License-Identifier: Apache-2.0
"""A release signed with throwaway keys the SDK is made to trust, and a fake
unit (console + transmitter chain) that keeps firmware headers and preset
slots. Copied from openlifu-sdk unit-test/test_release_update.py, which a
package cannot import; keep the two in step."""

from __future__ import annotations

import hashlib
import json

from openlifu_sdk.io import LIFUCrypto
from openlifu_sdk.io import LIFUPresetPackage as pp
from openlifu_sdk.io import LIFUReleaseUpdate as ru
from openlifu_sdk.io.component import FirmwareDomain

COMMIT = "76" * 20
OLD_COMMIT = "83" * 20
RELEASED = 3
LOCK = {"firmware": {"console-fw": {"tag": "1.3.0-rc.4"},
                     "transmitter-fw": {"tag": "2.1.0-rc.7"}}}


def make_keys(tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    out = {}
    for board in LIFUCrypto.FIRMWARE_BOARDS:
        for domain in LIFUCrypto.FIRMWARE_DOMAINS:
            key = ec.generate_private_key(ec.SECP256R1())
            d = tmp_path / "keys" / board / domain
            d.mkdir(parents=True)
            (d / "ecdsa_private.pem").write_bytes(key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption()))
            out[board, domain] = LIFUCrypto.SigningKeys.from_directory(d)
    return out


def _sums(folder):
    lines = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(folder).as_posix()}"
             for p in sorted(folder.rglob("*")) if p.is_file() and p.name != "SHA256SUMS"]
    (folder / "SHA256SUMS").write_text("\n".join(lines) + "\n")


def app_image(keys, board, domain, version, body=b"app"):
    return LIFUCrypto.sign_firmware_bytes(body * 64, keys[board, domain], version)


def make_firmware(root, keys, domain="research", *, tx=("2.1.0", b"tx-rc8"),
                  console=("1.3.0", b"hv-rc8")):
    fw = root / "firmware"
    for board, folder, (version, body) in (
            ("console", "openlifu-console-fw", console),
            ("transmitter", "openlifu-transmitter-fw", tx)):
        (fw / folder).mkdir(parents=True)
        (fw / folder / f"{folder}-signed.bin").write_bytes(
            app_image(keys, board, domain, version, body))
    for board, folder, version, bl in (
            ("console", "open-lifu-console-bl-updater", "1.2.31", "1.1.0-rc.3"),
            ("transmitter", "open-lifu-transmitter-bl-updater", "2.0.31", "1.1.0-rc.2")):
        (fw / folder).mkdir(parents=True)
        (fw / folder / f"{folder}-signed.bin").write_bytes(
            app_image(keys, board, "research", version, b"shim"))
        (fw / folder / f"{folder}.cdx.json").write_text(json.dumps(
            {"components": [{"name": ru.BOOTLOADER_COMPONENTS[board], "version": bl}]}))
        _sums(fw / folder)
    (fw / "firmware-lock.json").write_text(json.dumps(LOCK))
    _sums(fw)
    return fw


def preset_package(keys, board, domain="research", commit=COMMIT, body=b"preset"):
    if board == "console":
        return pp.build_preset_package(body * 8, diathermy_sha=commit, context=domain,
                                       keys=keys[board, domain])
    return pp.build_tx_preset(body * 8, diathermy_sha=commit, context=domain,
                              keys=keys[board, domain])


def make_presets(root, keys, domain="research", commit=COMMIT, n=RELEASED):
    d = root / "presets"
    d.mkdir()
    (d / "console-presets.bin").write_bytes(preset_package(keys, "console", domain, commit))
    entries = []
    for slot in range(n):
        (d / f"transmitter-slot{slot:02d}-module0.bin").write_bytes(
            preset_package(keys, "transmitter", domain, commit, body=bytes([slot]) * 8))
        entries.append({"slot": slot, "id": f"p{slot}", "modules": 1})
    (d / "presets-manifest.json").write_text(json.dumps(
        {"context": domain, "signed_with": domain, "diathermy_sha": commit, "presets": entries}))
    _sums(d)
    return d


class FakeBoard:
    """Firmware reads for one or more modules: version, domain, header."""

    def __init__(self, images, domain="research"):
        self.images = list(images)
        self.domains = [domain] * len(self.images)
        self.connected = True

    def get_version(self, module=0):
        return LIFUCrypto.FirmwareHeader.from_bytes(self.images[module]).fw_version_str

    def firmware_domain(self, module=0):
        return FirmwareDomain(self.domains[module])

    def firmware_header(self, module=0):
        return LIFUCrypto.FirmwareHeader.from_bytes(self.images[module])

    def is_connected(self):
        return self.connected

    def connect(self):
        return self.connected


class FakeHv(FakeBoard):
    def __init__(self, image, preset=None):
        super().__init__([image])
        self.preset = preset
        self.log = []

    def get_preset_info(self):
        return pp.parse_preset_header(self.preset) if self.preset else None

    def write_presets(self, package):
        self.preset = package
        self.log.append("write console")

    def turn_12v_off(self):
        self.log.append("12v off")

    def turn_12v_on(self):
        self.log.append("12v on")


class FakeTx(FakeBoard):
    def __init__(self, images, slots=None):
        super().__init__(images)
        self.slots = slots or {m: {} for m in range(len(images))}
        self.log = []

    def get_module_count(self):
        return len(self.images)

    def get_preset_info(self, slot, module=0):
        data = self.slots[module].get(slot)
        return pp.parse_preset_header(data) if data else None

    def write_preset(self, slot, data, module=0):
        self.slots[module][slot] = data
        self.log.append(("write", module, slot))

    def delete_preset(self, slot, module=0):
        self.slots[module].pop(slot)
        self.log.append(("delete", module, slot))


def unit(keys, release, *, tx_version="2.1.0", tx_body=b"tx-rc8", modules=2, commit=COMMIT,
         domain="research", hv_version="1.3.0", hv_body=b"hv-rc8"):
    """A unit whose firmware is built from *tx_body* / *hv_body* (the
    release's are "-rc8"; the release's exact image when they match) and
    whose presets are the release's own packages, or packages of another
    diathermy *commit*."""
    def image(board, version, body):
        rel = release.apps[board]
        if (version, body) == (rel.version, b"tx-rc8" if board == "transmitter" else b"hv-rc8"):
            return rel.path.read_bytes()
        return app_image(keys, board, domain, version, body)

    def tx_preset(slot):
        if commit == COMMIT and domain == "research":
            return release.presets.tx_image(slot, 0)
        return preset_package(keys, "transmitter", domain, commit, body=bytes([slot]) * 8)

    slots = {m: {s: tx_preset(s) for s in range(RELEASED)} for m in range(modules)}
    tx = FakeTx([image("transmitter", tx_version, tx_body)] * modules, slots)
    tx.domains = [domain] * modules
    console = (release.presets.console if commit == COMMIT and domain == "research"
               else preset_package(keys, "console", domain, commit))
    hv = FakeHv(image("console", hv_version, hv_body), console)
    hv.domains = [domain]
    return hv, tx


class FakeUpdaters:
    """Stands in for LIFUFirmwareUpdate / LIFUTransmitterFirmwareUpdate:
    records the order, installs the release image on the fake board."""

    def __init__(self, release, hv, tx):
        self.order = []
        rel = self
        rel_app = {b: i.path.read_bytes() for b, i in release.apps.items()}

        class Console:
            def __init__(self, hv=None, **_):
                pass

            def update(self, progress_callback=None, **_):
                rel.order.append("console")
                if progress_callback:
                    progress_callback(512, 1024, "USB DFU write")
                hv.images[0] = rel_app["console"]
                return type("R", (), {"summary": "console updated"})()

        class Transmitter:
            def __init__(self, tx=None, **_):
                pass

            def update_slave(self, module, **_):
                rel.order.append(f"module {module}")
                tx.images[module] = rel_app["transmitter"]
                return type("R", (), {"summary": f"module {module} updated"})()

            def update(self, **_):
                rel.order.append("master")
                tx.images[0] = rel_app["transmitter"]
                return type("R", (), {"summary": "master updated"})()

        self.Console, self.Transmitter = Console, Transmitter


class FakeInterface:
    """LIFUInterface's surface the updater uses."""

    def __init__(self, hv, tx):
        self.hvcontroller, self.txdevice = hv, tx
        self.closed = False

    def is_device_connected(self):
        return self.txdevice.is_connected(), self.hvcontroller.is_connected()

    def close(self):
        self.closed = True
