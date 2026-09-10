# DeepLight user guide

For installing and launching, see the [README](../README.md). This describes
what the window does once it is open.

---

## The window

![The DeepLight window](deeplight.png)

Three regions, and the two side ones fold away with **F9** and **F10** when the
image needs the room:

- **Left**: the panels that define an acquisition — Scan, Save, Spectro,
  Positioners.
- **Centre**: four tabs — *Scan* (the images), *Stitching* (mosaics), *Camera*
  (the widefield camera), *Spectro* (Brillouin and Raman).
- **Right**: collapsible analysis panels — Analog Visualizer, Stepper
  Visualizer, Nyquist, Depth Power, Line Profile, Histogram, FRC, Logs.

The bar across the top holds preview, continuous preview, record, stop and the
shutter. The bar at the bottom shows progress, elapsed and remaining time.

---

## Keyboard

| Key | What it does |
|---|---|
| `Space` | single preview |
| `Ctrl+Space` | continuous preview |
| `Esc` | stop |
| `Ctrl+Q` | toggle the shutter |
| `F1` | pull the black point down onto the darkest pixel |
| `F2` | pull the white point up onto the brightest |
| `Ctrl+R` | draw a rectangle on the image |
| `Ctrl+Return` | make that rectangle the next field of view |
| `F9` / `F10` | fold the left / right panel |
| `Ctrl+P` | write a snapshot as PNG, scale bar included |
| `Ctrl+Shift+C` | copy that snapshot to the clipboard |

**Stage keys.** With *Lock shortcuts* unticked in the Positioners panel, the
arrow keys drive the stage: `←` `→` for X, `↑` `↓` for Y, `PageUp` / `PageDown`
for Z, each by the step shown in the panel. They are locked by default, and
locked automatically while an acquisition runs, so a key press cannot move the
sample mid-scan. They are also ignored while a text field has focus.

**F1 and F2 act on the image under the cursor**, or on every channel if the
cursor is elsewhere. They move one bound and leave the other where you put it,
which is what makes them usable while hunting for faint signal.

---

## Setting up a scan

The Scan panel has four axis rows. Each row picks an axis and gives it a size in
µm, a number of pixels and an offset. The step is computed, not typed.

- **Rows 1 and 2** make the image. `X-Galvo` and `Y-Galvo` for laser scanning,
  `X-Stage` and `Y-Stage` for sample scanning (the *Laser scanning* /
  *Sample scanning* buttons swap the offered axes).
- **Rows 3 and 4** step between frames: `Z-Vcoil` for a depth stack,
  `Polarization` for a P-SHG series. A stack axis also offers *Around* (centred
  on the current position) or *From* (starting at it).

Offsets are **relative to where the sample is now**, so moving the stage moves
the whole field with it.

**Dwell time** is in µs per pixel. *Duration* and the estimated file size update
as you type, and both account for the time the stack axis needs to settle
between planes.

**Bidirectional** scanning halves the time by acquiring on both sweeps. The
returning line lands a few pixels off because the galvo lags; type the shift
that lines them up once, press Return, and DeepLight converts it into a lag in
µs and recomputes the shift whenever the dwell changes. Calibrated once, not per
scan.

Values are checked as you type. An axis asked to leave its range is refused and
the field reverts. The limits are checked again against the stage's real
position when you start, since the stage may have moved since.

---

## Detectors

Four channels: **PMT-Vis** and **PMT-IR** are analog, integrated over the dwell
by the acquisition card and displayed in V·µs; **Ch 0** and **Ch 1** are photon
counting on the card's counters, displayed in counts.

Each image footer shows the share of pixels sitting on the input range limit —
grey at zero, amber past a tenth of a percent, red past one. A saturated PMT
looks like a well-contrasted image and nothing else says so. Digital channels
show nothing there: a counter has no fixed ceiling.

---

## Polarisation (P-SHG)

Two waveplates before the objective, doing different jobs:

- The **half-wave plate** carries the azimuth. A scan to azimuth θ drives it to
  θ/2. One setting describes the mount — *Horizontal at*, the plate angle where
  the light comes out horizontal — in the Positioners settings.
- The **quarter-wave plate** is a compensator, correcting the ellipticity the
  optics upstream introduce. It does not scan. It parks on one of three measured
  positions, entered in the same settings and reached with the **Lin**, **CD**
  and **CG** buttons.

For a series, put `Polarization` on row 3 and give it the angles you want. A
scan parks the compensator on *linear* at its first point rather than trusting
where it was left, and returns the half-wave plate where it started at the end.
The azimuths visited are listed in the saved file.

---

## Depth compensation

Scattering attenuates the beam with depth, so a plane taken deeper receives less
light than one near the surface. The **Depth Power** panel raises the incident
power to keep the power in the focal volume constant: P(z) = P₀·exp(µz).

Tick *Activate*, set µ, and the surface power is captured when the stack is
armed — the ramp always starts from there rather than from wherever the previous
stack ended, so arming twice cannot compound. The laser returns to its surface
power when the run finishes.

