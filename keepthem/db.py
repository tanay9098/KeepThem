"""FalkorDB access layer.

Everything in KeepThem talks to the graph through `KnowledgeGraph`, which
  * runs *Cypher* (read-only queries go through GRAPH.RO_QUERY, so a read tool can
    never mutate the graph), and
  * converts FalkorDB Node/Edge/Path objects to plain JSON-safe dicts so results can
    be shipped to an LLM, a UI or an MCP client unchanged.

Backends
  server    - a FalkorDB server (docker compose up -d)           <- the real deployment
  embedded  - `falkordblite`, the same engine run in-process     <- no-Docker fallback
  auto      - try `server`, fall back to `embedded` (default)
"""
from __future__ import annotations

import os
from typing import Any

from . import config


class KnowledgeGraph:
    """Thin wrapper around one FalkorDB graph."""

    def __init__(self, graph, backend: str):
        self.graph = graph
        self.backend = backend

    # -- queries --------------------------------------------------------------
    def read(self, cypher: str, params: dict | None = None) -> list[dict]:
        """Read-only Cypher -> list of row dicts (column name -> python value)."""
        return _rows(self.graph.ro_query(cypher, params or {}))

    def write(self, cypher: str, params: dict | None = None) -> list[dict]:
        """Mutating Cypher -> list of row dicts."""
        return _rows(self.graph.query(cypher, params or {}))

    def delete(self) -> None:
        """Drop the whole graph (used by the seed script)."""
        try:
            self.graph.delete()
        except Exception:  # graph didn't exist yet
            pass

    def is_empty(self) -> bool:
        try:
            return self.read("MATCH (e:Employee) RETURN count(e) AS n")[0]["n"] == 0
        except Exception:
            return True


# -- value conversion -----------------------------------------------------------
def _rows(result) -> list[dict]:
    header = [h[1] for h in result.header]
    return [dict(zip(header, (to_py(v) for v in row))) for row in result.result_set]


def to_py(v: Any) -> Any:
    """FalkorDB Node/Edge/Path -> plain dicts tagged with `_t`."""
    # Imported lazily so `import keepthem.db` works before the client is installed.
    from falkordb import Edge, Node, Path

    if isinstance(v, Node):
        return {"_t": "node", "gid": v.id, "labels": list(v.labels), "props": dict(v.properties)}
    if isinstance(v, Edge):
        return {"_t": "edge", "gid": v.id, "type": v.relation, "src": v.src_node,
                "dst": v.dest_node, "props": dict(v.properties)}
    if isinstance(v, Path):
        return {"_t": "path", "nodes": [to_py(n) for n in v.nodes()],
                "edges": [to_py(e) for e in v.edges()]}
    if isinstance(v, list):
        return [to_py(x) for x in v]
    if isinstance(v, dict):
        return {k: to_py(x) for k, x in v.items()}
    return v


# -- connection ---------------------------------------------------------------
_KG: KnowledgeGraph | None = None


def connect(mode: str | None = None) -> KnowledgeGraph:
    mode = mode or config.DB_MODE
    errors = []
    if mode in ("server", "auto"):
        try:
            from falkordb import FalkorDB

            client = FalkorDB(host=config.FALKORDB_HOST, port=config.FALKORDB_PORT,
                              password=config.FALKORDB_PASSWORD)
            client.connection.ping()
            return KnowledgeGraph(client.select_graph(config.GRAPH_NAME),
                                  f"FalkorDB server {config.FALKORDB_HOST}:{config.FALKORDB_PORT}")
        except Exception as ex:  # noqa: BLE001
            errors.append(f"server: {ex}")
            if mode == "server":
                raise ConnectionError(
                    f"Cannot reach FalkorDB at {config.FALKORDB_HOST}:{config.FALKORDB_PORT} "
                    f"({ex}). Start it with `docker compose up -d`.") from ex
    try:
        from redislite.falkordb_client import FalkorDB as EmbeddedFalkorDB  # falkordblite

        os.makedirs(os.path.dirname(config.EMBEDDED_PATH) or ".", exist_ok=True)
        client = EmbeddedFalkorDB(config.EMBEDDED_PATH)
        return KnowledgeGraph(client.select_graph(config.GRAPH_NAME),
                              f"embedded FalkorDB (falkordblite) at {config.EMBEDDED_PATH}")
    except Exception as ex:  # noqa: BLE001
        errors.append(f"embedded: {ex}")
    raise ConnectionError(
        "No FalkorDB available. Run `docker compose up -d` or `pip install falkordblite`.\n  "
        + "\n  ".join(errors))


def get_kg() -> KnowledgeGraph:
    """Process-wide singleton connection."""
    global _KG
    if _KG is None:
        _KG = connect()
    return _KG


def set_kg(kg: KnowledgeGraph | None) -> None:
    """Inject a connection (tests)."""
    global _KG
    _KG = kg
