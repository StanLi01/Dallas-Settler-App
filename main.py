"""
settler_web/main.py — The Settler App (FastAPI)

Analytic layers:
  1. Additive risk decomposition (Shapley-exact for additive models)
  2. Spatial enforcement-equity audit
  3. OLS trajectory forecasting with prediction intervals
  4. Temporal risk profile (hour of day / day of week)
  5. Case-outcome profile, indirectly standardised by offence mix
  6. Exposure profile (who and what actually gets targeted)

Run:  python -m uvicorn main:app --port 8000
"""
import os, math, re, statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter, Retry
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

load_dotenv()

USER_AGENT_EMAIL = os.getenv("USER_AGENT_EMAIL", "")
CENSUS_API_KEY   = os.getenv("CENSUS_API_KEY",   "")
DALLAS_CRIME_URL = "https://www.dallasopendata.com/resource/qv6i-rri7.json"

CACHE_TTL_HOURS  = 6
FETCH_PAGE_SIZE  = 50000
BUFFER_KM        = 1.0
YEARS_BACK       = 4
DALLAS_AREA_KM2  = 997.0

CRIME_SELECT_COLS = (
    "incidentnum,servyr,offincident,nibrs_crime_category,incident_address,"
    "division,zip_code,geocoded_column,time1,day1,status,victimtype,"
    "premise,weaponused"
)

session = requests.Session()
retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
adapter = HTTPAdapter(max_retries=retries)
session.mount("http://", adapter)
session.mount("https://", adapter)
HEADERS = {"User-Agent": f"SettlerApp/1.0 ({USER_AGENT_EMAIL})"}

_crime_cache: Dict    = {"data": None, "fetched_at": None}
_fetch_progress: Dict = {"status": "idle", "fetched": 0, "error": None}
_baseline_cache: Dict = {"data": None}
_zcta_cache: Dict     = {"data": None}
_equity_cache: Dict   = {"data": None}

app = FastAPI(title="The Settler App")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


class LookupRequest(BaseModel):
    address: str


class AnalyzeRequest(BaseModel):
    address: str
    lat: float
    lon: float


# ══════════════════════════════════════════════════════════════════════════
#  TAXONOMY
# ══════════════════════════════════════════════════════════════════════════

VIOLENT_KEYS = ("MURDER", "HOMICIDE", "MANSLAUGHTER", "ASSAULT", "ROBBERY",
                "RAPE", "SEXUAL", "SODOMY", "KIDNAP", "SHOOTING", "STABBING",
                "INJURED PERSON", "FAMILY VIOLENCE")
PROPERTY_KEYS = ("BURGLARY", "THEFT", "LARCENY", "SHOPLIFT", "STOLEN", "BMV",
                 "VANDALISM", "MISCHIEF", "ARSON", "FRAUD", "FORGERY", "EMBEZZ",
                 "CREDIT CARD", "IDENTITY", "UNAUTHORIZED USE OF MOTOR")
VICE_KEYS = ("DRUG", "NARCOTIC", "MARIJUANA", "PARAPHERNALIA", "CONTROLLED SUB",
             "PROSTITUT", "GAMBL", "LIQUOR", "ALCOHOL", "DWI", "INTOX")

DISCRETIONARY_KEYS = VICE_KEYS + (
    "TRESPASS", "LOITER", "WARRANT", "TRAFFIC", "TEMP TAG", "REGISTRATION",
    "DRIVING WHILE LICENSE", "NO DRIVER", "EVADING", "RESIST", "FAIL TO ID",
    "DISORDERLY", "CURFEW", "OBSTRUCT",
)

SEVERITY = {"violent": 10.0, "property": 3.0, "vice": 1.0, "other": 1.0}


def classify_offence(off: str) -> str:
    o = (off or "").upper()
    if any(k in o for k in VIOLENT_KEYS):
        return "violent"
    if any(k in o for k in PROPERTY_KEYS):
        return "property"
    if any(k in o for k in VICE_KEYS):
        return "vice"
    return "other"


def is_discretionary(off: str) -> bool:
    o = (off or "").upper()
    return any(k in o for k in DISCRETIONARY_KEYS)


def classify_premise(p: str) -> str:
    """Order matters: 'Apartment Parking Lot' is parking, not a residence."""
    s = (p or "").upper()
    if not s:
        return "other"
    if "PARKING" in s:
        return "parking"
    if "APARTMENT" in s:
        return "apartment"
    if "RESIDENCE" in s or "RESIDENTIAL" in s:
        return "home"
    if any(k in s for k in ("HIGHWAY", "STREET", "ALLEY", "OUTDOOR", "PARK ")):
        return "street"
    if any(k in s for k in ("RESTAURANT", "CONVENIENCE", "GAS ", "SERVICE STATION",
                            "RETAIL", "BUSINESS", "COMMERCIAL", "STORE", "OFFICE",
                            "BANK", "HOTEL", "MOTEL")):
        return "business"
    return "other"


def is_cleared(status: str) -> bool:
    s = (status or "").upper()
    return s.startswith("CLEAR") or "CLOSED" in s


def is_arrest(status: str) -> bool:
    return (status or "").upper().startswith("CLEAR BY ARREST")


