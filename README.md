# 🕸️ KeepThem — an agent that investigates attrition risk by reasoning over a graph

> **FalkorDB "Graph Hacks: Context for AI Agents" · Track 1 — Agents That Act on Connected Data**

Most attrition models flag *who* might leave. KeepThem asks the question a manager actually needs
answered: **why, through whom, and what should we do about it?** It models a company as a
FalkorDB knowledge graph (employees, managers, teams, projects, reviews, events, decisions) and gives an
AI agent a toolbox of **Cypher queries and graph algorithms**. Every conclusion comes back with the exact
paths that justify it, and the agent can write the actions it recommends back into the graph — which then
changes the next answer.

```
 "Investigate attrition risk on the Platform team"
        │
        ▼
 ┌─────────────┐  tool calls   ┌────────────────────────────┐   Cypher / algo.*   ┌───────────┐
 │  Agent      │ ────────────▶ │ investigate_risk           │ ──────────────────▶ │           │
 │ (Claude or  │               │ find_risk_paths            │                     │ FalkorDB  │
 │  offline)   │ ◀──────────── │ run_graph_algorithm        │ ◀────────────────── │  graph    │
 └─────────────┘  paths +      │ recommend_actions          │   paths, rows       │           │
        │         Cypher +     │ explain_decision           │                     └───────────┘
        │         evidence     │ query_graph (read-only)    │                           ▲
        ▼                      │ log_event / record_action ─┼───────────────────────────┘
   answer with paths           └────────────────────────────┘     writes (human-confirmed)
```

## What it finds in the demo data

The synthetic company ("Northwind Labs", 32 people, 5 teams) has a built-in story the agent has to *discover*:

* Platform's manager **Marcus Webb has 9 direct reports** — because Director **Helen Brooks denied his backfill**
  (`(Dana)-[:REPORTS_TO]->(Marcus)<-[:AFFECTED]-(Decision headcount_denied)<-[:MADE]-(Helen)`).
* **Priya Nair** and **Tom Becker** resigned this summer. Both worked on **Orion Migration**, which was cancelled.
* **Dana Ruiz**, who holds skills nobody else on the team has, is the next domino: review score fell 4.4 → 2.6, promotion
  denied, complaint filed, same cancelled project, peer of a leaver, paid below same-role teammates.
* Payments and Data are healthy — the agent should (and does) leave them alone.

## Quick start

```bash
pip install -r requirements.txt

# 1. FalkorDB
docker compose up -d            # FalkorDB on :6379, FalkorDB Browser on http://localhost:3000
#    no Docker? `pip install -r requirements-embedded.txt` — the same engine, run in-process.
#    (KEEPTHEM_DB=auto, the default, tries the server first and falls back to embedded.)

# 2. Seed the synthetic company
python -m keepthem.seed

# 3. Demo UI
streamlit run app.py

# …or the CLI
python -m keepthem.cli demo                                  # scripted 5-step walkthrough
python -m keepthem.cli ask "Investigate attrition risk on the Platform team" --cypher
python -m keepthem.cli chat
```

**With Claude** (tool-calling agent, free-form questions): `export ANTHROPIC_API_KEY=...` (model via
`KEEPTHEM_MODEL`, default `claude-opus-5-5`). **Without a key** an offline agent routes your question to the same tools
deterministically, so the whole demo works with zero credentials. Both emit the same events, so the UI and CLI are identical.

### Demo script (≈3 minutes)

| # | You ask | What happens in the graph |
|---|---|---|
| 1 | *Investigate attrition risk on the Platform team* | Scores all 10 members (≈150 Cypher queries); finds 2 resignations in 12 months, 2 high / 6 medium risk, and flags `overloaded_manager` (9/10) and `peer_attrition` (10/10) as **systemic** drivers |
| 2 | *What is Dana Ruiz's attrition risk?* | 93/100 with every point attached to paths: review drop, adverse decisions, peers who left, the shared cancelled project |
| 3 | *Which managers are overloaded?* | `manager_load` (degree centrality): Marcus 9 reports, 2 already left; paths to each report |
| 4 | *Show attrition clusters* | Native FalkorDB label propagation over `PEER_OF`: a peer group containing both leavers **and** Dana |
| 5 | *Why is Dana Ruiz high risk?* | Score arithmetic + the Cypher behind each term + the evidence subgraph |
| 6 | Open **🔍 Reasoning trace** / **🕸️ Evidence graph** in the UI | Every Cypher query and parameter set; the exact nodes/edges |
| 7 | *Log a completed stay interview for Dana Ruiz* | Writes `(Marcus)-[:MADE]->(Decision)-[:AFFECTED]->(Dana)`; re-scores her: **93 → 85** |

