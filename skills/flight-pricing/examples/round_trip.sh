#!/usr/bin/env bash
# Round-trip flight search: SFO → LHR, business class, 2 passengers
python ~/.openclaw/workspace/skills/flight-pricing/flight_pricing.py \
  --origin SFO \
  --destination LHR \
  --date 2026-06-01 \
  --return-date 2026-06-15 \
  --cabin BUSINESS \
  --passengers 2 \
  --sort-by price
