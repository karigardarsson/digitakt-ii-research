# TIME FOLD

An experimental oscillator I've been working on for Digitakt II.

Instead of changing the pitch, it warps the timing within the waveform itself and feeds some of its previous shape back into the next update. This lets the harmonics evolve while keeping the fundamental pitch stable.

Try holding a note and moving FOLD in either direction from 64. Raise MEM to retain more of the previous shape, or play around with DRIFT for more irregular movement.

I've tested it on my Digitakt II running a modified OS 1.17. I particularly liked the three-track pad and the different textures produced by the two folding directions.

I'm sharing the source and a host test here. Build information, test results and current limitations are in the technical notes.

The phase-map engine is my original work. The Digitakt II integration uses [DigiKit by Em D](https://github.com/ElekLabs/digikit) and reuses the SY FANG wrapper pattern.

Licensed under AGPL-3.0-or-later. See NOTICE.
