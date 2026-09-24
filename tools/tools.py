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


def read_notebook(name: str) -> str:
    """Read a notebook's source code, restricted to notebooks/ under the domain repo."""
    path = _safe_path("notebooks", f"{name}.py")
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist")
    return path.read_text(encoding="utf-8")


def write_notebook(name: str, code: str) -> str:
    """Write a notebook's source code, restricted to notebooks/ under the domain repo."""
    path = _safe_path("notebooks", f"{name}.py")
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
    db_path = _safe_path("data", f"{db_name}.duckdb")
    con = duckdb.connect(str(db_path))
    try:
        result = con.execute(stripped)
        cols = [d[0] for d in result.description]
        rows = result.fetchall()
        return [dict(zip(cols, row)) for row in rows]
    finally:
        con.close()


def run_validation(target_table: str = "customer_target") -> dict:
    """Run the deterministic validation rules for the customer entity."""
    from orchestrator.canonical_spec import build_canonical_specification
    from orchestrator.config import customer_spec_paths
    from validation.rules import run_validation as _run_validation

    spec = build_canonical_specification(**customer_spec_paths())
    db_path = _safe_path("data", "warehouse.duckdb")
    con = duckdb.connect(str(db_path))
    try:
        report = _run_validation(spec, con, "raw_customers", target_table)
    finally:
        con.close()

    return {
        "passed": report.passed,
        "errors": [
            {"rule": e.rule, "field": e.field, "message": e.message}
            for e in report.errors
        ],
    }
