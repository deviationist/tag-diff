#!/usr/bin/env python3
"""Read/diff audio metadata tags across two parallel library trees.

Originally a OneTagger A/B-test helper; grew into a self-contained HTML
report with a GitHub-style review UI. Tag reading via mutagen (handles
AIFF/MP3 ID3, plus M4A/MP4 atoms and generic Vorbis).

Modes:
  tagtool.py dump FILE...                       JSON of normalized tags per file
  tagtool.py diff PRE POST                      readable diff of one file pair
  tagtool.py treediff PRE_ROOT POST_ROOT        per-file diffs to stdout
                                                  (newline rel-paths on stdin)
  tagtool.py extract PRE_ROOT POST_ROOT OUT.json
                                                walk trees, dump diffs to JSON
                                                  (slow — runs mutagen)
  tagtool.py render IN.json OUT.html            build HTML from cached JSON
                                                  (fast — no filesystem walk)
  tagtool.py report PRE_ROOT POST_ROOT OUT.html
                                                extract + render in one pass

Ignore flags (treediff / extract / report):
  --ignore GLOB          skip rels with any path component matching GLOB
                         (repeatable; appends to defaults)
  --no-default-ignore    drop the built-in defaults (use only --ignore values)

Default ignore patterns: `.*` — any dot-prefixed path component (covers
`.sync`, `.DS_Store`, `._<name>` AppleDouble files, `.Trash`, etc.).
"""
import sys, json, os, fnmatch

# Path components matching any of these globs cause the rel to be skipped.
# `.*` is the catch-all for the Unix "hidden" convention — covers `.sync`
# (Resilio archive), `.DS_Store`, `.AppleDouble`, `._foo` (AppleDouble
# resource-fork stubs), `.Trash`, etc.
DEFAULT_IGNORES = [".*"]


def should_ignore(rel, patterns):
    """True if any path component of `rel` matches any glob in `patterns`."""
    for part in rel.split(os.sep):
        for pat in patterns:
            if fnmatch.fnmatchcase(part, pat):
                return True
    return False


def parse_ignore_flags(argv):
    """Pop --ignore PATTERN and --no-default-ignore out of argv.

    Returns (cleaned_argv, ignore_patterns). Defaults are always included
    unless --no-default-ignore is present; --ignore values append.
    """
    extras = []
    keep_defaults = True
    cleaned = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--ignore" and i + 1 < len(argv):
            extras.append(argv[i + 1])
            i += 2
        elif a == "--no-default-ignore":
            keep_defaults = False
            i += 1
        else:
            cleaned.append(a)
            i += 1
    ignores = (list(DEFAULT_IGNORES) if keep_defaults else []) + extras
    return cleaned, ignores

# Friendly names for the ID3 frames OneTagger writes (per auto-tag.json `tags`).
ID3_MAP = {
    "TIT2": "title", "TPE1": "artist", "TPE2": "albumArtist", "TALB": "album",
    "TCON": "genre", "TBPM": "bpm", "TKEY": "key", "TPUB": "label",
    "TDRC": "date", "TYER": "year", "TDAT": "date_DDMM", "TRCK": "track",
    "TPOS": "disc", "TIT3": "version", "TPE4": "remixer", "TSRC": "isrc",
    "TCOM": "composer", "TOAL": "origAlbum",
}

# MP4/M4A atom -> friendly name (mirrors the ID3 names so AIFF/MP3/M4A line up)
MP4_MAP = {
    "\xa9nam": "title", "\xa9ART": "artist", "aART": "albumArtist", "\xa9alb": "album",
    "\xa9gen": "genre", "gnre": "genre", "\xa9day": "date", "trkn": "track",
    "disk": "disc", "tmpo": "bpm", "\xa9wrt": "composer", "\xa9cmt": "comment",
}
# OneTagger/iTunes freeform ("----:com.apple.iTunes:NAME") -> friendly name
MP4_FREEFORM = {
    "LABEL": "label", "PUBLISHER": "label", "INITIALKEY": "key", "KEY": "key",
    "BPM": "bpm", "ISRC": "isrc", "CATALOGNUMBER": "catalogNumber",
}


def read_tags(path):
    """Return {friendly_tag: 'value'} for one file; {} if unreadable/untagged."""
    from mutagen import File as MFile
    try:
        f = MFile(path)
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}"}
    if f is None or f.tags is None:
        return {}
    tags = f.tags
    out = {}
    try:
        from mutagen.id3 import ID3
        is_id3 = isinstance(tags, ID3)
    except Exception:
        is_id3 = False
    if is_id3:
        for key, frame in tags.items():
            base = key.split(":")[0]
            if base == "APIC":
                try:
                    out["cover_art"] = f"<{frame.mime}, {len(frame.data)} bytes>"
                except Exception:
                    out["cover_art"] = "<image>"
                continue
            if hasattr(frame, "text"):
                if base == "TXXX":
                    name = "TXXX:" + key.split(":", 1)[1] if ":" in key else "TXXX"
                else:
                    name = ID3_MAP.get(base, base)
                val = "; ".join(str(t) for t in frame.text)
            else:
                # non-text / binary frame (GEOB, PRIV, MCDI, ...): note presence only
                name = base
                val = f"<{type(frame).__name__}>"
            if len(val) > 300:
                val = val[:300] + f"…<+{len(val) - 300} chars>"
            out[name] = val
    else:
        try:
            from mutagen.mp4 import MP4Tags
            is_mp4 = isinstance(tags, MP4Tags)
        except Exception:
            is_mp4 = False
        for key, val in tags.items():
            if is_mp4 and key == "covr":
                try:
                    out["cover_art"] = f"<cover, {len(bytes(val[0]))} bytes>" if val else "<cover>"
                except Exception:
                    out["cover_art"] = "<cover>"
                continue
            if is_mp4 and key.startswith("----"):
                nm = key.split(":")[-1]
                name = MP4_FREEFORM.get(nm.upper(), nm)
                try:
                    sval = "; ".join(b.decode("utf-8", "replace") if isinstance(b, (bytes, bytearray)) else str(b) for b in val)
                except Exception:
                    sval = str(val)
            elif is_mp4 and key in ("trkn", "disk"):
                name = MP4_MAP.get(key, key)
                sval = "; ".join((f"{t[0]}/{t[1]}" if isinstance(t, tuple) and len(t) == 2 else str(t)) for t in val)
            elif is_mp4:
                name = MP4_MAP.get(key, key)
                sval = "; ".join(str(v) for v in val) if isinstance(val, list) else str(val)
            else:
                name = key
                sval = "; ".join(str(v) for v in val) if isinstance(val, list) else str(val)
            if len(sval) > 300:
                sval = sval[:300] + f"…<+{len(sval) - 300} chars>"
            out[name] = sval
    return out


