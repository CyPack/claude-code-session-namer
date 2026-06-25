# cc-session-namer

Automatically give your **unnamed Claude Code chat sessions** a meaningful
`PROJECT: TOPIC` title, so your session list stops being a wall of
indistinguishable first-prompts. Runs on a cron schedule, preserves chronology,
skips junk/test chats, and is idempotent.

> Built for people who run *many* Claude Code sessions across many projects. After
> a while, `cc l` (or any session lister) becomes unreadable: dozens of sessions
> all showing their raw first prompt. You can't tell which project is at which
> stage. This names them for you, in the background.

It pairs naturally with the
[`Session List Turbo`](https://github.com/CyPack/claude-code-power-tweaks)
tweak, but works with any setup where sessions live in `~/.claude/projects/`.

---

## What it does

For each unnamed session it reads the **first prompt, last prompt, and last AI
reply**, asks a headless `claude -p` call to infer a short title, and appends a
`custom-title` line to the session's `.jsonl`. The result shows up as the
session's name in your lister.

Example output titles:

```
VOORINFRA: SCU W26 AUTO-UPLOAD
T4F: BAM/SOR CROSS-CHECK PIPELINE
KG: SESSION INGEST CRON SETUP
SYSTEM: TRACKPAD JITTER ROOT-CAUSE
```

(Titles are uppercase `PROJECT: TOPIC`. The default prompt is tuned for
Turkish + English mixed usage; edit `PROMPT_HEADER` in `name_sessions.py` to taste.)

## The 5-layer pipeline

| Layer | Stage | What happens |
|-------|-------|--------------|
| 1 | **Discover** | Scan slug dirs. HOME = last 30 days; other project dirs = last 2 chats each. |
| 2 | **Filter** | Deterministically drop test/empty/benchmark chats (`is_garbage`) — they never reach the LLM. |
| 3 | **Infer** | One batched headless `claude -p` call returns `PROJECT: TOPIC` or `skip` per session. |
| 4 | **Write** | Append `custom-title` **and** fix `mtime` back to the last real event — **chronology preserved**. |
| 5 | **Verify** | Confirm each touched slug's files are still in monotonic mtime order. |

See [`docs/DESIGN.md`](docs/DESIGN.md) for the full rationale and the hard-won
pitfalls.

## Why the mtime-fix matters (the non-obvious trick)

Most session listers sort by file `mtime`. Appending a `custom-title` line
touches the file, pushing every freshly-named session to the **top** of your list
and destroying chronology. So after writing, the script resets each file's
`mtime` back to its **last real event's timestamp** (`os.utime`), leaving content
untouched. Without this, naming your sessions would scramble their order — a real
bug we hit and fixed. This is the single most important detail in the project.

## Install

```bash
git clone https://github.com/CyPack/claude-code-session-namer.git
cd claude-code-session-namer
./install.sh          # copies scripts, runs self-test, installs cron
# choose your own schedule:
CRON_HOURS="9,13,18,22" ./install.sh
```

## Usage

```bash
python3 ~/.cc-session-namer/name_sessions.py --selftest   # pure-function tests (must be GREEN)
python3 ~/.cc-session-namer/name_sessions.py --dry-run    # preview (calls LLM, writes nothing)
python3 ~/.cc-session-namer/name_sessions.py --commit     # actually write (with mtime-fix)
~/.cc-session-namer/run-namer.sh                          # cron entry-point (also runs manually)
```

## Safety / design rules

- **Append-only.** It only ever *adds* a `custom-title` line; it never deletes or
  rewrites transcript content. (Compatible with keeping full chat history.)
- **Idempotent.** Already-named sessions are skipped. LLM-skipped sessions are
  recorded in `skipped.txt` and never re-sent.
- **Active sessions protected.** A session touched in the last 15 minutes is left
  for the next run (it might be open right now).
- **Trusted LLM only.** It uses `claude -p` because chat content can be private.
  Don't route this through an untrusted/third-party model.
- **Privacy.** `namer.log`, `skipped.txt`, the `work/` dir and any caches are
  `.gitignore`d — they can contain bits of your chats and must never be committed.

## Requirements & portability

- Python 3.8+ (stdlib only).
- The `claude` CLI on `PATH`, logged in (used in headless `-p` mode).
- A scheduler for automation (optional — you can run it manually).

Built on **fish 4.6 + kitty + Fedora 44 + cron**, but nothing is tied to that:
`name_sessions.py` is plain Python and shell/terminal-agnostic; `run-namer.sh`
runs under `bash`. The only host-specific piece is the **scheduler** — `install.sh`
uses `crontab`; on systemd-only hosts wrap `run-namer.sh` in a `systemd --user`
timer, and on macOS use `launchd`/`cron`. An agent setting this up elsewhere should
detect the host first ([`AGENTS.md`](AGENTS.md) Step 0).

## License

MIT © 2026 CyPack. See [LICENSE](LICENSE).
