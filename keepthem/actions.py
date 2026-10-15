"""Recommendations: turn the signals the graph found into concrete, owned actions.

Every recommendation says
  * WHAT to do and WHO owns it                (owner is read from the graph: manager / director)
  * WHY - which signals triggered it           (`triggered_by`) and the supporting graph paths
  * HOW the specifics were chosen              (extra Cypher, e.g. "least-loaded sibling manager")

Rules are deliberately plain Python so a hackathon team can add one in ~15 lines.
"""
from __future__ import annotations

from . import config
from .db import KnowledgeGraph
from .evidence import Evidence
from .risk import assess_employee

# --- extra graph lookups used to make recommendations specific -----------------------------
# Sibling = another manager under the same director. Prefer the one whose reports share the most
# skills with the overloaded manager's reports (so moved people can actually be useful), then the lowest load.
Q_SIBLING_MANAGERS = """
MATCH p=(e:Employee {id:$id})-[:REPORTS_TO]->(m:Manager)-[:REPORTS_TO]->(d:Manager)<-[:REPORTS_TO]-(sib:Manager)
WHERE sib.id <> m.id
OPTIONAL MATCH (sib)<-[:REPORTS_TO]-(r:Employee {status:'active'})
WITH p, m, sib, d, count(r) AS load
OPTIONAL MATCH (m)<-[:REPORTS_TO]-(:Employee {status:'active'})-[:HAS_SKILL]->(s:Skill)
              <-[:HAS_SKILL]-(:Employee {status:'active'})-[:REPORTS_TO]->(sib)
WITH p, sib, d, load, count(DISTINCT s) AS skill_overlap
RETURN p, sib.id AS sibling_id, sib.name AS sibling, d.id AS director_id, d.name AS director,
       load, skill_overlap
ORDER BY skill_overlap DESC, load ASC LIMIT 1"""

Q_MOBILITY = """
MATCH p=(e:Employee {id:$id})-[:HAS_SKILL]->(s:Skill)<-[:HAS_SKILL]-(c:Employee {status:'active'})
      -[:WORKED_ON]->(pr:Project {status:'active'})<-[:OWNS]-(t:Team)
WHERE c.id <> e.id AND NOT (e)-[:WORKED_ON]->(pr) AND NOT (e)-[:MEMBER_OF]->(t)
RETURN p, pr.name AS project, pr.id AS project_id, s.name AS skill"""

Q_OPEN_ACTIONS = """
MATCH (d:Decision)-[:AFFECTED]->(e:Employee {id:$id})
WHERE d.type STARTS WITH 'retention_' AND d.outcome IN ['proposed','completed']
RETURN d.type AS type, d.outcome AS outcome, d.date AS date"""

Q_DIRECTOR = """
MATCH p=(e:Employee {id:$id})-[:REPORTS_TO]->(m:Manager)-[:REPORTS_TO]->(d:Manager)
RETURN p, d.name AS director"""

Q_CAUSE_DECISION = """
MATCH p=(h:Manager)-[:MADE]->(d:Decision {id:$did})-[:AFFECTED]->(m:Manager)
RETURN p, h.name AS maker"""


def _rec(action_type, title, owner, urgency, because, triggered_by, evidence=None, queries=None, detail=""):
    return {"action_type": action_type, "action": title, "detail": detail, "owner": owner,
            "urgency": urgency, "because": because, "triggered_by": triggered_by,
            "paths": (evidence.paths if evidence else []), "queries": queries or [],
            "evidence": evidence.to_dict() if evidence else {"nodes": [], "edges": []}}


