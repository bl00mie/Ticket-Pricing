---
name: flight-pricing
description: Use when searching for flight prices, comparing fare options for a specific itinerary, or tracking the same route over time with local cached history. Supports one-way and round-trip lookups, cabin filtering, nonstop preference, and historical price snapshots via a local Python script backed by Duffel.
---

# Flight Pricing

Use this skill when Chad wants current flight options or ongoing price tracking for a route.

## Run the tool

From the OpenClaw workspace, use:

```bash
python3 /home/chad/.openclaw/workspace/skills/flight-pricing/flight_pricing.py [flags]
```

The workspace path is correct even though the skill may be symlinked to a repository underneath.

## Setup

If the script fails on missing dependencies, install them with:

```bash
python3 -m pip install --user -r /home/chad/.openclaw/workspace/skills/flight-pricing/requirements.txt
```

If `.env` is missing, create it from `.env.example` and add tokens:

```bash
cp /home/chad/.openclaw/workspace/skills/flight-pricing/.env.example \
   /home/chad/.openclaw/workspace/skills/flight-pricing/.env
```

Required token:
- `DUFFEL_ACCESS_TOKEN`

Optional token:
- `TRAVELPAYOUTS_TOKEN` for broader price context

Never commit `.env`, cache DBs, logs, or token values.

## Common usage

### Round-trip, premium cabin, nonstop preferred

```bash
python3 /home/chad/.openclaw/workspace/skills/flight-pricing/flight_pricing.py \
  --origin SEA --destination BWI --date 2026-06-09 --return-date 2026-06-14 \
  --cabin FIRST --non-stop --max-results 5
```

### Force live refresh instead of cache

```bash
python3 /home/chad/.openclaw/workspace/skills/flight-pricing/flight_pricing.py \
  --origin SEA --destination BWI --date 2026-06-09 --return-date 2026-06-14 \
  --cabin FIRST --non-stop --max-results 5 --force-refresh
```

### Show cached history

```bash
python3 /home/chad/.openclaw/workspace/skills/flight-pricing/flight_pricing.py \
  --origin SEA --destination BWI --date 2026-06-09 --return-date 2026-06-14 \
  --cabin FIRST --history
```

## What to summarize for Chad

The script prints JSON. Usually summarize:
- cheapest total price
- airline(s)
- outbound and return times
- whether it is truly nonstop
- whether cache or live data was used
- any notable trend from `--history`

For Chad's preferences, call out:
- Alaska options first if competitive
- materially cheaper alternatives if they beat Alaska by enough to matter
- whether the itinerary matches stated timing preferences, not just raw price

## Important behavior notes

- `--non-stop` means direct flights only.
- Route matching should be exact. Do not accept off-route offers that land in the wrong airport just because the provider returned them.
- For ongoing monitoring, collect a fresh live sample periodically with `--force-refresh`, then compare against `--history`.
- If no price context is available, say that plainly instead of inventing trend confidence.
