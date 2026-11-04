"""Prepare and sign the CSPv2 SHA256SUM manifest via middleware-signing."""

from __future__ import annotations

import argparse
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

from release_service_utils.helpers import internal_request, oras_utils, tekton
from release_service_utils.helpers.logger import logger

CHECKSUM_FILENAME = "SHA256SUM"
SIGNING_INPUT_DIRNAME = "signing-input"
SIGNED_OUTPUT_DIRNAME = "signing-output"
RESULTS_FILENAME = "results.json"
_SIGNATURE_FILE_SUFFIX = ".asc"
_RATE_LIMITED_LABEL = "internal-services.appstudio.redhat.com/rate-limited"
_RATE_LIMITING_GROUP_LABEL = (
    "internal-services.appstudio.redhat.com/rate-limiting-group"
)
_RATE_LIMITING_GROUP = "signing-server"


def prepare(data_dir: Path) -> None:
    """Zip only SHA256SUM for the signing pipeline's single-zip input contract."""
    checksum_path = data_dir / CHECKSUM_FILENAME
    if not checksum_path.is_file():
        raise FileNotFoundError(
            f"Required checksum manifest not found: {checksum_path}"
        )

    signing_input_dir = data_dir / SIGNING_INPUT_DIRNAME
    if signing_input_dir.exists():
        shutil.rmtree(signing_input_dir)
    signing_input_dir.mkdir(parents=True)

    zip_path = signing_input_dir / f"{CHECKSUM_FILENAME}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(checksum_path, arcname=CHECKSUM_FILENAME)
    logger.info("Prepared checksum-only signing input at %s", zip_path)


def _oras_pull_spec(artifact_uri: str) -> str:
    """Convert a Trusted Artifact URI to the registry pull spec accepted by oras."""
    if artifact_uri.startswith("oci://"):
        return artifact_uri.removeprefix("oci://")
    if artifact_uri.startswith("oci:"):
        return artifact_uri.removeprefix("oci:")
    return artifact_uri


def request_signature(
    *,
    input_artifact_file: Path,
    output_artifact_file: Path,
    signed_output_dir: Path,
) -> None:
    """Submit middleware-signing InternalRequest and pull its results artifact."""
    input_artifact = input_artifact_file.read_text(encoding="utf-8").strip()
    if not input_artifact:
        raise ValueError("The signing input Trusted Artifact reference is empty.")

    signing_pipeline = tekton.require_env("PARAM_SIGNING_PIPELINE")
    signing_repo = tekton.require_env("PARAM_SIGNING_REPO")
    signing_revision = tekton.require_env("PARAM_SIGNING_REVISION")
    params: dict[str, str] = {
        "sourceDataArtifact": input_artifact,
        "ociStorage": tekton.require_env("PARAM_OCI_STORAGE"),
        "ociArtifactExpiresAfter": tekton.require_env(
            "PARAM_OCI_ARTIFACT_EXPIRES_AFTER"
        ),
        "orasOptions": tekton.require_env("PARAM_ORAS_OPTIONS"),
        "trustedArtifactsDebug": tekton.require_env("PARAM_TRUSTED_ARTIFACTS_DEBUG"),
        "dataDir": tekton.require_env("PARAM_DATA_DIR"),
        "exclude": "[]",
        "keyname": tekton.require_env("PARAM_KEYNAME"),
        "onbehalfof": tekton.require_env("PARAM_ONBEHALFOF"),
        "kerberos_keytab_secret": tekton.require_env("PARAM_KERBEROS_KEYTAB_SECRET"),
        "kerberos_keytab": tekton.require_env("PARAM_KERBEROS_KEYTAB"),
        "kerberos_principal": tekton.require_env("PARAM_KERBEROS_PRINCIPAL"),
        "verbose": tekton.require_env("PARAM_VERBOSE"),
        "caTrustConfigMapName": tekton.require_env("PARAM_CA_TRUST_CONFIG_MAP_NAME"),
        "caTrustConfigMapKey": tekton.require_env("PARAM_CA_TRUST_CONFIG_MAP_KEY"),
        "taTaskGitUrl": tekton.require_env("PARAM_TASK_GIT_URL"),
        "taTaskGitRevision": tekton.require_env("PARAM_TASK_GIT_REVISION"),
        "trusted_artifacts_dockerconfig_secret": tekton.require_env(
            "PARAM_TRUSTED_ARTIFACTS_DOCKERCONFIG_SECRET"
        ),
        "taskGitUrl": signing_repo,
        "taskGitRevision": signing_revision,
    }
    labels = {
        internal_request.PIPELINERUN_UID_LABEL: tekton.require_env(
            "PARAM_PIPELINERUN_UID"
        ),
        internal_request.TASK_GROUP_LABEL: tekton.require_env("PARAM_TASK_RUN_UID"),
        _RATE_LIMITED_LABEL: "true",
        _RATE_LIMITING_GROUP_LABEL: _RATE_LIMITING_GROUP,
    }

    logger.info(
        "Submitting %s signing request with key %s for %s.",
        signing_pipeline,
        params["keyname"],
        params["onbehalfof"],
    )
    try:
        ir_name = internal_request.create(
            signing_pipeline,
            params=params,
            labels=labels,
            sync=True,
            timeout=int(tekton.require_env("PARAM_REQUEST_TIMEOUT")),
            service_account=tekton.require_env("PARAM_SIGNING_SERVICE_ACCOUNT"),
            pipeline_timeout=tekton.require_env("PARAM_PIPELINE_TIMEOUT"),
            task_timeout=tekton.require_env("PARAM_TASK_TIMEOUT"),
            finally_timeout=tekton.require_env("PARAM_FINALLY_TIMEOUT"),
        )
    except internal_request.InternalRequestWaitError as err:
        raise RuntimeError(f"Middleware signing request failed: {err}") from err

    results: dict[str, Any] = internal_request.fetch_results(ir_name)
    output_artifact = results.get("sourceDataArtifact")
    if not isinstance(output_artifact, str) or not output_artifact.strip():
        raise RuntimeError("middleware-signing returned no sourceDataArtifact result.")

    output_artifact_file.parent.mkdir(parents=True, exist_ok=True)
    output_artifact_file.write_text(output_artifact, encoding="utf-8")
    if signed_output_dir.exists():
        shutil.rmtree(signed_output_dir)
    signed_output_dir.mkdir(parents=True)
    oras_utils.oras_pull(_oras_pull_spec(output_artifact), signed_output_dir)
    logger.info("Pulled middleware-signing results into %s", signed_output_dir)


