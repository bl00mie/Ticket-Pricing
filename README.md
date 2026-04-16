# Ticket Pricing — OpenClaw Flight Pricing Skill

A self-hosted flight pricing tool designed for OpenClaw agents. It fetches live flight offers from a configurable provider (Duffel by default), caches results locally for historical analysis, and exposes a simple CLI that OpenClaw agents can invoke via bash.

## Key features

- Live flight price lookup via Duffel (provider adapter pattern)
- Local SQLite cache (WAL mode) to accumulate true historical prices
- `--history` flag to query accumulated price snapshots
- Optional aggregate price context via Travelpayouts
- Robust input validation, retries, and cache fallback
- OpenClaw skill integration via `SKILL.md` (bash-callable CLI)

## Quickstart

Requirements
- Python 3.11+
- pip

Install
```bash
cd "c:\Users\chadb\Documents\projects\Ticket Pricing\skills\flight-pricing"
pip install -r requirements.txt
cp .env.example .env
# Edit .env to add DUFFEL_ACCESS_TOKEN (required) and TRAVELPAYOUTS_TOKEN (optional)
```

Run an example
```bash
python flight_pricing.py --origin LAX --destination JFK --date 2026-05-15
```

Run tests
```bash
python -m pytest tests/ -v
```

## Where things live

- Skill code: `skills/flight-pricing/`
- CLI entry: `skills/flight-pricing/flight_pricing.py`
- Cache DB (default): `~/.openclaw/workspace/skills/flight-pricing/cache.db` (override with `--cache-db`)
- OpenClaw skill descriptor: `skills/flight-pricing/SKILL.md`
- Tests: `skills/flight-pricing/tests/`

## Environment

Create `.env` from `.env.example` and set:
- `DUFFEL_ACCESS_TOKEN` (required)
- `TRAVELPAYOUTS_TOKEN` (optional)

## Notes
- The tool intentionally treats "historical pricing" as the locally accumulated cache; no public API reliably returns actual past ticket quotes.
- Duffel was selected because it provides broad airline coverage and a stable API; Amadeus self-service is being decommissioned in July 2026 and lacks several major carriers in its free tier.

## Contributing

Contributions welcome. To add another provider, implement `providers/<name>.py` by subclassing the interface in `providers/base.py`.

## License

Add your license here.
