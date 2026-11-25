# Recovering a unit over SWD

The updater never needs a debugger: research units update over USB, and the
clinical updater converts them to clinical over USB. This page is for the
cases the updater cannot handle:

- taking a **clinical** (locked) unit back to research, for an RMA or on the
  bench;
- putting a specific image on a **research** unit, e.g. the last field
  release, so it can be updated the way a tester or customer would;
- a board whose flash is blank or half written.

All of it is done over SWD with an ST-Link and OpenOCD. Taking a clinical
board back to research **mass-erases** it, user configuration included
(serial number, hardware ID, calibration). Save the configuration first
(step 1).

Verified on the bench on 2026-11-22: console (STM32F072) and transmitter
master and slave (STM32L443), from the clinical bootloader 1.1.0 back to
research, and research units back to the field release (console 1.2.6,
transmitter 2.0.8).

## What a clinical board looks like

| | Console (STM32F072) | Transmitter (STM32L443) |
|---|---|---|
| Readout protection | RDP level 1 | RDP level 1 |
| Write protection | WRP, first 64 KB (bootloader) | WRP1A/B, pages 0-31 (bootloader) |
| Other | | PCROP 0x08000400-0x080004FF (key area), with PCROP_RDP (erased by an RDP regression) |
| SWD | attaches normally | **disabled by the bootloader on every boot** (SWDIO/SWCLK set to inputs, DFU included): attach under reset only |
| User configuration | page 62 (0x0801F000) | page 127 (0x0803F800) |

RDP level 1 to level 0 is a hardware mass erase of the whole flash. It
cannot keep the configuration page.

## What you need

- An ST-Link (V2 or V3) wired **SWDIO, SWCLK, GND**, and for the transmitter
  **NRST** as well. The bench ST-Link sits on the board's test points.
- OpenOCD 0.12 (xPack) or STM32CubeProgrammer. The commands below are
  OpenOCD.
- The research production image of the firmware you want:
  `openlifu-console-fw-production.bin` / `openlifu-transmitter-fw-production.bin`
  from the firmware repository's GitHub release (research bootloader +
  research-signed application, at 0x08000000).
- The unit powered from its own supply. The ST-Link does not power it.

**Check the link before writing anything.** Read the same flash range twice
and compare (a locked chip reads as an error, not as data; do this on an
unlocked one):

```sh
openocd -f interface/stlink.cfg -c "transport select swd" -f target/stm32l4x.cfg \
  -c "reset_config none" -c init -c "dump_image a.bin 0x08000000 0x8000" -c shutdown
openocd ... -c "dump_image b.bin 0x08000000 0x8000" -c shutdown
cmp a.bin b.bin
```

If the two reads differ, the link is bad, most often the **ground lead**.
The signs are bytes shifted by one bit (0xC1 read as 0xE0), "Failed to write
memory at 0x2000xxxx", ST-Link status 0x18/0x19, and CubeProgrammer verify
mismatches, even at 200 kHz. Do not program over a link that fails this
check.

## 1. Save the user configuration

While the unit still runs its application, read each board's configuration
through the SDK and keep it:

```python
import json
from openlifu_sdk.io.LIFUInterface import LIFUInterface

itf = LIFUInterface()
saved = {"console": itf.hvcontroller.read_config().json_data,
         "transmitter": {m: itf.txdevice.read_config(module=m).json_data
                         for m in range(itf.txdevice.get_module_count())}}
json.dump(saved, open("unit-config.json", "w"), indent=2)
itf.close()
```

A unit that no longer runs an application cannot be read this way. Its
calibration then has to come from the production records.

## 2. Console: clinical to research

The console still attaches over plain SWD at RDP level 1.

1. **RDP 1 to 0** (mass erase). WRP survives this:

   ```sh
   openocd -f interface/stlink.cfg -c "transport select swd" -f target/stm32f0x.cfg \
     -c "reset_config none" -c init -c "mww 0x40015808 0x00001800" -c halt \
     -c "stm32f1x unlock 0" -c "stm32f1x options_load 0" -c shutdown
   ```

