from .Mock_Microscope import MockMicroscope
from .Nidaq_Microscope import NidaqMicroscope

BACKENDS = {
    "mock": MockMicroscope,
    "nidaq": NidaqMicroscope,
}

def create_microscope_backend(name: str, scan_parameters=None):
    backend_name = (name or "mock").lower()

    backend_cls = BACKENDS.get(backend_name)
    if backend_cls is None:
        raise ValueError(
            f"Unknown backend '{backend_name}'. Expected one of: {', '.join(BACKENDS)}."
        )

    print(f"[BackendFactory] {backend_cls.__name__}")
    return backend_cls(scan_parameters=scan_parameters)