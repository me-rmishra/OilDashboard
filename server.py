"""Local oil-market dashboard. Run: python server.py  ->  http://localhost:8765
Data: Yahoo Finance futures (free, delayed) + public RSS feeds. Standard library only."""
import json, os, time, threading, urllib.request, xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8765
TTL = 300  # seconds cache
UA = {"User-Agent": "Mozilla/5.0"}
SYMS = {"BZ=F": "Brent", "CL=F": "WTI", "RB=F": "RBOB Gasoline", "HO=F": "ULSD (Heating Oil)", "NG=F": "Henry Hub Gas"}
FEEDS = {
    "OilPrice.com": "https://oilprice.com/rss/main",
    "Rigzone": "https://www.rigzone.com/news/rss/rigzone_latest.aspx",
    "EIA Today in Energy": "https://www.eia.gov/rss/todayinenergy.xml",
}
_cache, _lock = {}, threading.Lock()

def cached(key, fn, ok):
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < TTL:
            return hit[1]
    val = fn()
    if ok(val):
        with _lock:
            _cache[key] = (time.time(), val)
        return val
    if hit:  # refresh failed: serve last good data, flagged stale
        return dict(hit[1], stale=True, errors=val.get("errors", {}))
    return val

def get(url, tries=3, timeout=15, headers=UA):
    err = None
    for i in range(tries):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout).read()
        except Exception as e:
            err = e; time.sleep(1 + i)
    raise err

def fred(sid):
    rows = [l.split(",") for l in get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}", 3, 30, {"User-Agent": "OilDashboard/1.0 (+https://github.com/me-rmishra/OilDashboard)"}).decode().splitlines()[1:]]
    return {r[0][:7]: float(r[1]) for r in rows if len(r) == 2 and r[1] not in ("", ".")}

def fetch_sym(sym):
    try: raw = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1y&interval=1d", 2)
    except Exception: raw = get(f"https://query2.finance.yahoo.com/v8/finance/chart/{sym}?range=1y&interval=1d", 2)
    j = json.loads(raw)
    r = j["chart"]["result"][0]
    pts = [(t, c) for t, c in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]) if c is not None]
    return pts

def build_prices():
    series, errors = {}, {}
    def one(s):
        try: series[s] = dict(fetch_sym(s))
        except Exception as e: errors[SYMS[s]] = str(e)
    ts = [threading.Thread(target=one, args=(s,)) for s in SYMS]
    [t.start() for t in ts]; [t.join() for t in ts]
    out = {"series": {}, "errors": errors, "updated": time.strftime("%Y-%m-%d %H:%M:%S")}
    for s, d in series.items():
        out["series"][SYMS[s]] = sorted(d.items())
    # derived spreads on dates where all inputs exist (USD/bbl; RBOB & HO are $/gal -> x42)
    def derive(name, fn, need):
        if not all(n in series for n in need): return
        dates = sorted(set.intersection(*[set(series[n]) for n in need]))
        out["series"][name] = [(t, fn(*[series[n][t] for n in need])) for t in dates]
    derive("Brent-WTI", lambda b, w: b - w, ["BZ=F", "CL=F"])
    derive("Gasoline crack (vs Brent)", lambda b, g: g * 42 - b, ["BZ=F", "RB=F"])
    derive("Diesel crack (vs Brent)", lambda b, h: h * 42 - b, ["BZ=F", "HO=F"])
    derive("3-2-1 crack (vs WTI)", lambda w, g, h: (2 * g * 42 + h * 42 - 3 * w) / 3, ["CL=F", "RB=F", "HO=F"])
    derive("3-2-1 crack (vs Brent)", lambda b, g, h: (2 * g * 42 + h * 42 - 3 * b) / 3, ["BZ=F", "RB=F", "HO=F"])
    # simple proxy: 50% gasoline / 40% diesel (distillate) / 10% residue at ~60% of crude, vs Brent
    derive("Simple GRM proxy (vs Brent)", lambda b, g, h: 0.5 * g * 42 + 0.4 * h * 42 + 0.1 * 0.6 * b - b, ["BZ=F", "RB=F", "HO=F"])
    # WTI-referenced cracks (daily)
    derive("Gasoline crack (vs WTI)", lambda w, g: g * 42 - w, ["CL=F", "RB=F"])
    derive("Diesel crack (vs WTI)", lambda w, h: h * 42 - w, ["CL=F", "HO=F"])
    derive("Simple GRM proxy (vs WTI)", lambda w, g, h: 0.5 * g * 42 + 0.4 * h * 42 + 0.1 * 0.6 * w - w, ["CL=F", "RB=F", "HO=F"])
    # Dubai: free source is monthly IMF averages via FRED (no free daily Dubai exists)
    out["monthly"] = {}
    try:
        dub, bre = fred("POILDUBUSDM"), fred("POILBREUSDM")
        months = sorted(dub)[-24:]
        out["monthly"]["Dubai (monthly avg)"] = [[m + "-01", dub[m]] for m in months]
        out["monthly"]["Brent-Dubai (monthly)"] = [[m + "-01", bre[m] - dub[m]] for m in months if m in bre]
        def mavg(sym):
            acc = {}
            for t, c in series.get(sym, {}).items():
                a = acc.setdefault(time.strftime("%Y-%m", time.gmtime(t)), [0.0, 0]); a[0] += c; a[1] += 1
            return {m: v[0] / v[1] for m, v in acc.items() if v[1] >= 15}  # skip partial months
        rb, ho = mavg("RB=F"), mavg("HO=F")
        g_, d_, c_, r_ = [], [], [], []
        for m in months:
            if m in rb and m in ho:
                dd, g, h = dub[m], rb[m] * 42, ho[m] * 42; d = m + "-01"
                g_.append([d, g - dd]); d_.append([d, h - dd]); c_.append([d, (2 * g + h - 3 * dd) / 3])
                r_.append([d, 0.5 * g + 0.4 * h + 0.1 * 0.6 * dd - dd])
        out["monthly"].update({"Gasoline crack (vs Dubai)": g_, "Diesel crack (vs Dubai)": d_,
                               "3-2-1 crack (vs Dubai)": c_, "Simple GRM proxy (vs Dubai)": r_})
    except Exception as e:
        errors["Dubai (FRED monthly)"] = str(e)
    return out


