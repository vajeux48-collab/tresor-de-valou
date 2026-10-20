#!/usr/bin/env python3
"""Tirage d'équipes avec QR code, sans compte : serveur autonome (Python 3, aucune installation).

Lancer :
    python3 serveur_tirage.py
    python3 serveur_tirage.py --port 8000 --password monmotdepasse
    python3 serveur_tirage.py --url https://mon-adresse-publique.example   # si vous passez par un tunnel

Ensuite :
  - Organisateur : ouvrez l'adresse affichée + /organisateur (mot de passe affiché au démarrage).
    Vous y trouvez le QR code, les places prises et les noms qui arrivent en direct.
  - Joueurs : ils scannent le QR code, saisissent leur nom, appuient sur Jouer et reçoivent leur numéro d'équipe.
    Aucun compte n'est nécessaire.

Les joueurs doivent pouvoir joindre votre ordinateur :
  - même réseau Wi-Fi que vous : l'adresse affichée au démarrage suffit ;
  - joueurs à distance : utilisez un tunnel (par exemple cloudflared ou ngrok) et passez son adresse avec --url.

Les résultats sont gardés dans tirage_etat.json (le tirage survit à un redémarrage).
Chaque navigateur ne peut tirer qu'une fois (l'identité est mémorisée dans le navigateur).
"""
import argparse
import json
import os
import re
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOCK = threading.Lock()
STATE_FILE = os.path.join(os.environ.get("TIRAGE_DATA_DIR", "."), "tirage_etat.json")
STATE = {"teams": 8, "size": 10, "players": []}
CFG = {"password": "", "play_url": ""}
ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


# ---------------------------------------------------------------- état
def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("players"), list):
            STATE["teams"] = int(data.get("teams", 8))
            STATE["size"] = int(data.get("size", 10))
            STATE["players"] = data["players"]
    except (OSError, ValueError):
        pass


