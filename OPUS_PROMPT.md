# Strategic Prompt for Opus: Flight Pricing Tool for OpenClaw Agents

## Overview

This document contains the single-shot prompt for Claude Opus to build a production-ready flight pricing tool for OpenClaw agents. The prompt below reflects corrections and additions made after reviewing the initial draft against live API documentation and the actual OpenClaw architecture.

---

## Corrections Made vs. Initial Draft

These were real errors in the first pass that would have caused Opus to build the wrong thing:

| Issue | What was wrong | What's correct |
|-------|---------------|----------------|
| Amadeus decommission | Not mentioned | Self-Service portal shuts down July 17, 2026 — 3 months from now |
| Airline coverage | Presented as comprehensive | Excludes AA, Delta, British Airways, and **all low-cost carriers** (Southwest, Spirit, etc.) |
| Historical pricing | "Amadeus FlexSearch supports historical pricing" | **False.** Flight Price Analysis returns statistical percentile ranges only, not past quotes. True historical data = local cache accumulated over time. |
| OpenClaw plugin interface | Python class with schema registration | **Wrong.** Skills are a `SKILL.md` markdown file + a bash-callable script. Agents invoke via their native `bash` tool. |
| Output schema | `"outbound_flights": [...]` | Undefined — Opus would invent a schema. Explicit structure required. |
| Cache TTL | "Never auto-purge" | Needs two modes: short TTL for "book-ready" data, keep-forever for historic research |
| SQLite concurrency | Not addressed | Multiple agents can hit the cache simultaneously; WAL mode required |
| Input parameters | Passengers + airports + dates | Missing: cabin class, non-stop filter, max results, sort order |
| Date validation | Not specified | return_date must be after departure_date; past dates are invalid for live queries |

---

## SEND THIS PROMPT TO OPUS:

