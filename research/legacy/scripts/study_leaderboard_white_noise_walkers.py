"""Public-only leaderboard availability audit; no account credentials or orders.

Fetch: python scripts/study_leaderboard_white_noise_walkers.py --output NEW_DIR
Replay: python scripts/study_leaderboard_white_noise_walkers.py --offline EXISTING_DIR
Only GET requests to the explicitly listed public hosts are made.
"""

import argparse
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

TEAM = "White-Noise-Walkers"
SITE = "https://perpetual-alpha26.github.io"
REPO = "perpetual-alpha26/perpetual-alpha26.github.io"
CF = "https://d23m2l9bjnbgul.cloudfront.net/public/"
ADDRESS = re.compile(r"\b0x[a-fA-F0-9]{40}\b")


def utc():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch(output):
    output.mkdir(parents=True, exist_ok=False)
    manifest = []

    def get(url, filename=None):
        started = utc()
        req = Request(url, headers={"User-Agent": "PublicLeaderboardResearch/1.0"})
        with urlopen(req, timeout=30) as response:
            raw = response.read()
            manifest.append({
                "url": url, "started_at": started, "retrieved_at": utc(),
                "status": response.status, "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw), "file": filename,
                "date": response.headers.get("Date"),
                "etag": response.headers.get("ETag"),
            })
        if filename:
            (output / filename).write_bytes(raw)
        return raw.decode("utf-8")

    try:
        for name in ("leaderboard", "chart", "practice", "practice-chart"):
            get(CF + name + ".json", name + ".json")
        for route, name in (("/leaderboard", "leaderboard.html"),
                            ("/docs/", "guide.html"), ("/faq/", "faq.html")):
            get(SITE + route, name)
        tree = json.loads(get(f"https://api.github.com/repos/{REPO}/git/trees/main?recursive=1", "tree.json"))
        if tree.get("truncated"):
            raise ValueError("Repository tree is truncated")
        get("https://api.github.com/users/perpetual-alpha26/repos?per_page=100", "public-repos.json")
        audits = []
        # Inspect all source text at this immutable revision, excluding generated _site duplicates.
        for item in tree["tree"]:
            path = item["path"]
            if item["type"] != "blob" or path.startswith("_site/"):
                continue
            if not path.endswith((".md", ".html", ".yml", ".xml", ".txt", ".css")):
                continue
            url = f"https://raw.githubusercontent.com/{REPO}/{tree['sha']}/{path}"
            # Team metadata contains emails: retain audit facts/hash, not personal contacts.
            filename = None if path == "_data/teams.yml" else "source-" + path.replace("/", "__")
            source = get(url, filename)
            keys = sorted(set(re.findall(r"^\s*(?:-\s*)?([A-Za-z_]+):", source, re.M)))
            audits.append({"path": path, "contains_target_name": TEAM in source,
                           "address_literals": sorted(set(ADDRESS.findall(source))),
                           "yaml_keys": keys if path.endswith(".yml") else None})
        write_json(output / "source-audit.json", {"revision": tree["sha"], "files": audits})
    finally:
        write_json(output / "manifest.json", manifest)


def summarize(output):
    result = {"team": TEAM, "wallet_mapping": "not established; manual evidence review required", "phases": {}}
    for board_name, chart_name in (("leaderboard", "chart"), ("practice", "practice-chart")):
        board = json.loads((output / (board_name + ".json")).read_text(encoding="utf-8"))
        chart = json.loads((output / (chart_name + ".json")).read_text(encoding="utf-8"))
        rows = [t for t in board["teams"] if t["name"] == TEAM]
        lines = [t for t in chart["teams"] if t["name"] == TEAM]
        if len(rows) != 1 or len(lines) != 1:
            raise ValueError("Target name missing or ambiguous; do not infer account identity")
        line = lines[0]
        start = datetime.fromisoformat(chart["start"].replace("Z", "+00:00"))
        points = [(start + timedelta(seconds=i * chart["step_s"]), v)
                  for i, v in enumerate(line["values"]) if v is not None]
        # Match official frontend: append latest snapshot only if newer than grid endpoint.
        latest = datetime.fromisoformat(line["last_at"].replace("Z", "+00:00"))
        if not points or latest > points[-1][0]:
            points.append((latest, line["pnl"]))
        changes = [b[1] - a[1] for a, b in zip(points, points[1:])
                   if (b[0] - a[0]).total_seconds() == chart["step_s"]]
        peak = 1.0
        drawdown = 0.0
        for _, v in points:
            peak = max(peak, 1 + v)
            drawdown = min(drawdown, (1 + v) / peak - 1)
        result["phases"][board_name] = {
            "board_generated_at": board["generated_at"],
            "chart_generated_at": chart["generated_at"],
            "phase": board["phase"], "from": board["from"], "to": board["to"],
            "team_count": len(board["teams"]), "official_numeric_rank_count":
                sum(isinstance(t.get("rank"), int) for t in board["teams"]),
            "target_board_row": rows[0],
            "board_pnl_position_among_traded": 1 + sum(
                t.get("ranked", False) and t.get("return") is not None
                and t["return"] > rows[0]["return"] for t in board["teams"]),
            "board_team_fields": sorted({k for t in board["teams"] for k in t}),
            "chart_team_fields": sorted({k for t in chart["teams"] for k in t}),
            "address_literals_in_json": sorted(set(ADDRESS.findall(json.dumps([board, chart])))),
            "chart_position": line["position"], "latest_pnl": line["pnl"],
            "last_at": line["last_at"], "step_s": chart["step_s"],
            "grid_count": len(line["values"]), "grid_null_count": line["values"].count(None),
            "first_point": [points[0][0].isoformat(), points[0][1]],
            "last_grid_time": (start + timedelta(seconds=(len(line["values"]) - 1) * chart["step_s"])).isoformat(),
            "min_pnl": min(v for _, v in points), "max_pnl": max(v for _, v in points),
            "sampled_normalized_wealth_drawdown": drawdown,
            "adjacent_full_step_pairs": len(changes),
            "positive_steps": sum(x > 0 for x in changes),
            "negative_steps": sum(x < 0 for x in changes),
            "zero_steps": sum(x == 0 for x in changes),
        }
    write_json(output / "summary.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path, help="New snapshot directory; never overwrite")
    mode.add_argument("--offline", type=Path, help="Replay saved snapshots without network")
    args = parser.parse_args()
    if args.output:
        fetch(args.output)
    summarize(args.output or args.offline)
