"""Interactive review page, served on this machine only (127.0.0.1).

Lets you click the four corners of a print, rotate, choose the enhancement level, mark a photo as
checked, exclude it and leave a note. Decisions are saved to review/overrides.json and the photo is
reprocessed at once.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import paths, process

PORT = 8765
ALLOWED_KEYS = {"quad", "rotation_cw_deg", "level", "exclude", "note", "reviewed"}
_lock = threading.Lock()

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Photo review</title>
<style>
  :root { --bg:#1c1b1a; --card:#272523; --ink:#ece7df; --dim:#a39c90; --accent:#d8b98a; --line:#3a3734;
          --high:#5ac85a; --medium:#ebc83c; --low:#e65046; --manual:#3ca0eb; }
  * { box-sizing:border-box; }
  body { margin:0; padding:16px; background:var(--bg); color:var(--ink); font:14px/1.45 -apple-system, sans-serif; }
  h1 { font-size:20px; margin:0 0 4px; } h2 { font-size:16px; margin:0 0 10px; }
  p.lead { color:var(--dim); max-width:85ch; margin:0 0 12px; }
  section { position:relative; background:var(--card); border-radius:8px; padding:14px; margin:0 0 18px; }
  section.busy { pointer-events:none; }
  section.busy > :not(.loading) { opacity:.35; }
  .loading { display:none; position:absolute; inset:0; z-index:2; align-items:center; justify-content:center; font-weight:600; }
  section.busy .loading { display:flex; }
  .loading span.box { background:#111c; padding:10px 16px; border-radius:8px; display:flex; align-items:center; gap:10px; }
  .spin { width:18px; height:18px; border:3px solid #fff4; border-top-color:var(--accent); border-radius:50%;
          animation:spin .8s linear infinite; }
  @keyframes spin { to { transform:rotate(360deg); } }
  @media (prefers-reduced-motion: reduce) { .spin { animation-duration:3s; } }
  .top { position:sticky; top:0; z-index:5; display:flex; flex-wrap:wrap; gap:8px; align-items:center;
         background:var(--bg); padding:10px 0; margin-bottom:14px; border-bottom:1px solid var(--line); }
  .top button:disabled { opacity:.4; cursor:default; }
  #count { color:var(--dim); }
  #status { margin-left:auto; display:flex; align-items:center; gap:8px; font-weight:600; }
  #status.saved { color:var(--high); } #status.error { color:var(--low); } #status.saving { color:var(--accent); }
  .shots { display:grid; grid-template-columns:repeat(auto-fit, minmax(230px, 1fr)); gap:10px; align-items:start; }
  figure { margin:0; } figcaption { color:var(--dim); padding-top:4px; font-size:12px; }
  figure img { display:block; width:100%; height:auto; max-height:70vh; object-fit:contain; background:#111; border-radius:4px; }
  figure.chosen img { outline:2px solid var(--accent); }
  .capture { position:relative; line-height:0; }
  .capture svg { position:absolute; inset:0; width:100%; height:100%; pointer-events:none; }
  .capture.picking { cursor:crosshair; outline:2px dashed var(--manual); touch-action:none; user-select:none; }
  figure.wide { grid-column:1 / -1; }
  figure.wide .capture { width:fit-content; max-width:100%; margin:0 auto; }
  figure.wide img { width:auto; max-width:100%; height:auto; max-height:calc(100vh - 90px); }
  #loupe { display:none; position:fixed; z-index:20; width:200px; height:200px; border-radius:50%; pointer-events:none;
           border:2px solid var(--manual); box-shadow:0 6px 22px #000c; background-color:#111; background-repeat:no-repeat; }
  #loupe::before, #loupe::after { content:""; position:absolute; }
  #loupe::before { left:50%; top:0; bottom:0; width:1px; margin-left:-.5px;
           background:linear-gradient(to bottom, #3ca0eb 0 44%, transparent 44% 56%, #3ca0eb 56%); }
  #loupe::after { top:50%; left:0; right:0; height:1px; margin-top:-.5px;
           background:linear-gradient(to right, #3ca0eb 0 44%, transparent 44% 56%, #3ca0eb 56%); }
  .bar { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-top:12px; }
  button, select, input[type=text] { font:inherit; color:var(--ink); background:#35322f; border:1px solid var(--line);
          border-radius:6px; padding:6px 10px; min-height:34px; }
  button:hover { background:#45413d; cursor:pointer; } input[type=text] { flex:1 1 220px; }
  .tag { display:inline-block; padding:1px 8px; border-radius:10px; font-size:12px; color:#111; font-weight:600; }
  .high { background:var(--high); } .medium { background:var(--medium); } .low { background:var(--low); color:#fff; }
  .manual { background:var(--manual); } .event { background:var(--accent); } .todo { background:var(--low); color:#fff; }
  .meta { color:var(--dim); margin-top:8px; overflow-wrap:anywhere; } .flag { color:#e0a060; }
  .yours { color:var(--manual); } .savedat { color:var(--high); }
  section.excluded h2 { text-decoration:line-through; color:var(--dim); }
  #empty { color:var(--dim); padding:30px 0; }
</style></head><body>
<h1>Photo review</h1>
<p class="lead">Each row shows the capture with the outline used, then geometry only, <b>light</b> and <b>standard</b>.
The outlined result is the one saved. To fix a crop press <b>Set corners</b> and click the four corners of the
photograph, in any order; a magnifier follows the pointer (on a touch screen, drag and release). Tick
<b>Looks good</b> to clear a photo from the "Needs review" list. There is no Save button: every change is written to
<code>review/overrides.json</code> as soon as you make it, and the status on the right confirms it.</p>
<div class="top">
  <button id="undo" disabled>Undo</button>
  <button id="redo" disabled>Redo</button>
  <label>Show <select id="filter">
    <option value="needs">Needs review</option><option value="all">All photos</option>
    <option value="mine">Changed by you</option><option value="excluded">Excluded</option></select></label>
  <label>Chapter <select id="chapter"><option value="">All</option></select></label>
  <span id="count"></span>
  <span id="status" class="saved" role="status" aria-live="polite">Nothing changed yet</span>
</div>
<div id="list"></div>
<div id="loupe" aria-hidden="true"></div>
<script>
const names = {quad:'corners', rotation_cw_deg:'rotation', level:'enhancement', exclude:'exclude', note:'note', reviewed:'looks good'};
const picking = {}, savedAt = {}, undoStack = [], redoStack = [];
let stamp = Date.now(), results = [];
const img = (id, kind) => `/previews/${id}_${kind}.jpg?v=${stamp}`;
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const clock = d => d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'});

function card(r) {
  const d = r.detect, o = r.orientation, e = r.enhancement, ov = r.override || {};
  const fig = (kind, label) => `<figure class="${(kind === 'geom' ? 'none' : kind) === e.chosen ? 'chosen' : ''}">
      <a href="${img(r.id, kind)}" target="_blank"><img loading="lazy" src="${img(r.id, kind)}" alt=""></a><figcaption>${label}</figcaption></figure>`;
  const faces = o.faces ? `faces suggest ${o.faces.rotation_cw_deg}° (${o.faces.decisive ? 'decisive' : 'not decisive'})` : 'faces not checked';
  const mine = Object.keys(ov).map(k => names[k] || k);
  return `<section id="s-${r.id}" class="${ov.exclude ? 'excluded' : ''}">
    <div class="loading"><span class="box"><span class="spin"></span>Saving and reprocessing…</span></div>
    <h2>#${r.capture_order} &nbsp; ${esc(r.filename)} &nbsp; <span class="tag event">${r.category}</span>
      ${r.needs_review ? '<span class="tag todo">needs review</span>' : ''}</h2>
    <div class="shots">
      <figure><div class="capture" data-id="${r.id}"><img loading="lazy" src="${img(r.id, 'overlay')}" alt="" draggable="false">
        <svg viewBox="0 0 100 100" preserveAspectRatio="none"><g class="dots"></g></svg></div>
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
      <label><input type="checkbox" data-act="reviewed" data-id="${r.id}" ${ov.reviewed ? 'checked' : ''}> Looks good</label>
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

function visible(r) {
  const f = document.getElementById('filter').value, c = document.getElementById('chapter').value, ov = r.override || {};
  if (c && r.category !== c) return false;
  if (f === 'needs') return r.needs_review;
  if (f === 'mine') return Object.keys(ov).length > 0;
  if (f === 'excluded') return !!ov.exclude;
  return true;
}
// Redraws the list for the current filter. A photo you just settled stays visible until the next redraw.
function render() {
  const shown = results.filter(visible);
  document.getElementById('list').innerHTML = shown.map(card).join('') ||
    '<p id="empty">Nothing to show for this filter.</p>';
  updateCount();
}
function updateCount() {
  const shown = document.querySelectorAll('#list section').length, todo = results.filter(r => r.needs_review).length;
  document.getElementById('count').textContent = `${shown} shown · ${todo} of ${results.length} need review`;
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
  setStatus('saving', 'Loading…');
  results = await (await fetch('/api/results')).json();
  stamp = Date.now();
  const chapters = [...new Set(results.map(r => r.category))];
  document.getElementById('chapter').innerHTML = '<option value="">All</option>' +
    chapters.map(c => `<option>${esc(c)}</option>`).join('');
  if (!results.some(r => r.needs_review)) document.getElementById('filter').value = 'all';
  render();
  setStatus('saved', 'Nothing changed yet');
}

// Sends one change, waits for the server to save and reprocess, then redraws that photo.
async function change(id, patch, record = true) {
  const i = results.findIndex(x => x.id === id), ov = results[i].override || {};
  const prev = {};
  for (const k in patch) prev[k] = k in ov ? ov[k] : null;
  let section = document.getElementById('s-' + id);
  if (!section) { document.getElementById('filter').value = 'all'; render(); section = document.getElementById('s-' + id); }
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
    updateCount();
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
    `<ellipse cx="${p[0]*100}" cy="${p[1]*100}" rx="0.5" ry="0.9" fill="#3ca0eb" stroke="#fff" stroke-width="1" vector-effect="non-scaling-stroke"/>`).join('');
}
const loupe = document.getElementById('loupe'), RADIUS = 100;
function hideLoupe() { loupe.style.display = 'none'; }
// Shows a magnified view of the capture under the pointer, offset so it never covers the spot being placed.
function moveLoupe(ev) {
  const cap = ev.target.closest ? ev.target.closest('.capture.picking') : null;
  if (!cap) return hideLoupe();
  const box = cap.getBoundingClientRect(), x = ev.clientX - box.left, y = ev.clientY - box.top;
  if (x < 0 || y < 0 || x > box.width || y > box.height) return hideLoupe();
  const picture = cap.querySelector('img');
  const ZOOM = Math.min(6, Math.max(3, picture.naturalWidth / box.width));   // about one capture pixel per screen pixel
  loupe.style.display = 'block';
  loupe.style.backgroundImage = `url("${picture.src}")`;
  loupe.style.backgroundSize = `${box.width * ZOOM}px ${box.height * ZOOM}px`;
  loupe.style.backgroundPosition = `${RADIUS - x * ZOOM}px ${RADIUS - y * ZOOM}px`;
  let left = ev.clientX + 28, top = ev.clientY - 2 * RADIUS - 28;
  if (left + 2 * RADIUS > innerWidth) left = ev.clientX - 2 * RADIUS - 28;
  if (top < 0) top = ev.clientY + 28;
  loupe.style.left = left + 'px';
  loupe.style.top = top + 'px';
}
document.addEventListener('pointermove', moveLoupe);
document.addEventListener('pointerdown', moveLoupe);
document.addEventListener('scroll', hideLoupe, true);
// A corner is placed where the pointer is released, so a finger can drag to refine before letting go.
document.addEventListener('pointerup', ev => {
  const cap = ev.target.closest ? ev.target.closest('.capture.picking') : null;
  if (!cap || !picking[cap.dataset.id]) return;
  const id = cap.dataset.id, box = cap.getBoundingClientRect(), pts = picking[id];
  const x = (ev.clientX - box.left) / box.width, y = (ev.clientY - box.top) / box.height;
  if (x < 0 || y < 0 || x > 1 || y > 1) return;
  pts.push([+x.toFixed(5), +y.toFixed(5)]);
  drawDots(id);
  if (ev.pointerType !== 'mouse') hideLoupe();
  if (pts.length === 4) { delete picking[id]; hideLoupe(); change(id, {quad: pts}); }
  refreshButtons();
});

function cancelPicking() {
  hideLoupe();
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
document.getElementById('filter').addEventListener('change', () => { cancelPicking(); render(); });
document.getElementById('chapter').addEventListener('change', () => { cancelPicking(); render(); });
document.addEventListener('keydown', ev => {
  if (ev.key === 'Escape') return cancelPicking();
  if (ev.target.matches('input[type=text]')) return;
  if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === 'z') { ev.preventDefault(); ev.shiftKey ? redo() : undo(); }
});

document.addEventListener('click', ev => {
  const b = ev.target.closest('button[data-act]');
  if (!b) return;
  const id = b.dataset.id, r = results.find(x => x.id === id);
  if (b.dataset.act === 'corners') {
    picking[id] = [];
    const c = document.querySelector(`.capture[data-id="${id}"]`);
    c.querySelector('img').src = img(id, 'capture');      // sharp, without the drawn outline
    c.classList.add('picking');
    c.closest('figure').classList.add('wide');
    c.scrollIntoView({block: 'nearest'});
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
  if (t.dataset.act === 'reviewed') change(id, {reviewed: t.checked});
  if (t.dataset.act === 'note') change(id, {note: t.value});
});
load();
</script></body></html>"""


def apply_override(photo_id: str, patch: dict) -> dict:
    """Merge a change into overrides.json (None removes a key), reprocess, return that photo's result."""
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
        results = process.run("all", quiet=True)
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
                results = process.run("all", quiet=True)
            return self._send(json.dumps(results).encode("utf-8"), "application/json")
        if route.startswith("/previews/"):
            base = (paths.PREVIEWS_DIR / "photos").resolve()
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
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        raise SystemExit(f"The review page is already running: open http://127.0.0.1:{port} "
                         "(or stop the other copy first).")
    print(f"review: http://127.0.0.1:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
