# Flight Pricing Tool for OpenClaw Agents

A CLI tool that fetches live flight prices from the Duffel API, caches
every result in a local SQLite database for historical tracking, and
optionally enriches responses with aggregate price context from
Travelpayouts.

## Prerequisites

- **Python** 3.11+
- **OpenClaw** (any recent version with bash tool support)
- **Duffel API token** (free tier — 1,000 offers/month)
- **Travelpayouts token** *(optional, for price context)*

## Installation

```bash
# 1. Navigate to the skill directory
cd ~/.openclaw/workspace/skills/flight-pricing

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Create your environment file
cp .env.example .env

# 4. Edit .env and add your API tokens
#    DUFFEL_ACCESS_TOKEN=duffel_test_...   (required)
#    TRAVELPAYOUTS_TOKEN=...               (optional)
```

## API Setup

### Duffel (required)

1. Sign up at <https://app.duffel.com>
2. Navigate to **Tokens** in the dashboard
3. Create a **Test** token (prefix `duffel_test_`) for development
4. Create a **Live** token (prefix `duffel_live_`) for production
5. Paste the token into `.env` as `DUFFEL_ACCESS_TOKEN`

Free tier includes 1,000 offer requests per month — sufficient for
personal agent use.

### Travelpayouts (optional)

1. Sign up at <https://www.travelpayouts.com/developers/api>
2. Copy your API token
3. Paste into `.env` as `TRAVELPAYOUTS_TOKEN`

This adds a `price_context` section to responses showing whether the
current price is below, at, or above the typical range for that route.

## Configuration

All configuration is via environment variables (loaded from `.env`):

| Variable | Required | Description |
|----------|----------|-------------|
| `DUFFEL_ACCESS_TOKEN` | Yes | Duffel API bearer token |
| `TRAVELPAYOUTS_TOKEN` | No | Travelpayouts API token for price context |

## OpenClaw Skill Setup

The `SKILL.md` file in this directory teaches your OpenClaw agent how to
use the tool.  Place the entire `flight-pricing/` directory under:

```
~/.openclaw/workspace/skills/flight-pricing/
```

The agent will automatically discover `SKILL.md` and learn to invoke the
tool via its `bash` tool.

## Usage Examples

### 1. One-way flight
```bash
python flight_pricing.py --origin LAX --destination JFK --date 2026-05-15
```

### 2. Round-trip
```bash
python flight_pricing.py --origin LAX --destination JFK \
  --date 2026-05-15 --return-date 2026-05-20
```

### 3. Non-stop business class
```bash
python flight_pricing.py --origin SFO --destination LHR \
  --date 2026-06-01 --cabin BUSINESS --non-stop
```

### 4. Multiple passengers, sorted by duration
```bash
python flight_pricing.py --origin ORD --destination MIA \
  --date 2026-07-04 --passengers 4 --sort-by duration
```

### 5. Price history
```bash
python flight_pricing.py --origin LAX --destination JFK \
  --date 2026-05-15 --history
```

### 6. Force fresh data (bypass cache)
```bash
python flight_pricing.py --origin LAX --destination JFK \
  --date 2026-05-15 --force-refresh
```

## Output Format

All output is a single JSON object printed to stdout.

### Successful search

```json
{
  "query": {
    "origin": "LAX",
    "destination": "JFK",
    "departure_date": "2026-05-15",
    "return_date": null,
    "passengers": 1,
    "cabin": "ECONOMY",
    "non_stop": false,
    "currency": "USD",
    "queried_at": "2026-04-16T10:30:00Z"
  },
  "meta": {
    "cache_hit": false,
    "data_source": "duffel_live",
    "cache_age_minutes": null,
    "total_results": 2,
    "provider": "duffel"
  },
  "results": [
    {
      "offer_id": "off_abc123",
      "total_price": 289.50,
      "price_per_person": 289.50,
      "currency": "USD",
      "cabin": "ECONOMY",
      "outbound": {
        "departure_at": "2026-05-15T08:00:00",
        "arrival_at": "2026-05-15T16:30:00",
        "duration_minutes": 330,
        "stops": 0,
        "segments": [
          {
            "flight_number": "UA123",
            "carrier_iata": "UA",
            "carrier_name": "United Airlines",
            "origin": "LAX",
            "destination": "JFK",
            "departure_at": "2026-05-15T08:00:00",
            "arrival_at": "2026-05-15T16:30:00",
            "aircraft": "Boeing 737-800"
          }
        ]
      },
      "return": null,
      "baggage_included": true,
      "refundable": false,
      "expires_at": "2026-04-17T10:30:00Z",
      "fetched_at": "2026-04-16T10:30:00Z"
    }
  ],
  "price_context": {
    "source": "travelpayouts_aggregate",
    "typical_minimum": 199.00,
    "typical_median": 350.00,
    "typical_maximum": 650.00,
    "current_assessment": "below_median"
  }
}
```

### Error

