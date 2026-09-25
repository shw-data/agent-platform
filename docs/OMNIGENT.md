# Understanding Omnigent

This document explains [Omnigent](https://github.com/omnigent-ai/omnigent)
itself — what it is, how it actually works under the hood, how its
config files are structured, and the conventions worth following for a
production-grade setup. It uses this project's own `omnigent/coordinator.yaml`
as the running example throughout, so everything here is grounded in a
real file, not an abstract description.

This is a companion to [RUNBOOK.md](RUNBOOK.md) (how to install and run
it) and [TESTING.md](TESTING.md) (how to exercise it) — read this one to
understand *how it works*, not how to set it up.

---

## 1. What Omnigent is

Omnigent is an open-source **meta-harness**: a common orchestration layer
that sits on top of coding agents (Claude Code, Codex, Cursor, and others)
or your own custom agents, and adds three things none of them have on
their own:

- **A common way to define an agent** — one YAML file: a system prompt,
  a tool list, and policies — regardless of which underlying model/harness
  actually runs it.
- **Governance** — policies that can pause an agent for your approval,
  cap spend, or restrict which tools it can reach, enforced centrally
  rather than left to each agent's own good behavior.
- **A place to talk to it** — a local web UI, a terminal REPL, and (if
  deployed) your phone, all showing the same live session.

For this project, Omnigent is the layer that turns "run a Python script
that generates a notebook" into "have a conversation with an agent that
decides what to do next, and pauses before anything risky."

---

## 2. How it actually works

At its core, Omnigent runs an ordinary **LLM tool-calling loop**: your
agent's system prompt plus its declared tools go to a model each turn; the
model either replies in plain text or asks to call one of the declared
tools; Omnigent executes that tool and feeds the result back in; the loop
continues until the model has nothing more to do that turn.

What makes an agent's *behavior* is almost entirely the **prompt** — there
is no separate orchestration code deciding "call X, then Y, then Z."
Our `coordinator.yaml`'s numbered workflow (read the spec, delegate
drafting, validate, retry-or-ask, get approval, commit) is not Python
control flow anywhere — it's prose in the `prompt:` field that the model
reads and follows each turn, choosing which declared tool to call next
based on what it's read so far. This is the biggest mental shift from a
traditional pipeline: the workflow lives in English, not in `if`/`else`.

### Three kinds of tools, and they behave very differently

