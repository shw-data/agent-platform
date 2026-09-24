# Testing Guide — DuckDB CLI, Test Data, and Test Scenarios

This is for anyone who's followed [RUNBOOK.md](RUNBOOK.md) and has
everything installed, and now wants to actually exercise the platform and
see it work. Every command and expected result below was run for real
while writing this document — not theoretical.

**The main thing to test is §5, Scenario 3** — the actual Omnigent
Coordinator, generating and validating on its own. §4's Scenarios 1 and 2
are optional manual/scripted checks: useful for debugging or CI, but they
bypass Omnigent entirely and call the Python tool functions directly, so
they don't prove the conversational agent itself works.

---

## 1. Install the DuckDB CLI

This is the actual `duckdb` command-line tool for poking around the
database interactively — separate from the `duckdb` *Python package*
(already in `requirements.txt`), which is what the code itself uses.

```bash
curl https://install.duckdb.org | sh
```

Installs to `~/.duckdb/cli/latest/duckdb`, and symlinks it into
`~/.local/bin/duckdb` (already on PATH if you followed the runbook).
Confirm it worked:

```bash
duckdb --version
```

---

## 2. How to query the warehouse

**One-off query from the shell** (no need to open an interactive session):
```bash
cd data-domain
duckdb data/warehouse.duckdb -c "SHOW TABLES;"
duckdb data/warehouse.duckdb -c "SELECT * FROM raw_customers LIMIT 5;"
duckdb data/warehouse.duckdb -c "SELECT COUNT(*) FROM raw_customers;"
```

**Interactive session** (for poking around, running several queries in a
row):
```bash
duckdb data/warehouse.duckdb
```
This drops you into a `D` prompt. Standard SQL works; end statements with
`;`. Useful ones while testing:
```sql
SHOW TABLES;
DESCRIBE raw_customers;
SELECT * FROM raw_customers WHERE status NOT IN ('active','inactive','pending');
SELECT customer_id, COUNT(*) FROM raw_customers GROUP BY customer_id HAVING COUNT(*) > 1;
.quit
```

**Through the platform's own guarded tool** (read-only, same restrictions
the agent itself has — `SELECT`/`DESCRIBE`/`SHOW`/`PRAGMA` only, single
statement):
```bash
cd agent-platform
.venv/bin/python -c "
from tools.tools import run_duckdb
print(run_duckdb('SELECT * FROM raw_customers LIMIT 5'))
"
```

---

## 3. Generating, resetting, and customizing test data

The seed data is small and synthetic on purpose — no real data ever
needed. Each entity has its own generator script in `data/`, all following
the same pattern:

| Entity | Generator (edit this to customize) | Loader | Raw table |
|---|---|---|---|
| `customer` | `data/generate_synthetic_customers.py` | `data/load_raw_to_duckdb.py` | `raw_customers` *(legacy name — see the naming note in §6)* |
| `product` | `data/generate_synthetic_products.py` | `data/load_products_to_duckdb.py` | `product` |

Both generators are **seeded** (`random.seed(...)` near the top), so
running one again always produces the exact same rows — reproducible by
design.

### Basic usage

```bash
cd data-domain
python3 data/generate_synthetic_customers.py   # writes data/raw/customers.csv
python3 data/load_raw_to_duckdb.py               # loads it into data/warehouse.duckdb
```

### How to customize the generated data

Open the generator file for the entity you want to change (e.g.
[`data/generate_synthetic_customers.py`](../../data-domain/data/generate_synthetic_customers.py)
in `data-domain`) — it's a plain script, not a config file, so any change
is just editing Python:

- **More/fewer rows**: change the `range(1, 23)` loop bound in the "Valid
  rows" section.
- **Different value pools**: edit the `FIRST_NAMES` / `LAST_NAMES` /
  `COUNTRIES` / `STATUSES` lists near the top.
- **Add a new kind of deliberately-broken row**: copy one of the numbered
  blocks under "Problematic rows" and change the field that's wrong — each
  one is just a dict appended to `rows`, with a comment explaining what
  makes it invalid and which validation rule it should trip.
- **Different date ranges**: edit the `start`/`end` dates in
  `random_dob()` / `random_signup()`.

After editing, regenerate and reload:
```bash
python3 data/generate_synthetic_customers.py
python3 data/load_raw_to_duckdb.py
```

### Full reset to a clean baseline

```bash
cd data-domain
rm -f data/warehouse.duckdb data/raw/customers.csv data/raw/products.csv
python3 data/generate_synthetic_customers.py
python3 data/load_raw_to_duckdb.py
python3 data/generate_synthetic_products.py
python3 data/load_products_to_duckdb.py
git checkout -- notebooks/customer_transformation.py    # restore the original notebook
python3 notebooks/customer_transformation.py             # rebuild customer_target from it
rm -f notebooks/product_transformation.py                # product has no notebook yet - that's the point of Scenario 5
```

