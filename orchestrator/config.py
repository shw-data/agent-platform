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


def spec_paths(entity: str) -> dict:
    """Locate the four spec files for any entity, e.g. spec_paths("customer")."""
    return {
        "contract_path": domain_path(f"contracts/{entity}.yaml"),
        "schema_path": domain_path(f"schemas/{entity}.yaml"),
        "mapping_path": domain_path(f"mappings/{entity}.yaml"),
        "model_path": domain_path(f"models/{entity}.yaml"),
    }
