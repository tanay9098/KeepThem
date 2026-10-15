"""Agents that drive the tools.

* `ClaudeAgent`  - a plain tool-calling loop on the Anthropic Messages API (no framework).
                   Claude decides which tools to call; every call hits FalkorDB.
* `OfflineAgent` - a deterministic planner with the same interface and the same tools, so the
                   demo (and the tests) work without an API key.

Both are generators of events, which the CLI and the Streamlit app render:

    {"type": "tool_call",   "name", "input"}
    {"type": "tool_result", "name", "input", "result"}     # the full, un-trimmed result
    {"type": "final",       "text"}
"""
from __future__ import annotations

import os
import re
from typing import Iterator

from . import config, report, tools
from .db import KnowledgeGraph, get_kg
from .risk import resolve_employee

SYSTEM_PROMPT = f"""You are KeepThem, an analyst agent that investigates employee-attrition risk using a FalkorDB \
knowledge graph of a (synthetic) company: employees, teams, managers, projects, reviews, events and decisions. \
Today in the company's timeline is {config.AS_OF}.

HOW TO WORK
1. Investigate with the tools before answering - never guess. Start broad (investigate_risk on a team, or \
run_graph_algorithm), then go deep on the people who matter (investigate_risk / find_risk_paths on an \
employee), then call recommend_actions.
2. Every claim must be traceable. Quote the graph paths the tools return (they look like \
`(Dana Ruiz)-[:REPORTS_TO]->(Marcus Webb)`) next to the claim they support. Do not invent employees, scores, \
dates or paths.
3. Prefer multi-hop explanations: connect the individual's signals to systemic causes (e.g. an overloaded \
manager whose backfill was denied by a director) rather than listing facts.
4. Separate individual drivers from systemic drivers. If most of a team shares a driver, say the fix is \
organisational.
5. Recommendations must be concrete: what, who owns it, by when, and which graph evidence justifies it.
6. If the user asks "why" or "show the query", use explain_decision and show the Cypher.
7. WRITES (log_event, record_action): only when the user asks for it. First call with confirmed=false to preview; \
set confirmed=true only after the user agrees (or explicitly told you to log it). After writing, re-check the \
employee's risk to show the effect.
8. Ethics: this supports retention (helping people), not discipline. Never suggest punishing, monitoring or \
managing out someone because they are at risk. The data is synthetic.

FORMAT: start with a 2-3 sentence bottom line. Then findings (with paths), then a numbered action list \
(owner, urgency). Keep it tight. Finish with what you'd investigate next, if anything."""


def _has_credentials() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


class ClaudeAgent:
    """Manual tool-use loop: call Claude -> run the tools it asks for -> feed results back -> repeat."""

    def __init__(self, kg: KnowledgeGraph | None = None, model: str | None = None, client=None, max_steps: int = 12):
        import anthropic

        self.kg = kg or get_kg()
        self.model = model or config.MODEL
        self.client = client or anthropic.Anthropic()
        self.max_steps = max_steps
        self.messages: list[dict] = []   # persists across turns -> multi-turn chat

    def run(self, user_message: str) -> Iterator[dict]:
        self.messages.append({"role": "user", "content": user_message})
        for _ in range(self.max_steps):
            resp = self.client.messages.create(
                model=self.model, max_tokens=16000, system=SYSTEM_PROMPT,
                tools=tools.anthropic_tool_defs(), messages=self.messages)
            self.messages.append({"role": "assistant", "content": resp.content})

            if resp.stop_reason == "refusal":
                yield {"type": "final", "text": "The model declined this request."}
                return
            calls = [b for b in resp.content if b.type == "tool_use"]
            text = "".join(b.text for b in resp.content if b.type == "text")
            if resp.stop_reason != "tool_use" or not calls:
                suffix = "\n\n_(response truncated: max_tokens)_" if resp.stop_reason == "max_tokens" else ""
                yield {"type": "final", "text": text + suffix}
                return

            results = []
            for c in calls:   # all results go back in ONE user message
                yield {"type": "tool_call", "name": c.name, "input": c.input}
                result = tools.call_tool(c.name, c.input, kg=self.kg)
                yield {"type": "tool_result", "name": c.name, "input": c.input, "result": result}
                results.append({"type": "tool_result", "tool_use_id": c.id,
                                "content": tools.compact_for_llm(result), "is_error": "error" in result})
            self.messages.append({"role": "user", "content": results})
        yield {"type": "final", "text": "Stopped: reached the maximum number of tool-use steps."}


