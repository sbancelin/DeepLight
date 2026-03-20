def create_microscope_backend(name: str, scan_parameters=None):
    backend_name = (name or "mock").lower()

    if backend_name == "mock":
        from .Mock_Microscope import MockMicroscope
        backend_cls = MockMicroscope

    elif backend_name == "nidaq":
        from .Nidaq_Microscope import NidaqMicroscope
        backend_cls = NidaqMicroscope

    else:
        raise ValueError(
            f"Unknown backend '{backend_name}'. Expected one of: mock, nidaq."
        )

    print(f"[BackendFactory] {backend_cls.__name__}")
    return backend_cls(scan_parameters=scan_parameters)