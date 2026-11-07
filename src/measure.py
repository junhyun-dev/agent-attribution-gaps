"""All measures reported in the paper, from the tables made by extract.py.

Usage: python src/measure.py --data out/v2
Writes results.json and a few CSVs next to the data. Figures: src/figures.py.
"""
import argparse
import json
import math
import os
import re
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import analyze  # noqa: E402
import delegation  # noqa: E402

# Controls introduced by the operator (from git history of the rules file, KST -> UTC)
CONTROL_OWNERSHIP = pd.Timestamp("2026-10-26 07:10", tz="UTC")  # ownership list + "commit only your own paths"
CONTROL_RUNLIMIT = pd.Timestamp("2026-10-27 05:19", tz="UTC")   # cap on concurrent worker runs
CONTROL_LOCK = pd.Timestamp("2026-11-04 15:22", tz="UTC")       # shared lock for heavy runs
SWITCH_WINDOW = pd.Timedelta(minutes=30)
LOOKBACK = pd.Timedelta(hours=24)

CATS = [
    ("rules", re.compile(r"(^|/)(AGENTS|CLAUDE)\.md$|SKILL\.md$|standard-process\.md$|/prompts/|/skills/")),
    ("status / registry", re.compile(r"DASHBOARD\.md$|places\.tsv$|sessions\.md$|entries\.tsv$|site-terms\.tsv$|(^|/)README\.md$|gate\.md$|00-now\.md$")),
    ("shared notes", re.compile(r"notes\.md$|scratch[^/]*\.md$|_docroom\.md$|toc\.md$|/book/")),
    ("shared code", re.compile(r"\.py$|\.sh$")),
]


def category(path):
    for name, rx in CATS:
        if rx.search(path):
            return name
    return "project content"


def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - r) / d, (c + r) / d)


LINEAGE_DROP = re.compile(r"클로드|세션|\s|v\d+$|·?Codex$")


def lineage(name):
    """Successive sessions that hold the same role (e.g. 'chief v5' -> 'chief v6') share a lineage."""
    return LINEAGE_DROP.sub("", LINEAGE_DROP.sub("", name or "")) or None