**What's in the customer data (30 rows)**: 22 clean rows, plus 8
deliberately broken ones — a duplicate `customer_id` (`CUST0001`), a null
`dob`, a malformed `dob` (`14/03/1985`, not ISO), a null `email`, a
malformed email, an invalid `status` (`unknown_status`), an invalid
country code (`ZZ9`), and a future `signup_date`. These are supposed to
stay broken and keep failing validation — that's what Scenario 1 tests.

**What's in the product data (20 rows)**: 15 clean rows, plus 5
deliberately broken ones — a missing `sku`, a duplicate `product_id`
(`PROD0001`), an invalid `status`, a future `launch_date`, and a missing
`price`.

---

## 4. Manual/optional scenarios (bypass Omnigent, call the tools directly)

These are quick sanity checks and regression tests — good for debugging a
specific tool function or for a CI check, but they don't exercise the
conversational agent. Skip to §5 if you only want to test the real thing.

### Scenario 1 (manual) — Deterministic validation catches the known bad rows

**What it proves**: the validation layer genuinely checks data quality,
with zero LLM involvement, and catches exactly the issues that are there.

```bash
cd agent-platform
.venv/bin/python -c "
from tools.tools import run_validation
import json
print(json.dumps(run_validation('customer', 'raw_customers'), indent=2))
"
```

**Expected result**: `"passed": false`, with exactly **7** errors:
`required_field_not_null` (×2, for `birth_date` and `email`),
`allowed_values` (`status`), `uniqueness` (`customer_id`), `format_email`,
`format_country_code`, `constraint_not_in_future`. If you get a different
count, something about the seed data or the notebook has changed from
baseline — reset per §3 and try again.

### Scenario 2 (manual) — A notebook change is actually picked up (regression test)

**What it proves**: `run_validation` genuinely re-executes the current
notebook before checking — not a stale cached result. (This was a real bug
we found and fixed; this scenario exists specifically to catch it if it
ever comes back.)

```bash
cd agent-platform
.venv/bin/python -c "
from tools.tools import write_notebook, run_validation

deduped = '''import duckdb

con = duckdb.connect(\"data/warehouse.duckdb\")

con.execute(\"\"\"
CREATE OR REPLACE TABLE customer_target AS
SELECT
    CAST(customer_id AS VARCHAR) AS customer_id,
    CAST(first_name AS VARCHAR) AS first_name,
    CAST(last_name AS VARCHAR) AS last_name,
    TRY_CAST(dob AS DATE) AS birth_date,
    CAST(LOWER(email) AS VARCHAR) AS email,
    CAST(country AS VARCHAR) AS country,
    TRY_CAST(signup_date AS DATE) AS signup_date,
    CAST(status AS VARCHAR) AS status
FROM raw_customers
QUALIFY row_number() OVER (PARTITION BY customer_id ORDER BY customer_id) = 1
\"\"\")

con.close()
'''
write_notebook('customer', deduped)
r = run_validation('customer', 'raw_customers')
print('error count:', len(r['errors']))
print('rules:', [e['rule'] for e in r['errors']])
"
```

**Expected result**: error count drops from 7 to **6** — `uniqueness` is
gone (the `QUALIFY` clause dedups `CUST0001`), every other error stays.
If the count is still 7, `run_validation` isn't re-executing the notebook
— that's the bug from before.

