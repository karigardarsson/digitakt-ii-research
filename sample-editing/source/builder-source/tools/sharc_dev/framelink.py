"""The ColdFire-to-SHARC frame link of each known MAIN OS image.

Tools look a profile up by the SHA-256 of the MAIN OS image, so a snapshot or
dump of another image stops them instead of using wrong addresses. The
addresses are in docs/findings/04-coldfire-dsp-link.md: "The ColdFire tells
the SHARC through a periodic DSPI2 frame", "The frame capture runs; the frame
build is switched off" and, for 1.16, "The frame link on Digitakt II 1.16".

Profile keys: name; vector, the frame interrupt; handler, the interrupt
handler that builds the frame; driver, the DSPI2 driver it calls; counter,
the handler's pacing counter; gate, countdown, mode and stop, the frame-gate
variables (writing 0 to gate opens it); stop_writer, the instruction that
sets stop, outside any function; tables.
"""

import hashlib

# (base, row size, rows, name): the DSPI2 buffers and the per-track tables the
# frame handler reads when it builds the frame, plus the two flag arrays the
# MIDI flag writer sets when a track becomes MIDI. The same in 1.15C and 1.16.
TABLES = (
    (0x80005348, 0x802, 1, "tx_frame"),
    (0x8000488C, 0xABC, 1, "rx_frame"),
    (0x800047FC, 4, 16, "track_long"),
    (0x80003CD0, 0x9A, 16, "track_9a"),
    (0x80005B50, 0x8E, 16, "track_8e"),
    (0x80004684, 4, 16, "midi_flag_a"),
    (0x800046C4, 4, 16, "midi_flag_b"),
)

# Profile keys that name one long variable.
VARIABLES = ("counter", "gate", "countdown", "mode", "stop")

PROFILES = {
    "6a6a887b0573a557b71badf32cd9392777c60b4d1f33dfae12bb8346a014a37b": {
        "name": "Digitakt II 1.15C",
        "vector": 191,
        "handler": 0x4002D652,
        "driver": 0x400CF9C4,
        "counter": 0x4028AC90,
        "gate": 0x4094E4F4,
        "countdown": 0x4094E4F0,
        "mode": 0x4094E4F8,
        "stop": 0x4094E4EC,
        "stop_writer": 0x4002D632,
        "tables": TABLES,
    },
    "57bb4dfa8df07d846adc72fdb4fb0d3cd3c5680c524bf498338460207e008e7d": {
        "name": "Digitakt II 1.16",
        "vector": 191,
        "handler": 0x4002DD0C,
        "driver": 0x400CD2BC,
        "counter": 0x402A1488,
        "gate": 0x409664F4,
        "countdown": 0x409664F0,
        "mode": 0x409664F8,
        "stop": 0x409664EC,
        "stop_writer": 0x4002DCE2,
        "tables": TABLES,
    },
}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def profile_for(image):
    """-> (sha256, profile) for a MAIN OS image file; SystemExit if it has none."""
    sha = sha256_file(image)
    if sha not in PROFILES:
        raise SystemExit(
            "%s (sha-256 %s) has no frame-link profile; known: %s"
            % (image, sha, ", ".join(p["name"] for p in PROFILES.values()))
        )
    return sha, PROFILES[sha]
