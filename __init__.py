"""DeepLight -- control software for a multiphoton microscope.

The application is launched with ``python -m DeepLight``. The names below are
the scripting surface: an acquisition described as data and run without the
window, for sweeps and batches.

    from DeepLight import Axis, Recipe, run

They are imported on first use rather than here, so ``import DeepLight`` stays
free of Qt and of every driver behind it.
"""

_PUBLIC = {
    "Axis": "recipe",
    "Recipe": "recipe",
    "polarization_sweep": "recipe",
    "RunResult": "api",
    "Session": "api",
    "run": "api",
}

__all__ = sorted(_PUBLIC)


def __getattr__(name):
    module = _PUBLIC.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from importlib import import_module
    return getattr(import_module(f".{module}", __name__), name)


def __dir__():
    return sorted(set(globals()) | set(_PUBLIC))