# ----------------------------------------------------------------------------------------------
class OfflineAgent:
    """Deterministic planner: parse intent -> call the same tools -> render a report."""

    ACTION_WORDS = {"stay interview": "retention_stay_interview", "rebalance": "retention_manager_rebalance",
                    "span of control": "retention_manager_rebalance", "career": "retention_career_plan",
                    "promotion": "retention_promotion_review", "comp": "retention_comp_review",
                    "pay": "retention_comp_review", "rotation": "retention_project_rotation",
                    "complaint": "retention_complaint_followup", "knowledge": "retention_knowledge_transfer"}
    ALGO_WORDS = [("manager load", "manager_load"), ("overload", "manager_load"), ("span", "manager_load"),
                  ("pagerank", "pagerank"), ("page rank", "pagerank"), ("betweenness", "betweenness"),
                  ("bridge", "betweenness"), ("cluster", "attrition_clusters"), ("communit", "attrition_clusters"),
                  ("contagion", "peer_contagion"), ("influence", "peer_influence"), ("central", "manager_load")]

    def __init__(self, kg: KnowledgeGraph | None = None):
        self.kg = kg or get_kg()

    # -- helpers -----------------------------------------------------------------------------
    def _call(self, name: str, args: dict) -> Iterator[dict]:
        yield {"type": "tool_call", "name": name, "input": args}
        result = tools.call_tool(name, args, kg=self.kg)
        yield {"type": "tool_result", "name": name, "input": args, "result": result}
        self._last = result

    def _find_employee(self, text: str) -> str | None:
        t = text.lower()
        for r in self.kg.read("MATCH (e:Employee) RETURN e.id AS id, e.name AS name"):
            if re.search(rf"\b{r['id'].lower()}\b", t) or r["name"].lower() in t:
                return r["id"]
        firsts: dict[str, list] = {}
        for r in self.kg.read("MATCH (e:Employee) RETURN e.id AS id, e.name AS name"):
            firsts.setdefault(r["name"].split()[0].lower(), []).append(r["id"])
        for w in re.findall(r"[a-z]+", t):
            if w in firsts and len(firsts[w]) == 1 and w not in ("data",):
                return firsts[w][0]
        return None

    def _find_team(self, text: str) -> str | None:
        t = text.lower()
        for r in self.kg.read("MATCH (t:Team) RETURN t.name AS name"):
            if re.search(rf"\b{re.escape(r['name'].lower())}\b", t):
                return r["name"]
        return None

    # -- main ----------------------------------------------------------------------------------
    def run(self, user_message: str) -> Iterator[dict]:
        p = user_message.lower()
        emp, team = self._find_employee(user_message), self._find_team(user_message)
        parts: list[str] = []

        def emit(result):
            parts.append(report.render(result))

        if emp and re.search(r"\b(log|record|save|mark|write)\b", p):
            yield from self._call("recommend_actions", {"employee_id": emp})
            recs = self._last["recommendations"]
            wanted = next((v for k, v in self.ACTION_WORDS.items() if k in p), None)
            rec = next((r for r in recs if r["action_type"] == wanted), recs[0])
            status = "completed" if re.search(r"complet|done|held|finished", p) else "proposed"
            yield from self._call("record_action", {
                "employee_id": emp, "action_type": rec["action_type"], "description": rec["action"],
                "status": status, "confirmed": True})   # the user explicitly asked for the write
            emit(self._last)
            yield from self._call("investigate_risk", {"employee_id": emp})
            parts.append("**Re-assessed after the write:**\n\n" + report.render(self._last))
        elif emp and re.search(r"\b(why|explain|how come|evidence)\b", p):
            yield from self._call("explain_decision", {"query": user_message, "employee_id": emp})
            emit(self._last)
        elif re.search(r"\b(why|explain)\b", p) and re.search(r"\bD-\d{3}\b", user_message, re.I):
            yield from self._call("explain_decision", {"query": user_message})
            emit(self._last)
        elif emp:
            for name, args in [("investigate_risk", {"employee_id": emp}), ("find_risk_paths", {"employee_id": emp}),
                               ("recommend_actions", {"employee_id": emp})]:
                yield from self._call(name, args)
                emit(self._last)
        elif any(w in p for w, _ in self.ALGO_WORDS):
            algo = next(a for w, a in self.ALGO_WORDS if w in p)
            yield from self._call("run_graph_algorithm", {"algorithm": algo, "params": {}})
            emit(self._last)
        elif team:
            yield from self._call("investigate_risk", {"team_name": team})
            emit(self._last)
            top = self._last["ranking"][0]
            if top["risk_level"] != "low":
                parts.append(f"---\n#### Deep dive on the highest-risk person: {top['name']}")
                for name, args in [("find_risk_paths", {"employee_id": top["id"]}),
                                   ("recommend_actions", {"employee_id": top["id"]})]:
                    yield from self._call(name, args)
                    emit(self._last)
        else:
            parts.append("I can: investigate a **team** (“Investigate attrition risk on the Platform team”), an "
                         "**employee** (“What is Dana Ruiz's attrition risk?”), run **algorithms** (“Which managers "
                         "are overloaded?”, “attrition clusters”, “pagerank”), explain (“Why is Dana high risk?”), "
                         "or **write** (“Log a stay interview for Dana as completed”).")
        yield {"type": "final", "text": "\n\n".join(parts)}


def make_agent(kg: KnowledgeGraph | None = None, offline: bool | None = None):
    """Claude if credentials are available (unless offline=True), otherwise the offline agent."""
    if offline is None:
        offline = not _has_credentials()
    return OfflineAgent(kg) if offline else ClaudeAgent(kg)
