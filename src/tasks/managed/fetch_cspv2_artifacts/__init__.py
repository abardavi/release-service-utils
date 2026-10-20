"""Fetch OCI artifacts referenced by a Snapshot's components for CSPv2 release."""

from .fetch_cspv2_artifacts import (  # noqa: F401
    compute_checksums,
    fetch_artifacts,
    fetch_readme,
    get_artifacts,
    main,
    run,
)