def has_weapon(w: str) -> bool:
    s = (w or "").upper()
    if not s or s in ("OTHER", "UNKNOWN"):
        return False
    return "NONE" not in s


# ══════════════════════════════════════════════════════════════════════════
#  GEOCODING / WEATHER / CENSUS
# ══════════════════════════════════════════════════════════════════════════

def is_coord_string(s) -> Optional[Tuple[float, float]]:
    if not isinstance(s, str):
        return None
    m = re.search(r"(-?\d+\.\d+)[, ]+\s*(-?\d+\.\d+)", s.strip())
    if m:
        try:
            return float(m.group(1)), float(m.group(2))
        except ValueError:
            return None
    return None


def geocode_dallas(address: str) -> Tuple[float, float]:
    if not address.strip():
        raise ValueError("Address is empty.")
    resp = session.get(
        "https://nominatim.openstreetmap.org/search",
        headers=HEADERS, timeout=10,
        params={"q": address, "format": "json", "limit": 1, "addressdetails": 1,
                "viewbox": "-97.0,33.3,-96.4,32.6", "bounded": 1},
    )
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise ValueError("Could not find that address in Dallas.")
    return float(data[0]["lat"]), float(data[0]["lon"])


def get_weather(lat: float, lon: float) -> dict:
    resp = session.get(
        "https://api.open-meteo.com/v1/forecast", timeout=10,
        params={"latitude": lat, "longitude": lon, "current_weather": True,
                "hourly": "precipitation_probability", "forecast_days": 1},
    )
    resp.raise_for_status()
    data = resp.json()
    cw = data.get("current_weather", {})
    precip = 0
    try:
        precip = data["hourly"]["precipitation_probability"][datetime.utcnow().hour]
    except Exception:
        pass
    wmo = {0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
           45: "Fog", 61: "Light rain", 63: "Rain", 65: "Heavy rain",
           80: "Showers", 95: "Thunderstorm"}
    temp_c = cw.get("temperature", 0)
    return {"temp_f": round(temp_c * 9 / 5 + 32, 1), "temp_c": round(temp_c, 1),
            "wind_mph": round(cw.get("windspeed", 0) * 0.621371, 1),
            "condition": wmo.get(cw.get("weathercode", 0), "Unknown"),
            "precip_pct": precip}


def _get_census_tract(lat: float, lon: float):
    resp = session.get("https://geo.fcc.gov/api/census/block/find", timeout=10,
                       params={"latitude": lat, "longitude": lon, "format": "json"})
    resp.raise_for_status()
    fips = resp.json()["Block"]["FIPS"]
    return fips[0:2], fips[2:5], fips[5:11]


