"""Interactive review page, served on this machine only (127.0.0.1).

Lets you click the four corners of a print, rotate, choose the enhancement level, exclude a photo and
leave a note. Decisions are saved to review/overrides.json and the photo is reprocessed at once.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import paths, process

PORT = 8765
ALLOWED_KEYS = {"quad", "rotation_cw_deg", "level", "exclude", "note"}
_lock = threading.Lock()

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pilot review</title>
<style>
  :root { --bg:#1c1b1a; --card:#272523; --ink:#ece7df; --dim:#a39c90; --accent:#d8b98a; --line:#3a3734;
          --high:#5ac85a; --medium:#ebc83c; --low:#e65046; --manual:#3ca0eb; }
  * { box-sizing:border-box; }
  body { margin:0; padding:16px; background:var(--bg); color:var(--ink); font:14px/1.45 -apple-system, sans-serif; }
  h1 { font-size:20px; margin:0 0 4px; } h2 { font-size:16px; margin:0 0 10px; }
  p.lead { color:var(--dim); max-width:85ch; margin:0 0 20px; }
  section { background:var(--card); border-radius:8px; padding:14px; margin:0 0 18px; }
  section { position:relative; }
  section.busy { pointer-events:none; }
  section.busy > :not(.loading) { opacity:.35; }
  .loading { display:none; position:absolute; inset:0; z-index:2; align-items:center; justify-content:center; gap:10px;
             font-weight:600; }
  section.busy .loading { display:flex; }
  .loading span.box { background:#111c; padding:10px 16px; border-radius:8px; display:flex; align-items:center; gap:10px; }
  .spin { width:18px; height:18px; border:3px solid #fff4; border-top-color:var(--accent); border-radius:50%;
          animation:spin .8s linear infinite; }
  @keyframes spin { to { transform:rotate(360deg); } }
  @media (prefers-reduced-motion: reduce) { .spin { animation-duration:3s; } }
  .top { position:sticky; top:0; z-index:5; display:flex; flex-wrap:wrap; gap:8px; align-items:center;
         background:var(--bg); padding:10px 0; margin-bottom:14px; border-bottom:1px solid var(--line); }
  .top button:disabled { opacity:.4; cursor:default; }
  #status { margin-left:auto; display:flex; align-items:center; gap:8px; font-weight:600; }
  #status.saved { color:var(--high); } #status.error { color:var(--low); } #status.saving { color:var(--accent); }
  .yours { color:var(--manual); } .savedat { color:var(--high); }
  .shots { display:grid; grid-template-columns:repeat(auto-fit, minmax(230px, 1fr)); gap:10px; align-items:start; }
  figure { margin:0; } figcaption { color:var(--dim); padding-top:4px; font-size:12px; }
  figure img { display:block; width:100%; height:auto; max-height:70vh; object-fit:contain; background:#111; border-radius:4px; }
  figure.chosen img { outline:2px solid var(--accent); }
  .capture { position:relative; line-height:0; }
  .capture svg { position:absolute; inset:0; width:100%; height:100%; }
  .capture.picking { cursor:crosshair; outline:2px dashed var(--manual); }
  .bar { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-top:12px; }
  button, select, input[type=text] { font:inherit; color:var(--ink); background:#35322f; border:1px solid var(--line);
          border-radius:6px; padding:6px 10px; min-height:34px; }
  button:hover { background:#45413d; cursor:pointer; } input[type=text] { flex:1 1 220px; }
  .tag { display:inline-block; padding:1px 8px; border-radius:10px; font-size:12px; color:#111; font-weight:600; }
  .high { background:var(--high); } .medium { background:var(--medium); } .low { background:var(--low); color:#fff; }
  .manual { background:var(--manual); } .event { background:var(--accent); }
  .meta { color:var(--dim); margin-top:8px; overflow-wrap:anywhere; } .flag { color:#e0a060; }
  section.excluded h2 { text-decoration:line-through; color:var(--dim); }
</style></head><body>
<h1>Pilot review</h1>
<p class="lead">Each row shows the capture with the outline used, then geometry only, <b>light</b> and <b>standard</b>.
The outlined result is the one saved. To fix a crop press <b>Set corners</b> and click the four corners of the
photograph on the capture, in any order. There is no Save button: every change is written to
<code>review/overrides.json</code> and applied as soon as you make it, and the status on the right confirms it.</p>
<div class="top">
  <button id="undo" disabled>Undo</button>
  <button id="redo" disabled>Redo</button>
  <span id="status" class="saved" role="status" aria-live="polite">Nothing changed yet</span>
</div>
<div id="list"></div>
<script>
const colours = {high:'#5ac85a', medium:'#ebc83c', low:'#e65046', manual:'#3ca0eb'};
const names = {quad:'corners', rotation_cw_deg:'rotation', level:'enhancement', exclude:'exclude', note:'note'};
const picking = {}, savedAt = {}, undoStack = [], redoStack = [];
let stamp = Date.now(), results = [];
const img = (id, kind) => `/previews/${id}_${kind}.jpg?v=${stamp}`;
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const clock = d => d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'});

function card(r) {
  const d = r.detect, o = r.orientation, e = r.enhancement, ov = r.override || {};
  const pts = d.quad_fraction.map(p => `${p[0]*100},${p[1]*100}`).join(' ');
  const fig = (kind, label) => `<figure class="${(kind === 'geom' ? 'none' : kind) === e.chosen ? 'chosen' : ''}">
      <a href="${img(r.id, kind)}" target="_blank"><img src="${img(r.id, kind)}" alt=""></a><figcaption>${label}</figcaption></figure>`;
  const faces = o.faces ? `faces suggest ${o.faces.rotation_cw_deg}° (${o.faces.decisive ? 'decisive' : 'not decisive'})` : 'faces not checked';
  const mine = Object.keys(ov).map(k => names[k] || k);
  return `<section id="s-${r.id}" class="${ov.exclude ? 'excluded' : ''}">
    <div class="loading"><span class="box"><span class="spin"></span>Saving and reprocessing…</span></div>
    <h2>#${r.capture_order} &nbsp; ${esc(r.filename)} &nbsp; <span class="tag event">${r.category}</span></h2>
    <div class="shots">
      <figure><div class="capture" data-id="${r.id}"><img src="${img(r.id, 'capture')}" alt="" draggable="false">
        <svg viewBox="0 0 100 100" preserveAspectRatio="none"><polygon points="${pts}" fill="none"
          stroke="${colours[d.level]}" stroke-width="2" vector-effect="non-scaling-stroke"/><g class="dots"></g></svg></div>
        <figcaption>capture + outline</figcaption></figure>
      ${fig('geom', 'geometry only')}${fig('light', 'light')}${fig('standard', 'standard')}
    </div>
    <div class="bar">
      <span class="tag ${d.level}">crop ${d.level} ${d.confidence.toFixed(2)}</span>
      <button data-act="corners" data-id="${r.id}">Set corners</button>
      ${ov.quad ? `<button data-act="auto" data-id="${r.id}">Back to automatic crop</button>` : ''}
      <button data-act="ccw" data-id="${r.id}" aria-label="Rotate left">&#8634; Rotate left</button>
      <button data-act="cw" data-id="${r.id}" aria-label="Rotate right">&#8635; Rotate right</button>
      <label>Enhancement <select data-act="level" data-id="${r.id}">
        ${['none', 'light', 'standard'].map(l => `<option ${l === e.chosen ? 'selected' : ''}>${l}</option>`).join('')}</select></label>
      <label><input type="checkbox" data-act="exclude" data-id="${r.id}" ${ov.exclude ? 'checked' : ''}> Exclude</label>
      <input type="text" data-act="note" data-id="${r.id}" placeholder="Note" value="${esc(ov.note || '')}">
      ${mine.length ? `<button data-act="reset" data-id="${r.id}">Reset this photo</button>` : ''}
    </div>
    <div class="meta">
      ${mine.length ? `<span class="yours">Your changes: ${mine.join(', ')}.</span>` : 'Automatic result, no changes by you.'}
      ${savedAt[r.id] ? `<span class="savedat">Saved ${clock(savedAt[r.id])}.</span>` : ''}<br>
      Rotation ${o.rotation_cw_deg}° from ${o.source} (${o.confidence}); ${faces}.
      ${d.cropped ? '' : 'Crop not applied, whole frame kept. '}Edited variant: ${r.variant_kind || 'none'}.
      Output ${r.output.width}×${r.output.height}.
      ${r.flags.length ? `<span class="flag">${esc(r.flags.join(', '))}</span>` : ''}</div>
  </section>`;
}

function setStatus(kind, text) {
  const el = document.getElementById('status');
  el.className = kind;
  el.innerHTML = (kind === 'saving' ? '<span class="spin"></span>' : '') + esc(text);
}
function describe(entry) {
  const r = results.find(x => x.id === entry.id);
  return `${Object.keys(entry.patch).map(k => names[k] || k).join(', ')} on #${r.capture_order}`;
}
function refreshButtons() {
  const u = document.getElementById('undo'), r = document.getElementById('redo');
  const pickingPoints = Object.values(picking).some(p => p.length);
  u.disabled = !undoStack.length && !pickingPoints;
  u.textContent = pickingPoints ? 'Undo last corner' : undoStack.length ? `Undo: ${describe(undoStack.at(-1))}` : 'Undo';
  r.disabled = !redoStack.length;
  r.textContent = redoStack.length ? `Redo: ${describe(redoStack.at(-1))}` : 'Redo';
}

async function load() {
  results = await (await fetch('/api/results')).json();
  stamp = Date.now();
  document.getElementById('list').innerHTML = results.map(card).join('');
}

// Sends one change, waits for the server to save and reprocess, then redraws that photo.
async function change(id, patch, record = true) {
  const i = results.findIndex(x => x.id === id), ov = results[i].override || {};
  const prev = {};
  for (const k in patch) prev[k] = k in ov ? ov[k] : null;
  const section = document.getElementById('s-' + id);
  section.classList.add('busy');
  setStatus('saving', 'Saving…');
  try {
    const res = await fetch('/api/override', {method: 'POST', body: JSON.stringify({id, patch})});
    const out = await res.json();
    if (!res.ok || out.error) throw new Error(out.error || `HTTP ${res.status}`);
    results[i] = out;
    savedAt[id] = new Date();
    stamp = Date.now();
    section.outerHTML = card(out);
    if (record) { undoStack.push({id, patch, prev}); redoStack.length = 0; }
    setStatus('saved', `Saved to overrides.json at ${clock(savedAt[id])}`);
    return true;
  } catch (err) {
    section.classList.remove('busy');
    setStatus('error', `Not saved: ${err.message}`);
    return false;
  } finally {
    refreshButtons();
  }
}

function drawDots(id) {
  const cap = document.querySelector(`.capture[data-id="${id}"]`);
  cap.querySelector('.dots').innerHTML = picking[id].map(p =>
    `<circle cx="${p[0]*100}" cy="${p[1]*100}" r="1.2" fill="#3ca0eb"/>`).join('');
}
function cancelPicking() {
  for (const id in picking) { delete picking[id]; document.getElementById('s-' + id).outerHTML = card(results.find(x => x.id === id)); }
  refreshButtons();
}
async function undo() {
  const active = Object.keys(picking).find(id => picking[id].length);
  if (active) { picking[active].pop(); drawDots(active); return refreshButtons(); }
  const entry = undoStack.pop();
  if (!entry) return;
  if (await change(entry.id, entry.prev, false)) redoStack.push(entry); else undoStack.push(entry);
  refreshButtons();
  document.getElementById('s-' + entry.id).scrollIntoView({block: 'nearest'});
}
async function redo() {
  const entry = redoStack.pop();
  if (!entry) return;
  if (await change(entry.id, entry.patch, false)) undoStack.push(entry); else redoStack.push(entry);
  refreshButtons();
  document.getElementById('s-' + entry.id).scrollIntoView({block: 'nearest'});
}
document.getElementById('undo').addEventListener('click', undo);
document.getElementById('redo').addEventListener('click', redo);
document.addEventListener('keydown', ev => {
  if (ev.key === 'Escape') return cancelPicking();
  if (ev.target.matches('input[type=text]')) return;
  if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === 'z') { ev.preventDefault(); ev.shiftKey ? redo() : undo(); }
});

document.addEventListener('click', ev => {
  const cap = ev.target.closest('.capture');
  if (cap && picking[cap.dataset.id]) {
    const id = cap.dataset.id, box = cap.getBoundingClientRect(), pts = picking[id];
    pts.push([+((ev.clientX - box.left) / box.width).toFixed(4), +((ev.clientY - box.top) / box.height).toFixed(4)]);
    drawDots(id);
    if (pts.length === 4) { delete picking[id]; change(id, {quad: pts}); }
    return refreshButtons();
  }
  const b = ev.target.closest('button[data-act]');
  if (!b) return;
  const id = b.dataset.id, r = results.find(x => x.id === id);
  if (b.dataset.act === 'corners') {
    picking[id] = [];
    const c = document.querySelector(`.capture[data-id="${id}"]`);
    c.classList.add('picking'); c.querySelector('polygon').style.display = 'none';
    b.textContent = 'Click 4 corners (Esc cancels)';
  }
  if (b.dataset.act === 'auto') change(id, {quad: null});
  if (b.dataset.act === 'cw') change(id, {rotation_cw_deg: (r.orientation.rotation_cw_deg + 90) % 360});
  if (b.dataset.act === 'ccw') change(id, {rotation_cw_deg: (r.orientation.rotation_cw_deg + 270) % 360});
  if (b.dataset.act === 'reset') change(id, Object.fromEntries(Object.keys(r.override).map(k => [k, null])));
});
document.addEventListener('change', ev => {
  const t = ev.target, id = t.dataset.id;
  if (t.dataset.act === 'level') change(id, {level: t.value});
  if (t.dataset.act === 'exclude') change(id, {exclude: t.checked});
  if (t.dataset.act === 'note') change(id, {note: t.value});
});
load();
</script></body></html>"""


