"""Deterministic validation rules.

Every check here is plain Python + DuckDB SQL against the Canonical
Specification - no LLM involved. The Validation Agent (Phase 7) calls
these functions; Claude is only used later to explain/summarize results,
never to decide pass/fail.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import duckdb


@dataclass
class ValidationError:
    rule: str
    field: Optional[str]
    message: str


@dataclass
class ValidationReport:
    errors: list

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0

    def summary(self) -> str:
        if self.passed:
            return "PASS - all checks succeeded"
        lines = [f"FAIL - {len(self.errors)} error(s):"]
        for e in self.errors:
            lines.append(f"  [{e.rule}] field={e.field}: {e.message}")
        return "\n".join(lines)


def _table_columns(con, table: str) -> dict:
    rows = con.execute(f"DESCRIBE {table}").fetchall()
    return {r[0]: r[1] for r in rows}


_TYPE_ALIASES = {
    "STRING": "VARCHAR",
    "TEXT": "VARCHAR",
    "CHAR": "VARCHAR",
}


def _normalize_type(type_name: str) -> str:
    type_name = type_name.upper().split("(")[0].strip()
    return _TYPE_ALIASES.get(type_name, type_name)


def validate_source_columns_exist(spec, con, source_table) -> list:
    errors = []
    try:
        cols = _table_columns(con, source_table)
    except duckdb.Error as e:
        return [ValidationError("source_table_exists", None, f"Source table '{source_table}' not found: {e}")]
    for f in spec.fields:
        if f.source_field not in cols:
            errors.append(ValidationError(
                "source_column_exists", f.source_field,
                f"Required source column '{f.source_field}' not found in '{source_table}'",
            ))
    return errors


def validate_target_schema(spec, con, target_table) -> list:
    errors = []
    try:
        cols = _table_columns(con, target_table)
    except duckdb.Error as e:
        return [ValidationError("target_table_exists", None, f"Target table '{target_table}' not found: {e}")]

    for f in spec.fields:
        if f.target_field not in cols:
            errors.append(ValidationError(
                "target_column_exists", f.target_field,
                f"Required target column '{f.target_field}' not found in '{target_table}'",
            ))
            continue
        actual_type = _normalize_type(cols[f.target_field])
        expected_type = _normalize_type(f.target_type)
        if actual_type != expected_type:
            errors.append(ValidationError(
                "target_column_type", f.target_field,
                f"Column '{f.target_field}' has type {actual_type}, expected {expected_type} "
                f"(mapping: {f.source_field} -> {f.target_field} via cast={f.cast})",
            ))
    return errors


def validate_required_fields(spec, con, target_table) -> list:
    errors = []
    for f in spec.fields:
        if not f.required:
            continue
        try:
            n = con.execute(
                f"SELECT COUNT(*) FROM {target_table} WHERE {f.target_field} IS NULL"
            ).fetchone()[0]
        except duckdb.Error:
            continue  # missing-column case already reported by validate_target_schema
        if n > 0:
            errors.append(ValidationError(
                "required_field_not_null", f.target_field,
                f"{n} row(s) have NULL in required field '{f.target_field}'",
            ))
    return errors


def validate_allowed_values(spec, con, target_table) -> list:
    errors = []
    for f in spec.fields:
        if not f.allowed_values:
            continue
        allowed = ", ".join(f"'{v}'" for v in f.allowed_values)
        try:
            rows = con.execute(
                f"SELECT DISTINCT {f.target_field} FROM {target_table} "
                f"WHERE {f.target_field} IS NOT NULL AND {f.target_field} NOT IN ({allowed})"
            ).fetchall()
        except duckdb.Error:
            continue
        if rows:
            bad = ", ".join(str(r[0]) for r in rows)
            errors.append(ValidationError(
                "allowed_values", f.target_field,
                f"Column '{f.target_field}' has values outside {f.allowed_values}: {bad}",
            ))
    return errors


def validate_uniqueness(spec, con, target_table) -> list:
    errors = []
    for f in spec.fields:
        if not f.unique:
            continue
        try:
            n = con.execute(
                f"SELECT COUNT(*) FROM ("
                f"  SELECT {f.target_field} FROM {target_table} "
                f"  GROUP BY {f.target_field} HAVING COUNT(*) > 1"
                f") dup"
            ).fetchone()[0]
        except duckdb.Error:
            continue
        if n > 0:
            errors.append(ValidationError(
                "uniqueness", f.target_field,
                f"Column '{f.target_field}' has {n} duplicate value(s)",
            ))
    return errors


def validate_data_quality(spec, con, target_table) -> list:
    """Format/constraint checks driven by the contract (spec item 8)."""
    errors = []
    for f in spec.fields:
        try:
            con.execute(f"SELECT {f.target_field} FROM {target_table} LIMIT 0")
        except duckdb.Error:
            continue  # missing-column case already reported by validate_target_schema

        if f.format == "email":
            n = con.execute(
                f"SELECT COUNT(*) FROM {target_table} "
                f"WHERE {f.target_field} IS NOT NULL AND {f.target_field} NOT LIKE '%@%.%'"
            ).fetchone()[0]
            if n > 0:
                errors.append(ValidationError(
                    "format_email", f.target_field, f"{n} row(s) fail email format check",
                ))

        if f.format == "iso_country_code_2":
            n = con.execute(
                f"SELECT COUNT(*) FROM {target_table} "
                f"WHERE {f.target_field} IS NOT NULL AND LENGTH({f.target_field}) != 2"
            ).fetchone()[0]
            if n > 0:
                errors.append(ValidationError(
                    "format_country_code", f.target_field, f"{n} row(s) fail 2-letter country code check",
                ))

        if f.constraint == "not_in_future":
            try:
                n = con.execute(
                    f"SELECT COUNT(*) FROM {target_table} "
                    f"WHERE {f.target_field} IS NOT NULL AND CAST({f.target_field} AS DATE) > current_date"
                ).fetchone()[0]
            except duckdb.Error as e:
                errors.append(ValidationError(
                    "constraint_not_in_future", f.target_field,
                    f"Could not evaluate not-in-future constraint (likely malformed date value): {e}",
                ))
                continue
            if n > 0:
                errors.append(ValidationError(
                    "constraint_not_in_future", f.target_field,
                    f"{n} row(s) have {f.target_field} in the future",
                ))
    return errors


def run_validation(spec, con, source_table, target_table) -> ValidationReport:
    errors = []
    errors += validate_source_columns_exist(spec, con, source_table)
    errors += validate_target_schema(spec, con, target_table)
    errors += validate_required_fields(spec, con, target_table)
    errors += validate_allowed_values(spec, con, target_table)
    errors += validate_uniqueness(spec, con, target_table)
    errors += validate_data_quality(spec, con, target_table)
    return ValidationReport(errors=errors)


if __name__ == "__main__":
    import duckdb as _duckdb
    from orchestrator.canonical_spec import build_canonical_specification

    spec = build_canonical_specification(
        contract_path="contracts/customer.yaml",
        schema_path="schemas/customer_source.yaml",
        mapping_path="mappings/customer.yaml",
        model_path="models/customer.yaml",
    )

    con = _duckdb.connect("data/warehouse.duckdb")

    print("--- Validating raw_customers AGAINST the target spec (expected to FAIL) ---")
    report = run_validation(spec, con, source_table="raw_customers", target_table="raw_customers")
    print(report.summary())

    con.close()