More, with real captured output: [`examples/example_queries.md`](examples/example_queries.md).

## Graph model

```
(Employee {id,name,role,tenure_months,location,salary_band,status: active|left})   ← managers also carry :Manager
(Team {name,function})   (Project {name,status})   (Skill {name,market_demand})
(Review {score,date,sentiment,summary})   (Event {type,date,description})   (Decision {type,date,outcome,reason})

(Employee)-[:REPORTS_TO]->(Manager)      (Employee)-[:MEMBER_OF]->(Team)       (Team)-[:OWNS]->(Project)
(Employee)-[:WORKED_ON]->(Project)       (Employee)-[:HAS_SKILL]->(Skill)
(Employee)-[:RECEIVED]->(Review)         (Employee)-[:EXPERIENCED]->(Event)
(Manager)-[:MADE]->(Decision)            (Decision)-[:AFFECTED]->(Employee)    (Employee)-[:PEER_OF]->(Employee)
```

Event types: `promotion, project_change, project_cancelled, complaint, manager_change, reorg, overtime_spike,
kudos, peer_left, resignation, …` · Decision types: `promotion_denied, raise_denied, transfer_denied,
project_removal, headcount_denied, promotion_granted, retention_*` (agent-recorded actions).
Departed employees stay in the graph with `status:'left'` — **who left, and how they connect to who is still here, is the signal.**
Data lives in [`keepthem/data.py`](keepthem/data.py); everything is synthetic.

## How the graph drives the decisions (not just stores data)

**Risk score** = Σ signal points (clamped 0–100). Each signal is *one Cypher query that returns paths*; a signal with
no path gets no points (enforced by a test):

| Signal | Multi-hop pattern | Pts |
|---|---|---|
| `low_review` | `(e)-[:RECEIVED]->(Review)` latest score + drop vs previous | ≤20 |
| `overloaded_manager` | `(e)-[:REPORTS_TO]->(m)<-[:REPORTS_TO]-(peers)` **and** `(e)-[:REPORTS_TO]->(m)<-[:AFFECTED]-(headcount_denied)<-[:MADE]-(Director)` | ≤16 |
| `peer_attrition` | `(e)-[:PEER_OF]-(leaver)-[:EXPERIENCED]->(resignation)` and same-team leavers | ≤20 |
| `shared_project_exits` | `(e)-[:WORKED_ON]->(Project)<-[:WORKED_ON]-(leaver)-[:EXPERIENCED]->(resignation)` | ≤12 |
| `negative_decisions` | `(Manager)-[:MADE]->(Decision)-[:AFFECTED]->(e)` denied promotion/raise/transfer | ≤16 |
| `negative_events` | `(e)-[:EXPERIENCED]->(complaint \| reorg \| …)` | ≤12 |
| `pay_gap`, `stagnation`, `hot_skills` | same-role teammates' bands; no promotion event; high-demand skills | ≤10/6/5 |
| `retention_actions` | completed `retention_*` Decisions → **negative** points (closes the loop) | ≥-16 |

Separately, **key-person impact** — skills nobody else on the team holds — is a graph query too, and the recommendations use it.
**Recommendations** ([`actions.py`](keepthem/actions.py)) are derived from the signals *plus further graph queries*: e.g. the manager to
move reports to is the sibling under the same director with the most skill overlap and lowest load; the rotation target is an active
project on another team staffed by people sharing ≥2 of the person's skills. Each has an owner (read from the graph), urgency, the
signals that triggered it, and its supporting paths.

### Agent tools ([`keepthem/tools.py`](keepthem/tools.py))

| Tool | Purpose |
|---|---|
| `investigate_risk(employee_id \| team_name)` | Multi-hop risk assessment; team mode ranks members and finds systemic drivers |
| `find_risk_paths(employee_id)` | Explicit path analysis: links to leavers (peer / project / team / manager), management chain + load, upstream decisions, cascade exposure |
| `run_graph_algorithm(algorithm, params)` | `manager_load` (degree centrality), `peer_influence`, `pagerank`, `betweenness` (native), `attrition_clusters` (native label propagation), `peer_contagion` (variable-length paths), `shortest_path` |
| `recommend_actions(employee_id)` | Concrete, owned, time-boxed actions with evidence |
| `explain_decision(query)` | "Why?" → score arithmetic + nodes/edges + Cypher. Query can name an employee, team, or decision id (`D-001`) |
| `query_graph(cypher)` | Read-only Cypher escape hatch — runs through `GRAPH.RO_QUERY`, so the **database itself** rejects writes |
| `log_event`, `record_action` | Writes. Preview unless `confirmed=true` (the system prompt tells the agent to ask the user first) |

