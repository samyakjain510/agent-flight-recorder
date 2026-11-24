"""Copy a Claude Code project folder with personal data removed, for a public demo.

    python3 scripts/scrub_transcripts.py ~/.claude/projects/<project-folder> examples/demo-run \
        --forbid "Your Name" --forbid other-word

Claude Code transcripts hold more than the conversation. Besides your paths and user
name, they carry your account and organization ids, your email address (as context
for the model), your machine's environment, snapshots of Claude Code's system prompt,
and request metadata. This script keeps only what flightrec reads:

- Files: session transcripts, subagent transcripts and their .meta.json files. Other
  files Claude Code keeps in the folder are left behind.
- Records: the types flightrec reads (messages, attachments, compaction markers,
  Claude Code's totals, fork links, titles). Request metadata, data-flow, file
  history and unknown record types are dropped.
- Attachments: a few harmless kinds are kept whole (date, model, token reminders,
  and anything carrying a subagent's hand-back or a person's message). Every other
  attachment is reduced to {"type": ..., "scrubbed": true}, keeping its place and time.
- Fields: account and organization ids are removed from every record.
- Text: your home folder becomes /home/demo, your user name "demo", this computer's
  name "demo-host", and every email address user@example.com. Add more with
  --replace OLD=NEW.

Then it checks the copy and exits with an error, printing only counts and never
text, if your home folder, user name, computer name, any --forbid word
(case-insensitive), or a real email address is still there.

It does not judge whether the content of a run is fit to publish. Make demo runs on
purpose, on public code, and read the result before sharing it.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
import socket
import sys
from typing import Dict, List, Tuple

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SAFE_EMAIL_DOMAINS = ("example.com", "anthropic.com")

KEEP_RECORDS = {"assistant", "user", "attachment", "system", "cost-state", "derived-from",
                "custom-title", "ai-title", "summary", "queue-operation"}
KEEP_ATTACHMENTS = {"date", "model", "total_tokens_reminder", "queued_command"}
DROP_FIELDS = {"accountUuid", "organizationUuid"}


def default_replacements() -> List[Tuple[str, str]]:
    home = os.path.expanduser("~")
    user = getpass.getuser()
    host = socket.gethostname().split(".")[0]
    pairs = [(home, "/home/demo"), (home.replace("/", "-"), "-home-demo"), (user, "demo")]
    if host and host.lower() != "localhost":
        pairs.append((host, "demo-host"))
    return pairs


def scrub_text(text: str, pairs: List[Tuple[str, str]]) -> str:
    for old, new in sorted(pairs, key=lambda p: -len(p[0])):  # longest first, so paths go before names
        text = text.replace(old, new)
    return EMAIL_RE.sub(lambda m: m.group() if m.group().split("@")[1].endswith(SAFE_EMAIL_DOMAINS)
                        else "user@example.com", text)


def scrub_record(r: dict) -> dict:
    """None means drop the record."""
    if r.get("type") not in KEEP_RECORDS:
        return None
    for f in DROP_FIELDS:
        r.pop(f, None)
    a = r.get("attachment")
    if r.get("type") == "attachment" and isinstance(a, dict):
        if a.get("type") not in KEEP_ATTACHMENTS and "origin" not in a:
            r["attachment"] = {"type": a.get("type"), "scrubbed": True}
    return r


def leaks(text: str, words: List[str]) -> Dict[str, int]:
    low = text.lower()
    found = {w: low.count(w.lower()) for w in words if w and w.lower() in low}
    emails = [e for e in EMAIL_RE.findall(text) if not e.split("@")[1].endswith(SAFE_EMAIL_DOMAINS)]
    if emails:
        found["<email address>"] = len(emails)
    for f in DROP_FIELDS:
        if f'"{f}"' in text:
            found[f"<{f}>"] = text.count(f'"{f}"')
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("source", help="a project folder under ~/.claude/projects")
    ap.add_argument("dest", help="output root; files go to DEST/projects/<folder>/")
    ap.add_argument("--replace", action="append", default=[], metavar="OLD=NEW")
    ap.add_argument("--forbid", action="append", default=[], metavar="WORD",
                    help="fail if this word is still present after scrubbing (case-insensitive)")
    args = ap.parse_args()

    pairs = default_replacements() + [tuple(x.split("=", 1)) for x in args.replace if "=" in x]
    watch = [os.path.expanduser("~"), getpass.getuser(), socket.gethostname().split(".")[0]] + args.forbid
    src = os.path.abspath(os.path.expanduser(args.source))
    out_dir = os.path.join(args.dest, "projects", scrub_text(os.path.basename(src.rstrip("/")), pairs))
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)

    total: Dict[str, int] = {}
    files = kept = dropped = 0
    for dirpath, _, names in os.walk(src):
        rel = os.path.relpath(dirpath, src)
        target_dir = os.path.join(out_dir, scrub_text(rel, pairs)) if rel != "." else out_dir
        for name in sorted(names):
            if not (name.endswith(".jsonl") or name.endswith(".meta.json")):
                continue
            with open(os.path.join(dirpath, name), encoding="utf-8", errors="replace") as f:
                raw = f.read()
            if name.endswith(".jsonl"):
                lines = []
                for line in raw.splitlines():
                    try:
                        r = scrub_record(json.loads(line))
                    except json.JSONDecodeError:
                        r = None
                    if r is None:
                        dropped += 1
                        continue
                    kept += 1
                    lines.append(json.dumps(r, ensure_ascii=False))
                raw = "\n".join(lines) + "\n"
            text = scrub_text(raw, pairs)
            os.makedirs(target_dir, exist_ok=True)
            with open(os.path.join(target_dir, scrub_text(name, pairs)), "w", encoding="utf-8") as f:
                f.write(text)
            files += 1
            for k, v in leaks(text, watch).items():
                total[k] = total.get(k, 0) + v
    print(f"copied {files} files to {out_dir}: kept {kept} records, dropped {dropped}")
    if total:
        print("STILL PRESENT after scrubbing (counts only):")
        for k, v in total.items():
            print(f"  {'<forbidden word ' + str(watch.index(k)) + '>' if k in watch else k}: {v}")
        return 1
    print("check passed: no home folder, user name, computer name, account ids, forbidden words or email addresses")
    return 0


if __name__ == "__main__":
    sys.exit(main())