| `type:` | What happens when it's called | Example in this repo |
|---|---|---|
| `function` | Omnigent imports a dotted Python path and calls it **in-process**, in whatever environment is running the Omnigent server (not necessarily your project's own venv — see RUNBOOK §8.1) | `read_contract`, `build_canonical_spec`, `write_notebook`, `run_validation` |
| `agent` | Omnigent starts a genuinely separate **sub-agent session** with its own system prompt, dispatches your message to it, and returns whatever it replies as the tool's result | `notebook_drafter` |
| `mcp` | Omnigent talks to an external MCP server (a local command or a remote URL) and exposes its tools | not used in this repo yet |

The distinction between `function` and `agent` matters more than it looks:
a `function` tool is plain code — deterministic, no LLM involved, cheap,
predictable. An `agent` tool is a real second LLM call, with its own
context, its own reasoning, and its own cost — but *tracked and governed by
Omnigent*, unlike a Python tool that quietly calls an LLM SDK on its own
(a real mistake we made and fixed in this project — see RUNBOOK §8.3).
Deciding which one a given piece of work should be is one of the more
important design calls in any Omnigent agent: if a "tool" needs judgment,
interpretation, or creative output, it should almost always be a sub-agent,
not a function.

### Policies sit outside this loop entirely

Policies aren't something the model chooses to invoke — they're a gate
Omnigent enforces around specific actions regardless of what the model
wants. Our `ask_on_os_tools` policy doesn't appear anywhere in the tool
list the model sees; it silently intercepts any `sys_os_*` call (shell,
file read/write/edit) and pauses for your approval before it's allowed to
execute. This is what makes the human-approval gate real rather than
advisory — the model can *decide* to run `git push`, but it cannot make
that happen without you approving it.

---

## 3. Configuration file structure (annotated against our real file)

```yaml
name: data_eng_coordinator          # identifier shown in sessions/logs
description: >-                     # shown in the UI; not read by the model
  ...

prompt: |                           # THE system prompt - the actual logic
  You are the coordinator...

executor:
  harness: claude-sdk               # which underlying harness/SDK runs this agent

async: true                         # exposes async work tools
cancellable: true                   # session can be cancelled mid-run

os_env:                             # sandbox for sys_os_* (shell/file) tools
  type: caller_process
  cwd: .                            # "wherever omnigent run was launched from"
  sandbox:
    type: linux_bwrap
    write_paths: [.]
    cwd_allow_hidden: [.git]        # otherwise .git is hidden inside the sandbox
    allow_network: true             # otherwise no DNS/network at all
    env_passthrough: [GH_TOKEN]     # otherwise $HOME (and its credentials) are unreachable

tools:                              # the function/agent/mcp tools this agent can call
  read_contract:
    type: function
    callable: tools.tools.read_contract
  notebook_drafter:
    type: agent
    prompt: |
      You are a data engineering code generator...
    executor:
      harness: claude-sdk

policies:                           # gates enforced OUTSIDE the tool-choice loop
  approve_shell:
    type: function
    handler: omnigent.policies.builtins.safety.ask_on_os_tools
```

A few fields worth calling out specifically:

- **`prompt` vs `instructions`** — `instructions: AGENTS.md` is the same
  idea but loaded from a file, useful once a prompt gets long enough that
  you want it reviewable/shareable outside the YAML. We haven't needed
  this yet; worth doing if the Coordinator's prompt keeps growing.
- **`os_env` governs shell/file tools, not `type: function` tools** — this
  tripped us up initially. A `type: function` Python tool runs with
  whatever access the Omnigent server process itself has; `os_env`'s
  sandbox only wraps `sys_os_shell`/`sys_os_read`/`sys_os_write`/`sys_os_edit`
  and terminals. Two separate trust boundaries, easy to conflate.
- **`policies` here are per-agent** — Omnigent also supports server-wide
  policies (in a `server_config.yaml`, applying to every agent) and
  session-level ones (added live, by you or the agent, via chat: *"add a
  policy that asks before shell commands"*). All three layers stack,
  strictest wins.

---

## 4. The request flow, end to end

```
you send a message
        │
        ▼
Coordinator's system prompt + full tool list + conversation so far → model
        │
        ├── model replies in plain text ──────────────► shown to you, turn ends
        │
        └── model asks to call a tool
                │
                ├── type: function → Omnigent imports & runs the Python
                │        function in-process, feeds the return value back
                │
                ├── type: agent → Omnigent starts/continues a SEPARATE
                │        sub-agent session, sends it your message, feeds
                │        its reply back as the tool result
                │
                └── (sys_os_* built-ins) → policies evaluate FIRST;
                         ASK pauses for your approval; only once
                         approved does the actual action run
                        │
                        ▼
        tool result goes back into the conversation, loop continues
        until the model has nothing more to do this turn
```

For our own pipeline specifically, one real "generate and validate" ask
walks: `build_canonical_spec` (function) → delegate to `notebook_drafter`
(agent, its own real LLM call) → `write_notebook` (function) →
`run_validation` (function) → the model's own reasoning decides
retry/ask/proceed → eventually `sys_os_shell` for git, gated by
`ask_on_os_tools`.

---

## 5. Conventions for a production-grade setup

### Split sub-agents into their own files once you have more than one

Omnigent auto-discovers an `agents/` folder next to your main config —
each subfolder is a separate sub-agent, referenced from the parent just by
name:

```
omnigent/
├── coordinator.yaml
└── agents/
    └── notebook_drafter/
        └── config.yaml        # its own prompt, executor, even its own policies
```

```yaml
# coordinator.yaml, instead of an inline block
tools:
  agents:
    - notebook_drafter
```

This is exactly what the bundled `polly` example does in production —
its `pi`/`claude_code`/`codex` workers are each a fully separate
`agents/<name>/config.yaml`, including their own `guardrails.policies`
scoped independently of Polly's own. We're still small enough (one
sub-agent) that an inline block is fine, but this is the pattern to move
to the moment a second one is added — independent code review per prompt,
reusability across coordinators, and per-agent policy scoping, none of
which an inline block gives you.

### Reserve a `skills/` folder for reusable prose instructions

Both bundled examples keep a `skills/<name>/SKILL.md` folder alongside
their `agents/` folder — plain prose files describing a reusable procedure
(Polly's `investigate`, `fanout`, `cross-review` skills). Skills are
authored directly (they're prose, not code) and loaded by name from the
prompt. Worth reaching for once a workflow gets complex enough to want
naming/reuse, rather than growing the main prompt indefinitely.

### Layer policies deliberately: server-wide, per-agent, per-session

Don't put everything in the agent YAML. Spend caps and org-wide
restrictions belong in a server-wide `server_config.yaml` so they apply
uniformly regardless of which agent runs; agent-specific gates (ours:
`ask_on_os_tools`) belong in that agent's own YAML; anything a specific
user wants tightened for their own session, they can add live via chat.
Session policies evaluate first (strictest), then agent, then server-wide
— design for that order.

### Never let a `function` tool make its own hidden LLM call

Covered in depth in RUNBOOK §8.3 — worth restating here as a standing
rule: if a piece of work needs a model's judgment, it's a sub-agent
(`type: agent`), governed and tracked by Omnigent. A Python tool that
imports an LLM SDK and calls it directly is invisible to session tracking,
cost policies, and approval gates — a real anti-pattern, not a shortcut.

### Keep credentials out of the sandbox by design, pass tokens explicitly

`$HOME` is never mounted into the bwrap sandbox, and most dotfiles are
masked by default (RUNBOOK §8.3 has the exact mechanics). Don't fight
this by widening the sandbox — pass exactly the token a specific action
needs via `env_passthrough` (as we do for `GH_TOKEN`), scoped to the
narrowest credential that does the job.

### Watch the `config.yaml` filename trap

A file literally named `config.yaml` is parsed by a different, stricter
schema (the one the bundled examples use — nested `executor.type: omnigent`
+ a required `spec_version`) than any other filename, which uses the
simpler flat `executor.harness:` form this project's files use. Pick any
other name for your own agents (we use `coordinator.yaml`) unless you're
deliberately opting into that format.

---

## 6. Where to read more (official sources)

- **Main repo & README**: [github.com/omnigent-ai/omnigent](https://github.com/omnigent-ai/omnigent)
- **Full agent YAML schema**: [docs/AGENT_YAML_SPEC.md](https://github.com/omnigent-ai/omnigent/blob/main/docs/AGENT_YAML_SPEC.md)
- **Policies — builtins, writing your own, the trust model**: [docs/POLICIES.md](https://github.com/omnigent-ai/omnigent/blob/main/docs/POLICIES.md)
- **Deploying a server (Docker, Render, Railway, Fly.io, etc.)**: [deploy/README.md](https://github.com/omnigent-ai/omnigent/blob/main/deploy/README.md)
- **Bundled examples to read/copy from**: `examples/polly/` (multi-agent
  coding orchestrator, the reference for the `agents/`/`skills/` layout)
  and `examples/debby/` (a smaller two-agent example) in the repo
- **Website**: [omnigent.ai](https://omnigent.ai)
- **Discord**: [discord.gg/omnigent](https://discord.gg/omnigent)
