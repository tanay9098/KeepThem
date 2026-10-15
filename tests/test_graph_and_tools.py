import json
from types import SimpleNamespace

import pytest

from keepthem import actions, agent, algorithms, risk, tools


# ---------------------------------------------------------------- seed / model
def test_seed_shape(kg):
    counts = {r["l"]: r["n"] for r in kg.read("MATCH (n) RETURN labels(n)[0] AS l, count(n) AS n")}
    assert 20 <= counts["Employee"] <= 40
    for label in ("Team", "Project", "Review", "Event", "Decision", "Skill"):
        assert counts[label] > 0
    rels = {r["t"] for r in kg.read("MATCH ()-[r]->() RETURN DISTINCT type(r) AS t")}
    assert {"REPORTS_TO", "MEMBER_OF", "WORKED_ON", "HAS_SKILL", "RECEIVED", "EXPERIENCED", "MADE",
            "AFFECTED", "PEER_OF"} <= rels


def test_managers_are_employees(kg):
    rows = kg.read("MATCH (m:Employee:Manager) RETURN count(m) AS n")
    assert rows[0]["n"] == 5


# ---------------------------------------------------------------- risk engine
def test_dana_is_top_platform_risk_and_high(kg):
    team = risk.assess_team(kg, "Platform")
    assert team["ranking"][0]["name"] == "Dana Ruiz"
    assert team["ranking"][0]["risk_level"] == "high"
    assert team["resignations_last_12m"] == 2


def test_healthy_teams_are_low(kg):
    for t in ("Payments", "Data"):
        assert risk.assess_team(kg, t)["risk_distribution"]["high"] == 0


def test_every_scored_signal_has_graph_evidence(kg):
    """The core explainability contract: no points without paths, and paths without Cypher."""
    for emp in ("E006", "E011", "E007", "E027"):
        a = risk.assess_employee(kg, emp)
        for s in a["signals"]:
            if s["points"] > 0:
                assert s["paths"], f"{emp}:{s['key']} scored without any supporting path"
                assert s["queries"] and all("MATCH" in q["cypher"] for q in s["queries"])
                assert s["evidence"]["nodes"]
                # edges are required unless the claim is an absence ("no promotion event exists")
                assert s["evidence"]["edges"] or all(p.startswith("NOT EXISTS") for p in s["paths"])


def test_multi_hop_root_cause_is_found(kg):
    """Dana -> Marcus <- (headcount_denied) <- Helen : a 3-hop upstream cause."""
    a = risk.assess_employee(kg, "E006")
    sig = next(s for s in a["signals"] if s["key"] == "overloaded_manager")
    assert any("headcount_denied" in p and "Helen Brooks" in p for p in sig["paths"])


def test_score_formula_adds_up(kg):
    a = risk.assess_employee(kg, "E006")
    assert a["risk_score"] == min(100, max(0, sum(a["all_signal_points"].values())))


def test_key_person_detected(kg):
    assert set(risk.assess_employee(kg, "E006")["impact"]["sole_skills"]) == {"Rust", "Distributed Systems"}


def test_resolve_employee(kg):
    assert risk.resolve_employee(kg, "dana")["id"] == "E006"
    assert risk.resolve_employee(kg, "E006")["name"] == "Dana Ruiz"
    assert risk.resolve_employee(kg, "nobody-here") is None


# ---------------------------------------------------------------- recommendations
def test_recommendations_are_concrete_and_traceable(kg):
    r = actions.recommend(kg, "E006")
    types = {x["action_type"] for x in r["recommendations"]}
    assert {"retention_stay_interview", "retention_manager_rebalance", "retention_promotion_review"} <= types
    reb = next(x for x in r["recommendations"] if x["action_type"] == "retention_manager_rebalance")
    assert reb["owner"] == "Helen Brooks" and "Elena Costa" in reb["detail"]   # chosen by graph query
    for x in r["recommendations"]:
        assert x["because"] and x["triggered_by"] and x["paths"]


def test_low_risk_employee_needs_no_action(kg):
    assert actions.recommend(kg, "E017")["recommendations"][0]["action_type"] == "none"


