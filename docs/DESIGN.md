# Design & Pitfalls

The reasoning behind cc-session-namer, and the non-obvious traps it was built to
avoid. If you fork or port it, read this first.

## The problem, precisely

Claude Code stores each session as a `.jsonl` under
`~/.claude/projects/<slug>/<uuid>.jsonl`. A session has no name unless you give it
one. A session lister that sorts by `mtime` and shows the first user prompt
becomes unusable once you have hundreds of sessions: you can't tell which project
is at which stage.

A session is "named" by appending one line:

```json
{"type": "custom-title", "customTitle": "PROJECT: TOPIC", "sessionId": "<uuid>"}
```

The lister reads every line; the **last** `custom-title` wins. That makes naming
**append-only and idempotent** — exactly the properties we want for something that
touches real history.

## The 5 layers

### 1. Discover (`select_candidates`)
- HOME slug: candidates from the last `HOME_WINDOW_DAYS` (default 30).
- Every other project slug: only the last `OTHER_LAST_N` (default 2) chats.
- Files are mtime-sorted, so we can stop early once outside the window.

### 2. Filter (`is_garbage`)
Deterministic rejection of test/benchmark/empty chats **before** spending an LLM
call: model-identity probes, trivial math, `ping`/`pong`, `GLM_ROUTE_OK`-style
markers, code-benchmark prompts, empty sessions, known API-error replies. This
keeps cost down and avoids naming throwaway sessions.

### 3. Infer (`call_llm`)
One **batched** headless `claude -p` call for all candidates, returning a JSON
array of `{"i": N, "name": "..."}` or `{"i": N, "skip": true}`. Batching matters:
one call for 25 sessions, not 25 calls.

### 4. Write (`write_name`) — the critical layer
Append the `custom-title` line, **then immediately fix mtime** (see below).

### 5. Verify (`verify_monotonic`)
After writing, confirm each touched slug's files are still in descending-mtime
order. The script logs `kronoloji-monotonik=True/False`.

## Pitfall #1 — appending scrambles your list (the big one)

Appending a line updates the file's `mtime` to *now*. Since listers sort by
`mtime`, every freshly-named session jumps to the **top**, destroying chronology.

**Fix:** after appending, reset the file's mtime back to the timestamp of the last
*real* event in the transcript (ignoring the `custom-title` line we just added):

```python
ts = cand["last_evt_epoch"]          # last non-custom-title event timestamp
if ts:
    at = os.path.getatime(path)
    os.utime(path, (at, ts))         # restore mtime; content untouched
```

Without this, the tool actively makes the list *worse*. This was a real,
reproduced bug. It is the single most important line in the project.

## Pitfall #2 — idempotency & the skip set

Re-running must be safe. Two mechanisms:
- Sessions that already have a `custom-title` are skipped (natural diff).
- Sessions the LLM marked `skip` are appended to `skipped.txt` so they're never
  re-sent (otherwise every run pays to re-evaluate the same junk).

## Pitfall #3 — don't scan the wrong slugs

`slug_excluded` filters out temporary/worker/state slugs (hidden `.`-dirs encoded
as `--` in the slug, `-tmp-*`, agent-worker dirs, benchmark dirs) and the tool's
own LLM workdir. Naming those is noise.

## Pitfall #4 — don't touch live sessions

A session whose mtime is within `ACTIVE_GRACE_SEC` (default 15 min) might be open
right now. Skip it; the next cron run will catch it once it's idle.

## Pitfall #5 — privacy of the model

Transcripts can contain private/customer data. Inference uses the user's own
`claude -p`. Do **not** route this through a third-party/untrusted model. Logs and
the skip file can also contain chat fragments → gitignored, never committed.

## Testing

`name_sessions.py --selftest` runs ~26 assertions over the pure functions
(`is_garbage`, `project_from_cwd`, `slug_excluded`, `iso_to_epoch`,
`strip_json_fence`, `clamp_title`). No network, no real data. CI-friendly.

## Tunables

| Constant | Default | Meaning |
|----------|---------|---------|
| `HOME_WINDOW_DAYS` | 30 | how far back to consider HOME chats |
| `OTHER_LAST_N` | 2 | last N chats per non-home project |
| `ACTIVE_GRACE_SEC` | 900 | skip sessions touched within this window |
| `MAX_BATCH` | 25 | max names written per run (cost cap) |
| `MAX_TITLE` | 46 | title clamp length |
| `MODEL` | `claude-sonnet-4-6` | inference model |
| `PROMPT_HEADER` | — | the title-style instruction (edit for your language/format) |