2. **Clear WRP**, keeping RDP level 0: erase the option bytes, write
   RDP = 0xAA back, reload them:

   ```sh
   openocd -f interface/stlink.cfg -c "transport select swd" -f target/stm32f0x.cfg \
     -c "reset_config none" -c init -c halt \
     -c "mww 0x40022004 0x45670123" -c "mww 0x40022004 0xCDEF89AB" \
     -c "mww 0x40022008 0x45670123" -c "mww 0x40022008 0xCDEF89AB" \
     -c "mww 0x4002200C 0x34" \
     -c "mmw 0x40022010 0x20 0" -c "mmw 0x40022010 0x40 0" -c "sleep 200" \
     -c "mmw 0x40022010 0 0x20" \
     -c "mmw 0x40022010 0x10 0" -c "mwh 0x1FFFF800 0x00AA" -c "sleep 50" \
     -c "mmw 0x40022010 0 0x10" \
     -c "mmw 0x40022010 0x2000 0" -c shutdown
   ```

   (FLASH_KEYR, FLASH_OPTKEYR, clear SR, OPTER + STRT, OPTPG + RDP byte,
   OBL_LAUNCH.)

3. **Check:** FLASH_OBR (0x4002201C) bits 2:1 = 0 (level 0) and FLASH_WRPR
   (0x40022020) = 0xFFFFFFFF:

   ```sh
   openocd ... -c init -c halt -c "mdw 0x4002201C" -c "mdw 0x40022020" -c shutdown
   ```

4. **Write the research production image** and verify it:

   ```sh
   openocd -f interface/stlink.cfg -c "transport select swd" -f target/stm32f0x.cfg \
     -c "reset_config none" -c init -c "mww 0x40015808 0x00001800" -c halt \
     -c "flash write_image erase openlifu-console-fw-production.bin 0x08000000 bin" \
     -c "verify_image openlifu-console-fw-production.bin 0x08000000 bin" \
     -c "reset run" -c shutdown
   ```

## 3. Transmitter: clinical to research

The clinical bootloader turns SWD off on every boot, so plain SWD fails with
"init mode failed", even in DFU. Attach **under reset** (NRST wired):

```sh
PRE='-f interface/stlink.cfg -c "transport select swd" -f target/stm32l4x.cfg
     -c "reset_config srst_only srst_nogate connect_assert_srst" -c init
     -c "reset halt" -c "mww 0xE0042008 0x00001800"'
```

(The last command freezes the watchdogs while halted.) Without NRST, the only
other way in is BOOT0 (PH3) high at power-up.

1. **Probe** the option bytes:

   ```sh
   openocd $PRE -c "mdw 0x40022020 1" -c "mdw 0x40022024 2" -c "mdw 0x4002202C 2" -c shutdown
   ```

   OPTR[7:0] is RDP (0xAA level 0, 0xCC level 2, anything else level 1);
   PCROP1SR/ER and WRP1AR/BR are off when start > end.

2. **Unlock in one option-programming step.** The L4 only lets PCROP shrink
   together with the RDP regression, so RDP, PCROP and WRP are written
   together, then launched:

   ```sh
   openocd $PRE \
     -c "mww 0x40022008 0x45670123" -c "mww 0x40022008 0xCDEF89AB" \
     -c "mww 0x4002200C 0x08192A3B" -c "mww 0x4002200C 0x4C5D6E7F" \
     -c "mww 0x40022010 0x0000C3FB" \
     -c "mmw 0x40022020 0x000000AA 0x000000FF" \
     -c "mww 0x40022024 0x0000FFFF" \
     -c "mmw 0x40022028 0x00000000 0x0000FFFF" \
     -c "mww 0x4002202C 0x000000FF" -c "mww 0x40022030 0x000000FF" \
     -c "mmw 0x40022014 0x00020000 0" -c "sleep 2000" -c "mdw 0x40022010 1" \
     -c "mmw 0x40022014 0x08000000 0" -c shutdown
   ```

   (FLASH_KEYR, FLASH_OPTKEYR, clear SR, OPTR.RDP = 0xAA, PCROP1SR = 0xFFFF,
   PCROP1ER end = 0 keeping PCROP_RDP, WRP1AR/BR off, OPTSTRT, OBL_LAUNCH.)
   The option reload mass-erases the chip, the PCROP area included. Probe
   again: RDP 0xAA, WRP and PCROP off.

