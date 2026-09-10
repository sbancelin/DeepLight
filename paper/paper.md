---
title: 'DeepLight: multimodal acquisition software for a polarisation-resolved multiphoton microscope'
tags:
  - Python
  - microscopy
  - multiphoton
  - second-harmonic generation
  - polarimetry
  - Brillouin
  - instrument control
authors:
  - name: Stéphane Bancelin
    orcid: 0000-0000-0000-0000        # TO FILL IN
    affiliation: 1
affiliations:
  - name: Laboratoire Photonique, Numérique et Nanosciences (LP2N), Université de Bordeaux, CNRS, Institut d'Optique Graduate School, Talence, France
    index: 1
date: 10 September 2026
bibliography: paper.bib
---

# Summary

`DeepLight` is the acquisition software of a home-built multiphoton microscope
that combines polarisation-resolved second-harmonic generation (P-SHG) imaging
with Brillouin and Raman spectroscopy on the same sample, in the same session.
It drives the scanning, the stages, the lasers, the waveplates and the
spectrometers from one window, writes the results in community formats with the
metadata needed to interpret them, and exposes the same acquisition pipeline as
a Python API so that a series can be scripted rather than clicked.

Home-built microscopes are usually run by home-built software, and that software
is usually where the reproducibility of an experiment quietly fails: the pixel
size lives in a lab notebook, the polarisation calibration in a spreadsheet, and
the analysis script has to be told what the acquisition already knew.
`DeepLight` was written to close that gap for a specific instrument while
keeping the parts that touch hardware behind small contracts, so that the same
software can be adapted to another bench by writing a class rather than by
editing the application.

# Statement of need

Multiphoton microscopes are overwhelmingly custom instruments, and the software
that runs them determines what experiments are practical on them. Two families
of tools exist. General frameworks such as `Micro-Manager` [@Edelstein2014] and
its Python interface `Pycro-Manager` [@Pinkard2021] cover a very wide range of
hardware and are excellent for widefield and camera-based imaging, but
point-scanning with waveform-driven galvanometers is not their centre of
gravity. Dedicated point-scanning packages such as `ScanImage`
[@Pologruto2003] are mature and widely used, but are built around
MATLAB and around fluorescence imaging.

Neither family addresses the specific combination this instrument needs:

- **Polarisation-resolved SHG as a first-class scan dimension.** P-SHG measures
  the orientation and organisation of non-centrosymmetric structures, notably
  collagen, by acquiring an image stack at a series of excitation polarisation
  azimuths [@Stoller2002; @Tiaho2007; @Bancelin2014]. It is not a fluorescence
  channel and it is not a Z stack: it is a dimension of the acquisition whose
  coordinate is an angle, and whose meaning depends on the state of two
  waveplates being known and recorded.

- **Point-scanning and spectroscopy in one session.** Brillouin microscopy
  probes mechanical properties through a spectral shift [@Scarcelli2008;
  @Prevedel2019]; Raman probes chemical composition. Correlating either with
  P-SHG on the same field requires one piece of software to own the stage, the
  shutter and the coordinates, rather than two programs taking turns.

- **Metadata that survives the acquisition.** An image whose pixel size is not
  in the file is measured wrongly by whoever opens it next.

`DeepLight` is in daily use on the instrument it was written for. It is offered
here not as a general replacement for the tools above, but as a working example
of an instrument-specific acquisition program that keeps its data
interpretable, its hardware layer replaceable, and its acquisitions scriptable —
and as a starting point for groups building comparable benches.

# Functionality

## Acquisition

An acquisition is up to four nested axes, chosen per row from the galvanometers,
the stage, the depth actuator and the polarisation axis, with repetitions
providing a time dimension and up to four detector channels. A Z stack, a P-SHG
series, a time series or any combination is therefore the same object, described
the same way and written to the same file layout.

Two analog channels are integrated over the dwell time by the acquisition card;
two further channels count photons on its counters. Bidirectional scanning is
supported, with the return-line offset derived from a galvanometer lag
calibrated once rather than retuned whenever the dwell time changes. Stack axes
have their settling time reserved in the execution plan, so a plane is not
acquired while the actuator is still travelling.