def save_state():
    tmp = STATE_FILE + ".tmp"
    os.makedirs(os.path.dirname(os.path.abspath(tmp)), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(STATE, f, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)


def build_state(client_id, is_admin):
    teams, size = STATE["teams"], STATE["size"]
    counts = [0] * (teams + 1)
    for p in STATE["players"]:
        if 1 <= p["team"] <= teams:
            counts[p["team"]] += 1
    mine = None
    for p in STATE["players"]:
        if client_id and p["id"] == client_id:
            mine = {"team": p["team"], "name": p["name"]}
    out = {
        "teams": teams,
        "size": size,
        "total": sum(counts),
        "counts": counts[1:],
        "mine": mine,
        "admin": is_admin,
        "play_url": CFG["play_url"],
    }
    if is_admin:
        out["players"] = [
            {"name": p["name"], "team": p["team"], "at": p["at"]} for p in STATE["players"]
        ]
    return out


def assign_team(client_id, name):
    """Attribue une place libre tirée au hasard. Retourne (code HTTP, dict)."""
    with LOCK:
        for p in STATE["players"]:
            if p["id"] == client_id:
                return 200, {"team": p["team"], "name": p["name"], "deja": True}
        teams, size = STATE["teams"], STATE["size"]
        counts = [0] * (teams + 1)
        for p in STATE["players"]:
            if 1 <= p["team"] <= teams:
                counts[p["team"]] += 1
        free = [max(0, size - counts[t]) for t in range(1, teams + 1)]
        total_free = sum(free)
        if total_free <= 0:
            return 409, {"erreur": "Toutes les places sont prises."}
        r = secrets.randbelow(total_free)
        team = 1
        for i, f in enumerate(free):
            if r < f:
                team = i + 1
                break
            r -= f
        STATE["players"].append(
            {"id": client_id, "name": name, "team": team, "at": int(time.time() * 1000)}
        )
        save_state()
        return 200, {"team": team, "name": name, "deja": False}


# ---------------------------------------------------------------- pages
CSS = """
/* Peña Valou : Féria Cévennes / Pays Basque */
:root{--bg:#fff0d8;--surface:#fffaf0;--surface-2:#f6dfb8;--fg:#38221d;--muted:#71534a;--line:#e0bd8c;--accent:#bb202b;--accent-fg:#fff;--ok:#1c6c50;--warn:#a62d25;--fd:"Bricolage Grotesque","Trebuchet MS",sans-serif;--fb:"IBM Plex Sans",system-ui,sans-serif;--fm:"IBM Plex Mono",ui-monospace,monospace}
body{background:repeating-linear-gradient(90deg,#bd2630 0 50px,#fff7e8 50px 100px);min-height:100vh;position:relative}
body:before{content:"";position:fixed;inset:0;background:#fff0d8b8;pointer-events:none}
.wrap{position:relative}
.panel{border:4px solid #fff;border-radius:16px;box-shadow:0 9px 0 #85242b,0 15px 36px #512a2740;background:#fff9e9}
h1{color:#a9222d;text-transform:uppercase;text-shadow:2px 2px #e8c388}
button.primary{background:#bd2630;border-color:#8c1d26;border-radius:8px;font-weight:800}
button:not(.primary){border-radius:8px}
.bignum.done{color:#bd2630!important;border-color:#d4a743!important;background:#ffedc6!important}
.play:before{content:"🐂  LE TRÉSOR DE VALOU  🐗";display:block;width:100%;padding:14px 4px;background:#1f5849;color:#fff5df;border:3px solid #d8ad52;border-radius:9px;font-weight:800;letter-spacing:.06em;box-sizing:border-box}
.play:after{content:"30 ANS DE VALENTIN · 31 OCTOBRE 2026 · CÉVENNES × PAYS BASQUE";font-size:11px;font-weight:800;letter-spacing:.08em;color:#81262b;text-align:center}
header:before{content:"🐂  PEÑA VALOU · FÉRIA · CÉVENNES × PAYS BASQUE  🐗";display:block;background:#1f5849;color:#fff5df;border:3px solid #d8ad52;border-radius:9px;padding:12px;margin-bottom:18px;text-align:center;font-weight:800}
.team h3{border-bottom-color:#bd2630}.fill{background:#bd2630}
@media(max-width:560px){body{padding:12px}.panel{padding:16px}}

:root{--bg:#eef0f3;--surface:#fff;--surface-2:#e4e8ee;--fg:#1b2330;--muted:#5d6877;--line:#cdd4de;--accent:#c2410c;--accent-fg:#fff;--ok:#17703f;--warn:#a35200;
--fd:"Bricolage Grotesque","Trebuchet MS",sans-serif;--fb:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;--fm:"IBM Plex Mono",ui-monospace,Menlo,monospace}
@media (prefers-color-scheme:dark){:root{--bg:#121820;--surface:#1b232e;--surface-2:#26313f;--fg:#e8ecf2;--muted:#9aa5b5;--line:#34404f;--accent:#f08a54;--accent-fg:#1a0e07;--ok:#5fcf92;--warn:#f0b259;color-scheme:dark}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 var(--fb);padding:28px max(16px,env(safe-area-inset-right)) 48px max(16px,env(safe-area-inset-left))}
h1,h2,h3{font-family:var(--fd);margin:0}
.wrap{max-width:1180px;margin:0 auto;display:grid;gap:24px}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:18px;display:grid;gap:16px;min-width:0}
label{font-size:12px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);display:block;margin-bottom:6px}
input{width:100%;font:14px/1.45 var(--fm);color:var(--fg);background:var(--bg);border:1px solid var(--line);border-radius:4px;padding:10px}
input:focus-visible,button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
button{font:600 14px var(--fb);border-radius:4px;padding:10px 16px;cursor:pointer;border:1px solid var(--line);background:var(--surface-2);color:var(--fg)}
button:disabled{opacity:.55;cursor:not-allowed}
button.primary{background:var(--accent);border-color:var(--accent);color:var(--accent-fg)}
.msg{font-size:13px;margin:0;min-height:1.4em}.msg.err{color:var(--warn)}
.hint{font-size:13px;color:var(--muted);margin:0}
.actions{display:flex;gap:10px;flex-wrap:wrap}
"""

PLAYER_HTML = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Le Trésor de Valou — Tirage</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,700&family=IBM+Plex+Sans:wght@400;600&family=IBM+Plex+Mono:wght@400&display=swap">
<style>%%CSS%%
.play{max-width:460px;margin:0 auto;width:100%;text-align:center;justify-items:center;padding:28px 22px}
.play h1{font-size:1.5rem;text-wrap:balance}
.bignum{font-family:var(--fd);font-weight:700;font-size:clamp(6rem,32vw,9rem);line-height:1;width:100%;padding:20px 8px;background:var(--bg);border:2px solid var(--line);border-radius:6px;font-variant-numeric:tabular-nums}
.bignum.done{border-color:var(--accent);color:var(--accent)}
.field{width:100%;text-align:left}.field input{font:1.05rem var(--fb);padding:12px}
button.big{font-size:1.15rem;padding:14px 28px;width:100%}
.label{margin:0;font-size:1.05rem;min-height:1.6em}
</style></head><body>
<div class="wrap"><section class="panel play">
  <h1>Quelle est ton équipe ?</h1>
  <div class="bignum" id="bignum" aria-hidden="true">?</div>
  <p class="label" id="label" aria-live="polite">Chargement…</p>
  <div class="field"><label for="pname">Nom du joueur</label>
    <input id="pname" type="text" maxlength="30" placeholder="Votre prénom et nom" autocomplete="name"></div>
  <button class="primary big" id="play" type="button" disabled>Jouer</button>
  <p class="msg" id="msg" role="status"></p>
  <p class="hint" id="left"></p>
</section></div>
<script>
(function(){
  var $=function(i){return document.getElementById(i)};
  var cid="";
  try{cid=localStorage.getItem("tirage-id")||""}catch(e){}
  if(!/^[A-Za-z0-9_-]{8,64}$/.test(cid)){
    var a=new Uint8Array(16);crypto.getRandomValues(a);
    cid=Array.prototype.map.call(a,function(b){return("0"+b.toString(16)).slice(-2)}).join("");
    try{localStorage.setItem("tirage-id",cid)}catch(e){}
  }
  var st=null,busy=false,rolling=false;
  function render(){
    if(rolling||!st)return;
    var big=$("bignum"),btn=$("play"),pn=$("pname");
    var free=st.teams*st.size-st.total;
    if(st.mine){
      big.textContent=st.mine.team;big.className="bignum done";
      $("label").textContent="Vous êtes dans l'équipe "+st.mine.team+". Retenez ce numéro.";
      pn.value=st.mine.name;pn.disabled=true;btn.disabled=true;btn.textContent="Numéro déjà tiré";
      $("left").textContent=st.total+" / "+(st.teams*st.size)+" places prises";return;
    }
    big.className="bignum";big.textContent="?";btn.textContent="Jouer";pn.disabled=busy;
    $("left").textContent=free+" place"+(free>1?"s":"")+" restante"+(free>1?"s":"")+" sur "+(st.teams*st.size);
    if(free<=0){$("label").textContent="Toutes les places sont prises.";btn.disabled=true;return}
    $("label").textContent="Appuyez sur Jouer pour connaître votre équipe.";btn.disabled=busy;
  }
  function refresh(){
    fetch("/api/etat?id="+cid,{cache:"no-store"}).then(function(r){return r.json()}).then(function(j){st=j;render()})
      .catch(function(){if(!st)$("label").textContent="Serveur injoignable. Vérifiez votre connexion."});
  }
  $("play").addEventListener("click",function(){
    if(busy)return;
    var name=$("pname").value.trim().slice(0,30),msg=$("msg");
    if(!name){msg.className="msg err";msg.textContent="Saisissez votre nom pour jouer.";$("pname").focus();return}
    busy=true;rolling=true;msg.className="msg";msg.textContent="";$("play").disabled=true;$("pname").disabled=true;
    $("label").textContent="Tirage en cours…";
    var reduce=window.matchMedia&&matchMedia("(prefers-reduced-motion: reduce)").matches;
    var timer=reduce?null:setInterval(function(){$("bignum").textContent=1+Math.floor(Math.random()*(st?st.teams:8))},70);
    var t0=Date.now();
    fetch("/api/jouer",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id:cid,name:name})})
      .then(function(r){return r.json().then(function(j){return{ok:r.ok,j:j}})})
      .catch(function(){return{ok:false,j:{erreur:"Serveur injoignable. Réessayez."}}})
      .then(function(res){
        var wait=reduce?0:Math.max(0,1300-(Date.now()-t0));
        setTimeout(function(){
          if(timer)clearInterval(timer);
          rolling=false;busy=false;
          if(!res.ok){msg.className="msg err";msg.textContent=res.j.erreur||"Le tirage a échoué.";}
          refresh();
        },wait);
      });
  });
  refresh();setInterval(function(){if(!rolling)refresh()},4000);
})();
</script></body></html>
"""

ORGA_HTML = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Peña Valou — Organisateur</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,700&family=IBM+Plex+Sans:wght@400;600&family=IBM+Plex+Mono:wght@400&display=swap">
<script src="https://cdnjs.cloudflare.com/ajax/libs/qrcode-generator/1.4.4/qrcode.min.js"></script>
<style>%%CSS%%
h1{font-size:clamp(1.7rem,4vw,2.4rem);line-height:1.1}
.panel h2{font-size:1.25rem}
.grid{display:grid;grid-template-columns:200px minmax(0,1fr);gap:20px;align-items:start}
@media(max-width:560px){.grid{grid-template-columns:minmax(0,1fr)}}
.qr{background:#fff;border:1px solid var(--line);border-radius:6px;padding:8px;width:200px;max-width:100%;aspect-ratio:1}
.qr svg{display:block;width:100%;height:100%}.qr p{color:#444;font-size:12px;margin:0}
.link{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.link code{font:12px var(--fm);overflow-wrap:anywhere;flex:1 1 180px;min-width:0;color:var(--muted)}
.places{font:13px var(--fm);margin:0 0 10px}
.bars{display:grid;gap:5px}
.bar{display:grid;grid-template-columns:64px minmax(0,1fr) 44px;gap:8px;align-items:center;font:12px var(--fm)}
.track{height:8px;background:var(--surface-2);border-radius:4px;overflow:hidden}.fill{height:100%;background:var(--accent)}
.n{text-align:right;color:var(--muted)}
.teams{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:12px}
.team{background:var(--surface);border:1px solid var(--line);border-radius:6px;min-width:0;overflow:hidden}
.team h3{padding:10px 12px;display:flex;justify-content:space-between;align-items:baseline;gap:8px;font-size:1rem;background:var(--surface-2);border-bottom:2px solid var(--accent)}
.team h3 span{font:12px var(--fm);color:var(--muted)}
.team ol{list-style:none;margin:0;padding:6px 0;counter-reset:n}
.team li{counter-increment:n;padding:3px 12px;display:flex;gap:10px;align-items:baseline;font-size:14px;overflow-wrap:anywhere}
.team li:before{content:counter(n);font:11px var(--fm);color:var(--muted);min-width:1.4em;text-align:right}
.empty{grid-column:1/-1;border:1px dashed var(--line);border-radius:6px;padding:20px;color:var(--muted);text-align:center}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px;max-width:420px}
.head{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap}
.meta{font:12px var(--fm);color:var(--muted)}
.confirm{display:flex;gap:8px;flex-wrap:wrap;align-items:center;font-size:13px}
</style></head><body>
<div class="wrap">
  <header><h1>Le Trésor de Valou</h1>
  <p class="hint">Les joueurs scannent le QR code, saisissent leur nom et appuient sur Jouer. Aucun compte n'est nécessaire.</p></header>

  <section class="panel" id="gate">
    <h2>Accès organisateur</h2>
    <div style="max-width:320px"><label for="pw">Mot de passe</label><input id="pw" type="password" autocomplete="current-password"></div>
    <div class="actions"><button class="primary" id="login" type="button">Entrer</button></div>
    <p class="msg" id="gmsg" role="status">Le mot de passe est affiché dans le terminal au démarrage du serveur.</p>
  </section>

  <div id="main" hidden class="wrap">
    <section class="panel">
      <h2>Tirage par QR code</h2>
      <div class="grid">
        <div class="qr" id="qr"></div>
        <div><p class="places" id="status">Chargement…</p><div class="bars" id="bars"></div></div>
      </div>
      <div class="link"><code id="url"></code><button type="button" id="copy">Copier le lien</button></div>
      <p class="msg" id="cmsg" role="status"></p>
    </section>

    <section class="panel">
      <h2>Réglages</h2>
      <div class="row">
        <div><label for="nt">Équipes</label><input id="nt" type="number" min="1" max="50" inputmode="numeric"></div>
        <div><label for="sz">Joueurs par équipe</label><input id="sz" type="number" min="1" max="200" inputmode="numeric"></div>
      </div>
      <div class="actions"><button type="button" id="save">Enregistrer</button></div>
      <p class="hint">Les réglages ne se changent que si aucun joueur n'a encore tiré. Remettez d'abord les places à zéro si besoin.</p>
      <div class="actions" id="rrow"><button type="button" id="reset">Remettre les places à zéro</button></div>
      <div class="confirm" id="rconf" hidden><span>Effacer tous les numéros et tous les noms ?</span>
        <button type="button" id="ryes">Oui, effacer</button><button type="button" id="rno">Annuler</button></div>
      <p class="msg" id="amsg" role="status"></p>
    </section>

    <section class="wrap" style="gap:14px">
      <div class="head"><h2>Joueurs inscrits</h2><span class="meta" id="meta"></span></div>
      <div class="teams" id="live"></div>
    </section>
  </div>
</div>
<script>
(function(){
  var $=function(i){return document.getElementById(i)};
  var pw="";try{pw=sessionStorage.getItem("adm")||""}catch(e){}
  var last="",lastUrl="",timer=null;
  function api(path,opts){
    opts=opts||{};opts.headers=Object.assign({"X-Admin":pw},opts.headers||{});opts.cache="no-store";
    return fetch(path,opts).then(function(r){return r.json().then(function(j){return{ok:r.ok,j:j}})});
  }
  function qr(url){
    if(url===lastUrl)return;lastUrl=url;$("url").textContent=url;
    try{
      var q=qrcode(0,"M");q.addData(url);q.make();
      var n=q.getModuleCount(),m=3,d="";
      for(var r=0;r<n;r++)for(var c=0;c<n;c++)if(q.isDark(r,c))d+="M"+(c+m)+" "+(r+m)+"h1v1h-1z";
      var s=n+m*2;
      $("qr").innerHTML='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 '+s+' '+s+'" shape-rendering="crispEdges" role="img" aria-label="QR code"><rect width="'+s+'" height="'+s+'" fill="#fff"/><path d="'+d+'" fill="#000"/></svg>';
    }catch(e){$("qr").innerHTML="<p>QR code indisponible (connexion internet requise pour le générer). Partagez le lien.</p>"}
  }
  function render(j){
    var key=JSON.stringify(j);if(key===last)return;last=key;
    qr(j.play_url);
    var total=j.teams*j.size;
    $("status").textContent=j.total+" / "+total+" places prises ("+j.teams+" équipes de "+j.size+")";
    $("meta").textContent=j.total+" / "+total+" inscrits";
    var bars=$("bars");bars.innerHTML="";
    j.counts.forEach(function(c,i){
      var row=document.createElement("div");row.className="bar";
      var a=document.createElement("span");a.textContent="Équipe "+(i+1);
      var t=document.createElement("span");t.className="track";
      var f=document.createElement("span");f.className="fill";f.style.display="block";f.style.width=Math.min(100,c/j.size*100)+"%";t.appendChild(f);
      var n=document.createElement("span");n.className="n";n.textContent=c+"/"+j.size;
      row.appendChild(a);row.appendChild(t);row.appendChild(n);bars.appendChild(row);
    });
    if(document.activeElement!==$("nt"))$("nt").value=j.teams;
    if(document.activeElement!==$("sz"))$("sz").value=j.size;
    var live=$("live");live.innerHTML="";
    if(!j.players.length){var e=document.createElement("p");e.className="empty";e.textContent="Aucun joueur pour l'instant. Les noms apparaissent ici dès que quelqu'un appuie sur Jouer.";live.appendChild(e);return}
    for(var t=1;t<=j.teams;t++){
      var list=j.players.filter(function(p){return p.team===t}).sort(function(a,b){return a.at-b.at});
      var art=document.createElement("article");art.className="team";
      var h=document.createElement("h3");h.textContent="Équipe "+t;
      var sp=document.createElement("span");sp.textContent=list.length+" / "+j.size;h.appendChild(sp);
      var ol=document.createElement("ol");
      list.forEach(function(p){var li=document.createElement("li");li.textContent=p.name||"Joueur sans nom";ol.appendChild(li)});
      art.appendChild(h);art.appendChild(ol);live.appendChild(art);
    }
  }
  function poll(){
    api("/api/etat").then(function(r){
      if(!r.j.admin){logout("Mot de passe incorrect.");return}
      render(r.j);
    }).catch(function(){$("status").textContent="Serveur injoignable."});
  }
  function logout(msg){
    pw="";try{sessionStorage.removeItem("adm")}catch(e){}
    if(timer){clearInterval(timer);timer=null}
    $("main").hidden=true;$("gate").hidden=false;$("gmsg").className="msg err";$("gmsg").textContent=msg;
  }
  function enter(){
    $("gate").hidden=true;$("main").hidden=false;last="";poll();
    if(!timer)timer=setInterval(poll,2000);
  }
  $("login").addEventListener("click",function(){
    pw=$("pw").value;
    api("/api/etat").then(function(r){
      if(r.j.admin){try{sessionStorage.setItem("adm",pw)}catch(e){}enter()}
      else{logout("Mot de passe incorrect.")}
    }).catch(function(){logout("Serveur injoignable.")});
  });
  $("pw").addEventListener("keydown",function(e){if(e.key==="Enter")$("login").click()});
  $("copy").addEventListener("click",function(){
    var u=$("url").textContent,m=$("cmsg");
    function fb(){var t=document.createElement("textarea");t.value=u;document.body.appendChild(t);t.select();var ok=false;try{ok=document.execCommand("copy")}catch(e){}document.body.removeChild(t);m.textContent=ok?"Lien copié.":"Copie impossible : sélectionnez le lien à la main."}
    if(navigator.clipboard&&navigator.clipboard.writeText)navigator.clipboard.writeText(u).then(function(){m.textContent="Lien copié."},fb);else fb();
  });
  $("save").addEventListener("click",function(){
    var m=$("amsg");m.className="msg";
    api("/api/reglages",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({teams:parseInt($("nt").value,10),size:parseInt($("sz").value,10)})})
      .then(function(r){m.className=r.ok?"msg":"msg err";m.textContent=r.ok?"Réglages enregistrés.":(r.j.erreur||"Échec.");last="";poll()});
  });
  $("reset").addEventListener("click",function(){$("rrow").hidden=true;$("rconf").hidden=false});
  $("rno").addEventListener("click",function(){$("rconf").hidden=true;$("rrow").hidden=false});
  $("ryes").addEventListener("click",function(){
    $("rconf").hidden=true;$("rrow").hidden=false;var m=$("amsg");
    api("/api/reset",{method:"POST"}).then(function(r){m.className=r.ok?"msg":"msg err";m.textContent=r.ok?"Toutes les places ont été remises à zéro.":(r.j.erreur||"Échec.");last="";poll()});
  });
  if(pw){api("/api/etat").then(function(r){if(r.j.admin)enter();else logout("")}).catch(function(){})}
})();
</script></body></html>
"""