def _signature_text(signed_output_dir: Path) -> str:
    """Find the detached signature for SHA256SUM in results.json."""
    results_path = signed_output_dir / RESULTS_FILENAME
    if not results_path.is_file():
        raise FileNotFoundError(f"Signing results not found: {results_path}")

    results = json.loads(results_path.read_text(encoding="utf-8"))
    entries = results.get("results")
    if not isinstance(entries, list):
        raise TypeError("Signing results JSON does not contain a results list.")

    matching_entries = [
        entry
        for entry in entries
        if isinstance(entry, dict)
        and Path(str(entry.get("file", ""))).name == CHECKSUM_FILENAME
    ]
    if len(matching_entries) != 1:
        raise ValueError(
            f"Expected one signature result for {CHECKSUM_FILENAME}; found {len(matching_entries)}."
        )

    signature = matching_entries[0].get("signature")
    if not isinstance(signature, str) or not signature.strip():
        raise ValueError(f"Signing results for {CHECKSUM_FILENAME} have no signature.")
    return signature


def finalize(data_dir: Path, signed_output_dir: Path) -> None:
    """Write the detached signature embedded in signing results as SHA256SUM.asc."""
    signature = _signature_text(signed_output_dir)
    output_path = data_dir / f"{CHECKSUM_FILENAME}{_SIGNATURE_FILE_SUFFIX}"
    output_path.write_text(signature, encoding="utf-8")
    logger.info("Wrote detached checksum signature to %s", output_path)


def main() -> int:
    """Run the requested signing phase using the Task's environment parameters."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "sign", "finalize"])
    args = parser.parse_args()
    data_dir = Path(tekton.require_env("PARAM_DATA_DIR"))

    if args.command == "prepare":
        prepare(data_dir)
        return 0
    if args.command == "sign":
        request_signature(
            input_artifact_file=Path(
                tekton.require_env("PARAM_SIGNING_INPUT_ARTIFACT_FILE")
            ),
            output_artifact_file=Path(
                tekton.require_env("PARAM_SIGNING_OUTPUT_ARTIFACT_FILE")
            ),
            signed_output_dir=Path(tekton.require_env("PARAM_SIGNED_OUTPUT_DIR")),
        )
        return 0

    finalize(
        data_dir,
        Path(tekton.require_env("PARAM_SIGNED_OUTPUT_DIR")),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
