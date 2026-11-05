# Sample Editing Pack — HOW-TO

Load, chop, trim and save samples, zoom into waveforms and smooth SLICE loops.
This guide is for the Sample Editing Pack on **Digitakt II OS 1.17**.

- [SampleLAB: load, edit and save](#samplelab-load-edit-and-save)
- [SmartSlice](#smartslice)
- [Universal Zoom](#universal-zoom)
- [CFADE](#cfade)

## SampleLAB: load, edit and save

### Open SampleLAB

After recording, stop at the **TRIM** screen and **push encoder A** at **A:LAB**.
The normal recording and SAVE controls remain available.

![A:LAB on stopped recording TRIM](images/6x/samplelab-trim-entry.png)

To load a saved sample, use **B:LOAD** from idle RECORD or stopped TRIM.
From inside SampleLAB, **push D** to open SOURCE, then:

1. Select **LOAD FROM +DRIVE** with UP/DOWN and press YES.
2. Browse to your sample and press YES to load it.
3. The sample opens as **WHOLE 01/01**, ready to edit.

![SOURCE menu](images/6x/drive-source.png)
![LOAD FROM +DRIVE selected](images/6x/drive-load-drive.png)
![Choose a sample](images/6x/drive-file.png)
![Loaded sample](images/6x/drive-loaded.png)

![Loading from +Drive demonstration](../media/reddit/04-samplelab-drive-load.gif)

Loading a sample for editing does not automatically add it to the project pool.
SOURCE also offers **RECORD (TRIM)** for working with a recording.

### Navigating SampleLAB

1. **Open Settings:** From the Waveform screen, push encoder H.
2. **Open Info:** Push H again. If CHOP analysis is pending, this also applies it.
3. **Return to Waveform:** Push H once more.
4. **Go back:** Press NO from Settings or Info to return directly to Waveform.
5. **Exit SampleLAB:** Press NO from the Waveform screen.

NO returns to Waveform without applying pending CHOP analysis.

### Using Settings

1. Adjust parameters using encoders A–D (top row) and E–H (bottom row).
2. Check the underlined **YES** prompt to see what pressing YES will do.
3. Press YES to apply pending analysis or execute the selected action.

![SampleLAB Settings](images/6x/lab-settings.png)

### Using Info

1. Open Info by pushing H from Settings.
2. Read the available encoder PUSH shortcuts and navigation instructions.
3. Push H or press NO to return to Waveform.

![SampleLAB Info](images/6x/info-chop.png)

Info does not adjust values when you turn an encoder. During an operation,
follow the on-screen **NO:CANCEL** or **NO:BACK** prompt.

### CHOP controls and shortcuts

| Encoder | Turn | Push |
|---|---|---|
| A | Select a region | Fit the selected region on screen |
| B | Waveform: move the region. Settings: adjust AUTO sensitivity or the COUNT/EVEN target | Settings: cycle AUTO → COUNT → EVEN |
| C | Adjust FADE for exported copies | — |
| D | Settings: choose CHOP or LOOP | Open SOURCE |
| E | Adjust START | Hold while turning for single-frame precision |
| F | Adjust END | Restore the previous marker layout; hold while turning for single-frame precision |
| G | Zoom, or pan when PAN is selected | Switch zoom/pan control |
| H | Select PLAY, SAVE, POOL, SAVE ALL or ALL POOL | Waveform → Settings → Info → Waveform |

The selected H action is shown above H. B's analysis-type shortcut works in
Settings. In Info, A, D, F and G PUSH shortcuts remain available.

### Chop a sample

1. Push H to open Settings. Choose **CHOP** with D if needed.
2. Push B to choose an analysis type:
   - **AUTO:** detect slice points; turn B to adjust sensitivity.
   - **COUNT:** request a number of regions; turn B to set the target.
   - **EVEN:** divide the sample evenly; turn B to set the number of regions.
3. Press **YES: ANALYZE**. Alternatively, push H to apply pending analysis and
   open Info, then push H again to return to the waveform.
4. Turn A to choose a region and push A to fit it on screen.

![Chopped sample](images/6x/lab-analyzed.png)

![SampleLAB chopping and trimming demonstration](../media/reddit/02-samplelab-smartchop-edit.gif)

### Trim, move and zoom

- Turn **E** to adjust START and **F** to adjust END.
- Turn **B** on the Waveform screen to move the selected region.
- Turn **G** to zoom. Push G to switch between zoom and pan.
- Push **A** to fit the selected region.

Zoom in for fine editing, or zoom out to travel further through the sample.
Faster turns move further; hold E or F while turning for single-frame precision.

![Fit the selected region](images/6x/lab-fit.png)
![Adjust START](images/6x/lab-trim-start.png)
![Adjust END](images/6x/lab-trim-end.png)

### Undo marker edits

**Push F** to restore the previous marker layout. There is one level of marker
undo. It can restore the whole-sample layout from before chopping; it does not
undo exported files or pool assignments. The screen tells you when no marker
backup is available.

![Markers restored](images/6x/lab-undo.png)

### Work with a loop

1. Open Settings and turn D to **LOOP**.
2. Adjust **START with E**, **BPM with F** and **BEATS with G in Settings**.
   B provides fine movement.
3. Return to Waveform to audition and check the range. G on the Waveform screen
   still controls zoom/pan.
4. Turn C to apply up to **50 ms of edge FADE** to an exported copy if wanted.

![LOOP Settings](images/6x/settings-loop.png)
![LOOP Info shortcuts](images/6x/info-loop.png)

### Audition, save and add to the pool

Turn H to select an action, then press YES. If the YES prompt says **ANALYZE**,
that confirmation applies pending analysis first; check the prompt again before
performing your chosen action.

| Action | What it does |
|---|---|
| PLAY | Audition the selected region |
| SAVE | Save the selected region as an edited copy |
| POOL | Export and assign the selected result to the project sample pool |
| SAVE ALL | Export all regions as separate copies |
| ALL POOL | Export all regions and assign them to the pool |

In LOOP mode, H offers **PLAY, SAVE and POOL**.

![SAVE selected](images/6x/save.png)
![SAVED confirmation](images/6x/saved.png)
![Pool assignment complete](images/6x/pool.png)
![SAVE ALL selected](images/6x/save-all.png)

Wait for **SAVED**, **IN POOL** or the batch result. Your source file is preserved,
and exported names avoid overwriting existing files. Save the project normally
to keep pool changes.

If a batch is cancelled or fails, copies and pool assignments already completed
may remain. Marker undo does not remove them.

## SmartSlice

SmartSlice finds slice points in a sample used by the **SLICE** machine. It is
separate from SampleLAB's CHOP tools.

1. Select a track using the SLICE machine and load your sample.
2. On its SRC page, open the SLICE menu and choose **CREATE SLICE GRID**.
3. Turn **encoder E below 2 slices** until the **SLICES** field reads **SMART**.
4. Press **YES** to create the slice points. Press NO to cancel.

![SMART selected below 2 slices](images/6x/smartslice-03-smart.png)

Numeric choices create a regular grid instead. These examples show 16 and 64:

![16-slice grid](images/6x/smartslice-02-grid-16.png)
![64-slice grid](images/6x/smartslice-04-grid-64.png)

Choose **EDIT SLICE POINTS** to inspect or adjust the result. SMART does not
automatically open that editor. The number and placement of points depend on
the sample.

![Slice points after SmartSlice](images/6x/smartslice-05-result-markers.png)

![SmartSlice demonstration](../media/reddit/01-smartslice.gif)

## Universal Zoom

On ordinary sample-machine SRC pages:

- **Tap C** to cycle through **1×, 2×, 4×, 8×, 16×, 32× and 64×**.
- **Turn C** to pan while zoomed in.
- **Hold C** to reset. Tapping again after 64× also returns to 1×.

On the **SLICE** machine, use **G** for these controls; C is used for CFADE.
SampleLAB uses its own G zoom/pan control described above.

![Waveform overview at 1×](images/6x/zoom-01-overview-1x.png)
![Waveform detail at 64×](images/6x/zoom-02-detail-64x.png)

![Universal Zoom demonstration](../media/reddit/03-universal-zoom.gif)

## CFADE

On the SLICE machine's SRC page, **turn C** to adjust playback loop-wrap
blending from **OFF/0 to 127**. Increase it to smooth the wrap of a repeating
slice, then listen and adjust to taste. G remains available for zoom.

![CFADE off](images/6x/cfade-01-off.png)
![CFADE at 30](images/6x/cfade-02-value-30.png)

CFADE blends the wrap within a loop, not transitions between different slices.
SampleLAB's **C:FADE** is separate: it sets an edge fade in milliseconds for
exported copies and preserves the source file.

---

[Back to the README](../../README.md) · [Windows download and installation](../README.md) · [Technical reference](TECHNICAL_NOTES.md)
