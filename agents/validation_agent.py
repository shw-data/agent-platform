"""Validation Agent.

Executes the generated notebook against the local DuckDB database (plain
subprocess - no API call), then runs the deterministic validation rules
(validation/rules.py) against the actual output. Claude is only used -
optionally, and only on explicit request - to produce a human-readable
explanation of failures; it never decides pass/fail.
"""
from __future__ import annotations

import re
import subprocess
import sys

import duckdb

from validation.rules import run_validation, ValidationReport


def _extract_exception_line(stderr: str) -> str:
    """Pick the most specific 'SomeError: message' line out of a traceback."""
    lines = [ln for ln in stderr.strip().splitlines() if ln.strip()]
    for ln in reversed(lines):
        if re.search(r"\w+(Error|Exception):", ln):
            return ln.strip()
    return lines[-1] if lines else stderr


def execute_notebook(notebook_path: str, cwd: str = None) -> tuple[bool, str]:
    """Run the generated notebook as a subprocess (pure local execution, no API calls).

    Runs with cwd set to the domain repo root by default, since the
    generated notebook itself uses paths relative to that repo (e.g.
    "data/warehouse.duckdb") - the same way a data engineer would run it
    locally from within their own repo.

    Returns (success, stderr) instead of raising, so an execution failure
    (e.g. a strict CAST choking on a malformed value) becomes a normal
    ValidationError the correction loop can feed back to the Notebook Agent,
    rather than an uncaught exception that aborts the pipeline.
    """
    result = subprocess.run(
        [sys.executable, notebook_path],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    return result.returncode == 0, result.stderr


def validate_notebook(
    spec,
    notebook_path: str,
    db_path: str = None,
    source_table: str = "raw_customers",
    target_table: str = "customer_target",
    notebook_cwd: str = None,
) -> ValidationReport:
    from orchestrator.config import DOMAIN_REPO, domain_path
    from validation.rules import ValidationError

    if db_path is None:
        db_path = domain_path("data/warehouse.duckdb")
    if notebook_cwd is None:
        notebook_cwd = str(DOMAIN_REPO)

    success, stderr = execute_notebook(notebook_path, cwd=notebook_cwd)
    if not success:
        return ValidationReport(errors=[ValidationError(
            rule="notebook_execution_error",
            field=None,
            message=f"Notebook failed to execute: {_extract_exception_line(stderr)}",
        )])

    con = duckdb.connect(db_path)
    try:
        report = run_validation(spec, con, source_table, target_table)
    finally:
        con.close()

    return report


def explain_report(report: ValidationReport) -> str:
    """Optional: ask Claude to summarize validation errors in plain language.

    Not used for pass/fail - that decision is already made deterministically
    by run_validation(). Costs an API call - call only with user confirmation.
    """
    from anthropic import Anthropic
    from dotenv import load_dotenv

    load_dotenv()
    client = Anthropic()

    response = client.messages.create(
        model="claude-opus-5",
        max_tokens=1024,
        system=(
            "You explain data validation failures to a data engineer in plain, "
            "concise language. Do not add new findings - only explain the ones given."
        ),
        messages=[{
            "role": "user",
            "content": f"Explain these validation results:\n\n{report.summary()}",
        }],
    )
    return "".join(b.text for b in response.content if b.type == "text")


if __name__ == "__main__":
    from orchestrator.canonical_spec import build_canonical_specification
    from orchestrator.config import customer_spec_paths, domain_path

    spec = build_canonical_specification(**customer_spec_paths())

    report = validate_notebook(
        spec, notebook_path=domain_path("notebooks/customer_transformation.py")
    )
    print(report.summary())
