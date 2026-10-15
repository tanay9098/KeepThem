"""Command line interface.

    python -m keepthem.cli seed
    python -m keepthem.cli ask "Investigate attrition risk on the Platform team"
    python -m keepthem.cli chat                       # interactive (Claude if ANTHROPIC_API_KEY, else offline)
    python -m keepthem.cli tool investigate_risk '{"employee_id": "Dana Ruiz"}'
    python -m keepthem.cli demo                       # the scripted 7-step demo flow
    python -m keepthem.cli cypher "MATCH (e:Employee) RETURN count(e) AS n"
Add --offline to force the deterministic agent, --cypher to print the Cypher behind each tool call.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import report, seed, tools
from .agent import make_agent
from .db import get_kg


def _print_events(agent, prompt: str, show_cypher: bool, show_raw: bool = False) -> None:
    for ev in agent.run(prompt):
        if ev["type"] == "tool_call":
            print(f"\n\033[36m→ tool: {ev['name']}({json.dumps(ev['input'], ensure_ascii=False)})\033[0m")
        elif ev["type"] == "tool_result":
            r = ev["result"]
            print(f"\033[2m  ← {r.get('summary') or r.get('error') or 'ok'}\033[0m")
            if show_cypher:
                for q in r.get("queries", []):
                    print(f"\033[2m  Cypher [{q['name']}]\n    " + q["cypher"].replace("\n", "\n    ")
                          + f"\n    params={json.dumps(q['params'], default=str)[:200]}\033[0m")
        elif ev["type"] == "final":
            print("\n" + ev["text"])


DEMO_STEPS = [
    ("1. Investigate the team", "Investigate attrition risk on the Platform team"),
    ("2. Which managers are overloaded?", "Which managers are overloaded?"),
    ("3. Which peer groups are at risk of attrition contagion?", "Show attrition clusters"),
    ("4. Explain the top risk", "Why is Dana Ruiz high risk?"),
    ("5. Write an action back to the graph", "Log a completed stay interview for Dana Ruiz"),
]


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="keepthem", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="use the deterministic agent (no API key needed)")
    ap.add_argument("--cypher", action="store_true", help="print the Cypher behind every tool call")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed", help="(re)build the graph")
    a = sub.add_parser("ask", help="ask one question"); a.add_argument("question")
    sub.add_parser("chat", help="interactive chat")
    sub.add_parser("demo", help="scripted demo flow (offline agent)")
    sub.add_parser("tools", help="list tools")
    t = sub.add_parser("tool", help="call a tool directly"); t.add_argument("name"); t.add_argument("args", nargs="?", default="{}")
    c = sub.add_parser("cypher", help="run read-only Cypher"); c.add_argument("query")
    args = ap.parse_args(argv)

    if args.cmd == "seed":
        seed.seed()
        return
    kg = get_kg()
    seed.ensure_seeded(kg)
    print(f"[graph: {kg.backend}]", file=sys.stderr)

    if args.cmd == "tools":
        for tl in tools.TOOLS:
            print(f"- {tl['name']}: {tl['description'][:140]}")
    elif args.cmd == "tool":
        res = tools.call_tool(args.name, json.loads(args.args), kg=kg)
        print(report.render(res))
        if args.cypher:
            for q in res.get("queries", []):
                print(f"\n-- {q['name']}\n{q['cypher']}\n-- params: {q['params']}")
    elif args.cmd == "cypher":
        res = tools.query_graph(args.query, kg=kg)
        print(json.dumps(res.get("rows", res), indent=2, default=str))
        for p in res.get("paths", [])[:10]:
            print("path:", p)
    elif args.cmd == "ask":
        _print_events(make_agent(kg, args.offline or None), args.question, args.cypher)
    elif args.cmd == "demo":
        agent = make_agent(kg, offline=True)
        for title, q in DEMO_STEPS:
            print(f"\n{'=' * 100}\n{title}\n> {q}\n{'=' * 100}")
            _print_events(agent, q, args.cypher)
    elif args.cmd == "chat":
        agent = make_agent(kg, args.offline or None)
        print(f"KeepThem ({type(agent).__name__}). Ask about a team or employee; Ctrl-D to quit.")
        while True:
            try:
                q = input("\nyou> ").strip()
            except EOFError:
                break
            if q:
                _print_events(agent, q, args.cypher)


if __name__ == "__main__":
    main()
