"""Entry point for fetch_cspv2_artifacts task."""

from __future__ import annotations

from release_service_utils.tasks.managed.fetch_cspv2_artifacts.fetch_cspv2_artifacts import (
    main,
)

if __name__ == "__main__":
    raise SystemExit(main())