Every result carries `paths` (readable), `evidence` (`nodes`/`edges`, rendered as a graph in the UI) and `queries` (the Cypher + params).

### Explainability, concretely

```
Dana Ruiz — risk 93/100 (high)
score = low_review(+16) + overloaded_manager(+16) + peer_attrition(+13) + shared_project_exits(+12)
      + negative_decisions(+16) + negative_events(+10) + pay_gap(+5) + hot_skills(+5) = 93
- Manager is overloaded (+16): Marcus Webb has 9 active direct reports (threshold 8). Root cause: a headcount_denied decision…
    (Dana Ruiz)-[:REPORTS_TO]->(Marcus Webb)<-[:AFFECTED]-(Decision headcount_denied 2026-05-20 [denied])<-[:MADE]-(Helen Brooks)
```

## Layout

```
docker-compose.yml        FalkorDB + Browser
app.py                    Streamlit demo (chat · reasoning trace · evidence graph · log-action buttons)
keepthem/
  data.py  seed.py        synthetic company → FalkorDB (Cypher MERGE/CREATE, indexes)
  db.py                   FalkorDB wrapper: RO_QUERY for reads, Node/Edge/Path → JSON
  risk.py                 signal queries + scoring + team roll-up
  actions.py              signals → concrete recommendations
  algorithms.py           graph algorithms (native algo.* and Cypher)
  tools.py                the 8 tools + JSON schemas + dispatcher
  agent.py                ClaudeAgent (manual tool-use loop) · OfflineAgent (deterministic)
  mcp_server.py           the same tools over MCP
  cli.py  report.py       CLI + markdown rendering
examples/                 generated example queries / expected outputs
tests/                    36 tests against a real FalkorDB
```

## MCP (optional)

```json
{"mcpServers": {"keepthem": {"command": "python", "args": ["-m", "keepthem.mcp_server"], "cwd": "/path/to/KeepThem"}}}
```
Works with `mcp` 1.x (`FastMCP`) and 2.x (`MCPServer`). Claude Desktop / Claude Code can then investigate the graph directly.

## Tests

```bash
pytest -q        # 36 tests; use a throw-away graph, never touch your data
```
They run the real Cypher (server if reachable, else embedded FalkorDB) and assert the contract that matters:
*no points without paths*, the multi-hop root cause is found, read tools can't write, writes need confirmation, a completed action lowers risk,
and the Claude loop executes parallel tool calls and returns them in one message (via a scripted fake client — no API key needed).

## Extending

* **New signal**: add a `Q_…` Cypher constant + a `sig_…` function in `risk.py` returning a `Signal`, add it to `assess_employee`.
* **New recommendation**: add a rule in `actions.recommend` (≈10 lines).
* **New algorithm**: add an entry to `algorithms.ALGORITHMS` and a branch in `run` — it's automatically exposed to Claude, MCP, CLI and UI.
* **Real data**: replace `data.py` with a loader from your HRIS; the model and everything above it are unchanged.
* **LangGraph**: `tools.TOOLS` is plain JSON-schema + callables, so it drops into any framework; the custom loop is ~30 lines on purpose.

## Design notes & limitations

* **Reproducible demo**: "today" is pinned (`KEEPTHEM_AS_OF=2026-10-01`) so results don't drift.
* **Heuristic weights, not a trained model.** The point is an *auditable* score whose every term is a graph path; weights live next to each
  signal and are meant to be tuned. A real deployment would validate them against historical attrition.
* **`PEER_OF` is stored one-way** (as in the spec) and queried undirected; native PageRank/betweenness see it directionally, which the tool says in its output.
* FalkorDB can't do undirected `shortestPath`, so `shortest_path` uses a bounded variable-length path search over a whitelist of relationship types.
* **Ethics**: attrition analytics can be misused. KeepThem is framed as *retention support* (the agent is instructed never to suggest
  discipline/monitoring), the data is synthetic, and writes require confirmation.

## Hackathon checklist (Track 1)

- [x] Graph **drives** decisions: scores, recommendations and their targets (which manager, which project) are graph queries
- [x] **Multi-hop** reasoning: 3-hop root cause (Dana → manager ← denied backfill ← director), shared-project-with-leaver, peer-of-peer contagion
- [x] Graph queries **and algorithms** as agent tools (degree centrality, PageRank, betweenness, label propagation, variable-length paths)
- [x] **Actions** from the graph: ranked, owned recommendations; agent writes events/decisions back and the score responds
- [x] **Explainable**: every conclusion returns paths, nodes/edges and the Cypher
- [x] FalkorDB is the only data store; all interaction is Cypher
