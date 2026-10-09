# The Settler App

**Dallas crime and neighborhood intelligence for people deciding where to live.**

Search any Dallas address and Settler shows what has actually been happening around it: four years of police incidents within 1 km, how the area compares to the citywide baseline, when and where incidents tend to happen, where crime and home values are heading, and an **equity audit** that flags when a high crime count may reflect heavier policing rather than greater danger to residents.

### ▶️ [Try it live](https://dallas-settler-app-9fh3.onrender.com)

*Hosted on a free tier, so the first visit may take a minute to wake up.*

<!-- SCREENSHOTS: add images to docs/, then uncomment:
![Map view](docs/map.png)
![Equity audit](docs/equity.png)
-->

---

## Why I built it

A friend moved to Dallas without knowing the city and asked me a simple question: "Is this neighborhood actually safe?" The data to answer that exists, but it is spread across police records and census tables, and the commercial tools that summarize it do not show their methods. Settler puts it in one place and is transparent about how every number is produced.

---

## Analytic layers

Every searched address is analyzed within a **1 km radius** and compared against a citywide baseline built from the same dataset.

| # | Layer | What it tells you |
|---|-------|-------------------|
| 1 | **Risk decomposition** | An additive log-density score split into violent, property, vice, other, recency and clustering components. For an additive model each bar is that feature's exact Shapley contribution; zero means citywide-typical. |
| 2 | **Enforcement-equity audit** | Compares the local share of officer-initiated (discretionary) offenses such as drugs, trespass, warrants and traffic against the citywide norm, and relates it to ZIP-level median income from the Census. Flags areas where counts are likely inflated by enforcement intensity. |
| 3 | **Trajectory forecasting** | OLS trends with 80% and 95% prediction intervals for local crime counts and median home values. The current, incomplete year is excluded. |
| 4 | **Temporal profile** | Hour-of-day and day-of-week patterns, indexed against the citywide distribution. |
| 5 | **Case outcomes** | Clearance rates indirectly standardized by offense mix, so an area is not penalized or flattered for having a different blend of crime types. |
| 6 | **Exposure profile** | Where incidents occur (homes, apartments, parking, streets, businesses), victim types, and the share of violent incidents involving a weapon. |

The map also plots every reported incident from the last four years, and the sidebar shows local weather and a ranked list of the nearest incidents. Dark and light themes are included.

### A note on fairness

Raw crime counts can unfairly stigmatize neighborhoods. Officer-initiated offenses are recorded where police choose to patrol, so heavily policed areas can look more dangerous than they are for the people who live there. Dallas does not publish an arrest-initiation flag, so Settler uses the discretionary-offense share as a proxy. It is a screening indicator, not evidence of bias in any individual case.

---

## Setup

```bash
git clone https://github.com/StanLi01/Dallas-Settler-App.git
cd Dallas-Settler-App
python -m venv venv
venv\Scripts\activate            # Mac/Linux: source venv/bin/activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill it in:

```
USER_AGENT_EMAIL=you@example.com
CENSUS_API_KEY=your_census_key_here
```

A free Census key takes about a minute: https://api.census.gov/data/key_signup.html

---

## Running it

**Without a terminal:** double-click `run_app.py`. It starts the server and opens your browser. Close the console window to stop it.

**With a terminal:**

```bash
uvicorn main:app --reload --port 8000
```

Then open http://localhost:8000

### First load takes 30-90 seconds

Dallas Open Data caps rows per request, so the backend pages through the dataset in 50,000-row chunks. The query selects only the columns the app needs, and results are cached server-side for 6 hours, so later loads are instant. A live badge shows progress ("Fetched 50,000 records...") while it loads.

---

## Deploying

**Render:** the included `render.yaml` sets the build and start commands; set `USER_AGENT_EMAIL` and `CENSUS_API_KEY` as environment variables.

**Railway:** `railway init && railway up`, then add the same two variables in the dashboard.

For production with multiple workers, swap the in-memory crime cache for Redis so all processes share one cache.

---

## Configuration

Constants at the top of `main.py`:

```python
BUFFER_KM       = 1.0    # analysis radius
YEARS_BACK      = 4      # how far back to pull crime data
CACHE_TTL_HOURS = 6      # how long before re-fetching
FETCH_PAGE_SIZE = 50000  # rows per API page
```

---

## API reference

| Method | Path | Returns |
|--------|------|---------|
| GET | `/` | The app |
| POST | `/api/lookup` | Coordinates, weather, buffer radius |
| POST | `/api/analyze` | Home values, nearby crimes, and all six analytic layers |
| GET | `/api/crimes/geojson` | Every crime as a GeoJSON point |
| GET | `/api/crimes/progress` | Fetch progress for the loading badge |
| GET | `/api/crimes/status` | Cache count, age, configured buffer |
| GET | `/api/clearance/by-class` | Citywide clearance rate per offense class |

---

## Project layout

```
Dallas-Settler-App/
├── main.py             # FastAPI backend and all analytics
├── run_app.py          # Double-click launcher
├── templates/
│   └── index.html      # UI: map, sidebar, theme toggle
├── static/             # Static assets
├── docs/               # Screenshots
├── requirements.txt
├── render.yaml         # Render deploy config
├── .env.example        # Copy to .env and add your keys
└── LICENSE
```

---

## Troubleshooting: the map has no dots

1. Check the console running the server. You should see lines like `[Settler] Total records fetched: ...`. Silence means the browser request never reached the backend.
2. Confirm you are running the folder you are editing.
3. Hard-refresh with Ctrl+Shift+R (Cmd+Shift+R on Mac).
4. Read the loading badge; it shows the exact error if the fetch fails.

---

## Data sources

- **Crime:** [Dallas Open Data, Police Incidents](https://www.dallasopendata.com/) (SODA API)
- **Home values and demographics:** [U.S. Census ACS 5-year](https://api.census.gov/data/) (B25077, B19013, B01003)
- **Tract lookup:** [FCC Census Block API](https://geo.fcc.gov/api/census/)
- **Weather:** [Open-Meteo](https://open-meteo.com/)
- **Geocoding:** [Nominatim / OpenStreetMap](https://nominatim.openstreetmap.org/)
- **Map tiles:** CARTO

---

## Limitations

- Reported incidents are not the same as all crime; under-reporting varies by area and offense.
- Incident times are often discovery times, which shifts some property crime toward mornings.
- ACS estimates are smoothed over five years, so home-value forecasts have wide intervals.
- The equity audit is a proxy-based screening tool, not a causal finding.

---

## Feedback

Issues and suggestions are welcome, especially on methodology.

## License

MIT, see [LICENSE](LICENSE).
