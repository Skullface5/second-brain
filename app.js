"use strict";
/* Second Brain — ledger UI.
   Same behaviour as before (search, category tabs, source filter, sort, inline expansion,
   "/" to focus). Markup changed to the two-column ledger: mono rail + entry body. */

const $ = s => document.querySelector(s);
const S = { items: [], q: "", cat: "all", src: "all", sort: "new", open: new Set() };

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
    const r = await fetch("data/news.json", {cache:"no-store"});
    if(!r.ok) throw new Error("HTTP "+r.status);
    const d = await r.json();
    S.items = (d.items||[]).filter(x => x && (x.title||x.url));
    $("#gen").textContent = d.generated ? "synced " + when(d.generated) : "";
  }catch(e){
    $("#stat").textContent = "unavailable";
    showEmpty("Could not load data/news.json. " + e.message, true);
    return;
  }
  buildControls();
  render();
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
  if(it.content && it.content.length > 40) return it.content;
  return "";
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
    const id = it.url || it.title;
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
          (body?'<div class="body">'+hl(body, terms)+"</div>":"")+
          '<div class="entry-foot">'+
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
  if(S.open.has(id)) S.open.delete(id); else S.open.add(id);
  row.classList.toggle("open");
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

load();
