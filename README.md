# Who wrote this? — attribution in a real swarm of coding agents

Code for the Swarm Alignment Hackathon (Apart Research, 6–8 Nov 2026) report
*"Who Wrote This? What an Overseer Can Attribute in a Real Swarm of 1,500 Coding Agents"*.

**All code in this repository was written during the hackathon (7–8 Nov 2026). Nothing is pre-existing.**
The swarm, its working rules and its logs existed before the hackathon; they are the object of study and are not included here.

## What it does

Turns harness logs from Claude Code (session transcripts, including subagents) and Codex (rollout logs), plus `git log`,
into flat who-wrote-what-when tables, and computes how much of the swarm's activity an overseer can attribute.
It reads metadata only (tool names, file paths, timestamps, outcomes, commit subjects, launch/commit commands)
and never stores message bodies or file contents.

| Step | File | Output |
|---|---|---|
| 1. Extract | `src/extract.py` | `agents.csv`, `writes.csv`, `commits.csv`, `messages.csv`, `launches.csv` |
| 2. Link workers to launchers | `src/delegation.py` | (used by step 3) |
| 3. Measures | `src/measure.py` (+ `src/analyze.py`) | `results.json`, `h3_files.csv`, `known_writes.csv`, `daily.csv` |
| 4. Robustness | `src/robust.py` | before/after variants |
| 5. Log-folder access | `src/logsurface.py` | counts of agent calls touching the log folders |
| 6. Figures | `src/figures.py` | `fig1_activity.png`, `fig2_observability.png`, `fig3_crossrole.png` |

```bash
python3 -m venv .venv && .venv/bin/pip install pandas matplotlib scipy
.venv/bin/python src/extract.py --out out/run            # reads ~/.claude/projects/<repo> and ~/.codex/sessions
git -C <repo> log --since=2026-09-01 --format='@@%H%x09%at%x09%an%x09%s' --name-only --no-renames > out/run/gitlog.txt
.venv/bin/python src/measure.py --data out/run --until "2026-11-07 07:00"
.venv/bin/python src/robust.py out/run
.venv/bin/python src/figures.py out/run out/fig
```

Paths in `extract.py` (`REPO`, default log folders) point at the author's machine; change them for your own swarm.

## Known pitfalls (found while building this)

- Claude Code transcripts contain records that replay earlier requests without timestamps (`api-request-blob`).
  Counting tool calls in them doubles everything. `extract.py` counts only `assistant` / `user` records.
- Claude Code removes transcripts about 30 days after their last activity, so run the extraction early.
- Commits made with `git commit -q` print no hash; commits are tied to sessions by subject + time instead.

## Data

Not released: the logs come from a private workspace. The report gives only aggregates.
