"""A whole acquisition, from a recipe to a file read back off the disk.

The claim this suite exists to check is the one everything else rests on: the
pixels written are the pixels acquired, at the scale the file says, with a
record of how they were taken. It runs against the simulated microscope, so it
needs no bench.
"""

import json
import os
import re

import numpy as np
import pytest
import tifffile

from DeepLight import Axis, Recipe, Session, polarization_sweep


@pytest.fixture(scope="module")
def session(qapp):
    with Session(backend="mock", session_log=False) as open_session:
        yield open_session


def ome_sizes(path):
    with tifffile.TiffFile(path) as handle:
        xml = handle.ome_metadata
    return {k: int(re.search(f'Size{k}="(\\d+)"', xml).group(1))
            for k in ("X", "Y", "Z", "C", "T")}


def sidecar(path):
    with open(os.path.splitext(path)[0] + ".json", encoding="utf-8") as handle:
        return json.load(handle)


def test_a_field_is_written_and_reads_back_identical(session, tmp_path):
    result = session.run(Recipe(
        axes=[Axis("X-Galvo", pixels=64, size_um=20.0),
              Axis("Y-Galvo", pixels=48, size_um=15.0)],
        dwell_us=2.0,
        detectors=["PMT-Vis"],
        folder=str(tmp_path), filename="field",
        comment="written by the test suite",
    ))

    assert result.path and os.path.isfile(result.path)

    # X-Galvo sweeps the sample's y direction: 64 rows, 48 columns.
    assert result.images["PMT-Vis"].shape == (64, 48)
    assert ome_sizes(result.path) == {"X": 48, "Y": 64, "Z": 1, "C": 1, "T": 1}

    with tifffile.TiffFile(result.path) as handle:
        assert handle.is_ome
        written = handle.asarray()

    # The frame the acquisition displayed is the frame on disk.
    assert np.allclose(written, result.images["PMT-Vis"])


def test_the_file_states_its_scale_in_micrometres(session, tmp_path):
    """Without this a viewer opens the acquisition in pixels and the
    micrometres are lost -- and a measurement made from it is wrong."""
    result = session.run(Recipe(
        axes=[Axis("X-Galvo", pixels=64, size_um=20.0),
              Axis("Y-Galvo", pixels=48, size_um=15.0)],
        dwell_us=2.0, folder=str(tmp_path), filename="scaled",
    ))

    with tifffile.TiffFile(result.path) as handle:
        xml = handle.ome_metadata

    # Columns are drawn by Y-Galvo, rows by X-Galvo.
    assert float(re.search(r'PhysicalSizeX="([^"]+)"', xml).group(1)) == pytest.approx(15.0 / 47.0, rel=1e-4)
    assert float(re.search(r'PhysicalSizeY="([^"]+)"', xml).group(1)) == pytest.approx(20.0 / 63.0, rel=1e-4)
    assert 'PhysicalSizeXUnit="µm"' in xml


def test_the_record_beside_the_data_says_how_it_was_taken(session, tmp_path):
    result = session.run(Recipe(
        axes=[Axis("X-Galvo", pixels=32, size_um=10.0),
              Axis("Y-Galvo", pixels=32, size_um=10.0)],
        dwell_us=2.0, detectors=["PMT-Vis"],
        folder=str(tmp_path), filename="traceable",
        comment="PMT gain 0.55 V",
        optics={"objective": "Nikon CFI Plan Apo 25XC W 1300",
                "numerical_aperture": 1.1},
    ))

    provenance = sidecar(result.path)["provenance"]

    assert provenance["software"]["name"] == "DeepLight"
    assert provenance["software"]["version"]
    assert provenance["optics"]["numerical_aperture"] == 1.1
    assert provenance["comment"] == "PMT gain 0.55 V"
    assert provenance["sampling"]["dwell_time_s"] == pytest.approx(2e-6)
    assert [d["name"] for d in provenance["detectors"] if d["enabled"]] == ["PMT-Vis"]

    # The raw parameters stay underneath, so nothing is lost.
    assert sidecar(result.path)["scan_params"]["active_axes"] == ["X-Galvo", "Y-Galvo"]


def test_a_polarisation_stack_records_the_angles_it_visited(session, tmp_path):
    """A plane index should never have to be matched back to an angle by hand."""
    result = session.run(Recipe(
        axes=[Axis("X-Galvo", 32, 20.0), Axis("Y-Galvo", 32, 20.0),
              polarization_sweep(count=4, span_deg=180.0)],
        dwell_us=2.0, folder=str(tmp_path), filename="pshg",
    ))

    assert result.frames == 4
    assert ome_sizes(result.path)["Z"] == 4
    assert sidecar(result.path)["provenance"]["polarization_angles_deg"] == [0.0, 45.0, 90.0, 135.0]


def test_without_a_folder_nothing_is_written_and_the_frames_still_come_back(session):
    """A dry run of the real pipeline: useful on its own, and it proves the
    writing is a separate concern from the acquiring."""
    result = session.run(Recipe(
        axes=[Axis("X-Galvo", 32, 10.0), Axis("Y-Galvo", 32, 10.0)],
        dwell_us=2.0,
    ))

    assert result.path is None
    assert result.images["PMT-Vis"].shape == (32, 32)


def test_one_session_runs_several_acquisitions_and_drives_the_stage(session, tmp_path):
    """What a batch script does: move, acquire, move, acquire."""
    start = session.positions()["z"]
    written = []

    for depth in (0.0, 10.0):
        session.move("Z-Vcoil", start + depth)
        result = session.run(Recipe(
            axes=[Axis("X-Galvo", 16, 5.0), Axis("Y-Galvo", 16, 5.0)],
            dwell_us=2.0, folder=str(tmp_path), filename=f"depth_{int(depth)}",
        ))
        written.append(result.path)

    session.move("Z-Vcoil", start)

    assert len(set(written)) == 2
    assert all(os.path.isfile(path) for path in written)
    assert session.positions()["z"] == pytest.approx(start, abs=0.5)