def diff_tags(pre, post):
    """List of changes: ('+',k,new) added, ('-',k,old) removed, ('~',k,old,new) changed."""
    changes = []
    for k in sorted(set(pre) | set(post)):
        a, b = pre.get(k), post.get(k)
        if a == b:
            continue
        if a is None:
            changes.append(("+", k, b))
        elif b is None:
            changes.append(("-", k, a))
        else:
            changes.append(("~", k, a, b))
    return changes


def _fmt(changes):
    lines = []
    for c in changes:
        if c[0] == "+":
            lines.append(f"    + {c[1]}: {c[2]}")
        elif c[0] == "-":
            lines.append(f"    - {c[1]}: {c[2]}")
        else:
            lines.append(f"    ~ {c[1]}: {c[2]!r} -> {c[3]!r}")
    return lines


KEY_FIELDS = ("title", "artist", "album", "albumArtist", "genre",
              "bpm", "key", "label", "date", "cover_art")


def extract_data(pre_root, post_root, rels, ignores=None):
    """Walk PRE and POST trees, read tags, compute diffs. Return a JSON-able
    data dict — consumed by html_report / render_html.

    `ignores` is a list of fnmatch-style globs matched against each rel's
    path components — rels with any matching component are dropped before
    any tag I/O happens. None means use DEFAULT_IGNORES; pass [] to disable
    filtering entirely.

    The mutagen reads are the slow part of the pipeline; cache this to disk
    (`tagtool.py extract`) and you can iterate on HTML/CSS/JS instantly via
    `tagtool.py render` without re-reading every file.
    """
    from datetime import datetime
    ignores = DEFAULT_IGNORES if ignores is None else ignores
    files = []
    unmatched = []
    field_counts = {}
    tc = {"added": 0, "removed": 0, "changed": 0}
    n_total = 0
    n_ignored = 0
    for rel in rels:
        if should_ignore(rel, ignores):
            n_ignored += 1
            continue
        n_total += 1
        pre_tags = read_tags(os.path.join(pre_root, rel))
        post_tags = read_tags(os.path.join(post_root, rel))
        # OneTagger marks every match with 1T_TAGGEDDATE; absence = unmatched.
        if not ("TXXX:1T_TAGGEDDATE" in post_tags or "1T_TAGGEDDATE" in post_tags):
            tags_subset = {k: v for k, v in post_tags.items() if k in KEY_FIELDS}
            unmatched.append({"rel": rel, "tags": tags_subset})
        changes = diff_tags(pre_tags, post_tags)
        if not changes:
            continue
        for c in changes:
            field_counts[c[1]] = field_counts.get(c[1], 0) + 1
            if c[0] == "+":
                tc["added"] += 1
            elif c[0] == "-":
                tc["removed"] += 1
            else:
                tc["changed"] += 1
        marker_only = (len(changes) == 1 and changes[0][0] == "+"
                       and changes[0][1] == "TXXX:1T_TAGGEDDATE")
        files.append({
            "rel": rel,
            "marker_only": marker_only,
            "changes": [list(c) for c in changes],
        })
    return {
        "version": 1,
        "generated": datetime.now().isoformat(timespec="seconds"),
        "pre_root": pre_root,
        "post_root": post_root,
        "n_total": n_total,
        "n_ignored": n_ignored,
        "ignores": list(ignores),
        "files": files,
        "unmatched": unmatched,
        "field_counts": field_counts,
        "tc": tc,
    }


