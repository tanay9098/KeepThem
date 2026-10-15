"""Load the synthetic company into FalkorDB.

    python -m keepthem.seed            # (re)build the graph
    python -m keepthem.seed --no-reset # add to whatever is there

Graph model (all nodes carry a unique business `id`):

  (Employee {id,name,role,tenure_months,location,salary_band,status})   [+ :Manager label]
  (Team)  (Project)  (Review {score,date,sentiment,summary})  (Skill {market_demand})
  (Event {type,date,description})  (Decision {type,date,outcome,reason})

  (Employee)-[:REPORTS_TO]->(Manager)      (Employee)-[:MEMBER_OF]->(Team)
  (Employee)-[:WORKED_ON]->(Project)       (Employee)-[:HAS_SKILL]->(Skill)
  (Employee)-[:RECEIVED]->(Review)         (Employee)-[:EXPERIENCED]->(Event)
  (Manager)-[:MADE]->(Decision)            (Decision)-[:AFFECTED]->(Employee)
  (Employee)-[:PEER_OF]->(Employee)        (Team)-[:OWNS]->(Project)
"""
from __future__ import annotations

import argparse
from datetime import date

from . import config, data
from .db import KnowledgeGraph, get_kg

SCHEMA = [  # indexes make id lookups O(log n); FalkorDB creates them on first use
    "CREATE INDEX FOR (e:Employee) ON (e.id)",
    "CREATE INDEX FOR (e:Employee) ON (e.status)",
    "CREATE INDEX FOR (t:Team) ON (t.id)",
    "CREATE INDEX FOR (p:Project) ON (p.id)",
    "CREATE INDEX FOR (s:Skill) ON (s.id)",
    "CREATE INDEX FOR (ev:Event) ON (ev.type)",
    "CREATE INDEX FOR (d:Decision) ON (d.type)",
    "CREATE INDEX FOR (r:Review) ON (r.date)",
]


def _sentiment(score: float) -> str:
    return "positive" if score >= 4.0 else "neutral" if score >= 3.0 else "negative"


def _summary(emp_id: str, review_date: str, score: float) -> str:
    if (emp_id, review_date) in data.REVIEW_SUMMARIES:
        return data.REVIEW_SUMMARIES[(emp_id, review_date)]
    if score >= 4.0:
        return "Strong performance; consistently exceeds expectations."
    if score >= 3.0:
        return "Solid contributor; meets expectations."
    return "Below expectations this period; support plan recommended."


