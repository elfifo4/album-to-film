"""Interactive review page, served on this machine only (127.0.0.1).

Lets you click the four corners of a print, rotate, choose the enhancement level, mark a photo as
checked, exclude it and leave a note. Decisions are saved to review/overrides.json and the photo is
reprocessed at once.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import editorial, paths, process

PORT = 8765
ALLOWED_KEYS = {"quad", "rotation_cw_deg", "level", "exclude", "note", "reviewed", "emotional", "trailer", "full"}
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
         background:var(--bg); padding:6px 0 10px; margin-bottom:14px; }
  .top button:disabled { opacity:.4; cursor:default; }
  #count { color:var(--dim); }
  .tabs { display:flex; gap:4px; flex-basis:100%; border-bottom:1px solid var(--line); }
  .tabs button { background:none; border:none; border-bottom:3px solid transparent; border-radius:6px 6px 0 0;
                 padding:10px 18px; font-size:15px; font-weight:600; color:var(--dim); min-height:44px; }
  .tabs button:hover { background:#2a2826; color:var(--ink); }
  .tabs button[aria-selected="true"] { color:var(--ink); border-bottom-color:var(--accent); background:#2a2826; }
  .tabs .n { display:inline-block; min-width:26px; margin-left:8px; padding:1px 8px; border-radius:11px; font-size:12px;
             background:#3a3734; color:var(--ink); font-variant-numeric:tabular-nums; }
  .tabs button[aria-selected="true"] .n { background:var(--accent); color:#111; }
  #filmctl { display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
  #filmctl[hidden] { display:none; }
  #filmsummary { flex-basis:100%; color:var(--ink); font-variant-numeric:tabular-nums; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(180px, 1fr)); gap:12px; }
  .chapterhead { grid-column:1 / -1; margin:14px 0 0; font-size:15px; color:var(--accent); }
  .tile { background:var(--card); border-radius:8px; padding:8px; border:2px solid transparent; }
  .tile.selected { border-color:var(--accent); }
  .tile[draggable="true"] { cursor:grab; }
  .tile.dragging { opacity:.35; }
  .tile.drop-before { box-shadow:-5px 0 0 var(--manual); } .tile.drop-after { box-shadow:5px 0 0 var(--manual); }
  .tile .num { font-size:20px; font-weight:700; color:var(--ink); margin-right:6px; }
  .tile.out img { opacity:.35; }
  .chips { display:flex; gap:6px; margin-top:6px; }
  .chips button { flex:1; min-height:30px; padding:2px 0; font-size:12px; }
  .chips button.on { background:var(--accent); color:#111; font-weight:600; border-color:var(--accent); }
  .tile .seq { display:inline-block; min-width:24px; padding:0 6px; margin-right:4px; border-radius:10px; background:var(--accent);
               color:#111; font-weight:700; text-align:center; }
  .tile .mv { float:right; } .tile .mv button { min-height:26px; padding:0 8px; }
  .tile img { display:block; width:100%; height:150px; object-fit:contain; background:#111; border-radius:4px; }
  .tile .cap { margin:6px 0 2px; font-variant-numeric:tabular-nums; color:var(--dim); }
  .tile .cap b { color:var(--ink); } .tile .in { color:var(--accent); font-weight:600; }
  .stars { display:flex; gap:0; }
  .star { background:none; border:none; padding:2px 3px; min-height:30px; font-size:18px; color:#5a5550; }
  .star.on { color:#f0c040; } .star:hover { background:none; color:#ffe08a; }
  .seg { display:flex; margin-top:4px; }
  .seg button { flex:1; border-radius:0; min-height:32px; padding:4px 0; }
  .seg button:first-child { border-radius:6px 0 0 6px; } .seg button:last-child { border-radius:0 6px 6px 0; }
  .seg button.active { background:var(--accent); color:#111; font-weight:600; border-color:var(--accent); }
  #lightbox { position:fixed; inset:0; z-index:50; background:#000e; display:flex; align-items:center; justify-content:center; }
  #lightbox[hidden] { display:none; }
  #lightbox img { max-width:calc(100vw - 140px); max-height:calc(100vh - 80px); object-fit:contain; border-radius:4px; }
  #lightbox button { position:absolute; background:#2a2826; border:1px solid var(--line); color:var(--ink); border-radius:50%;
                     width:48px; height:48px; font-size:26px; line-height:1; padding:0; }
  #lightbox button:hover { background:#45413d; }
  #lbclose { top:14px; right:14px; } #lbprev { left:14px; top:50%; margin-top:-24px; } #lbnext { right:14px; top:50%; margin-top:-24px; }
  #lbcap { position:absolute; bottom:12px; left:0; right:0; text-align:center; color:var(--ink); }
  @media (max-width: 600px) { #lightbox img { max-width:100vw; } }
  section.leaving { opacity:0; transform:translateX(24px); transition:opacity .25s, transform .25s; }
  @media (prefers-reduced-motion: reduce) { section.leaving { transition:none; } }
  #status { margin-left:auto; display:flex; align-items:center; gap:8px; font-weight:600; }
  #status.saved { color:var(--high); } #status.error { color:var(--low); } #status.saving { color:var(--accent); }
  .shots { display:grid; grid-template-columns:repeat(auto-fit, minmax(230px, 1fr)); gap:10px; align-items:start; }
  figure { margin:0; } figcaption { color:var(--dim); padding-top:4px; font-size:12px; }
  figure img { display:block; width:100%; height:auto; max-height:70vh; object-fit:contain; background:#111; border-radius:4px; }
  figure.chosen img { outline:2px solid var(--accent); }
  .capture { position:relative; line-height:0; }
  .capture svg { position:absolute; inset:0; width:100%; height:100%; pointer-events:none; }
  .capture.editing { outline:2px dashed var(--manual); touch-action:none; user-select:none; }
  .handle { position:absolute; width:26px; height:26px; margin:-13px 0 0 -13px; border-radius:50%; border:3px solid #fff;
            box-shadow:0 0 0 2px var(--manual), 0 2px 8px #000a; background:rgba(60,160,235,.3); cursor:grab; touch-action:none; }
  .handle::after { content:""; position:absolute; inset:-12px; }
  .handle.dragging { cursor:grabbing; background:transparent; }
  .handle:focus-visible { outline:3px solid var(--accent); outline-offset:3px; }
  #editctl { display:flex; flex-wrap:wrap; gap:8px; align-items:center; padding:4px 8px; border-radius:8px;
             background:#22394a; border:1px solid var(--manual); }
  #editctl[hidden] { display:none; }
  #edithint { color:var(--ink); }
  figure.wide { scroll-margin-top:150px; }
  button.primary { background:#2f6f9f; border-color:var(--manual); font-weight:600; }
  button.primary:hover { background:#3a82b8; } button:disabled { opacity:.4; cursor:default; }
  figure.wide { grid-column:1 / -1; }
  figure.wide .capture { width:fit-content; max-width:100%; margin:0 auto; }
  figure.wide img { width:auto; max-width:100%; height:auto; max-height:calc(100vh - 170px); }
  #loupe { display:none; position:fixed; z-index:20; width:200px; height:200px; border-radius:50%; pointer-events:none;
           border:2px solid var(--manual); box-shadow:0 6px 22px #000c; background-color:#111; background-repeat:no-repeat; }
  #loupe::before, #loupe::after { content:""; position:absolute; }
  #loupe::before { left:50%; top:0; bottom:0; width:1px; margin-left:-.5px;
           background:linear-gradient(to bottom, #3ca0eb 0 48.5%, transparent 48.5% 51.5%, #3ca0eb 51.5%); }
  #loupe::after { top:50%; left:0; right:0; height:1px; margin-top:-.5px;
           background:linear-gradient(to right, #3ca0eb 0 48.5%, transparent 48.5% 51.5%, #3ca0eb 51.5%); }
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
The outlined result is the one saved. To fix a crop press <b>Edit corners</b>: the outline appears with four handles, drag any of them
(a magnifier shows the exact spot; arrow keys nudge a focused handle), then press <b>Apply corners</b> in the bar at the top. When a photo is
right, tick <b>Looks good</b> (or <b>Exclude</b>) and it moves from <b>To review</b> to <b>Done</b>.
<b>Duplicates</b> lists prints that were photographed more than once: keep one of each group and exclude the rest.
<b>Films</b> shows which photographs each film uses: change the stars to re-rank, or force a photo <b>In</b> or <b>Out</b>
(<b>Auto</b> lets the ranking decide). To change the order, drag a photo onto another one in the same chapter,
or use its ◀ ▶ buttons; the order is one story order shared by both films.
<b>All photos</b> lists every photograph by its number, with a button per film to put it in or take it out. There is no Save button: every change is written to
<code>review/overrides.json</code> as soon as you make it, and the status on the right confirms it.</p>
<div class="top">
  <div class="tabs" role="tablist">
    <button role="tab" id="tab-todo" data-tab="todo" aria-selected="true">To review<span class="n" id="n-todo">0</span></button>
    <button role="tab" id="tab-done" data-tab="done" aria-selected="false">Done<span class="n" id="n-done">0</span></button>
    <button role="tab" id="tab-dups" data-tab="dups" aria-selected="false">Duplicates<span class="n" id="n-dups">0</span></button>
    <button role="tab" id="tab-films" data-tab="films" aria-selected="false">Films</button>
    <button role="tab" id="tab-all" data-tab="all" aria-selected="false">All photos<span class="n" id="n-all">0</span></button>
  </div>
  <button id="undo" disabled>Undo</button>
  <button id="redo" disabled>Redo</button>
  <label id="showwrap" hidden>Show <select id="filter">
    <option value="all">All done</option><option value="mine">Changed by you</option>
    <option value="excluded">Excluded</option></select></label>
  <label>Chapter <select id="chapter"><option value="">All</option></select></label>
  <span id="editctl" hidden>
    <button class="primary" data-act="apply" id="apply" disabled>Apply corners</button>
    <button data-act="cancel">Cancel</button>
    <span id="edithint">Drag a corner, then Apply (Enter). Esc cancels.</span>
  </span>
  <span id="filmctl" hidden>
    <label>Film <select id="film"></select></label>
    <label>Show <select id="filmshow"><option value="selected">In the film</option>
      <option value="rest">Not in the film</option><option value="all">All photos</option></select></label>
    <button id="resetorder">Reset order</button>
    <span id="filmsummary"></span>
  </span>
  <span id="count"></span>
  <span id="status" class="saved" role="status" aria-live="polite">Nothing changed yet</span>
</div>
<div id="list"></div>
<div id="loupe" aria-hidden="true"></div>
<div id="lightbox" hidden role="dialog" aria-modal="true" aria-label="Enlarged photograph">
  <button id="lbclose" aria-label="Close">&times;</button>
  <button id="lbprev" aria-label="Previous">&#8249;</button>
  <img id="lbimg" alt="">
  <button id="lbnext" aria-label="Next">&#8250;</button>
  <div id="lbcap"></div>
</div>
<script>
const names = {quad:'corners', rotation_cw_deg:'rotation', level:'enhancement', exclude:'exclude', note:'note', reviewed:'looks good', emotional:'score', trailer:'trailer pick', full:'full movie pick'};
const savedAt = {}, undoStack = [], redoStack = [];
let stamp = Date.now(), results = [];
let films = {films: {}, chapter_order: []};
let tab = 'todo';      // which tab is open: 'todo' (To review) or 'done'
let editing = null;   // {id, pts: four [x, y] fractions of the capture, history: [{i, before}]}
let drag = null;
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
      ${r.needs_review ? '<span class="tag todo">needs review</span>' : ''}
      ${r.duplicate ? `<span class="tag manual">same print as ${r.duplicate.others.map(o => '#' + o).join(', ')}${r.duplicate.suggested_keep ? ' · sharpest' : ''}</span>` : ''}</h2>
    <div class="shots">
      <figure><div class="capture" data-id="${r.id}"><img loading="lazy" src="${img(r.id, 'overlay')}" alt="" draggable="false">
</div>
        <figcaption>capture + outline</figcaption></figure>
      ${fig('geom', 'geometry only')}${fig('light', 'light')}${fig('standard', 'standard')}
    </div>
    <div class="bar">
      <span class="tag ${d.level}">crop ${d.level} ${d.confidence.toFixed(2)}</span>
      <button data-act="corners" data-id="${r.id}">Edit corners</button>
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
  if (tab === 'dups') return !!r.duplicate;
  if (tab === 'todo') return r.needs_review;
  if (r.needs_review) return false;
  if (f === 'mine') return Object.keys(ov).length > 0;
  if (f === 'excluded') return !!ov.exclude;
  return true;
}
function showTab(name) {
  cancelEdit();
  tab = name;
  for (const b of document.querySelectorAll('.tabs button')) b.setAttribute('aria-selected', b.dataset.tab === tab);
  document.getElementById('showwrap').hidden = tab !== 'done';
  document.getElementById('filmctl').hidden = tab !== 'films';
  render();
  scrollTo({top: 0});
}
// Redraws the list for the current filter. A photo you just settled stays visible until the next redraw.
function tile(r) {
  const film = document.getElementById('film').value, e = r.editorial, f = e.films[film], state = f.pin || 'auto';
  const stars = [1, 2, 3, 4, 5].map(v => `<button class="star ${v <= e.emotional ? 'on' : ''}" data-act="star" data-id="${r.id}"
      data-v="${v}" aria-label="Score ${v} of 5">★</button>`).join('');
  const seg = [['in', 'In'], ['auto', 'Auto'], ['out', 'Out']].map(([k, label]) =>
    `<button class="${state === k ? 'active' : ''}" data-act="pick" data-id="${r.id}" data-v="${k}">${label}</button>`).join('');
  const seq = f.selected ? films.films[film].order.indexOf(r.id) + 1 : 0;
  return `<div class="tile ${f.selected ? 'selected' : ''}" id="t-${r.id}" draggable="true" data-chapter="${esc(r.category)}">
    <a href="${img(r.id, 'light')}" target="_blank" draggable="false"><img loading="lazy" src="${img(r.id, 'light')}" alt="" draggable="false"></a>
    <div class="cap">${seq ? `<span class="seq" title="Position in the film">${seq}</span>` : ''}<b>#${r.capture_order}</b>
      <span class="mv"><button data-act="move" data-id="${r.id}" data-v="-1" aria-label="Move earlier">◀</button><button
        data-act="move" data-id="${r.id}" data-v="1" aria-label="Move later">▶</button></span></div>
    <div class="stars" title="Emotional score (${e.emotional_source})">${stars}</div><div class="seg">${seg}</div></div>`;
}
function renderFilms() {
  const film = document.getElementById('film').value, show = document.getElementById('filmshow').value;
  const c = document.getElementById('chapter').value, order = films.chapter_order, info = films.films[film];
  let list = results.filter(r => r.editorial && (!c || r.category === c));
  if (show === 'selected') list = list.filter(r => r.editorial.films[film].selected);
  if (show === 'rest') list = list.filter(r => !r.editorial.films[film].selected);
  list.sort((a, b) => a.editorial.position - b.editorial.position);
  document.getElementById('resetorder').hidden = !films.custom_order;
  let html = '', current = null;
  for (const r of list) {
    if (r.category !== current) { current = r.category; html += `<h2 class="chapterhead">${esc(current)}</h2>`; }
    html += tile(r);
  }
  document.getElementById('list').innerHTML = html ? `<div class="grid">${html}</div>` : '<p id="empty">Nothing to show for this filter.</p>';
  const t = info.estimated_seconds, mmss = `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`;
  document.getElementById('filmsummary').textContent = `${info.label}: ${info.count} photographs, about ${mmss} ` +
    `(target ${Math.floor(info.target_seconds / 60)}:${String(info.target_seconds % 60).padStart(2, '0')}) · ` +
    order.map(ch => `${ch} ${info.by_chapter[ch] || 0}`).join(' · ');
  document.getElementById('count').textContent = `${list.length} shown`;
}
// Every photograph by number, so any of them can be named or added to a film directly.
function renderAll() {
  const c = document.getElementById('chapter').value;
  const list = results.filter(r => !c || r.category === c).sort((a, b) => a.capture_order - b.capture_order);
  const chip = (r, film, label) => {
    const on = r.editorial.films[film].selected;
    return `<button class="${on ? 'on' : ''}" data-act="toggle" data-film="${film}" data-id="${r.id}"
      aria-pressed="${on}" title="${on ? 'In this film; click to take out' : 'Not in this film; click to add'}">${on ? '✓ ' : '+ '}${label}</button>`;
  };
  const html = list.map(r => {
    const out = (r.override || {}).exclude;
    return `<div class="tile ${out ? 'out' : ''}" id="t-${r.id}">
      <a href="${img(r.id, 'light')}" target="_blank" draggable="false"><img loading="lazy" src="${img(r.id, 'light')}" alt="" draggable="false"></a>
      <div class="cap"><span class="num">${r.capture_order}</span>${esc(r.category)}${out ? ' · excluded' : ''}</div>
      ${r.editorial ? `<div class="chips">${Object.entries(films.films).map(([k, f]) => chip(r, k, f.label)).join('')}</div>` : ''}</div>`;
  }).join('');
  document.getElementById('list').innerHTML = html ? `<div class="grid">${html}</div>` : '<p id="empty">Nothing to show for this filter.</p>';
  document.getElementById('count').textContent = `${list.length} shown`;
}
async function refreshAll() {
  results = await (await fetch('/api/results')).json();
  films = await (await fetch('/api/films')).json();
}
function render() {
  document.getElementById('n-all').textContent = results.length;
  if (tab === 'films') return renderFilms();
  if (tab === 'all') return renderAll();
  const shown = results.filter(visible);
  if (tab === 'dups') shown.sort((a, b) => a.duplicate.group - b.duplicate.group || a.capture_order - b.capture_order);
  document.getElementById('list').innerHTML = shown.map(card).join('') ||
    `<p id="empty">${tab === 'todo' ? 'Nothing left to review here.' : 'Nothing to show for this filter.'}</p>`;
  updateCount();
}
function updateCount() {
  if (tab === 'films' || tab === 'all') return;
  const shown = document.querySelectorAll('#list section:not(.leaving)').length, todo = results.filter(r => r.needs_review).length;
  document.getElementById('n-todo').textContent = todo;
  document.getElementById('n-done').textContent = results.length - todo;
  const open = new Set(results.filter(r => r.duplicate && !(r.override || {}).exclude).map(r => r.duplicate.group));
  const kept = g => results.filter(r => r.duplicate && r.duplicate.group === g && !(r.override || {}).exclude).length;
  document.getElementById('n-dups').textContent = [...open].filter(g => kept(g) > 1).length;
  document.getElementById('count').textContent = `${shown} shown`;
  if (!shown && !document.getElementById('empty')) render();
}
function setStatus(kind, text) {
  const el = document.getElementById('status');
  el.className = kind;
  el.innerHTML = (kind === 'saving' ? '<span class="spin"></span>' : '') + esc(text);
}
function describe(entry) {
  if (entry.kind === 'order') return 'order';
  const r = results.find(x => x.id === entry.id);
  return `${Object.keys(entry.patch).map(k => names[k] || k).join(', ')} on #${r.capture_order}`;
}
function refreshButtons() {
  const u = document.getElementById('undo'), r = document.getElementById('redo');
  const moved = !!(editing && editing.history.length);
  u.disabled = !undoStack.length && !moved;
  u.textContent = moved ? 'Undo corner move' : undoStack.length ? `Undo: ${describe(undoStack.at(-1))}` : 'Undo';
  r.disabled = !redoStack.length;
  r.textContent = redoStack.length ? `Redo: ${describe(redoStack.at(-1))}` : 'Redo';
}

async function load() {
  setStatus('saving', 'Loading…');
  await refreshAll();
  stamp = Date.now();
  document.getElementById('film').innerHTML = Object.entries(films.films).map(([k, f]) => `<option value="${k}">${esc(f.label)}</option>`).join('');
  const chapters = [...new Set(results.map(r => r.category))];
  document.getElementById('chapter').innerHTML = '<option value="">All</option>' +
    chapters.map(c => `<option>${esc(c)}</option>`).join('');
  showTab(results.some(r => r.needs_review) ? 'todo' : 'done');
  setStatus('saved', 'Nothing changed yet');
}

// Sends one change, waits for the server to save and reprocess, then redraws that photo.
async function change(id, patch, record = true) {
  const i = results.findIndex(x => x.id === id), ov = results[i].override || {};
  const prev = {};
  for (const k in patch) prev[k] = k in ov ? ov[k] : null;
  const section = document.getElementById('s-' + id);     // absent when the photo lives in the other tab
  if (section) section.classList.add('busy');
  setStatus('saving', 'Saving…');
  try {
    const res = await fetch('/api/override', {method: 'POST', body: JSON.stringify({id, patch})});
    const out = await res.json();
    if (!res.ok || out.error) throw new Error(out.error || `HTTP ${res.status}`);
    results[i] = out;
    savedAt[id] = new Date();
    if (tab === 'films' || tab === 'all') {
      await refreshAll();
      render();
      if (record) { undoStack.push({id, patch, prev}); redoStack.length = 0; }
      setStatus('saved', `Saved at ${clock(savedAt[id])}`);
      return true;
    }
    stamp = Date.now();
    let moved = '';
    if (section && !visible(out)) {          // settled (or reopened): slide it out of this tab
      moved = out.needs_review ? ' · moved to To review' : ' · moved to Done';
      section.classList.remove('busy');
      section.classList.add('leaving');
      setTimeout(() => { section.remove(); updateCount(); }, 260);
    } else if (section) {
      section.outerHTML = card(out);
    } else if (visible(out)) {
      render();
    }
    if (record) { undoStack.push({id, patch, prev}); redoStack.length = 0; }
    setStatus('saved', `Saved at ${clock(savedAt[id])}${moved}`);
    updateCount();
    return true;
  } catch (err) {
    if (section) section.classList.remove('busy');
    setStatus('error', `Not saved: ${err.message}`);
    return false;
  } finally {
    refreshButtons();
  }
}

const clamp = v => Math.min(1, Math.max(0, v));
const captureOf = id => document.querySelector(`.capture[data-id="${id}"]`);

// Opens the corner editor on one photo: the current outline with four draggable handles.
function startEdit(id) {
  cancelEdit();
  const r = results.find(x => x.id === id), cap = captureOf(id);
  editing = {id, pts: r.detect.edit_quad_fraction.map(p => [clamp(p[0]), clamp(p[1])]), history: []};
  cap.querySelector('img').src = img(id, 'capture');      // sharp, without the drawn outline
  cap.classList.add('editing');
  cap.closest('figure').classList.add('wide');
  cap.insertAdjacentHTML('beforeend',
    `<svg viewBox="0 0 100 100" preserveAspectRatio="none"><polygon fill="rgba(60,160,235,.08)" stroke="#3ca0eb"
       stroke-width="2" vector-effect="non-scaling-stroke"/></svg>` +
    editing.pts.map((_, i) => `<div class="handle" data-i="${i}" tabindex="0" role="button" aria-label="Corner ${i + 1}, drag or use arrow keys"></div>`).join(''));
  document.getElementById('editctl').hidden = false;
  drawEdit();
  cap.closest('figure').scrollIntoView({block: 'start'});
  refreshButtons();
}
function drawEdit() {
  const cap = captureOf(editing.id);
  cap.querySelector('polygon').setAttribute('points', editing.pts.map(p => `${p[0] * 100},${p[1] * 100}`).join(' '));
  cap.querySelectorAll('.handle').forEach((h, i) => { h.style.left = editing.pts[i][0] * 100 + '%'; h.style.top = editing.pts[i][1] * 100 + '%'; });
  document.getElementById('apply').disabled = !editing.history.length;
}
function cancelEdit() {
  hideLoupe();
  drag = null;
  document.getElementById('editctl').hidden = true;
  if (!editing) return;
  const id = editing.id;
  editing = null;
  const section = document.getElementById('s-' + id);
  if (section) section.outerHTML = card(results.find(x => x.id === id));
  refreshButtons();
}
function applyEdit() {
  if (!editing || !editing.history.length) return;
  const {id, pts} = editing;
  editing = null;
  hideLoupe();
  document.getElementById('editctl').hidden = true;
  change(id, {quad: pts});
}

const loupe = document.getElementById('loupe'), RADIUS = 100;
function hideLoupe() { loupe.style.display = 'none'; }
// Magnified view of the capture around corner i, placed beside the handle so it never covers it.
function showLoupe(i) {
  const cap = captureOf(editing.id), box = cap.getBoundingClientRect(), picture = cap.querySelector('img');
  const x = editing.pts[i][0] * box.width, y = editing.pts[i][1] * box.height;
  const ZOOM = Math.min(6, Math.max(3, picture.naturalWidth / box.width));   // about one capture pixel per screen pixel
  loupe.style.display = 'block';
  loupe.style.backgroundImage = `url("${picture.src}")`;
  loupe.style.backgroundSize = `${box.width * ZOOM}px ${box.height * ZOOM}px`;
  loupe.style.backgroundPosition = `${RADIUS - x * ZOOM}px ${RADIUS - y * ZOOM}px`;
  const cx = box.left + x, cy = box.top + y;
  let left = cx + 40, top = cy - 2 * RADIUS - 40;
  if (left + 2 * RADIUS > innerWidth) left = cx - 2 * RADIUS - 40;
  if (top < 0) top = cy + 40;
  loupe.style.left = left + 'px';
  loupe.style.top = top + 'px';
}
document.addEventListener('scroll', () => { if (!drag) hideLoupe(); }, true);
document.addEventListener('pointerdown', ev => {
  const h = ev.target.closest ? ev.target.closest('.handle') : null;
  if (!h || !editing) return;
  ev.preventDefault();
  h.setPointerCapture(ev.pointerId);
  const i = +h.dataset.i, box = h.closest('.capture').getBoundingClientRect();
  // remember where inside the handle it was grabbed, so it does not jump under the pointer
  drag = {i, h, before: [...editing.pts[i]],
          dx: ev.clientX - (box.left + editing.pts[i][0] * box.width), dy: ev.clientY - (box.top + editing.pts[i][1] * box.height)};
  h.classList.add('dragging');
  showLoupe(i);
});
document.addEventListener('pointermove', ev => {
  if (!drag || !editing) return;
  const box = drag.h.closest('.capture').getBoundingClientRect();
  editing.pts[drag.i] = [+clamp((ev.clientX - drag.dx - box.left) / box.width).toFixed(5),
                         +clamp((ev.clientY - drag.dy - box.top) / box.height).toFixed(5)];
  drawEdit();
  showLoupe(drag.i);
});
function endDrag() {
  if (!drag) return;
  drag.h.classList.remove('dragging');
  const now = editing ? editing.pts[drag.i] : drag.before;
  if (editing && (now[0] !== drag.before[0] || now[1] !== drag.before[1])) editing.history.push({i: drag.i, before: drag.before});
  drag = null;
  hideLoupe();
  if (editing) drawEdit();
  refreshButtons();
}
document.addEventListener('pointerup', endDrag);
document.addEventListener('pointercancel', endDrag);
document.addEventListener('focusout', ev => { if (ev.target.classList && ev.target.classList.contains('handle') && !drag) hideLoupe(); });

// Enlarged view in place of opening the image in a new browser tab. Arrows step through the images beside it.
let lightbox = null;      // {links, i}
function showLightbox() {
  const a = lightbox.links[lightbox.i], card = a.closest('section, .tile');
  document.getElementById('lbimg').src = a.href;
  const what = a.closest('figure')?.querySelector('figcaption')?.textContent || '';
  const label = card.querySelector('h2, .cap b, .num');      // card heading, film tile, or All-photos tile
  const which = (label.classList.contains('num') ? '#' : '') + label.textContent.replace(/\s+/g, ' ').trim();
  document.getElementById('lbcap').textContent = `${which}${what ? ' · ' + what : ''}  (${lightbox.i + 1} of ${lightbox.links.length})`;
  document.getElementById('lbprev').hidden = document.getElementById('lbnext').hidden = lightbox.links.length < 2;
  document.getElementById('lightbox').hidden = false;
  document.body.style.overflow = 'hidden';
}
function closeLightbox() {
  lightbox = null;
  document.getElementById('lightbox').hidden = true;
  document.body.style.overflow = '';
}
function stepLightbox(d) {
  lightbox.i = (lightbox.i + d + lightbox.links.length) % lightbox.links.length;
  showLightbox();
}
document.addEventListener('click', ev => {
  const a = ev.target.closest ? ev.target.closest('#list a[target="_blank"]') : null;
  if (a) {
    ev.preventDefault();
    const links = [...a.closest('.shots, .grid').querySelectorAll('a[target="_blank"]')];
    lightbox = {links, i: links.indexOf(a)};
    return showLightbox();
  }
  if (!lightbox) return;
  if (ev.target.id === 'lbprev') return stepLightbox(-1);
  if (ev.target.id === 'lbnext') return stepLightbox(1);
  if (ev.target.id === 'lbclose' || ev.target.id === 'lightbox') closeLightbox();    // the X, or the dark area around the photo
});

const replay = (entry, which) => entry.kind === 'order' ? sendOrder({order: entry[which]}, false) : change(entry.id, entry[which], false);

// Story order: one order per chapter, shared by both films.
async function sendOrder(payload, record = true) {
  setStatus('saving', 'Saving…');
  try {
    const res = await fetch('/api/order', {method: 'POST', body: JSON.stringify(payload)});
    const out = await res.json();
    if (!res.ok || out.error) throw new Error(out.error || `HTTP ${res.status}`);
    await refreshAll();
    render();
    if (record) { undoStack.push({kind: 'order', prev: out.prev, patch: out.order}); redoStack.length = 0; }
    setStatus('saved', `Order saved at ${clock(new Date())}`);
    return true;
  } catch (err) {
    setStatus('error', `Not saved: ${err.message}`);
    return false;
  } finally {
    refreshButtons();
  }
}
function stepMove(id, direction) {            // one place earlier or later among the tiles shown in its chapter
  const me = document.getElementById('t-' + id);
  const same = [...document.querySelectorAll('.tile')].filter(t => t.dataset.chapter === me.dataset.chapter);
  const other = same[same.indexOf(me) + direction];
  if (other) sendOrder({move: {id, target: other.id.slice(2), place: direction < 0 ? 'before' : 'after'}});
}
let dragId = null;
const clearDropMarks = () => document.querySelectorAll('.drop-before, .drop-after').forEach(t => t.classList.remove('drop-before', 'drop-after'));
document.addEventListener('dragstart', ev => {
  const t = ev.target.closest ? ev.target.closest('.tile') : null;
  if (!t) return;
  dragId = t.id.slice(2);
  ev.dataTransfer.effectAllowed = 'move';
  ev.dataTransfer.setData('text/plain', dragId);
  t.classList.add('dragging');
});
document.addEventListener('dragover', ev => {
  const t = ev.target.closest ? ev.target.closest('.tile') : null, me = dragId && document.getElementById('t-' + dragId);
  clearDropMarks();
  if (!t || !me || t === me || t.dataset.chapter !== me.dataset.chapter) return;
  ev.preventDefault();
  const box = t.getBoundingClientRect();
  t.classList.add(ev.clientX > box.left + box.width / 2 ? 'drop-after' : 'drop-before');
});
document.addEventListener('drop', ev => {
  const t = document.querySelector('.drop-before, .drop-after');
  if (!t || !dragId) return;
  ev.preventDefault();
  sendOrder({move: {id: dragId, target: t.id.slice(2), place: t.classList.contains('drop-after') ? 'after' : 'before'}});
});
document.addEventListener('dragend', () => { clearDropMarks(); document.querySelector('.tile.dragging')?.classList.remove('dragging'); dragId = null; });

async function undo() {
  if (editing && editing.history.length) {
    const step = editing.history.pop();
    editing.pts[step.i] = step.before;
    drawEdit();
    return refreshButtons();
  }
  const entry = undoStack.pop();
  if (!entry) return;
  if (await replay(entry, 'prev')) redoStack.push(entry); else undoStack.push(entry);
  refreshButtons();
  document.getElementById('s-' + entry.id)?.scrollIntoView({block: 'nearest'});
}
async function redo() {
  const entry = redoStack.pop();
  if (!entry) return;
  if (await replay(entry, 'patch')) undoStack.push(entry); else redoStack.push(entry);
  refreshButtons();
  document.getElementById('s-' + entry.id)?.scrollIntoView({block: 'nearest'});
}
for (const b of document.querySelectorAll('.tabs button')) b.addEventListener('click', () => showTab(b.dataset.tab));
document.getElementById('resetorder').addEventListener('click', () => sendOrder({order: {}}));
document.getElementById('film').addEventListener('change', render);
document.getElementById('filmshow').addEventListener('change', render);
document.getElementById('undo').addEventListener('click', undo);
document.getElementById('redo').addEventListener('click', redo);
document.getElementById('filter').addEventListener('change', () => { cancelEdit(); render(); });
document.getElementById('chapter').addEventListener('change', () => { cancelEdit(); render(); });
document.addEventListener('keydown', ev => {
  if (lightbox) {
    if (ev.key === 'Escape') closeLightbox();
    if (ev.key === 'ArrowLeft') stepLightbox(-1);
    if (ev.key === 'ArrowRight') stepLightbox(1);
    return;
  }
  if (ev.key === 'Escape') return cancelEdit();
  if (ev.target.matches('input[type=text]')) return;
  if (editing && ev.key === 'Enter') { ev.preventDefault(); return applyEdit(); }
  const step = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]}[ev.key];
  if (editing && step && ev.target.classList.contains('handle')) {      // nudge by one screen pixel, ten with Shift
    ev.preventDefault();
    const i = +ev.target.dataset.i, box = ev.target.closest('.capture').getBoundingClientRect(), n = ev.shiftKey ? 10 : 1;
    editing.history.push({i, before: [...editing.pts[i]]});
    editing.pts[i] = [+clamp(editing.pts[i][0] + step[0] * n / box.width).toFixed(5), +clamp(editing.pts[i][1] + step[1] * n / box.height).toFixed(5)];
    drawEdit();
    showLoupe(i);
    return refreshButtons();
  }
  if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === 'z') { ev.preventDefault(); ev.shiftKey ? redo() : undo(); }
});

document.addEventListener('click', ev => {
  const b = ev.target.closest('button[data-act]');
  if (!b) return;
  const id = b.dataset.id, r = results.find(x => x.id === id);
  if (b.dataset.act === 'toggle') return change(id, {[b.dataset.film]: r.editorial.films[b.dataset.film].selected ? 'out' : 'in'});
  if (b.dataset.act === 'move') return stepMove(id, +b.dataset.v);
  if (b.dataset.act === 'star') return change(id, {emotional: +b.dataset.v});
  if (b.dataset.act === 'pick') return change(id, {[document.getElementById('film').value]: b.dataset.v === 'auto' ? null : b.dataset.v});
  if (b.dataset.act === 'corners') return startEdit(id);
  if (b.dataset.act === 'apply') return applyEdit();
  if (b.dataset.act === 'cancel') return cancelEdit();
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


def apply_order(payload: dict) -> dict:
    """Change the story order: move one photo next to another, or replace the whole order (undo, reset)."""
    with _lock:
        if "order" in payload:
            previous, story = editorial.load_story_order(), payload["order"]
            editorial.save_story_order(story)
        else:
            results = process.run("all", quiet=True)
            candidates = [r for r in results if not r["override"].get("exclude")]
            move = payload["move"]
            previous, story = editorial.move_in_story(candidates, move["id"], move["target"], move["place"])
        process.run("all", quiet=True)
    return {"prev": previous, "order": story}


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
        if route == "/api/films":
            return self._send(json.dumps({"films": process.LAST_SELECTION.get("films", {}),
                                          "chapter_order": process.LAST_SELECTION.get("chapter_order", []),
                                          "custom_order": process.LAST_SELECTION.get("custom_order", False)}).encode("utf-8"),
                              "application/json")
        if route.startswith("/previews/"):
            base = (paths.PREVIEWS_DIR / "photos").resolve()
            target = (base / route[len("/previews/"):]).resolve()
            if base in target.parents and target.is_file():
                return self._send(target.read_bytes(), "image/jpeg")
        self._send(b"not found", "text/plain", 404)

    def do_POST(self):
        if self.path not in ("/api/override", "/api/order"):
            return self._send(b"not found", "text/plain", 404)
        try:
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            result = apply_order(payload) if self.path == "/api/order" else apply_override(payload["id"], payload["patch"])
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