def html_report(pre_root, post_root, rels, out_path, data=None, ignores=None):
    """Write a self-contained GitHub-style HTML report of PRE->POST tag changes.

    If `data` is supplied (an extract_data dict — typically from a cached JSON),
    skips the slow tree walk and just renders. Otherwise walks the trees via
    extract_data. `ignores` is only consulted when `data is None`.
    """
    import html as _html
    from datetime import datetime
    if data is None:
        data = extract_data(pre_root, post_root, rels, ignores=ignores)
    pre_root = data["pre_root"]
    post_root = data["post_root"]
    n_total = data["n_total"]
    tc = data["tc"]
    field_counts = data["field_counts"]
    unmatched_data = data["unmatched"]
    # Human-readable label + default visibility for each KEY_FIELD. Used by
    # both the unmatched-section column toggles and the file-filter checkboxes
    # below. Single source of truth for the labels you see on screen.
    UMETA = {
        "title":       ("Title",        True),
        "artist":      ("Artist",       True),
        "album":       ("Album",        True),
        "albumArtist": ("Album artist", True),
        "genre":       ("Genre",        False),
        "bpm":         ("BPM",          False),
        "key":         ("Key",          False),
        "label":       ("Label",        False),
        "date":        ("Date",         False),
        "cover_art":   ("Artwork",      False),
    }
    KEY_FIELDS_SET = set(KEY_FIELDS)

    # Build the HTML rows from raw changes data.
    files = []
    for f in data["files"]:
        rel = f["rel"]
        rel_e = _html.escape(rel)
        name_e = _html.escape(os.path.splitext(os.path.basename(rel))[0], quote=True)
        marker_only = f["marker_only"]
        # Per-file filter classes — which KEY_FIELDS have any change here, and
        # whether any change is an overwrite (~) or removal (-) of an existing
        # value. The file-filter controls below toggle these via CSS.
        kf_changes = set()
        has_overwrite = False
        for c in f["changes"]:
            if c[1] in KEY_FIELDS_SET:
                kf_changes.add(c[1])
            if c[0] in ("~", "-"):
                has_overwrite = True
        file_classes = [f"change-{k}" for k in sorted(kf_changes)]
        if has_overwrite:
            file_classes.append("has-overwrite")
        cls_str = " ".join(file_classes)
        rows = []
        for c in f["changes"]:
            tag = _html.escape(c[1])
            if c[0] == "+":
                before, after, cls = "", _html.escape(str(c[2])), "add"
            elif c[0] == "-":
                before, after, cls = _html.escape(str(c[2])), "", "del"
            else:
                before, after, cls = _html.escape(str(c[2])), _html.escape(str(c[3])), "mod"
            rows.append(
                f'<tr class="{cls}" data-file="{rel_e}" data-tag="{tag}">'
                f'<td class="k">{tag}</td>'
                f'<td class="before">{before}</td>'
                f'<td class="after">{after}</td>'
                f'<td class="action"><button class="dismiss-row" title="Mark row reviewed">✓</button></td>'
                f'</tr>'
            )
        files.append((rel_e, name_e, marker_only, rows, cls_str))

    fields_rows = "".join(
        f"<tr><td>{_html.escape(k)}</td><td>{v}</td></tr>"
        for k, v in sorted(field_counts.items(), key=lambda x: -x[1]))

    file_sections = "".join(
        f'<details class="{cls}" data-file="{rel}"{" data-marker-only=\"1\"" if marker_only else ""} open><summary>'
        f'<span class="path">{rel}</span>'
        f'<button class="copy-name" data-copy="{name}" title="Copy filename (no ext)">⧉</button>'
        f'<span class="spacer"></span>'
        f'<span class="count">{len(rs)} change{"s" if len(rs) != 1 else ""}</span>'
        f'<button class="dismiss-file" title="Mark file reviewed">✓ file</button>'
        f'</summary>'
        f'<table class="diff"><colgroup><col class="col-k"><col><col><col class="col-a"></colgroup>'
        f'<tbody>{"".join(rs)}</tbody></table></details>'
        for rel, name, marker_only, rs, cls in files)
    marker_only_count = sum(1 for _, _, mo, _, _ in files if mo)

    # File-section filter controls (parallel to the unmatched-section filters).
    # KEY_FIELDS is the source of truth; checkboxes are 1:1. OR semantics across
    # selected fields. Plus an "only overwrites" toggle for the small (~218)
    # subset of files where OneTagger changed or removed an existing value —
    # the high-scrutiny set when validating that `overwrite: false` didn't bite.
    file_filter_boxes = "".join(
        f'<label><input type="checkbox" data-file-filter="{k}"> {_html.escape(UMETA[k][0])}</label>'
        for k in KEY_FIELDS
    )
    file_filters_html = (
        '<div class="file-filters">'
        '<div class="ctrl-row">'
        '<span class="ctrl-label">Show only files with changes to:</span>'
        f'{file_filter_boxes}'
        '<button class="ctrl-clear" id="clear-file-filters" title="Clear field filters">clear</button>'
        '</div>'
        '<div class="ctrl-row">'
        '<label><input type="checkbox" id="only-overwrites"> Only files with overwrites '
        '(<span class="mod-c">~</span> or <span class="del-c">−</span>) — the high-scrutiny subset</label>'
        '</div>'
        '</div>'
    )

    # Unmatched section — files OneTagger couldn't tag (no 1T_TAGGEDDATE marker)
    # Each row is a "manual review queue" item; the existing dismiss-row JS marks
    # them as reviewed via localStorage keyed by file::__unmatched__.
    if unmatched_data:
        # UMETA is hoisted to the top of html_report so file-filter controls
        # and unmatched-section controls share the same label source.
        # cover_art's raw value is verbose (`<image/jpeg, NNNN bytes>`); for
        # the column we just want a yes/blank presence indicator.
        def cell_for(k, raw):
            if k == "cover_art":
                return "yes" if raw else ""
            return _html.escape(raw)

        u_rows = []
        for u in sorted(unmatched_data, key=lambda x: x["rel"]):
            rel = u["rel"]
            post_tags = u["tags"]
            rel_e = _html.escape(rel)
            name_e = _html.escape(os.path.splitext(os.path.basename(rel))[0], quote=True)
            vals = {k: (post_tags.get(k) or "") for k in KEY_FIELDS}
            # Per-row classes mark which fields are EMPTY. The filter checkboxes
            # then use CSS `tr:not(.missing-X)` to hide non-matching rows —
            # multiple filters AND naturally because each rule hides independently.
            missing_classes = " ".join(f"missing-{k}" for k, v in vals.items() if not v)
            present = [f for f in KEY_FIELDS if post_tags.get(f)]
            present_str = _html.escape(", ".join(present) if present else "(no recognised tags)")
            tds = (
                f'<td class="rownum" data-col="num"></td>'
                f'<td class="path" data-col="path">{rel_e}'
                f'<button class="copy-name" data-copy="{name_e}" title="Copy filename (no ext)">⧉</button>'
                f'</td>'
                + "".join(f'<td data-col="{k}">{cell_for(k, vals[k])}</td>' for k in KEY_FIELDS)
                + f'<td class="present" data-col="present">{present_str}</td>'
                + f'<td class="action" data-col="action"><button class="dismiss-row" title="Mark reviewed">✓</button></td>'
            )
            u_rows.append(
                f'<tr class="{missing_classes}" data-file="{rel_e}" data-tag="__unmatched__">'
                f'{tds}</tr>'
            )

        # Filter boxes: one per KEY_FIELD, none checked by default.
        filter_boxes = "".join(
            f'<label><input type="checkbox" data-filter="{k}"> {_html.escape(UMETA[k][0])}</label>'
            for k in KEY_FIELDS
        )
        # Column-visibility: one per KEY_FIELD with its default + "Currently has" (always default on).
        col_boxes = "".join(
            f'<label><input type="checkbox" data-col-toggle="{k}"'
            f'{" checked" if UMETA[k][1] else ""}> {_html.escape(UMETA[k][0])}</label>'
            for k in KEY_FIELDS
        ) + '<label><input type="checkbox" data-col-toggle="present" checked> Currently has</label>'
        controls_html = (
            '<div class="unmatched-controls">'
            '<div class="ctrl-row">'
            '<span class="ctrl-label">Show rows missing:</span>'
            f'{filter_boxes}'
            '<button class="ctrl-clear" id="clear-filters" title="Clear missing-field filters">clear</button>'
            '</div>'
            '<div class="ctrl-row">'
            '<span class="ctrl-label">Visible columns:</span>'
            f'{col_boxes}'
            '</div>'
            '</div>'
        )

        # Header row + colgroup mirror KEY_FIELDS, plus the fixed #/Path/CurrentlyHas/action columns.
        col_cols = "".join(f'<col data-col="{k}">' for k in KEY_FIELDS)
        ths = "".join(
            f'<th data-sort="{k}" data-col="{k}">{_html.escape(UMETA[k][0])}</th>'
            for k in KEY_FIELDS
        )

        unmatched_section = (
            '<details class="unmatched-section" open>'
            f'<summary><span class="path">Unmatched — needs manual review</span>'
            f'<span class="spacer"></span>'
            f'<span class="count" id="unmatched-counter">0/{len(unmatched_data)} confirmed</span></summary>'
            '<div class="hint">These files weren\'t matched by any OneTagger platform run. Open them in '
            'Meta (Mac), Rekordbox, or any tag editor to add tags manually — tick ✓ as you finish each one.</div>'
            f'{controls_html}'
            '<table class="unmatched">'
            f'<colgroup><col class="col-num"><col class="col-path">{col_cols}<col class="col-tags"><col class="col-a"></colgroup>'
            f'<thead><tr>'
            f'<th class="col-num" data-col="num">#</th>'
            f'<th data-sort="path" data-col="path">Path</th>'
            f'{ths}'
            f'<th data-col="present">Currently has</th><th data-col="action"></th></tr></thead>'
            f'<tbody>{"".join(u_rows)}</tbody></table></details>'
        )
    else:
        unmatched_section = ""

    css = r"""
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;font:14px/1.45 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;background:#0d1117;color:#c9d1d9}
header.bar{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:14px;
  padding:8px 16px;background:#161b22;border-bottom:1px solid #30363d;font-size:13px}
header.bar h1{font-size:13px;margin:0;font-weight:600}
header.bar .stats{color:#8b949e;font-family:ui-monospace,SFMono-Regular,monospace;font-size:12px}
header.bar .spacer{flex:1}
header.bar label{color:#8b949e;cursor:pointer;user-select:none;display:inline-flex;align-items:center;gap:6px}
header.bar #counter{color:#8b949e;font-variant-numeric:tabular-nums;font-size:12px}
header.bar button{background:#21262d;border:1px solid #30363d;color:#c9d1d9;padding:3px 10px;border-radius:4px;cursor:pointer;font-size:12px}
header.bar button:hover{background:#30363d}
.add-c{color:#3fb950}.del-c{color:#f85149}.mod-c{color:#d29922}
.wrap{padding:16px}
.meta{display:grid;grid-template-columns:1fr auto;column-gap:24px;align-items:start;
  margin-bottom:16px;padding:8px 12px;
  background:#161b22;border:1px solid #30363d;border-radius:6px;font-size:12px;color:#8b949e}
.meta .small{align-self:start;justify-self:end;text-align:right}
.meta table.mini td{padding:1px 14px 1px 0;font-variant-numeric:tabular-nums}
.meta .small{font-family:ui-monospace,SFMono-Regular,monospace}
details{border:1px solid #30363d;border-radius:6px;margin:8px 0;background:#161b22}
details>summary{position:sticky;top:39px;z-index:5;padding:8px 12px;cursor:pointer;list-style:none;
  background:#161b22;border-bottom:1px solid #30363d;display:flex;align-items:center;gap:12px;
  font-family:ui-monospace,SFMono-Regular,monospace;font-size:13px;border-radius:5px 5px 0 0}
details:not([open])>summary{border-bottom:none;border-radius:5px}
details>summary::-webkit-details-marker,details>summary::marker{display:none}
details>summary::before{content:"▸";color:#8b949e;display:inline-block;width:1ch}
details[open]>summary::before{content:"▾"}
details>summary .path{color:#79c0ff;flex:0 1 auto;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
details>summary .spacer{flex:1}
details>summary .count{color:#8b949e;font-size:12px}
details>summary button.dismiss-file{background:transparent;border:1px solid #30363d;color:#8b949e;
  padding:2px 8px;border-radius:4px;cursor:pointer;font-size:12px}
details>summary button.dismiss-file:hover{color:#3fb950;border-color:#3fb950}
details.all-dismissed{opacity:.55}
details.all-dismissed>summary button.dismiss-file{color:#3fb950;border-color:#3fb950}
table.diff{width:100%;table-layout:fixed;border-collapse:collapse}
table.diff col.col-k{width:200px}
table.diff col.col-a{width:48px}
table.diff td{padding:5px 10px;vertical-align:top;border-top:1px solid #21262d;
  font-family:ui-monospace,SFMono-Regular,monospace;font-size:13px;word-break:break-word;white-space:pre-wrap}
td.k{color:#8b949e}
tr.add td.after,tr.mod td.after{color:#3fb950;background:rgba(46,160,67,.08)}
tr.del td.before,tr.mod td.before{color:#f85149;background:rgba(248,81,73,.08)}
td.action{text-align:right;padding-right:8px}
td.action button{background:transparent;border:0;color:#8b949e;cursor:pointer;font-size:14px;
  padding:2px 6px;border-radius:3px;line-height:1}
td.action button:hover{color:#3fb950;background:rgba(46,160,67,.12)}
tr.dismissed{display:none}
body.show-dismissed tr.dismissed{display:table-row;opacity:.4}
body.show-dismissed tr.dismissed td.action button{color:#3fb950}
.sub{color:#8b949e}
button.copy-name{background:transparent;border:1px solid #30363d;color:#8b949e;
  padding:1px 6px;border-radius:3px;cursor:pointer;font-size:11px;margin-left:6px;font-family:ui-monospace,monospace}
button.copy-name:hover{color:#79c0ff;border-color:#79c0ff}
button.copy-name.copied{color:#3fb950;border-color:#3fb950}
details[data-marker-only="1"]{display:none}
body.show-marker-only details[data-marker-only="1"]{display:block;opacity:.6}
body.show-marker-only details[data-marker-only="1"]>summary .path::after{
  content:" · marker-only";color:#8b949e;font-size:11px;font-weight:normal}
.unmatched-section{border-color:#d29922;margin:0 0 16px 0}
.unmatched-section>summary{top:39px;background:#161b22}
.unmatched-section>summary .path{color:#d29922}
.unmatched-section .hint{padding:8px 12px;font-size:12px;color:#8b949e;border-bottom:1px solid #21262d}
.unmatched-controls{padding:8px 12px;border-bottom:1px solid #21262d;background:#0d1117;
  display:flex;flex-direction:column;gap:6px}
.unmatched-controls .ctrl-row{display:flex;align-items:center;flex-wrap:wrap;gap:14px;font-size:12px}
.unmatched-controls .ctrl-label{color:#8b949e;font-weight:600;min-width:170px}
.unmatched-controls label{color:#c9d1d9;cursor:pointer;user-select:none;display:inline-flex;align-items:center;gap:5px}
.unmatched-controls input[type=checkbox]{accent-color:#d29922}
.unmatched-controls .ctrl-clear{margin-left:auto;background:#21262d;border:1px solid #30363d;
  color:#c9d1d9;padding:2px 10px;border-radius:4px;cursor:pointer;font-size:11px}
.unmatched-controls .ctrl-clear:hover{background:#30363d}
/* Generic hide rules — work for any field key. JS computes which rows match
   the active filters and which cells belong to hidden columns, then toggles
   these two classes. Field-agnostic by construction. */
table.unmatched tr.filter-hidden{display:none}
table.unmatched .col-hidden{display:none}
details[data-file].filter-hidden{display:none}
/* File-filter controls — mirrors the unmatched-controls styling so the
   two panels read as siblings. */
.file-filters{padding:8px 12px;margin:0 0 16px 0;background:#161b22;border:1px solid #30363d;
  border-radius:6px;display:flex;flex-direction:column;gap:6px}
.file-filters .ctrl-row{display:flex;align-items:center;flex-wrap:wrap;gap:14px;font-size:12px}
.file-filters .ctrl-label{color:#8b949e;font-weight:600;min-width:220px}
.file-filters label{color:#c9d1d9;cursor:pointer;user-select:none;display:inline-flex;align-items:center;gap:5px}
.file-filters input[type=checkbox]{accent-color:#79c0ff}
.file-filters .ctrl-clear{margin-left:auto;background:#21262d;border:1px solid #30363d;
  color:#c9d1d9;padding:2px 10px;border-radius:4px;cursor:pointer;font-size:11px}
.file-filters .ctrl-clear:hover{background:#30363d}
table.unmatched{width:100%;table-layout:fixed;border-collapse:collapse}
table.unmatched col.col-num{width:48px}
table.unmatched col.col-path{width:32%}
table.unmatched col.col-tags{width:18%}
table.unmatched col.col-a{width:48px}
table.unmatched tbody{counter-reset:rownum}
table.unmatched tbody tr{counter-increment:rownum}
table.unmatched td.rownum{text-align:right;color:#8b949e;font-variant-numeric:tabular-nums;font-size:11px}
table.unmatched td.rownum::before{content:counter(rownum)}
table.unmatched th.col-num{text-align:right;font-size:11px}
table.unmatched th,table.unmatched td{padding:5px 10px;vertical-align:top;border-top:1px solid #21262d;
  font-family:ui-monospace,SFMono-Regular,monospace;font-size:13px;word-break:break-word;white-space:pre-wrap}
table.unmatched th{color:#8b949e;text-align:left;font-weight:600;background:#161b22;position:sticky;top:78px}
table.unmatched th[data-sort]{cursor:pointer;user-select:none}
table.unmatched th[data-sort]:hover{color:#79c0ff}
table.unmatched th[data-sort].sort-active{color:#79c0ff}
table.unmatched th .sort-arrow{margin-left:4px;font-size:10px}
table.unmatched td.path{color:#79c0ff}
table.unmatched td.present{color:#8b949e;font-size:12px}
.meta-fields{border:none;background:transparent;margin:0;padding:0}
.meta-fields>summary{position:static;padding:0;background:transparent;border-bottom:none;border-radius:0}
.meta-fields>table{margin-top:6px}
"""

    js = r"""
(function(){
  const KEY='tagdiff:dismissed', SHOW_KEY='tagdiff:showDismissed';
  let dismissed;
  try{ dismissed=new Set(JSON.parse(localStorage.getItem(KEY)||'[]')); }catch(e){ dismissed=new Set(); }
  function save(){ try{ localStorage.setItem(KEY,JSON.stringify([...dismissed])); }catch(e){} updateCounter(); }
  function rowKey(tr){ return tr.dataset.file+'::'+tr.dataset.tag; }
  function applyDismissals(){
    document.querySelectorAll('tr[data-file]').forEach(tr=>{
      tr.classList.toggle('dismissed', dismissed.has(rowKey(tr)));
    });
    document.querySelectorAll('details[data-file]').forEach(d=>{
      const rows=d.querySelectorAll('tr[data-file]');
      const all=rows.length && [...rows].every(r=>r.classList.contains('dismissed'));
      d.classList.toggle('all-dismissed', all);
    });
  }
  function updateCounter(){
    // counter reflects what's actually in the visible review queue: when marker-only
    // is hidden, those files and their rows don't count toward the "reviewed" totals.
    const showMO=document.body.classList.contains('show-marker-only');
    const dets=[...document.querySelectorAll('details[data-file]')]
                  .filter(d=>showMO || d.dataset.markerOnly!=='1');
    // Diff-section progress (excludes unmatched section so the two counters don't conflate)
    const diffRows=[];
    dets.forEach(d=>d.querySelectorAll('tr[data-file]').forEach(tr=>diffRows.push(tr)));
    const totalRows=diffRows.length;
    let doneRows=0; diffRows.forEach(tr=>{ if(dismissed.has(rowKey(tr))) doneRows++; });
    const totalFiles=dets.length;
    let doneFiles=0; dets.forEach(d=>{
      const rs=d.querySelectorAll('tr[data-file]');
      if(rs.length && [...rs].every(r=>dismissed.has(rowKey(r)))) doneFiles++;
    });
    const c=document.getElementById('counter');
    if(c){
      // When a file-filter is active, show '· N visible' so progress in
      // the filtered subset is legible without losing the total.
      const visibleFiles=dets.filter(d=>!d.classList.contains('filter-hidden')).length;
      const filterOn=visibleFiles!==totalFiles;
      c.textContent='reviewed: '+doneFiles+'/'+totalFiles+' files · '+doneRows+'/'+totalRows+' changes'
        +(filterOn ? ' · '+visibleFiles+' visible' : '');
    }
    // Unmatched-section progress — separate so you can see manual-fix backlog independently
    const unmatchedRows=[...document.querySelectorAll('details.unmatched-section tr[data-file]')];
    let doneUnmatched=0; unmatchedRows.forEach(tr=>{ if(dismissed.has(rowKey(tr))) doneUnmatched++; });
    const uc=document.getElementById('unmatched-counter');
    if(uc){
      // When a missing-field filter is on, the "visible" count diverges from total.
      // Use offsetParent==null as a fast display:none test.
      const visible=unmatchedRows.filter(tr=>tr.offsetParent!==null).length;
      const filterOn=visible!==unmatchedRows.length;
      uc.textContent=doneUnmatched+'/'+unmatchedRows.length+' confirmed'
        +(filterOn ? ' · '+visible+' visible' : '');
    }
    const pm=document.getElementById('page-meter');
    if(pm){
      const sel=showMO?'details[data-file]':'details[data-file]:not([data-marker-only="1"])';
      const visible=document.querySelectorAll(sel).length;
      requestAnimationFrame(()=>{
        const h=document.documentElement.scrollHeight;
        pm.textContent=visible+' visible · '+h.toLocaleString()+' px';
      });
    }
  }
  function sortUnmatched(col, dir){
    const table=document.querySelector('table.unmatched');
    if(!table) return;
    const tbody=table.querySelector('tbody');
    const rows=[...tbody.querySelectorAll('tr[data-file]')];
    // Generic lookup by data-col: any sortable column works without a hardcoded index.
    rows.sort((a,b)=>{
      let av, bv;
      if(col==='path'){
        av=(a.dataset.file||'').toLowerCase();
        bv=(b.dataset.file||'').toLowerCase();
      } else {
        av=(a.querySelector('td[data-col="'+col+'"]')?.textContent||'').trim().toLowerCase();
        bv=(b.querySelector('td[data-col="'+col+'"]')?.textContent||'').trim().toLowerCase();
      }
      // Treat empties as 'greater than' any value: asc -> A-Z-empty, desc -> empty-Z-A
      const aE=av==='', bE=bv==='';
      if(aE && !bE) return dir==='desc' ? -1 : 1;
      if(bE && !aE) return dir==='desc' ? 1 : -1;
      return dir==='desc' ? bv.localeCompare(av) : av.localeCompare(bv);
    });
    rows.forEach(r=>tbody.appendChild(r));
    table.querySelectorAll('th[data-sort]').forEach(th=>{
      th.classList.remove('sort-active');
      const arrow=th.querySelector('.sort-arrow');
      if(arrow) arrow.remove();
      th.dataset.sortDirection='';
    });
    const activeTh=table.querySelector('th[data-sort="'+col+'"]');
    if(activeTh){
      activeTh.classList.add('sort-active');
      activeTh.dataset.sortDirection=dir;
      const arrow=document.createElement('span');
      arrow.className='sort-arrow';
      arrow.textContent=dir==='desc' ? '▼' : '▲';
      activeTh.appendChild(arrow);
    }
    try{ localStorage.setItem('tagdiff:unmatchedSort', JSON.stringify({col,dir})); }catch(_){}
  }
  document.addEventListener('click', e=>{
    const t=e.target;
    const sortTh=t.closest && t.closest('th[data-sort]');
    if(sortTh && t.closest('table.unmatched')){
      const col=sortTh.dataset.sort;
      const currentDir=sortTh.dataset.sortDirection;
      sortUnmatched(col, currentDir==='asc' ? 'desc' : 'asc');
      return;
    }
    if(t.classList.contains('dismiss-row')){
      const tr=t.closest('tr'); const k=rowKey(tr);
      if(dismissed.has(k)) dismissed.delete(k); else dismissed.add(k);
      save(); applyDismissals();
    } else if(t.classList.contains('dismiss-file')){
      e.preventDefault(); e.stopPropagation();
      const d=t.closest('details');
      const rows=[...d.querySelectorAll('tr[data-file]')];
      const allDone=rows.length && rows.every(r=>dismissed.has(rowKey(r)));
      rows.forEach(r=>{ const k=rowKey(r); if(allDone) dismissed.delete(k); else dismissed.add(k); });
      save(); applyDismissals();
    } else if(t.classList.contains('copy-name')){
      e.preventDefault(); e.stopPropagation();
      const text=t.dataset.copy||'';
      if(navigator.clipboard && navigator.clipboard.writeText){
        navigator.clipboard.writeText(text).then(()=>{
          t.classList.add('copied'); const orig=t.textContent; t.textContent='✓';
          setTimeout(()=>{ t.classList.remove('copied'); t.textContent=orig; }, 1200);
        }).catch(()=>{ t.textContent='!'; setTimeout(()=>{ t.textContent='⧉'; }, 1200); });
      }
    }
  });
  document.addEventListener('DOMContentLoaded', ()=>{
    const sd=document.getElementById('show-dismissed');
    const persisted=localStorage.getItem(SHOW_KEY)==='1';
    sd.checked=persisted; document.body.classList.toggle('show-dismissed', persisted);
    sd.addEventListener('change', e=>{
      document.body.classList.toggle('show-dismissed', e.target.checked);
      try{ localStorage.setItem(SHOW_KEY, e.target.checked?'1':'0'); }catch(_){}
    });
    const MO_KEY='tagdiff:showMarkerOnly';
    const smo=document.getElementById('show-marker-only');
    const moPersist=localStorage.getItem(MO_KEY)==='1';
    smo.checked=moPersist; document.body.classList.toggle('show-marker-only', moPersist);
    smo.addEventListener('change', e=>{
      document.body.classList.toggle('show-marker-only', e.target.checked);
      try{ localStorage.setItem(MO_KEY, e.target.checked?'1':'0'); }catch(_){}
      updateCounter();
    });
    document.getElementById('clear-all').addEventListener('click', ()=>{
      if(confirm('Clear all reviewed marks?')){ dismissed.clear(); save(); applyDismissals(); }
    });
    // Unmatched-table controls: missing-field filters + column-visibility toggles.
    // State lives in localStorage. The CSS only knows two generic rules
    // (tr.filter-hidden and .col-hidden); this code decides which rows/cells
    // get those classes based on the active filter/hidden sets. Field-agnostic:
    // adding/removing columns needs no CSS changes.
    const FILTER_KEY='tagdiff:unmatchedFilters';
    const COL_KEY='tagdiff:unmatchedHiddenCols';
    const uTable=document.querySelector('table.unmatched');
    if(uTable){
      let filters, hidden;
      try{ filters=new Set(JSON.parse(localStorage.getItem(FILTER_KEY)||'[]')); }catch(_){ filters=new Set(); }
      try{ hidden=new Set(JSON.parse(localStorage.getItem(COL_KEY)||'[]')); }catch(_){ hidden=new Set(); }
      const allRows=[...uTable.querySelectorAll('tbody tr[data-file]')];
      function applyFilters(){
        // OR semantics: a row passes if it's missing ANY of the selected fields.
        // No filters selected → show everything (otherwise an empty .some() would
        // return false and hide every row).
        const active=[...filters];
        allRows.forEach(tr=>{
          const ok=active.length===0
            || active.some(k=>tr.classList.contains('missing-'+k));
          tr.classList.toggle('filter-hidden', !ok);
        });
      }
      function applyHiddenCols(){
        uTable.querySelectorAll('[data-col]').forEach(el=>{
          el.classList.toggle('col-hidden', hidden.has(el.dataset.col));
        });
      }
      function applyState(){ applyFilters(); applyHiddenCols(); updateCounter(); }
      document.querySelectorAll('input[data-filter]').forEach(cb=>{
        cb.checked=filters.has(cb.dataset.filter);
        cb.addEventListener('change', e=>{
          const k=e.target.dataset.filter;
          if(e.target.checked) filters.add(k); else filters.delete(k);
          try{ localStorage.setItem(FILTER_KEY, JSON.stringify([...filters])); }catch(_){}
          applyState();
        });
      });
      document.querySelectorAll('input[data-col-toggle]').forEach(cb=>{
        // checkbox checked = column visible; checkbox unchecked = column hidden.
        cb.checked=!hidden.has(cb.dataset.colToggle);
        cb.addEventListener('change', e=>{
          const k=e.target.dataset.colToggle;
          if(e.target.checked) hidden.delete(k); else hidden.add(k);
          try{ localStorage.setItem(COL_KEY, JSON.stringify([...hidden])); }catch(_){}
          applyState();
        });
      });
      const cf=document.getElementById('clear-filters');
      if(cf) cf.addEventListener('click', ()=>{
        filters.clear();
        try{ localStorage.setItem(FILTER_KEY, '[]'); }catch(_){}
        document.querySelectorAll('input[data-filter]').forEach(cb=>cb.checked=false);
        applyState();
      });
      applyState();
    }
    // File-section filters: per-field checkboxes (OR semantics) + an
    // "only overwrites" toggle. Each <details data-file> carries
    // change-<field> classes (one per KEY_FIELD it has any change in) and
    // optionally has-overwrite (any ~ or -). JS computes which files pass
    // and toggles `filter-hidden`; the CSS rule does the rest.
    const FFK='tagdiff:fileFilters', OOK='tagdiff:onlyOverwrites';
    const fileFilters=new Set();
    try{ JSON.parse(localStorage.getItem(FFK)||'[]').forEach(k=>fileFilters.add(k)); }catch(_){}
    let onlyOverwrites=false;
    try{ onlyOverwrites=localStorage.getItem(OOK)==='1'; }catch(_){}
    const allFileDetails=[...document.querySelectorAll('details[data-file]')];
    function applyFileFilters(){
      const active=[...fileFilters];
      allFileDetails.forEach(d=>{
        let pass=true;
        if(active.length>0) pass=active.some(k=>d.classList.contains('change-'+k));
        if(pass && onlyOverwrites) pass=d.classList.contains('has-overwrite');
        d.classList.toggle('filter-hidden', !pass);
      });
      updateCounter();
    }
    document.querySelectorAll('input[data-file-filter]').forEach(cb=>{
      cb.checked=fileFilters.has(cb.dataset.fileFilter);
      cb.addEventListener('change', e=>{
        const k=e.target.dataset.fileFilter;
        if(e.target.checked) fileFilters.add(k); else fileFilters.delete(k);
        try{ localStorage.setItem(FFK, JSON.stringify([...fileFilters])); }catch(_){}
        applyFileFilters();
      });
    });
    const oo=document.getElementById('only-overwrites');
    if(oo){
      oo.checked=onlyOverwrites;
      oo.addEventListener('change', e=>{
        onlyOverwrites=e.target.checked;
        try{ localStorage.setItem(OOK, onlyOverwrites?'1':'0'); }catch(_){}
        applyFileFilters();
      });
    }
    const cff=document.getElementById('clear-file-filters');
    if(cff) cff.addEventListener('click', ()=>{
      fileFilters.clear();
      try{ localStorage.setItem(FFK, '[]'); }catch(_){}
      document.querySelectorAll('input[data-file-filter]').forEach(cb=>cb.checked=false);
      applyFileFilters();
    });
    applyFileFilters();

    // Persist open/closed state of the two top-level <details> sections.
    // HTML defaults are: meta-fields closed, unmatched-section open. Saved
    // state overrides; if no saved state exists, the HTML default is kept.
    const SECTIONS=[
      {sel:'details.meta-fields',       key:'tagdiff:openChangesByField'},
      {sel:'details.unmatched-section', key:'tagdiff:openUnmatched'},
    ];
    SECTIONS.forEach(({sel,key})=>{
      const d=document.querySelector(sel);
      if(!d) return;
      let saved=null;
      try{ saved=localStorage.getItem(key); }catch(_){}
      if(saved==='1') d.open=true;
      else if(saved==='0') d.open=false;
      d.addEventListener('toggle',()=>{
        try{ localStorage.setItem(key, d.open?'1':'0'); }catch(_){}
      });
    });
    applyDismissals(); updateCounter();
    try{
      const saved=JSON.parse(localStorage.getItem('tagdiff:unmatchedSort')||'null');
      if(saved && saved.col) sortUnmatched(saved.col, saved.dir||'asc');
    }catch(_){}
  });
})();
"""

    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>OneTagger tag diff</title><style>{css}</style></head><body>