def teams(a, launches):
    link, how = delegation.link(a, launches)
    a = a.copy()
    a["team"] = a.apply(lambda r: link.get(r.agent_id, r.principal), axis=1)
    names = a.set_index("agent_id")["name"].to_dict()
    a["lineage"] = a.team.map(lambda t: lineage(names.get(t, "")) or t)
    a["linked"] = a.agent_id.map(lambda x: x in link)
    a["role"] = "lead"
    a.loc[a.kind == "claude:sub", "role"] = "subagent"
    a.loc[(a.kind == "codex:codex_exec") & a.linked, "role"] = "worker (linked)"
    a.loc[(a.kind == "codex:codex_exec") & ~a.linked, "role"] = "worker (unlinked)"
    return a, link, how


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--until", default="2026-11-07 07:00", help="UTC cut-off; excludes the hackathon session itself")
    args = ap.parse_args()
    D = args.data
    a, w, c, m = analyze.load(D)
    until = pd.Timestamp(args.until, tz="UTC")
    w, c, m = w[w.ts < until], c[c.ts < until], m[m.ts < until]
    a = a[pd.to_datetime(a.first_ts, utc=True, errors="coerce") < until]
    L = pd.read_csv(os.path.join(D, "launches.csv"), dtype=str)
    a, link, how = teams(a, L)
    git = analyze.read_gitlog(os.path.join(D, "gitlog.txt"))
    t0, t1 = w.ts.min(), w.ts.max()
    git = [g for g in git if t0 <= g["ts"] <= t1]
    R = {"window_utc": [str(t0), str(t1)], "days": round((t1 - t0).total_seconds() / 86400, 1)}

    # ---------- structure ----------
    R["agents_by_kind"] = a.kind.value_counts().to_dict()
    R["agents_by_role"] = a.role.value_counts().to_dict()
    leads = a[a.role == "lead"]
    fan = a[a.role.isin(["subagent", "worker (linked)"])].groupby("team").size()
    R["leads"] = int(len(leads))
    R["leads_with_any_worker"] = int(len(fan))
    R["fanout_max"] = int(fan.max()) if len(fan) else 0
    top = fan.sort_values(ascending=False)
    R["top5_leads_share_of_delegated"] = round(float(top.head(5).sum() / top.sum()), 3) if len(top) else None
    R["linked_workers"] = {"linked": int(a.linked.sum()), "codex_exec": int((a.kind == "codex:codex_exec").sum()),
                           "by_method": pd.Series(how).value_counts().to_dict()}
    # concurrency: agents active per hour
    a["t_first"] = pd.to_datetime(a.first_ts, utc=True, errors="coerce")
    a["t_last"] = pd.to_datetime(a.last_ts, utc=True, errors="coerce")
    hours = pd.date_range(t0.floor("h"), t1.ceil("h"), freq="h")
    act = []
    ev = pd.concat([w[["agent_id", "ts"]], c[["agent_id", "ts"]], m[["agent_id", "ts"]]])
    ev["h"] = ev.ts.dt.floor("h")
    per_h = ev.merge(a[["agent_id", "role"]], on="agent_id").groupby(["h", "role"]).agent_id.nunique().unstack(fill_value=0)
    per_h = per_h.reindex(hours, fill_value=0)
    per_h.to_csv(os.path.join(D, "active_per_hour.csv"))
    tot = per_h.sum(axis=1)
    R["active_agents_per_hour"] = {"median_nonzero": float(tot[tot > 0].median()), "max": int(tot.max()),
                                   "max_at": str(tot.idxmax())}
    R["messages"] = m.direction.value_counts().to_dict()

    # ---------- observability ----------
    names_norm = set(re.sub(r"\s+", "", x) for x in a.name if x)

    def session_claim(subj):
        if ":" not in subj[:60]:
            return False
        return re.sub(r"\s+", "", subj.split(":", 1)[0]) in names_norm

    n_git = len(git)
    claims = sum(session_claim(g["subject"]) for g in git)
    matched = analyze.match_commits(c, git, a.assign(principal=a.team))
    mhash = {x["hash"] for x in matched}
    claim_or_match = sum(1 for g in git if session_claim(g["subject"]) or g["hash"] in mhash)
    attr = analyze.h4_attribution(matched, a.assign(principal=a.team))
    attr["claim"] = attr.prefix.map(lambda p: re.sub(r"\s+", "", p) in names_norm)
    cl = attr[attr.claim]
    R["observability_commits"] = {
        "git_commits": n_git,
        "agent_written_session_claim": claims,
        "harness_matched": len(matched),
        "either": claim_or_match,
        "claims_checked_against_harness": int(len(cl)),
        "claims_correct": int((cl.prefix.map(lambda p: re.sub(r"\s+", "", p)) ==
                               cl.committer_name.map(lambda p: re.sub(r"\s+", "", p))).sum()),
    }
    # committed file changes: is there a structured write event (by anyone) in the 24h before the commit?
    ws = w[(w.ok == 1) & w.path.map(analyze.repo_path)].merge(a[["agent_id", "team", "role"]], on="agent_id")
    ws = ws.sort_values("ts")
    by_path = {p: (g.ts.values, g.team.values, g.role.values) for p, g in ws.groupby("path")}
    rows = []
    mt = {x["hash"]: x for x in matched}
    for g in git:
        mm = mt.get(g["hash"])
        for f in g["files"]:
            ext = os.path.splitext(f)[1].lower()
            tv = by_path.get(f)
            lw_team = lw_role = None
            seen = False
            if tv is not None:
                ts_arr, tm_arr, rl_arr = tv
                gt = g["ts"].to_datetime64()
                idx = (ts_arr <= gt).sum() - 1
                if idx >= 0 and gt - ts_arr[idx] <= LOOKBACK.to_timedelta64():
                    seen = True
                    lw_team, lw_role = tm_arr[idx], rl_arr[idx]
            rows.append(dict(hash=g["hash"], ts=g["ts"], file=f, ext=ext, cat=category(f), seen=seen,
                             committer_team=mm["principal"] if mm else None, shape=mm["shape"] if mm else None,
                             last_team=lw_team, last_role=lw_role))
    cf = pd.DataFrame(rows)
    text_ext = {".md", ".py", ".tsv", ".csv", ".json", ".sh", ".txt", ".yaml", ".yml", ".toml", ".js", ".ts", ".css"}
    cf["kind"] = cf.ext.map(lambda e: "text" if e in text_ext else ("html" if e in (".html", ".htm") else "binary/image"))
    R["observability_files"] = {
        "file_changes": int(len(cf)),
        "seen_any_structured_write_24h": int(cf.seen.sum()),
        "by_kind": {k: {"n": int(len(g)), "seen": int(g.seen.sum())} for k, g in cf.groupby("kind")},
        "md_py_only": {"n": int(cf.ext.isin([".md", ".py"]).sum()), "seen": int(cf[cf.ext.isin([".md", ".py"])].seen.sum())},
    }

    # ---------- hazards ----------
    # H1 cross-team interleaving (exclude unlinked workers: their team is unknown)
    known = ws[ws.role != "worker (unlinked)"].sort_values(["path", "ts"]).copy()
    known["prev_team"] = known.groupby("path").team.shift()
    known["gap"] = known.groupby("path").ts.diff()
    known["switch"] = known.prev_team.notna() & (known.prev_team != known.team)
    known["fast"] = known.switch & (known.gap <= SWITCH_WINDOW)
    known["cat"] = known.path.map(category)
    files_team = known.groupby("path").team.nunique()
    R["h1"] = {
        "files_written_known": int(len(files_team)),
        "files_multi_team": int((files_team > 1).sum()),
        "switches": int(known.switch.sum()),
        "fast_switches_30min": int(known.fast.sum()),
        "fast_switch_by_cat": known[known.fast].cat.value_counts().to_dict(),
        "files_by_cat": known.drop_duplicates("path").cat.value_counts().to_dict(),
        "multi_team_files_by_cat": known[known.path.isin(files_team[files_team > 1].index)].drop_duplicates("path").cat.value_counts().to_dict(),
    }
    known.to_csv(os.path.join(D, "known_writes.csv"), index=False)

    # H2 stale-write rejections: did another team write the file in the 30 min before?
    st = w[w.err == "stale"].merge(a[["agent_id", "team"]], on="agent_id")
    race = 0
    for r in st.itertuples():
        prior = ws[(ws.path == r.path) & (ws.ts < r.ts) & (ws.ts >= r.ts - SWITCH_WINDOW)]
        if (prior.team != r.team).any():
            race += 1
    cl_att = w.merge(a[["agent_id", "kind"]], on="agent_id")
    claude_attempts = int(cl_att.kind.str.startswith("claude").sum())
    R["h2"] = {"stale_rejects": int(len(st)), "claude_write_attempts": claude_attempts,
               "with_other_team_write_30min_before": race,
               "by_cat": st.path.map(category).value_counts().to_dict()}

    # H3 commit scope: committed file last written (24h) by a different *known* team
    h3 = cf[cf.committer_team.notna() & cf.seen & (cf.last_role != "worker (unlinked)")].copy()
    lin = a.drop_duplicates("team").set_index("team")["lineage"].to_dict()
    h3["committer_lineage"] = h3.committer_team.map(lin)
    h3["last_lineage"] = h3.last_team.map(lin)
    h3["kind_of_foreign"] = "own"
    h3.loc[(h3.last_team != h3.committer_team) & (h3.last_lineage == h3.committer_lineage), "kind_of_foreign"] = "successor"
    h3.loc[(h3.last_team != h3.committer_team) & (h3.last_lineage != h3.committer_lineage), "kind_of_foreign"] = "cross-role"
    h3["foreign"] = h3.kind_of_foreign == "cross-role"
    h3["period"] = (h3.ts >= CONTROL_OWNERSHIP).map({True: "after", False: "before"})
    per_commit = h3.groupby(["hash", "period"]).foreign.any().reset_index()
    out = {}
    for p, g in per_commit.groupby("period"):
        k, n = int(g.foreign.sum()), int(len(g))
        lo, hi = wilson(k, n)
        out[p] = {"commits_with_seen_files": n, "with_foreign_file": k, "rate": round(k / n, 4), "ci95": [round(lo, 4), round(hi, 4)]}
    shp = {}
    pcs = h3.groupby(["hash", "period", "shape"]).foreign.any().reset_index()
    for (p, sh), g in pcs.groupby(["period", "shape"]):
        k, n = int(g.foreign.sum()), int(len(g))
        lo, hi = wilson(k, n)
        shp[f"{p}|{sh}"] = {"n": n, "foreign": k, "rate": round(k / n, 4), "ci95": [round(lo, 4), round(hi, 4)]}
    allc = pd.DataFrame(matched)
    allc["period"] = (allc.ts >= CONTROL_OWNERSHIP).map({True: "after", False: "before"})
    R["commit_shapes"] = allc.groupby(["period", "shape"]).size().unstack(fill_value=0).to_dict(orient="index")
    R["h3_kinds"] = {p: g.groupby("hash").kind_of_foreign.agg(lambda x: "cross-role" if (x == "cross-role").any() else ("successor" if (x == "successor").any() else "own")).value_counts().to_dict()
                     for p, g in h3.groupby("period")}
    R["h3"] = {"by_period": out, "by_period_shape": shp, "foreign_files_by_cat": h3[h3.foreign].cat.value_counts().to_dict(),
               "foreign_files": int(h3.foreign.sum()), "files_checked": int(len(h3))}
    h3.to_csv(os.path.join(D, "h3_files.csv"), index=False)

    # H1 before/after (fast switches per 1,000 known writes)
    known["period"] = (known.ts >= CONTROL_OWNERSHIP).map({True: "after", False: "before"})
    h1p = {}
    for p, g in known.groupby("period"):
        k, n = int(g.fast.sum()), int(len(g))
        lo, hi = wilson(k, n)
        h1p[p] = {"writes": n, "fast_switches": k, "per_1000": round(1000 * k / n, 2), "ci95_per_1000": [round(1000 * lo, 2), round(1000 * hi, 2)]}
    R["h1"]["by_period"] = h1p
    # H2 before/after
    w2 = w.merge(a[["agent_id", "kind"]], on="agent_id")
    w2 = w2[w2.kind.str.startswith("claude")]
    w2["period"] = (w2.ts >= CONTROL_OWNERSHIP).map({True: "after", False: "before"})
    R["h2"]["by_period"] = {p: {"attempts": int(len(g)), "stale": int((g.err == "stale").sum())} for p, g in w2.groupby("period")}

    # daily series for the figure
    daily = pd.DataFrame({
        "writes_known": known.groupby(known.ts.dt.floor("D")).size(),
        "fast_switches": known[known.fast].groupby(known[known.fast].ts.dt.floor("D")).size(),
    }).fillna(0)
    pc = h3.groupby("hash").agg(ts=("ts", "first"), foreign=("foreign", "any"))
    daily["commits_checked"] = pc.groupby(pc.ts.dt.floor("D")).size()
    daily["commits_foreign"] = pc[pc.foreign].groupby(pc[pc.foreign].ts.dt.floor("D")).size()
    daily = daily.fillna(0)
    daily.to_csv(os.path.join(D, "daily.csv"))
    R["controls_utc"] = {"ownership_list": str(CONTROL_OWNERSHIP), "run_limit": str(CONTROL_RUNLIMIT), "heavy_lock": str(CONTROL_LOCK)}
    with open(os.path.join(D, "results.json"), "w") as fh:
        json.dump(R, fh, indent=1, ensure_ascii=False, default=str)
    print(json.dumps(R, indent=1, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
