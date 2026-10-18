"use strict";
/* Second Brain — ledger UI.
   Same behaviour as before (search, category tabs, source filter, sort, inline expansion,
   "/" to focus). Markup changed to the two-column ledger: mono rail + entry body. */

const $ = s => document.querySelector(s);
const S = { items: [], byId: {}, q: "", cat: "all", src: "all", sort: "new",
            open: new Set(), meta: {}, fullTried: new Set() };
const FULL_CACHE = "sb-full-v1";     // Cache API store for the page shards
const SHELL_CACHE = "sb-shell-v1";   // app shell + news.json

function esc(s){ return (s||"").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }

function hl(s, terms){
  let out = esc(s);
  for(const t of terms){
    if(!t) continue;
    out = out.replace(new RegExp("("+t.replace(/[.*+?^${}()|[\]\\]/g,"\\$&")+")","ig"), "<mark>$1</mark>");
  }
  return out;
}

function when(ts){
  if(!ts) return "";
  const d = new Date(ts); if(isNaN(d)) return String(ts).slice(0,10);
  const s = (Date.now()-d)/1000;
  if(s < 60) return "now";
  if(s < 3600) return (s/60|0)+"m";
  if(s < 86400) return (s/3600|0)+"h";
  if(s < 604800) return (s/86400|0)+"d";
  return d.toISOString().slice(0,10);
}

function fmtN(n){
  if(!n) return null;
  if(n>=1e6) return (n/1e6).toFixed(1)+"M";
  if(n>=1e3) return (n/1e3).toFixed(1)+"k";
  return ""+n;
}

async function load(){
  try{
    /* No cache:no-store — that flag is exactly what made the page unreadable
       offline. The service worker now owns freshness (network-first, cache on
       failure) and the browser cache still revalidates on every visit. */
    const r = await fetch("data/news.json");
    if(!r.ok) throw new Error("HTTP "+r.status);
    const d = await r.json();
    S.items = (d.items||[]).filter(x => x && (x.title||x.url));
    S.meta = d;
    S.byId = {};
    S.items.forEach(i => { if(i.id) S.byId[i.id] = i; });
    const g = d.generated ? when(d.generated) : "";
    $("gen").textContent = g ? "synced " + g : "";
  }catch(e){
    $("stat").textContent = navigator.onLine ? "unavailable" : "offline";
    const cached = await shellFallback();
    if(cached) return;
    showEmpty("Could not load data/news.json. " + e.message, true);
    return;
  }
  buildControls();
  renderStatus();
  render();
}

/* If the network is gone but a previous copy exists, serve that instead of an
   error page — that is the whole point of the offline layer. */
async function shellFallback(){
  if(!("caches" in window)) return false;
  try{
    const c = await caches.open(SHELL_CACHE);
    const r = await c.match("data/news.json");
    if(!r) return false;
    const d = await r.json();
    S.items = (d.items||[]).filter(x => x && (x.title||x.url));
    S.meta = d;
    S.byId = {};
    S.items.forEach(i => { if(i.id) S.byId[i.id] = i; });
    $("gen").textContent = "offline copy";
    buildControls();
    renderStatus();
    render();
    return true;
  }catch(_){ return false; }
}

/* E — thin status strip: last sync + a dot per source (red when a source has
   produced nothing for three runs in a row, which is how one dies quietly). */
function renderStatus(){
  const strip = $("#strip");
  if(!strip) return;
  const src = (S.meta && S.meta.sources) || {};
  const names = Object.keys(src);
  if(!names.length){ strip.hidden = true; return; }
  strip.hidden = false;
  const full = S.meta.full_count || 0;
  strip.innerHTML =
    '<span class="st-lbl">sources</span>' +
    names.sort().map(n => {
      const h = src[n] || {};
      const cls = h.ok === false ? "bad" : (h.stale ? "warn" : "good");
      const title = n + " · tracked " + (h.tracked||0) + " · last new " + (h.last_new||0) +
                    (h.error ? " · " + h.error : "");
      return '<span class="dot '+cls+'" title="'+esc(title)+'"><i></i>'+esc(n)+'</span>';
    }).join("") +
    '<span class="sep"></span>' +
    '<span class="st-lbl">full pages ' + full + '</span>';
}