# ---------------------------------------------------------------- serveur
class Handler(BaseHTTPRequestHandler):
    server_version = "TirageEquipes"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def _is_admin(self):
        given = self.headers.get("X-Admin", "")
        return bool(given) and secrets.compare_digest(given.encode(), CFG["password"].encode())

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if n <= 0 or n > 10000:
            return None
        try:
            data = json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path in ("/", "/jouer"):
            self._send(200, PLAYER_HTML.replace("%%CSS%%", CSS), "text/html; charset=utf-8")
        elif path in ("/organisateur", "/admin"):
            self._send(200, ORGA_HTML.replace("%%CSS%%", CSS), "text/html; charset=utf-8")
        elif path == "/api/etat":
            cid = ""
            for part in query.split("&"):
                if part.startswith("id="):
                    cid = part[3:]
            if not ID_RE.match(cid):
                cid = ""
            admin = self._is_admin()
            with LOCK:
                out = build_state(cid, admin)
            self._json(200, out)
        else:
            self._json(404, {"erreur": "Introuvable."})

    def do_POST(self):
        path = self.path.partition("?")[0]
        if path == "/api/jouer":
            body = self._body()
            if body is None:
                return self._json(400, {"erreur": "Requête invalide."})
            cid = str(body.get("id", ""))
            name = " ".join(str(body.get("name", "")).split())[:30]
            if not ID_RE.match(cid):
                return self._json(400, {"erreur": "Identifiant invalide."})
            if not name:
                return self._json(400, {"erreur": "Saisissez votre nom pour jouer."})
            code, out = assign_team(cid, name)
            return self._json(code, out)
        if path == "/api/reset":
            if not self._is_admin():
                return self._json(403, {"erreur": "Mot de passe incorrect."})
            with LOCK:
                STATE["players"] = []
                save_state()
            return self._json(200, {"ok": True})
        if path == "/api/reglages":
            if not self._is_admin():
                return self._json(403, {"erreur": "Mot de passe incorrect."})
            body = self._body()
            try:
                teams, size = int(body["teams"]), int(body["size"])
            except (TypeError, KeyError, ValueError):
                return self._json(400, {"erreur": "Valeurs invalides."})
            if not (1 <= teams <= 50 and 1 <= size <= 200):
                return self._json(400, {"erreur": "Équipes : 1 à 50. Joueurs par équipe : 1 à 200."})
            with LOCK:
                if STATE["players"]:
                    return self._json(409, {"erreur": "Des joueurs ont déjà tiré : remettez d'abord les places à zéro."})
                STATE["teams"], STATE["size"] = teams, size
                save_state()
            return self._json(200, {"ok": True})
        self._json(404, {"erreur": "Introuvable."})


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser(description="Tirage d'équipes avec QR code, sans compte.")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    ap.add_argument("--password", default=os.environ.get("TIRAGE_ADMIN_PASSWORD", ""), help="mot de passe organisateur (généré si absent)")
    ap.add_argument("--url", default=os.environ.get("RENDER_EXTERNAL_URL", ""), help="adresse publique des joueurs (tunnel), sans / final")
    ap.add_argument("--equipes", type=int, default=0, help="nombre d'équipes de départ")
    ap.add_argument("--taille", type=int, default=0, help="joueurs par équipe de départ")
    args = ap.parse_args()

    load_state()
    if args.equipes and not STATE["players"]:
        STATE["teams"] = max(1, min(50, args.equipes))
    if args.taille and not STATE["players"]:
        STATE["size"] = max(1, min(200, args.taille))
    CFG["password"] = args.password or secrets.token_urlsafe(6)
    base = args.url.rstrip("/") if args.url else "http://%s:%d" % (lan_ip(), args.port)
    CFG["play_url"] = base + "/"

    srv = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print("Serveur de tirage démarré.")
    print("  Joueurs (QR code)  :", CFG["play_url"])
    print("  Organisateur       :", base + "/organisateur")
    print("  Mot de passe       :", CFG["password"])
    print("  Équipes            : %d x %d" % (STATE["teams"], STATE["size"]))
    print("Ctrl+C pour arrêter.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt.")


if __name__ == "__main__":
    main()