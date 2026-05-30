# tag-diff

A small toolkit for batch-tagging an audio library with
[OneTagger](https://github.com/Marekkon5/onetagger) and producing a
**git-diff-style HTML report** of the before/after metadata changes.

Built originally for an A/B comparison on a ~3,800-track DJ library: clone the
dataset (ZFS), tag the clone in batches, compare against the pristine original.

![tag-diff report — GitHub-style review UI for OneTagger metadata changes](assets/screenshot.jpg?v=2)

## Components

### `tagtool.py` — read / diff / report audio tags

Tag reading via [mutagen](https://mutagen.readthedocs.io/) — handles **AIFF**,
**MP3**, **M4A**, **FLAC**, **WAV**. ID3 frames map to friendly names; MP4 atoms
and freeform `----:com.apple.iTunes:…` tags map to consistent names across formats
so the diff is readable.

Modes:

| Mode | Args | What it does |
|---|---|---|
| `dump` | `FILE…` | JSON of normalized tags per file |
| `diff` | `PRE POST` | Readable text diff of one file pair |
| `treediff` | `PRE_ROOT POST_ROOT` *(rels on stdin)* | Per-file diffs + summary in the terminal |
| `extract` | `PRE_ROOT POST_ROOT OUT.json` *(rels on stdin)* | Walk trees, dump diff dict to JSON (slow — runs mutagen) |
| `render` | `IN.json OUT.html` | Build HTML report from cached JSON (fast — no filesystem walk) |
| `report` | `PRE_ROOT POST_ROOT OUT.html` *(rels on stdin)* | Convenience: `extract` + `render` in one pass |

**Ignore patterns** (`treediff` / `extract` / `report`): rels with any path
component matching a glob are skipped before any tag I/O.

| Flag | Meaning |
|---|---|
| `--ignore GLOB` | Skip rels with any path component matching `GLOB` (repeatable; appends to defaults). |
| `--no-default-ignore` | Drop the built-in defaults — only patterns from `--ignore` apply. |

Default: `.*` — any dot-prefixed component, covering `.sync` (Resilio's
archive folder), `.DS_Store`, `._<name>` (macOS AppleDouble resource-fork
stubs), `.Trash`, etc. Use e.g. `--ignore '*.bak' --ignore '@eaDir'` to add
your own.

### HTML report (`tagtool.py report` / `render`)

GitHub-style review UI in one self-contained file:

- **Sticky top toolbar** — overall stats (`X scanned · Y changed · (M marker-only) · K unmatched · +A ~M −R changes`), `show marker-only` / `show reviewed` / `show artwork` toggles, live `reviewed: X/Y files · A/B changes` counter (gains `· N visible` when a filter is active), live `visible · doc-height-px` page-meter, `clear` button.
- **Fixed-position columns** (tag · before · after · ✓) so info stays in the same place as you scroll across files.
- **Per-row ✓** and **per-file ✓** mark-as-reviewed buttons; persisted in browser `localStorage` keyed by `file::tag` (survives page reload; regenerated reports keep the marks for the same rows).
- **Unmatched section** at the top (amber) — manual-review queue listing every file OneTagger couldn't match, with path · title · artist · album-artist · album · what tags it currently has · ✓. Its own collapsible header carries an `X/Y confirmed` progress counter. Open in Meta (Mac) / Rekordbox / any tag editor to fix them manually. Two control rows above the table: **missing-field filters** (show rows lacking values in *any* of the selected columns — OR semantics; tick album-artist + album to see rows missing either) and **column visibility toggles** (hide columns you don't care about). Both persist in `localStorage`; counter shows `… · N visible` when a filter is active.
- **File-section filters** — the main git-diff list has a parallel control panel: **field filters** (tick `Album` + `Album artist` to surface only files where OneTagger touched either, OR semantics) and an **"only files with overwrites (`~` or `−`)"** toggle for the small subset where OT replaced or removed an existing value (the high-scrutiny set when validating `overwrite: false`). State persists in `localStorage`; counter shows `… · N visible` when active.
- **Marker-only filter** — files whose only diff is OneTagger's `TXXX:1T_TAGGEDDATE` stamp (i.e. OneTagger matched but the file was already fully tagged, so nothing was actually written with `overwrite:false`) are hidden by default as review noise; `show marker-only` brings them back greyed-out.
- **Optional artwork preview** — `show artwork` reveals 48px thumbnails next to every `cover_art` change. Click → lightbox at native 256px with **`‹` / `›` buttons and `←` / `→` keyboard** to step through every visible cover in document order. Off by default — when on, only then do the ~1,000 base64 thumbnails get hydrated, so the cold report stays fast.
- **⧉ copy buttons** next to every filename — copies the basename without extension to clipboard. Useful for googling tracks while validating tag changes.
- **Filename gets a timestamp inserted automatically**: `report.html` → `report-YYYY-MM-DD-HHMM.html`, plus a stable `report.html` copy refreshed to the latest.

### Fast iteration with `extract` + `render`

The slow part of building a report is mutagen reading tags from every file in both trees. The cheap part is composing the HTML. Splitting them lets you cache the slow part:

```bash
# Slow: walk files, dump the diff dict to JSON
python tagtool.py extract /path/pre /path/post data.json < file-list.txt

# Fast: build HTML from the cache (~0.1s for 3,800 files)
python tagtool.py render data.json out.html
```

Re-run `extract` only when the on-disk tags actually change (another tagging pass, manual edits). Iterate on the HTML/CSS/JS in `tagtool.py` and hammer `render` for instant feedback. The JSON cache is gitignored (it's library-specific).

### `run-bulk.sh` — batch OneTagger driver

Bash driver that runs OneTagger's CLI in batches inside a running container.

- Reads `b*` batch files from `$BATCH_DIR` (default `/tmp/bulk-batches`).
- Pipes container-rooted paths to `docker exec -i $CONTAINER` and invokes `onetagger-cli autotagger` with the profile at `$CONFIG`.
- Logs a one-line per-batch summary + the raw OneTagger output to `$LOG` (default `/tmp/ot-bulk.log`).
- Continues on per-batch errors; **resumable** when paired with `skipTagged:true` in the OneTagger profile.
- `DRY_RUN=1` swaps the tagger for an echo — exercises the whole pipeline (path prefixing, `docker exec` plumbing, summary, totals) without writing any tags.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# generate an HTML report between two parallel music trees
.venv/bin/python tagtool.py report /path/pre /path/post out.html < file-list.txt

# bulk-tag a tree via OneTagger (assumes a running container with the tree mounted at /music)
split -l 100 -d -a 3 file-list.txt /tmp/bulk-batches/b
DRY_RUN=1 bash run-bulk.sh           # exercise without tagging
bash run-bulk.sh                     # the real run
```

## `examples/`

- **`docker-compose.yml`** — a generic OneTagger container stack; adapt the
  music bind to your tree and uncomment `group_add` if your music dir is gated
  by a group. Pair with a `config/auto-tag.json` next to the compose file.
- **`auto-tag.example.json`** — the autotagger profile we ended up using for an
  A/B run (gap-fill-only — `overwrite:false`, Discogs styles → genre, art
  preserved). **Secrets are blanked**; copy to `config/auto-tag.json` and fill
  in your own Discogs token + any other credentials before use.

## Requirements

- Python 3.10+ with `mutagen` and `Pillow` (`requirements.txt`). Pillow is used to extract+resize embedded cover art into the report; if absent, the artwork toggle just stays empty (no other features break).
- For `run-bulk.sh`: Docker, a running OneTagger container with the music tree bind-mounted, a tuned `auto-tag.json` profile.
