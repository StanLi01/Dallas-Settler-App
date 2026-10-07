# The Settler App

Dallas crime and neighborhood intelligence on one screen. Every reported crime from the
last four years is plotted as its own point. Search any address and the map flies in,
draws a **1 km buffer**, and the sidebar fills with home values, weather, and a breakdown
of what's happening around that location.

---

## What it does

**On load** — Fetches every crime record from Dallas Open Data (paginated, so nothing is
truncated) and plots each one as a small red dot. These stay on the map permanently;
searching never removes them.

**On search** — Geocodes the address, then:

| Output | Detail |
|---|---|
| Map | Flies to the address and fits the view to the full 1 km buffer |
| Buffer | One dashed 5 km ring so you can see every surrounding crime at a glance |
| Weather | Temperature, condition, wind, rain chance at that spot |
| Home values | Median value 2019–2023 (Census ACS 5-year) as a bar chart |
| Crime summary | Count at the exact address, and count within 1 km |
| Top crime types | Ranked bar chart of the most common offenses in the buffer |
| Nearest crimes | Sorted by distance, each labeled with how far away it is |

**Theme toggle** — Dark and light modes. The Leaflet basemap tiles swap along with the UI.

---

## Setup

One-time install:

    cd settler_web
    python -m venv venv
    source venv/bin/activate          # Windows: venv\Scripts\activate
    pip install fastapi "uvicorn[standard]" requests python-dotenv jinja2

Create `.env` in the `settler_web/` folder:

    USER_AGENT_EMAIL=you@example.com
    CENSUS_API_KEY=your_census_key_here

A free Census key takes about a minute: https://api.census.gov/data/key_signup.html

---

## Running it

**Without a terminal** — double-click `run_app.py`. It starts the server and
opens your browser automatically. Close the console window to stop it.

**With a terminal:**

    cd settler_web
    uvicorn main:app --reload --port 8000

Then open http://localhost:8000

---

## First load takes 30–90 seconds

This is expected, not a bug. Dallas Open Data caps how many rows a single request can
return, so the backend pages through the dataset with `$offset` in 50,000-row
chunks until it reaches the end. Four years of citywide data is typically 2–4 sequential
requests.

Two things make it as fast as it can be:

- The query uses `$select` to pull only the eight columns the app actually
  needs, instead of all ~70 fields per record.
- Results are cached server-side for 6 hours, so every reload after the first is instant.

While it loads, the sidebar badge polls the backend and shows a live count
("Fetched 50,000 records…") rather than a spinner that tells you nothing.

---

## Project layout

    settler_web/
    ├── main.py             # FastAPI backend
    ├── run_app.py          # Double-click launcher
    ├── templates/
    │   └── index.html      # Entire UI: map, sidebar, theme toggle
    ├── static/             # (empty — for custom assets)
    ├── .env                # Your keys — do not commit
    └── README.md

---

## API reference

| Method | Path | Returns |
|---|---|---|
| GET | `/` | The app |
| POST | `/api/lookup` | `{lat, lon, address, weather, buffer_km}` |
| POST | `/api/analyze` | Home values, crimes at address, crimes within 1 km, type breakdown |
| GET | `/api/crimes/geojson` | Every crime as a GeoJSON point |
| GET | `/api/crimes/progress` | `{status, fetched, error}` — powers the loading badge |
| GET | `/api/crimes/status` | Cache count, age, configured buffer |

GeoJSON properties are abbreviated to keep the payload small across tens of thousands
of features: `a` = address, `t` = incident type, `y` = year.

---

## Configuration

Edit these constants at the top of `main.py`:

    BUFFER_KM       = 1.0    # search radius
    YEARS_BACK      = 4      # how far back to pull crime data
    CACHE_TTL_HOURS = 6      # how long before re-fetching
    FETCH_PAGE_SIZE = 50000  # rows per API page

---

## If the map has no dots

1. **Check the console window** running the server. You should see:

       [Settler] Fetched 50000 rows (total: 50000)
       [Settler] Total records fetched: 134221
       [Settler] Records with coordinates: 131004

   Silence means the browser request never reached the backend.

2. **Confirm you're editing the folder you're running.** If more than one copy of this
   project exists on disk, run `pwd` immediately before starting the server.

3. **Hard-refresh** — Ctrl+Shift+R (Windows/Linux) or Cmd+Shift+R (Mac). Browsers cache
   `index.html` and its inline script aggressively.

4. **Read the badge.** It reports the exact error text if the fetch fails, so you won't
   have to guess whether it's slow or broken.

---

## Deploying

**Render** — build `pip install -r requirements.txt`, start
`uvicorn main:app --host 0.0.0.0 --port $PORT`, and set `USER_AGENT_EMAIL`
and `CENSUS_API_KEY` as environment variables.

**Railway** — `railway init && railway up`, then add the same two variables in
the dashboard.

For production with multiple workers, swap the in-memory `_crime_cache` dict for
Redis so all processes share one cache instead of each fetching separately.

---

## Data sources

- **Crime** — [Dallas Open Data, Police Incidents](https://www.dallasopendata.com/resource/qv6i-rri7.json) (SODA API)
- **Home values** — [Census ACS 5-year](https://api.census.gov/data/), variable B25077_001E
- **Tract lookup** — [FCC Block API](https://geo.fcc.gov/api/census/block/find)
- **Weather** — [Open-Meteo](https://open-meteo.com/) (no key required)
- **Geocoding** — [Nominatim / OpenStreetMap](https://nominatim.openstreetmap.org/)
- **Tiles** — CARTO Dark Matter and Positron
