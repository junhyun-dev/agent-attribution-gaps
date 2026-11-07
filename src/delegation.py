"""Link each Codex exec thread to the session that launched it (heuristic, from launch commands)."""
import os
import pandas as pd

HOME = os.path.expanduser("~")
REPO = os.path.join(HOME, "personal")


def expand(cmd):
    return (cmd.replace("$HOME", HOME).replace("${HOME}", HOME).replace("~/", HOME + "/")
               .replace('"$PWD"', REPO).replace("$PWD", REPO))


import re

REAL_LAUNCH = re.compile(r"(codex_oneoff\.sh|codex_oneoff\.py)\s+[\"'$~/A-Za-z]|codex exec\s+[-\"'$~/A-Za-z]")
NOT_LAUNCH = re.compile(r"^\s*(sed|cat|grep|head|tail|less|wc|ls)\b")


def is_launch(cmd):
    return bool(REAL_LAUNCH.search(cmd)) and not NOT_LAUNCH.search(cmd)


def link(a, launches):
    L = launches[launches.cmd.fillna("").map(is_launch)].copy()
    L["ts"] = pd.to_datetime(L.ts, utc=True, errors="coerce")
    L["cmdx"] = L.cmd.map(expand)
    L = L.merge(a[["agent_id", "principal"]], on="agent_id")
    cx = a[a.kind.str.startswith("codex:codex_exec")].copy()
    cx["t"] = pd.to_datetime(cx.first_ts, utc=True, errors="coerce")
    out = {}
    how = {}
    for r in cx.itertuples():
        if pd.isna(r.t):
            continue
        win = L[(L.ts <= r.t + pd.Timedelta(seconds=30)) & (L.ts >= r.t - pd.Timedelta(minutes=20))]
        cwd_abs = r.cwd if r.cwd.startswith("/") else (os.path.join(HOME, r.cwd[2:]) if r.cwd.startswith("~/") else os.path.join(REPO, r.cwd))
        rel = r.cwd if not r.cwd.startswith(("~", "/")) else None
        hit = win[win.cmdx.str.contains(cwd_abs, regex=False) | (win.cmdx.str.contains(rel, regex=False) if rel and rel != "." else False)]
        if len(hit):
            out[r.agent_id] = hit.sort_values("ts").iloc[-1].principal
            how[r.agent_id] = "path"
        else:
            near = win[win.ts >= r.t - pd.Timedelta(minutes=3)]
            if near.principal.nunique() == 1:
                out[r.agent_id] = near.principal.iloc[0]
                how[r.agent_id] = "time"
    return out, how