function buildControls(){
  const cats = {};
  S.items.forEach(i => { const k = i.category||"other"; cats[k] = (cats[k]||0)+1; });
  const c = $("#cats");
  c.innerHTML = "";
  const mk = (id,label,n) => {
    const b = document.createElement("button");
    b.type = "button";
    b.setAttribute("role","tab");
    b.innerHTML = esc(label) + '<span class="n">' + n + "</span>";
    b.dataset.id = id;
    b.setAttribute("aria-selected", String(S.cat===id));
    if(S.cat===id) b.classList.add("on");
    b.onclick = () => {
      S.cat = id;
      [...c.children].forEach(x => {
        const on = x.dataset.id===id;
        x.classList.toggle("on", on);
        x.setAttribute("aria-selected", String(on));
      });
      render();
    };
    return b;
  };
  c.appendChild(mk("all","all",S.items.length));
  Object.keys(cats).sort((a,b)=>cats[b]-cats[a]).forEach(k => c.appendChild(mk(k,k,cats[k])));

  const srcs = [...new Set(S.items.map(i=>i.source).filter(Boolean))].sort();
  $("#src").innerHTML = '<option value="all">all</option>' +
    srcs.map(s=>'<option value="'+esc(s)+'">'+esc(s)+"</option>").join("");
}

function match(it, terms){
  if(S.cat !== "all" && (it.category||"other") !== S.cat) return false;
  if(S.src !== "all" && it.source !== S.src) return false;
  if(!terms.length) return true;
  const hay = [it.title,it.summary,it.content,it.source,it.category,(it.tags||[]).join(" ")]
    .join(" ").toLowerCase();
  return terms.every(t => hay.includes(t));
}

function bodyFor(it){
  if(it.full && it.full.length > 40) return it.full;      // the whole page, when loaded
  if(it.content && it.content.length > 40) return it.content;
  return "";
}

function rowId(it){ return it.id || it.url || it.title; }

/* The full page lives in data/full/<id>.json so the phone only downloads a page
   when it is opened. Once fetched it is cached (Cache API) and readable offline
   forever. */
async function loadFull(id, row){
  const it = S.byId[id];
  if(!it || !it.has_full || it.full) return;
  if(S.fullTried.has(id)) return;
  S.fullTried.add(id);
  const body = row.querySelector(".body");
  const note = row.querySelector(".fullnote");
  if(note) note.textContent = "fetching full page…";
  try{
    const r = await fetch("data/full/" + id + ".json");
    if(!r.ok) throw new Error("HTTP "+r.status);
    const d = await r.json();
    it.full = d.full || "";
    it.word_count = d.word_count || it.word_count;
    if(body){ body.innerHTML = hl(it.full, S.q.toLowerCase().split(/\s+/).filter(Boolean)); }
    if(note) note.textContent = (it.word_count||0) + " words · saved for offline";
  }catch(e){
    if(note) note.textContent = navigator.onLine ? "full page unavailable" : "offline — not saved yet";
  }
}

function showEmpty(msg, isError){
  const el = $("#empty");
  el.hidden = false;
  el.querySelector("h2").textContent = isError ? "Data unavailable." : "Nothing matches that.";
  $("#empty-msg").textContent = msg || "Try fewer words, or clear the filters.";
  $("#empty-reset").style.display = isError ? "none" : "";
}

function render(){
  const terms = S.q.toLowerCase().split(/\s+/).filter(Boolean);
  let rows = S.items.filter(i => match(i, terms));

  if(S.sort==="new") rows.sort((a,b)=> String(b.date||"").localeCompare(String(a.date||"")));
  else if(S.sort==="old") rows.sort((a,b)=> String(a.date||"").localeCompare(String(b.date||"")));
  else rows.sort((a,b)=> String(a.title||"").localeCompare(String(b.title||"")));

  $("#count").textContent = rows.length + " of " + S.items.length + " entries";
  $("#foot-count").textContent = S.items.length + " tracked";
  $("#stat").textContent = S.items.length + " entries";

  const list = $("#list");
  if(!rows.length){ list.innerHTML = ""; showEmpty(); return; }
  $("#empty").hidden = true;

  list.innerHTML = rows.slice(0,600).map(it => {
    const cat = it.category || "other";
    const id = rowId(it);
    const open = S.open.has(id);
    const body = bodyFor(it);
    const m = it.meta;

    let stats = "";
    if(m){
      const parts = [];
      if(m.downloads) parts.push("<span>downloads <b>"+fmtN(m.downloads)+"</b></span>");
      if(m.likes)     parts.push("<span>likes <b>"+fmtN(m.likes)+"</b></span>");
      if(m.pipeline)  parts.push("<span>"+esc(m.pipeline)+"</span>");
      if(m.library)   parts.push("<span>"+esc(m.library)+"</span>");
      if(parts.length) stats = '<div class="meta">'+parts.join("")+"</div>";
      if(it.tags && it.tags.length)
        stats += '<div class="tags">'+it.tags.map(t=>'<span class="tag">'+esc(t)+"</span>").join("")+"</div>";
    }

    return '<article class="entry'+(open?" open":"")+'" data-id="'+esc(id)+'" tabindex="0">'+
        '<div class="rail">'+
          '<span class="cat '+esc(cat)+'">'+esc(cat)+"</span>"+
          '<span class="src">'+esc(it.source||"")+"</span>"+
          '<span class="ago">'+when(it.date)+"</span>"+
        "</div>"+
        '<div class="entry-body">'+
          '<h3 class="etitle"><span>'+hl(it.title||"(untitled)", terms)+"</span>"+
            '<svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>'+
          "</h3>"+
          (it.summary?'<p class="esum">'+hl(it.summary, terms)+"</p>":"")+
          stats+
          (it.has_full?'<div class="fchips"><span class="chip">full page · '+(it.word_count||0)+' words</span></div>':"")+
          (body?'<div class="body">'+hl(body, terms)+"</div>":"")+
          '<div class="entry-foot">'+
            '<span class="fullnote"></span>'+
            (it.url?'<a class="openlink" href="'+esc(it.url)+'" target="_blank" rel="noopener">'+
              '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>'+
              "open source</a>":'<span>no link</span>')+
          "</div>"+
        "</div>"+
      "</article>";
  }).join("");
}

