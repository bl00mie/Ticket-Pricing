# flight-pricing

Fetches current flight prices for a route and maintains a local cache for historical price tracking. Output is JSON — parse it and summarize the key fields for the user.

## Usage

```bash
python ~/.openclaw/workspace/skills/flight-pricing/flight_pricing.py [flags]
```

### Required Flags
| Flag | Description |
|------|-------------|
| `--origin` | Origin IATA airport code (e.g. LAX) |
| `--destination` | Destination IATA airport code (e.g. JFK) |
| `--date` | Departure date in YYYY-MM-DD format |

### Optional Flags
| Flag | Description | Default |
|------|-------------|---------|
| `--return-date` | Return date YYYY-MM-DD (omit for one-way) | — |
| `--passengers` | Number of passengers (1-9) | 1 |
| `--cabin` | ECONOMY, PREMIUM_ECONOMY, BUSINESS, FIRST | ECONOMY |
| `--non-stop` | Only show direct flights | off |
| `--max-results` | Maximum results to return | 10 |
| `--sort-by` | price, duration, or departure_time | price |
| `--currency` | ISO 4217 currency code | USD |
| `--force-refresh` | Bypass cache, fetch live prices | off |
| `--history` | Show cached price history for this route | off |

## Examples

### One-way flight search
```bash
python ~/.openclaw/workspace/skills/flight-pricing/flight_pricing.py \
  --origin LAX --destination JFK --date 2026-05-15
```
Returns JSON with the cheapest flights from LAX to JFK on May 15, sorted by price.

### Round-trip with cabin class
```bash
python ~/.openclaw/workspace/skills/flight-pricing/flight_pricing.py \
  --origin SFO --destination LHR --date 2026-06-01 --return-date 2026-06-15 \
  --cabin BUSINESS --passengers 2
```
Returns business-class round-trip options for 2 passengers, SFO to London Heathrow.

### Price history for a route
```bash
python ~/.openclaw/workspace/skills/flight-pricing/flight_pricing.py \
  --origin LAX --destination JFK --date 2026-05-15 --history
```
Returns all previously cached price snapshots for this route, sorted chronologically. Use this to see how prices have changed over time.

## Output

The tool prints a single JSON object to stdout. Key fields to summarize:
- `results[].total_price` and `results[].price_per_person` — the prices
- `results[].outbound.segments[].carrier_name` — airline name
- `results[].outbound.departure_at` / `arrival_at` — flight times
- `results[].outbound.stops` — number of connections (0 = nonstop)
- `meta.cache_hit` — whether data came from cache or live API
- `price_context.current_assessment` — whether the price is below/at/above typical

Errors are also JSON with `"error": true` and a `"code"` field.

## One-Time Setup

```bash
cd ~/.openclaw/workspace/skills/flight-pricing
pip install -r requirements.txt
cp .env.example .env
# Edit .env and add your DUFFEL_ACCESS_TOKEN
```

Get your Duffel API token at https://app.duffel.com/tokens (free tier: 1,000 offers/month).