# ---------------------------------------------------------------- algorithms
def test_manager_load_flags_marcus(kg):
    r = algorithms.run(kg, "manager_load")
    assert r["results"][0]["manager"] == "Marcus Webb" and r["results"][0]["overloaded"]
    assert sum(1 for x in r["results"] if x["overloaded"]) == 1


@pytest.mark.parametrize("name,params", [
    ("peer_influence", {}), ("pagerank", {"relationship": "REPORTS_TO"}), ("betweenness", {}),
    ("attrition_clusters", {}), ("peer_contagion", {}),
    ("shortest_path", {"from_employee": "Dana Ruiz", "to_employee": "Nina Kowalski"})])
def test_algorithms_run(kg, name, params):
    r = algorithms.run(kg, name, params)
    assert "error" not in r and r["results"] and r["queries"]


def test_pagerank_ranks_director_first(kg):
    r = algorithms.run(kg, "pagerank", {"relationship": "REPORTS_TO", "top_n": 1})
    assert r["results"][0]["employee"] == "Helen Brooks"


def test_clusters_group_dana_with_leavers(kg):
    cl = algorithms.run(kg, "attrition_clusters")["results"]
    assert any("Priya Nair" in c["left"] and "Dana Ruiz" in c["still_here"] for c in cl)


def test_unknown_algorithm_is_an_error_not_a_crash(kg):
    assert "error" in algorithms.run(kg, "nope")


# ---------------------------------------------------------------- tools
def test_find_risk_paths(kg):
    r = tools.find_risk_paths("Dana Ruiz")
    assert any(l["leaver"] == "Priya Nair" and l["hops"] == 1 for l in r["links_to_leavers"])
    assert r["upstream_decisions"][0]["decision"] == "D-010"
    assert r["cascade_exposure"][0]["employee"]


def test_explain_decision_variants(kg):
    for q, key in [("Why is Dana Ruiz high risk?", "why"), ("what happened after D-001", "aftermath_events"),
                   ("why is the Platform team at risk", "systemic_drivers")]:
        r = tools.explain_decision(q)
        assert key in r and r["paths"] and r["evidence"]["nodes"] and r["queries"], q
    assert "error" in tools.explain_decision("something unrelated")


def test_query_graph_is_read_only(kg):
    ok = tools.query_graph("MATCH p=(e:Employee {id:'E006'})-[:REPORTS_TO]->(m) RETURN p")
    assert ok["paths"] == ["(Dana Ruiz)-[:REPORTS_TO]->(Marcus Webb)"]
    bad = tools.query_graph("MATCH (e:Employee {id:'E006'}) SET e.name='pwned'")
    assert "error" in bad
    assert kg.read("MATCH (e:Employee {id:'E006'}) RETURN e.name AS n")[0]["n"] == "Dana Ruiz"


def test_call_tool_never_raises(kg):
    assert "error" in tools.call_tool("nope")
    assert "error" in tools.call_tool("investigate_risk", {"bogus": 1})
    assert "error" in tools.call_tool("investigate_risk", {"employee_id": "zzz"})


def test_tool_schemas_are_valid_for_the_api():
    names = {t["name"] for t in tools.anthropic_tool_defs()}
    assert {"investigate_risk", "find_risk_paths", "run_graph_algorithm", "recommend_actions",
            "explain_decision", "log_event", "record_action"} <= names
    for t in tools.anthropic_tool_defs():
        assert t["input_schema"]["type"] == "object" and t["description"]
        json.dumps(t)


def test_compact_for_llm_drops_bulk_but_keeps_paths(kg):
    full = tools.investigate_risk(employee_id="E006")
    s = tools.compact_for_llm(full)
    assert '"evidence"' not in s and "REPORTS_TO" in s and len(s) < 24100


# ---------------------------------------------------------------- writes (re-seed first)
def test_writes_need_confirmation(fresh_kg):
    before = fresh_kg.read("MATCH (d:Decision) RETURN count(d) AS n")[0]["n"]
    r = tools.record_action("E006", "retention_stay_interview", "x")
    assert r["status"] == "preview"
    assert fresh_kg.read("MATCH (d:Decision) RETURN count(d) AS n")[0]["n"] == before
    assert tools.log_event("E006", "stay_interview", "x")["status"] == "preview"


