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
"""
import sys, json, os

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


def extract_data(pre_root, post_root, rels):
    """Walk PRE and POST trees, read tags, compute diffs. Return a JSON-able
    data dict — consumed by html_report / render_html.

    The mutagen reads are the slow part of the pipeline; cache this to disk
    (`tagtool.py extract`) and you can iterate on HTML/CSS/JS instantly via
    `tagtool.py render` without re-reading every file.
    """
    from datetime import datetime
    files = []
    unmatched = []
    field_counts = {}
    tc = {"added": 0, "removed": 0, "changed": 0}
    n_total = 0
    for rel in rels:
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
        "files": files,
        "unmatched": unmatched,
        "field_counts": field_counts,
        "tc": tc,
    }


def html_report(pre_root, post_root, rels, out_path, data=None):
    """Write a self-contained GitHub-style HTML report of PRE->POST tag changes.

    If `data` is supplied (an extract_data dict — typically from a cached JSON),
    skips the slow tree walk and just renders. Otherwise walks the trees via
    extract_data.
    """
    import html as _html
    from datetime import datetime
    if data is None:
        data = extract_data(pre_root, post_root, rels)
    pre_root = data["pre_root"]
    post_root = data["post_root"]
    n_total = data["n_total"]
    tc = data["tc"]
    field_counts = data["field_counts"]
    unmatched_data = data["unmatched"]
    # Build the HTML rows from raw changes data.
    files = []
    for f in data["files"]:
        rel = f["rel"]
        rel_e = _html.escape(rel)
        name_e = _html.escape(os.path.splitext(os.path.basename(rel))[0], quote=True)
        marker_only = f["marker_only"]
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
        files.append((rel_e, name_e, marker_only, rows))

    fields_rows = "".join(
        f"<tr><td>{_html.escape(k)}</td><td>{v}</td></tr>"
        for k, v in sorted(field_counts.items(), key=lambda x: -x[1]))

    file_sections = "".join(
        f'<details data-file="{rel}"{" data-marker-only=\"1\"" if marker_only else ""} open><summary>'
        f'<span class="path">{rel}</span>'
        f'<button class="copy-name" data-copy="{name}" title="Copy filename (no ext)">⧉</button>'
        f'<span class="spacer"></span>'
        f'<span class="count">{len(rs)} change{"s" if len(rs) != 1 else ""}</span>'
        f'<button class="dismiss-file" title="Mark file reviewed">✓ file</button>'
        f'</summary>'
        f'<table class="diff"><colgroup><col class="col-k"><col><col><col class="col-a"></colgroup>'
        f'<tbody>{"".join(rs)}</tbody></table></details>'
        for rel, name, marker_only, rs in files)
    marker_only_count = sum(1 for _, _, mo, _ in files if mo)

    # Unmatched section — files OneTagger couldn't tag (no 1T_TAGGEDDATE marker)
    # Each row is a "manual review queue" item; the existing dismiss-row JS marks
    # them as reviewed via localStorage keyed by file::__unmatched__.
    if unmatched_data:
        u_rows = []
        for u in sorted(unmatched_data, key=lambda x: x["rel"]):
            rel = u["rel"]
            post_tags = u["tags"]
            rel_e = _html.escape(rel)
            name_e = _html.escape(os.path.splitext(os.path.basename(rel))[0], quote=True)
            title = _html.escape(post_tags.get("title") or "")
            artist = _html.escape(post_tags.get("artist") or "")
            album = _html.escape(post_tags.get("album") or "")
            present = [f for f in KEY_FIELDS if post_tags.get(f)]
            present_str = _html.escape(", ".join(present) if present else "(no recognised tags)")
            u_rows.append(
                f'<tr data-file="{rel_e}" data-tag="__unmatched__">'
                f'<td class="path">{rel_e}'
                f'<button class="copy-name" data-copy="{name_e}" title="Copy filename (no ext)">⧉</button>'
                f'</td>'
                f'<td>{title}</td>'
                f'<td>{artist}</td>'
                f'<td>{album}</td>'
                f'<td class="present">{present_str}</td>'
                f'<td class="action"><button class="dismiss-row" title="Mark reviewed">✓</button></td>'
                f'</tr>'
            )
        unmatched_section = (
            '<details class="unmatched-section" open>'
            f'<summary><span class="path">Unmatched — needs manual review</span>'
            f'<span class="spacer"></span>'
            f'<span class="count">{len(unmatched_data)} files</span></summary>'
            '<div class="hint">These files weren\'t matched by any OneTagger platform run. Open them in '
            'Meta (Mac), Rekordbox, or any tag editor to add tags manually — tick ✓ as you finish each one.</div>'
            '<table class="unmatched">'
            '<colgroup><col class="col-path"><col><col><col><col class="col-tags"><col class="col-a"></colgroup>'
            '<thead><tr><th>Path</th><th>Title</th><th>Artist</th><th>Album</th>'
            '<th>Currently has</th><th></th></tr></thead>'
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
.meta{display:flex;gap:24px;flex-wrap:wrap;margin-bottom:16px;padding:10px 14px;
  background:#161b22;border:1px solid #30363d;border-radius:6px;font-size:12px;color:#8b949e;align-items:flex-start}
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
.unmatched-section{border-color:#d29922}
.unmatched-section>summary{top:39px;background:#161b22}
.unmatched-section>summary .path{color:#d29922}
.unmatched-section .hint{padding:8px 12px;font-size:12px;color:#8b949e;border-bottom:1px solid #21262d}
table.unmatched{width:100%;table-layout:fixed;border-collapse:collapse}
table.unmatched col.col-path{width:32%}
table.unmatched col.col-tags{width:18%}
table.unmatched col.col-a{width:48px}
table.unmatched th,table.unmatched td{padding:5px 10px;vertical-align:top;border-top:1px solid #21262d;
  font-family:ui-monospace,SFMono-Regular,monospace;font-size:13px;word-break:break-word;white-space:pre-wrap}
table.unmatched th{color:#8b949e;text-align:left;font-weight:600;background:#161b22;position:sticky;top:78px}
table.unmatched td.path{color:#79c0ff}
table.unmatched td.present{color:#8b949e;font-size:12px}
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
    const diffRows=[];
    dets.forEach(d=>d.querySelectorAll('tr[data-file]').forEach(tr=>diffRows.push(tr)));
    const unmatchedRows=[...document.querySelectorAll('details.unmatched-section tr[data-file]')];
    const trs=diffRows.concat(unmatchedRows);
    const totalRows=trs.length;
    let doneRows=0; trs.forEach(tr=>{ if(dismissed.has(rowKey(tr))) doneRows++; });
    const totalFiles=dets.length;
    let doneFiles=0; dets.forEach(d=>{
      const rs=d.querySelectorAll('tr[data-file]');
      if(rs.length && [...rs].every(r=>dismissed.has(rowKey(r)))) doneFiles++;
    });
    const c=document.getElementById('counter');
    if(c) c.textContent='reviewed: '+doneFiles+'/'+totalFiles+' files · '+doneRows+'/'+totalRows+' changes';
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
  document.addEventListener('click', e=>{
    const t=e.target;
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
    applyDismissals(); updateCounter();
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
<table class="mini"><tbody><tr><td class="sub" colspan="2">changes by field</td></tr>{fields_rows}</tbody></table>
<div class="small">PRE <code>{_html.escape(pre_root)}</code> → POST <code>{_html.escape(post_root)}</code> · generated {datetime.now():%Y-%m-%d %H:%M}</div>
</div>
{unmatched_section}
{file_sections}
</div>
<script>{js}</script>
</body></html>"""
    with open(out_path, "w") as f:
        f.write(doc)
    return len(files), n_total, tc


