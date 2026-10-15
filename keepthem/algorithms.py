"""Graph algorithms exposed to the agent via `run_graph_algorithm`.

Mix of FalkorDB's *native* algorithms (CALL algo.*) and plain Cypher analytics. Every
algorithm returns the same envelope:

    {algorithm, description, how_to_read, results[], queries[], evidence{nodes,edges}, paths[]}
"""
from __future__ import annotations

from . import config
from .db import KnowledgeGraph
from .evidence import Evidence
from .risk import resolve_employee

ALGORITHMS = {
    "manager_load": "Degree centrality on REPORTS_TO: direct reports per manager, flagging overload.",
    "peer_influence": "Degree centrality on PEER_OF: who is most connected in the peer network (and how many peers already left).",
    "pagerank": "FalkorDB native PageRank over REPORTS_TO or PEER_OF: structural importance.",
    "betweenness": "FalkorDB native betweenness over PEER_OF: people who bridge otherwise separate groups.",
    "attrition_clusters": "FalkorDB native label propagation over PEER_OF: peer communities that already lost someone.",
    "peer_contagion": "Variable-length paths (PEER_OF*1..2): active employees closest to people who resigned.",
    "shortest_path": "Shortest connection between two employees across PEER_OF/REPORTS_TO/WORKED_ON/MEMBER_OF. params: from_employee, to_employee.",
}

Q_MANAGER_LOAD = """
MATCH (m:Manager {status:'active'})
OPTIONAL MATCH (m)<-[:REPORTS_TO]-(r:Employee {status:'active'})
WITH m, count(r) AS direct_reports
WHERE direct_reports > 0
OPTIONAL MATCH (m)<-[:REPORTS_TO]-(x:Employee {status:'left'})
OPTIONAL MATCH (m)-[:MEMBER_OF]->(t:Team)
RETURN m.id AS id, m.name AS manager, t.name AS team, direct_reports, count(x) AS reports_who_left
ORDER BY direct_reports DESC"""

Q_REPORT_PATHS = """
MATCH p=(r:Employee {status:'active'})-[:REPORTS_TO]->(m:Manager {id:$mid})
RETURN p"""

Q_PEER_DEGREE = """
MATCH (e:Employee {status:'active'})
OPTIONAL MATCH (e)-[:PEER_OF]-(p:Employee)
RETURN e.id AS id, e.name AS employee, count(p) AS peers,
       toInteger(sum(CASE WHEN p.status = 'left' THEN 1 ELSE 0 END)) AS peers_who_left
ORDER BY peers DESC, employee"""

Q_PAGERANK = "CALL algo.pageRank('Employee', '{rel}') YIELD node, score RETURN node.id AS id, node.name AS employee, node.status AS status, score ORDER BY score DESC"
Q_BETWEENNESS = ("CALL algo.betweenness({nodeLabels:['Employee'], relationshipTypes:['PEER_OF']}) "
                 "YIELD node, score RETURN node.id AS id, node.name AS employee, node.status AS status, score "
                 "ORDER BY score DESC")
Q_COMMUNITIES = ("CALL algo.labelPropagation({nodeLabels:['Employee'], relationshipTypes:['PEER_OF'], maxIterations:20}) "
                 "YIELD node, communityId RETURN communityId, collect({id: node.id, name: node.name, status: node.status}) AS members")
Q_COMMUNITY_EDGES = """
MATCH p=(a:Employee)-[:PEER_OF]-(b:Employee)
WHERE a.id IN $ids AND b.id IN $ids AND a.id < b.id
RETURN p"""
Q_CONTAGION = """
MATCH p=(e:Employee {status:'active'})-[:PEER_OF*1..2]-(x:Employee {status:'left'})
RETURN e.id AS id, e.name AS employee, x.name AS leaver, size(relationships(p)) AS hops, p"""
Q_SHORTEST = """
MATCH p=(a:Employee {id:$a})-[:PEER_OF|REPORTS_TO|WORKED_ON|MEMBER_OF*1..5]-(b:Employee {id:$b})
RETURN p, size(relationships(p)) AS hops ORDER BY hops ASC LIMIT 3"""


def _env(name, how, results, queries, ev: Evidence):
    return {"algorithm": name, "description": ALGORITHMS[name], "how_to_read": how, "results": results,
            "queries": queries, "evidence": ev.to_dict(), "paths": ev.paths}


