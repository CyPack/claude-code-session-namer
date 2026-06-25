---
name: cc-session-namer
description: >
  Give unnamed Claude Code chat sessions a meaningful `PROJECT: TOPIC` title via a
  headless LLM call, so you can tell which project is at which stage. Preserves list
  chronology (mtime-fix), drops test/empty/broken chats, idempotent. Works manually
  or via cron. Covers the HOME slug + the last N chats of each project dir.
  TRIGGERS: "name my unnamed chats", "session rename", "auto-title sessions",
  "which project which stage", "run the namer", "label cc l sessions".
  CONTEXT: when the user wants to name/clean up unnamed sessions in their session
  list, or manage the naming automation (cron).
---

# cc-session-namer (skill)

Unnamed chats in your session list show only their raw first prompt → once they
pile up you can't tell which project is at which stage. This skill detects unnamed
sessions and generates a meaningful `PROJECT: TOPIC` title from the first/last
prompt + last AI reply.

## Architecture (5 layers)

| Layer | Does |
|-------|------|
| 1. Discover | Scan slug dirs; HOME = last 30 days, other dirs = last 2 chats each |
| 2. Filter | Drop test/empty/broken/benchmark chats deterministically (`is_garbage`) — never sent to the LLM |
| 3. Infer | Headless `claude -p` batch → `PROJECT: TOPIC` or skip |
| 4. Write | Append `custom-title` + `os.utime` mtime-fix → **chronology preserved** |
| 5. Verify | Confirm each touched slug is in monotonic mtime order |

## Files

- **Core:** `~/.cc-session-namer/name_sessions.py` (pure Python, `--selftest` ≈ 26 assertions)
- **Cron wrapper:** `~/.cc-session-namer/run-namer.sh` (flock + log + timeout)
- **State:** `~/.cc-session-namer/skipped.txt` (LLM-skipped sessions — never re-sent)
- **Log:** `~/.cc-session-namer/namer.log`
- **LLM workdir:** `~/.cc-session-namer/work/` (its own `claude -p` calls — not scanned)

## Usage

```bash
python3 ~/.cc-session-namer/name_sessions.py --selftest   # pure-function tests (GREEN)
python3 ~/.cc-session-namer/name_sessions.py --dry-run    # preview (calls LLM, writes nothing)
python3 ~/.cc-session-namer/name_sessions.py --commit     # actually write (with mtime-fix)
~/.cc-session-namer/run-namer.sh                          # cron entry-point (also runs manually)
```

## Iron rules

1. **Append-only** — adds a jsonl line, never deletes (compatible with full-history preservation).
2. **Chronology** — appending breaks mtime → `os.utime(path, last_event_ts)` after every write is MANDATORY, or named sessions jump to the top of the list (a proven trap).
3. **Idempotent** — already-titled sessions are skipped; LLM-skipped go to `skipped.txt`.
4. **Junk never runs** — test/empty/broken filtered both deterministically (`is_garbage`) and via LLM `skip`.
5. **Active sessions protected** — touched within 15 min = possibly open, left for the next run.
6. **Trusted LLM** — `claude -p`. Don't use an untrusted/third-party model: chat content can be private.
7. **Temp/worker slugs excluded** — hidden `.`-dirs (`--`), `-tmp-*`, worker/benchmark dirs.

## Title format

`PROJECT: TOPIC` (uppercase, ~44 chars). In a project-dir chat: project name from
the dir, title = what was last done / which stage. In a general (home) chat: pick
a sensible PROJECT label from the topic.
