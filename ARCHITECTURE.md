# KeepThem — Architecture

KeepThem is a Python app that models a company as a **FalkorDB property graph** and gives an
AI agent (Claude or a deterministic offline agent) a set of **Cypher-backed tools** to investigate
attrition risk, explain it as graph paths, and write retention actions back to the graph.

> Verified against the code at commit `9009816`. If you change a module, update the matching
> section here and add an entry to [`MEMORY.md`](MEMORY.md).

---

## 1. Tech stack

| Layer | Technology | Where |
|---|---|---|
| Language | Python 3.10+ (uses `X \| None` type hints; tested on 3.13) | everywhere |
| Database | **FalkorDB** graph DB, queried only with **Cypher** (Redis protocol) | `keepthem/db.py` |
| DB client | `falkordb` Python client (`>=1.0.0`) | `requirements.txt` |
| DB, no-Docker fallback | `falkordblite` (embedded FalkorDB, in-process) — optional | `requirements-embedded.txt` |
| DB, container | Docker Compose: `falkordb/falkordb:latest` (6379 + Browser on 3000) | `docker-compose.yml` |
| LLM agent | Anthropic Messages API via `anthropic` SDK, manual tool-use loop (no framework) | `keepthem/agent.py` |
| Default model | `claude-opus-5-5` (override with `KEEPTHEM_MODEL`) | `keepthem/config.py` |
| UI | Streamlit (`>=1.50`) + Graphviz chart for the evidence graph | `app.py` |
| Tool protocol | MCP (`mcp` 1.x `FastMCP` or 2.x `MCPServer`), stdio transport — optional | `keepthem/mcp_server.py` |
| CLI | `argparse` | `keepthem/cli.py` |
| Tests | `pytest` against a real FalkorDB (server or embedded), no DB mocking | `tests/` |

There is **no web/REST API** and **no other data store**: FalkorDB is the only persistence.

---

## 2. Folder structure

```
KeepThem/
├── app.py                     Streamlit UI: chat, reasoning trace (Cypher), evidence graph, "log action" buttons
├── docker-compose.yml         FalkorDB server + FalkorDB Browser
├── requirements.txt           falkordb, anthropic, streamlit, mcp, pytest
├── requirements-embedded.txt  falkordblite (optional, run without Docker)
├── .env.example               all supported environment variables
├── README.md                  product overview, quick start, demo script
├── ARCHITECTURE.md            this file
├── MEMORY.md                  project context + change log for the next developer / AI tool
├── RULES.md                   engineering rules (dependencies, API stability, testing)
├── keepthem/
│   ├── __init__.py            version (0.1.0)
│   ├── config.py              env-driven settings, pinned "today" (AS_OF), risk thresholds
│   ├── db.py                  KnowledgeGraph wrapper: read() = GRAPH.RO_QUERY, write(), Node/Edge/Path → JSON; connect()/get_kg()/set_kg()
│   ├── data.py                synthetic company "Northwind Labs" as plain Python tuples (no logic)
│   ├── seed.py                loads data.py into FalkorDB (indexes, MERGE/CREATE); ensure_seeded()
│   ├── risk.py                risk engine: one Cypher query per signal → points + paths; employee & team assessment
│   ├── actions.py             signals + extra graph lookups → ranked, owned recommendations
│   ├── algorithms.py          graph algorithms (native algo.* + Cypher), registry ALGORITHMS
│   ├── evidence.py            Evidence collector: paths → readable strings + {nodes, edges}
│   ├── tools.py               the 8 agent tools, JSON schemas (TOOLS), dispatcher call_tool(), LLM trimming
│   ├── agent.py               ClaudeAgent (tool-use loop) · OfflineAgent (deterministic router) · make_agent()
│   ├── report.py              Markdown rendering of tool results (offline agent, CLI, UI)
│   ├── cli.py                 CLI: seed | ask | chat | demo | tools | tool | cypher
│   └── mcp_server.py          same 8 tools exposed over MCP (stdio)
├── examples/
│   ├── generate_examples.py   regenerates example_queries.md from real runs on a throw-away graph
│   └── example_queries.md     captured prompts + outputs
└── tests/
    ├── conftest.py            session-scoped throw-away graph `keepthem_test`; skips if no FalkorDB
    └── test_graph_and_tools.py 36 tests (29 functions, 2 parametrized)
```

Dependency direction (no cycles):