```json
{
  "error": true,
  "code": "INVALID_AIRPORT_CODE",
  "message": "LAX1 is not a valid IATA airport code.",
  "query": { "origin": "LAX1", "destination": "JFK", ... }
}
```

## Cache

### Location

```
~/.openclaw/workspace/skills/flight-pricing/cache.db
```

Override with `--cache-db /path/to/other.db` for testing.

### Schema

```sql
CREATE TABLE price_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    departure_date TEXT NOT NULL,
    return_date TEXT,
    passengers INTEGER NOT NULL,
    cabin TEXT NOT NULL,
    currency TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    provider TEXT NOT NULL,
    response_json TEXT NOT NULL
);
```

### TTL

- **Live queries**: cached results are valid for **4 hours**.  After
  that, a new live fetch is made automatically.
- **`--history`**: returns ALL cached entries regardless of age.
- **`--force-refresh`**: always fetches live, bypassing cache.

### Direct queries with sqlite3

```bash
# Find the cheapest recorded price for LAX→JFK
sqlite3 ~/.openclaw/workspace/skills/flight-pricing/cache.db \
  "SELECT fetched_at,
          json_extract(response_json, '$[0].total_price') AS cheapest
   FROM price_cache
   WHERE origin='LAX' AND destination='JFK'
   ORDER BY cheapest ASC
   LIMIT 5;"
```

## Historical Price Research

True historical pricing = the accumulated cache.  Every live query
stores its results, building a price history over time.

### SQL: cheapest price per day

```sql
SELECT date(fetched_at) AS day,
       MIN(json_extract(value, '$.total_price')) AS cheapest
FROM price_cache, json_each(response_json)
WHERE origin = 'LAX' AND destination = 'JFK'
GROUP BY day
ORDER BY day;
```

### SQL: price trend for a specific date

```sql
SELECT fetched_at,
       json_extract(response_json, '$[0].total_price') AS cheapest
FROM price_cache
WHERE origin = 'LAX' AND destination = 'JFK'
  AND departure_date = '2026-05-15'
ORDER BY fetched_at;
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `AUTHENTICATION_ERROR` | Check that `DUFFEL_ACCESS_TOKEN` is set in `.env` and is valid |
| `INVALID_AIRPORT_CODE` | Use exactly 3 uppercase letters (IATA code). Check the code at <https://www.iata.org/en/publications/directories/code-search/> |
| `PAST_DATE_ERROR` | Departure date must be today or later for live queries. Use `--history` for past dates. |
| `INVALID_DATE_RANGE` | Return date must be at least 1 day after departure date |
| `API_UNAVAILABLE` | Duffel API is down or your network is offline. Cached data will be used if available. |
| `No module named 'pydantic'` | Run `pip install -r requirements.txt` |
| Empty results | Try removing `--non-stop`, different dates, or a different route. Some routes have limited availability. |
| Stale prices | Use `--force-refresh` to bypass the 4-hour cache |

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  OpenClaw Agent                                         │
│  (reads SKILL.md, invokes via bash tool)                │
└──────────────────────┬──────────────────────────────────┘
                       │  bash: python flight_pricing.py ...
                       ▼
┌─────────────────────────────────────────────────────────┐
│  flight_pricing.py  (CLI entry point)                   │
│  ├── argparse + validation                              │
│  ├── cache check (SQLite, 4h TTL)                       │
│  ├── live fetch via provider adapter                    │
│  └── JSON output to stdout                              │
├─────────────────────────────────────────────────────────┤
│  providers/                                             │
│  ├── base.py       Abstract FlightSearchProvider        │
│  ├── duffel.py     POST /air/offer_requests             │
│  └── travelpayouts.py  GET prices_for_dates (context)   │
├─────────────────────────────────────────────────────────┤
│  cache.py          SQLite + WAL mode                    │
│  cache.db          ~/.openclaw/.../cache.db             │
├─────────────────────────────────────────────────────────┤
│  models.py         Pydantic v2 data models              │
└─────────────────────────────────────────────────────────┘
```

## Adding a New Provider

1. Create `providers/my_provider.py`
2. Subclass `FlightSearchProvider` from `providers/base.py`
3. Implement the `search()` method
4. Import and instantiate in `flight_pricing.py` where `DuffelProvider`
   is used (or add a `--provider` CLI flag)

No changes to models, cache, or tests infrastructure needed.

## Limitations

This tool does **not**:

- **Book flights** — it's a price lookup tool, not a booking engine
- **Select seats** or add baggage upgrades
- **Cover all airlines** — coverage depends on Duffel's network
  (most major carriers + many LCCs via Duffel Access, but not every
  airline worldwide)
- **Guarantee prices** — offers expire (see `expires_at` field);
  prices can change between lookup and booking
- **Provide true global historical data** — historical pricing is
  limited to what has been cached locally by previous queries
- **Convert currencies** via a separate FX API — it uses the currency
  returned by the provider or the `--currency` flag passed to the API
- **Handle infant/child passenger types** — all passengers are treated
  as adults
