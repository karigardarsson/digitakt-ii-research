# Restore official stock OS

[All-in-One installation](INSTALLATION.md) · [Practical HOW TO](DIGITAKT_II_ALL_IN_ONE_USER_GUIDE.md) · [v0.3.0 release](https://github.com/karigardarsson/digitakt-ii-research/releases/tag/v0.3.0-beta)

Keep a known-good official Digitakt II stock `.syx`, a physical MIDI interface, and a MIDI cable ready before installing unofficial firmware.

1. Connect the interface's **MIDI OUT** to Digitakt II **MIDI IN**.
2. Hold **FUNC** while powering on Digitakt II.
3. Press **TRIG 4** for **OS UPGRADE**.
4. Open Elektron Transfer and go to the **SYSEX TRANSFER** page.
5. Choose **OS upgrade via device startup menu**.
6. Select the official stock Digitakt II OS file and follow the instructions.
7. Do not interrupt power until the update finishes and the instrument reboots.

USB MIDI cannot be used for this startup-menu route. It requires physical MIDI DIN.

Official reference: [Elektron — update via SysEx transfer](https://support.elektron.se/support/solutions/articles/43000662701-how-to-update-your-device-via-sysex-transfer).

Recovery is not guaranteed if the startup menu or bootloader cannot run, power is lost during a critical write, or storage or hardware is damaged.
