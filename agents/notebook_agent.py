"""Notebook Agent.

Generates or updates the DuckDB/Python transformation notebook from the
Canonical Specification. Uses the Claude API for code generation only -
it does NOT decide correctness itself; that is the Validation Agent's job
(deterministic rules in validation/rules.py).
"""
from __future__ import annotations

import re

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

MODEL = "claude-opus-5"

SYSTEM_PROMPT = """You are a data engineering code generator. You write a single, \
self-contained Python script that uses the duckdb module to transform a \
source table into a target table inside a local DuckDB database file.

Rules:
- Connect to the DuckDB database at "data/warehouse.duckdb".
- Read from the source table and write the result into the target table \
using `CREATE OR REPLACE TABLE <target> AS SELECT ...`.
- Apply exactly the transform/cast specified for each field in the \
Canonical Specification you are given - do not invent your own casts or \
transforms.
- The "lowercase" transform means SQL LOWER(column).
- The "cast_date" transform means casting the source string to DATE.
- The "none" transform means passing the column through unchanged (still \
apply the given `cast` type).
- If a cast could fail on a malformed source value (e.g. a date string not \
in YYYY-MM-DD format), use TRY_CAST instead of CAST for that column, so the \
row gets NULL in that column instead of the whole script crashing. Do not \
add any other error handling, filtering, or logging beyond this.
- Close the connection at the end.
- Output ONLY the raw Python source code. No markdown fences, no \
explanation, no comments about what you changed.
"""


def _extract_code(text: str) -> str:
    text = text.strip()
    match = re.search(r"```(?:python)?\s*(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text


def _build_user_prompt(
    spec,
    source_table: str,
    target_table: str,
    existing_code: str = None,
    validation_errors: list = None,
) -> str:
    parts = [
        "Canonical Specification (JSON):",
        spec.to_json(),
        "",
        f"Source table name: {source_table}",
        f"Target table name: {target_table}",
    ]

    if existing_code and validation_errors:
        parts += [
            "",
            "The following notebook was previously generated but FAILED validation.",
            "Existing notebook code:",
            "```python",
            existing_code,
            "```",
            "",
            "Validation errors to fix:",
        ]
        for e in validation_errors:
            parts.append(f"- [{e.rule}] field={e.field}: {e.message}")
        parts.append("")
        parts.append(
            "Return the FULL corrected Python script that fixes every error above "
            "while continuing to satisfy the Canonical Specification."
        )
    else:
        parts += [
            "",
            "Generate the transformation script from scratch.",
        ]

    return "\n".join(parts)


def generate_notebook(
    spec,
    source_table: str = "raw_customers",
    target_table: str = "customer_target",
    existing_code: str = None,
    validation_errors: list = None,
    max_tokens: int = 4096,
) -> str:
    client = Anthropic()
    user_prompt = _build_user_prompt(
        spec, source_table, target_table, existing_code, validation_errors
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = "".join(block.text for block in response.content if block.type == "text")
    return _extract_code(text)


if __name__ == "__main__":
    from orchestrator.canonical_spec import build_canonical_specification
    from orchestrator.config import customer_spec_paths, domain_path

    spec = build_canonical_specification(**customer_spec_paths())

    code = generate_notebook(spec)
    print(code)

    notebook_path = domain_path("notebooks/customer_transformation.py")
    with open(notebook_path, "w", encoding="utf-8") as f:
        f.write(code)
    print(f"\n--- Saved to {notebook_path} ---")