def real_home_values(lat: float, lon: float) -> List[Dict]:
    try:
        state, county, tract = _get_census_tract(lat, lon)
    except Exception:
        return []
    SENTINELS = {-666666666, -888888888, -999999999}
    results = []
    for y in [2019, 2020, 2021, 2022, 2023]:
        params = {"get": "B25077_001E", "for": f"tract:{tract}",
                  "in": f"state:{state} county:{county}", "key": CENSUS_API_KEY}
        try:
            resp = session.get(f"https://api.census.gov/data/{y}/acs/acs5",
                               params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            raw = data[1][0] if data and len(data) >= 2 else None
            val = None if raw in (None, "", "null") else int(raw)
            ok = val is not None and val not in SENTINELS and val > 0
            results.append({"year": y, "value": val if ok else None})
        except Exception:
            results.append({"year": y, "value": None})
    return results


def zcta_demographics() -> Dict[str, Dict]:
    if _zcta_cache["data"] is not None:
        return _zcta_cache["data"]
    out: Dict[str, Dict] = {}
    try:
        resp = session.get("https://api.census.gov/data/2022/acs/acs5",
                           params={"get": "B19013_001E,B01003_001E",
                                   "for": "zip code tabulation area:*",
                                   "key": CENSUS_API_KEY}, timeout=60)
        resp.raise_for_status()
        rows = resp.json()
        SENTINELS = {-666666666, -888888888, -999999999}
        for r in rows[1:]:
            try:
                inc = int(r[0]); inc = None if inc in SENTINELS or inc <= 0 else inc
            except (TypeError, ValueError):
                inc = None
            try:
                pop = int(r[1]); pop = None if pop < 0 else pop
            except (TypeError, ValueError):
                pop = None
            out[r[2]] = {"income": inc, "population": pop}
        print(f"[Settler] ZCTA demographics loaded: {len(out)} areas")
    except Exception as e:
        print(f"[Settler] ZCTA demographics unavailable: {e}")
    _zcta_cache["data"] = out
    return out


# ══════════════════════════════════════════════════════════════════════════
#  GEO + NORMALISATION
# ══════════════════════════════════════════════════════════════════════════

def haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def normalize_address_for_match(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"\(.*?\)", " ", s.upper())
    s = re.sub(r"[^A-Z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _parse_hour(t: str) -> Optional[int]:
    if not t:
        return None
    m = re.match(r"^\s*(\d{1,2}):(\d{2})", str(t))
    if not m:
        return None
    h = int(m.group(1))
    return h if 0 <= h <= 23 else None


DAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def normalize_soda_record(rec: Dict) -> Dict:
    addr = (rec.get("incident_address") or "").strip()
    lat = lon = None
    gc = rec.get("geocoded_column")
    if isinstance(gc, dict):
        try:
            lat = float(gc.get("latitude")); lon = float(gc.get("longitude"))
        except (TypeError, ValueError):
            lat = lon = None
    try:
        year = int(rec.get("servyr"))
    except (TypeError, ValueError):
        year = None

    incident_type = rec.get("offincident") or "Unknown"
    status = rec.get("status") or ""
    day = (rec.get("day1") or "").strip()[:3].title()

    return {
        "address": addr, "lat": lat, "lon": lon, "year": year,
        "incident_type": incident_type,
        "category": rec.get("nibrs_crime_category") or incident_type,
        "division": rec.get("division") or "",
        "zip_code": (rec.get("zip_code") or "").strip(),
        "cls": classify_offence(incident_type),
        "disc": is_discretionary(incident_type),
        "hour": _parse_hour(rec.get("time1")),
        "day": day if day in DAY_ORDER else None,
        "status": status,
        "cleared": is_cleared(status),
        "arrest": is_arrest(status),
        "victim": (rec.get("victimtype") or "").strip(),
        "prem": classify_premise(rec.get("premise")),
        "premise_raw": (rec.get("premise") or "").strip(),
        "weapon": has_weapon(rec.get("weaponused")),
    }


# ══════════════════════════════════════════════════════════════════════════
#  FETCH
# ══════════════════════════════════════════════════════════════════════════

def _fetch_all_pages(min_year: int) -> List[Dict]:
    global _fetch_progress
    _fetch_progress = {"status": "fetching", "fetched": 0, "error": None}
    all_records: List[Dict] = []
    offset = 0
    while True:
        params = {"$select": CRIME_SELECT_COLS, "$limit": FETCH_PAGE_SIZE,
                  "$offset": offset, "$where": f"servyr >= {min_year}"}
        try:
            resp = session.get(DALLAS_CRIME_URL, params=params, headers=HEADERS, timeout=90)
            resp.raise_for_status()
            page = resp.json()
        except Exception as e:
            print(f"[Settler] Fetch error at offset {offset}: {e}")
            _fetch_progress["error"] = str(e)
            break
        if not page:
            break
        all_records.extend(page)
        _fetch_progress["fetched"] = len(all_records)
        print(f"[Settler] Fetched {len(page)} rows (total: {len(all_records)})")
        if len(page) < FETCH_PAGE_SIZE:
            break
        offset += FETCH_PAGE_SIZE
    _fetch_progress["status"] = "done"
    return all_records


def _fetch_and_cache_crimes() -> List[Dict]:
    now = datetime.utcnow()
    c = _crime_cache
    if (c["data"] is not None and c["fetched_at"]
            and (now - c["fetched_at"]) < timedelta(hours=CACHE_TTL_HOURS)):
        return c["data"]
    raw = _fetch_all_pages(now.year - YEARS_BACK)
    print(f"[Settler] Total records fetched: {len(raw)}")
    norm = [normalize_soda_record(r) for r in raw]
    norm = [r for r in norm if r["lat"] is not None and r["lon"] is not None]
    print(f"[Settler] Records with coordinates: {len(norm)}")
    c["data"] = norm
    c["fetched_at"] = now
    _baseline_cache["data"] = None
    _equity_cache["data"] = None
    return norm


def build_geojson(crimes: List[Dict]) -> Dict:
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
         "properties": {"a": r["address"], "t": r["incident_type"],
                        "y": str(r["year"] or ""), "c": r["cls"]}}
        for r in crimes]}


# ══════════════════════════════════════════════════════════════════════════
#  CITYWIDE BASELINE
# ══════════════════════════════════════════════════════════════════════════

RISK_WEIGHTS = {"violent": 3.0, "property": 1.5, "vice": 0.8, "other": 0.5,
                "recency": 1.0, "concentration": 1.0}
RISK_LABELS = {"violent": "Violent crime density", "property": "Property crime density",
               "vice": "Drug &amp; vice density", "other": "Other / disorder density",
               "recency": "Recent-year concentration",
               "concentration": "Clustering near the address"}
RISK_NOTES = {"violent": "Assault, robbery, homicide, sexual offences",
              "property": "Burglary, theft, vehicle crime, vandalism, fraud",
              "vice": "Narcotics, paraphernalia, alcohol offences",
              "other": "Trespass, warrants, traffic, public-order",
              "recency": "Is activity trending into the latest full year?",
              "concentration": "Are incidents at the doorstep or out at the ring edge?"}


