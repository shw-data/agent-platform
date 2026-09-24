"""Correction loop (Phase 8).

Wires the Notebook Agent and Validation Agent together: validate, and on
failure ask the Notebook Agent to fix the specific errors, up to a capped
number of attempts. Stops as soon as validation passes rather than always
spending the full attempt budget.
"""
from __future__ import annotations

from agents.notebook_agent import generate_notebook
from agents.validation_agent import validate_notebook

FIX_MAX_TOKENS = 2048


def run_correction_loop(
    spec,
    notebook_path: str,
    max_attempts: int = 3,
    source_table: str = "raw_customers",
    target_table: str = "customer_target",
):
    attempt = 1
    report = None

    while attempt <= max_attempts:
        print(f"--- Attempt {attempt}/{max_attempts}: validating {notebook_path} ---")
        report = validate_notebook(
            spec, notebook_path, source_table=source_table, target_table=target_table
        )
        print(report.summary())

        if report.passed:
            print(f"\nPASSED on attempt {attempt}.")
            return report

        if attempt == max_attempts:
            print(f"\nFAILED after {max_attempts} attempt(s). Stopping.")
            return report

        print(f"\nRequesting fix from Notebook Agent (attempt {attempt} failed)...")
        with open(notebook_path, "r", encoding="utf-8") as f:
            existing_code = f.read()

        fixed_code = generate_notebook(
            spec,
            source_table=source_table,
            target_table=target_table,
            existing_code=existing_code,
            validation_errors=report.errors,
            max_tokens=FIX_MAX_TOKENS,
        )
        with open(notebook_path, "w", encoding="utf-8") as f:
            f.write(fixed_code)

        attempt += 1

    return report


if __name__ == "__main__":
    from orchestrator.canonical_spec import build_canonical_specification
    from orchestrator.config import spec_paths, domain_path

    spec = build_canonical_specification(**spec_paths("customer"))

    run_correction_loop(
        spec,
        notebook_path=domain_path("notebooks/customer_transformation.py"),
        max_attempts=3,
    )