def seed(kg: KnowledgeGraph | None = None, reset: bool = True, verbose: bool = True) -> dict:
    kg = kg or get_kg()
    if reset:
        kg.delete()
    for ddl in SCHEMA:
        try:
            kg.write(ddl)
        except Exception:  # index already exists
            pass

    as_of = date.fromisoformat(config.AS_OF)
    managers = {e[4] for e in data.EMPLOYEES if e[4]}

    # Teams, projects, skills ---------------------------------------------------------
    for tid, name, fn in data.TEAMS:
        kg.write("MERGE (t:Team {id:$id}) SET t.name=$name, t.function=$fn", dict(id=tid, name=name, fn=fn))
    for pid, name, status, tid in data.PROJECTS:
        kg.write("MATCH (t:Team {id:$tid}) MERGE (p:Project {id:$id}) SET p.name=$name, p.status=$status "
                 "MERGE (t)-[:OWNS]->(p)", dict(id=pid, name=name, status=status, tid=tid))
    for sid, name, demand in data.SKILLS:
        kg.write("MERGE (s:Skill {id:$id}) SET s.name=$name, s.market_demand=$d", dict(id=sid, name=name, d=demand))

    # Employees (+ :Manager label for anyone with reports) ------------------------------
    for eid, name, role, tid, mid, tenure, loc, band, status in data.EMPLOYEES:
        label = "Employee:Manager" if eid in managers else "Employee"
        kg.write(f"MERGE (e:{label} {{id:$id}}) SET e.name=$name, e.role=$role, e.tenure_months=$tenure, "
                 "e.location=$loc, e.salary_band=$band, e.status=$status",
                 dict(id=eid, name=name, role=role, tenure=tenure, loc=loc, band=band, status=status))
        kg.write("MATCH (e:Employee {id:$id}),(t:Team {id:$tid}) MERGE (e)-[:MEMBER_OF]->(t)", dict(id=eid, tid=tid))
        if mid:
            kg.write("MATCH (e:Employee {id:$id}),(m:Manager {id:$mid}) MERGE (e)-[:REPORTS_TO]->(m)",
                     dict(id=eid, mid=mid))

    # Peers, projects, skills ------------------------------------------------------------
    for a, b in data.PEERS:
        kg.write("MATCH (a:Employee {id:$a}),(b:Employee {id:$b}) MERGE (a)-[:PEER_OF]->(b)", dict(a=a, b=b))
    for pid, emps in data.WORKED_ON.items():
        for eid in emps:
            kg.write("MATCH (e:Employee {id:$e}),(p:Project {id:$p}) MERGE (e)-[:WORKED_ON]->(p)", dict(e=eid, p=pid))
    for eid, skills in data.EMPLOYEE_SKILLS.items():
        for sid in skills:
            kg.write("MATCH (e:Employee {id:$e}),(s:Skill {id:$s}) MERGE (e)-[:HAS_SKILL]->(s)", dict(e=eid, s=sid))

    # Reviews ------------------------------------------------------------------------------
    n_reviews = 0
    for eid, scores in data.REVIEW_SCORES.items():
        for i, (rdate, score) in enumerate(zip(data.REVIEW_DATES[-len(scores):], scores)):
            kg.write("MATCH (e:Employee {id:$e}) CREATE (e)-[:RECEIVED]->(:Review {id:$id, score:$score, "
                     "date:$date, sentiment:$sent, summary:$summary})",
                     dict(e=eid, id=f"R-{eid}-{i + 1}", score=score, date=rdate, sent=_sentiment(score),
                          summary=_summary(eid, rdate, score)))
            n_reviews += 1

    # Events (explicit + generated resignations and peer_left) --------------------------------
    events = list(data.EVENTS)
    for eid, left in data.LEFT_DATES.items():
        events.append((eid, "resignation", left, data.RESIGNATION_TEXT[eid]))
        leaver = next(e for e in data.EMPLOYEES if e[0] == eid)
        for a, b in data.PEERS:  # everyone who was a peer of the leaver experienced a peer_left
            other = b if a == eid else a if b == eid else None
            if other and other not in data.LEFT_DATES:
                events.append((other, "peer_left", left, f"Peer {leaver[1]} left the company."))
    for i, (eid, etype, edate, desc) in enumerate(events, 1):
        kg.write("MATCH (e:Employee {id:$e}) CREATE (e)-[:EXPERIENCED]->(:Event {id:$id, type:$t, date:$d, "
                 "description:$desc})", dict(e=eid, id=f"EV-{i:04d}", t=etype, d=edate, desc=desc))

    # Decisions -----------------------------------------------------------------------------------
    for did, maker, affected, dtype, ddate, outcome, reason in data.DECISIONS:
        kg.write("MATCH (m:Manager {id:$m}),(e:Employee {id:$e}) CREATE (m)-[:MADE]->(d:Decision {id:$id, "
                 "type:$t, date:$date, outcome:$o, reason:$r})-[:AFFECTED]->(e)",
                 dict(m=maker, e=affected, id=did, t=dtype, date=ddate, o=outcome, r=reason))

    stats = {r["label"]: r["n"] for r in kg.read(
        "MATCH (n) RETURN labels(n)[0] AS label, count(n) AS n ORDER BY label")}
    stats["relationships"] = kg.read("MATCH ()-[r]->() RETURN count(r) AS n")[0]["n"]
    if verbose:
        print(f"Seeded '{config.GRAPH_NAME}' on {kg.backend} (as of {as_of}):")
        for k, v in stats.items():
            print(f"  {k:<14}{v}")
    return stats


def ensure_seeded(kg: KnowledgeGraph | None = None) -> None:
    """Seed only if the graph is empty (used by the CLI/UI for a zero-setup first run)."""
    kg = kg or get_kg()
    if kg.is_empty():
        seed(kg, verbose=False)


def main() -> None:
    ap = argparse.ArgumentParser(description="Seed the KeepThem graph in FalkorDB")
    ap.add_argument("--no-reset", action="store_true", help="don't delete the existing graph first")
    seed(reset=not ap.parse_args().no_reset)


if __name__ == "__main__":
    main()