def citywide_baseline() -> Dict:
    if _baseline_cache["data"] is not None:
        return _baseline_cache["data"]
    crimes = _crime_cache["data"] or []
    counts = Counter(r["cls"] for r in crimes)
    years = [r["year"] for r in crimes if r["year"]]
    latest_full = (max(years) - 1) if years else None
    recent_share = 0.0
    if latest_full and crimes:
        recent_share = sum(1 for r in crimes if r["year"] == latest_full) / len(crimes)

    hours = Counter(r["hour"] for r in crimes if r["hour"] is not None)
    total_h = sum(hours.values()) or 1
    days = Counter(r["day"] for r in crimes if r["day"])
    total_d = sum(days.values()) or 1

    with_status = [r for r in crimes if r["status"]]
    city_cleared = (sum(1 for r in with_status if r["cleared"]) / len(with_status)) if with_status else 0.0

    # Clearance varies enormously by offence type — officer-initiated offences
    # clear at several times the rate of victim-reported ones. Storing the
    # per-class rates lets outcome_profile() standardise for offence mix
    # instead of comparing an area's raw rate against a citywide average that
    # reflects a completely different basket of crimes.
    cleared_by_class = {}
    for cls in ("violent", "property", "vice", "other"):
        sub = [r for r in with_status if r["cls"] == cls]
        cleared_by_class[cls] = (sum(1 for r in sub if r["cleared"]) / len(sub)) if sub else 0.0

    prem = Counter(r["prem"] for r in crimes)
    total_p = sum(prem.values()) or 1

    out = {
        "density": {k: counts.get(k, 0) / DALLAS_AREA_KM2
                    for k in ("violent", "property", "vice", "other")},
        "recent_share": recent_share, "latest_full_year": latest_full,
        "hour_share": {h: hours.get(h, 0) / total_h for h in range(24)},
        "day_share": {d: days.get(d, 0) / total_d for d in DAY_ORDER},
        "cleared_share": city_cleared,
        "cleared_by_class": cleared_by_class,
        "premise_share": {k: prem.get(k, 0) / total_p for k in prem},
        "total": len(crimes),
    }
    _baseline_cache["data"] = out
    return out


# ══════════════════════════════════════════════════════════════════════════
#  1. RISK DECOMPOSITION
# ══════════════════════════════════════════════════════════════════════════

def risk_decomposition(hits: List[Dict], radius_km: float) -> Dict:
    base = citywide_baseline()
    area = math.pi * radius_km ** 2
    n = len(hits)
    contribs = []
    obs_counts = Counter(h["cls"] for h in hits)

    for cls in ("violent", "property", "vice", "other"):
        obs = obs_counts.get(cls, 0)
        exp = base["density"].get(cls, 0.0) * area
        contribs.append({"key": cls, "label": RISK_LABELS[cls], "note": RISK_NOTES[cls],
                         "observed": obs, "expected": round(exp, 1),
                         "contribution": round(RISK_WEIGHTS[cls] * math.log((obs + 1) / (exp + 1)), 3)})

    lfy = base["latest_full_year"]
    if lfy and n:
        obs_r = sum(1 for h in hits if h["year"] == lfy)
        exp_r = base["recent_share"] * n
        contribs.append({"key": "recency", "label": RISK_LABELS["recency"],
                         "note": RISK_NOTES["recency"] + f" (baseline year {lfy})",
                         "observed": obs_r, "expected": round(exp_r, 1),
                         "contribution": round(RISK_WEIGHTS["recency"] * math.log((obs_r + 1) / (exp_r + 1)), 3)})
    if n:
        inner = radius_km / 3.0
        obs_i = sum(1 for h in hits if h["distance_km"] <= inner)
        exp_i = n / 9.0
        contribs.append({"key": "concentration", "label": RISK_LABELS["concentration"],
                         "note": RISK_NOTES["concentration"],
                         "observed": obs_i, "expected": round(exp_i, 1),
                         "contribution": round(RISK_WEIGHTS["concentration"] * math.log((obs_i + 1) / (exp_i + 1)), 3)})

    total = round(sum(c["contribution"] for c in contribs), 3)
    if   total < -1.5: band, blurb = "Well below typical", "Materially quieter than an average Dallas location."
    elif total < -0.5: band, blurb = "Below typical", "Somewhat quieter than an average Dallas location."
    elif total <  0.5: band, blurb = "Typical", "Statistically indistinguishable from citywide average."
    elif total <  1.5: band, blurb = "Above typical", "Elevated relative to an average Dallas location."
    else:              band, blurb = "Well above typical", "Substantially elevated relative to citywide average."

    contribs.sort(key=lambda c: -abs(c["contribution"]))
    return {"total": total, "band": band, "blurb": blurb, "contributions": contribs,
            "method": ("Additive log-density model. Each bar is that feature's exact Shapley "
                       "contribution, which for an additive model equals w-f (x-f minus E[x-f]). "
                       "Zero means citywide-typical.")}


# ══════════════════════════════════════════════════════════════════════════
#  2. EQUITY AUDIT
# ══════════════════════════════════════════════════════════════════════════

def _pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return num / (dx * dy) if dx and dy else None


