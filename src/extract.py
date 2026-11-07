"""Turn harness logs (Claude Code transcripts, Codex rollouts) into flat event tables.

Written during the Swarm Alignment Hackathon (Nov 2026). Reads logs only; never
stores message bodies or file contents, only who touched which path when.

Outputs (CSV, under --out):
  agents.csv    one row per agent instance (a transcript / rollout file)
  writes.csv    structured file-write attempts with outcome
  commits.csv   git commits whose harness log shows the agent ran `git commit`
  messages.csv  cross-session messages sent / received (no bodies)
"""
import argparse
import csv
import glob
import json
import os
import re
import sys

HOME = os.path.expanduser("~")
REPO = os.path.join(HOME, "personal")

COMMIT_RE = re.compile(r"\[(?:main|master)(?: \(root-commit\))? ([0-9a-f]{7,40})\]")
PATCH_FILE_RE = re.compile(r"\*\*\* (Add|Update|Delete) File: (.+?)(?:\\n|\n|$)")
MSG_RE = re.compile(r"""-m\s+(?:\\?["'])?(?:\$\(cat <<'?EOF'?\s*)?([^\n"'$\\]{4,80})""")
LAUNCH_RE = re.compile(r"codex_oneoff|codex exec|codex -a|codex --")
MOVE_RE = re.compile(r"\*\*\* Move to: (.+?)(?:\\n|\n|$)")
STALE_MARK = "modified since read"


def norm(path, cwd=None):
    if not path:
        return None
    path = path.strip().strip('"').strip("'")
    if not os.path.isabs(path):
        if not cwd:
            return "rel:" + path
        path = os.path.join(cwd, path)
    path = os.path.normpath(path)
    if path == REPO or path.startswith(REPO + "/"):
        return os.path.relpath(path, REPO)
    if path.startswith(HOME + "/"):
        return "~/" + os.path.relpath(path, HOME)
    return "abs:" + path


def text_of(content):
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for b in content:
            if isinstance(b, dict):
                out.append(str(b.get("text") or b.get("content") or ""))
            else:
                out.append(str(b))
        return "\n".join(out)
    return str(content)


def commit_shape(cmd):
    """How the commit chose its files: explicit paths, everything, or whatever was staged (shared index)."""
    seg = cmd[max(0, cmd.find("commit") - 400):]
    if re.search(r"git (add (-A|--all|\.)(\s|$|&|;)|commit[^\n]*\s-a[m]?\b)", cmd):
        return "add-all"
    if re.search(r"commit[^\n|;&]*\s--\s+\S", seg) or re.search(r"commit[^\n|;&]*\s--\s*(\\\n|$)", seg):
        return "explicit-paths"
    if re.search(r"git add \S", cmd):
        return "add-then-commit"
    return "staged-only"


def commit_msg(cmd):
    m = MSG_RE.search(cmd)
    return m.group(1).strip() if m else ""


class Tables:
    def __init__(self):
        self.agents, self.writes, self.commits, self.messages, self.launches = [], [], [], [], []


def claude_file(path, kind, parent, T):
    agent_id = os.path.splitext(os.path.basename(path))[0]
    name, cwd, first, last = None, None, None, None
    pending = {}  # tool_use_id -> (ts, tool, paths, is_commit)
    n = 0
    with open(path, errors="replace") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            t = d.get("type")
            ts = d.get("timestamp")
            if t in ("agent-name", "custom-title"):
                name = d.get("agentName") or d.get("customTitle") or name
                continue
            if t == "queue-operation" and d.get("operation") == "enqueue":
                c = d.get("content") or ""
                m = re.search(r'<cross-session-message[^>]*from-name="([^"]*)"', c)
                if m and ts:
                    T.messages.append(dict(agent_id=agent_id, ts=ts, direction="recv", peer=m.group(1)))
                continue
            if ts:
                first = first or ts
                last = ts
            cwd = d.get("cwd") or cwd
            if t not in ("assistant", "user"):
                continue  # e.g. 'api-request-blob' replays earlier tool calls without timestamps
            msg = d.get("message")
            if not isinstance(msg, dict) or not isinstance(msg.get("content"), list):
                continue
            for b in msg["content"]:
                bt = b.get("type")
                if bt == "tool_use":
                    n += 1
                    nm, inp = b.get("name"), b.get("input") or {}
                    if nm in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                        p = inp.get("file_path") or inp.get("notebook_path")
                        pending[b.get("id")] = (ts, nm, [norm(p, cwd)], False)
                    elif nm == "Bash" and re.search(r"git( -C \S+)? commit", str(inp.get("command", ""))):
                        pending[b.get("id")] = (ts, "Bash", [commit_msg(str(inp.get("command", ""))), commit_shape(str(inp.get("command", "")))], True)
                    if nm == "Bash" and LAUNCH_RE.search(str(inp.get("command", ""))):
                        T.launches.append(dict(agent_id=agent_id, ts=ts, cmd=str(inp.get("command", ""))[:1500]))
                    if nm == "SendMessage":
                        T.messages.append(dict(agent_id=agent_id, ts=ts, direction="send", peer=str(inp.get("to", ""))[:80]))
                elif bt == "tool_result":
                    info = pending.pop(b.get("tool_use_id"), None)
                    if not info:
                        continue
                    cts, tool, paths, is_commit = info
                    out = text_of(b.get("content"))
                    if is_commit:
                        T.commits.append(dict(agent_id=agent_id, ts=cts, hash=" ".join(COMMIT_RE.findall(out)), msg=paths[0] or "", shape=paths[1]))
                        continue
                    err = bool(b.get("is_error")) or out.startswith("<tool_use_error>")
                    kind_err = "stale" if STALE_MARK in out else ("error" if err else "")
                    for p in paths:
                        T.writes.append(dict(agent_id=agent_id, ts=cts, path=p, tool=tool, ok=int(not err), err=kind_err))
    T.agents.append(dict(agent_id=agent_id, kind=kind, name=name or "", parent=parent or "", cwd=norm(cwd) if cwd else "",
                         first_ts=first or "", last_ts=last or "", n_tool_calls=n))


