# KeepThem — Project Memory

Hand-off context so the next developer or AI tool can work without reading the whole codebase.
Read with [`ARCHITECTURE.md`](ARCHITECTURE.md) (how it works) and [`RULES.md`](RULES.md) (how to change it).
**Update the "Current state" and "Change log" sections after every meaningful change.**

## What this is (one paragraph)

A hackathon project (FalkorDB "Graph Hacks", Track 1) that investigates employee-attrition risk by reasoning over a
FalkorDB knowledge graph. An agent (Claude via a manual tool-use loop, or a deterministic offline agent) calls 8
Cypher-backed tools; every answer comes with graph paths, nodes/edges and the Cypher behind it. Retention actions can
be written back to the graph (with confirmation) and lower the risk score. Data is a synthetic 32-person company,
"Northwind Labs".

## Where things are (fast lookup)

| Need to… | Go to |
|---|---|
| Change a threshold / env setting | `keepthem/config.py` |
| Change demo data | `keepthem/data.py` (declarative tuples only) |
| Add/adjust a risk signal | `keepthem/risk.py` — `Q_*` constant + `sig_*` function, register in `assess_employee` |
| Add a recommendation rule | `keepthem/actions.py` → `recommend()` |
| Add a graph algorithm | `keepthem/algorithms.py` → `ALGORITHMS` + branch in `run()` (auto-exposed everywhere) |
| Add/modify a tool | `keepthem/tools.py` (`TOOLS`) **and** `keepthem/mcp_server.py` |
| Change how results look | `keepthem/report.py` |
| Change agent behaviour / prompt | `keepthem/agent.py` (`SYSTEM_PROMPT`, `OfflineAgent.run`) |
| UI | `app.py` |

## Key facts & invariants

* FalkorDB is the only store; reads go through `GRAPH.RO_QUERY`.
* "Today" = `2026-10-01` (pinned). Demo story: Platform manager Marcus Webb has 9 reports (backfill denied by Director
  Helen Brooks); Priya Nair and Tom Becker left; Dana Ruiz scores **93 (high)** and drops to **85** after a completed
  stay interview. Payments and Data are healthy.
* No points without paths (tested). Writes need `confirmed=true`. `call_tool` never raises.
* `PEER_OF` stored one way, queried undirected. Undirected `shortestPath` isn't supported by FalkorDB, so
  `shortest_path` uses a bounded variable-length search.

## How to run / verify

```bash
pip install -r requirements.txt -r requirements-embedded.txt
python -m keepthem.seed          # build the graph (embedded if no server)
python -m keepthem.cli demo      # 5-step offline walkthrough
streamlit run app.py             # UI
pytest -q                        # expect: 36 passed
```

## Current state (2026-10-18)

* Version `0.1.0`, single commit of the app (`9009816`). All 36 tests pass on Python 3.13 with embedded FalkorDB.
* Docs: `README.md` (overview), `ARCHITECTURE.md`, `RULES.md`, `MEMORY.md`. No PRD yet.
* Known limitations: heuristic (untuned) weights; synthetic data only; no auth or multi-user support; no REST API.

## Open ideas / next steps

* Write a PRD (`PRD.md`): goals, target users, features, requirements, success metrics.
* Loader for real HRIS data replacing `data.py`.
* Validate signal weights against historical attrition.

## Change log

| Date | Change | Files |
|---|---|---|
| 2026-10-18 | Added ARCHITECTURE.md, RULES.md, MEMORY.md. Fixed doc drift: README layout (config.py, evidence.py), README decision types (`raise_approved`), cli.py docstring ("7-step" → "5-step" demo), `.env.example` (`KEEPTHEM_EMBEDDED_PATH`). No behaviour changes. | docs, `keepthem/cli.py` (docstring), `.env.example` |
| (initial) | KeepThem: graph-driven attrition-risk agent on FalkorDB | all |
