# Sample Fusion V2 — Repair4 hardware-evaluation guide

Use the existing **FUS2 / Sample Fusion 2**, machine type14. Repair4 fixes it;
it does not add another machine or repackage the unchanged original TWIN. Original
TWIN (12) and FUSE (13) remain separately selectable. Existing FUSE Sounds
retain their original controls and processing; they are not silently converted.
Both FUSE and FUS2 retain private note-off ownership. TWIN remains unchanged.

## Controls

Press SRC again for the second page. Standard AMP, FILTER, FX and track-level
controls remain available. Begin at a LOW track level: Repair4 removes the
unintended SRC-level attenuation from the response field. A saved FUS2 Sound
can therefore be louder than Repair3 at identical VOL. No stock gain control
or limiter has been removed, and saved raw values are not rewritten.

| Page | Knob | Label | Purpose |
| --- | --- | --- | --- |
| 1 | A | SMPA | Project sample A and its F:/R: direction |
| 1 | B | TUNA | A tuning, native Elektron graphical representation |
| 1 | C | SYNC | OFF / MATCH / REL |
| 1 | D | FUSE | BLND / RING / DUCK / MRPH / GATE |
| 1 | E | SMPB | Project sample B and its F:/R: direction |
| 1 | F | TUNB / TRAN | B tuning when OFF; relative transpose when REL; blank in MATCH |
| 1 | G | BAL / WET / DEP / POS | Mode-dependent amount |
| 1 | H | blank | No duplicated response control |
| 2 | A | ST.A | Independent A playback-start offset |
| 2 | B | ST.B | Independent B playback-start offset |
| 2 | C | DRY / RCVR / AUTO | Mode-dependent response; blank in BLND |

Direction retains the established encoding: F:0–1023, then R:0–1023.
Signed sample selection was not substituted: this preserves the full slot
space, native discrete selection and persistence representation.

### Amount and response

| Mode | Page1 amount | Page2 response |
| --- | --- | --- |
| BLND | A → B balance | Not used |
| RING | Clean → ring, with a gentler squared wet-depth curve | Clean A → B balance |
| DUCK | DEP: depth of A suppressing B | RCVR: recovery toward dry B after A falls, about 10–2000 ms |
| GATE | DEP: depth of A opening B | RCVR: closure after A falls, about 10–2000 ms |
| MRPH | Explicit A → B position | 0 = manual; positive values give an automatic sweep, approximately 10 ms → 8 s |

In DUCK/GATE, **A is the sidechain, not an additional audible dry layer**.
DEP 0 gives normal B playback, independently of RCVR. Full-depth DUCK suppresses
B while A is strong; GATE is its inverse. A now keeps looping as the key in
DUCK/GATE even with SYNC OFF. Use a percussive A with gaps and a longer B,
DEP high and short RCVR to hear clear pumping. A sustained, nearly constant A
naturally gives sustained suppression/opening; recovery is heard when A falls,
not as a separate source of motion. There is a 5 ms detector hold.

For RING start at WET 0 and DRY near the middle; increase WET gradually.
At WET 100% there is no clean component, so DRY intentionally has no effect.

For controlled MRPH stages set AUTO to OFF, then p-lock or LFO-modulate POS.
For a one-way sweep, set POS to the starting balance and AUTO to a fade time.
Positive AUTO keeps short A alive during the fade. The clock starts on entry
to MRPH or on retrigger, not invisibly in other modes. These are crossfades between interacting native
sample streams, not spectral morphing.

### Starts and loops

ST.A/ST.B are 0–127 offsets **from the playback edge in the chosen direction**.
Zero starts at the normal forward beginning or reverse end. Higher values move
into the sample. The range reserves the native 141-frame minimum window;
short samples therefore do not have an exact 0–100% correspondence.

Example: use the same piano in both slots, A forward at ST.A 0, B reversed
around ST.B 44/127. That starts B near 65% through the original waveform.
Native start/length boundaries are reused; there is no replacement reverse
renderer. B loops within its window while held. Note-off disables looping until
the next trigger, allowing the native end/release behavior rather than a timeout.
AMP/FX tails remain governed by the instrument's existing downstream processing.
A remains a one-shot in BLND, RING and manual MRPH; it loops only in the
participating key/automatic-fade modes above. Native note-off disables BOTH
loops until retrigger. A mode change cannot resurrect a released or invalidated
sample record. Live entry into a key/fade mode can restart an already-ended A
only while its original resident sample metadata remains valid and B is active.

### SYNC is varispeed, not elastic stretching

- OFF: native independent tuning and playback rates.
- MATCH: fit B's remaining duration to A, following A's tuning. B tuning is hidden.
- REL: the same fit plus B's TRAN setting, a transpose relative to A's tuning.

INFO uses the stable title "B sync" with a full value explanation:
"Independent", "Fit to A" or "Fit+transpose". The small knob keeps its
short OFF/MATCH/REL label. INFO explanations therefore follow the current value
immediately, rather than retaining the description from before an encoder edit.

FUS2 computes the ratio from the remaining native playback windows, including
ST.A/ST.B; the extra rate adjustment is bounded to 0.25–4×. Extreme duration
ratios therefore cannot be matched exactly. REL transposition intentionally
changes the duration relationship, and native pitch limits still apply.
No pitch-preserving time-stretch system is included or implied.

## Auditions and evidence

Repair4 audio reels play the Repair3 MODEL first, then the Repair4 MODEL.
Inputs are deterministic generated sounds, not instrument recordings or user
samples. Shared compiled C uses host interpolation and explicit playback/loop
policies; old unintended SRC-level attenuation is modeled from separate native
AMP measurements. There is no per-variant normalization. Four sample pairings,
continuous/retriggered playback and SYNC OFF/ON are measured in music-report.json.
The contrasting-tone MORPH reel separates A at 240 Hz from B at 960 Hz, so the
transition can be distinguished from simple loudness change.

The delivered validation distinguishes native-code/emulator tests from these
host previews and from hardware. A reproduced V1 release-byte lifetime defect
is corrected; complete native frame/AMP STOP/repeated-STOP/retrigger tests are
included. **This does not conclusively identify every cause of the user's
intermittent physical STOP symptom. Hardware confirmation remains necessary.**

Sound-v3 and Pattern payload converters round-trip exactly, including both new
start fields. Real Project filesystem reload/power-cycle, sample collect/purge,
slot remapping, secondary-sample portability, physical controls/Song Mode and
maximum silicon track load are not established by those offline tests.
Use already loaded project slots and a disposable/copied project for evaluation;
do not rely on collect/purge or slot reordering for secondary-sample protection.

This is a hardware-evaluation candidate, not a claim of production reliability
or musical hardware success. No hardware operation was performed to create it.