def apply_override(photo_id: str, patch: dict) -> dict:
    """Merge a change into overrides.json (None removes a key), reprocess the pilot, return that photo's result."""
    with _lock:
        overrides = process.load_overrides()
        entry = overrides.get(photo_id, {})
        for key, value in patch.items():
            if key not in ALLOWED_KEYS:
                raise ValueError(f"unknown override key: {key}")
            if value is None or value == "" or value is False:
                entry.pop(key, None)
            else:
                entry[key] = value
        if entry:
            overrides[photo_id] = entry
        else:
            overrides.pop(photo_id, None)
        process.save_overrides(overrides)
        results = process.run(quiet=True)
    return next(r for r in results if r["id"] == photo_id)


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = self.path.split("?")[0]
        if route == "/":
            return self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")
        if route == "/api/results":
            with _lock:
                results = process.run(quiet=True)
            return self._send(json.dumps(results).encode("utf-8"), "application/json")
        if route.startswith("/previews/"):
            base = (paths.PREVIEWS_DIR / "pilot").resolve()
            target = (base / route[len("/previews/"):]).resolve()
            if base in target.parents and target.is_file():
                return self._send(target.read_bytes(), "image/jpeg")
        self._send(b"not found", "text/plain", 404)

    def do_POST(self):
        if self.path != "/api/override":
            return self._send(b"not found", "text/plain", 404)
        try:
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            result = apply_override(payload["id"], payload["patch"])
        except Exception as e:
            return self._send(json.dumps({"error": repr(e)}).encode("utf-8"), "application/json", 400)
        self._send(json.dumps(result).encode("utf-8"), "application/json")

    def log_message(self, *args):
        pass


def run(port: int = PORT) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"review: http://127.0.0.1:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
