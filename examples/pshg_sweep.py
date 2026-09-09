"""Run acquisitions from a script, without opening DeepLight.

Two things the window cannot do: repeat an acquisition a hundred times without
someone clicking, and decide what to do next from what the last one returned.

Run it against the simulator first -- nothing here touches hardware on the mock
backend:

    python -m DeepLight.examples.pshg_sweep

then pass --backend nidaq on the microscope PC.
"""

import argparse
import os

from DeepLight import Axis, Recipe, Session, polarization_sweep


def pshg_sweep(session: Session, folder: str) -> str:
    """A polarisation-resolved SHG series: one plane per azimuth.

    The half-wave plate steps through eight angles over 180 degrees, and the
    file that comes out lists them in its provenance, so the plane index never
    has to be matched back to an angle by hand.
    """
    result = session.run(Recipe(
        axes=[
            Axis("X-Galvo", pixels=512, size_um=100.0),
            Axis("Y-Galvo", pixels=512, size_um=100.0),
            polarization_sweep(count=8, span_deg=180.0),
        ],
        dwell_us=4.0,
        detectors=["PMT-Vis"],
        folder=folder,
        filename="pshg",
        comment="P-SHG series, 8 azimuths -- PMT gain 0.55 V",
        optics={
            "objective": "Nikon CFI Plan Apo 25XC W 1300",
            "numerical_aperture": 1.1,
            "refractive_index": 1.33,
            "wavelength_nm": 920.0,
            "process_order": 2,
        },
    ))
    print(f"  {result.frames} planes in {result.duration_s:.1f} s -> {result.path}")
    return result.path


def depth_series(session: Session, folder: str, depths_um=(0.0, 20.0, 40.0)) -> list:
    """One field per depth, the stage moved between them.

    move() only returns once the axis has arrived: starting a scan while the
    stage is still travelling would image the wrong plane.
    """
    written = []
    start = session.positions()["z"]

    for depth in depths_um:
        session.move("Z-Vcoil", start + depth)
        result = session.run(Recipe(
            axes=[
                Axis("X-Galvo", pixels=256, size_um=50.0),
                Axis("Y-Galvo", pixels=256, size_um=50.0),
            ],
            dwell_us=4.0,
            detectors=["PMT-Vis"],
            folder=folder,
            filename=f"depth_{int(depth):+04d}um",
            comment=f"{depth:g} µm below the surface",
        ))
        print(f"  {depth:6.1f} µm -> {os.path.basename(result.path)}")
        written.append(result.path)

    session.move("Z-Vcoil", start)
    return written


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["mock", "nidaq"], default="mock")
    parser.add_argument("--folder", default=os.path.join(os.getcwd(), "deeplight_scripted"))
    args = parser.parse_args()

    os.makedirs(args.folder, exist_ok=True)

    # One session for the whole script: opening it connects the hardware, and
    # doing that per acquisition would cost more than the acquisitions.
    with Session(backend=args.backend) as session:
        print("P-SHG sweep:")
        pshg_sweep(session, args.folder)

        print("Depth series:")
        depth_series(session, args.folder)

    print(f"\nWritten to {args.folder}")


if __name__ == "__main__":
    main()
