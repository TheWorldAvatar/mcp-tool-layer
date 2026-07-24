"""Warm carpark geocode cache for near-CREATE labels.

Usage:
  python -m mini_marie.zaha.sg_old.warm_carpark_geocode
  python -m mini_marie.zaha.sg_old.warm_carpark_geocode --live
"""

from __future__ import annotations

import argparse
import json

from mini_marie.zaha.sg_old import label_store as ls


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Also call Nominatim (slow; rate-limited)")
    args = parser.parse_args()
    stats = ls.warm_carpark_geocodes_near_create(live_geocode=args.live)
    nearest = ls.find_nearest_carpark_to_create(limit=8)
    print(json.dumps({"warm": stats, "nearest": nearest}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
