# AGENTS.md

Notes for AI coding agents (Claude Code, Cursor, etc.) working on this repo.
Humans: skim `README.md` first; this file assumes you already know what the
project does.

## Architecture in one paragraph

`tagtool.py` is the whole library. It reads audio tags via **mutagen**,
computes per-file diffs between a PRE and POST tree, and renders a
self-contained HTML report with a GitHub-style review UI. The pipeline is
deliberately split into a **slow** data step and a **fast** rendering step:

```
extract_data(pre_root, post_root, rels)  ->  dict   (slow: walks both trees)
render_html(data, out_path)              ->  HTML   (fast: just builds strings)
html_report(...)                         ->  HTML   (convenience: both, in memory)
```

The CLI exposes the same trio as `extract` / `render` / `report` subcommands.
`run-bulk.sh` is an unrelated bash helper that drives OneTagger over an
already-running container in batches; it doesn't share state with `tagtool.py`.

## The data dict (the contract between `extract` and `render`)

```python
{
  "version": 1,
  "generated": "ISO-8601 timestamp",
  "pre_root": "/path",
  "post_root": "/path",
  "n_total":   <int — files walked>,
  "files": [
    {
      "rel": "rel/path.aiff",
      "marker_only": <bool — only diff is +TXXX:1T_TAGGEDDATE>,
      "changes": [
        ["+", "<tag>", "<new>"],
        ["-", "<tag>", "<old>"],
        ["~", "<tag>", "<old>", "<new>"],
      ]
    }, ...
  ],
  "unmatched": [
    {"rel": "rel/path.aiff", "tags": {"title": "...", "artist": "...", ...}}
  ],
  "field_counts": {"genre": <int>, ...},
  "tc": {"added": <int>, "changed": <int>, "removed": <int>}
}
```

This is the JSON written by `extract`. `render` reads it and builds HTML.
**Do not break this schema** without bumping `version` — cached `data.json`
files (gitignored) will be reused across renders.

## Iteration loop (this is the point of the split)

When iterating on the HTML/CSS/JS, **do not re-extract.** That walks 3,800+
files with mutagen and takes ~30 seconds. Instead:

```bash
python tagtool.py extract /tank/music /tank/music-ot data.json < file-list.txt   # ~30s, once
python tagtool.py render  data.json report.html                                   # ~0.1s, every change
```

Most edits to `tagtool.py` only touch the rendering — tweak, re-render in
~100ms, reload the HTML, repeat. Only re-run `extract` when the on-disk tags
genuinely changed (another OneTagger pass, manual fixes, etc.) or when you
modified `extract_data` itself.

## Conventions worth knowing before you change things

- **Tag-reading layer** (`read_tags`) normalises ID3 frames, MP4 atoms, and
  freeform `----:com.apple.iTunes:NAME` keys to consistent friendly names via
  `ID3_MAP` / `MP4_MAP` / `MP4_FREEFORM`. AIFF/MP3 `title` and M4A `©nam`
  should both come out as `"title"`. If you add a new tag the report cares
  about, extend those maps so the same field name surfaces across formats.
- **Marker-only detection** is `len(changes) == 1 and changes[0][0] == "+"
  and changes[0][1] == "TXXX:1T_TAGGEDDATE"`. If OneTagger's marker frame
  changes name upstream, update both that check *and* the equivalent check
  in `extract_data` that classifies unmatched files.
- **`KEY_FIELDS`** (module-level) is the canonical list of tag fields the
  unmatched section's "Currently has" column considers. Adding more fields
  there means more chips show up in that cell — also widen the column.
- **Don't pre-render HTML inside `extract_data`.** The data step must produce
  JSON-serialisable plain values (lists, dicts, strings, ints, bools).
  Pre-rendered HTML belongs in `render_html` only.
- **All UI persistence** (mark-as-reviewed state, `show marker-only` toggle,
  `show reviewed` toggle) is in browser `localStorage` keyed by
  `tagdiff:dismissed` / `tagdiff:showDismissed` / `tagdiff:showMarkerOnly`.
  Dismissals are keyed by `<rel-path>::<tag-name>` so they survive report
  re-generation as long as the same `(file, tag)` pairs appear.

## What lives where

| File | Why it exists |
|---|---|
| `tagtool.py` | The whole tool. CLI + library, all in one. |
| `run-bulk.sh` | Bash driver for `onetagger-cli` in batches; unrelated to tagtool.py. |
| `examples/docker-compose.yml` | Generic OneTagger container stack (Tier-0 hardened). User adapts paths. |
| `examples/auto-tag.example.json` | The autotagger profile that worked for us; **secrets blanked**. |
| `requirements.txt` | Just `mutagen`. |
| `assets/screenshot.jpg` | README hero image. |
| `data.json` | (gitignored) The `extract`'s JSON cache — library-specific. |
| `report*.html` | (gitignored) Generated outputs. |

## Conventions you should NOT change without good reason

- **Single-file `tagtool.py`.** No package layout. Easy to drop into anywhere.
- **No test suite.** The renderer is exercised manually via the iteration
  loop and the comparison "does the diff in `report.html` match what
  `mutagen-inspect` shows for a few sample files." If you add tests, keep
  them quick and deterministic — don't require a 3,800-file fixture.
- **No external HTML/CSS/JS frameworks.** Single file, hand-written CSS, a
  vanilla-JS IIFE. Easy to scp; easy to read; works offline.
- **Secrets stay out of `examples/`.** The audit pattern: `grep -inE
  'token|secret|api[_-]?key|password' examples/` should only ever match
  empty-string fields and explanatory comments.

## Git/commit hygiene

- Author email is `secret.registry@pm.me` (the project owner's Proton
  address). Use it for in-session commits; don't switch to a different
  identity.
- Commit messages: imperative subject, short paragraph explaining *why*
  (not just *what* — the diff says what).
- Co-Authored-By trailer for agent-made commits:
  `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`.