def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(2)
    mode = sys.argv[1]
    if mode == "dump":
        print(json.dumps({p: read_tags(p) for p in sys.argv[2:]}, indent=2, ensure_ascii=False))
    elif mode == "diff":
        pre, post = read_tags(sys.argv[2]), read_tags(sys.argv[3])
        for line in _fmt(diff_tags(pre, post)) or ["    (no tag changes)"]:
            print(line)
    elif mode == "treediff":
        pre_root, post_root = sys.argv[2], sys.argv[3]
        rels = [l.strip() for l in sys.stdin if l.strip()]
        n_changed = 0
        field_counts = {}
        for rel in rels:
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
        print(f"\n--- summary: {n_changed}/{len(rels)} files changed ---")
        for k, v in sorted(field_counts.items(), key=lambda x: -x[1]):
            print(f"    {v:4d}  {k}")
    elif mode == "report":
        import shutil
        from datetime import datetime
        pre_root, post_root, out = sys.argv[2], sys.argv[3], sys.argv[4]
        rels = [l.strip() for l in sys.stdin if l.strip()]
        ts = datetime.now().strftime("%Y-%m-%d-%H%M")
        base, ext = os.path.splitext(out)
        out_ts = f"{base}-{ts}{ext or '.html'}"
        ch, tot, tc = html_report(pre_root, post_root, rels, out_ts)
        # also refresh the unversioned 'latest' copy at the requested path
        try:
            shutil.copy2(out_ts, out)
        except Exception as e:
            print(f"warning: could not refresh latest {out}: {e}", file=sys.stderr)
        print(f"wrote {out_ts}  (latest -> {out})")
        print(f"  {ch}/{tot} files changed (+{tc['added']} ~{tc['changed']} -{tc['removed']})")
    elif mode == "extract":
        # Slow data step: read PRE/POST tags, compute diffs, write JSON for fast re-render.
        pre_root, post_root, out_json = sys.argv[2], sys.argv[3], sys.argv[4]
        rels = [l.strip() for l in sys.stdin if l.strip()]
        data = extract_data(pre_root, post_root, rels)
        with open(out_json, "w") as f:
            json.dump(data, f, separators=(",", ":"))
        print(f"wrote {out_json}  ({data['n_total']} scanned, "
              f"{len(data['files'])} with diffs, {len(data['unmatched'])} unmatched)")
    elif mode == "render":
        # Fast render step: load extract_data JSON, build HTML — no filesystem walk.
        import shutil
        from datetime import datetime
        in_json, out = sys.argv[2], sys.argv[3]
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
