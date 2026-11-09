# Who wrote this? — attribution gaps in a shared workspace of coding agents

Code for the report *"Who Wrote This? Measuring Attribution Gaps in a Shared Workspace of 1,576 Coding-Agent Sessions and Runs"*,
submitted to the Swarm Alignment Hackathon (Apart Research, November 2026). The report is [`paper/final-candidate.pdf`](paper/final-candidate.pdf).

**All code in this repository was written during the hackathon (7–9 Nov 2026, Korea time; the deadline was 8 Nov AoE). Nothing is pre-existing.**
The workspace, its working rules and its logs existed before the hackathon; they are the object of study and are not included here.

## What it measures

One operator's git repository was used for 38 days by 1,576 Claude Code and Codex sessions, subagents and worker runs (7,986 commits).
Using only the logs the harnesses already write, how far can an overseer tell who changed what?

| Question | Result |
|---|---|
| Which session made a commit? | Matching shell calls to commits links 71% of commits to a session; all 277 linked ground-truth commits (commit hash printed in the log) were right |
| Which agent wrote a committed file change? | 12% have an edit-tool event on the same path in the preceding 24 hours; for another 15% a shell command names the exact path; for 67% the shell logs name only an enclosing folder |
| Which session launched a worker run? | Two timing rules find a candidate launcher for 9% or 33% of worker runs |
| Where do commits include another role's changes? | Mostly around the coordinator agent (38% and 20% of the commits involving it, before and after an operator rule change, against 0.5% and 2.7% of all other commits); audited cases looked like intended hand-offs |

## What it does

Turns harness logs from Claude Code (session transcripts, including subagents) and Codex (rollout logs), plus `git log`,
into flat who-wrote-what-when tables, then computes the measures above.
It reads metadata only (tool names, file paths, timestamps, outcomes, commit subjects, launch and commit commands)
and never stores message bodies or file contents.

| Step | File | Output |
|---|---|---|
| 1. Extract | `src/extract.py` | `agents.csv`, `writes.csv`, `commits.csv`, `messages.csv`, `launches.csv` |
| 2. Link workers to launchers | `src/delegation.py` | (used by step 3) |
| 3. Measures | `src/measure.py` (+ `src/analyze.py`) | `results.json`, `h3_files.csv`, `known_writes.csv`, `daily.csv` |
| 4. Paths named in shell commands | `src/shellrefs.py` | share of committed changes whose path or folder a shell command named |
| 5. Robustness | `src/robust.py` | before/after variants, busiest days left out |
| 6. Log-folder access | `src/logsurface.py` | counts of agent calls touching the log folders |
| 7. Figures | `src/figures.py` | `fig1_activity.png`, `fig2_observability.png`, `fig3_crossrole.png` |

```bash
python3 -m venv .venv && .venv/bin/pip install pandas matplotlib scipy
.venv/bin/python src/extract.py --out out/run            # reads ~/.claude/projects/<repo> and ~/.codex/sessions
git -C <repo> log --since=2026-09-01 --format='@@%H%x09%at%x09%an%x09%s' --name-only --no-renames > out/run/gitlog.txt
.venv/bin/python src/measure.py --data out/run --until "2026-11-07 07:00"
.venv/bin/python src/shellrefs.py --data out/run
.venv/bin/python src/robust.py out/run
.venv/bin/python src/figures.py out/run out/fig
```

Paths in `extract.py` and `shellrefs.py` (`REPO`, default log folders) point at the author's machine; change them for your own workspace.
Role names are read from session titles in the author's own naming (for example `총괄` = coordinator); adapt `measure.py` to yours.

## Report

`paper/body3.html` is the report text; `paper/build.py` adds the style from `paper/report-draft1.html` and prints it to PDF:

```bash
cd paper && python3 build.py body3.html final-candidate.pdf --author "Junhyun Park · Independent" --code "Code: <this repository>"
```

`report-draft1.html`, `report.html` and `body2.html` are earlier drafts, kept for the record.

## Known pitfalls (found while building this)

- Claude Code transcripts contain records that replay earlier requests without timestamps (`api-request-blob`).
  Counting tool calls in them doubles everything. `extract.py` counts only `assistant` / `user` records.
- Claude Code removes transcripts about 30 days after their last activity, so run the extraction early.
- Commits made with `git commit -q` print no hash; commits are tied to sessions by subject and time instead.

## Data

Not released: the logs come from a private workspace. The report gives only aggregates.