def recommend(kg: KnowledgeGraph, emp_id: str, assessment: dict | None = None) -> dict:
    """Return a ranked list of recommendations for one employee."""
    a = assessment or assess_employee(kg, emp_id)
    if "error" in a:
        return a
    emp = a["employee"]
    sig = {s["key"]: s for s in a["signals"] if s["points"] > 0}
    manager = emp["manager"] or "their manager"
    recs: list[dict] = []

    existing = {(r["type"], r["outcome"]) for r in kg.read(Q_OPEN_ACTIONS, {"id": emp_id})}

    # 1 Overloaded manager -> rebalance span of control (and fix the upstream cause)
    if "overloaded_manager" in sig:
        sibs = kg.read(Q_SIBLING_MANAGERS, {"id": emp_id})
        ev, q = Evidence(), [{"name": "least-loaded sibling manager under the same director",
                              "cypher": Q_SIBLING_MANAGERS.strip(), "params": {"id": emp_id}}]
        if sibs:
            s = sibs[0]
            ev.add_path(s["p"])
            n = sig["overloaded_manager"]["facts"]["direct_reports"]
            move = max(2, n - config.OVERLOAD_THRESHOLD + 2)
            detail = (f"{s['director']} should move {move} of {manager}'s {n} reports to {s['sibling']} "
                      f"(currently {s['load']} reports; {s['skill_overlap']} overlapping skills)")
            detail += (", and revisit the denied backfill that caused the overload."
                       if "root_cause_decision" in sig["overloaded_manager"]["facts"] else ".")
            owner = s["director"]
        else:
            detail, owner = f"Rebalance {manager}'s team or approve a backfill.", "HRBP"
        ev.merge(_evidence_of(sig["overloaded_manager"]))
        recs.append(_rec("retention_manager_rebalance", f"Reduce {manager}'s span of control", owner, "within 2 weeks",
                         sig["overloaded_manager"]["summary"], ["overloaded_manager"], ev, q, detail))

    # 2 Low/falling review -> calibration + career conversation
    if "low_review" in sig:
        f = sig["low_review"]["facts"]
        recs.append(_rec(
            "retention_career_plan", f"Hold a career and workload conversation with {emp['name']}",
            manager, "this week",
            sig["low_review"]["summary"] + (" A sharp drop in a previously strong performer is usually "
            "context (workload, cancelled project), not capability." if f["drop_vs_previous"] >= 0.5 else ""),
            ["low_review"], _evidence_of(sig["low_review"]), [],
            detail=f"Re-calibrate the latest review (score {f['latest']}) against project outcomes with the "
                   f"skip-level manager before it is final."))

    # 3 Adverse decisions -> reopen
    if "negative_decisions" in sig:
        types = [d["type"] for d in sig["negative_decisions"]["facts"]["decisions"]]
        promo = "promotion_denied" in types
        recs.append(_rec(
            "retention_promotion_review" if promo else "retention_comp_review",
            f"Re-open the {'promotion' if promo else 'compensation'} decision for {emp['name']}",
            manager if not promo else "Director + HRBP", "within 30 days",
            sig["negative_decisions"]["summary"], ["negative_decisions"], _evidence_of(sig["negative_decisions"]), [],
            detail=("Recorded reason: " + "; ".join(
                        f"'{d['reason']}'" for d in sig["negative_decisions"]["facts"]["decisions"]
                        if d["type"] == ("promotion_denied" if promo else d["type"]))
                    + " " + ("Share written criteria and a dated timeline for the next calibration."
                              if promo else "Check the denial against the current budget cycle."))))

    # 4 Peer / project attrition -> stay interview
    peers = sig.get("peer_attrition"), sig.get("shared_project_exits")
    if any(peers):
        trig = [s["key"] for s in peers if s]
        who = sorted({n for s in peers if s for k in ("peer_leavers", "teammate_leavers", "leavers")
                      for n in s["facts"].get(k, [])})
        ev = Evidence()
        for s in peers:
            if s:
                ev.merge(_evidence_of(s))
        recs.append(_rec(
            "retention_stay_interview", f"Run a stay interview with {emp['name']} (ask about the exits of {' and '.join(who)})",
            "HRBP", "within 7 days",
            " ".join(s["summary"] for s in peers if s), trig, ev, [],
            detail="Ask what pushed colleagues out, and what would make staying a clear yes."))

    # 5 Same cancelled/troubled project -> rotation option
    if "shared_project_exits" in sig:
        rows = kg.read(Q_MOBILITY, {"id": emp_id})
        by_proj: dict[str, dict] = {}
        for r in rows:
            d = by_proj.setdefault(r["project"], {"skills": set(), "paths": []})
            d["skills"].add(r["skill"])
            d["paths"].append(r["p"])
        by_proj = {k: v for k, v in by_proj.items() if len(v["skills"]) >= 2}
        if by_proj:
            best, d = max(by_proj.items(), key=lambda kv: len(kv[1]["skills"]))
            ev = Evidence()
            for p in d["paths"][:4]:
                ev.add_path(p)
            recs.append(_rec(
                "retention_project_rotation", f"Offer {emp['name']} a rotation onto {best}",
                manager, "within 30 days",
                f"Skills overlap on {', '.join(sorted(d['skills']))} with people already on {best}; "
                f"current project history ({', '.join(sig['shared_project_exits']['facts']['projects'])}) is "
                "where colleagues resigned.", ["shared_project_exits"], ev,
                [{"name": "active projects staffed by people who share this person's skills",
                  "cypher": Q_MOBILITY.strip(), "params": {"id": emp_id}}],
                detail="Internal mobility is cheaper than replacement and gives a visible fresh start."))

    # 6 Pay gap
    if "pay_gap" in sig:
        f = sig["pay_gap"]["facts"]
        recs.append(_rec(
            "retention_comp_review", f"Review {emp['name']}'s compensation",
            "HRBP + " + manager, "next comp cycle (or off-cycle if high risk)",
            sig["pay_gap"]["summary"], ["pay_gap"], _evidence_of(sig["pay_gap"]), [],
            detail=f"Band {f['salary_band']} vs {f['same_role_team_avg_band']} for the same role on the team."))

    # 7 Complaint / negative event follow-up
    evs = sig.get("negative_events")
    if evs and any(e["type"] == "complaint" for e in evs["facts"]["events"]):
        recs.append(_rec(
            "retention_complaint_followup", f"Close the loop on {emp['name']}'s complaint",
            "HRBP", "this week", evs["summary"], ["negative_events"], _evidence_of(evs), [],
            detail="Acknowledge it in writing and name one visible change."))

    # 8 Stagnation
    if "stagnation" in sig:
        recs.append(_rec(
            "retention_career_plan", f"Build a growth path for {emp['name']}", manager, "next quarter",
            sig["stagnation"]["summary"], ["stagnation"], _evidence_of(sig["stagnation"]), [],
            detail="Agree the next-level criteria and a review date."))

    # 9 Key-person impact -> knowledge transfer
    if a["impact"]["sole_skills"] and a["risk_level"] != "low":
        sk = ", ".join(a["impact"]["sole_skills"])
        key_ev = Evidence()
        for pth in a["impact"]["paths"]:
            key_ev.paths.append(pth)
        for n in a["impact"]["evidence"]["nodes"]:
            key_ev.nodes[n["id"]] = n
        for ed in a["impact"]["evidence"]["edges"]:
            key_ev.edges[(ed["from"], ed["type"], ed["to"])] = ed
        recs.append(_rec(
            "retention_knowledge_transfer", f"De-risk the key-person dependency on {emp['name']} ({sk})",
            manager, "within 30 days",
            f"{emp['name']} is the only person on {emp['team']} with: {sk}. "
            f"If they leave, the team loses these capabilities.", ["key_person"], key_ev,
            a["impact_queries"], detail="Pair a second engineer on these areas and document runbooks."))

    # Dedupe by action_type (keep first), annotate with already-logged status, rank by triggering points
    seen, out = set(), []
    for r in recs:
        if r["action_type"] in seen:
            continue
        seen.add(r["action_type"])
        pts = sum(sig[k]["points"] for k in r["triggered_by"] if k in sig)
        pts += 10 * len(a["impact"]["sole_skills"]) if "key_person" in r["triggered_by"] else 0
        r["impact_points"] = pts
        r["already_logged"] = next((o for (t, o) in existing if t == r["action_type"]), None)
        out.append(r)
    out.sort(key=lambda r: -r["impact_points"])
    for i, r in enumerate(out, 1):
        r["rank"] = i

    if not out:
        out = [dict(_rec("none", "No intervention needed - keep monitoring", manager, "next review cycle",
                         "No risk signals fired in the graph.", [], None, [], "Re-run after the next review cycle."),
                    rank=1, impact_points=0, already_logged=None)]
    ev = Evidence()
    for r in out:
        for n in r["evidence"]["nodes"]:
            ev.nodes.setdefault(n["id"], n)
        for e in r["evidence"]["edges"]:
            ev.edges.setdefault((e["from"], e["type"], e["to"]), e)
        for p in r["paths"]:
            ev.paths.append(p) if p not in ev.paths else None
    return {"employee": emp, "risk_score": a["risk_score"], "risk_level": a["risk_level"],
            "recommendations": out, "evidence": ev.to_dict(), "paths": ev.paths}


def _evidence_of(signal_dict: dict) -> Evidence:
    """Rebuild an Evidence from a serialised signal (nodes/edges/paths)."""
    ev = Evidence()
    for n in signal_dict["evidence"]["nodes"]:
        ev.nodes[n["id"]] = n
    for e in signal_dict["evidence"]["edges"]:
        ev.edges[(e["from"], e["type"], e["to"])] = e
    ev.paths = list(signal_dict["paths"])
    return ev
