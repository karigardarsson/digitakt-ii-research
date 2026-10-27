# Plaits, 48k

The DSP routine of the PLAITS machine (modules/machines/plaits) for core/dsp-inline. This build renders PLAITS at 48 kHz straight into the voice's output; the stock decimator is skipped. This is the default build (image `out/patch/plaits-va-48k-1.17.syx`).

- Devices and firmware: Digitakt II 1.17.
- Select this module (it pulls in machines/plaits, core/params and core/dsp-inline); machines/plaits-48k and
  machines/plaits-96k conflict, pick one.
- The sources live in modules/machines/plaits/src/dsp: the entry shim plaits_entry.s (here assembled with
  src/dsp/mode.s first) and the C voice wrapper, VA engine and half-band filter, compiled by Selache's C compiler and
  merged into one object by pk_build.

The gallery shows this text. Keep it free of firmware bytes and vendor tool names.
