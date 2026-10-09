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

## Using the app

Settler is available only as a hosted web app: **[open it here](https://dallas-settler-app-9fh3.onrender.com)**. Nothing to install. The first visit may take a minute or two while the free server wakes up and loads four years of crime data; after that it's fast.

## License

Copyright (c) 2026 Stanley Osondu. All rights reserved. The source code is not licensed for copying, modification, redistribution or self-hosting. See [LICENSE](LICENSE).