EIA_KEY = os.environ.get("EIA_API_KEY", "DEMO_KEY")  # DEMO_KEY is rate-limited; free key: eia.gov/opendata
EIA = [  # name, series id, unit, divisor
    ("US commercial crude stocks (ex-SPR)", "WCESTUS1", "mb", 1000),
    ("Cushing crude stocks", "W_EPC0_SAX_YCUOK_MBBL", "mb", 1000),
    ("US gasoline stocks", "WGTSTUS1", "mb", 1000),
    ("US distillate stocks", "WDISTUS1", "mb", 1000),
    ("Refinery utilisation", "WPULEUS3", "%", 1),
    ("Refinery crude runs", "WCRRIUS2", "mb/d", 1000),
]
def build_eia():
    from datetime import date, timedelta
    out, errors = {}, {}
    def one(item):
        name, sid, unit, div = item
        try:
            j = json.loads(get(f"https://api.eia.gov/v2/seriesid/PET.{sid}.W?api_key={EIA_KEY}&length=330&sort[0][column]=period&sort[0][direction]=desc"))
            rows = [(r["period"], float(r["value"]) / div) for r in j["response"]["data"] if r.get("value") not in (None, "")]
            rows.sort()
            dts = {date.fromisoformat(p): v for p, v in rows}
            avg5 = []
            for p, v in rows[-52:]:
                d0, vals = date.fromisoformat(p), []
                for y in range(1, 6):
                    tgt = d0 - timedelta(days=364 * y)
                    near = [d for d in dts if abs((d - tgt).days) <= 3]
                    if near: vals.append(dts[near[0]])
                if vals: avg5.append((p, sum(vals) / len(vals)))
            out[name] = {"unit": unit, "data": rows[-52:], "avg5": avg5}
        except Exception as e:
            errors[name] = str(e)
    ts = [threading.Thread(target=one, args=(i,)) for i in EIA]
    [t.start() for t in ts]; [t.join() for t in ts]
    return {"series": out, "errors": errors, "updated": time.strftime("%Y-%m-%d %H:%M:%S")}

def build_news():
    items = []
    for src, url in FEEDS.items():
        try:
            root = ET.fromstring(get(url))
            for it in root.iter("item"):
                items.append({"source": src, "title": (it.findtext("title") or "").strip(),
                              "link": (it.findtext("link") or "").strip(),
                              "date": (it.findtext("pubDate") or "").strip()})
        except Exception as e:
            items.append({"source": src, "title": f"[feed unavailable: {e}]", "link": "", "date": ""})
    from email.utils import parsedate_to_datetime
    def ts(i):
        try: return parsedate_to_datetime(i["date"]).timestamp()
        except Exception: return 0
    items.sort(key=ts, reverse=True)
    return items[:60]

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, code, body, ctype):
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        p = self.path.split("?")[0]
        try:
            if p in ("/api/prices", "/data/prices.json"): self.send(200, json.dumps(cached("p", build_prices, lambda v: bool(v["series"]))).encode(), "application/json")
            elif p in ("/api/eia", "/data/eia.json"): self.send(200, json.dumps(cached("e", build_eia, lambda v: bool(v["series"]))).encode(), "application/json")
            elif p in ("/api/news", "/data/news.json"): self.send(200, json.dumps(cached("n", build_news, lambda v: any(not i["title"].startswith("[feed") for i in v))).encode(), "application/json")
            elif p in ("/", "/index.html"): self.send(200, (Path(__file__).parent / "docs" / "index.html").read_bytes(), "text/html; charset=utf-8")
            else: self.send(404, b"not found", "text/plain")
        except Exception as e:
            self.send(500, json.dumps({"error": str(e)}).encode(), "application/json")

if __name__ == "__main__":
    print(f"Dashboard running at http://localhost:{PORT}  (Ctrl+C to stop)")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
