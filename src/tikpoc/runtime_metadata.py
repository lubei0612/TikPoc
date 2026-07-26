import os
from importlib.metadata import PackageNotFoundError, version

from .device_side_protocol import PROTOCOL_VERSION, SUPPORTED_HELPER_VERSIONS
from .rules import POLICY_VERSION


def runtime_metadata() -> dict[str, object]:
    try:
        project_version = version("tikpoc")
    except PackageNotFoundError:
        project_version = "development"
    build_commit = os.getenv("TIKPOC_BUILD_COMMIT", "development").strip()
    return {
        "project_version": project_version,
        "build_commit": build_commit or "development",
        "policy_version": POLICY_VERSION,
        "device_protocol_version": PROTOCOL_VERSION,
        "supported_helper_versions": sorted(SUPPORTED_HELPER_VERSIONS),
        "mobile_runtime": "vmos-autonomous-https",
    }
