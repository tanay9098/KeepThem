# KeepThem — Engineering Rules

These rules apply to every change, by humans or AI tools. Read this file, [`MEMORY.md`](MEMORY.md) and
[`ARCHITECTURE.md`](ARCHITECTURE.md) before editing code.

## 1. No unnecessary dependencies

* Do **not** add a package to `requirements.txt` / `requirements-embedded.txt` unless the standard library or an
  existing dependency cannot reasonably do the job. Justify every new dependency in the commit message and in `MEMORY.md`.
* Current runtime dependencies are only: `falkordb`, `anthropic`, `streamlit`, `mcp` (+ optional `falkordblite`), and
  `pytest` for tests. Keep it that way by default.
* No agent frameworks (LangChain, LangGraph, …), ORMs, or extra databases. FalkorDB is the only data store and all
  data access is Cypher through `keepthem/db.py`.
* Optional dependencies must stay optional: import them lazily so the core works without them
  (pattern: `db.to_py`, `agent.ClaudeAgent.__init__`).

## 2. Don't break existing APIs

The following are public contracts. Changing them requires updating every caller, the docs and the tests in the same change:

* **Tool names, argument names and defaults** in `keepthem/tools.py` (`TOOLS`) and their mirrors in
  `keepthem/mcp_server.py`. Add new optional arguments instead of renaming or removing existing ones.
* **Result shape**: every tool result keeps `paths`, `evidence {nodes, edges}` and `queries [{name, cypher, params}]`;
  errors are returned as `{"error": ...}` — `call_tool` must never raise.
* **Agent event stream**: `tool_call` / `tool_result` / `final` events (consumed by `app.py` and `cli.py`).
* **Graph schema**: node labels, relationship types and property names listed in `ARCHITECTURE.md §3`. Add, don't rename.
* **Safety guarantees**: read tools use `KnowledgeGraph.read` (`GRAPH.RO_QUERY`); writes preview unless `confirmed=true`;
  a signal gets no points without supporting paths.
* **CLI subcommands and flags** (`seed, ask, chat, demo, tools, tool, cypher`, `--offline`, `--cypher`) and
  **environment variables** in `.env.example`.

## 3. Test after every change

* Run the full test suite after **every** change, before committing:

  ```bash
  pip install -r requirements.txt -r requirements-embedded.txt   # once
  pytest -q                                                      # must be all green (currently 36 passed)
  ```
  Tests use a throw-away graph (`keepthem_test`) and the embedded engine if no server is running.
* A skipped run ("no FalkorDB available") does **not** count as passing — fix the environment and re-run.
* New behaviour needs a new test in `tests/` against the real graph (no mocking of the DB layer).
* If a change affects demo output, regenerate `examples/example_queries.md` with `python examples/generate_examples.py`
  and smoke-test the entry points you touched (`python -m keepthem.cli demo`, `streamlit run app.py`).
* Never skip, delete or weaken a test to make a change pass.

## 4. Housekeeping

* Keep `ARCHITECTURE.md` accurate when you change structure, schema, tools or flows.
* Add a dated entry to the change log in `MEMORY.md` for every meaningful change.
* Keep data declarative in `data.py`; never put demo-specific logic in the loader or the risk engine.
* Keep `KEEPTHEM_AS_OF` pinned in demos and tests so output is reproducible.
* Never commit secrets (`.env` is git-ignored); data stays synthetic.
