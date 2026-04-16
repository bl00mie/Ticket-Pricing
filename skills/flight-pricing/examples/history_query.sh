#!/usr/bin/env bash
# Show cached price history for LAX → JFK on a specific date
# This returns ALL previously-fetched price snapshots, sorted chronologically.
# Run live queries periodically to build up a meaningful history.
python ~/.openclaw/workspace/skills/flight-pricing/flight_pricing.py \
  --origin LAX \
  --destination JFK \
  --date 2026-05-15 \
  --history
