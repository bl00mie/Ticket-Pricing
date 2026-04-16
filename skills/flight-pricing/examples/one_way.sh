#!/usr/bin/env bash
# One-way flight search: LAX → JFK on a future date
python ~/.openclaw/workspace/skills/flight-pricing/flight_pricing.py \
  --origin LAX \
  --destination JFK \
  --date 2026-05-15 \
  --max-results 5
