"""Coordination-hazard measures from the event tables made by extract.py.

H1 interleaved writes   same file, consecutive successful writes by different principals within a window
H2 stale-write rejects   harness refused a write because the file changed since the agent read it
H3 commit scope leakage  a commit includes a file whose last observed writer is another principal
H4 attribution mismatch  agent-written commit subject names a different session than the harness log
"""
import argparse
import json
import os
import re
import sys

import pandas as pd

WINDOW_MIN = 30


def load(d):
    a = pd.read_csv(os.path.join(d, "agents.csv"), dtype=str).fillna("")
    w = pd.read_csv(os.path.join(d, "writes.csv"), dtype={"path": str}).fillna("")
    c = pd.read_csv(os.path.join(d, "commits.csv"), dtype=str).fillna("")
    m = pd.read_csv(os.path.join(d, "messages.csv"), dtype=str).fillna("")
    for t in (w, c, m):
        t["ts"] = pd.to_datetime(t["ts"], utc=True, errors="coerce")
    # principal: a Claude subagent acts for its parent session
    a["principal"] = a.apply(lambda r: r.parent if r.parent else r.agent_id, axis=1)
    names = a.set_index("agent_id")["name"].to_dict()
    a["principal_name"] = a.principal.map(names).fillna("")
    a["family"] = a.kind.str.split(":").str[0]
    return a, w, c, m


def read_gitlog(path):
    rows, cur = [], None
    for line in open(path, errors="replace"):
        line = line.rstrip("\n")
        if line.startswith("@@"):
            h, at, an, subj = (line[2:].split("\t", 3) + ["", "", ""])[:4]
            cur = dict(hash=h, ts=pd.Timestamp(int(at), unit="s", tz="UTC"), author=an, subject=subj, files=[])
            rows.append(cur)
        elif line.strip() and cur is not None:
            cur["files"].append(line.strip())
    return rows


def repo_path(p):
    return bool(p) and not p.startswith(("~", "abs:", "rel:"))


def h1_interleave(w, a):
    ws = w[(w.ok == 1) & w.path.map(repo_path)].merge(a[["agent_id", "principal", "family"]], on="agent_id")
    ws = ws.sort_values(["path", "ts"])
    ws["prev_principal"] = ws.groupby("path").principal.shift()
    ws["gap_min"] = ws.groupby("path").ts.diff().dt.total_seconds() / 60
    ws["switch"] = ws.prev_principal.notna() & (ws.prev_principal != ws.principal)
    ws["switch_fast"] = ws.switch & (ws.gap_min <= WINDOW_MIN)
    per_file = ws.groupby("path").agg(writes=("ts", "size"), principals=("principal", "nunique"),
                                      switches=("switch", "sum"), fast=("switch_fast", "sum"))
    return ws, per_file


def match_commits(c, git, a):
    """Attach a harness committer to each git commit: same subject prefix, call within 15 min before commit time."""
    cc = c[c.msg.str.len() >= 4].merge(a[["agent_id", "principal", "family"]], on="agent_id")
    by_prefix = {}
    for r in cc.itertuples():
        by_prefix.setdefault(r.msg[:24], []).append(r)
    out = []
    for g in git:
        cands = [r for r in by_prefix.get(g["subject"][:24], [])
                 if pd.Timedelta(minutes=-1) <= g["ts"] - r.ts <= pd.Timedelta(minutes=15)]
        if not cands:
            continue
        best = min(cands, key=lambda r: abs((g["ts"] - r.ts).total_seconds()))
        out.append(dict(hash=g["hash"], ts=g["ts"], subject=g["subject"], files=g["files"],
                        committer=best.agent_id, principal=best.principal, family=best.family,
                        shape=getattr(best, "shape", "")))
    return out


def h3_scope(matched, ws):
    """For each file in a matched commit: who was the last observed writer before the commit?"""
    last = {}
    by_path = {p: grp[["ts", "principal"]].values for p, grp in ws.groupby("path")}
    rows = []
    for m in matched:
        for f in m["files"]:
            arr = by_path.get(f)
            lw = None
            if arr is not None:
                prior = [x for x in arr if x[0] <= m["ts"]]
                if prior:
                    lw = prior[-1][1]
            if lw is None:
                cat = "unobserved"
            elif lw == m["principal"]:
                cat = "own"
            else:
                cat = "foreign"
            rows.append(dict(hash=m["hash"], ts=m["ts"], file=f, committer=m["principal"], family=m["family"],
                             last_writer=lw or "", cat=cat))
    return pd.DataFrame(rows)


SPACE = re.compile(r"\s+")


def h4_attribution(matched, a):
    names = a.drop_duplicates("agent_id").set_index("agent_id")["name"].to_dict()
    rows = []
    for m in matched:
        subj = m["subject"]
        prefix = subj.split(":", 1)[0].strip() if ":" in subj[:60] else ""
        nm = names.get(m["principal"], "")
        if not prefix or not nm:
            cat = "no-claim" if not prefix else "unnamed-committer"
        else:
            p, n = SPACE.sub("", prefix), SPACE.sub("", nm)
            cat = "match" if (p == n or p in n or n in p) else "mismatch"
        rows.append(dict(hash=m["hash"], family=m["family"], prefix=prefix, committer_name=nm, cat=cat))
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    args = ap.parse_args()
    a, w, c, m = load(args.data)
    git = read_gitlog(os.path.join(args.data, "gitlog.txt"))
    t0 = w.ts.min()
    git = [g for g in git if g["ts"] >= t0]
    ws, per_file = h1_interleave(w, a)
    matched = match_commits(c, git, a)
    scope = h3_scope(matched, ws)
    attr = h4_attribution(matched, a)
    res = {
        "window": [str(t0), str(w.ts.max())],
        "agents": a.kind.value_counts().to_dict(),
        "principals_writing_repo": int(ws.principal.nunique()),
        "repo_writes_ok": int(len(ws)),
        "files_written": int(len(per_file)),
        "files_multi_principal": int((per_file.principals > 1).sum()),
        "writes_on_multi_principal_files": int(per_file[per_file.principals > 1].writes.sum()),
        "switches": int(ws.switch.sum()),
        f"switches_within_{WINDOW_MIN}min": int(ws.switch_fast.sum()),
        "stale_rejects": int((w.err == "stale").sum()),
        "write_attempts_claude": int(w.merge(a, on="agent_id").query("family=='claude'").shape[0]),
        "git_commits_in_window": len(git),
        "git_commits_matched_to_harness": len(matched),
        "scope_cats": scope.cat.value_counts().to_dict() if len(scope) else {},
        "commits_with_foreign_file": int(scope[scope.cat == "foreign"].hash.nunique()) if len(scope) else 0,
        "attribution": attr.cat.value_counts().to_dict() if len(attr) else {},
        "messages": m.direction.value_counts().to_dict(),
    }
    print(json.dumps(res, indent=1, ensure_ascii=False, default=str))
    per_file.sort_values("fast", ascending=False).head(25).to_csv(os.path.join(args.data, "top_interleaved_files.csv"))
    scope.to_csv(os.path.join(args.data, "scope.csv"), index=False)
    attr.to_csv(os.path.join(args.data, "attribution.csv"), index=False)
    ws.to_csv(os.path.join(args.data, "repo_writes.csv"), index=False)


if __name__ == "__main__":
    main()
