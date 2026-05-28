# tag-diff

A small toolkit for batch-tagging an audio library with
[OneTagger](https://github.com/Marekkon5/onetagger) and producing a
**git-diff-style HTML report** of the before/after metadata changes.

Built originally for an A/B comparison on a ~3,800-track DJ library: clone the
dataset (ZFS), tag the clone in batches, compare against the pristine original.

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
| `report` | `PRE_ROOT POST_ROOT OUT.html` *(rels on stdin)* | Self-contained HTML report (see below) |

### HTML report (`tagtool.py report`)

GitHub-style review UI in one self-contained file:

- Sticky top toolbar — overall stats (scanned / changed / +A ~M −R), `show reviewed` toggle, live counter, `clear` button.
- Fixed-position columns (tag · before · after · ✓) so info stays in the same place as you scroll across files.
- **Per-row ✓** and **per-file ✓** mark-as-reviewed buttons; persisted in browser `localStorage` keyed by `file::tag` (survives page reload; regenerated reports keep the marks for the same rows).
- Filename gets a timestamp inserted automatically: `report.html` → `report-YYYY-MM-DD-HHMM.html`, plus a stable `report.html` copy refreshed to the latest.

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

- Python 3.10+ with `mutagen` (`requirements.txt`).
- For `run-bulk.sh`: Docker, a running OneTagger container with the music tree bind-mounted, a tuned `auto-tag.json` profile.
