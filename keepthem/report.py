"""Markdown rendering of tool results (used by the offline agent, the CLI and the UI)."""
from __future__ import annotations

BAR = {"high": "🔴", "medium": "🟠", "low": "🟢"}


def _bar(score: int) -> str:
    n = round(score / 10)
    return "█" * n + "░" * (10 - n)


def employee(a: dict, max_paths: int = 3) -> str:
    e = a["employee"]
    out = [f"### {BAR[a['risk_level']]} {e['name']} — risk **{a['risk_score']}/100** ({a['risk_level']})",
           f"{e['role']} · {e['team']} · reports to {e['manager'] or '—'} · {e['tenure_months']} months · band {e['salary_band']}",
           f"`{_bar(a['risk_score'])}`  score = {a['score_formula']}"]
    if a["impact"]["sole_skills"]:
        out.append(f"⚠️ **Key person:** only person on {e['team']} with {', '.join(a['impact']['sole_skills'])}.")
    out.append("\n**Why (each line is backed by graph paths):**")
    for s in sorted(a["signals"], key=lambda s: -s["points"]):
        if not s["points"]:
            continue
        out.append(f"- **{s['title']}** (+{s['points']}): {s['summary']}")
        for p in s["paths"][:max_paths]:
            out.append(f"    - `{p}`")
        if len(s["paths"]) > max_paths:
            out.append(f"    - … {len(s['paths']) - max_paths} more path(s)")
    return "\n".join(out)


def team(t: dict) -> str:
    d = t["risk_distribution"]
    out = [f"## {t['team']} team — average risk {t['average_risk']}",
           f"{t['headcount']} active · **{t['resignations_last_12m']} resignation(s)** in 12 months "
           f"({t['attrition_rate_12m']:.0%} attrition) · 🔴 {d['high']} high · 🟠 {d['medium']} medium · 🟢 {d['low']} low"]
    if t["managers"]:
        out.append("Manager load: " + ", ".join(f"{m['name']} ({m['direct_reports']} reports)" for m in t["managers"]))
    if t["systemic_drivers"]:
        out.append("\n**Systemic drivers** (signals hitting many people → fix the system, not the individuals):")
        for dr in t["systemic_drivers"][:4]:
            out.append(f"- `{dr['signal']}` affects {dr['affects']}/{dr['of']}")
    out.append("\n| # | Employee | Risk | Level | Key-person skills | Top signals |\n|---|---|---|---|---|---|")
    for i, r in enumerate(t["ranking"], 1):
        sigs = "; ".join(f"{s['key']} +{s['points']}" for s in r["top_signals"]) or "—"
        out.append(f"| {i} | {r['name']} | {r['risk_score']} | {BAR[r['risk_level']]} {r['risk_level']} | "
                   f"{', '.join(r['key_person_skills']) or '—'} | {sigs} |")
    if t.get("paths"):
        out.append("\n**Resignations in this team (evidence):**")
        out += [f"- `{p}`" for p in t["paths"][:4]]
    return "\n".join(out)


def recommendations(r: dict, max_paths: int = 2) -> str:
    e = r["employee"]
    out = [f"### Recommended actions for {e['name']} (risk {r['risk_score']})"]
    for x in r["recommendations"]:
        done = f" _(already {x['already_logged']})_" if x.get("already_logged") else ""
        out.append(f"{x['rank']}. **{x['action']}** — owner: {x['owner']} · {x['urgency']}{done}")
        out.append(f"    - {x['detail']}" if x["detail"] else "")
        out.append(f"    - _Because:_ {x['because']}")
        for p in x["paths"][:max_paths]:
            out.append(f"    - `{p}`")
    return "\n".join(l for l in out if l)


def risk_paths(r: dict) -> str:
    out = [f"### Path analysis: {r['employee']['name']}", r["summary"], "\n**Links to colleagues who resigned:**"]
    by_leaver: dict[str, list] = {}
    for l in r["links_to_leavers"]:
        by_leaver.setdefault(l["leaver"], []).append(l)
    for leaver, ls in by_leaver.items():
        out.append(f"- **{leaver}** — {len(ls)} independent connection(s):")
        out += [f"    - {l['relationship']} ({l['hops']} hop): `{l['path']}`" for l in ls]
    if r["upstream_decisions"]:
        out.append("\n**Upstream decisions behind the manager's load:**")
        out += [f"- {u['decision']} {u['type']} — “{u['reason']}”: `{u['path']}`" for u in r["upstream_decisions"]]
    if r["cascade_exposure"]:
        out.append("\n**Cascade exposure (if they leave, who is next?):**")
        out += [f"- {c['employee']} — {c['hops']} hop(s), risk {c['risk_score']} ({c['risk_level']})" for c in r["cascade_exposure"][:5]]
    return "\n".join(out)


def algorithm(r: dict) -> str:
    out = [f"### Algorithm: `{r['algorithm']}`", r["description"], f"_{r['how_to_read']}_", ""]
    for row in r["results"][:10]:
        out.append("- " + ", ".join(f"{k}: {v}" for k, v in row.items()))
    if r["paths"]:
        out.append("\n**Paths:**")
        out += [f"- `{p}`" for p in r["paths"][:5]]
    return "\n".join(out)


def explain(r: dict) -> str:
    out = [f"### Why: {r['subject']}", r["explanation"]]
    for w in r.get("why", []):
        out.append(f"- **{w['title']}** (+{w['points']}): {w['because']}")
        out += [f"    - `{p}`" for p in w["supporting_paths"][:2]]
    if r.get("why_these_actions"):
        out.append("\n**Why these actions:**")
        out += [f"- {a['action']} ← {', '.join(a['triggered_by'])}" for a in r["why_these_actions"][:5]]
    elif r.get("paths"):
        out += [f"- `{p}`" for p in r["paths"][:5]]
    return "\n".join(out)


def write(r: dict) -> str:
    if r.get("status") == "preview":
        return f"📝 **Preview only (nothing written):** `{r['would_write']}`"
    extra = f" Risk is now **{r['risk_after']['score']}** ({r['risk_after']['level']})." if "risk_after" in r else ""
    return f"✅ **Written to the graph** for {r['employee']}: `{r.get('decision_id') or r.get('event_id')}`.{extra}\n" + \
           "\n".join(f"- `{p}`" for p in r["paths"])


def render(result: dict) -> str:
    """Pick the right renderer for a tool result."""
    if "error" in result:
        return f"⚠️ {result['error']}"
    tool = result.get("tool")
    if tool == "investigate_risk":
        return team(result) if "ranking" in result else employee(result)
    if tool == "find_risk_paths":
        return risk_paths(result)
    if tool == "run_graph_algorithm":
        return algorithm(result)
    if tool == "recommend_actions":
        return recommendations(result)
    if tool == "explain_decision":
        return explain(result)
    if tool in ("log_event", "record_action"):
        return write(result)
    return f"```\n{result}\n```"
