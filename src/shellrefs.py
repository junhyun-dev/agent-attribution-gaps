"""Which committed-but-unseen file changes were at least *named* in a shell command shortly before?

Collects path-like tokens from every shell call (Claude Bash; Codex exec/shell) with time and agent.
Keeps only tokens (paths), never whole commands, in the output. Usage:
  python src/shellrefs.py --data out/v3        (reads the same logs as extract.py)
"""
import argparse
import bisect
import glob
import json
import os
import re
import sys
from collections import defaultdict

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import analyze  # noqa: E402

HOME = os.path.expanduser("~")
REPO = os.path.join(HOME, "personal")
TOK = re.compile(r"[A-Za-z0-9_.~$/{}\-가-힣]+/[A-Za-z0-9_.\-/가-힣]+")
UNTIL = "2026-11-07T07:00"


def norm_tok(t):
    t = t.strip("'\"`.,;:()[]{}")
    for pre in (REPO + "/", "~/personal/", "$HOME/personal/", "${HOME}/personal/", "$PWD/", "./"):
        if t.startswith(pre):
            t = t[len(pre):]
    return t.rstrip("/")


SPLIT = re.compile(r"&&|\|\||;|\n|\|")
READONLY = re.compile(r"^\s*(cd|git|ls|cat|head|tail|grep|rg|wc|find|echo|printf|diff|less|stat|du|file|pdfinfo|pdftotext|sed -n|awk|sort|uniq|test|\[|python3 -c \"import json;print)\b")


def harvest(cmd, ts, out):
    """Keep path tokens only from command segments that can write: not git, not read-only listing/printing."""
    keep = " ".join(seg for seg in SPLIT.split(cmd) if seg.strip() and not READONLY.match(seg))
    for t in TOK.findall(keep):
        t = norm_tok(t)
        if t and not t.startswith(("/", "~", "$", "http")):
            out[t].append(ts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    args = ap.parse_args()
    refs = defaultdict(list)
    dirs = [os.path.join(HOME, ".claude/projects/-home-junhyun-personal")] + glob.glob(os.path.join(HOME, ".claude/projects/-home-junhyun-dev*"))
    for d in dirs:
        for f in glob.glob(os.path.join(d, "*.jsonl")) + glob.glob(os.path.join(d, "*/subagents/*.jsonl")):
            for line in open(f, errors="replace"):
                if '"Bash"' not in line:
                    continue
                try:
                    dd = json.loads(line)
                except ValueError:
                    continue
                ts = dd.get("timestamp") or ""
                if dd.get("type") != "assistant" or not ts or ts >= UNTIL:
                    continue
                for b in dd["message"].get("content", []):
                    if b.get("type") == "tool_use" and b.get("name") == "Bash":
                        harvest(str(b.get("input", {}).get("command", "")), ts, refs)
    for f in glob.glob(os.path.join(HOME, ".codex/sessions/**/*.jsonl"), recursive=True):
        for line in open(f, errors="replace"):
            if "function_call" not in line and "custom_tool_call" not in line and "local_shell" not in line:
                continue
            try:
                dd = json.loads(line)
            except ValueError:
                continue
            p = dd.get("payload") if isinstance(dd.get("payload"), dict) else {}
            ts = dd.get("timestamp") or ""
            if p.get("type") in ("function_call", "custom_tool_call", "local_shell_call") and ts and ts < UNTIL:
                raw = p.get("arguments") or p.get("input") or json.dumps(p.get("action") or "")
                harvest(raw if isinstance(raw, str) else json.dumps(raw), ts, refs)
    idx = {k: sorted(pd.to_datetime(v, utc=True).as_unit("ns").asi8) for k, v in refs.items()}
    print("distinct path tokens", len(idx), file=sys.stderr)

    a, w, c, m = analyze.load(args.data)
    ws = w[(w.ok == 1) & w.path.map(analyze.repo_path)].sort_values("ts")
    seen = defaultdict(list)
    for p, t in zip(ws.path, ws.ts.dt.as_unit("ns").astype("int64")):
        seen[p].append(t)
    git = analyze.read_gitlog(os.path.join(args.data, "gitlog_status.txt"))
    t0, t1 = w.ts.min(), pd.Timestamp(UNTIL, tz="UTC")
    day = 24 * 3600 * 10**9
    cnt = defaultdict(int)

    def hit(key, lo, hi):
        arr = idx.get(key)
        if not arr:
            return False
        i = bisect.bisect_left(arr, lo)
        return i < len(arr) and arr[i] <= hi

    for g in git:
        if not (t0 <= g["ts"] < t1):
            continue
        gt = g["ts"].as_unit("ns").value
        for f in g["files"]:
            s = seen.get(f)
            if s:
                i = bisect.bisect_right(s, gt) - 1
                if i >= 0 and gt - s[i] <= day:
                    cnt["seen_edit_tool"] += 1
                    continue
            ext = os.path.splitext(f)[1].lower()
            kind = "text" if ext in (".md", ".py", ".tsv", ".csv", ".json", ".sh", ".txt", ".yaml", ".yml", ".toml", ".js", ".ts", ".css") else "other"
            if hit(f, gt - day, gt):
                cnt[f"unseen_{kind}_path_named"] += 1
                continue
            parts = f.split("/")
            if any(hit("/".join(parts[:k]), gt - day, gt) for k in range(len(parts) - 1, 1, -1)):
                cnt[f"unseen_{kind}_folder_named"] += 1
            else:
                cnt[f"unseen_{kind}_not_named"] += 1
    tot = sum(cnt.values())
    print(json.dumps({"total_changes": tot, **dict(cnt)}, indent=1))
    json.dump({"total_changes": tot, **dict(cnt)}, open(os.path.join(args.data, "shellrefs.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