```
You are building a flight pricing lookup tool for OpenClaw agents (https://openclaw.ai). OpenClaw is a personal AI assistant that runs on the user's own hardware, with agents that have access to bash, file read/write, and a skills system. Read the context below carefully before writing any code.

═══════════════════════════════════════════════════════
CONTEXT: HOW THIS WILL BE USED
═══════════════════════════════════════════════════════

OpenClaw skills are NOT Python plugins or imported modules. They work like this:
  1. The core logic lives in a Python or shell script that can be called from the command line.
  2. A SKILL.md file in ~/.openclaw/workspace/skills/<skill-name>/SKILL.md teaches the agent what the skill does and how to invoke it via bash.
  3. The agent reads SKILL.md at startup and uses its bash tool to run the script when needed.

So your primary deliverable is a bash-callable Python script + a well-written SKILL.md. No class registration, no plugin API, no imports.

═══════════════════════════════════════════════════════
CONTEXT: THE "HISTORICAL PRICING" REQUIREMENT
═══════════════════════════════════════════════════════

No free or widely-available public API provides true historical flight prices (i.e., what a specific ticket cost in the past). What exists is:
  - Live/future pricing: what tickets cost right now for a future date
  - Statistical ranges: the Flight Price Analysis API returns min/quartile/max bands derived from historical bookings, not actual past quotes

TRUE historical pricing in this tool means: every time the tool fetches a live price, it stores it in a local SQLite cache. Over time, this accumulates a genuine price history for any route the agents care about. The agents can then query that cache to see "what did LAX→JFK cost last month vs today?"

Implement the tool with this understanding. Do not claim to retrieve past prices from external APIs — you cannot. Historic pricing IS the local cache.

═══════════════════════════════════════════════════════
CONTEXT: API CHOICE — IMPORTANT CONSTRAINTS
═══════════════════════════════════════════════════════

⚠️  AMADEUS WARNING: The Amadeus Self-Service developer portal is being decommissioned on July 17, 2026 (three months from now). Do not use Amadeus as the primary API. It also excludes American Airlines, Delta, British Airways, and ALL low-cost carriers (Southwest, Spirit, Frontier, Ryanair, etc.) from its self-service tier.

Use the following API stack instead:

PRIMARY: Duffel API (https://duffel.com/docs)
  - Modern GDS aggregator, production-grade
  - Free tier: 1,000 offers/month (sufficient for personal agent use)
  - Covers major airlines including low-cost carriers via Duffel Access
  - Clean REST API with clear authentication (Bearer token)
  - Env vars: DUFFEL_ACCESS_TOKEN
  - Endpoint: POST https://api.duffel.com/air/offer_requests

SUPPLEMENTAL (for price trend context): Aviasales/Travelpayouts Data API (https://support.travelpayouts.com/hc/en-us/articles/203956163)
  - Free with API token
  - Returns cheapest cached prices for a route/month (derived from aggregate search history)
  - Good for "is this price typical?" context questions
  - Env vars: TRAVELPAYOUTS_TOKEN
  - NOT for live bookable prices — for trend context only

Implement an adapter/provider pattern so the API source is swappable (in case Duffel pricing changes or agent volume grows). Each provider implements the same interface: search(origin, destination, departure_date, return_date, passengers, cabin_class, non_stop, max_results) → List[Offer].

═══════════════════════════════════════════════════════
REQUIREMENTS
═══════════════════════════════════════════════════════

1. INPUT PARAMETERS (all CLI flags, all optional except marked required)
   --origin         IATA code, e.g. LAX  [required]
   --destination    IATA code, e.g. JFK  [required]
   --date           Departure date YYYY-MM-DD  [required]
   --return-date    Return date YYYY-MM-DD (omit for one-way)
   --passengers     Integer, default 1
   --cabin          ECONOMY | PREMIUM_ECONOMY | BUSINESS | FIRST, default ECONOMY
   --non-stop       Flag: only return direct flights
   --max-results    Integer, default 10
   --sort-by        price | duration | departure_time, default price
   --currency       ISO 4217 code, default USD
   --force-refresh  Flag: bypass cache and fetch live data
   --history        Flag: show cached price history for this route instead of fetching

   Validation rules (raise clear error if violated):
   - Both IATA codes must be exactly 3 uppercase letters
   - Departure date must not be in the past (for live queries)
   - If return-date provided, it must be >= departure date + 1 day
   - passengers must be 1–9

2. OUTPUT FORMAT — EXACT JSON SCHEMA

   Success (live or cached):
   {
     "query": {
       "origin": "LAX",
       "destination": "JFK",
       "departure_date": "2026-05-15",
       "return_date": "2026-05-20",   // null if one-way
       "passengers": 1,
       "cabin": "ECONOMY",
       "non_stop": false,
       "currency": "USD",
       "queried_at": "2026-04-16T10:30:00Z"
     },
     "meta": {
       "cache_hit": false,
       "data_source": "duffel_live",  // "duffel_live" | "cache" | "unavailable"
       "cache_age_minutes": null,     // integer if cache_hit=true
       "total_results": 3,
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
           "departure_at": "2026-05-15T08:00:00-07:00",
           "arrival_at": "2026-05-15T16:30:00-04:00",
           "duration_minutes": 330,
           "stops": 0,
           "segments": [
             {
               "flight_number": "UA123",
               "carrier_iata": "UA",
               "carrier_name": "United Airlines",
               "origin": "LAX",
               "destination": "JFK",
               "departure_at": "2026-05-15T08:00:00-07:00",
               "arrival_at": "2026-05-15T16:30:00-04:00",
               "aircraft": "Boeing 737-800"
             }
           ]
         },
         "return": null,    // same schema as "outbound" if round-trip, else null
         "baggage_included": true,
         "refundable": false,
         "expires_at": "2026-04-16T11:30:00Z",  // offer expiry from API
         "fetched_at": "2026-04-16T10:30:00Z"
       }
     ],
     "price_context": {   // from Travelpayouts if available, else null
       "source": "travelpayouts_aggregate",
       "typical_minimum": 199.00,
       "typical_median": 350.00,
       "typical_maximum": 650.00,
       "current_assessment": "below_median"   // "below_median" | "median" | "above_median" | "unknown"
     }
   }

   No results:
   {
     "query": { ... },
     "meta": { "cache_hit": false, "data_source": "duffel_live", "total_results": 0, "provider": "duffel" },
     "results": [],
     "price_context": null,
     "message": "No flights found for this route and date. Try different dates or remove the non-stop filter."
   }

   Error:
   {
     "error": true,
     "code": "INVALID_AIRPORT_CODE",   // machine-readable snake_case code
     "message": "LAX1 is not a valid IATA airport code.",
     "query": { ... }
   }

3. CACHE ARCHITECTURE

   SQLite database at ~/.openclaw/workspace/skills/flight-pricing/cache.db
   Enable WAL mode (PRAGMA journal_mode=WAL) so multiple concurrent agent sessions can read without blocking each other.

   Tables:
   CREATE TABLE price_cache (
     id INTEGER PRIMARY KEY AUTOINCREMENT,
     origin TEXT NOT NULL,
     destination TEXT NOT NULL,
     departure_date TEXT NOT NULL,
     return_date TEXT,           -- NULL for one-way
     passengers INTEGER NOT NULL,
     cabin TEXT NOT NULL,
     currency TEXT NOT NULL,
     fetched_at TEXT NOT NULL,   -- ISO 8601 UTC
     provider TEXT NOT NULL,
     response_json TEXT NOT NULL -- full JSON blob of the results array
   );
   CREATE INDEX idx_route_date ON price_cache(origin, destination, departure_date, return_date, cabin, currency);

   Cache read logic:
   - For LIVE pricing queries: cache is valid for 4 hours. If cache_age > 4 hours OR --force-refresh, fetch live.
   - For --history queries: return ALL cached rows for this route (no TTL), sorted by fetched_at ASC.
   - Always write new live results to cache before returning them.

4. OPENCLAW SKILL INTEGRATION

   Deliverable: skills/flight-pricing/SKILL.md
   
   The SKILL.md must:
   - Explain concisely what the skill does (1-2 sentences)
   - Show the exact bash command to invoke it with every parameter
   - Give 3 concrete example invocations with expected output summary
   - Explain the --history flag and how to use it for price trend research
   - Note that output is JSON (tell the agent to parse it and summarize key fields)
   - Include a note about one-time setup (pip install, API token)

   Example entry in SKILL.md:
   ## flight-pricing
   Fetches current and historical flight prices for a route.
   Usage: python ~/.openclaw/workspace/skills/flight-pricing/flight_pricing.py [flags]
   Example: python ~/.openclaw/workspace/skills/flight-pricing/flight_pricing.py --origin LAX --destination JFK --date 2026-05-15 --passengers 2

5. ERROR HANDLING

   - Network timeout: retry 2x with 2s/4s exponential backoff, then fall back to cache
   - API rate limit (429): wait the Retry-After header value, then retry once
   - API auth failure: fail immediately with AUTHENTICATION_ERROR (do not retry)
   - Invalid IATA code: validate with regex [A-Z]{3} before sending to API; fail with INVALID_AIRPORT_CODE
   - Past departure date: fail immediately with PAST_DATE_ERROR
   - return_date before departure_date: fail with INVALID_DATE_RANGE
   - No results from API: return empty results array (not an error)
   - Cache write failure: log warning and continue (cache failures should not block the response)
   - All errors logged to ~/.openclaw/workspace/skills/flight-pricing/flight_pricing.log with ISO timestamp

6. TESTING REQUIREMENTS

   Tests must use mocked HTTP responses — never hit live APIs during test runs.
   Use pytest + responses (or unittest.mock) to intercept HTTP calls.

   Test cases required:
   a) One-way flight: valid query returns expected JSON structure
   b) Round-trip flight: return segments populated correctly
   c) Cache hit: second identical query within 4 hours returns cache_hit=true
   d) Cache miss: query after 4 hours fetches live and updates cache
   e) --force-refresh: bypasses cache even if fresh
   f) --history: returns all cached rows sorted by date
   g) Invalid IATA code: returns error JSON with INVALID_AIRPORT_CODE
   h) Past departure date: returns error JSON with PAST_DATE_ERROR
   i) return_date before departure_date: returns error JSON with INVALID_DATE_RANGE
   j) API network error: falls back to cache when cache exists
   k) API network error: returns unavailable error when no cache exists
   l) API returns zero results: returns empty results array with message
   m) Non-stop filter: --non-stop flag correctly filters results
   n) Cabin class: --cabin BUSINESS changes search parameters
   o) Multi-passenger: --passengers 3 multiplies price correctly
   p) SQLite WAL mode: verify PRAGMA journal_mode returns 'wal'
   q) Concurrent cache writes: two simultaneous writes don't corrupt data

   Run with: python -m pytest tests/ -v
   All tests must pass before delivery. Target >90% code coverage.

7. DOCUMENTATION (README.md)

   Required sections:
   a) What this is (2-3 sentences)
   b) Prerequisites (Python version, Node/OpenClaw version)
   c) Installation: exact copy-paste commands from zero to working
   d) API Setup: how to get Duffel token (step-by-step), Travelpayouts token (optional)
   e) Configuration: .env.example with all variables documented
   f) OpenClaw skill setup: where to put files, how to install dependencies
   g) Usage examples: 5+ examples covering one-way, round-trip, history, cabin, non-stop
   h) Output format: annotated JSON showing every field
   i) Cache: where the DB lives, how to query it directly with sqlite3, schema
   j) Historical price research: SQL examples to find cheapest day, price over time
   k) Troubleshooting: 6+ common errors with fix instructions
   l) Architecture: ASCII diagram of how the components connect
   m) Limitations: what this tool does NOT do (baggage upsells, seat selection, actual booking, low-cost carrier coverage gaps)

8. DIRECTORY STRUCTURE

   Deliver files in this layout:
   skills/
   └── flight-pricing/
       ├── SKILL.md                      ← OpenClaw skill definition
       ├── flight_pricing.py             ← Main entry point (CLI)
       ├── providers/
       │   ├── __init__.py
       │   ├── base.py                   ← Abstract provider interface
       │   ├── duffel.py                 ← Duffel API implementation
       │   └── travelpayouts.py          ← Travelpayouts supplemental
       ├── cache.py                      ← SQLite cache logic
       ├── models.py                     ← Pydantic data models
       ├── tests/
       │   ├── __init__.py
       │   ├── test_flight_pricing.py
       │   ├── test_cache.py
       │   └── fixtures/
       │       ├── duffel_response.json  ← Sample API response for mocking
       │       └── empty_response.json
       ├── examples/
       │   ├── one_way.sh
       │   ├── round_trip.sh
       │   └── history_query.sh
       ├── requirements.txt
       ├── .env.example
       └── README.md

9. QUALITY STANDARDS

   - PEP 8, max line length 100
   - All public functions have docstrings (Args, Returns, Raises)
   - Pydantic models for all API inputs and outputs (validated at system boundary)
   - No hardcoded credentials, no default test tokens, no localhost URLs
   - Logging: INFO level for normal operations, DEBUG for API request/response details
   - Performance: cache reads <100ms, live API calls should complete in <8 seconds

10. EDGE CASES TO HANDLE

    - Overnight flights (departure day != arrival day): use full ISO 8601 timestamps, never just HH:MM
    - Flights crossing the international date line: trust the API's timestamps, do not re-calculate
    - Single-character IATA codes don't exist — reject them
    - Multi-segment one-way (connecting flights): show all segments in outbound.segments
    - Provider returns price in non-USD: convert to requested currency using the rate embedded in the API response; do not call a separate FX API
    - API offer expiry: include expires_at in output; warn in log if offer expires in < 30 minutes
    - Travelpayouts returns no trend data: set price_context to null, do not error
    - DB file does not exist: create it on first run automatically

═══════════════════════════════════════════════════════
VALIDATION CHECKLIST — CONFIRM ALL BEFORE DELIVERY
═══════════════════════════════════════════════════════

☐ All pytest tests pass: `python -m pytest tests/ -v` (zero failures, zero errors)
☐ Coverage ≥ 90%: `python -m pytest --cov=. tests/`
☐ CLI one-way works: `python flight_pricing.py --origin LAX --destination JFK --date 2026-05-15`
☐ CLI round-trip works: with --return-date flag
☐ CLI history works: `python flight_pricing.py --origin LAX --destination JFK --date 2026-05-15 --history`
☐ Error outputs are valid JSON with "error": true and a machine-readable "code" field
☐ SQLite WAL mode confirmed: PRAGMA journal_mode returns 'wal'
☐ Cache persists across process restarts
☐ No hardcoded credentials anywhere in the codebase (grep check)
☐ .env.example contains all required variables with placeholder values and comments
☐ requirements.txt has all dependencies with pinned versions (pip freeze format)
☐ SKILL.md is accurate: example commands in SKILL.md are copy-paste runnable
☐ README.md has a Limitations section that honestly states what this cannot do
☐ Provider adapter pattern: adding a new provider only requires adding a file in providers/
☐ Past date input returns a clean error JSON, not a Python traceback
☐ Invalid 3-letter codes (e.g. "ZZZ") fail gracefully with INVALID_AIRPORT_CODE

PRIORITY: Correctness > Robustness > Completeness > Elegance.
A tool that correctly handles failure cases is worth more than one that has more features but crashes on edge inputs.

Do not ask clarifying questions. When a decision is ambiguous, pick the most robust option and document the decision in a comment or the README. Begin with a brief implementation plan (< 200 words), confirm it, then deliver all files.
```

