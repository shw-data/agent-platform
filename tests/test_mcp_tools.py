"""Demo/test for mcp/tools.py - the controlled tool layer.

Plain assert-based script (stdlib only, no pytest dependency) proving:
1. The legitimate tool calls work end-to-end.
2. The guardrails actually reject path-escape and non-SELECT attempts,
   which is the entire point of this layer existing.

Run with: python -m tests.test_mcp_tools
"""
import duckdb

from tools.tools import (
    ToolAccessError,
    read_contract,
    read_mapping,
    read_model,
    read_notebook,
    read_schema,
    run_duckdb,
    run_validation,
    write_notebook,
)

print("--- Legitimate calls ---")

contract = read_contract("customer")
assert contract["name"] == "customer"
print(f"read_contract('customer'): OK ({len(contract['requirements'])} requirements)")

schema = read_schema("customer")
assert schema["name"] == "customer"
print(f"read_schema('customer'): OK ({len(schema['columns'])} columns)")

mapping = read_mapping("customer")
assert mapping["name"] == "customer_mapping"
print(f"read_mapping('customer'): OK ({len(mapping['mappings'])} field mappings)")

model = read_model("customer")
assert model["name"] == "customer_target"
print(f"read_model('customer'): OK ({len(model['columns'])} columns)")

code = read_notebook("customer")
assert "duckdb" in code
print(f"read_notebook('customer'): OK ({len(code)} chars)")

write_notebook("_scratch_roundtrip_test", "# scratch\nprint('hello')\n")
roundtrip = read_notebook("_scratch_roundtrip_test")
assert roundtrip == "# scratch\nprint('hello')\n"
from orchestrator.config import DOMAIN_REPO
(DOMAIN_REPO / "notebooks" / "_scratch_roundtrip_test_transformation.py").unlink()
print("write_notebook -> read_notebook round-trip: OK")

rows = run_duckdb("SELECT COUNT(*) AS n FROM raw_customers")
assert rows[0]["n"] == 30
print(f"run_duckdb(SELECT COUNT...): OK ({rows[0]['n']} rows)")

report = run_validation("customer", "raw_customers")
print(f"run_validation('customer', 'raw_customers'): OK (passed={report['passed']}, {len(report['errors'])} error(s))")

print("\n--- Guardrail checks (these MUST raise) ---")

try:
    read_contract("../../../../etc/passwd")
    raise SystemExit("FAIL: path escape via read_contract was NOT blocked")
except ToolAccessError as e:
    print(f"read_contract('../../../../etc/passwd') correctly blocked: {e}")

try:
    read_notebook("../../../../etc/shadow")
    raise SystemExit("FAIL: path escape via read_notebook was NOT blocked")
except ToolAccessError as e:
    print(f"read_notebook('../../../../etc/shadow') correctly blocked: {e}")

try:
    run_duckdb("DROP TABLE raw_customers")
    raise SystemExit("FAIL: destructive SQL was NOT blocked")
except ToolAccessError as e:
    print(f"run_duckdb('DROP TABLE ...') correctly blocked: {e}")

try:
    run_duckdb("SELECT * FROM raw_customers; ATTACH '/etc/passwd' AS x")
    raise SystemExit("FAIL: multi-statement SQL injection was NOT blocked")
except (ToolAccessError, duckdb.Error) as e:
    print(f"multi-statement injection attempt correctly rejected: {type(e).__name__}")

print("\nAll checks passed.")
