"""Figures for the report. Usage: python src/figures.py out/v2 out/fig"""
import json
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, os.path.dirname(__file__))
from measure import CONTROL_LOCK, CONTROL_OWNERSHIP, CONTROL_RUNLIMIT, wilson  # noqa: E402

D, OUT = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)
R = json.load(open(os.path.join(D, "results.json")))
BLUE, GREY, LIGHT, RED = "#1f5fbf", "#6b7280", "#c7d2e3", "#c0392b"
plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False,
                     "font.family": "DejaVu Sans", "savefig.dpi": 200})

# ---- Figure 1: who was active, per day ----
ph = pd.read_csv(os.path.join(D, "active_per_hour.csv"), index_col=0, parse_dates=True)
day = pd.DataFrame(index=ph.index.floor("D").unique())
ev = pd.concat([pd.read_csv(os.path.join(D, f), usecols=["agent_id", "ts"]) for f in ("writes.csv", "commits.csv", "messages.csv")])
ev["ts"] = pd.to_datetime(ev.ts, utc=True)
ev = ev[ev.ts < pd.Timestamp(R["window_utc"][1]).ceil("min")]
ag = pd.read_csv(os.path.join(D, "agents.csv"), dtype=str)
link_roles = pd.read_csv(os.path.join(D, "known_writes.csv"), usecols=["agent_id", "role"]).drop_duplicates("agent_id")
roles = ag[["agent_id", "kind"]].merge(link_roles, on="agent_id", how="left")
roles["r"] = roles.role.fillna(roles.kind.map(lambda k: "subagent" if k == "claude:sub" else ("worker" if k.startswith("codex:codex_exec") else "lead")))
roles["r"] = roles.r.replace({"worker (linked)": "worker", "worker (unlinked)": "worker"})
ev = ev.merge(roles[["agent_id", "r"]], on="agent_id")
ev["d"] = ev.ts.dt.floor("D")
daily = ev.groupby(["d", "r"]).agent_id.nunique().unstack(fill_value=0)[["lead", "subagent", "worker"]]
fig, ax = plt.subplots(figsize=(6.5, 2.4))
bottom = None
for col, colr, lab in (("lead", BLUE, "lead sessions (Claude, Codex)"), ("subagent", LIGHT, "Claude subagents"), ("worker", GREY, "Codex worker runs")):
    ax.bar(daily.index, daily[col], bottom=bottom, color=colr, width=0.8, label=lab)
    bottom = daily[col] if bottom is None else bottom + daily[col]
top = ax.get_ylim()[1]
HANDOVER = pd.Timestamp("2026-10-22 13:30", tz="UTC")
for t, lab, yy, ha in ((HANDOVER, "coordinator\nsession change ", 0.97, "right"), (CONTROL_OWNERSHIP, " ownership list", 0.80, "left"), (CONTROL_RUNLIMIT, " worker cap", 0.97, "left"), (CONTROL_LOCK, " heavy-run lock", 0.97, "left")):
    ax.axvline(t, color=RED, lw=0.8, ls="--")
    ax.text(t, top * yy, lab, color=RED, fontsize=7, va="top", ha=ha)
ax.set_ylabel("agents active that day")
ax.legend(frameon=False, fontsize=7, loc="upper left", bbox_to_anchor=(0, 0.88))
ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%b %d"))
fig.tight_layout()
fig.savefig(os.path.join(OUT, "fig1_activity.png"))

# ---- Figure 2: what an overseer can attribute, by log source ----
oc, of, lw, h2 = R["observability_commits"], R["observability_files"], R["linked_workers"], R["h2"]
rows = [
    ("Commits naming their session\n(agent-written commit subject)", oc["agent_written_session_claim"], oc["git_commits"]),
    ("Commits tied to a session\n(harness logs: shell call + subject + time)", oc["harness_matched"], oc["git_commits"]),
    ("Committed file changes with an\nedit-tool event in prior 24 h", of["seen_any_structured_write_24h"], of["file_changes"]),
    ("  - text files only", of["by_kind"]["text"]["seen"], of["by_kind"]["text"]["n"]),
    ("Codex worker runs tied to\nthe session that launched them", lw["linked"], lw["codex_exec"]),
    ("Harness-detected write conflicts with\na visible conflicting writer", h2["with_other_team_write_30min_before"], h2["stale_rejects"]),
]
fig, ax = plt.subplots(figsize=(6.5, 2.9))
ys = list(range(len(rows)))[::-1]
for y, (lab, k, n) in zip(ys, rows):
    p = k / n
    lo, hi = wilson(k, n)
    ax.barh(y, 1, color="#eef2f8", height=0.62)
    ax.barh(y, p, color=BLUE if p >= 0.5 else GREY, height=0.62)
    ax.errorbar(p, y, xerr=[[max(0, p - lo)], [max(0, hi - p)]], fmt="none", ecolor="black", lw=0.7, capsize=2)
    ax.text(1.01, y, f"{100*p:.0f}%  ({k:,} / {n:,})", va="center", fontsize=7.5)
ax.set_yticks(ys)
ax.set_yticklabels([r[0] for r in rows], fontsize=7.5)
ax.set_xlim(0, 1)
ax.set_xticks([0, .25, .5, .75, 1])
ax.set_xticklabels(["0", "25%", "50%", "75%", "100%"])
ax.set_xlabel("share an overseer can attribute from the logs (95% Wilson CI)")
fig.tight_layout()
fig.savefig(os.path.join(OUT, "fig2_observability.png"), bbox_inches="tight")

# ---- Figure 3: cross-role commits before/after, with and without the coordinator ----
h = pd.read_csv(os.path.join(D, "h3_files.csv"))
coord = (h.last_lineage.fillna("") == "총괄") | (h.committer_lineage.fillna("") == "총괄")
groups = [("all commits", h), ("commits not involving\nthe coordinator role", h[~coord])]
fig, ax = plt.subplots(figsize=(4.2, 2.4))
x = 0
ticks, labs = [], []
for lab, g in groups:
    pc = g.groupby(["hash", "period"]).foreign.any().reset_index()
    for i, per in enumerate(("before", "after")):
        s = pc[pc.period == per]
        k, n = int(s.foreign.sum()), len(s)
        lo, hi = wilson(k, n)
        ax.bar(x + i * 0.8, k / n, width=0.75, color=GREY if per == "before" else BLUE)
        ax.errorbar(x + i * 0.8, k / n, yerr=[[max(0, k / n - lo)], [max(0, hi - k / n)]], fmt="none", ecolor="black", lw=0.7, capsize=2)
        ax.text(x + i * 0.8, hi + 0.004, f"{100*k/n:.1f}%\n{k}/{n}", ha="center", fontsize=6.5)
    ticks.append(x + 0.4)
    labs.append(lab)
    x += 2.4
ax.set_xticks(ticks)
ax.set_xticklabels(labs, fontsize=7.5)
ax.set_ylabel("commits that include a file\nlast written by another role", fontsize=7.5)
from matplotlib.patches import Patch  # noqa: E402
ax.legend(handles=[Patch(color=GREY, label="before Oct 26 07:10 UTC"), Patch(color=BLUE, label="after")], frameon=False, fontsize=7)
ax.set_ylim(0, 0.2)
fig.tight_layout()
fig.savefig(os.path.join(OUT, "fig3_crossrole.png"))
print("ok")
