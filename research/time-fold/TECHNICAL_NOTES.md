# TIME FOLD technical notes

## Run the host test

From this folder, build the portable C99 engine. The Python probe requires NumPy.

```sh
cc -std=c99 -O2 -Wall -Wextra -Werror -fPIC -shared -o /tmp/fold-host.so source/fold.c
python probe.py /tmp/fold-host.so
```

With MSYS2 UCRT64 on Windows, build to a relative path and run the probe using a Python environment with NumPy installed.

```text
gcc -save-temps=obj -std=c99 -O2 -Wall -Wextra -Werror -shared -o fold-host.dll source/fold.c
python probe.py fold-host.dll
```

The host probe checks the neutral sine, phase-map monotonicity and inversion, persistence with feedback, DRIFT at matched carrier phase, and the harmonic spectrum at two pitches. In the reference run, two different histories followed by the same control settings differed by 0.240 RMS early with MEM at maximum and 0.029 RMS later. With MEM at zero the difference was zero.

## Device prototype and evidence

The engine stores two 33-point maps and projects the warped cycle onto up to eight harmonics. NEST changes the subdivision depth and clock ratio. CLOCK sets the parent rate and DRIFT varies the timing of both clocks. The SHARC emitter and native adapter use the existing DigiKit inline voice and parameter interfaces.

Reproducing the device firmware requires the original DigiKit and Selache integration environment and a separately supplied stock OS 1.17 image. This snapshot supports the standalone host test.

- **Emulator.** The archived candidate passed cold loading, native SRC values and locks, Project recall, and the control-frame-to-audio path. The staggered 16-track case reached 82.75% in the conservative instruction-cost model over 33 callbacks. Hardware timing and headroom remain unmeasured.
- **Hardware listening.** The author heard the three-voice pad on A01, the same-pitch inverse and forward plucks on A02, and the matched DRIFT comparison on A03 and A04. The installed image hash was not read back from the device.
- **Open.** Power-cycle Project recall and compatibility with other firmware modifications.

The offline test image is identified by SHA-256 `84d5eaa53aacaa0bb63ba639fe24bf3f4c591facc003fd0daac412f406b3297a`. The [source checksums](source-sha256.json) identify the copied instrument sources.