def run(kg: KnowledgeGraph, algorithm: str, params: dict | None = None) -> dict:
    params = params or {}
    algorithm = (algorithm or "").strip().lower()
    if algorithm not in ALGORITHMS:
        return {"error": f"Unknown algorithm '{algorithm}'", "available": ALGORITHMS}
    top = int(params.get("top_n", 8))
    ev = Evidence()

    if algorithm == "manager_load":
        thr = int(params.get("threshold", config.OVERLOAD_THRESHOLD))
        rows = kg.read(Q_MANAGER_LOAD)
        for r in rows:
            r["overloaded"] = r["direct_reports"] >= thr
        queries = [{"name": "degree centrality of REPORTS_TO", "cypher": Q_MANAGER_LOAD.strip(), "params": {}}]
        for r in [r for r in rows if r["overloaded"]]:
            queries.append({"name": f"reports of overloaded manager {r['manager']}", "cypher": Q_REPORT_PATHS.strip(),
                            "params": {"mid": r["id"]}})
            for p in kg.read(Q_REPORT_PATHS, {"mid": r["id"]}):
                ev.add_path(p["p"])
        return _env(algorithm, f"Managers with >= {thr} active direct reports are overloaded; "
                    "`reports_who_left` shows whether the load already cost the team people.", rows, queries, ev)

    if algorithm == "peer_influence":
        rows = kg.read(Q_PEER_DEGREE)[:top]
        return _env(algorithm, "More peers = more exposure to peer-driven attrition contagion if someone leaves.",
                    rows, [{"name": "degree centrality of PEER_OF", "cypher": Q_PEER_DEGREE.strip(), "params": {}}], ev)

    if algorithm == "pagerank":
        rel = str(params.get("relationship", "PEER_OF")).upper()
        if rel not in ("PEER_OF", "REPORTS_TO"):
            return {"error": "relationship must be PEER_OF or REPORTS_TO"}
        q = Q_PAGERANK.format(rel=rel)
        rows = [r for r in kg.read(q) if r["status"] == "active"][:top]
        for r in rows:
            r["score"] = round(r["score"], 4)
        note = ("Edges point from report to manager, so rank flows up the chain." if rel == "REPORTS_TO" else
                "PEER_OF edges are stored in one direction, so treat this as a coarse centrality signal.")
        return _env(algorithm, note, rows, [{"name": f"native PageRank over {rel}", "cypher": q, "params": {}}], ev)

    if algorithm == "betweenness":
        rows = [r for r in kg.read(Q_BETWEENNESS) if r["status"] == "active"][:top]
        return _env(algorithm, "High betweenness = a bridge between peer groups; their departure would "
                    "disconnect them, and bridges spread attrition news.", rows,
                    [{"name": "native betweenness over PEER_OF", "cypher": Q_BETWEENNESS, "params": {}}], ev)

    if algorithm == "attrition_clusters":
        rows = kg.read(Q_COMMUNITIES)
        clusters = []
        for r in rows:
            members = r["members"]
            left = [m for m in members if m["status"] == "left"]
            active = [m for m in members if m["status"] == "active"]
            if left and active:
                clusters.append({"community": r["communityId"], "left": [m["name"] for m in left],
                                 "still_here": [m["name"] for m in active], "ids": [m["id"] for m in members]})
        clusters.sort(key=lambda c: -len(c["left"]))
        queries = [{"name": "native label propagation over PEER_OF", "cypher": Q_COMMUNITIES, "params": {}}]
        for c in clusters:
            queries.append({"name": f"peer links inside community {c['community']}",
                            "cypher": Q_COMMUNITY_EDGES.strip(), "params": {"ids": c["ids"]}})
            for p in kg.read(Q_COMMUNITY_EDGES, {"ids": c["ids"]}):
                ev.add_path(p["p"])
            del c["ids"]
        return _env(algorithm, "Each cluster is a tight peer group that has already lost someone. "
                    "Colleagues in it are the likeliest to follow.", clusters, queries, ev)

    if algorithm == "peer_contagion":
        rows = kg.read(Q_CONTAGION)
        agg: dict[str, dict] = {}
        for r in rows:
            a = agg.setdefault(r["id"], {"id": r["id"], "employee": r["employee"], "exposure": 0.0, "leavers": {}})
            if r["leaver"] not in a["leavers"] or r["hops"] < a["leavers"][r["leaver"]]:
                if r["leaver"] not in a["leavers"]:
                    a["exposure"] += 1.0 / r["hops"]
                a["leavers"][r["leaver"]] = r["hops"]
        results = sorted(agg.values(), key=lambda a: -a["exposure"])[:top]
        for a in results:
            a["exposure"] = round(a["exposure"], 2)
            a["leavers"] = [{"name": n, "hops": h} for n, h in sorted(a["leavers"].items(), key=lambda kv: kv[1])]
        keep = {a["id"] for a in results}
        best: dict[tuple, dict] = {}
        for r in rows:
            if r["id"] in keep and ((r["id"], r["leaver"]) not in best or r["hops"] < best[(r["id"], r["leaver"])]["hops"]):
                best[(r["id"], r["leaver"])] = r
        for r in best.values():
            ev.add_path(r["p"])
        return _env(algorithm, "exposure = sum over leavers of 1/hops through PEER_OF. A direct peer (1 hop) "
                    "counts double a peer-of-peer (2 hops).", results,
                    [{"name": "paths from active employees to leavers over PEER_OF*1..2",
                      "cypher": Q_CONTAGION.strip(), "params": {}}], ev)

    # shortest_path
    a, b = resolve_employee(kg, params.get("from_employee", "")), resolve_employee(kg, params.get("to_employee", ""))
    if not a or not b or "ambiguous" in a or "ambiguous" in b:
        return {"error": "Provide unambiguous params.from_employee and params.to_employee (name or id).",
                "from": a, "to": b}
    rows = kg.read(Q_SHORTEST, {"a": a["id"], "b": b["id"]})
    for r in rows:
        ev.add_path(r["p"])
    results = [{"hops": r["hops"], "path": p} for r, p in zip(rows, ev.paths)]
    return _env(algorithm, f"Shortest connection(s) between {a['name']} and {b['name']}; "
                "fewer hops = stronger social/organisational tie.", results,
                [{"name": "bounded variable-length path search", "cypher": Q_SHORTEST.strip(),
                  "params": {"a": a["id"], "b": b["id"]}}], ev)
