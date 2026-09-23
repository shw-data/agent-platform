"""Canonical Specification builder.

Merges the data contract, source schema, mapping, and target model into a
single normalized structure so the Notebook Agent and Validation Agent
share exactly the same interpretation of requirements, instead of each
independently parsing four separate YAML files.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml


@dataclass
class CanonicalField:
    source_field: str
    source_type: str
    target_field: str
    target_type: str
    transform: str
    cast: str
    target_nullable: bool
    required: bool
    unique: bool = False
    primary_key: bool = False
    allowed_values: Optional[list] = None
    format: Optional[str] = None
    constraint: Optional[str] = None


@dataclass
class CanonicalSpecification:
    entity: str
    contract_version: int
    source_name: str
    target_name: str
    fields: list = field(default_factory=list)

    def get_field(self, *, source_field: str = None, target_field: str = None) -> Optional[CanonicalField]:
        for f in self.fields:
            if source_field is not None and f.source_field == source_field:
                return f
            if target_field is not None and f.target_field == target_field:
                return f
        return None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def _load_yaml(path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def build_canonical_specification(
    contract_path,
    schema_path,
    mapping_path,
    model_path,
) -> CanonicalSpecification:
    contract = _load_yaml(contract_path)
    schema = _load_yaml(schema_path)
    mapping = _load_yaml(mapping_path)
    model = _load_yaml(model_path)

    schema_columns = {c["name"]: c for c in schema["columns"]}
    model_columns = {c["name"]: c for c in model["columns"]}
    contract_requirements = {r["field"]: r for r in contract["requirements"]}

    fields = []
    for m in mapping["mappings"]:
        source_field = m["source_field"]
        target_field = m["target_field"]

        src_col = schema_columns.get(source_field)
        if src_col is None:
            raise ValueError(
                f"Mapping references unknown source field '{source_field}' "
                f"not present in {schema_path}"
            )

        tgt_col = model_columns.get(target_field)
        if tgt_col is None:
            raise ValueError(
                f"Mapping references unknown target field '{target_field}' "
                f"not present in {model_path}"
            )

        req = contract_requirements.get(source_field, {})
        tgt_nullable = tgt_col.get("nullable", True)

        fields.append(CanonicalField(
            source_field=source_field,
            source_type=src_col["type"],
            target_field=target_field,
            target_type=tgt_col["type"],
            transform=m.get("transform", "none"),
            cast=m.get("cast", tgt_col["type"]),
            target_nullable=tgt_nullable,
            required=bool(req.get("required", not tgt_nullable)),
            unique=bool(req.get("unique", False)) or bool(tgt_col.get("primary_key", False)),
            primary_key=bool(tgt_col.get("primary_key", False)),
            allowed_values=req.get("allowed_values") or tgt_col.get("allowed_values"),
            format=req.get("format"),
            constraint=req.get("constraint"),
        ))

    return CanonicalSpecification(
        entity=contract["name"],
        contract_version=contract.get("version", 1),
        source_name=schema["name"],
        target_name=model["name"],
        fields=fields,
    )


if __name__ == "__main__":
    from orchestrator.config import customer_spec_paths

    spec = build_canonical_specification(**customer_spec_paths())
    print(spec.to_json())
