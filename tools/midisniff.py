#!/usr/bin/env python3
"""Passively capture Elektron SysEx on a MIDI port and decode the envelope.

Read-only: this opens the device's MIDI *input* (the stream the device sends)
and never transmits anything. On macOS CoreMIDI a source can have several
listeners, so this runs alongside Elektron Transfer / Overbridge and captures
the device's replies to that app.

It reassembles SysEx, prints raw hex, and for any Elektron frame
(`F0 00 20 3C <dev> 00 <cmd> <b5> <b6> <b7> ... <ck_hi> <ck_lo> <len_hi>
<len_lo> F7`, see docs/MIDI-SYSEX-RPC.md) it splits off the four envelope
header bytes, 8-in-7 unpacks the body, and shows the MidiRpc header
(seq/len/type). The point is to read the four envelope bytes that static
analysis left open.

    uv run --with python-rtmidi python tools/midisniff.py --list
    uv run --with python-rtmidi python tools/midisniff.py --seconds 30
"""
import argparse
import sys
import time


def unpack_8in7(packed):
    """Inverse of the device pack (tools/... FUN_40152242): each group is one
    MSB-collector byte then up to 7 data bytes; data byte i takes its high bit
    from collector bit (6-i)."""
    out = bytearray()
    i = 0
    n = len(packed)
    while i < n:
        collector = packed[i]
        i += 1
        for j in range(7):
            if i >= n:
                break
            hi = (collector >> (6 - j)) & 1
            out.append((packed[i] & 0x7F) | (hi << 7))
            i += 1
    return bytes(out)


ELEKTRON = bytes((0x00, 0x20, 0x3C))


def decode_frame(msg):
    """Return a list of human lines describing one SysEx message."""
    lines = [f"  raw ({len(msg)}B): " + " ".join(f"{b:02x}" for b in msg[:64])
             + (" ..." if len(msg) > 64 else "")]
    if len(msg) >= 6 and msg[0] == 0xF0 and msg[1:4] == ELEKTRON:
        dev = msg[4]
        sep = msg[5]
        lines.append(f"  elektron envelope: dev=0x{dev:02x} sep=0x{sep:02x}")
        if len(msg) >= 10:
            cmd, b5, b6, b7 = msg[6], msg[7], msg[8], msg[9]
            lines.append(f"  >>> envelope header bytes: "
                         f"cmd=0x{cmd:02x} b5=0x{b5:02x} b6=0x{b6:02x} b7=0x{b7:02x}")
            # body is between the 10-byte header and the 5-byte trailer
            if len(msg) >= 15 and msg[-1] == 0xF7:
                packed = msg[10:-5]
                ck_hi, ck_lo, len_hi, len_lo = msg[-5], msg[-4], msg[-3], msg[-2]
                cksum = (ck_hi << 7) | ck_lo
                declared = (len_hi << 7) | len_lo
                calc = sum(b & 0x7F for b in packed) & 0x3FFF
                body = unpack_8in7(packed)
                lines.append(f"  trailer: cksum=0x{cksum:04x} "
                             f"(calc 0x{calc:04x} {'ok' if calc == cksum else 'MISMATCH'}) "
                             f"len_field={declared} (packed_body+5={len(packed) + 5})")
                lines.append(f"  packed body ({len(packed)}B) -> unpacked ({len(body)}B): "
                             + " ".join(f"{x:02x}" for x in body[:48])
                             + (" ..." if len(body) > 48 else ""))
                if len(body) >= 5:
                    seq = (body[0] << 8) | body[1]
                    rlen = (body[2] << 8) | body[3]
                    typ = body[4]
                    kind = "response" if typ & 0x80 else "request"
                    lines.append(f"  MidiRpc: seq={seq} len={rlen} "
                                 f"type=0x{typ:02x} ({kind}, family 0x{typ & 0x7F:02x})")
    elif len(msg) >= 3 and msg[0] == 0xF0 and msg[1] == 0x7E:
        lines.append(f"  universal non-realtime (SDS): "
                     + " ".join(f"{b:02x}" for b in msg))
    return lines


def main():
    import rtmidi

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="list ports and exit")
    ap.add_argument("--port", default="Digitakt",
                    help="substring of the input port name (default: Digitakt)")
    ap.add_argument("--seconds", type=float, default=30.0,
                    help="capture duration")
    ap.add_argument("--max", type=int, default=200,
                    help="stop after this many SysEx messages")
    args = ap.parse_args()

    mi = rtmidi.MidiIn()
    ports = mi.get_ports()
    if args.list:
        print("MIDI input ports:")
        for i, n in enumerate(ports):
            print(f"  {i}: {n}")
        return 0

    idx = next((i for i, n in enumerate(ports) if args.port.lower() in n.lower()),
               None)
    if idx is None:
        print(f"no input port matching {args.port!r}; have: {ports}", file=sys.stderr)
        return 1

    mi.open_port(idx)
    mi.ignore_types(sysex=False, timing=True, active_sense=True)
    print(f"listening on {ports[idx]!r} for {args.seconds:g}s "
          f"(passive, nothing is transmitted) -- drive Elektron Transfer now")

    count = 0
    end = time.time() + args.seconds
    while time.time() < end and count < args.max:
        m = mi.get_message()
        if not m:
            time.sleep(0.002)
            continue
        data, _dt = m
        if not data or data[0] != 0xF0:
            continue  # only care about SysEx here
        count += 1
        print(f"\n[{count}] t={time.strftime('%H:%M:%S')}")
        for line in decode_frame(bytes(data)):
            print(line)

    mi.close_port()
    print(f"\ndone: {count} SysEx message(s) captured")
    return 0


if __name__ == "__main__":
    sys.exit(main())