```
leaf modules (no internal imports): config, data, evidence, report
db         ← config
seed       ← config, data, db
risk       ← config, db, evidence
actions    ← config, db, evidence, risk
algorithms ← config, db, evidence, risk
tools      ← actions, algorithms, config, risk, db, evidence
agent      ← config, report, tools, db, risk
cli / app.py / mcp_server ← agent, tools, seed, report, db, config
```

---

## 3. Database (FalkorDB graph)

* Graph name: `KEEPTHEM_GRAPH` (default `keepthem`; tests use `keepthem_test`, examples use `keepthem_examples`).
* Backend selection (`KEEPTHEM_DB`): `server` | `embedded` | `auto` (default — try server, fall back to embedded file
  at `KEEPTHEM_EMBEDDED_PATH`, default `.keepthem/falkordb.rdb`).
* Reads go through `GRAPH.RO_QUERY` (`KnowledgeGraph.read`), so read paths cannot mutate the graph — the DB enforces it.

### Nodes

| Label | Properties | Notes |
|---|---|---|
| `Employee` (+ `Manager` if anyone reports to them) | `id, name, role, tenure_months, location, salary_band (1-7), status ('active'\|'left')` | leavers stay in the graph |
| `Team` | `id, name, function` | |
| `Project` | `id, name, status ('active'\|'cancelled'…)` | |
| `Skill` | `id, name, market_demand ('high'…)` | |
| `Review` | `id, score (1-5), date, sentiment, summary` | |
| `Event` | `id, type, date, description` (+ `source:'keepthem-agent'` when written by the agent) | |
| `Decision` | `id, type, date, outcome, reason` (+ `proposed_by:'KeepThem'` when written by the agent) | |

### Relationships

```
(Employee)-[:REPORTS_TO]->(Manager)     (Employee)-[:MEMBER_OF]->(Team)     (Team)-[:OWNS]->(Project)
(Employee)-[:WORKED_ON]->(Project)      (Employee)-[:HAS_SKILL]->(Skill)
(Employee)-[:RECEIVED]->(Review)        (Employee)-[:EXPERIENCED]->(Event)
(Manager)-[:MADE]->(Decision)           (Decision)-[:AFFECTED]->(Employee)
(Employee)-[:PEER_OF]->(Employee)       stored one way, queried undirected
```

### Indexes (`seed.SCHEMA`)

`Employee.id`, `Employee.status`, `Team.id`, `Project.id`, `Skill.id`, `Event.type`, `Decision.type`, `Review.date`.

### Vocabularies

* Seeded event types: `promotion, project_change, project_cancelled, complaint, manager_change, reorg, overtime_spike,
  kudos, …` plus generated `resignation` and `peer_left`.
* Event types the agent may **write** (`tools.EVENT_TYPES`): `promotion, project_change, complaint, manager_change, reorg,
  kudos, stay_interview, training, resignation_notice, overtime_spike, project_cancelled`.
* Seeded decision types: `promotion_denied, promotion_granted, raise_denied, raise_approved, transfer_denied,
  project_removal, headcount_denied`.
* Decision types the agent may **write** (`tools.ACTION_TYPES`): `retention_stay_interview, retention_manager_rebalance,
  retention_career_plan, retention_promotion_review, retention_comp_review, retention_project_rotation,
  retention_complaint_followup, retention_knowledge_transfer` with `outcome` `proposed|completed`.

### Seeded volume (default data)

32 Employees (29 active, 3 left; 5 also `:Manager`), 5 Teams, 10 Projects, 17 Skills, 88 Reviews, 39 Events,
10 Decisions, 373 relationships. "Today" is pinned to `KEEPTHEM_AS_OF=2026-10-01` for reproducible output.

---

## 4. API / request flow

KeepThem's "API" is the **tool layer** in `keepthem/tools.py`. Every front end calls the same functions:

```
 Streamlit (app.py) ─┐
 CLI (cli.py) ───────┼─► agent.make_agent() ─► ClaudeAgent | OfflineAgent ─┐
                     │                                                     ├─► tools.call_tool(name, args, kg)
 MCP client ─────────┴─► mcp_server.py (@mcp.tool wrappers) ───────────────┘          │
 CLI `tool` / `cypher` ──────────────────────────────────────────────────────────────►│
                                                                                       ▼
                                    risk.py · actions.py · algorithms.py · evidence.py
                                                                                       ▼
                                          db.KnowledgeGraph.read() / write()  ── Cypher ──► FalkorDB
```

