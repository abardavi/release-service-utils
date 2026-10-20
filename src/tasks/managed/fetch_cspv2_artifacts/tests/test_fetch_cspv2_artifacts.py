"""Tests for fetch_cspv2_artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from release_service_utils.tasks.managed.fetch_cspv2_artifacts.fetch_cspv2_artifacts import (
    ARTIFACTS_DIRNAME,
    README_FILENAME,
    README_RELATIVE_PATH,
    SHA256SUM_FILENAME,
    compute_checksums,
    fetch_artifacts,
    fetch_readme,
    get_artifacts,
    main,
    run,
)

TASK = "release_service_utils.tasks.managed.fetch_cspv2_artifacts.fetch_cspv2_artifacts"


def test_get_artifacts_returns_all_in_order(tmp_path: Path) -> None:
    """Return every component's containerImage, in snapshot order."""
    snap = {
        "components": [
            {"containerImage": "quay.io/org/img1@sha256:aaa"},
            {"containerImage": "quay.io/org/img2@sha256:bbb"},
        ]
    }
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(snap), encoding="utf-8")
    assert get_artifacts(path) == [
        "quay.io/org/img1@sha256:aaa",
        "quay.io/org/img2@sha256:bbb",
    ]


@patch(f"{TASK}.oras_utils.oras_pull")
def test_fetch_artifacts_pulls_every_artifact_into_shared_dir(
    mock_pull: MagicMock, tmp_path: Path
) -> None:
    """Pull every artifact into the same shared download directory."""
    artifacts = ["quay.io/org/img1@sha256:aaa", "quay.io/org/img2@sha256:bbb"]
    fetch_artifacts(artifacts, tmp_path)

    assert mock_pull.call_args_list == [
        call(artifacts[0], tmp_path),
        call(artifacts[1], tmp_path),
    ]


def test_compute_checksums_writes_sha256sum_format(tmp_path: Path) -> None:
    """Write a SHA256SUM file in standard `sha256sum`-compatible format."""
    download_dir = tmp_path / "downloaded"
    download_dir.mkdir()
    (download_dir / "file-a.bin").write_bytes(b"content a")
    (download_dir / "file-b.bin").write_bytes(b"content b")

    output_path = tmp_path / "SHA256SUM"
    compute_checksums(download_dir, output_path)

    lines = sorted(output_path.read_text(encoding="utf-8").splitlines())
    assert lines == [
        "0069ffe8481777aa403982d9e9b3fa48957015a07cfa0f66dae32050b95bda54  file-a.bin",
        "c4363f384691f55ee5a5c315e1f7c37366ae232933665a93fe3bcc0d90bc937b  file-b.bin",
    ]


@patch(f"{TASK}.git_utils.clone")
def test_fetch_readme_copies_from_cloned_repo(mock_clone: MagicMock, tmp_path: Path) -> None:
    """Clone the shared provenance repo and copy its README.md to dest_path."""

    def _fake_clone(parent_dir: Path, clone_url: str) -> Path:
        repo_dir = parent_dir / "release-provenance-and-verification"
        readme = repo_dir / README_RELATIVE_PATH
        readme.parent.mkdir(parents=True, exist_ok=True)
        readme.write_text("provenance readme content", encoding="utf-8")
        return repo_dir

    mock_clone.side_effect = _fake_clone

    dest_path = tmp_path / "README.md"
    fetch_readme(dest_path)

    assert dest_path.read_text(encoding="utf-8") == "provenance readme content"
    assert mock_clone.call_count == 1


@patch(f"{TASK}.fetch_readme")
@patch(f"{TASK}.compute_checksums")
@patch(f"{TASK}.fetch_artifacts")
@patch(f"{TASK}.get_artifacts")
def test_run_orchestrates_fetch_checksum_and_readme(
    mock_get_artifacts: MagicMock,
    mock_fetch_artifacts: MagicMock,
    mock_compute_checksums: MagicMock,
    mock_fetch_readme: MagicMock,
    tmp_path: Path,
) -> None:
    """Fetch every artifact, checksum them, and fetch the provenance README."""
    mock_get_artifacts.return_value = ["quay.io/org/img1@sha256:aaa"]

    run(tmp_path, "snapshot.json")

    download_dir = tmp_path / ARTIFACTS_DIRNAME
    mock_get_artifacts.assert_called_once_with(tmp_path / "snapshot.json")
    mock_fetch_artifacts.assert_called_once_with(
        mock_get_artifacts.return_value, download_dir
    )
    mock_compute_checksums.assert_called_once_with(
        download_dir, tmp_path / SHA256SUM_FILENAME
    )
    mock_fetch_readme.assert_called_once_with(tmp_path / README_FILENAME)
    assert download_dir.is_dir()


@patch(f"{TASK}.run")
def test_main_reads_env_and_calls_run(
    mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read PARAM_DATA_DIR and PARAM_SNAPSHOT_PATH, then call run()."""
    monkeypatch.setenv("PARAM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PARAM_SNAPSHOT_PATH", "snapshot.json")

    assert main() == 0
    mock_run.assert_called_once_with(tmp_path, "snapshot.json")