---

## Research Notes: Why These Decisions Were Made

### API Choice Rationale

**Amadeus was rejected because:**
- The Self-Service portal is shutting down July 17, 2026 — building on it now is building on a deadline
- Excludes American Airlines, Delta, British Airways, and all LCCs in the self-service tier
- "Historical pricing via FlexSearch" in the original draft was incorrect: `GET /v1/analytics/itinerary-price-metrics` returns statistical percentile bands (MINIMUM/FIRST/MEDIUM/THIRD/MAXIMUM), not actual past ticket prices

**Duffel was chosen because:**
- Modern REST API, actively developed
- Covers low-cost carriers through Duffel Access
- Clean documentation, official SDKs available
- Free tier sufficient for personal agent use
- No decommission risk

**Travelpayouts was added as supplemental because:**
- Has aggregate search-history data useful for "is this a good price?" context
- Free API token
- Does not replace the cache-based historic data — it complements it with broader market context

### Why the Historical Pricing Architecture Matters

If Opus is told "retrieve historical pricing via the API," it will implement that and the tool will silently return wrong data (percentile bands presented as actual past prices). The prompt now explicitly prevents this by:
1. Explaining exactly what "historical" means in this context
2. Separating the two data types (statistical context from Travelpayouts vs accumulated cache)
3. Defining the `--history` flag behavior explicitly