**Reset after this one** (§3's full reset) before continuing — the
notebook on disk right now is the dedup test version, not the baseline.

---

## 5. The main test: the real Omnigent Coordinator, end to end

This is the one that actually matters — it exercises the conversational
agent itself, not just the underlying Python functions.

### Scenario 3 (primary) — Full Coordinator run against the existing `customer` entity

**What it proves**: the conversational agent actually calls the right
tools in the right order on its own, without a hand-written loop telling
it what to do next.

```bash
cd data-domain
PYTHONPATH=/absolute/path/to/agent-platform omnigent run /absolute/path/to/agent-platform/omnigent/coordinator.yaml
```
Send this message:
> Generate and validate a transformation notebook for the customer entity.

**Expected behavior**: you should see it call, in order, `read_contract`,
`read_schema`, `read_mapping`, `read_model` (or a subset, if it already
has context), then `generate_notebook_code`, `write_notebook`, and
`run_validation`. It should report back the same 7 errors as Scenario 1
(assuming a clean baseline) and correctly say these are known data-quality
issues for a human to review — not something to keep retrying forever.

**If validation fails for a genuinely fixable reason** (e.g. you'd broken
the notebook's `TRY_CAST` beforehand), it should call `generate_notebook_code`
again with the errors, `write_notebook` the fix, and `run_validation` again
— capped at a few attempts, not an infinite loop.

### Scenario 4 (primary) — The human-approval gate actually blocks

**What it proves**: the agent cannot touch git without your explicit
approval — the whole point of the human-in-the-loop design.

Continue the same session from Scenario 3, or start fresh with:
> Generate and validate a transformation notebook for the customer entity,
> then commit it to a new branch.

**Expected behavior**: when it attempts any shell/git action, Omnigent's
`ask_on_os_tools` policy should pause and prompt you to approve or deny —
visible either in the terminal or the web UI as an approval card.

- **Approve it** → confirm afterward with `git -C data-domain log --oneline -3`
  and `git -C data-domain branch` that a real branch/commit was created.
- **Deny it** → confirm nothing happened: `git -C data-domain status` should
  show no new branch or commit, only the notebook file still sitting
  modified on disk.

Try both at least once — approving is the common path, but denying is what
proves the gate is a real block and not just cosmetic.

### Scenario 5 (primary) — A brand-new entity, added the way a real data engineer would

**What it proves**: adding a new table to the platform is purely a data
engineering action — four new YAML files plus source data — with **zero
changes to `agent-platform`'s code**. This is the real test of whether the
platform generalizes, not just whether `customer` specifically works.

The `product` entity is already set up as this scenario's starting point:
`contracts/product.yaml`, `schemas/product.yaml`, `mappings/product.yaml`,
`models/product.yaml` all exist, and `data/generate_synthetic_products.py`
+ `data/load_products_to_duckdb.py` have already loaded 20 rows (15 clean,
5 deliberately broken — see §3) into a table named **`product`** — no
notebook exists for it yet.

```bash
cd data-domain
PYTHONPATH=/absolute/path/to/agent-platform omnigent run /absolute/path/to/agent-platform/omnigent/coordinator.yaml
```
Send this message:
> Generate and validate a transformation notebook for the product entity.

**Expected behavior**: same tool sequence as Scenario 3
(`read_contract("product")` → ... → `generate_notebook_code("product", "product")`
→ `write_notebook` → `run_validation("product", "product")`), producing a
brand-new `notebooks/product_transformation.py` and reporting **5** errors:
`required_field_not_null` (×2, `sku` and `price`), `allowed_values`
(`status`), `uniqueness` (`product_id`), `constraint_not_in_future`
(`launch_date`). This was confirmed to work exactly this way — including
the agent correctly inferring `TRY_CAST`/`LOWER()` usage from the mapping's
`transform`/`cast` fields for an entity it had never seen before — with no
`agent-platform` code changes at all.

**To build your own third entity** instead of using `product`, follow the
same shape: `contracts/<entity>.yaml`, `schemas/<entity>.yaml` (see §6 for
why this one doesn't get a `_source` suffix), `mappings/<entity>.yaml`,
`models/<entity>.yaml`, a `data/generate_synthetic_<entity>s.py`, and a
loader that creates a raw table **named exactly like the entity** (see
§6). **Avoid SQL reserved words as entity/table names** (`order`, `group`,
`table`, etc.) — DuckDB will reject unquoted queries against them; that's
why this example is `product`, not `order`.

---

## 6. A naming note: file names, table names, and the entity name should match

For any entity you add, keep this 1:1: if the entity is `product`, the
files are `contracts/product.yaml`, `schemas/product.yaml`,
`mappings/product.yaml`, `models/product.yaml` (no suffixes), and the raw
source table is named `product` too. This is exactly what `product` does
above, and it's why Scenario 5's `generate_notebook_code('product',
'product')` call doesn't need to guess anything.

**`customer` is a legacy exception to this**, predating the convention:
its raw table is `raw_customers` (prefixed and pluralized, not just
`customer`), which doesn't follow the entity name mechanically — that's
why every example involving it explicitly passes `"raw_customers"` as
`source_table` rather than deriving it, and why the Coordinator's own
prompt tells it to run `run_duckdb("SHOW TABLES")` to discover a source
table name if it's ever unsure. The schema file itself used to compound
this (`schemas/customer_source.yaml`, with an internal `name:
customer_source` that didn't match the entity name either) — that part
has been fixed, so schema files now follow the same no-suffix convention
as contracts/mappings/models for every entity, including `customer`.

Keeping new entities fully 1:1 (file names, `source_table`, target table
all derived from the entity name with no exceptions) is what lets the
Coordinator work generically, without needing a lookup table or a `SHOW
TABLES` guess every time.

---

## 7. After you're done testing

Reset to a clean baseline (§3) so the next person (or your next test run)
starts from the same known state, and check `git status` in both repos to
make sure nothing unexpected got committed.