<header class="bar">
<h1>OneTagger tag diff</h1>
<span class="stats">{n_total} scanned · {len(files) - marker_only_count} changed · <span class="sub">({marker_only_count} marker-only)</span> · <span class="del-c">{len(unmatched_data)} unmatched</span> · <span class="add-c">+{tc['added']}</span> <span class="mod-c">~{tc['changed']}</span> <span class="del-c">−{tc['removed']}</span> changes</span>
<span class="spacer"></span>
<label><input type="checkbox" id="show-marker-only"> show marker-only</label>
<label><input type="checkbox" id="show-dismissed"> show reviewed</label>
<span id="counter">reviewed: 0/0 files · 0/0 changes</span>
<span id="page-meter" class="sub" title="visible detail sections / document scrollable height — watch this jump when you flip the filters">— · — px</span>
<button id="clear-all" title="Clear all reviewed marks">clear</button>
</header>
<div class="wrap">
<div class="meta">
<details class="meta-fields">
  <summary><span class="sub">Changes by field</span></summary>
  <table class="mini"><tbody>{fields_rows}</tbody></table>
</details>
<div class="small">PRE <code>{_html.escape(pre_root)}</code> → POST <code>{_html.escape(post_root)}</code> · generated {datetime.now():%Y-%m-%d %H:%M}</div>
</div>
{unmatched_section}
{file_filters_html}
{file_sections}
</div>
<script>{js}</script>
</body></html>"""
    with open(out_path, "w") as f:
        f.write(doc)
    return len(files), n_total, tc


def main():
    argv, ignores = parse_ignore_flags(sys.argv[1:])
    if not argv:
        print(__doc__); sys.exit(2)
    mode = argv[0]
    if mode == "dump":
        print(json.dumps({p: read_tags(p) for p in argv[1:]}, indent=2, ensure_ascii=False))
    elif mode == "diff":
        pre, post = read_tags(argv[1]), read_tags(argv[2])
        for line in _fmt(diff_tags(pre, post)) or ["    (no tag changes)"]:
            print(line)
    elif mode == "treediff":
        pre_root, post_root = argv[1], argv[2]
        rels = [l.strip() for l in sys.stdin if l.strip()]
        n_changed = 0
        n_ignored = 0
        field_counts = {}
        for rel in rels:
            if should_ignore(rel, ignores):
                n_ignored += 1
                continue
            pre = read_tags(os.path.join(pre_root, rel))
            post = read_tags(os.path.join(post_root, rel))
            changes = diff_tags(pre, post)
            if not changes:
                continue
            n_changed += 1
            print(f"\n=== {rel}")
            for line in _fmt(changes):
                print(line)
            for c in changes:
                field_counts[c[1]] = field_counts.get(c[1], 0) + 1
        print(f"\n--- summary: {n_changed}/{len(rels) - n_ignored} files changed "
              f"({n_ignored} ignored by patterns: {ignores}) ---")
        for k, v in sorted(field_counts.items(), key=lambda x: -x[1]):
            print(f"    {v:4d}  {k}")
    elif mode == "report":
        import shutil
        from datetime import datetime
        pre_root, post_root, out = argv[1], argv[2], argv[3]
        rels = [l.strip() for l in sys.stdin if l.strip()]
        ts = datetime.now().strftime("%Y-%m-%d-%H%M")
        base, ext = os.path.splitext(out)
        out_ts = f"{base}-{ts}{ext or '.html'}"
        ch, tot, tc = html_report(pre_root, post_root, rels, out_ts, ignores=ignores)
        # also refresh the unversioned 'latest' copy at the requested path
        try:
            shutil.copy2(out_ts, out)
        except Exception as e:
            print(f"warning: could not refresh latest {out}: {e}", file=sys.stderr)
        print(f"wrote {out_ts}  (latest -> {out})")
        print(f"  {ch}/{tot} files changed (+{tc['added']} ~{tc['changed']} -{tc['removed']})")
    elif mode == "extract":
        # Slow data step: read PRE/POST tags, compute diffs, write JSON for fast re-render.
        pre_root, post_root, out_json = argv[1], argv[2], argv[3]
        rels = [l.strip() for l in sys.stdin if l.strip()]
        data = extract_data(pre_root, post_root, rels, ignores=ignores)
        with open(out_json, "w") as f:
            json.dump(data, f, separators=(",", ":"))
        print(f"wrote {out_json}  ({data['n_total']} scanned, "
              f"{len(data['files'])} with diffs, {len(data['unmatched'])} unmatched, "
              f"{data['n_ignored']} ignored by {data['ignores']})")
    elif mode == "render":
        # Fast render step: load extract_data JSON, build HTML — no filesystem walk.
        import shutil
        from datetime import datetime
        in_json, out = argv[1], argv[2]
        with open(in_json) as f:
            data = json.load(f)
        ts = datetime.now().strftime("%Y-%m-%d-%H%M")
        base, ext = os.path.splitext(out)
        out_ts = f"{base}-{ts}{ext or '.html'}"
        ch, tot, tc = html_report(None, None, None, out_ts, data=data)
        try:
            shutil.copy2(out_ts, out)
        except Exception as e:
            print(f"warning: could not refresh latest {out}: {e}", file=sys.stderr)
        print(f"wrote {out_ts}  (latest -> {out})")
        print(f"  {ch}/{tot} files changed (+{tc['added']} ~{tc['changed']} -{tc['removed']})  (from cached {in_json})")
    else:
        print(__doc__); sys.exit(2)


if __name__ == "__main__":
    main()
