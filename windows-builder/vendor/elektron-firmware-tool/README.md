# elektron-firmware-tool

An unofficial C tool to inspect and modify Elektron instrument OS files
(`.syx`). Not affiliated with or endorsed by Elektron.

## What it does

An OS `.syx` is a SysEx transport wrapping a container of compressed sections.
The tool can:

- **inspect**: report the layers and check the stored checksums
- **decompress**: write each section out as a raw file
- **modify**: replace a section (or set the version string) and recompute the
  checksums so the rebuilt `.syx` stays consistent

Container formats: ELE3, ELE2, ELEK, AD, and the older pre-ELE
(Machinedrum / Monomachine).

## Build

```sh
make
```

## Usage

```
elektron-firmware-tool -i <in.syx>                             summary + checksum verdict
elektron-firmware-tool -i <in.syx> -v                          full report, every layer
elektron-firmware-tool -i <in.syx> [-d N]... -o <outdir>       decompress sections
elektron-firmware-tool -i <in.syx> [-c N <file>]... [-V VER] -o <out.syx>   rebuild
```

Without `-o`, the default output is a one-glance summary listing the section ids
you can pass to `-d` and `-c`; `-v` adds the full per-layer report. Each `-c`
takes exactly one id and one file, and may be repeated.

`-h` lists all options.

### Compression level

`-l N` trades rebuild time against output size by bounding how many match
candidates the packer examines per position. `0` is fastest, `3` (the default)
smallest; the range spans roughly 16x in time for about 4% in size. Every level
produces a valid stream.

`-l` only affects `-c`. Inspecting and decompressing are unaffected.

### Emitting the container

`--emit-container F` writes the bin layer — the container inside the SysEx
transport — to `F`, in addition to whatever the command already does. With `-i`
it is the container as parsed from the input; with `-c`/`-V`/`-r` it is the
rebuilt one, taken at the point the transport encoder receives it, so for ELE3
it already has the version and the HMAC trailer applied. The two agree
byte-for-byte on an unmodified rebuild.

## Byte-exact repacking

`-r` rebuilds a file without changing anything. Sections are copied verbatim
rather than recompressed, so the whole pipeline — transport decode, container
parse, container assembly, transport encode — has to be lossless for the output
to match, which makes it a useful self-check:

```sh
elektron-firmware-tool -i OS.syx -r -o repacked.syx && cmp OS.syx repacked.syx
```

This reproduces 22 of the 23 images tested exactly. The one exception has a
stray `0x0A` after its final `F7`, outside the SysEx stream altogether, and is
byte-identical up to that.

Note that a file rebuilt with `-c` is *not* byte-identical even if the
replacement matches what was there, because this packer makes different (better)
choices than the one Elektron used. What `-c` preserves is the decompressed
content of every section; `-r` is what preserves the bytes.

## Structure

Split by protocol layer, following the path a file takes: transport → container
→ section → codec. Each layer's format constants live in its own header.

| | |
|---|---|
| `transport.[ch]` | SysEx framing, 8-in-7 and 2+7+7 packing, decode and encode |
| `container.[ch]` | ELE3 / ELE2 / ELEK / AD / pre-ELE parsing and assembly |
| `aplib.[ch]` | the LZ77 + Elias-gamma codec, both directions |
| `integrity.[ch]` | SHA-256, HMAC-SHA256, CRC-32, checksums, key derivation |
| `firmware.[ch]` | the loaded-file model, and every integrity check in one pass |
| `report.[ch]` | all output formatting; recomputes nothing |
| `device.[ch]` | product ids and names |
| `util.h` | byte order, alignment, buffer limits |
| `main.c` | command line |

## Notes

Intended for inspection, interoperability, research, preservation, and
owner-controlled experimentation with devices you own and firmware copies you
have lawfully obtained. This repository contains no Elektron firmware. Do not
redistribute original or modified Elektron firmware images, and comply with
applicable law and licence terms. Flashing modified firmware may render a
device unusable.

## License

MIT. See [LICENSE](LICENSE).
