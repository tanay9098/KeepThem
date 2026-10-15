"""Expose the KeepThem tools over MCP, so any MCP client (Claude Desktop, Claude Code, ...) can
investigate the FalkorDB graph.

    python -m keepthem.mcp_server                       # stdio transport

Claude Desktop / Claude Code config:
    {"mcpServers": {"keepthem": {"command": "python", "args": ["-m", "keepthem.mcp_server"],
                                 "cwd": "/path/to/KeepThem"}}}
"""
from __future__ import annotations

from . import seed, tools
from .db import get_kg

try:                                   # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:                    # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

mcp = _Server("keepthem", instructions=(
    "KeepThem investigates employee-attrition risk in a FalkorDB knowledge graph. Every result contains "
    "the graph `paths`, `evidence` nodes/edges and the Cypher `queries` behind it - cite them. "
    "Writes (log_event, record_action) preview unless confirmed=true; confirm with the user first."))


def _out(result: dict) -> dict:
    result.pop("assessments", None)      # per-employee detail of a team run is large; ask per employee
    return result


def _call(name: str, **kw) -> dict:
    return _out(tools.call_tool(name, {k: v for k, v in kw.items() if v is not None}, kg=get_kg()))


@mcp.tool()
def investigate_risk(employee_id: str | None = None, team_name: str | None = None) -> dict:
    """Multi-hop attrition-risk investigation of an employee (id or name) or a team (e.g. 'Platform')."""
    return _call("investigate_risk", employee_id=employee_id, team_name=team_name)


@mcp.tool()
def find_risk_paths(employee_id: str) -> dict:
    """Explicit path analysis: links to colleagues who resigned, management chain, upstream decisions, cascade."""
    return _call("find_risk_paths", employee_id=employee_id)


@mcp.tool()
def run_graph_algorithm(algorithm: str, params: dict | None = None) -> dict:
    """Run: manager_load, peer_influence, pagerank, betweenness, attrition_clusters, peer_contagion, shortest_path."""
    return _call("run_graph_algorithm", algorithm=algorithm, params=params)


@mcp.tool()
def recommend_actions(employee_id: str) -> dict:
    """Concrete, owned retention actions derived from the graph, each with its supporting paths."""
    return _call("recommend_actions", employee_id=employee_id)


@mcp.tool()
def explain_decision(query: str, employee_id: str | None = None) -> dict:
    """Why? Returns score arithmetic, supporting nodes/edges and Cypher. Query may name an employee, team or decision id."""
    return _call("explain_decision", query=query, employee_id=employee_id)


@mcp.tool()
def query_graph(cypher: str, params: dict | None = None) -> dict:
    """Run a read-only Cypher query against the company graph."""
    return _call("query_graph", cypher=cypher, params=params)


@mcp.tool()
def log_event(employee_id: str, event_type: str, description: str, event_date: str | None = None,
              confirmed: bool = False) -> dict:
    """WRITE an Event on an employee. Previews unless confirmed=true."""
    return _call("log_event", employee_id=employee_id, event_type=event_type, description=description,
                 event_date=event_date, confirmed=confirmed)


@mcp.tool()
def record_action(employee_id: str, action_type: str, description: str, status: str = "proposed",
                  confirmed: bool = False) -> dict:
    """WRITE a retention action (Decision node). status: proposed|completed. Previews unless confirmed=true."""
    return _call("record_action", employee_id=employee_id, action_type=action_type, description=description,
                 status=status, confirmed=confirmed)


def main() -> None:
    seed.ensure_seeded(get_kg())
    mcp.run("stdio")


if __name__ == "__main__":
    main()