def equity_profile() -> Dict:
    if _equity_cache["data"] is not None:
        return _equity_cache["data"]
    crimes = _crime_cache["data"] or []
    demo = zcta_demographics()

    agg = defaultdict(lambda: {"n": 0, "disc": 0, "sev": 0.0, "clr": 0, "st": 0})
    for r in crimes:
        z = r["zip_code"]
        if not z or len(z) != 5:
            continue
        a = agg[z]
        a["n"] += 1
        a["sev"] += SEVERITY[r["cls"]]
        if r["disc"]:
            a["disc"] += 1
        if r["status"]:
            a["st"] += 1
            if r["cleared"]:
                a["clr"] += 1

    zips: Dict[str, Dict] = {}
    for z, a in agg.items():
        if a["n"] < 200:
            continue
        d = demo.get(z, {})
        pop = d.get("population")
        zips[z] = {"zip": z, "incidents": a["n"], "disc_share": a["disc"] / a["n"],
                   "severity_per_incident": a["sev"] / a["n"],
                   "cleared_share": (a["clr"] / a["st"]) if a["st"] else None,
                   "income": d.get("income"), "population": pop,
                   "per_1k": (a["n"] / pop * 1000) if pop else None}

    shares = [v["disc_share"] for v in zips.values()]
    city_disc = statistics.median(shares) if shares else 0.0

    paired = [(v["disc_share"], v["income"]) for v in zips.values() if v["income"]]
    corr = _pearson([p[0] for p in paired], [p[1] for p in paired]) if len(paired) >= 3 else None

    clr_paired = [(v["cleared_share"], v["income"]) for v in zips.values()
                  if v["income"] and v["cleared_share"] is not None]
    clr_corr = (_pearson([p[0] for p in clr_paired], [p[1] for p in clr_paired])
                if len(clr_paired) >= 3 else None)

    out = {"zips": zips, "city_disc_share": city_disc,
           "income_corr": round(corr, 3) if corr is not None else None,
           "clearance_income_corr": round(clr_corr, 3) if clr_corr is not None else None,
           "n_zips": len(zips), "n_paired": len(paired)}
    _equity_cache["data"] = out
    return out


def equity_audit(hits: List[Dict]) -> Optional[Dict]:
    if not hits:
        return None
    prof = equity_profile()
    n = len(hits)
    local_disc = sum(1 for h in hits if h["disc"]) / n
    local_sev = sum(SEVERITY[h["cls"]] for h in hits) / n
    city_disc = prof["city_disc_share"]

    zc = Counter(h["zip_code"] for h in hits if h["zip_code"])
    zip_code = zc.most_common(1)[0][0] if zc else None
    zrec = prof["zips"].get(zip_code) if zip_code else None

    pct = None
    if zrec:
        shares = sorted(v["disc_share"] for v in prof["zips"].values())
        pct = round(sum(1 for s in shares if s < zrec["disc_share"]) / len(shares) * 100) if shares else None

    ratio = (local_disc / city_disc) if city_disc > 0 else 1.0
    if   ratio >= 1.35: flag, head = "high", "Count likely inflated by enforcement intensity"
    elif ratio >= 1.15: flag, head = "moderate", "Somewhat enforcement-weighted"
    elif ratio <= 0.75: flag, head = "low", "Count is predominantly victim-reported"
    else:               flag, head = "typical", "Enforcement mix close to citywide norm"

    return {"flag": flag, "headline": head,
            "local_disc_share": round(local_disc * 100, 1),
            "city_disc_share": round(city_disc * 100, 1),
            "ratio": round(ratio, 2),
            "severity_per_incident": round(local_sev, 2),
            "zip": zip_code, "zip_percentile": pct,
            "zip_income": zrec.get("income") if zrec else None,
            "zip_per_1k": round(zrec["per_1k"], 1) if zrec and zrec.get("per_1k") else None,
            "income_corr": prof["income_corr"],
            "clearance_income_corr": prof["clearance_income_corr"],
            "n_zips": prof["n_zips"],
            "caveat": ("Dallas publishes no arrest flag, so this uses discretionary "
                       "(officer-initiated) share as a proxy for enforcement intensity. "
                       "It is a screening indicator, not evidence of bias in any individual case.")}


# ══════════════════════════════════════════════════════════════════════════
#  3. FORECASTING
# ══════════════════════════════════════════════════════════════════════════

T_CRIT = {1: (3.078, 12.706), 2: (1.886, 4.303), 3: (1.638, 3.182),
          4: (1.533, 2.776), 5: (1.476, 2.571), 6: (1.440, 2.447),
          7: (1.415, 2.365), 8: (1.397, 2.306)}


def ols_forecast(xs, ys, horizons) -> Optional[Dict]:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    intercept = my - slope * mx
    sse = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    df = n - 2
    s = math.sqrt(sse / df) if df > 0 else 0.0
    sst = sum((y - my) ** 2 for y in ys)
    r2 = (1 - sse / sst) if sst > 0 else None
    t80, t95 = T_CRIT.get(df, (1.4, 2.3))
    preds = []
    for x0 in horizons:
        yh = intercept + slope * x0
        se = s * math.sqrt(1 + 1 / n + (x0 - mx) ** 2 / sxx)
        preds.append({"x": x0, "fit": yh, "lo80": yh - t80 * se, "hi80": yh + t80 * se,
                      "lo95": yh - t95 * se, "hi95": yh + t95 * se})
    return {"slope": slope, "intercept": intercept,
            "r2": round(r2, 3) if r2 is not None else None,
            "n": n, "residual_sd": round(s, 2), "predictions": preds}


