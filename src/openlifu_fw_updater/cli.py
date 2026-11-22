# SPDX-License-Identifier: Apache-2.0
"""``openlifu-fw-updater``: take the connected unit to a release.

Without arguments it installs the release embedded in this build. With
``--firmware`` (and ``--presets``) it installs those files instead: a
diathermy release's ``firmware-<tag>.zip`` and ``<context>-presets-<tag>.zip``,
zipped or unpacked. ``--plan`` shows what would change and writes nothing.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from openlifu_fw_updater.release import ReleaseFiles, embedded_release_dir, find_release


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="openlifu-fw-updater",
        description="Take the connected Open-LIFU unit to a release: the console and "
                    "transmitter firmware, then the presets, then a read-back.")
    parser.add_argument("--firmware", type=Path,
                        help="firmware-<tag>.zip (or its folder) instead of the embedded release")
    parser.add_argument("--presets", type=Path,
                        help="<context>-presets-<tag>.zip (or its folder); needs --firmware")
    parser.add_argument("--plan", action="store_true",
                        help="read the unit, show what would change, write nothing")
    parser.add_argument("-y", "--yes", action="store_true", help="do not ask before writing")
    parser.add_argument("--log-level", default="WARNING",
                        choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    args = parser.parse_args(argv)
    if args.presets and not args.firmware:
        parser.error("--presets needs --firmware")
    return args


def release_files(args: argparse.Namespace) -> ReleaseFiles:
    if args.firmware:
        return ReleaseFiles(tag="(from the command line)", firmware=args.firmware,
                            presets=args.presets)
    return find_release(embedded_release_dir())


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    logging.basicConfig(level=args.log_level, format="%(levelname)s %(name)s: %(message)s")
    # Imported here so --help works without the SDK's hardware stack.
    from openlifu_sdk.io.LIFUReleaseUpdate import ReleaseBundle, ReleaseError, run_cli

    try:
        files = release_files(args)
        print(f"Release {files.tag}")
        bundle = ReleaseBundle.load(files.firmware, files.presets)
        return run_cli(bundle, plan_only=args.plan, yes=args.yes)
    except (FileNotFoundError, ReleaseError) as e:
        print(f"ERROR: {e}")
        return 2
