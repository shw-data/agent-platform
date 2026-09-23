"""Locates the data-domain repo this agent platform operates on.

In production the agent platform is a separate deployable from the data
repos it acts on (a customer domain repo, an orders domain repo, ...).
DOMAIN_REPO_PATH (env var, set in .env) tells this codebase where that
repo currently lives - this is the seam MCP will later formalize with
permissioned tools instead of a bare path.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DOMAIN_REPO = Path(
    os.environ.get("DOMAIN_REPO_PATH", "../customer-data-domain")
).expanduser().resolve()


def domain_path(*parts: str) -> str:
    """Build an absolute path inside the domain repo, e.g. domain_path('contracts/customer.yaml')."""
    return str(DOMAIN_REPO.joinpath(*parts))


def customer_spec_paths() -> dict:
    return {
        "contract_path": domain_path("contracts/customer.yaml"),
        "schema_path": domain_path("schemas/customer_source.yaml"),
        "mapping_path": domain_path("mappings/customer.yaml"),
        "model_path": domain_path("models/customer.yaml"),
    }