## Polarisation

The excitation azimuth is set by a half-wave plate, which rotates linear
polarisation by twice its own angle; reaching an azimuth is therefore a single
halving from a plate angle measured once. A quarter-wave plate acts as a
compensator, correcting the ellipticity introduced by the dichroics and scan
mirrors, and is parked on one of three measured positions rather than turned
during a scan. The plate angle azimuths are counted from, the compensator state,
and the list of azimuths actually visited are all written into the saved file:
an azimuth is meaningless without the first, and a series acquired through a
mis-set compensator is not linear at the sample, neither of which is visible in
the images themselves.

## Data and provenance

Images and stacks are written as OME-TIFF or OME-Zarr [@Goldberg2005;
@Linkert2010; @Moore2021], carrying `PhysicalSize` and NGFF coordinate
transformations so that viewers open them in micrometres. Mosaics are written as
BigTIFF with an image pyramid. Brillouin datasets can be written in the HDF5-BLS
format used by the Brillouin community.

Beside every acquisition goes a record of how it was taken: the software version
and git revision, the sampling and dwell, the objective and numerical aperture,
the lasers that were on, the polarisation configuration, the detector channels,
any dark or flat-field correction applied, and the operator's comment. A log file
is written alongside as the run proceeds, so a run that stopped halfway still
records why. Settings can be saved as a preset and reloaded, and the same preset
is a recipe the scripting API can replay.

## Scripting

The graphical interface is one caller of the manager layer; a script is another,
using the same plan builder, the same acquisition manager and the same writers:

```python
from DeepLight import Axis, Recipe, Session, polarization_sweep

with Session(backend="nidaq") as session:
    for depth_um in range(0, 120, 20):
        session.move("Z-Vcoil", depth_um)
        session.run(Recipe(
            axes=[Axis("X-Galvo", 512, 100.0),
                  Axis("Y-Galvo", 512, 100.0),
                  polarization_sweep(count=18, span_deg=180.0)],
            dwell_us=4.0, detectors=["PMT-Vis"],
            folder=r"D:\data", filename=f"pshg_{depth_um}um",
        ))
```

A `Recipe` is plain data: it round-trips through JSON, so a series can be kept
in a file, version-controlled beside its results, and re-run.

## Hardware abstraction and simulation

Each device family — microscope backend, camera, detector, spectrograph,
shutter, power actuator, waveplate rotator — has a base class, a simulated
implementation and a validator that refuses an incomplete one. Supporting a
different brand means writing one class and returning it from a factory.

Every family has a mock, so the entire application, including a full acquisition
written to disk, runs with no hardware attached. This is what makes the test
suite runnable by a reviewer, and what allows the software to be developed away
from the bench.

# Figures

Proposed for the final version:

1. **The instrument and the software.** A schematic of the optical bench —
   lasers, waveplates, scan head, objective, the SHG and spectroscopy detection
   arms — alongside a screenshot of the acquisition window, with the
   correspondence between hardware and panels indicated. *Purpose: shows in one
   glance what the software has to coordinate.*

2. **A P-SHG series.** A collagen-rich sample imaged at 8–18 excitation
   azimuths: a montage of a few planes, and the fitted orientation map derived
   from them. *Purpose: the headline modality, and evidence that the
   polarisation axis produces analysable data.*

3. **Correlative acquisition.** The same field of view in SHG and in Brillouin
   (or Raman), with the shift or spectrum extracted at a few marked points.
   *Purpose: the multimodality claim, which is the main argument for one program
   owning the stage.*

4. **Provenance and formats.** A saved acquisition opened in Fiji or napari
   showing the correct micrometre scale, next to an excerpt of the JSON record
   listing the polarisation angles, the optics and the git revision. *Purpose:
   the reproducibility claim, made concrete.*

5. **Optional — depth compensation.** A Z stack acquired with and without the
   power ramp, with mean intensity against depth for both. *Purpose: shows a
   feature that is hard to argue for in words.*

# Acknowledgements

<!-- Funding, collaborators, and anyone who contributed to the instrument. -->

# References
