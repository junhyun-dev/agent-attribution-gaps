"""Robustness checks for the before/after change in cross-role commits (H3)."""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from measure import CONTROL_OWNERSHIP, wilson  # noqa: E402

D = sys.argv[1]
h = pd.read_csv(os.path.join(D, "h3_files.csv"))
h["ts"] = pd.to_datetime(h.ts, utc=True)
a = pd.read_csv(os.path.join(D, "agents.csv"), dtype=str).fillna("")


def rate(df, label):
    pc = df.groupby(["hash", "period"]).foreign.any().reset_index()
    out = []
    for p in ("before", "after"):
        g = pc[pc.period == p]
        k, n = int(g.foreign.sum()), len(g)
        lo, hi = wilson(k, n)
        out.append(f"{p} {k}/{n}={k/n:.3f} [{lo:.3f},{hi:.3f}]")
    print(f"{label:55s}", " | ".join(out))


rate(h, "main (24h lookback, all linked workers)")
chief = h.last_lineage.fillna("").str.fullmatch("총괄") | h.committer_lineage.fillna("").str.fullmatch("총괄")
rate(h[~chief], "excluding commits touching the chief lineage")
h["gap_h"] = None
rate(h[h.ts.between(CONTROL_OWNERSHIP - pd.Timedelta(days=7), CONTROL_OWNERSHIP + pd.Timedelta(days=7))], "only 7 days either side")
rate(h[h["shape"] == "add-then-commit"], "only 'git add X && git commit' commits")
# daily cross-role commit rate
pc = h.groupby("hash").agg(ts=("ts", "first"), foreign=("foreign", "any"))
d = pc.groupby(pc.ts.dt.floor("D")).foreign.agg(["sum", "count"])
d["rate"] = d["sum"] / d["count"]
print(d.to_string())