### Tool contract (do not break)

| Tool | Args | Kind |
|---|---|---|
| `investigate_risk` | `employee_id` \| `team_name` | read |
| `find_risk_paths` | `employee_id` | read |
| `run_graph_algorithm` | `algorithm` ∈ `manager_load, peer_influence, pagerank, betweenness, attrition_clusters, peer_contagion, shortest_path`, `params` | read |
| `recommend_actions` | `employee_id` | read |
| `explain_decision` | `query` (employee, team or decision id like `D-001`), `employee_id?` | read |
| `query_graph` | `cypher`, `params?` | read (`RO_QUERY`) |
| `log_event` | `employee_id, event_type, description, event_date?, confirmed=false` | write |
| `record_action` | `employee_id, action_type, description, status='proposed', confirmed=false` | write |

* Every result is a JSON-safe dict containing `paths` (readable strings), `evidence` `{nodes, edges}` and `queries`
  (`[{name, cypher, params}]`). Most also carry `tool` and `summary`.
* `call_tool` **never raises**: failures come back as `{"error": "..."}` so the agent can recover.
* Writes return `status:"preview"` and write nothing unless `confirmed=true` (human-in-the-loop).
* `record_action` with `status='completed'` promotes an existing proposal instead of duplicating it, then re-scores the
  employee and returns `risk_after`.

### Agent event stream

Both agents are generators yielding the same events, which the CLI and UI render:

```
{"type": "tool_call",   "name", "input"}
{"type": "tool_result", "name", "input", "result"}   # full result (UI uses evidence/queries)
{"type": "final",       "text"}
```

* **ClaudeAgent**: `messages.create(system=SYSTEM_PROMPT, tools=anthropic_tool_defs())` → run every `tool_use`
  block → send all `tool_result`s back in **one** user message (trimmed by `compact_for_llm`, which drops
  `evidence`/`assessments`/`impact_queries` and caps at 24 000 chars) → repeat, up to `max_steps=12`. Handles
  `refusal` and `max_tokens` stop reasons. Conversation persists across turns.
* **OfflineAgent**: regex/keyword router (employee name/id, team name, algorithm words, "why/explain", "log/record")
  → calls the same tools → renders Markdown via `report.render`.
* `make_agent()` picks Claude when `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` is set, else offline.

### Risk scoring flow (`risk.assess_employee`)

`Q_PROFILE` → each `sig_*` runs one Cypher query returning **paths** → points only if paths exist →
`score = clamp(Σ points, 0, 100)` → `high ≥ 65`, `medium ≥ 25`, else `low`. Signals and max points:
`low_review 20, overloaded_manager 16, peer_attrition 20, shared_project_exits 12, negative_decisions 16,
negative_events 12, pay_gap 10, stagnation 6, hot_skills 5, retention_actions −16`. `key_person_impact` (skills nobody
else on the team has) is reported separately. Thresholds and windows live in `config.py`.

---

## 5. Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `FALKORDB_HOST` / `FALKORDB_PORT` / `FALKORDB_PASSWORD` | `localhost` / `6379` / none | server connection |
| `KEEPTHEM_GRAPH` | `keepthem` | graph name |
| `KEEPTHEM_DB` | `auto` | `server` \| `embedded` \| `auto` |
| `KEEPTHEM_EMBEDDED_PATH` | `.keepthem/falkordb.rdb` | embedded DB file |
| `ANTHROPIC_API_KEY` | — | enables ClaudeAgent |
| `KEEPTHEM_MODEL` | `claude-opus-5-5` | Claude model id |
| `KEEPTHEM_AS_OF` | `2026-10-01` | the synthetic company's "today" |

---

## 6. Corrections made to existing docs (2026-10-18)

Compared README/docstrings with the code; these were out of date and have been fixed:

1. README "Layout" omitted `keepthem/config.py` and `keepthem/evidence.py` — added.
2. README decision types omitted `raise_approved` (present in `data.py`) — added.
3. `keepthem/cli.py` docstring called `demo` a "7-step" flow; `DEMO_STEPS` has 5 steps — fixed to "5-step".
4. `.env.example` did not list `KEEPTHEM_EMBEDDED_PATH` (read by `config.py`) — added as a commented entry.