/* ---------- interaction ---------- */
$("#list").addEventListener("click", e => {
  if(e.target.closest(".openlink")) return;
  const row = e.target.closest(".entry");
  if(!row) return;
  const id = row.dataset.id;
  const opening = !S.open.has(id);
  if(opening) S.open.add(id); else S.open.delete(id);
  row.classList.toggle("open");
  if(opening) loadFull(id, row);
});
$("#list").addEventListener("keydown", e => {
  if(e.key!=="Enter" && e.key!==" ") return;
  if(e.target.closest(".openlink")) return;
  const row = e.target.closest(".entry");
  if(!row) return;
  e.preventDefault();
  row.click();
});

const q = $("#q");
let tmr;
q.addEventListener("input", () => {
  $("#clear").classList.toggle("on", !!q.value);
  clearTimeout(tmr);
  tmr = setTimeout(() => { S.q = q.value.trim(); render(); }, 90);
});
$("#clear").onclick = () => { q.value=""; S.q=""; $("#clear").classList.remove("on"); q.focus(); render(); };
$("#src").onchange = e => { S.src = e.target.value; render(); };
$("#sort").onchange = e => { S.sort = e.target.value; render(); };
$("#empty-reset").onclick = () => {
  q.value=""; S.q=""; S.cat="all"; S.src="all"; S.sort="new";
  $("#clear").classList.remove("on");
  $("#src").value="all"; $("#sort").value="new";
  buildControls(); render();
};
document.addEventListener("keydown", e => {
  if(e.key==="/" && document.activeElement!==q && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)){
    e.preventDefault(); q.focus();
  }
  if(e.key==="Escape" && document.activeElement===q){ q.blur(); }
});

/* ---------- offline layer (C2) ----------
   Three layers, cheapest first:
     1. service worker -> the app shell, news.json and every page already opened
        stay readable with no connection at all
     2. page shards    -> opening an entry caches just that page
     3. this button    -> pulls EVERY page, then builds one self-contained HTML
        file with all content inline: a true offline copy of the whole brain. */

const OFFLINE = { saved: new Map(), busy: false };

async function getShard(id){
  const url = "data/full/" + id + ".json";
  const cacheable = ("caches" in window);
  if(cacheable){
    try{
      const c = await caches.open(FULL_CACHE);
      const hit = await c.match(url);
      if(hit) return await hit.json();
    }catch(_){}
  }
  const r = await fetch(url);
  if(!r.ok) throw new Error("HTTP "+r.status);
  /* Cache it right here rather than trusting the service worker to do it: on a
     first visit the SW may not be active yet, and "save for offline" must not
     depend on that timing. */
  if(cacheable){
    try{ const c = await caches.open(FULL_CACHE); await c.put(url, r.clone()); }catch(_){}
  }
  return await r.json();
}

function setOfflineStatus(msg){
  const el = $("#offline-status");
  if(el) el.textContent = msg;
}