def value_trajectory(home_values: List[Dict]) -> Optional[Dict]:
    pts = [(hv["year"], hv["value"]) for hv in home_values if hv.get("value")]
    if len(pts) < 3:
        return None
    xs = [p[0] for p in pts]; ys = [float(p[1]) for p in pts]
    last = max(xs)
    fit = ols_forecast(xs, ys, [last + 1, last + 2, last + 3])
    if not fit:
        return None
    base = ys[-1]
    return {"history": [{"year": x, "value": int(y)} for x, y in zip(xs, ys)],
            "projection": [{"year": int(p["x"]), "fit": int(round(p["fit"])),
                            "lo80": int(round(max(0, p["lo80"]))), "hi80": int(round(p["hi80"])),
                            "lo95": int(round(max(0, p["lo95"]))), "hi95": int(round(p["hi95"]))}
                           for p in fit["predictions"]],
            "annual_change": int(round(fit["slope"])),
            "annual_pct": round((fit["slope"] / base * 100) if base else 0.0, 2),
            "r2": fit["r2"], "n": fit["n"],
            "direction": "rising" if fit["slope"] > 0 else ("falling" if fit["slope"] < 0 else "flat"),
            "caveat": (f"Fitted on {fit['n']} annual ACS estimates. ACS 5-year values are "
                       "themselves smoothed over five years, so short-run turning points are "
                       "damped and intervals are wide.")}


def crime_trajectory(hits: List[Dict]) -> Optional[Dict]:
    cy = datetime.utcnow().year
    counts = Counter(h["year"] for h in hits if h["year"] and h["year"] < cy)
    if len(counts) < 3:
        return None
    xs = sorted(counts); ys = [float(counts[x]) for x in xs]
    last = max(xs)
    fit = ols_forecast(xs, ys, [last + 1, last + 2])
    if not fit:
        return None
    mean_y = sum(ys) / len(ys)
    return {"history": [{"year": x, "count": int(y)} for x, y in zip(xs, ys)],
            "projection": [{"year": int(p["x"]), "fit": max(0, round(p["fit"], 1)),
                            "lo80": max(0, round(p["lo80"], 1)), "hi80": round(p["hi80"], 1),
                            "lo95": max(0, round(p["lo95"], 1)), "hi95": round(p["hi95"], 1)}
                           for p in fit["predictions"]],
            "annual_change": round(fit["slope"], 1),
            "annual_pct": round((fit["slope"] / mean_y * 100) if mean_y else 0.0, 1),
            "r2": fit["r2"], "n": fit["n"],
            "direction": "rising" if fit["slope"] > 0 else ("falling" if fit["slope"] < 0 else "flat"),
            "excluded_year": cy,
            "caveat": (f"{cy} excluded as an incomplete reporting year. Fitted on {fit['n']} "
                       "complete years, so intervals are wide and this is a trend indicator "
                       "rather than a point prediction.")}


# ══════════════════════════════════════════════════════════════════════════
#  4. TEMPORAL PROFILE
# ══════════════════════════════════════════════════════════════════════════

BLOCKS = [("Late night", 0, 5), ("Morning", 6, 11),
          ("Afternoon", 12, 17), ("Evening", 18, 23)]


def temporal_profile(hits: List[Dict]) -> Optional[Dict]:
    timed = [h for h in hits if h["hour"] is not None]
    if len(timed) < 20:
        return None
    base = citywide_baseline()
    n = len(timed)

    hours = Counter(h["hour"] for h in timed)
    hour_rows = []
    for hh in range(24):
        obs = hours.get(hh, 0)
        exp = base["hour_share"].get(hh, 0.0) * n
        hour_rows.append({"hour": hh, "count": obs,
                          "share": round(obs / n * 100, 2),
                          "index": round((obs + 0.5) / (exp + 0.5), 2)})

    blocks = []
    for name, a, b in BLOCKS:
        obs = sum(hours.get(hh, 0) for hh in range(a, b + 1))
        exp = sum(base["hour_share"].get(hh, 0.0) for hh in range(a, b + 1)) * n
        blocks.append({"name": name, "range": f"{a:02d}:00-{b:02d}:59",
                       "count": obs, "share": round(obs / n * 100, 1),
                       "index": round((obs + 0.5) / (exp + 0.5), 2)})

    dayed = [h for h in hits if h["day"]]
    days = Counter(h["day"] for h in dayed)
    nd = len(dayed) or 1
    day_rows = [{"day": d, "count": days.get(d, 0),
                 "share": round(days.get(d, 0) / nd * 100, 1),
                 "index": round((days.get(d, 0) + 0.5) /
                                (base["day_share"].get(d, 1 / 7) * nd + 0.5), 2)}
                for d in DAY_ORDER]

    peak_block = max(blocks, key=lambda b: b["index"])
    quiet_block = min(blocks, key=lambda b: b["index"])
    peak_hour = max(hour_rows, key=lambda r: r["count"])
    peak_day = max(day_rows, key=lambda r: r["index"]) if dayed else None

    return {"n_timed": n, "hours": hour_rows, "blocks": blocks, "days": day_rows,
            "peak_block": peak_block, "quiet_block": quiet_block,
            "peak_hour": peak_hour, "peak_day": peak_day,
            "night_share": round(sum(b["share"] for b in blocks
                                     if b["name"] in ("Late night", "Evening")), 1),
            "caveat": ("Times are the reported time of occurrence. Incidents discovered later "
                       "(a car broken into overnight) carry the discovery time, which shifts "
                       "some property crime toward morning hours.")}


