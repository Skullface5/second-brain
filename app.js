"use strict";
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
  const d = new Date(ts); if(isNaN(d)) return ts.slice(0,10);
  const s = (Date.now()-d)/1000;
  if(s < 3600) return Math.max(1,(s/60|0))+"m ago";
  if(s < 86400) return (s/3600|0)+"h ago";
  if(s < 604800) return (s/86400|0)+"d ago";
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
    $("#stat").textContent = "data unavailable";
    $("#empty").hidden = false;
    $("#empty").querySelector("p").textContent = "Could not load data/news.json — " + e.message;
    return;
  }
  buildControls();
  render();
}

function buildControls(){
  const cats = {};
  S.items.forEach(i => cats[i.category||"other"] = (cats[i.category||"other"]||0)+1);
  const order = Object.keys(cats).sort((a,b)=>cats[b]-cats[a]);
  const c = $("#cats");
  c.innerHTML = "";
  const mk = (id,label,n) => {
    const b = document.createElement("button");
    b.innerHTML = esc(label) + '<span class="n">' + n + "</span>";
    b.dataset.id = id;
    if(S.cat===id) b.classList.add("on");
    b.onclick = () => { S.cat = id; [...c.children].forEach(x=>x.classList.toggle("on", x.dataset.id===id)); render(); };
    return b;
  };
  c.appendChild(mk("all","All",S.items.length));
  order.forEach(k => c.appendChild(mk(k, k.charAt(0).toUpperCase()+k.slice(1), cats[k])));

  const srcs = [...new Set(S.items.map(i=>i.source).filter(Boolean))].sort();
  $("#src").innerHTML = '<option value="all">All sources</option>' +
    srcs.map(s=>'<option value="'+esc(s)+'">'+esc(s)+'</option>').join("");
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

function render(){
  const terms = S.q.toLowerCase().split(/\s+/).filter(Boolean);
  let rows = S.items.filter(i => match(i, terms));

  if(S.sort==="new") rows.sort((a,b)=> (b.date||"").localeCompare(a.date||""));
  else if(S.sort==="old") rows.sort((a,b)=> (a.date||"").localeCompare(b.date||""));
  else rows.sort((a,b)=> (a.title||"").localeCompare(b.title||""));

  $("#count").textContent = rows.length + " of " + S.items.length + " items";
  $("#foot-count").textContent = S.items.length + " tracked";
  $("#stat").textContent = S.items.length + " items";

  const list = $("#list");
  $("#empty").hidden = rows.length > 0;
  list.innerHTML = rows.slice(0,600).map(it => {
    const cat = it.category||"other";
    const id = it.url||it.title;
    const open = S.open.has(id);
    const body = bodyFor(it);
    const m = it.meta;
    let stats = "";
    if(m){
      const parts = [];
      if(m.downloads) parts.push('<span class="kv"><b>'+fmtN(m.downloads)+'</b> downloads</span>');
      if(m.likes) parts.push('<span class="kv"><b>'+fmtN(m.likes)+'</b> likes</span>');
      if(m.pipeline) parts.push('<span class="kv">'+esc(m.pipeline)+'</span>');
      if(m.library) parts.push('<span class="kv">'+esc(m.library)+'</span>');
      stats = '<div class="stats">'+parts.join("")+'</div>';
      if(it.tags && it.tags.length)
        stats += '<div class="tags">'+it.tags.map(t=>'<span class="tag">'+esc(t)+'</span>').join("")+'</div>';
    }
    return '<div class="row'+(open?" open":"")+'" data-id="'+esc(id)+'">'+
      '<div class="rtop"><span class="badge '+esc(cat)+'">'+esc(cat)+'</span>'+
      '<span class="rtitle">'+hl(it.title||"(untitled)", terms)+'</span>'+
      '<svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="m6 9 6 6 6-6"/></svg></div>'+
      (it.summary?'<div class="rsum">'+hl(it.summary, terms)+'</div>':'')+
      stats+
      (body?'<div class="body">'+hl(body, terms)+'</div>':'')+
      '<div class="rfoot"><span class="src">'+esc(it.source||"")+'</span><span>'+when(it.date)+'</span>'+
      '<a class="open-link" href="'+esc(it.url||"#")+'" target="_blank" rel="noopener" title="Open source">'+
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>'+
      'source</a></div></div>';
  }).join("");
}

$("#list").addEventListener("click", e => {
  if(e.target.closest(".open-link")) return;
  const row = e.target.closest(".row");
  if(!row) return;
  const id = row.dataset.id;
  if(S.open.has(id)) S.open.delete(id); else S.open.add(id);
  row.classList.toggle("open");
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
document.addEventListener("keydown", e => {
  if(e.key==="/" && document.activeElement!==q){ e.preventDefault(); q.focus(); }
  if(e.key==="Escape" && document.activeElement===q){ q.blur(); }
});

load();