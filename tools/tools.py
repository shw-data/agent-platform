"""Controlled tool layer for the data-domain repo.

This is the lightweight, in-process version of Phase 9's "MCP tools":
each function here is scoped to one specific subdirectory of the domain
repo and rejects any name that would escape it. This is the guardrail
that stands in for "do not give the agent unrestricted filesystem
access" - callers get read_contract("customer"), never open(any_path).

(Upgrade path: these same functions could later be wrapped by the real
MCP protocol - e.g. via the official `mcp` Python SDK's FastMCP - to be
callable from Claude Desktop/Code or any other MCP client. For this POC,
our own orchestrator/agents code is the only caller, so the protocol
machinery isn't worth the extra process + dependency footprint yet.)
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import yaml

from orchestrator.config import DOMAIN_REPO


class ToolAccessError(Exception):
    """Raised when a requested name would escape its allowlisted directory."""


def _safe_path(subdir: str, filename: str) -> Path:
    base = (DOMAIN_REPO / subdir).resolve()
    candidate = (base / filename).resolve()
    if candidate != base and base not in candidate.parents:
        raise ToolAccessError(f"'{filename}' is not allowed under '{subdir}/'")
    return candidate


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def read_contract(name: str) -> dict:
    """Read a contract YAML, restricted to contracts/ under the domain repo."""
    return _load_yaml(_safe_path("contracts", f"{name}.yaml"))


def read_schema(name: str) -> dict:
    """Read a source schema YAML, restricted to schemas/ under the domain repo."""
    return _load_yaml(_safe_path("schemas", f"{name}.yaml"))


def read_mapping(name: str) -> dict:
    """Read a mapping YAML, restricted to mappings/ under the domain repo."""
    return _load_yaml(_safe_path("mappings", f"{name}.yaml"))


def read_model(name: str) -> dict:
    """Read a target model YAML, restricted to models/ under the domain repo."""
    return _load_yaml(_safe_path("models", f"{name}.yaml"))


def _notebook_filename(name: str) -> str:
    # Must match the repo's established convention - e.g. the checked-in
    # notebooks/customer_transformation.py - not just "<name>.py", or
    # write_notebook("customer", ...) silently creates a second, never-run
    # file instead of updating the one run_validation actually executes.
    return f"{name}_transformation.py"


def read_notebook(name: str) -> str:
    """Read a notebook's source code, restricted to notebooks/ under the domain repo."""
    path = _safe_path("notebooks", _notebook_filename(name))
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist")
    return path.read_text(encoding="utf-8")


def write_notebook(name: str, code: str) -> str:
    """Write a notebook's source code, restricted to notebooks/ under the domain repo."""
    path = _safe_path("notebooks", _notebook_filename(name))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")
    return str(path)


_ALLOWED_SQL_PREFIXES = ("SELECT", "DESCRIBE", "SHOW", "PRAGMA")


def run_duckdb(sql: str, db_name: str = "warehouse") -> list:
    """Run a single read-only query against a named DuckDB file under data/
    in the domain repo.

    Only SELECT/DESCRIBE/SHOW/PRAGMA are allowed, and only one statement -
    this tool is for inspection, not DDL/DML. Table creation still happens
    by executing the notebook itself, not through this tool.
    """
    stripped = sql.strip().rstrip(";").strip()
    if ";" in stripped:
        raise ToolAccessError("run_duckdb only allows a single statement (no ';' chaining)")
    if not stripped.upper().startswith(_ALLOWED_SQL_PREFIXES):
        raise ToolAccessError(
            f"run_duckdb only allows {_ALLOWED_SQL_PREFIXES} statements, got: {sql[:40]!r}"
        )
    # Tolerate a caller passing the filename with its extension already
    # (e.g. "warehouse.duckdb") instead of the bare stem this docstring
    # asks for - silently doubling it to "warehouse.duckdb.duckdb" would
    # open/create the wrong file instead of erroring.
    db_name = db_name.removesuffix(".duckdb")
    db_path = _safe_path("data", f"{db_name}.duckdb")
    con = duckdb.connect(str(db_path))
    try:
        result = con.execute(stripped)
        cols = [d[0] for d in result.description]
        rows = result.fetchall()
        return [dict(zip(cols, row)) for row in rows]
    finally:
        con.close()


def run_validation(entity: str, source_table: str) -> dict:
    """Execute an entity's current notebook and validate its output, e.g.
    run_validation("customer", "raw_customers").

    This RUNS notebooks/<entity>_transformation.py first (via subprocess,
    same as a human would), THEN checks the resulting target table against
    the contract - it does not just re-check whatever is already sitting in
    the warehouse from a previous run. Call this after every write_notebook
    to see that specific version's real result, not a stale one.

    source_table is the actual DuckDB table holding raw rows (there's no
    fixed convention tying it to the entity name, so it must be given
    explicitly - use run_duckdb("SHOW TABLES") to discover it if unsure).
    target_table comes from the entity's own spec (spec.target_name).
    """
    from agents.validation_agent import validate_notebook
    from orchestrator.canonical_spec import build_canonical_specification
    from orchestrator.config import spec_paths

    spec = build_canonical_specification(**spec_paths(entity))
    notebook_path = _safe_path("notebooks", _notebook_filename(entity))
    report = validate_notebook(
        spec,
        str(notebook_path),
        source_table=source_table,
        target_table=spec.target_name,
    )

    return {
        "passed": report.passed,
        "errors": [
            {"rule": e.rule, "field": e.field, "message": e.message}
            for e in report.errors
        ],
    }


def generate_notebook_code(
    entity: str,
    source_table: str,
    existing_code: str = None,
    validation_errors: list = None,
) -> str:
    """Generate transformation code for an entity via the Notebook Agent (1 LLM call).

    Returns the generated Python source as a string - call write_notebook()
    separately to persist it. Pass existing_code + validation_errors (as
    returned by run_validation()'s "errors" list) to ask for a fix instead
    of a from-scratch generation.
    """
    from agents.notebook_agent import generate_notebook
    from orchestrator.canonical_spec import build_canonical_specification
    from orchestrator.config import spec_paths
    from validation.rules import ValidationError

    spec = build_canonical_specification(**spec_paths(entity))
    if validation_errors and not isinstance(validation_errors[0], dict):
        raise TypeError(
            "validation_errors must be the exact 'errors' list from run_validation() "
            f"(a list of {{'rule','field','message'}} objects), not {type(validation_errors[0]).__name__} items"
        )
    errors = (
        [ValidationError(**e) for e in validation_errors]
        if validation_errors
        else None
    )
    return generate_notebook(
        spec,
        source_table=source_table,
        target_table=spec.target_name,
        existing_code=existing_code,
        validation_errors=errors,
    )
