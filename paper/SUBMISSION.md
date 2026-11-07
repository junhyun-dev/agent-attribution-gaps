# Apart submission form — text to paste

**Project title:** Who Wrote This? What an Overseer Can Attribute in a Real Swarm of 1,500 Coding Agents

**Tracks:** Multi-Agent Oversight and Control (primary) · The Science of Agent Networks

**Summary (short abstract):**
We measured how much of a real, cooperative swarm's activity an overseer can attribute to a specific agent using only the logs that today's coding-agent harnesses produce. Over 38 days, 1,576 agent instances (Claude Code and Codex sessions, subagents and worker runs) shared one git working tree and made 7,986 commits. Harness shell logs tie 71% of commits to a session, but only 12% of committed file changes appear as edit-tool events (agents mostly change files through shell commands and scripts), and only 33% of worker runs can be traced to the session that launched them. Commits that carry another role's changes are concentrated around the coordinator agent (74–98% of cases); elsewhere they stay below 3%. We release the extraction pipeline and recommend launcher IDs, harness-written commit trailers, change capture below the tool layer, and logs kept out of agents' reach.

**PDF:** report.pdf

**Optional:** project code = [GitHub link, after the author decides] · slides / video = none
