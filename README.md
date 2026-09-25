# agent-platform

An AI agent platform for data engineering: given a human-ratified data
contract, an agent drafts the DuckDB/Python code that transforms raw data
into the contract's shape, a deterministic rule engine checks its work with
zero LLM calls, and a human approves and merges the result. The agent never
edits a contract and never merges its own code — it only proposes, through a
narrow, permissioned tool layer, gated by [Omnigent](https://github.com/omnigent-ai/omnigent)'s
human-approval policy before anything git/PR-shaped happens.

**Setup, installation, and troubleshooting live in
[docs/RUNBOOK.md](docs/RUNBOOK.md)** — including WSL2/Ubuntu setup from
scratch, all prerequisites, a from-zero walkthrough for both repos, and a
table of every known issue with its fix. Once that's done, **[docs/TESTING.md](docs/TESTING.md)**
has the DuckDB CLI, how to query the warehouse, generating test data, and
5 scenarios (including adding a brand-new entity) to actually exercise the
platform. **[docs/OMNIGENT.md](docs/OMNIGENT.md)** explains Omnigent itself
— how the tool-calling loop works, the config file structure, and
production-grade conventions (splitting sub-agents into their own files,
policy layering, and more) — read that to understand *why*
`omnigent/coordinator.yaml` is built the way it is. This file is the
architecture overview; start in the runbook if you're setting up for the
first time.

---

## Architecture

Two teams own two different things, in two different repos, connected only
through a guarded tool layer — and Omnigent sits on top as the
conversational layer that decides what to do and pauses for a human before
anything risky.

```mermaid
flowchart TB
    HUMAN1(("👤 Data engineer<br/>ratifies contracts"))
    HUMAN2(("👤 Any human<br/>approves &amp; merges"))

    subgraph DD["📁 data-domain repo — owned by data engineers"]
        direction TB
        C["📄 contracts/&lt;entity&gt;.yaml<br/><i>business rules: required, unique,<br/>allowed values, format, constraints</i>"]
        S["📄 schemas/&lt;entity&gt;.yaml<br/><i>raw column names &amp; types,<br/>ground truth for what's really there</i>"]
        M["📄 mappings/&lt;entity&gt;.yaml<br/><i>source→target field rename +<br/>transform + cast, per field</i>"]
        MD["📄 models/&lt;entity&gt;.yaml<br/><i>target table shape: columns,<br/>types, primary key, nullability</i>"]
        NB["🐍 notebooks/&lt;entity&gt;_transformation.py<br/><i>agent-drafted, human-merged</i>"]
        DBX[("🗄️ data/warehouse.duckdb")]
    end

    subgraph AP["📁 agent-platform repo — owned by the platform team"]
        direction TB
        SPEC["🔧 canonical_spec.py<br/><i>merges the 4 YAMLs into ONE spec,<br/>deterministic, 0 LLM calls</i>"]
        VA["✅ validation_agent.py +<br/>validation/rules.py<br/><i>executes the notebook, then runs<br/>8 deterministic checks — 0 LLM calls.<br/>This is the real pass/fail authority</i>"]
        TOOLS["🔒 tools/tools.py<br/><i>the ONLY door into data-domain —<br/>each function scoped to one folder,<br/>rejects path traversal &amp; unsafe SQL</i>"]
    end

    subgraph OG["💬 Omnigent — every LLM call, one governed system"]
        direction TB
        COORD["🧭 coordinator.yaml (main agent)<br/><i>the system prompt + tool list —<br/>decides at runtime what to call next,<br/>instead of a hand-written retry loop</i>"]
        NA["🤖 notebook_drafter (sub-agent)<br/><i>declared in the SAME coordinator.yaml —<br/>narrow prompt forbids inventing<br/>casts/transforms not in the spec.<br/>Dispatched &amp; tracked by Omnigent itself,<br/>not a hidden separate API call</i>"]
        POLICY["🛡️ ask_on_os_tools policy<br/><i>builtin — pauses for approval<br/>before any shell/git action, at zero<br/>cost in custom code</i>"]
    end

    HUMAN1 -.->|writes / ratifies| C
    HUMAN1 -.->|defines| S
    HUMAN1 -.->|defines| M
    HUMAN1 -.->|defines| MD

    C --> SPEC
    S --> SPEC
    M --> SPEC
    MD --> SPEC
    COORD -->|build_canonical_spec| SPEC
    SPEC -->|canonical spec JSON| COORD
    COORD -->|"delegates: spec + tables"| NA
    NA -->|draft code| COORD
    COORD -->|write_notebook| TOOLS
    TOOLS --> NB
    NB -->|executed as a subprocess| VA
    DBX <-->|reads raw, checks target| VA
    VA -->|pass / fail + errors| COORD
    COORD -->|"code-level bug → retry"| NA
    COORD -->|"ambiguous / same error<br/>repeats → ask, don't loop"| HUMAN2
    COORD -->|validation passed| POLICY
    POLICY -->|"ASK: approve?"| HUMAN2
    HUMAN2 -->|approve| GIT["🌿 git branch + commit<br/><i>inside data-domain, never<br/>agent-platform</i>"]
    GIT --> PR["🔀 Pull Request"]
    PR -->|review &amp; merge| HUMAN2

    style HUMAN1 fill:#2d6a4f,color:#fff
    style HUMAN2 fill:#2d6a4f,color:#fff
    style POLICY fill:#7f1d1d,color:#fff
    style VA fill:#1e3a5f,color:#fff
```

**How to read it**: a human owns everything in the top-left box — that
never gets edited by the agent. The Coordinator merges those four files
into one shared spec via a deterministic tool call (`build_canonical_spec`
— zero LLM involved), then **delegates** drafting to `notebook_drafter`, a
declared Omnigent sub-agent with its own narrow prompt — not a hidden,
separate API call your own code makes behind Omnigent's back. Every LLM
call in the system goes through Omnigent this way: tracked, governed by
the same policies, using whatever model/credential you actually
configured. A completely separate, zero-LLM rule engine is the real judge
of whether the drafted code is correct — not the Coordinator's own opinion,
and not `notebook_drafter`'s own confidence in its work either. The
Coordinator decides whether to retry, when to stop and ask a human because
something looks like a genuine contract gap rather than a code bug, and —
always, with no exception coded anywhere — pauses for explicit human
approval before touching git. A human reviews and merges the PR; the agent
never does either.

### Why it's split this way

| Design choice | Why |
|---|---|
| Two repos, not one | Each team ships independently. The agent has no filesystem access to `data-domain` except through `tools/tools.py` — verified against path-traversal and SQL-injection attempts in `tests/test_mcp_tools.py`. |
| One canonical spec, not four separate YAML parses | The notebook agent and the validator must agree on requirements byte-for-byte — merging once removes an entire class of "they each read the contract slightly differently" bugs. |
| Deterministic validation, zero LLM calls | The agent's own read of its code is never the authority. 8 rule functions either pass or don't — no ambiguity, no cost, no chance of the model rating its own work favorably. |
| Omnigent's Coordinator instead of a hand-written retry loop | Lets the agent reason about *why* something failed — a code bug worth retrying vs. a genuine contract gap worth asking a human about — rather than mechanically retrying every failure the same way. |
| `notebook_drafter` as a declared Omnigent sub-agent, not a raw API call | Code drafting genuinely needs an LLM — that's not avoidable — but it should happen *through* Omnigent (tracked, governed by policies, using your configured model/credential), not via a separate `Anthropic()` client hidden inside a Python tool that Omnigent can't see. Every LLM call in the system goes through one governed layer. |
| `ask_on_os_tools`, a builtin policy, not custom code | The Coordinator's only shell usage is git/PR actions, so this one built-in policy gates exactly the risky step, for free. |

---

## Repo layout

```
agent-platform/
├── orchestrator/     canonical_spec.py, config.py
├── agents/            validation_agent.py
├── validation/        rules.py (the 8 deterministic checks)
├── tools/              tools.py (the guarded tool layer)
├── omnigent/           coordinator.yaml (main agent + notebook_drafter sub-agent + policy)
├── tests/              test_mcp_tools.py
└── docs/
    ├── RUNBOOK.md      ← full setup, install, and troubleshooting guide
    ├── TESTING.md      ← DuckDB CLI, test data, and test scenarios
    └── OMNIGENT.md     ← how Omnigent works, config structure, conventions
```
