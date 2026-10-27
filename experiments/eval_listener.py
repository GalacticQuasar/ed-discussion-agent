"""Measure how well listener rules separate matching posts, to tune thresholds.

Usage:
    uv run experiments/eval_listener.py collect COURSE_ID RULES.json OUT.jsonl [--since DATE] [--limit N]
    uv run experiments/eval_listener.py report OUT.jsonl

RULES.json is a list of {"name", "instructions", "staff_only"} objects.
`collect` scores a course's threads (newest first) against every rule with
the listener's judge and writes one row per (thread, rule) with "label": null.
Then set "label": true on the rows that should notify; `report` treats
unlabelled rows as false (true matches are rare, so you only need to find
those) and prints precision and recall per threshold for each rule.

Only thread posts are scored, not replies. ED_LISTENER_MODEL picks the model.
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime

import dotenv

from ed_discussion_mcp import server
from ed_discussion_mcp.listener import judge

dotenv.load_dotenv()

THRESHOLDS = [0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95]


def collect(course_id, rules_path, out_path, since=None, limit=200):
    with open(rules_path) as f:
        rules = json.load(f)
    since_dt = server._parse_since(since)
    started = time.time()
    rows = 0
    with open(out_path, "w") as out:
        threads = 0
        for thread in server._iter_threads(course_id):
            if since_dt and datetime.fromisoformat(thread["created_at"]) < since_dt and not thread["is_pinned"]:
                break
            if threads >= limit:
                break
            threads += 1
            summary = server._summary(thread)
            post = {
                "title": thread["title"],
                "category": thread["category"],
                "author_role": summary["author_role"],
                "body": server._text(thread),
            }
            for rule in rules:
                if rule.get("staff_only") and not summary["is_staff"]:
                    continue
                score = judge.score(post, [(0, rule["instructions"])])[0]
                out.write(json.dumps({
                    "rule": rule["name"],
                    "score": round(score, 3),
                    "label": None,
                    "title": summary["title"],
                    "author_role": summary["author_role"],
                    "created_at": summary["created_at"],
                    "url": summary["url"],
                }) + "\n")
                rows += 1
            print(f"\r{threads} threads, {rows} rows", end="", file=sys.stderr)
    print(f"\nWrote {out_path} in {time.time() - started:.0f}s with {judge.model()}", file=sys.stderr)


def report(path):
    by_rule = {}
    with open(path) as f:
        for line in f:
            row = json.loads(line)
            by_rule.setdefault(row["rule"], []).append((row["score"], bool(row["label"])))
    for rule, rows in by_rule.items():
        positives = sum(label for _, label in rows)
        print(f"\n{rule}: {len(rows)} rows, {positives} labelled true")
        if not positives:
            print("  (label some rows true to measure recall)")
        print(f"  {'threshold':>9} {'notify':>6} {'TP':>4} {'FP':>4} {'FN':>4} {'precision':>9} {'recall':>6}")
        for threshold in THRESHOLDS:
            tp = sum(s >= threshold and label for s, label in rows)
            fp = sum(s >= threshold and not label for s, label in rows)
            fn = positives - tp
            precision = f"{tp / (tp + fp):.2f}" if tp + fp else "-"
            recall = f"{tp / positives:.2f}" if positives else "-"
            print(f"  {threshold:>9.2f} {tp + fp:>6} {tp:>4} {fp:>4} {fn:>4} {precision:>9} {recall:>6}")
        missed = sorted((s for s, label in rows if label))[:3]
        if missed:
            print(f"  lowest-scoring true rows: {', '.join(f'{s:.2f}' for s in missed)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("collect")
    c.add_argument("course_id", type=int)
    c.add_argument("rules")
    c.add_argument("out")
    c.add_argument("--since")
    c.add_argument("--limit", type=int, default=200, help="max threads to score (default 200)")
    r = sub.add_parser("report")
    r.add_argument("path")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    if args.command == "collect":
        collect(args.course_id, args.rules, args.out, args.since, args.limit)
    else:
        report(args.path)


if __name__ == "__main__":
    main()
