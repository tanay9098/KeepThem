"""Explainability helpers: turn graph paths into readable strings and node/edge evidence.

Every tool collects the paths its Cypher returned into an `Evidence` object. The
evidence is returned with the answer so a human (or the LLM) can see exactly which
nodes and relationships the conclusion rests on.
"""
from __future__ import annotations


def node_name(n: dict) -> str:
    """Human-readable label for a node dict (see db.to_py)."""
    p, labels = n["props"], n["labels"]
    if "Review" in labels:
        return f"Review {p.get('date')} score {p.get('score')}"
    if "Event" in labels:
        return f"Event {p.get('type')} {p.get('date')}"
    if "Decision" in labels:
        return f"Decision {p.get('type')} {p.get('date')} [{p.get('outcome')}]"
    if "Project" in labels:
        return f"Project {p.get('name')}"
    if "Team" in labels:
        return f"Team {p.get('name')}"
    if "Skill" in labels:
        return f"Skill {p.get('name')}"
    return str(p.get("name") or p.get("id"))


def node_kind(n: dict) -> str:
    labels = n["labels"]
    return "Manager" if "Manager" in labels else (labels[0] if labels else "Node")


def path_to_str(path: dict) -> str:
    """(Dana Ruiz)-[:REPORTS_TO]->(Marcus Webb)<-[:REPORTS_TO]-(Sam Okoye)"""
    nodes, edges = path["nodes"], path["edges"]
    out = f"({node_name(nodes[0])})"
    for i, e in enumerate(edges):
        nxt = f"({node_name(nodes[i + 1])})"
        out += f"-[:{e['type']}]->{nxt}" if e["src"] == nodes[i]["gid"] else f"<-[:{e['type']}]-{nxt}"
    return out


class Evidence:
    """A de-duplicated set of nodes and edges, keyed by their business `id` property."""

    def __init__(self):
        self.nodes: dict[str, dict] = {}
        self.edges: dict[tuple, dict] = {}
        self.paths: list[str] = []

    def add_node(self, n: dict) -> None:
        key = n["props"].get("id", f"gid{n['gid']}")
        self.nodes.setdefault(key, {"id": key, "kind": node_kind(n), "name": node_name(n)})

    def add_path(self, path: dict) -> str:
        by_gid = {n["gid"]: n for n in path["nodes"]}
        for n in path["nodes"]:
            self.add_node(n)
        for e in path["edges"]:
            s, d = by_gid[e["src"]], by_gid[e["dst"]]
            sid, did = s["props"].get("id"), d["props"].get("id")
            self.edges.setdefault((sid, e["type"], did), {"from": sid, "to": did, "type": e["type"]})
        text = path_to_str(path)
        if text not in self.paths:
            self.paths.append(text)
        return text

    def merge(self, other: "Evidence") -> "Evidence":
        for k, v in other.nodes.items():
            self.nodes.setdefault(k, v)
        for k, v in other.edges.items():
            self.edges.setdefault(k, v)
        for p in other.paths:
            if p not in self.paths:
                self.paths.append(p)
        return self

    def to_dict(self) -> dict:
        return {"nodes": list(self.nodes.values()), "edges": list(self.edges.values())}
