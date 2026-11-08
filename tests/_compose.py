"""Reading the effective GPU compose stack from tests.

`docker-compose.gpu.yml` is an `include:` of a shared base and a per-vendor
overlay since v13.0, so a test that parses that file directly sees no services.
These helpers return what the NVIDIA stack actually contains, so the assertions
keep describing the deployed configuration instead of the file layout.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

# Order matters: the overlay wins where both files mention a service.
GPU_NVIDIA_FILES = ("docker-compose.gpu.base.yml", "docker-compose.gpu.nvidia.yml")


def gpu_nvidia_text() -> str:
    """Both files concatenated, for assertions that search for a literal."""
    return "\n".join((ROOT / name).read_text(encoding="utf-8") for name in GPU_NVIDIA_FILES)


def gpu_nvidia_services() -> dict:
    """Services of the NVIDIA stack with the overlay merged over the base.

    A service appears in both files when the overlay only adds the container
    runtime and the NVIDIA_* environment; the dictionaries are merged shallowly,
    which covers the keys these tests inspect.
    """
    services: dict = {}
    for name in GPU_NVIDIA_FILES:
        document = yaml.safe_load((ROOT / name).read_text(encoding="utf-8")) or {}
        for service, definition in (document.get("services") or {}).items():
            if service in services and isinstance(definition, dict) and isinstance(services[service], dict):
                merged = dict(services[service])
                merged.update(definition)
                services[service] = merged
            else:
                services[service] = definition
    return services
