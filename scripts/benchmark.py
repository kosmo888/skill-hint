#!/usr/bin/env python3
"""Reproduce the numbers quoted in README.md.

    python scripts/benchmark.py --skills ~/.hermes/skills --skills ~/.hermes/hermes-agent/skills

Queries whose expected skill is not in the library are skipped and counted, so the
script stays meaningful on someone else's skill set. It reports build time, query
latency percentiles, top-1/top-3 accuracy, and the share of unrelated chatter that
correctly produced no hint at all.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skill_matcher import SkillIndex  # noqa: E402

# (query, {acceptable skill names}) — deliberately colloquial, never phrased like a description.
CASES: list[tuple[str, set[str]]] = [
    ("how do I take a screenshot of the desktop", {"screenshot"}),
    ("add a row to the excel spreadsheet", {"xlsx", "Excel / XLSX", "microsoft-office"}),
    ("check my calendar for tomorrow", {"google-workspace", "dingtalk-calendar"}),
    ("send this file to telegram", {"telegram-file-send", "telegram-delivery"}),
    ("summarize this pdf document", {"pdf"}),
    ("search my github issues for this bug", {"github-gh-cli-first", "github-pr-workflow", "github"}),
    ("deploy the service with docker on the remote host", {"docker-host-management", "host-connection"}),
    ("what ssh port do I use for the jump host", {"ssh-connection-workflows"}),
    ("generate a chart from this csv", {"smart-charts", "echarts-chart-master"}),
    ("fill in this chinese application form with my info", {"personal-info-formfilling"}),
    ("audit this server's security and open ports", {"multi-server-security-audit"}),
    ("帮我查一下钉钉群消息", {"dingtalk-chat"}),
    ("把这份周报做成ppt", {"ppt-master", "ppt-craft-master"}),
    ("画一张流程图", {"processon-diagram-generator"}),
    ("登记一条违章记录", {"ehs-violation-registration"}),
    ("生成一份施工方案", {"construction-plan-documentation"}),
    ("爬取这个公众号文章", {"wechat-article-spider"}),
]
CHATTER = ["good morning, how are you", "thanks", "the meeting is at 3pm in room 4"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skills", action="append", required=True, help="skill root, repeatable")
    ap.add_argument("--top-n", type=int, default=3)
    ap.add_argument("--min-score", type=float, default=3.0)
    ap.add_argument("--repeat", type=int, default=300, help="timing repetitions per query")
    args = ap.parse_args()

    t0 = time.perf_counter()
    index = SkillIndex(args.skills).build()
    build_s = time.perf_counter() - t0
    print(f"skills indexed : {len(index.docs)}")
    print(f"build time     : {build_s:.2f} s")

    lat: list[float] = []
    top1 = top3 = evaluated = 0
    for query, expected in CASES:
        if not expected & set(index.by_name):
            print(f"  skip  {query!r} (no expected skill in this library)")
            continue
        evaluated += 1
        for _ in range(args.repeat):
            t = time.perf_counter()
            hits = index.match(query, top_n=args.top_n, min_score=args.min_score)
            lat.append(time.perf_counter() - t)
        names = [h[1] for h in hits]
        top1 += bool(names and names[0] in expected)
        top3 += bool(expected & set(names))
        mark = "ok " if names and names[0] in expected else ("t3 " if expected & set(names) else "MISS")
        print(f"  {mark} {query[:52]:52s} -> {', '.join(names) or '(no hint)'}")

    if lat:
        lat.sort()
        print(f"query latency  : p50 {statistics.median(lat) * 1000:.3f} ms, "
              f"p95 {lat[int(len(lat) * 0.95)] * 1000:.3f} ms")
    if evaluated:
        print(f"top-1          : {top1 / evaluated:.0%}   top-3: {top3 / evaluated:.0%}   "
              f"(over {evaluated} evaluated queries)")

    silent = sum(1 for q in CHATTER if not index.match(q, top_n=args.top_n, min_score=args.min_score))
    print(f"silent on chatter: {silent}/{len(CHATTER)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