3. **Power-cycle the unit.** A blank L4 boots ST's ROM bootloader, and the
   flash write that follows can fail with RAM write errors until the board
   has been power-cycled.

4. **Write the research production image** under reset:

   ```sh
   openocd $PRE -c "flash erase_sector 0 0 last" \
     -c "flash write_image openlifu-transmitter-fw-production.bin 0x08000000 bin" \
     -c "verify_image openlifu-transmitter-fw-production.bin 0x08000000 bin" \
     -c "reset run" -c shutdown
   ```

   A slave module is done the same way, one board at a time, with the
   ST-Link moved to its header.

## 4. A research board to a given image

A research board has no protection, so steps 2 and 3 do not apply. Write the
production image without erasing the configuration page, so the
configuration survives:

- console: erase pages 0-61 and 63 (page 63 is the anti-rollback floor), keep
  page 62;
- transmitter: erase pages 0-126 (page 126 is the floor), keep page 127.

```sh
openocd $PRE -c "flash erase_sector 0 0 126" \
  -c "flash write_image openlifu-transmitter-fw-production.bin 0x08000000 bin" \
  -c "verify_image openlifu-transmitter-fw-production.bin 0x08000000 bin" \
  -c "reset run" -c shutdown
```

Erasing the floor lets an older application in: the bootloader refuses
anything below the floor.

Field release images (the testers' starting point): console 1.2.6 (its
production image carries bootloader 1.0.3, reporting `1.0.3-rc.1`),
transmitter 2.0.8 (bootloader 1.0.1).

## 5. Restore the configuration

A freshly written application creates a default configuration (serial number
only, no calibration). Write the saved one back through the SDK:

```python
import json
from openlifu_sdk.io.LIFUInterface import LIFUInterface
from openlifu_sdk.io.LIFUUserConfig import LifuUserConfig

saved = json.load(open("unit-config.json"))
itf = LIFUInterface()
itf.hvcontroller.write_config(LifuUserConfig(json_data=saved["console"]))
for m, data in saved["transmitter"].items():
    itf.txdevice.write_config(LifuUserConfig(json_data=data), module=int(m))
itf.close()
```

Read it back and compare. The diathermy application refuses to start a
treatment without the transmitter calibration.

## 6. Check with the updater

Run the research updater for the release you want. It should plan the
expected steps (from the field release: both applications with their
bootloaders, then the presets) and finish with everything read back. A second
run must show everything up to date. To go clinical again, run the clinical
updater.

## Troubleshooting

| Symptom | Cause, fix |
|---|---|
| Transmitter: "init mode failed", target voltage fine | Clinical bootloader: attach under reset (NRST). Otherwise a loose lead. |
| Two reads differ; bytes off by one bit; RAM write errors at any speed | Ground lead. Fix the wiring first. |
| "Failed to write memory at 0x2000xxxx", status 0x18/0x19 after an unlock | Blank L4 in the ROM bootloader: power-cycle, then write under reset. |
| CubeProgrammer "Mass erase operation failed" | It may still have erased: read before assuming anything is left. |
| CubeProgrammer TrustZone / debug-authentication hint | Not relevant to these parts; the link is not working. |
| PCROP will not shrink on its own | It only shrinks together with the RDP regression (step 3.2). |
