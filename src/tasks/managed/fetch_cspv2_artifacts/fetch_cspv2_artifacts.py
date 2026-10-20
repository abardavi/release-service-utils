#!/usr/bin/env python3
"""Fetch OCI artifacts referenced by a Snapshot's components for CSPv2 release.

Downloads each component's artifact from Quay into a shared directory so their
checksums can be computed for the release's provenance-and-verification package.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from release_service_utils.helpers import oras_utils, tekton
from release_service_utils.helpers.vcs import git as git_utils

_CHECKSUM_CHUNK_SIZE = 1024 * 1024

README_REPO = "https://gitlab.cee.redhat.com/spmm/release-provenance-and-verification.git"
README_RELATIVE_PATH = "release-artifact-docs/README.md"

ARTIFACTS_DIRNAME = "artifacts"
SHA256SUM_FILENAME = "SHA256SUM"
README_FILENAME = "README.md"


def get_artifacts(snapshot_path: Path) -> list[str]:
    """Return every component's containerImage from the snapshot, in order."""
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    return [component["containerImage"] for component in snapshot["components"]]


def fetch_artifacts(artifacts: list[str], download_dir: Path) -> None:
    """Pull every artifact into the shared download_dir."""
    for artifact in artifacts:
        oras_utils.oras_pull(artifact, download_dir)


def compute_checksums(download_dir: Path, output_path: Path) -> None:
    """Write a SHA256SUM file (sha256sum-compatible) for every file in download_dir."""
    lines = []
    for path in sorted(download_dir.rglob("*")):
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(_CHECKSUM_CHUNK_SIZE), b""):
                digest.update(chunk)
        lines.append(f"{digest.hexdigest()}  {path.relative_to(download_dir)}")
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def fetch_readme(dest_path: Path, repo_url: str = README_REPO) -> None:
    """Clone the shared provenance-and-verification repo and copy its README.md."""
    with tempfile.TemporaryDirectory() as tmp:
        repo_dir = git_utils.clone(Path(tmp), repo_url)
        shutil.copy2(repo_dir / README_RELATIVE_PATH, dest_path)


def run(data_dir: Path, snapshot_path: str) -> None:
    """Fetch every component's artifact, checksum them, and fetch the provenance README."""
    artifacts = get_artifacts(data_dir / snapshot_path)

    download_dir = data_dir / ARTIFACTS_DIRNAME
    download_dir.mkdir(parents=True, exist_ok=True)
    fetch_artifacts(artifacts, download_dir)

    compute_checksums(download_dir, data_dir / SHA256SUM_FILENAME)
    fetch_readme(data_dir / README_FILENAME)


def main() -> int:
    """Read environment variables and run the fetch."""
    data_dir = Path(tekton.require_env("PARAM_DATA_DIR"))
    snapshot_path = tekton.require_env("PARAM_SNAPSHOT_PATH")

    run(data_dir, snapshot_path)
    return 0
