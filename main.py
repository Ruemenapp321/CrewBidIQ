
from __future__ import annotations
import csv, io, json, re
from pathlib import Path
from typing import Any
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pypdf import PdfReader

app = FastAPI(title="PairingIQ", version="0.1.1")

INDEX_HTML = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PairingIQ</title>
<style>
:root{--blue:#164e7a;--blue2:#2275aa;--bg:#f3f6fa;--card:#fff;--ink:#17324d;--muted:#64748b;--line:#dbe3ec}
*{box-sizing:border-box}body{margin:0;font-family:Arial,sans-serif;background:var(--bg);color:var(--ink)}
header{padding:24px 18px;color:#fff;background:linear-gradient(135deg,var(--blue),var(--blue2))}
header h1{margin:0;font-size:30px}header p{margin:6px 0 0;opacity:.9}
main{max-width:1100px;margin:18px auto;padding:0 14px 36px}
.card{background:#fff;border:1px solid var(--line);border-radius:14px;padding:17px;margin-bottom:17px;box-shadow:0 3px 12px rgba(23,50,77,.06)}
h2{margin:0 0 12px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:11px}
label{display:block;font-size:13px;color:var(--muted)}input{width:100%;padding:10px;margin-top:5px;border:1px solid var(--line);border-radius:9px;font:inherit}
button,.button{display:inline-block;padding:11px 16px;border:0;border-radius:9px;background:var(--blue);color:#fff;font-weight:700;text-decoration:none;cursor:pointer}
.secondary{background:#607d8b}.actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}.status{margin-top:11px;color:var(--muted);font-size:13px}
.table-wrap{overflow:auto;max-height:650px;border:1px solid var(--line);border-radius:10px}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{border-bottom:1px solid var(--line);padding:8px;text-align:left;vertical-align:top}
th{position:sticky;top:0;background:#edf3f8}.score{font-weight:800;color:var(--blue)}
</style>
</head>
<body>
<header><h1>PairingIQ</h1><p>Upload. Rank. Bid smarter.</p></header>
<main>
<section class="card">
<h2>Analyze a bid package</h2>
<div class="grid">
<label>Bid package<input id="file" type="file" accept=".pdf,.html,.htm,.txt,.csv"></label>
<label>Airline / fleet / base<input id="context" placeholder="Example: American A320, CLT"></label>
</div>
<div class="actions"><button id="analyzeBtn">Analyze</button></div>
<div id="status" class="status">Ready.</div>
</section>

<section class="card">
<h2>Preference profile</h2>
<div class="grid">
<label>Base airport<input id="baseAirport" value="DTW"></label>
<label>Elite cities<input id="eliteCities" value="BOS,LAX,SFO,SAN,SEA,PDX"></label>
<label>Secondary cities<input id="secondaryCities" value="DCA,BWI,MIA,FLL,PBI,MCO,TPA,RSW"></label>
<label>Interesting cities<input id="smallCities" value="TVC,BNA,SAV,CHS,BTV"></label>
<label>Penalty cities<input id="penaltyCities" value="AUS,SAT,DFW,IAH,HOU,SDQ,STI"></label>
<label>Preferred aircraft<input id="preferredAircraft" value="3NE,3N1,3NP"></label>
</div>
</section>

<section class="card">
<div style="display:flex;justify-content:space-between;gap:12px;align-items:center">
<div><h2>Ranked pairings</h2><p id="summary" class="status">No analysis yet.</p></div>
<a id="csvLink" class="button secondary" style="display:none">Export CSV</a>
</div>
<div class="table-wrap"><table>
<thead><tr><th>Rank</th><th>Pairing</th><th>Score</th><th>Cities</th><th>Aircraft</th><th>Redeye</th><th>DH</th><th>Transfers</th><th>Why</th></tr></thead>
<tbody id="results"></tbody>
</table></div>
</section>
</main>
<script>
const $=id=>document.getElementById(id);
const list=id=>$(id).value.split(",").map(x=>x.trim().toUpperCase()).filter(Boolean);
function profile(){return{
base_airport:$("baseAirport").value.trim().toUpperCase(),
elite_cities:list("eliteCities"),secondary_cities:list("secondaryCities"),
small_cities:list("smallCities"),penalty_cities:list("penaltyCities"),
preferred_aircraft:list("preferredAircraft")};}
$("analyzeBtn").onclick=async()=>{
 const file=$("file").files[0]; if(!file){alert("Choose a file first.");return;}
 $("status").textContent="Uploading and analyzing...";
 const fd=new FormData(); fd.append("file",file); fd.append("context",$("context").value); fd.append("profile_json",JSON.stringify(profile()));
 try{
  const r=await fetch("/api/analyze",{method:"POST",body:fd}); const b=await r.json();
  if(!r.ok) throw new Error(b.detail||"Analysis failed");
  const tb=$("results"); tb.innerHTML="";
  b.results.forEach((x,i)=>{const tr=document.createElement("tr");tr.innerHTML=`<td>${i+1}</td><td><b>${x.pairing}</b></td><td class="score">${x.score}</td><td>${x.cities.join(", ")}</td><td>${x.preferred_aircraft.join(", ")||"—"}</td><td>${x.redeye}</td><td>${x.deadheads}</td><td>${x.transfers.join(", ")||"—"}</td><td>${x.reasons.join("; ")}</td>`;tb.appendChild(tr);});
  $("summary").textContent=`${b.pairings_detected} pairing blocks detected from ${b.filename}.`;
  $("csvLink").href=`/api/csv?payload=${encodeURIComponent(JSON.stringify(b.results))}`;$("csvLink").style.display="inline-block";
  $("status").textContent="Analysis complete.";
 }catch(e){$("status").textContent=e.message;}
};
</script>
</body></html>
"""

@app.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    return HTMLResponse(INDEX_HTML)

def extract_text(upload: UploadFile, content: bytes) -> str:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix == ".pdf":
        reader = PdfReader(io.BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if suffix in {".html", ".htm"}:
        raw = content.decode("utf-8", errors="ignore")
        raw = re.sub(r"<script\b[^>]*>.*?</script>", " ", raw, flags=re.I|re.S)
        raw = re.sub(r"<style\b[^>]*>.*?</style>", " ", raw, flags=re.I|re.S)
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw))
    if suffix in {".txt", ".csv"}:
        return content.decode("utf-8", errors="ignore")
    raise HTTPException(400, "Supported formats: PDF, HTML, HTM, TXT, CSV")

def parse_pairings(text: str) -> list[dict[str, Any]]:
    normalized = text.replace("\r","\n")
    pattern = re.compile(r"(?mi)^\s*(?:#|PAIRING\s+|TRIP\s+)([A-Z]?\d{3,5})\b")
    matches = list(pattern.finditer(normalized))
    if not matches:
        ids=[]
        for token in re.findall(r"\b[A-Z]?\d{4}\b", normalized):
            if token not in ids: ids.append(token)
        return [{"id":x,"block":normalized[:100000]} for x in ids[:1000]]
    out=[]
    for i,m in enumerate(matches):
        end=matches[i+1].start() if i+1<len(matches) else len(normalized)
        out.append({"id":m.group(1).upper(),"block":normalized[m.start():end]})
    return out

def list_field(v: Any) -> list[str]:
    return [str(x).strip().upper() for x in (v if isinstance(v,list) else str(v or "").split(",")) if str(x).strip()]

def detect_airports(block: str) -> list[str]:
    excluded={"TOTAL","CREDIT","CHECK","PAGE","PILOT","PAIR","TRIP","FDP","TAFB","MAX","DAY"}
    out=[]
    for c in re.findall(r"\b[A-Z]{3}\b", block.upper()):
        if c not in excluded and c not in out: out.append(c)
    return out[:40]

def score_pairing(p: dict[str,Any], profile: dict[str,Any]) -> dict[str,Any]:
    cities=detect_airports(p["block"]); elite=set(list_field(profile.get("elite_cities"))); secondary=set(list_field(profile.get("secondary_cities")))
    small=set(list_field(profile.get("small_cities"))); penalty=set(list_field(profile.get("penalty_cities"))); aircraft=list_field(profile.get("preferred_aircraft"))
    score=0; reasons=[]
    for c in cities:
        if c in elite: score+=28; reasons.append(f"{c}: elite")
        elif c in secondary: score+=12; reasons.append(f"{c}: secondary")
        elif c in small: score+=6; reasons.append(f"{c}: interesting")
        if c in penalty: score-=18; reasons.append(f"{c}: penalty")
    hits=[a for a in aircraft if a in p["block"].upper()]; score+=20*len(hits)
    if hits: reasons.append(f"{len(hits)} preferred-aircraft code(s)")
    dh=len(re.findall(r"\bDH\b",p["block"].upper()))
    if dh==0: score+=10; reasons.append("all-operated signal")
    elif dh>1: score-=18*(dh-1); reasons.append(f"{dh} deadheads")
    transfer_pairs=[("SFO","SJC"),("JFK","LGA"),("JFK","EWR"),("LGA","EWR"),("DCA","IAD"),("DCA","BWI")]
    transfers=[f"{a}→{b}" for a,b in transfer_pairs if a in cities and b in cities]
    score-=32*len(transfers)
    up=p["block"].upper(); redeye="flagged" if "REDEYE" in up else ("possible" if len(re.findall(r"\b(?:2[1-3]|0[0-6])\d{2}\b",up))>=2 else "none")
    if redeye=="flagged": score-=55; reasons.append("redeye flagged")
    elif redeye=="possible": score-=18; reasons.append("possible overnight-time pattern")
    return {"pairing":p["id"],"score":round(score,1),"cities":cities,"preferred_aircraft":hits,"redeye":redeye,"deadheads":dh,"transfers":transfers,"reasons":reasons}

@app.post("/api/analyze")
async def analyze(file: UploadFile=File(...), context: str=Form(""), profile_json: str=Form(...)):
    profile=json.loads(profile_json); content=await file.read()
    if len(content)>50*1024*1024: raise HTTPException(413,"File exceeds 50 MB")
    text=extract_text(file,content); pairings=parse_pairings(text); results=[score_pairing(p,profile) for p in pairings]
    results.sort(key=lambda x:x["score"],reverse=True)
    return JSONResponse({"filename":file.filename,"context":context,"pairings_detected":len(pairings),"results":results[:500]})

@app.get("/api/csv")
def csv_export(payload: str):
    results=json.loads(payload); out=io.StringIO(); w=csv.writer(out)
    w.writerow(["Rank","Pairing","Score","Cities","Preferred Aircraft","Redeye","Deadheads","Transfers","Reasons"])
    for i,x in enumerate(results,1): w.writerow([i,x["pairing"],x["score"]," ".join(x["cities"])," ".join(x["preferred_aircraft"]),x["redeye"],x["deadheads"]," ".join(x["transfers"]),"; ".join(x["reasons"])])
    out.seek(0)
    return StreamingResponse(iter([out.getvalue()]),media_type="text/csv",headers={"Content-Disposition":'attachment; filename="pairing_analysis.csv"'})
