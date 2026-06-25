# AGENTS.md — for Claude Code & other coding agents

How an autonomous agent should operate, install, and maintain `cc-session-namer`.
Humans: start at [`README.md`](README.md).

## What this project is

A cron-driven tool that names unnamed Claude Code chat sessions
(`PROJECT: TOPIC`) by inferring a title from each transcript via a headless
`claude -p` call, while preserving list chronology.

## STEP 0 — Detect the host environment first

Authored on **fish 4.6 + kitty + Fedora 44 + cron**. The reader is likely on
something else. The core (`name_sessions.py`) is plain Python and shell/terminal-
agnostic, but the **scheduler** and the `claude` CLI invocation depend on the host:

```bash
echo "$SHELL"                          # which shell (affects nothing in the python core)
uname -s                               # Linux vs Darwin(macOS)
command -v crontab && echo cron        # scheduler available?
command -v systemctl && echo systemd
command -v claude                      # the headless `claude -p` CLI must be on PATH + logged in
python3 --version                      # 3.8+
```

Adapt:

| Host fact | Consequence |
|-----------|-------------|
| has `crontab` | `install.sh` works as-is |
| systemd-only / no crontab | wrap `run-namer.sh` in a `systemd --user` timer instead |
| macOS | use `launchd` (or `cron`) instead of `crontab`; data path is still `~/.claude/projects` |
| shell = fish/zsh/bash | doesn't matter — `run-namer.sh` runs under `/usr/bin/env bash` |
| no `claude` on PATH | the LLM inference step fails — install/login the Claude CLI first |

`install.sh` already derives `$HOME` dynamically and the cron schedule is
overridable via `CRON_HOURS`. Don't hardcode paths or a schedule.

## Operating it as an agent

1. **Self-test first.** `python3 name_sessions.py --selftest` must print
   `SELFTEST: GREEN` before you trust any run.
2. **Always dry-run before commit.** `--dry-run` shows what titles would be
   written (it does call the LLM but writes nothing). Review for sanity.
3. **Commit deliberately.** `--commit` appends `custom-title` lines and applies
   the mtime-fix. It is append-only and idempotent, but it does call the LLM
   (cost) and writes to real transcripts.
4. **Verify chronology after writing.** The script self-checks monotonic mtime
   per slug; confirm the final log line shows `kronoloji-monotonik=True`
   (chronology monotonic = true).

## Hard rules

- **Never delete or rewrite transcript content.** Only append a `custom-title`
  line. This keeps it compatible with full-history preservation.
- **Never commit logs / state / chat content.** `namer.log`, `skipped.txt`,
  `work/`, `*.db` are gitignored and contain fragments of private chats.
- **Trusted model only.** Use `claude -p` (the user's own Claude). Do not route
  transcript content through a third-party/untrusted model — it's potentially
  private data.
- **Keep it username-agnostic.** Paths derive from `$HOME` /
  `os.path.expanduser`. Don't hardcode a home path.
- **The mtime-fix is load-bearing.** If you modify the write path, you MUST keep
  resetting `mtime` to the last real event timestamp after appending, or you'll
  scramble the user's session ordering.

## Key files

| File | Role |
|------|------|
| `name_sessions.py` | core (scan / filter / infer / write / verify), `--selftest` |
| `run-namer.sh` | cron entry-point (flock + log + timeout) |
| `install.sh` | copies scripts, runs self-test, installs idempotent cron |
| `docs/DESIGN.md` | architecture + pitfalls |
| `skill/SKILL.md` | optional Claude Code skill wrapper |

## Tunables (top of `name_sessions.py`)

`HOME_WINDOW_DAYS`, `OTHER_LAST_N`, `ACTIVE_GRACE_SEC`, `MAX_BATCH`, `MAX_TITLE`,
`MODEL`, and `PROMPT_HEADER` (the title-style instruction). All have sane
defaults; adjust for your volume and language.