async function saveAllOffline(){
  if(OFFLINE.busy) return;
  OFFLINE.busy = true;
  const btn = $("#offline-save");
  if(btn){ btn.disabled = true; }
  try{
    const idx = await (await fetch("data/full/index.json")).json();
    const ids = (idx && idx.ids) || [];
    setOfflineStatus("0 / " + ids.length + " pages");
    let done = 0, failed = 0;
    const queue = ids.slice();
    const workers = Array.from({length: 4}, async () => {
      while(queue.length){
        const id = queue.shift();
        try{ OFFLINE.saved.set(id, await getShard(id)); }
        catch(_){ failed++; }
        done++;
        setOfflineStatus(done + " / " + ids.length + " pages" + (failed ? " (" + failed + " failed)" : ""));
        if(btn) btn.textContent = "saving " + Math.round(done*100/ids.length) + "%";
      }
    });
    await Promise.all(workers);
    setOfflineStatus(ids.length + " pages stored" + (failed ? ", " + failed + " failed" : "") + " — export ready");
    if(btn){ btn.disabled = false; btn.textContent = "export offline copy"; btn.dataset.ready = "1"; }
  }catch(e){
    setOfflineStatus("could not save all pages: " + e.message);
    if(btn){ btn.disabled = false; btn.textContent = "save for offline"; }
  }
  OFFLINE.busy = false;
}

function exportOffline(){
  const style = `
    :root{--ink:#dcd7ce;--ink-soft:#b3ada2;--ink-mute:#857f76;--bg:#131310;--bg-raise:#191914;
    --rule:#2a2921;--brass:#c9a227;--jade:#86a888}
    *{box-sizing:border-box}
    body{margin:0;background:var(--bg);color:var(--ink);line-height:1.6;
      font:16px/1.6 -apple-system,"Segoe UI",Roboto,sans-serif;padding:28px 20px 80px}
    .wrap{max-width:66ch;margin:0 auto}
    h1{font:600 30px/1.2 Georgia,serif;margin:0 0 6px}
    .sub{color:var(--ink-mute);font-size:13px;margin-bottom:26px}
    article{border-top:1px solid var(--rule);padding:22px 0}
    h2{font:600 20px/1.3 Georgia,serif;margin:0 0 6px}
    .meta{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-mute);margin-bottom:10px}
    .meta a{color:var(--brass);text-decoration:none}
    .body{white-space:pre-wrap;font-size:15px;color:var(--ink-soft);margin-top:10px}
    .nofull{color:var(--ink-mute);font-size:13px;font-style:italic}`;
  const parts = S.items.map(it => {
    const shard = OFFLINE.saved.get(it.id) || (it.full ? {full: it.full, word_count: it.word_count} : null);
    const full = shard && shard.full ? shard.full : "";
    return "<article><h2>" + esc(it.title || "(untitled)") + "</h2>" +
      '<div class="meta">' + esc(it.source || "") + " · " + esc(String(it.date || "").slice(0, 10)) +
      (it.url ? ' · <a href="' + esc(it.url) + '">source</a>' : "") +
      " · " + (full ? (shard.word_count || full.split(" ").length) + " words" : "summary only") +
      "</div>" +
      (it.summary ? "<p>" + esc(it.summary) + "</p>" : "") +
      (full ? '<div class="body">' + esc(full) + "</div>"
            : '<div class="nofull">Full text not captured for this entry yet.</div>') +
      "</article>";
  }).join("");
  const html = "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">" +
    '<meta name="viewport" content="width=device-width,initial-scale=1">' +
    "<title>Second Brain — offline copy</title><style>" + style + "</style></head><body><div class=\"wrap\">" +
    "<h1>Second Brain</h1>" +
    '<div class="sub">Offline copy · ' + S.items.length + " entries · exported " +
    new Date().toISOString().slice(0, 16).replace("T", " ") + " · no network needed</div>" +
    parts + "</div></body></html>";
  const blob = new Blob([html], {type: "text/html"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "second-brain-offline-" + new Date().toISOString().slice(0, 10) + ".html";
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 2000);
}

function wireOffline(){
  const save = $("#offline-save");
  if(save) save.onclick = () => {
    if(save.dataset.ready) exportOffline(); else saveAllOffline();
  };
  const exp = $("#offline-export");
  if(exp) exp.onclick = () => exportOffline();
  const net = () => {
    document.body.classList.toggle("is-offline", !navigator.onLine);
    if(!navigator.onLine) setOfflineStatus("offline — showing what was saved");
  };
  window.addEventListener("online", net);
  window.addEventListener("offline", net);
  net();
}

if("serviceWorker" in navigator){
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  });
}
wireOffline();

load();
