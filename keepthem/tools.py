"""The agent's toolbox. Every tool:

  * talks to FalkorDB with Cypher (read tools use GRAPH.RO_QUERY - they cannot write),
  * returns JSON-safe dicts,
  * returns its evidence: `paths` (readable), `evidence` {nodes, edges} and `queries` (the Cypher
    that produced the answer), so any conclusion can be traced back to the graph.

The same functions back (a) the Claude tool-calling agent, (b) the offline agent, (c) the MCP
server and (d) the Streamlit UI - there is exactly one implementation of each capability.

Tools
  investigate_risk     multi-hop risk assessment of an employee or a whole team
  find_risk_paths      explicit path analysis around one employee (links to leavers, mgmt chain, cascade)
  run_graph_algorithm  centrality / PageRank / betweenness / communities / contagion / shortest path
  recommend_actions    concrete, owned actions derived from the signals
  explain_decision     "why?" -> score arithmetic + supporting nodes/edges/Cypher
  query_graph          read-only Cypher escape hatch
  log_event            write: record an Event on an employee   (needs confirmed=true)
  record_action        write: record a retention Decision      (needs confirmed=true)
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from . import actions, algorithms, config, risk
from .db import KnowledgeGraph, get_kg
from .evidence import Evidence

EVENT_TYPES = ["promotion", "project_change", "complaint", "manager_change", "reorg", "kudos",
               "stay_interview", "training", "resignation_notice", "overtime_spike", "project_cancelled"]
ACTION_TYPES = ["retention_stay_interview", "retention_manager_rebalance", "retention_career_plan",
                "retention_promotion_review", "retention_comp_review", "retention_project_rotation",
                "retention_complaint_followup", "retention_knowledge_transfer"]


def _emp(kg, ref: str) -> dict:
    r = risk.resolve_employee(kg, ref)
    if not r:
        return {"error": f"No employee matches '{ref}'. Use an id like E006 or a name."}
    if "ambiguous" in r:
        return {"error": f"'{ref}' is ambiguous: {', '.join(r['ambiguous'])}"}
    return r


# ------------------------------------------------------------------------------------------
# 1. investigate_risk
# ------------------------------------------------------------------------------------------
def investigate_risk(employee_id: str | None = None, team_name: str | None = None,
                     kg: KnowledgeGraph | None = None) -> dict:
    """Multi-hop attrition-risk investigation of one employee (id or name) or a team."""
    kg = kg or get_kg()
    if team_name:
        out = risk.assess_team(kg, team_name)
        if "error" not in out:
            out["summary"] = (f"{out['team']}: {out['headcount']} active, {out['resignations_last_12m']} resignation(s) "
                              f"in 12 months, average risk {out['average_risk']}, "
                              f"{out['risk_distribution']['high']} high / {out['risk_distribution']['medium']} medium.")
        return {"tool": "investigate_risk", **out}
    if employee_id:
        e = _emp(kg, employee_id)
        if "error" in e:
            return e
        a = risk.assess_employee(kg, e["id"])
        a["summary"] = (f"{a['employee']['name']}: risk {a['risk_score']}/100 ({a['risk_level']}). "
                        + "; ".join(f"{s['title']} (+{s['points']})" for s in sorted(a["signals"], key=lambda s: -s["points"])
                                    if s["points"] > 0))
        return {"tool": "investigate_risk", **a}
    return {"error": "Provide employee_id (id or name) or team_name."}


# ------------------------------------------------------------------------------------------
# 2. find_risk_paths
# ------------------------------------------------------------------------------------------
Q_LINKS_TO_LEAVERS = """
MATCH p=(e:Employee {id:$id})-[:PEER_OF|WORKED_ON|MEMBER_OF|REPORTS_TO*1..3]-(x:Employee {status:'left'})
RETURN x.name AS leaver, p, size(relationships(p)) AS hops
ORDER BY hops ASC"""

Q_MGMT_CHAIN = """
MATCH p=(e:Employee {id:$id})-[:REPORTS_TO*1..3]->(m:Manager)
OPTIONAL MATCH (m)<-[:REPORTS_TO]-(r:Employee {status:'active'})
RETURN p, m.name AS manager, count(r) AS direct_reports ORDER BY size(relationships(p))"""

Q_DECISION_CHAIN = """
MATCH p=(e:Employee {id:$id})-[:REPORTS_TO*1..3]->(m:Manager)<-[:AFFECTED]-(d:Decision)<-[:MADE]-(h:Manager)
RETURN p"""

Q_CASCADE = """
MATCH p=(e:Employee {id:$id})-[:PEER_OF|WORKED_ON*1..2]-(o:Employee {status:'active'})
WHERE o.id <> e.id
RETURN DISTINCT o.id AS id, o.name AS name, min(size(relationships(p))) AS hops"""


def find_risk_paths(employee_id: str, kg: KnowledgeGraph | None = None) -> dict:
    """Explicit path analysis: HOW is this person connected to attrition drivers?"""
    kg = kg or get_kg()
    e = _emp(kg, employee_id)
    if "error" in e:
        return e
    eid = e["id"]
    ev, queries = Evidence(), []

    # (a) the shortest link of each kind to every person who already left
    rows = kg.read(Q_LINKS_TO_LEAVERS, {"id": eid})
    queries.append({"name": "connections to employees who resigned", "cypher": Q_LINKS_TO_LEAVERS.strip(),
                    "params": {"id": eid}})
    best: dict[tuple, dict] = {}
    for r in rows:
        kind = tuple(sorted({ed["type"] for ed in r["p"]["edges"]}))
        key = (r["leaver"], kind)
        if key not in best:
            best[key] = r
    links = []
    for (leaver, kind), r in sorted(best.items(), key=lambda kv: kv[1]["hops"]):
        if r["hops"] > 2:
            continue
        links.append({"leaver": leaver, "relationship": "+".join(kind), "hops": r["hops"], "path": ev.add_path(r["p"])})

    # (b) management chain and its load, (c) the decision chain behind that load
    chain = kg.read(Q_MGMT_CHAIN, {"id": eid})
    queries.append({"name": "management chain + load", "cypher": Q_MGMT_CHAIN.strip(), "params": {"id": eid}})
    mgmt = [{"manager": r["manager"], "direct_reports": r["direct_reports"], "path": ev.add_path(r["p"])} for r in chain]
    dec_rows = kg.read(Q_DECISION_CHAIN, {"id": eid})
    queries.append({"name": "decisions made upstream that affect the management chain",
                    "cypher": Q_DECISION_CHAIN.strip(), "params": {"id": eid}})
    upstream = []
    for r in dec_rows:
        d = next(n for n in r["p"]["nodes"] if "Decision" in n["labels"])["props"]
        upstream.append({"decision": d["id"], "type": d["type"], "reason": d["reason"], "path": ev.add_path(r["p"])})

    # (d) cascade: who else is exposed if this person goes?
    near = kg.read(Q_CASCADE, {"id": eid})
    queries.append({"name": "colleagues within 2 hops (peers / shared projects)", "cypher": Q_CASCADE.strip(),
                    "params": {"id": eid}})
    cascade = []
    for r in sorted(near, key=lambda r: r["hops"])[:8]:
        a = risk.assess_employee(kg, r["id"])
        cascade.append({"employee": r["name"], "hops": r["hops"], "risk_score": a["risk_score"], "risk_level": a["risk_level"]})
    cascade.sort(key=lambda c: -c["risk_score"])
    return {"tool": "find_risk_paths", "employee": e,
            "summary": f"{len(links)} link(s) to resigned colleagues; {len(mgmt)} manager(s) up the chain; "
                       f"{len(upstream)} upstream decision(s); {len(cascade)} colleagues exposed to a cascade.",
            "links_to_leavers": links, "management_chain": mgmt, "upstream_decisions": upstream,
            "cascade_exposure": cascade, "queries": queries, "evidence": ev.to_dict(), "paths": ev.paths}


# ------------------------------------------------------------------------------------------
# 3. run_graph_algorithm
# ------------------------------------------------------------------------------------------
def run_graph_algorithm(algorithm: str, params: dict | None = None, kg: KnowledgeGraph | None = None) -> dict:
    """Run a graph algorithm (see algorithms.ALGORITHMS)."""
    return {"tool": "run_graph_algorithm", **algorithms.run(kg or get_kg(), algorithm, params)}


# ------------------------------------------------------------------------------------------
# 4. recommend_actions
# ------------------------------------------------------------------------------------------
def recommend_actions(employee_id: str, kg: KnowledgeGraph | None = None) -> dict:
    """Concrete, owned retention actions derived from the graph signals."""
    kg = kg or get_kg()
    e = _emp(kg, employee_id)
    if "error" in e:
        return e
    out = actions.recommend(kg, e["id"])
    top = out["recommendations"][0]
    out["summary"] = f"{len(out['recommendations'])} action(s) for {e['name']}; first: {top['action']} ({top['owner']}, {top['urgency']})"
    return {"tool": "recommend_actions", **out}


# ------------------------------------------------------------------------------------------
# 5. explain_decision
# ------------------------------------------------------------------------------------------
Q_DECISION = """
MATCH p=(m:Manager)-[:MADE]->(d:Decision {id:$id})-[:AFFECTED]->(e:Employee)
RETURN p"""
Q_AFTERMATH = """
MATCH p=(d:Decision {id:$id})-[:AFFECTED]->(e:Employee)-[:EXPERIENCED]->(ev:Event)
WHERE ev.date >= d.date AND ev.type IN ['complaint','resignation','peer_left','overtime_spike']
RETURN p ORDER BY ev.date"""


def explain_decision(query: str, employee_id: str | None = None, kg: KnowledgeGraph | None = None) -> dict:
    """Explain a conclusion. `query` may name an employee, a team or a decision id (e.g. D-001).

    Always returns the supporting nodes/edges and the Cypher behind each claim."""
    kg = kg or get_kg()
    q = query or ""
    m = re.search(r"\bD-\d{3}\b", q, re.I)
    if m and not employee_id:
        did = m.group(0).upper()
        ev = Evidence()
        main = kg.read(Q_DECISION, {"id": did})
        if not main:
            return {"error": f"No decision {did}"}
        for r in main:
            ev.add_path(r["p"])
        after = kg.read(Q_AFTERMATH, {"id": did})
        for r in after:
            ev.add_path(r["p"])
        d = next(n for n in main[0]["p"]["nodes"] if "Decision" in n["labels"])["props"]
        return {"tool": "explain_decision", "subject": f"Decision {did}", "question": q,
                "explanation": f"{d['type']} ({d['outcome']}) on {d['date']}: {d['reason']}",
                "aftermath_events": [next(n for n in r["p"]["nodes"] if "Event" in n["labels"])["props"]["type"] for r in after],
                "queries": [{"name": "the decision", "cypher": Q_DECISION.strip(), "params": {"id": did}},
                            {"name": "what the affected employee experienced afterwards", "cypher": Q_AFTERMATH.strip(),
                             "params": {"id": did}}],
                "evidence": ev.to_dict(), "paths": ev.paths}

    target = employee_id
    if not target:
        for r in kg.read("MATCH (e:Employee) RETURN e.id AS id, e.name AS name"):
            if re.search(rf"\b{re.escape(r['id'])}\b", q, re.I) or r["name"].lower() in q.lower():
                target = r["id"]
                break
    if not target:
        for t in kg.read("MATCH (t:Team) RETURN t.name AS name"):
            if t["name"].lower() in q.lower():
                team = risk.assess_team(kg, t["name"])
                return {"tool": "explain_decision", "subject": f"Team {team['team']}", "question": q,
                        "explanation": f"Team average risk {team['average_risk']}. Systemic drivers (signals affecting most members): "
                                       + "; ".join(f"{d['signal']} affects {d['affects']}/{d['of']}" for d in team["systemic_drivers"][:4]),
                        "systemic_drivers": team["systemic_drivers"],
                        "ranking": [{k: r[k] for k in ("name", "risk_score", "risk_level", "top_signals")} for r in team["ranking"][:5]],
                        "queries": team["queries"], "evidence": team["evidence"], "paths": team["paths"]}
        return {"error": "Could not tell what to explain. Mention an employee, a team, or a decision id like D-001."}

    e = _emp(kg, target)
    if "error" in e:
        return e
    a = risk.assess_employee(kg, e["id"])
    rec = actions.recommend(kg, e["id"], a)
    why = [{"signal": s["key"], "title": s["title"], "points": s["points"], "because": s["summary"],
            "supporting_paths": s["paths"], "cypher": [x["cypher"] for x in s["queries"]]}
           for s in sorted(a["signals"], key=lambda s: -s["points"]) if s["points"]]
    ev = Evidence()
    for n in a["evidence"]["nodes"]:
        ev.nodes[n["id"]] = n
    for ed in a["evidence"]["edges"]:
        ev.edges[(ed["from"], ed["type"], ed["to"])] = ed
    ev.paths = list(a["paths"])
    return {"tool": "explain_decision", "subject": a["employee"]["name"], "question": q,
            "explanation": f"{a['employee']['name']} is rated {a['risk_level']} ({a['risk_score']}/100) because: " + a["score_formula"],
            "score_formula": a["score_formula"], "why": why,
            "key_person": a["impact"]["sole_skills"],
            "why_these_actions": [{"action": r["action"], "owner": r["owner"], "because": r["because"],
                                   "triggered_by": r["triggered_by"], "supporting_paths": r["paths"][:4]}
                                  for r in rec["recommendations"]],
            "queries": [{"name": f"{s['key']}: {x['name']}", "cypher": x["cypher"], "params": x["params"]}
                        for s in a["signals"] if s["points"] for x in s["queries"]],
            "evidence": ev.to_dict(), "paths": ev.paths}


# ------------------------------------------------------------------------------------------
# read-only Cypher escape hatch
# ------------------------------------------------------------------------------------------
def query_graph(cypher: str, params: dict | None = None, kg: KnowledgeGraph | None = None) -> dict:
    """Run read-only Cypher (GRAPH.RO_QUERY: the database itself rejects writes)."""
    kg = kg or get_kg()
    try:
        rows = kg.read(cypher, params or {})
    except Exception as ex:  # noqa: BLE001
        return {"error": f"Query failed: {ex}"}
    ev = Evidence()

    def walk(v):
        if isinstance(v, dict) and v.get("_t") == "path":
            ev.add_path(v)
        elif isinstance(v, dict) and v.get("_t") == "node":
            ev.add_node(v)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)

    for r in rows:
        walk(r)
    slim = [_slim(r) for r in rows[:50]]
    return {"tool": "query_graph", "row_count": len(rows), "rows": slim, "truncated": len(rows) > 50,
            "queries": [{"name": "ad-hoc read-only query", "cypher": cypher, "params": params or {}}],
            "evidence": ev.to_dict(), "paths": ev.paths}


def _slim(v: Any) -> Any:
    from .evidence import node_name, path_to_str
    if isinstance(v, dict) and v.get("_t") == "node":
        return {"node": node_name(v), **v["props"]}
    if isinstance(v, dict) and v.get("_t") == "path":
        return path_to_str(v)
    if isinstance(v, dict) and v.get("_t") == "edge":
        return {"edge": v["type"], **v["props"]}
    if isinstance(v, dict):
        return {k: _slim(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_slim(x) for x in v]
    return v


# ------------------------------------------------------------------------------------------
# 6. writes: log_event / record_action  (human-in-the-loop: confirmed=True required)
# ------------------------------------------------------------------------------------------
def log_event(employee_id: str, event_type: str, description: str, event_date: str | None = None,
              confirmed: bool = False, kg: KnowledgeGraph | None = None) -> dict:
    """Record an Event for an employee. Without confirmed=true this only previews."""
    kg = kg or get_kg()
    e = _emp(kg, employee_id)
    if "error" in e:
        return e
    if event_type not in EVENT_TYPES:
        return {"error": f"event_type must be one of {EVENT_TYPES}"}
    date_ = event_date or config.AS_OF
    eid = f"EV-{uuid.uuid4().hex[:8]}"
    cypher = ("MATCH (e:Employee {id:$emp}) CREATE (e)-[:EXPERIENCED]->(ev:Event "
              "{id:$id, type:$type, date:$date, description:$desc, source:'keepthem-agent'})")
    params = {"emp": e["id"], "id": eid, "type": event_type, "date": date_, "desc": description}
    if not confirmed:
        return {"tool": "log_event", "status": "preview", "message": "Nothing written. Call again with confirmed=true after the user agrees.",
                "would_write": params, "queries": [{"name": "write (not executed)", "cypher": cypher, "params": params}]}
    kg.write(cypher, params)
    rows = kg.read("MATCH p=(e:Employee {id:$emp})-[:EXPERIENCED]->(ev:Event {id:$id}) RETURN p", {"emp": e["id"], "id": eid})
    ev = Evidence()
    for r in rows:
        ev.add_path(r["p"])
    return {"tool": "log_event", "status": "written", "event_id": eid, "employee": e["name"],
            "queries": [{"name": "write", "cypher": cypher, "params": params}], "evidence": ev.to_dict(), "paths": ev.paths}


def record_action(employee_id: str, action_type: str, description: str, status: str = "proposed",
                  confirmed: bool = False, kg: KnowledgeGraph | None = None) -> dict:
    """Record a retention action as a Decision: (Manager)-[:MADE]->(Decision)-[:AFFECTED]->(Employee).

    status 'proposed' = planned; 'completed' = done (completed actions lower the risk score)."""
    kg = kg or get_kg()
    e = _emp(kg, employee_id)
    if "error" in e:
        return e
    if action_type not in ACTION_TYPES:
        return {"error": f"action_type must be one of {ACTION_TYPES}"}
    if status not in ("proposed", "completed"):
        return {"error": "status must be 'proposed' or 'completed'"}
    owner = kg.read("MATCH (e:Employee {id:$id})-[:REPORTS_TO]->(m:Manager) RETURN m.id AS id, m.name AS name", {"id": e["id"]})
    if not owner:
        return {"error": f"{e['name']} has no manager in the graph to own the action."}
    existing = kg.read("MATCH (d:Decision {type:$t, outcome:'proposed'})-[:AFFECTED]->(e:Employee {id:$id}) RETURN d.id AS id",
                       {"t": action_type, "id": e["id"]})
    if existing and status == "completed":   # promote the proposal instead of duplicating it
        cypher = "MATCH (d:Decision {id:$did}) SET d.outcome='completed', d.date=$date, d.reason=$desc"
        params = {"did": existing[0]["id"], "date": config.AS_OF, "desc": description}
        did = existing[0]["id"]
    else:
        did = f"D-{uuid.uuid4().hex[:6]}"
        cypher = ("MATCH (m:Manager {id:$mgr}),(e:Employee {id:$emp}) CREATE (m)-[:MADE]->(d:Decision "
                  "{id:$did, type:$type, date:$date, outcome:$status, reason:$desc, proposed_by:'KeepThem'})-[:AFFECTED]->(e)")
        params = {"mgr": owner[0]["id"], "emp": e["id"], "did": did, "type": action_type, "date": config.AS_OF,
                  "status": status, "desc": description}
    if not confirmed:
        return {"tool": "record_action", "status": "preview",
                "message": "Nothing written. Call again with confirmed=true after the user agrees.",
                "would_write": params, "queries": [{"name": "write (not executed)", "cypher": cypher, "params": params}]}
    kg.write(cypher, params)
    ev = Evidence()
    for r in kg.read("MATCH p=(m:Manager)-[:MADE]->(d:Decision {id:$did})-[:AFFECTED]->(e:Employee) RETURN p", {"did": did}):
        ev.add_path(r["p"])
    new = risk.assess_employee(kg, e["id"])
    return {"tool": "record_action", "status": "written", "decision_id": did, "outcome": status,
            "owner": owner[0]["name"], "employee": e["name"],
            "risk_after": {"score": new["risk_score"], "level": new["risk_level"]},
            "queries": [{"name": "write", "cypher": cypher, "params": params}], "evidence": ev.to_dict(), "paths": ev.paths}


# ------------------------------------------------------------------------------------------
# Registry (JSON schemas for Claude / MCP) and dispatcher
# ------------------------------------------------------------------------------------------
def _schema(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


TOOLS: list[dict] = [
    {"name": "investigate_risk", "fn": investigate_risk,
     "description": "Investigate attrition risk with multi-hop graph queries. Give employee_id (id like E006 or a name) "
                    "for one person, or team_name (e.g. 'Platform') to rank a whole team and find systemic drivers. "
                    "Returns score, per-signal points, the graph paths that justify each signal, and the Cypher used.",
     "input_schema": _schema({"employee_id": {"type": "string", "description": "Employee id (E006) or name"},
                              "team_name": {"type": "string", "description": "Team name, e.g. Platform"}})},
    {"name": "find_risk_paths", "fn": find_risk_paths,
     "description": "Explicit path analysis for one employee: shortest links to colleagues who resigned (peer / "
                    "shared project / team / manager), the management chain with load, upstream decisions that "
                    "explain it, and which colleagues are exposed to a cascade if this person leaves.",
     "input_schema": _schema({"employee_id": {"type": "string"}}, ["employee_id"])},
    {"name": "run_graph_algorithm", "fn": run_graph_algorithm,
     "description": "Run a graph algorithm. algorithm is one of: " + ", ".join(algorithms.ALGORITHMS) + ". "
                    + " | ".join(f"{k}: {v}" for k, v in algorithms.ALGORITHMS.items()),
     "input_schema": _schema({"algorithm": {"type": "string", "enum": list(algorithms.ALGORITHMS)},
                              "params": {"type": "object", "description": "Optional: top_n, threshold (manager_load), "
                                         "relationship PEER_OF|REPORTS_TO (pagerank), from_employee/to_employee (shortest_path)"}},
                             ["algorithm"])},
    {"name": "recommend_actions", "fn": recommend_actions,
     "description": "Concrete, owned, time-boxed retention actions for one employee, each tied to the signals and "
                    "graph paths that triggered it (e.g. which sibling manager can absorb reports, which project fits "
                    "their skills).",
     "input_schema": _schema({"employee_id": {"type": "string"}}, ["employee_id"])},
    {"name": "explain_decision", "fn": explain_decision,
     "description": "Explain WHY: returns the score arithmetic, the supporting nodes/edges and the Cypher behind a "
                    "conclusion. query can mention an employee, a team, or a decision id like D-001.",
     "input_schema": _schema({"query": {"type": "string", "description": "e.g. 'Why is Dana Ruiz high risk?'"},
                              "employee_id": {"type": "string"}}, ["query"])},
    {"name": "query_graph", "fn": query_graph,
     "description": "Run a READ-ONLY Cypher query for anything the other tools don't cover. Schema: nodes Employee"
                    "(id,name,role,tenure_months,location,salary_band,status active|left; also :Manager), Team, Project(status), "
                    "Review(score,date,sentiment), Event(type,date,description), Decision(type,date,outcome,reason), "
                    "Skill(market_demand). Edges: REPORTS_TO, MEMBER_OF, WORKED_ON, HAS_SKILL, RECEIVED, EXPERIENCED, "
                    "MADE, AFFECTED, PEER_OF, OWNS. Return paths (p=...) so evidence can be shown.",
     "input_schema": _schema({"cypher": {"type": "string"}, "params": {"type": "object"}}, ["cypher"])},
    {"name": "log_event", "fn": log_event,
     "description": "WRITE: record an Event (e.g. stay_interview, complaint, kudos) on an employee. Without "
                    "confirmed=true it only previews. Set confirmed=true ONLY after the user has explicitly agreed.",
     "input_schema": _schema({"employee_id": {"type": "string"}, "event_type": {"type": "string", "enum": EVENT_TYPES},
                              "description": {"type": "string"}, "event_date": {"type": "string", "description": "ISO date, default today"},
                              "confirmed": {"type": "boolean"}}, ["employee_id", "event_type", "description"])},
    {"name": "record_action", "fn": record_action,
     "description": "WRITE: record a retention action as a Decision node linked to the employee and their manager. "
                    "status 'proposed' (planned) or 'completed' (done - lowers the employee's risk score). Without "
                    "confirmed=true it only previews. Set confirmed=true ONLY after the user has explicitly agreed.",
     "input_schema": _schema({"employee_id": {"type": "string"}, "action_type": {"type": "string", "enum": ACTION_TYPES},
                              "description": {"type": "string"}, "status": {"type": "string", "enum": ["proposed", "completed"]},
                              "confirmed": {"type": "boolean"}}, ["employee_id", "action_type", "description"])},
]
TOOL_FUNCS = {t["name"]: t["fn"] for t in TOOLS}


def call_tool(name: str, args: dict | None = None, kg: KnowledgeGraph | None = None) -> dict:
    """Dispatch by name. Never raises: errors come back as {'error': ...} so the agent can recover."""
    fn = TOOL_FUNCS.get(name)
    if not fn:
        return {"error": f"Unknown tool '{name}'. Available: {', '.join(TOOL_FUNCS)}"}
    try:
        return fn(**(args or {}), **({"kg": kg} if kg else {}))
    except TypeError as ex:
        return {"error": f"Bad arguments for {name}: {ex}"}
    except Exception as ex:  # noqa: BLE001
        return {"error": f"{name} failed: {type(ex).__name__}: {ex}"}


def anthropic_tool_defs() -> list[dict]:
    return [{"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]} for t in TOOLS]


_DROP_FOR_LLM = {"evidence", "assessments", "impact_queries"}


def compact_for_llm(result: Any, limit: int = 24000) -> str:
    """Trim a tool result for the model: the graph evidence (nodes/edges) is for the UI; the
    model gets the readable paths, facts and Cypher, which are what it needs to cite."""
    import json

    def strip(v):
        if isinstance(v, dict):
            return {k: strip(x) for k, x in v.items() if k not in _DROP_FOR_LLM}
        if isinstance(v, list):
            return [strip(x) for x in v]
        return v

    s = json.dumps(strip(result), default=str, ensure_ascii=False)
    return s if len(s) <= limit else s[:limit] + '..."[truncated]"'