For a sample that does not follow Beer-Lambert, switch *Mode* to **Table** and
type the powers you measured as `depth:percent` pairs — `0:10, 40:25, 80:60`.
These are absolute percentages, so the surface power plays no part and is greyed
out. Between points the power is interpolated; **beyond them it is held flat
rather than extrapolated**, because continuing the curve past the deepest point
anyone measured is how a sample gets cooked. A table that cannot be read is
reported where you type it, and an unusable one holds the surface power rather
than moving the laser.

---

## Positions

The **Positions** panel remembers places on the sample. *Add* captures where the
stage is now, *Go* — or a double-click on a row — drives back to it, *Update*
replaces one with the current position. The list travels in the preset.

Coordinates are stored in the relative frame, the same one the scan offsets use
and the *Set 0* buttons define, so a saved position still means something after
a re-home. X and Y are driven together where the controller allows it: one axis
then the other traces an L across the sample instead of a diagonal. A move is
refused while an acquisition is running.

---

## Recording

Set a folder — one is proposed, dated `<data root>/YYYY/Month/DD` — a file name
and a comment. **Save** writes what is on screen. **REC** (the round button)
records the whole acquisition as it runs.

| Format | What it is for |
|---|---|
| OME-TIFF | images and stacks, `TCZYX` |
| OME-Zarr | NGFF 0.4, written as the run proceeds |
| HDF5-BLS | Brillouin datasets |

Files carry their pixel size, so Fiji and napari open them in micrometres.
Beside each one goes a JSON record: the build of DeepLight down to the git
revision, the sampling, the objective and NA, the lasers that were on, the
polarisation setup, the detectors, any dark or flat correction, and your
comment. A log file is written alongside as the run proceeds, so an acquisition
that stopped halfway still says why.

**The comment field is where the detector gain goes.** The software neither sets
it nor can read it back.

**Snapshot** copies the displayed image to a PNG or the clipboard, with a scale
bar burnt in and the length chosen automatically. It exports the whole frame at
its own resolution, not the zoomed view, so a figure is reproducible from the
acquisition.

**Preset** saves the whole setup — axes, dwell, detectors, optics — as a JSON
file to reload later. The window also reopens on the settings you closed it
with. A preset is also a recipe the scripting API can replay.

---

## Mosaics

The *Stitching* tab tiles a region larger than one field. Set the number of
tiles and the overlap; the stage walks a serpentine from where it is now, and
tiles are registered against their neighbours by cross-correlation in the
overlap. A stack per tile is supported, and for a Z or P stack the *per plane*
order does the whole mosaic at one plane before moving the axis.

The extent is checked against the stage limits before the first move: a mosaic
that would walk off is refused rather than failing on its last corner.

---

## Camera, Brillouin, Raman

The *Camera* tab drives the widefield camera: snap, live, ROI, binning,
exposure. **Dark** and **Flat** acquire correction references — for the dark you
block the light yourself, and the dialog says so.

The *Spectro* tab drives the Brillouin arm (a Princeton Instruments camera) and
the Raman arm (an IsoPlane 320 and its camera). **Dark** there closes the
shutter itself and puts it back as it found it. The EMCCD's dark current grows
with exposure, and a Brillouin exposure is long, so it matters more here than
elsewhere.

Both run against a software mock until the hardware is present, selected per
instrument in the configuration file.

---

## Analysis panels

- **Line Profile** — a movable line across the image, plotted live.
- **Histogram** — the distribution inside a rectangle you draw.
- **FRC** — Fourier ring correlation between two successive frames, an estimate
  of the resolution actually achieved rather than the one the optics allow.
- **Nyquist** — enter the objective, wavelength and process order (1P/2P/3P) and
  it gives the resolution and the pixel size to sample it properly. The orange
  line marks the Nyquist criterion; the sliders let you sit deliberately under
  or over it.
- **Analog / Stepper Visualizer** — the voltages sent to the galvos and the
  moves asked of the stage, as they happen.
- **Logs** — the same messages that go to the log files.

---

## Scripting

Anything the window does to an acquisition, a script can do:

```python
from DeepLight import Axis, Recipe, Session, polarization_sweep

with Session(backend="nidaq") as session:
    for depth in (0, 20, 40):
        session.move("Z-Vcoil", depth)
        session.run(Recipe(
            axes=[Axis("X-Galvo", 512, 100.0), Axis("Y-Galvo", 512, 100.0),
                  polarization_sweep(count=8, span_deg=180.0)],
            dwell_us=4.0, detectors=["PMT-Vis"],
            folder=r"D:\data", filename=f"pshg_{depth}um",
        ))
```

See `examples/pshg_sweep.py`, and try it with `backend="mock"` first.

---

## Configuration

One file per machine holds the COM ports, serial numbers, driver paths and the
data root. It is written for you on first run and its path is logged. Every key
is optional; delete a line and the default returns. See `config.py` for what
goes in it, and [adding-hardware.md](adding-hardware.md) for putting a different
instrument behind it.