### OpenClaw Skill Interface

OpenClaw's extension model (based on the actual GitHub repo at openclaw/openclaw):
- Skills live in `~/.openclaw/workspace/skills/<skill>/SKILL.md`
- Agents read SKILL.md at startup and learn what tools are available
- Agents invoke skills by running bash commands — the agent's own `bash` tool is the bridge
- No Python class registration, no gateway API, no JSON schema upload required

The correct deliverable is therefore: a bash-callable script + a SKILL.md that teaches the agent to run it.

### Cache TTL Strategy

Flight prices change constantly. A 4-hour TTL for "live" data is a reasonable balance between freshness and API quota consumption. The `--history` flag bypasses TTL and returns all accumulated rows — this is the historic pricing feature, and it's explicit, not implicit.

---

## Usage Instructions

1. Copy the prompt above (the code block under "SEND THIS PROMPT TO OPUS")
2. Paste it to Claude Opus as a single message
3. Opus will output a brief implementation plan (< 200 words) — read it for any red flags
4. Confirm, and Opus will deliver all files
5. Place files in `~/.openclaw/workspace/skills/flight-pricing/`
6. Create a `.env` from `.env.example`, add your Duffel token
7. Run `pip install -r requirements.txt`
8. Run `python -m pytest tests/ -v` to verify
9. The skill is now available to your OpenClaw agents

---

## Open Questions (Answer Before Sending)

- [ ] Do you want the cache in a custom location, or is `~/.openclaw/workspace/skills/flight-pricing/cache.db` correct for your setup?
- [ ] Do you need the Travelpayouts price context feature, or is live pricing + local cache sufficient?
- [ ] Any specific channels (WhatsApp, Telegram, Discord) where agents will use this most? May affect how SKILL.md is written.

---

**Created**: April 16, 2026  
**Revised**: April 16, 2026 (corrected Amadeus decommission, API coverage gaps, historical pricing architecture, OpenClaw interface, JSON schema, cache TTL, SQLite WAL mode, input parameters, date validation)  
**Status**: Ready to send to Opus
