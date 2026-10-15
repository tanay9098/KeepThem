"""The risk engine: multi-hop Cypher signals -> an explainable attrition-risk score.

Design rule: a signal is only worth points if the graph can SHOW it. Each signal is one
Cypher query that returns *paths*; those paths are the evidence we hand back to the user.

    score = clamp( sum(signal.points), 0, 100 )          (mitigation signals are negative)

Signals (max points) and the multi-hop pattern behind each:

  low_review         (20) (Employee)-[:RECEIVED]->(Review)                latest score low / falling
  overloaded_manager (16) (Employee)-[:REPORTS_TO]->(Manager)<-[:REPORTS_TO]-(peers)
                          ... <-[:AFFECTED]-(headcount_denied)<-[:MADE]-(Director)   (root cause)
  peer_attrition     (20) (Employee)-[:PEER_OF]-(Leaver)-[:EXPERIENCED]->(resignation)
                          (Employee)-[:MEMBER_OF]->(Team)<-[:MEMBER_OF]-(Leaver)-...->(resignation)
  shared_project_exits(12)(Employee)-[:WORKED_ON]->(Project)<-[:WORKED_ON]-(Leaver)-...->(resignation)
  negative_decisions (16) (Manager)-[:MADE]->(Decision)-[:AFFECTED]->(Employee)   denied raise/promotion...
  negative_events    (12) (Employee)-[:EXPERIENCED]->(Event)                      complaint, reorg, ...
  pay_gap            (10) (Employee)-[:MEMBER_OF]->(Team)<-[:MEMBER_OF]-(same-role colleague)
  stagnation          (6) no promotion Event in STAGNATION_DAYS with tenure >= 24 months
  hot_skills          (5) (Employee)-[:HAS_SKILL]->(Skill {market_demand:'high'})   easy to poach
  retention_actions  (-16) completed retention Decisions logged by KeepThem          mitigation

Separate from risk, `impact` says how costly a departure would be: skills only this person
holds on their team (the graph makes "key person" a query, not a hunch).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import config
from .db import KnowledgeGraph
from .evidence import Evidence

LEAVER_RESIGNATION = "(x:Employee {status:'left'})-[:EXPERIENCED]->(ev:Event {type:'resignation'})"


# --------------------------------------------------------------------------------------
# Cypher for each signal. Kept as module constants so tools can show them verbatim.
# --------------------------------------------------------------------------------------
Q_PROFILE = """
MATCH (e:Employee {id:$id})
OPTIONAL MATCH (e)-[:MEMBER_OF]->(t:Team)
OPTIONAL MATCH (e)-[:REPORTS_TO]->(m:Manager)
RETURN e, t.name AS team, t.id AS team_id, m.id AS manager_id, m.name AS manager"""

Q_REVIEWS = """
MATCH p=(e:Employee {id:$id})-[:RECEIVED]->(r:Review)
RETURN p, r.score AS score, r.date AS date
ORDER BY r.date DESC LIMIT 3"""

Q_MANAGER_LOAD = """
MATCH (e:Employee {id:$id})-[:REPORTS_TO]->(m:Manager)
OPTIONAL MATCH (m)<-[:REPORTS_TO]-(r:Employee {status:'active'})
RETURN m.id AS manager_id, m.name AS manager, count(r) AS direct_reports"""

Q_MANAGER_REPORTS = """
MATCH p=(e:Employee {id:$id})-[:REPORTS_TO]->(m:Manager)<-[:REPORTS_TO]-(r:Employee {status:'active'})
WHERE r.id <> e.id
RETURN p LIMIT 8"""

Q_MANAGER_ROOT_CAUSE = """
MATCH p=(e:Employee {id:$id})-[:REPORTS_TO]->(m:Manager)<-[:AFFECTED]-(d:Decision {type:'headcount_denied'})<-[:MADE]-(h:Manager)
RETURN p"""

Q_PEERS_LEFT = f"""
MATCH p=(e:Employee {{id:$id}})-[:PEER_OF]-{LEAVER_RESIGNATION}
WHERE ev.date >= $since
RETURN p"""

Q_TEAMMATES_LEFT = f"""
MATCH p=(e:Employee {{id:$id}})-[:MEMBER_OF]->(t:Team)<-[:MEMBER_OF]-{LEAVER_RESIGNATION}
WHERE ev.date >= $since AND NOT (e)-[:PEER_OF]-(x)
RETURN p"""

Q_PROJECT_EXITS = f"""
MATCH p=(e:Employee {{id:$id}})-[:WORKED_ON]->(pr:Project)<-[:WORKED_ON]-{LEAVER_RESIGNATION}
WHERE ev.date >= $since
RETURN p"""

Q_NEG_DECISIONS = """
MATCH p=(m:Manager)-[:MADE]->(d:Decision)-[:AFFECTED]->(e:Employee {id:$id})
WHERE d.type IN $types AND d.date >= $since
RETURN p"""

Q_NEG_EVENTS = """
MATCH p=(e:Employee {id:$id})-[:EXPERIENCED]->(ev:Event)
WHERE ev.type IN $types AND ev.date >= $since
RETURN p"""

Q_PAY_PEERS = """
MATCH p=(e:Employee {id:$id})-[:MEMBER_OF]->(t:Team)<-[:MEMBER_OF]-(o:Employee {status:'active'})
WHERE o.id <> e.id AND o.role = e.role
RETURN p, o.salary_band AS band, e.salary_band AS my_band"""

Q_LAST_PROMOTION = """
MATCH p=(e:Employee {id:$id})-[:EXPERIENCED]->(ev:Event {type:'promotion'})
RETURN p, ev.date AS date ORDER BY ev.date DESC LIMIT 1"""

Q_HOT_SKILLS = """
MATCH p=(e:Employee {id:$id})-[:HAS_SKILL]->(s:Skill {market_demand:'high'})
RETURN p"""

Q_MITIGATION = """
MATCH p=(m:Manager)-[:MADE]->(d:Decision)-[:AFFECTED]->(e:Employee {id:$id})
WHERE d.type STARTS WITH 'retention_' AND d.outcome = 'completed' AND d.date >= $since
RETURN p"""

Q_SOLE_SKILLS = """
MATCH p=(e:Employee {id:$id})-[:HAS_SKILL]->(s:Skill)
OPTIONAL MATCH (e)-[:MEMBER_OF]->(:Team)<-[:MEMBER_OF]-(o:Employee {status:'active'})-[:HAS_SKILL]->(s)
WHERE o.id <> e.id
WITH p, s, count(o) AS others
WHERE others = 0
RETURN p"""


@dataclass
class Signal:
    key: str
    title: str
    max_points: int
    points: int = 0
    summary: str = ""
    queries: list = field(default_factory=list)   # [{"name","cypher","params"}]
    evidence: Evidence = field(default_factory=Evidence)
    facts: dict = field(default_factory=dict)

    def to_dict(self, full: bool = True) -> dict:
        d = {"key": self.key, "title": self.title, "points": self.points, "max_points": self.max_points,
             "summary": self.summary, "facts": self.facts}
        if full:
            d.update(paths=self.evidence.paths, queries=self.queries, evidence=self.evidence.to_dict())
        return d


def _run(kg: KnowledgeGraph, sig: Signal, name: str, cypher: str, params: dict, evidence_ok: bool = True):
    """Run one query, remember it for the explanation, and (optionally) collect its paths."""
    sig.queries.append({"name": name, "cypher": cypher.strip(), "params": params})
    rows = kg.read(cypher, params)
    if evidence_ok:
        for r in rows:
            if isinstance(r.get("p"), dict):
                sig.evidence.add_path(r["p"])
    return rows


def _who(path_rows, kg=None):  # names of the leavers in a set of path rows
    names = []
    for r in path_rows:
        n = r["p"]["nodes"]
        leaver = next((x for x in n if x["props"].get("status") == "left"), None)
        if leaver:
            names.append(leaver["props"]["name"])
    return list(dict.fromkeys(names))


# --------------------------------------------------------------------------------------
# Signals
# --------------------------------------------------------------------------------------
def sig_low_review(kg, emp):
    s = Signal("low_review", "Low or falling performance review", 20)
    rows = _run(kg, s, "recent reviews", Q_REVIEWS, {"id": emp}, evidence_ok=False)
    if not rows:
        return s
    scores = [r["score"] for r in rows]
    latest, prev = scores[0], scores[1] if len(scores) > 1 else None
    pts = 12 if latest <= 2.5 else 8 if latest <= 3.0 else 3 if latest <= 3.4 else 0
    drop = (prev - latest) if prev is not None else 0
    pts += 8 if drop >= 1.0 else 4 if drop >= 0.5 else 0
    s.points = min(pts, s.max_points)
    s.facts = {"scores_latest_first": scores, "latest": latest, "drop_vs_previous": round(drop, 2)}
    if s.points:
        for r in rows:
            s.evidence.add_path(r["p"])
        trend = f" (down {drop:.1f} from {prev})" if drop >= 0.5 else ""
        s.summary = f"Latest review score {latest}{trend}."
    return s


def sig_overloaded_manager(kg, emp):
    s = Signal("overloaded_manager", "Manager is overloaded (span of control)", 16)
    rows = _run(kg, s, "manager direct-report count (degree centrality)", Q_MANAGER_LOAD, {"id": emp},
                evidence_ok=False)
    if not rows:
        return s
    load, mgr = rows[0]["direct_reports"], rows[0]["manager"]
    t = config.OVERLOAD_THRESHOLD
    s.points = 16 if load >= t else 8 if load == t - 1 else 0
    s.facts = {"manager": mgr, "manager_id": rows[0]["manager_id"], "direct_reports": load, "threshold": t}
    if s.points:
        s.summary = f"{mgr} has {load} active direct reports (overload threshold {t})."
        cause = _run(kg, s, "root cause: upstream decision affecting the manager", Q_MANAGER_ROOT_CAUSE,
                     {"id": emp})                       # first, so the root-cause path leads the evidence
        _run(kg, s, "who else reports to the same manager", Q_MANAGER_REPORTS, {"id": emp})
        if cause:
            d = next(n for n in cause[0]["p"]["nodes"] if "Decision" in n["labels"])["props"]
            s.summary += f" Root cause: a {d['type']} decision on {d['date']} ('{d['reason']}')."
            s.facts["root_cause_decision"] = d["id"]
    return s


def sig_peer_attrition(kg, emp):
    s = Signal("peer_attrition", "Peers and teammates recently resigned", 20)
    since = config.days_before(config.PEER_WINDOW_DAYS)
    peers = _run(kg, s, "peers who resigned (PEER_OF -> resignation)", Q_PEERS_LEFT, {"id": emp, "since": since})
    mates = _run(kg, s, "teammates who resigned (same Team, not peers)", Q_TEAMMATES_LEFT,
                 {"id": emp, "since": since})
    s.points = min(10 * len(peers) + 3 * len(mates), s.max_points)
    s.facts = {"peer_leavers": _who(peers), "teammate_leavers": _who(mates), "since": since}
    if s.points:
        parts = []
        if peers:
            parts.append(f"peer(s) {', '.join(s.facts['peer_leavers'])}")
        if mates:
            parts.append(f"teammate(s) {', '.join(s.facts['teammate_leavers'])}")
        s.summary = "Resigned since " + since + ": " + " and ".join(parts) + "."
    return s


def sig_shared_project_exits(kg, emp):
    s = Signal("shared_project_exits", "Colleagues from the same project resigned", 12)
    rows = _run(kg, s, "leavers who worked on the same project", Q_PROJECT_EXITS,
                {"id": emp, "since": config.days_before(config.PEER_WINDOW_DAYS)})
    s.points = min(6 * len(rows), s.max_points)
    projects = sorted({n["props"]["name"] for r in rows for n in r["p"]["nodes"] if "Project" in n["labels"]})
    status = {n["props"]["name"]: n["props"]["status"] for r in rows for n in r["p"]["nodes"]
              if "Project" in n["labels"]}
    s.facts = {"leavers": _who(rows), "projects": projects, "project_status": status}
    if s.points:
        s.summary = (f"{', '.join(s.facts['leavers'])} left after working on the same project(s): "
                     + ", ".join(f"{p} [{status[p]}]" for p in projects) + ".")
    return s


def sig_negative_decisions(kg, emp):
    s = Signal("negative_decisions", "Adverse decisions by managers", 16)
    rows = _run(kg, s, "denied raises/promotions/transfers", Q_NEG_DECISIONS,
                {"id": emp, "types": config.NEGATIVE_DECISIONS,
                 "since": config.days_before(config.DECISION_WINDOW_DAYS)})
    s.points = min(8 * len(rows), s.max_points)
    decisions = [next(n for n in r["p"]["nodes"] if "Decision" in n["labels"])["props"] for r in rows]
    s.facts = {"decisions": [{"id": d["id"], "type": d["type"], "date": d["date"], "reason": d.get("reason")}
                             for d in decisions]}
    if s.points:
        s.summary = "; ".join(f"{d['type']} on {d['date']}" for d in decisions) + "."
    return s


def sig_negative_events(kg, emp):
    s = Signal("negative_events", "Negative events (complaints, reorgs, cancelled projects)", 12)
    rows = _run(kg, s, "recent negative events", Q_NEG_EVENTS,
                {"id": emp, "types": config.NEGATIVE_EVENTS, "since": config.days_before(config.EVENT_WINDOW_DAYS)})
    s.points = min(5 * len(rows), s.max_points)
    evs = [next(n for n in r["p"]["nodes"] if "Event" in n["labels"])["props"] for r in rows]
    s.facts = {"events": [{"id": e["id"], "type": e["type"], "date": e["date"]} for e in evs]}
    if s.points:
        s.summary = "; ".join(f"{e['type']} on {e['date']}" for e in evs) + "."
    return s


def sig_pay_gap(kg, emp):
    s = Signal("pay_gap", "Paid below same-role teammates", 10)
    rows = _run(kg, s, "same-role colleagues on the same team", Q_PAY_PEERS, {"id": emp}, evidence_ok=False)
    if len(rows) < 2:
        return s
    avg, mine = sum(r["band"] for r in rows) / len(rows), rows[0]["my_band"]
    gap = avg - mine
    s.points = 10 if gap >= 2 else 5 if gap >= 1 else 0
    s.facts = {"salary_band": mine, "same_role_team_avg_band": round(avg, 2), "gap": round(gap, 2)}
    if s.points:
        for r in rows:
            s.evidence.add_path(r["p"])
        s.summary = f"Salary band {mine} vs {avg:.1f} average for {len(rows)} same-role teammates."
    return s


def sig_stagnation(kg, emp, profile):
    s = Signal("stagnation", "No promotion in 24+ months", 6)
    rows = _run(kg, s, "most recent promotion", Q_LAST_PROMOTION, {"id": emp}, evidence_ok=False)
    last = rows[0]["date"] if rows else None
    tenure = profile["props"]["tenure_months"]
    s.facts = {"tenure_months": tenure, "last_promotion": last}
    if tenure >= 24 and (last is None or last < config.days_before(config.STAGNATION_DAYS)):
        s.points = 6
        if rows:
            s.evidence.add_path(rows[0]["p"])
        else:  # absence has no path: say so explicitly so the claim is still checkable
            s.evidence.add_node(profile)
            s.evidence.paths.append(f"NOT EXISTS ({profile['props']['name']})-[:EXPERIENCED]->(Event promotion)")
        s.summary = (f"Tenure {tenure} months; " + (f"last promotion {last}." if last else "no promotion on record."))
    return s


def sig_hot_skills(kg, emp):
    s = Signal("hot_skills", "Highly marketable skills (easy to poach)", 5)
    rows = _run(kg, s, "skills with high market demand", Q_HOT_SKILLS, {"id": emp}, evidence_ok=False)
    names = [next(n for n in r["p"]["nodes"] if "Skill" in n["labels"])["props"]["name"] for r in rows]
    s.points = 5 if len(rows) >= 3 else 2 if len(rows) == 2 else 0
    s.facts = {"high_demand_skills": names}
    if s.points:
        for r in rows:
            s.evidence.add_path(r["p"])
        s.summary = f"Holds {len(names)} high-demand skills: {', '.join(names)}."
    return s


def sig_mitigation(kg, emp):
    s = Signal("retention_actions", "Retention actions completed (mitigation)", 0)
    rows = _run(kg, s, "completed retention decisions", Q_MITIGATION,
                {"id": emp, "since": config.days_before(90)})
    s.points = -min(8 * len(rows), 16)
    if s.points:
        types = [next(n for n in r["p"]["nodes"] if "Decision" in n["labels"])["props"]["type"] for r in rows]
        s.facts = {"completed": types}
        s.summary = f"{len(rows)} retention action(s) completed in the last 90 days: {', '.join(types)}."
    return s


def key_person_impact(kg, emp) -> dict:
    """Impact if this person leaves: skills nobody else on their team has."""
    s = Signal("key_person", "Key-person exposure", 0)
    rows = _run(kg, s, "skills only this person holds on their team", Q_SOLE_SKILLS, {"id": emp})
    skills = [next(n for n in r["p"]["nodes"] if "Skill" in n["labels"])["props"]["name"] for r in rows]
    return {"sole_skills": skills, "level": "high" if len(skills) >= 2 else "medium" if skills else "normal",
            "evidence": s.evidence, "queries": s.queries}


# --------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------
def level_for(score: int) -> str:
    return "high" if score >= config.HIGH_RISK else "medium" if score >= config.MEDIUM_RISK else "low"


def resolve_employee(kg: KnowledgeGraph, ref: str) -> dict | None:
    """'E006' | 'Dana Ruiz' | 'dana' -> {'id','name'} (None if no/ambiguous match)."""
    ref = (ref or "").strip()
    rows = kg.read("MATCH (e:Employee) WHERE toLower(e.id) = toLower($r) OR toLower(e.name) = toLower($r) "
                   "RETURN e.id AS id, e.name AS name", {"r": ref})
    if not rows:
        rows = kg.read("MATCH (e:Employee) WHERE toLower(e.name) CONTAINS toLower($r) "
                       "RETURN e.id AS id, e.name AS name ORDER BY e.status, e.name", {"r": ref})
    return rows[0] if len(rows) == 1 or (rows and rows[0]["name"].lower() == ref.lower()) else (
        {"ambiguous": [r["name"] for r in rows]} if rows else None)


def _formula(signals, raw, score) -> str:
    text = " + ".join(f"{s.key}({s.points:+d})" for s in signals if s.points) or "no signals"
    return f"{text} = {raw}" + (f" -> capped at {score}" if raw != score else "")


def assess_employee(kg: KnowledgeGraph, emp_id: str) -> dict:
    """Full explainable risk assessment of one employee."""
    prof_rows = kg.read(Q_PROFILE, {"id": emp_id})
    if not prof_rows:
        return {"error": f"No employee with id {emp_id}"}
    pr = prof_rows[0]
    e = pr["e"]
    signals = [sig_low_review(kg, emp_id), sig_overloaded_manager(kg, emp_id), sig_peer_attrition(kg, emp_id),
               sig_shared_project_exits(kg, emp_id), sig_negative_decisions(kg, emp_id),
               sig_negative_events(kg, emp_id), sig_pay_gap(kg, emp_id), sig_stagnation(kg, emp_id, e),
               sig_hot_skills(kg, emp_id), sig_mitigation(kg, emp_id)]
    raw = sum(s.points for s in signals)
    score = max(0, min(100, raw))
    impact = key_person_impact(kg, emp_id)
    ev = Evidence()
    for s in signals:
        if s.points:
            ev.merge(s.evidence)
    ev.merge(impact["evidence"]) if impact["sole_skills"] else None
    p = e["props"]
    return {
        "employee": {"id": p["id"], "name": p["name"], "role": p["role"], "team": pr["team"],
                     "team_id": pr["team_id"], "manager": pr["manager"], "manager_id": pr["manager_id"],
                     "tenure_months": p["tenure_months"], "salary_band": p["salary_band"],
                     "location": p["location"], "status": p["status"]},
        "risk_score": score,
        "risk_level": level_for(score),
        "impact": {"sole_skills": impact["sole_skills"], "level": impact["level"],
                   "paths": impact["evidence"].paths, "evidence": impact["evidence"].to_dict()},
        "score_formula": _formula(signals, raw, score),
        "signals": [s.to_dict() for s in signals if s.points or s.key in ("low_review",)],
        "all_signal_points": {s.key: s.points for s in signals},
        "impact_queries": impact["queries"],
        "evidence": ev.to_dict(),
        "paths": ev.paths,
    }


def _team_lookup(kg, team_query):
    rows = kg.read("MATCH (t:Team) WHERE toLower(t.name) = toLower($q) OR toLower(t.name) CONTAINS toLower($q) "
                   "RETURN t.id AS id, t.name AS name", {"q": team_query.strip().removesuffix(" team")})
    return rows[0] if rows else None


Q_TEAM_MEMBERS = """
MATCH (e:Employee {status:'active'})-[:MEMBER_OF]->(t:Team {id:$tid})
RETURN e.id AS id ORDER BY e.id"""

Q_TEAM_LEAVERS = """
MATCH p=(t:Team {id:$tid})<-[:MEMBER_OF]-(x:Employee {status:'left'})-[:EXPERIENCED]->(ev:Event {type:'resignation'})
WHERE ev.date >= $since
RETURN p"""

Q_TEAM_MANAGERS = """
MATCH (e:Employee {status:'active'})-[:MEMBER_OF]->(t:Team {id:$tid}), (e)-[:REPORTS_TO]->(m:Manager)
WITH DISTINCT m
OPTIONAL MATCH (m)<-[:REPORTS_TO]-(r:Employee {status:'active'})
RETURN m.id AS id, m.name AS name, count(r) AS direct_reports ORDER BY direct_reports DESC"""


def assess_team(kg: KnowledgeGraph, team_query: str) -> dict:
    """Rank every active member of a team and surface the team-level drivers."""
    team = _team_lookup(kg, team_query)
    if not team:
        names = [r["name"] for r in kg.read("MATCH (t:Team) RETURN t.name AS name")]
        return {"error": f"No team matching '{team_query}'. Teams: {', '.join(names)}"}
    members = [r["id"] for r in kg.read(Q_TEAM_MEMBERS, {"tid": team["id"]})]
    assessments = sorted((assess_employee(kg, m) for m in members), key=lambda a: (-a["risk_score"], -len(a["impact"]["sole_skills"])))
    since = config.days_before(365)
    leavers_ev, leaver_rows = Evidence(), kg.read(Q_TEAM_LEAVERS, {"tid": team["id"], "since": since})
    for r in leaver_rows:
        leavers_ev.add_path(r["p"])
    managers = kg.read(Q_TEAM_MANAGERS, {"tid": team["id"]})

    driver_counts: dict[str, list] = {}
    for a in assessments:
        for s in a["signals"]:
            if s["points"] > 0:
                driver_counts.setdefault(s["key"], []).append(a["employee"]["name"])
    drivers = sorted(({"signal": k, "affects": len(v), "of": len(members), "employees": v}
                      for k, v in driver_counts.items()), key=lambda d: -d["affects"])

    ev = Evidence().merge(leavers_ev)
    for a in assessments[:3]:  # evidence graph for the people we'd act on first
        for n in a["evidence"]["nodes"]:
            ev.nodes.setdefault(n["id"], n)
        for e in a["evidence"]["edges"]:
            ev.edges.setdefault((e["from"], e["type"], e["to"]), e)
    n_left = len(leaver_rows)
    return {
        "team": team["name"], "headcount": len(members), "resignations_last_12m": n_left,
        "attrition_rate_12m": round(n_left / (len(members) + n_left), 3) if members else 0,
        "managers": managers,
        "average_risk": round(sum(a["risk_score"] for a in assessments) / max(1, len(assessments)), 1),
        "risk_distribution": {lv: sum(1 for a in assessments if a["risk_level"] == lv)
                              for lv in ("high", "medium", "low")},
        "systemic_drivers": drivers,
        "ranking": [{"id": a["employee"]["id"], "name": a["employee"]["name"], "role": a["employee"]["role"],
                     "risk_score": a["risk_score"], "risk_level": a["risk_level"],
                     "key_person_skills": a["impact"]["sole_skills"],
                     "top_signals": [{"key": s["key"], "points": s["points"], "summary": s["summary"]}
                                     for s in sorted(a["signals"], key=lambda s: -s["points"])[:3] if s["points"]]}
                    for a in assessments],
        "assessments": {a["employee"]["id"]: a for a in assessments},
        "queries": [{"name": "team members", "cypher": Q_TEAM_MEMBERS.strip(), "params": {"tid": team["id"]}},
                    {"name": "team resignations (last 12 months)", "cypher": Q_TEAM_LEAVERS.strip(),
                     "params": {"tid": team["id"], "since": since}},
                    {"name": "manager load for the team", "cypher": Q_TEAM_MANAGERS.strip(),
                     "params": {"tid": team["id"]}}],
        "evidence": ev.to_dict(), "paths": ev.paths,
    }