# ══════════════════════════════════════════════════════════════════════════
#  5. CASE OUTCOMES — indirectly standardised
#
#  Comparing an area's raw clearance rate against the citywide average is
#  misleading: clearance is largely determined by offence type. Officer-
#  initiated offences (drugs, trespass) clear at very high rates because the
#  suspect is present at discovery; victim-reported offences (burglary,
#  theft) clear rarely. An area's headline rate therefore reflects its crime
#  mix more than its policing.
#
#  Indirect standardisation fixes this. For each offence class we take the
#  citywide clearance rate, multiply by how many of that class this area
#  actually has, and sum to get an expected clearance count. The ratio of
#  observed to expected isolates the part that is NOT explained by mix.
# ══════════════════════════════════════════════════════════════════════════

def outcome_profile(hits: List[Dict]) -> Optional[Dict]:
    known = [h for h in hits if h["status"]]
    if len(known) < 20:
        return None
    base = citywide_baseline()
    n = len(known)
    cleared = sum(1 for h in known if h["cleared"])
    arrests = sum(1 for h in known if h["arrest"])

    by_cls, expected = {}, 0.0
    for cls in ("violent", "property", "vice", "other"):
        sub = [h for h in known if h["cls"] == cls]
        if not sub:
            continue
        city_rate = base["cleared_by_class"].get(cls, 0.0)
        expected += len(sub) * city_rate
        by_cls[cls] = {
            "n": len(sub),
            "cleared_pct": round(sum(1 for h in sub if h["cleared"]) / len(sub) * 100, 1),
            "city_pct": round(city_rate * 100, 1),
        }

    crude = cleared / n
    adj_ratio = (cleared / expected) if expected > 0 else 1.0

    if   adj_ratio >= 1.15: flag = "above"
    elif adj_ratio <= 0.85: flag = "below"
    else:                   flag = "typical"

    return {
        "n": n,
        "cleared_pct": round(crude * 100, 1),
        "arrest_pct": round(arrests / n * 100, 1),
        "city_cleared_pct": round(base["cleared_share"] * 100, 1),
        "expected_pct": round(expected / n * 100, 1),
        "adj_ratio": round(adj_ratio, 2),
        "flag": flag,
        "by_class": by_cls,
        "caveat": ("Compared against what this area's particular mix of offence types would "
                   "be expected to yield at citywide rates, not against the raw citywide "
                   "average. Officer-initiated offences clear far more often than "
                   "victim-reported ones, so an unadjusted comparison mostly measures "
                   "offence mix rather than investigative performance."),
    }


# ══════════════════════════════════════════════════════════════════════════
#  6. EXPOSURE PROFILE
# ══════════════════════════════════════════════════════════════════════════

PREM_LABEL = {"home": "Houses", "apartment": "Apartments", "parking": "Parking &amp; vehicles",
              "street": "Streets &amp; outdoors", "business": "Shops &amp; businesses",
              "other": "Other locations"}


def exposure_profile(hits: List[Dict]) -> Optional[Dict]:
    if len(hits) < 15:
        return None
    n = len(hits)
    prem = Counter(h["prem"] for h in hits)
    base = citywide_baseline()

    rows = []
    for k, c in prem.most_common():
        exp = base["premise_share"].get(k, 0.0) * n
        rows.append({"key": k, "label": PREM_LABEL.get(k, k.title()), "count": c,
                     "share": round(c / n * 100, 1),
                     "index": round((c + 0.5) / (exp + 0.5), 2)})

    vt = Counter(h["victim"] for h in hits if h["victim"])
    nv = sum(vt.values()) or 1
    victims = [{"type": t, "count": c, "share": round(c / nv * 100, 1)}
               for t, c in vt.most_common(5)]
    indiv = vt.get("Individual", 0) / nv * 100 if nv else 0

    violent = [h for h in hits if h["cls"] == "violent"]
    weapon_pct = round(sum(1 for h in violent if h["weapon"]) / len(violent) * 100, 1) if violent else None

    return {"premises": rows, "victims": victims,
            "individual_pct": round(indiv, 1),
            "weapon_pct_of_violent": weapon_pct,
            "n_violent": len(violent),
            "top_premise": rows[0] if rows else None}


# ══════════════════════════════════════════════════════════════════════════
#  MATCHING
# ══════════════════════════════════════════════════════════════════════════

def crime_history_matches(user_input: str, crimes: List[Dict]) -> List[Dict]:
    if not user_input:
        return []
    coord = is_coord_string(user_input)
    matches = []
    if coord:
        lat0, lon0 = coord
        for r in crimes:
            if haversine_km(lat0, lon0, r["lat"], r["lon"]) <= 0.15:
                matches.append(r)
    else:
        ni = normalize_address_for_match(user_input)
        if not ni:
            return []
        for r in crimes:
            na = normalize_address_for_match(r["address"])
            if na and (ni == na or ni in na or na in ni):
                matches.append(r)
    seen, unique = set(), []
    for m in matches:
        key = (m["address"], m["incident_type"], str(m["year"]))
        if key not in seen:
            seen.add(key); unique.append(m)
    return unique