def codex_file(path, T):
    agent_id = "cx-" + os.path.splitext(os.path.basename(path))[0][-36:]
    cwd, origin, first, last, name = None, "", None, None, ""
    pending = {}
    n = 0
    with open(path, errors="replace") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            ts = d.get("timestamp")
            p = d.get("payload") if isinstance(d.get("payload"), dict) else {}
            t, pt = d.get("type"), p.get("type")
            if t == "session_meta":
                cwd = p.get("cwd") or cwd
                origin = p.get("originator") or origin
                name = p.get("thread_name") or name
                continue
            if t == "turn_context":
                cwd = p.get("cwd") or cwd
            if ts:
                first = first or ts
                last = ts
            if pt in ("function_call", "custom_tool_call", "local_shell_call"):
                n += 1
                raw = p.get("arguments") or p.get("input") or json.dumps(p.get("action") or "")
                raw = raw if isinstance(raw, str) else json.dumps(raw)
                wd = cwd
                m = re.search(r'"?workdir"?\s*:\s*"([^"]+)"', raw)
                if m:
                    wd = m.group(1)
                files = []
                for _op, f in PATCH_FILE_RE.findall(raw):
                    files.append(norm(f.strip(), wd))
                for f in MOVE_RE.findall(raw):
                    files.append(norm(f.strip(), wd))
                is_commit = bool(re.search(r"git( -C \S+)? commit", raw))
                if files or is_commit:
                    pending[p.get("call_id")] = (ts, files, (commit_msg(raw), commit_shape(raw)) if is_commit else None)
            elif pt in ("function_call_output", "custom_tool_call_output", "local_shell_call_output"):
                info = pending.pop(p.get("call_id"), None)
                if not info:
                    continue
                cts, files, cmsg = info
                out = p.get("output")
                out = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
                if cmsg is not None:
                    T.commits.append(dict(agent_id=agent_id, ts=cts, hash=" ".join(COMMIT_RE.findall(out)), msg=cmsg[0], shape=cmsg[1]))
                failed = "verification failed" in out or "Failed to apply patch" in out
                for f in files:
                    T.writes.append(dict(agent_id=agent_id, ts=cts, path=f, tool="apply_patch", ok=int(not failed),
                                         err="error" if failed else ""))
    T.agents.append(dict(agent_id=agent_id, kind="codex:" + (origin or "?"), name=name, parent="", cwd=norm(cwd) if cwd else "",
                         first_ts=first or "", last_ts=last or "", n_tool_calls=n))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--claude-dir", action="append",
                    help="Claude Code project log folder(s); default: the personal repo and ~/dev projects")
    ap.add_argument("--codex-dir", default=os.path.join(HOME, ".codex/sessions"))
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    T = Tables()
    dirs = args.claude_dir or ([os.path.join(HOME, ".claude/projects/-home-junhyun-personal")] +
                               sorted(glob.glob(os.path.join(HOME, ".claude/projects/-home-junhyun-dev*"))))
    for cdir in dirs:
        for f in sorted(glob.glob(os.path.join(cdir, "*.jsonl"))):
            claude_file(f, "claude", None, T)
            sid = os.path.splitext(os.path.basename(f))[0]
            for sf in sorted(glob.glob(os.path.join(cdir, sid, "subagents", "*.jsonl"))):
                claude_file(sf, "claude:sub", sid, T)
        print(f"claude dir done {cdir}", file=sys.stderr)
    cx = sorted(glob.glob(os.path.join(args.codex_dir, "**", "*.jsonl"), recursive=True))
    for i, f in enumerate(cx):
        codex_file(f, T)
        if i % 100 == 0:
            print(f"codex {i+1}/{len(cx)}", file=sys.stderr)
    os.makedirs(args.out, exist_ok=True)
    for nm in ("agents", "writes", "commits", "messages", "launches"):
        rows = getattr(T, nm)
        if not rows:
            continue
        with open(os.path.join(args.out, nm + ".csv"), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(nm, len(rows), file=sys.stderr)


if __name__ == "__main__":
    main()
