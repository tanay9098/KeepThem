"""KeepThem demo UI.   streamlit run app.py

Chat with the agent; for every answer you can open
  * the reasoning trace (each tool call, its Cypher and parameters), and
  * the evidence graph (exactly the nodes/edges the answer rests on).
Recommended actions have a button that writes them back into FalkorDB (human-in-the-loop).
"""
import json

import streamlit as st

from keepthem import config, report, seed, tools
from keepthem.agent import ClaudeAgent, OfflineAgent, _has_credentials
from keepthem.db import get_kg

st.set_page_config(page_title="KeepThem", page_icon="🕸️", layout="wide")

KIND_COLOR = {"Employee": "#cfe8ff", "Manager": "#9ecbff", "Team": "#d9f2d0", "Project": "#ffe2b8",
              "Review": "#f6d6ff", "Event": "#ffd6d6", "Decision": "#ffeaa0", "Skill": "#e4e4e4"}
EXAMPLES = ["Investigate attrition risk on the Platform team",
            "What is Dana Ruiz's attrition risk?",
            "Which managers are overloaded?",
            "Show attrition clusters",
            "Why is Dana Ruiz high risk?",
            "Log a completed stay interview for Dana Ruiz"]


def dot(evidence: dict) -> str:
    """Graphviz DOT for an evidence {nodes, edges} dict."""
    esc = lambda s: str(s).replace('"', "'")
    lines = ['digraph G { rankdir=LR; bgcolor="transparent"; '
             'node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10, fontcolor="#111111", color="#555555"]; '
             'edge [fontname="Helvetica", fontsize=8, color="#777777", fontcolor="#888888"];']
    for n in evidence["nodes"]:
        lines.append(f'"{esc(n["id"])}" [label="{esc(n["name"])}", fillcolor="{KIND_COLOR.get(n["kind"], "#ffffff")}"];')
    for e in evidence["edges"]:
        lines.append(f'"{esc(e["from"])}" -> "{esc(e["to"])}" [label="{e["type"]}"];')
    return "\n".join(lines + ["}"])


def merge_evidence(events: list[dict]) -> dict:
    nodes, edges = {}, {}
    for ev in events:
        if ev["type"] == "tool_result":
            for n in ev["result"].get("evidence", {}).get("nodes", []):
                nodes[n["id"]] = n
            for e in ev["result"].get("evidence", {}).get("edges", []):
                edges[(e["from"], e["type"], e["to"])] = e
    return {"nodes": list(nodes.values()), "edges": list(edges.values())}


# ---------------------------------------------------------------------------------- state / sidebar
@st.cache_resource(show_spinner="Connecting to FalkorDB…")
def connect():
    kg = get_kg()
    seed.ensure_seeded(kg)
    return kg


kg = connect()
st.session_state.setdefault("turns", [])

with st.sidebar:
    st.title("🕸️ KeepThem")
    st.caption("Graph-driven attrition-risk agent · FalkorDB")
    st.success(f"Graph: {kg.backend}", icon="🟢")
    counts = {r["l"]: r["n"] for r in kg.read("MATCH (n) RETURN labels(n)[0] AS l, count(n) AS n")}
    st.caption(" · ".join(f"{v} {k}" for k, v in counts.items()))
    choices = ["Offline agent (no API key)"] + (["Claude (tool-calling)"] if _has_credentials() else [])
    mode = st.radio("Agent", choices, index=len(choices) - 1)
    if not _has_credentials():
        st.caption("Set `ANTHROPIC_API_KEY` to use Claude. The offline agent calls the same tools.")
    if st.button("♻️ Reset demo data", width="stretch"):
        seed.seed(kg, verbose=False)
        st.session_state.turns = []
        st.rerun()
    st.divider()
    st.markdown("**Try:**")
    for ex in EXAMPLES:
        if st.button(ex, width="stretch", key=f"ex-{ex}"):
            st.session_state["pending"] = ex

if st.session_state.get("agent_mode") != mode:      # switching agent resets the conversation memory
    st.session_state.agent_mode = mode
    st.session_state.agent = OfflineAgent(kg) if mode.startswith("Offline") else ClaudeAgent(kg)
agent = st.session_state.agent


# ---------------------------------------------------------------------------------- rendering
def render_turn(turn: dict, idx: int) -> None:
    with st.chat_message(turn["role"]):
        st.markdown(turn["text"])
        if turn["role"] != "assistant" or not turn.get("events"):
            return
        calls = [e for e in turn["events"] if e["type"] == "tool_result"]
        with st.expander(f"🔍 Reasoning trace — {len(calls)} tool call(s) and the Cypher behind them"):
            for i, e in enumerate(calls, 1):
                st.markdown(f"**{i}. `{e['name']}`** `{json.dumps(e['input'], ensure_ascii=False)}`")
                st.caption(e["result"].get("summary") or e["result"].get("error") or "")
                for q in e["result"].get("queries", [])[:12]:
                    st.markdown(f"_{q['name']}_")
                    st.code(q["cypher"] + (f"\n// params: {json.dumps(q['params'], default=str)[:300]}" if q["params"] else ""),
                            language="cypher")
        ev = merge_evidence(turn["events"])
        if ev["nodes"]:
            with st.expander(f"🕸️ Evidence graph — {len(ev['nodes'])} nodes, {len(ev['edges'])} edges"):
                st.graphviz_chart(dot(ev), width="stretch")
        for e in calls:   # human-in-the-loop: write a recommendation back to the graph
            if e["name"] == "recommend_actions" and "recommendations" in e["result"]:
                emp = e["result"]["employee"]
                with st.expander(f"✅ Log recommended actions for {emp['name']} to the graph"):
                    for r in e["result"]["recommendations"]:
                        if r["action_type"] == "none":
                            continue
                        c1, c2, c3 = st.columns([6, 1.3, 1.3])
                        c1.markdown(f"**{r['rank']}. {r['action']}**  \n{r['owner']} · {r['urgency']}")
                        for status, col in (("proposed", c2), ("completed", c3)):
                            if col.button(status.capitalize(), key=f"log-{idx}-{emp['id']}-{r['action_type']}-{status}"):
                                res = tools.record_action(emp["id"], r["action_type"], r["action"], status, True, kg=kg)
                                st.success(f"Written: {res.get('decision_id')} — risk now {res['risk_after']['score']}")
                                st.code(res["paths"][0], language="text")


st.title("KeepThem")
st.caption("Ask who is likely to leave, why (as graph paths), and what to do about it.")
for i, t in enumerate(st.session_state.turns):
    render_turn(t, i)

prompt = st.chat_input("e.g. Investigate attrition risk on the Platform team") or st.session_state.pop("pending", None)
if prompt:
    st.session_state.turns.append({"role": "user", "text": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    events, text = [], ""
    with st.chat_message("assistant"):
        with st.status("Querying the graph…", expanded=True) as status:
            for ev in agent.run(prompt):
                if ev["type"] == "tool_call":
                    st.write(f"→ `{ev['name']}` {json.dumps(ev['input'], ensure_ascii=False)}")
                elif ev["type"] == "tool_result":
                    st.caption(ev["result"].get("summary") or ev["result"].get("error") or "done")
                elif ev["type"] == "final":
                    text = ev["text"]
                events.append(ev)
            status.update(label="Done", state="complete", expanded=False)
    st.session_state.turns.append({"role": "assistant", "text": text, "events": events})
    st.rerun()
