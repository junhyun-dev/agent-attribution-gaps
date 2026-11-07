"""Do agents touch the harness log folders? Counts only (no command text is printed)."""
import glob
import json
import os
import re
import sys
from collections import Counter

HOME = os.path.expanduser("~")
LOGDIR = re.compile(r"\.claude/projects|\.codex/sessions")
MUT = re.compile(r"\b(rm|mv|sed -i|truncate|shred)\b|>\s*\S*(\.claude/projects|\.codex/sessions)")
c = Counter()
dirs = [os.path.join(HOME, ".claude/projects/-home-junhyun-personal")] + glob.glob(os.path.join(HOME, ".claude/projects/-home-junhyun-dev*"))
for d in dirs:
    for f in glob.glob(os.path.join(d, "*.jsonl")) + glob.glob(os.path.join(d, "*/subagents/*.jsonl")):
        hit_read = hit_mut = False
        for line in open(f, errors="replace"):
            if "projects" not in line and "sessions" not in line:
                continue
            try:
                dd = json.loads(line)
            except ValueError:
                continue
            if dd.get("type") != "assistant" or (dd.get("timestamp") or "") >= "2026-11-07T07:00":
                continue
            msg = dd.get("message")
            if not isinstance(msg, dict) or not isinstance(msg.get("content"), list):
                continue
            for b in msg["content"]:
                if b.get("type") != "tool_use":
                    continue
                inp = json.dumps(b.get("input", {}), ensure_ascii=False)
                if LOGDIR.search(inp):
                    c["calls_touching_log_dirs"] += 1
                    hit_read = True
                    if b.get("name") in ("Edit", "Write") and "/memory/" in inp:
                        c["memory_file_edits"] += 1
                    elif b.get("name") in ("Edit", "Write") or MUT.search(inp):
                        c["calls_that_could_modify"] += 1
                        hit_mut = True
        c["agents"] += 1 if os.path.getsize(f) else 0
        c["agents_touching_log_dirs"] += hit_read
        c["agents_modifying_like"] += hit_mut
print(dict(c))