def test_completed_action_lowers_risk_and_closes_the_loop(fresh_kg):
    base = risk.assess_employee(fresh_kg, "E006")["risk_score"]
    p = tools.record_action("E006", "retention_stay_interview", "Planned", confirmed=True)
    assert p["status"] == "written" and p["owner"] == "Marcus Webb"
    assert p["paths"] == ["(Marcus Webb)-[:MADE]->(Decision retention_stay_interview 2026-10-01 [proposed])-[:AFFECTED]->(Dana Ruiz)"]
    assert p["risk_after"]["score"] == base                       # proposed != done
    rec = next(x for x in actions.recommend(fresh_kg, "E006")["recommendations"] if x["action_type"] == "retention_stay_interview")
    assert rec["already_logged"] == "proposed"
    c = tools.record_action("E006", "retention_stay_interview", "Held 1:1", status="completed", confirmed=True)
    assert c["decision_id"] == p["decision_id"]                    # promoted, not duplicated
    assert c["risk_after"]["score"] < base                         # mitigation signal kicked in
    assert fresh_kg.read("MATCH (d:Decision {type:'retention_stay_interview'}) RETURN count(d) AS n")[0]["n"] == 1


def test_log_event_writes_a_path(fresh_kg):
    r = tools.log_event("E006", "stay_interview", "Held stay interview", confirmed=True)
    assert r["status"] == "written" and "stay_interview" in r["paths"][0]
    assert "error" in tools.log_event("E006", "not_a_type", "x", confirmed=True)


# ---------------------------------------------------------------- agents
def test_offline_agent_team_flow(kg):
    events = list(agent.OfflineAgent(kg).run("Investigate attrition risk on the Platform team"))
    called = [e["name"] for e in events if e["type"] == "tool_call"]
    assert called[0] == "investigate_risk" and "recommend_actions" in called
    assert "Dana Ruiz" in events[-1]["text"] and "[:REPORTS_TO]" in events[-1]["text"]


@pytest.mark.parametrize("q,tool", [
    ("Which managers are overloaded?", "run_graph_algorithm"),
    ("Why is Dana Ruiz high risk?", "explain_decision"),
    ("tell me about Victor", "investigate_risk"),
])
def test_offline_agent_routing(kg, q, tool):
    first = next(e for e in agent.OfflineAgent(kg).run(q) if e["type"] == "tool_call")
    assert first["name"] == tool


def test_offline_agent_write_flow(fresh_kg):
    events = list(agent.OfflineAgent(fresh_kg).run("Log a completed stay interview for Dana Ruiz"))
    assert [e["name"] for e in events if e["type"] == "tool_call"] == ["recommend_actions", "record_action", "investigate_risk"]
    assert "Written to the graph" in events[-1]["text"]


class _FakeClaude:
    """Scripted stand-in for anthropic.Anthropic: asks for two tools in parallel, then answers."""
    def __init__(self):
        self.calls, self.messages = [], SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append({**kw, "messages": list(kw["messages"])})   # snapshot, like the HTTP request
        if len(self.calls) == 1:
            return SimpleNamespace(stop_reason="tool_use", content=[
                SimpleNamespace(type="text", text="Let me look."),
                SimpleNamespace(type="tool_use", id="t1", name="investigate_risk", input={"employee_id": "E006"}),
                SimpleNamespace(type="tool_use", id="t2", name="run_graph_algorithm", input={"algorithm": "manager_load"})])
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="Dana is high risk.")])


def test_claude_agent_loop_mechanics(kg):
    fake = _FakeClaude()
    events = list(agent.ClaudeAgent(kg, client=fake).run("check Dana"))
    assert [e["type"] for e in events] == ["tool_call", "tool_result", "tool_call", "tool_result", "final"]
    assert events[-1]["text"] == "Dana is high risk."
    # both tool results were returned together in ONE user message, ids matched
    last_user = fake.calls[1]["messages"][-1]
    assert last_user["role"] == "user" and [r["tool_use_id"] for r in last_user["content"]] == ["t1", "t2"]
    assert "REPORTS_TO" in last_user["content"][0]["content"]
    assert fake.calls[0]["tools"] and "KeepThem" in fake.calls[0]["system"]