def crimes_within_radius(lat, lon, crimes, radius_km=BUFFER_KM) -> List[Dict]:
    hits = []
    for r in crimes:
        d = haversine_km(lat, lon, r["lat"], r["lon"])
        if d <= radius_km:
            hits.append({**r, "distance_km": round(d, 3)})
    hits.sort(key=lambda x: x["distance_km"])
    return hits


def summarize_by_type(hits: List[Dict], top_n: int = 10) -> List[Dict]:
    agg = defaultdict(lambda: {"count": 0, "cls": "other", "years": Counter()})
    for h in hits:
        a = agg[h["incident_type"]]
        a["count"] += 1
        a["cls"] = h["cls"]
        if h["year"]:
            a["years"][h["year"]] += 1
    rows = sorted(agg.items(), key=lambda kv: -kv[1]["count"])[:top_n]
    return [{"type": t, "count": v["count"], "cls": v["cls"],
             "years": [{"year": y, "n": c} for y, c in sorted(v["years"].items())]}
            for t, v in rows]


# ══════════════════════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.post("/api/lookup")
async def lookup(body: LookupRequest):
    address = body.address.strip()
    coord = is_coord_string(address)
    try:
        lat, lon = coord if coord else geocode_dallas(address)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    try:
        weather = get_weather(lat, lon)
    except Exception:
        weather = {"condition": "Unavailable", "temp_f": None, "wind_mph": None, "precip_pct": None}
    return {"lat": lat, "lon": lon, "address": address,
            "weather": weather, "buffer_km": BUFFER_KM}


@app.post("/api/analyze")
async def analyze(body: AnalyzeRequest):
    crimes = _fetch_and_cache_crimes()
    history = crime_history_matches(body.address, crimes)
    nearby = crimes_within_radius(body.lat, body.lon, crimes, BUFFER_KM)
    home_vals = real_home_values(body.lat, body.lon)

    def slim(r):
        out = {"address": r["address"], "incident_type": r["incident_type"],
               "year": str(r["year"] or ""), "cls": r["cls"],
               "hour": r["hour"], "day": r["day"], "cleared": r["cleared"]}
        if "distance_km" in r:
            out["distance_km"] = r["distance_km"]
        return out

    def safe(fn, *a):
        try:
            return fn(*a)
        except Exception as e:
            print(f"[Settler] {fn.__name__} failed: {e}")
            return None

    return {
        "buffer_km": BUFFER_KM,
        "home_values": home_vals,
        "history_crimes": [slim(r) for r in history],
        "history_count": len(history),
        "history_by_type": summarize_by_type(history, 12),
        "nearby_crimes": [slim(r) for r in nearby[:150]],
        "nearby_count": len(nearby),
        "nearby_by_type": summarize_by_type(nearby, 12),
        "risk": safe(risk_decomposition, nearby, BUFFER_KM),
        "equity": safe(equity_audit, nearby),
        "forecast": {"value": safe(value_trajectory, home_vals),
                     "crime": safe(crime_trajectory, nearby)},
        "temporal": safe(temporal_profile, nearby),
        "outcomes": safe(outcome_profile, nearby),
        "exposure": safe(exposure_profile, nearby),
    }


@app.get("/api/crimes/geojson")
async def crimes_geojson():
    return JSONResponse(build_geojson(_fetch_and_cache_crimes()))


@app.get("/api/crimes/progress")
async def crimes_progress():
    return _fetch_progress


@app.get("/api/crimes/status")
async def crimes_status():
    f = _crime_cache.get("fetched_at")
    n = len(_crime_cache["data"]) if _crime_cache["data"] else 0
    return {"cached": bool(_crime_cache["data"]), "count": n,
            "fetched_at": f.isoformat() if f else None, "buffer_km": BUFFER_KM}


@app.get("/api/clearance/by-class")
async def clearance_by_class():
    """Citywide clearance rate per offence class — the standardisation weights."""
    _fetch_and_cache_crimes()
    base = citywide_baseline()
    return {"overall_pct": round(base["cleared_share"] * 100, 2),
            "by_class_pct": {k: round(v * 100, 2)
                             for k, v in base["cleared_by_class"].items()}}


@app.get("/api/equity/citywide")
async def equity_citywide():
    _fetch_and_cache_crimes()
    prof = equity_profile()
    rows = sorted(prof["zips"].values(), key=lambda v: -v["disc_share"])
    return {"city_disc_share": round(prof["city_disc_share"] * 100, 2),
            "income_corr": prof["income_corr"],
            "clearance_income_corr": prof["clearance_income_corr"],
            "n_zips": prof["n_zips"], "n_paired": prof["n_paired"],
            "zips": [{"zip": r["zip"], "incidents": r["incidents"],
                      "disc_share_pct": round(r["disc_share"] * 100, 1),
                      "cleared_pct": round(r["cleared_share"] * 100, 1) if r["cleared_share"] is not None else None,
                      "severity_per_incident": round(r["severity_per_incident"], 2),
                      "median_income": r["income"],
                      "incidents_per_1k": round(r["per_1k"], 1) if r.get("per_1k") else None}
                     for r in rows]}