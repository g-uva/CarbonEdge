"""
Script to download hourly CI for 2024 at location 42.3757, -72.5199
using the Electricity Maps API and the helper module `em_ci_fetcher`.

Outputs a CSV:
    ci_2024_42.3757_-72.5199.csv
"""

from __future__ import annotations

import logging
from pathlib import Path

from datetime import datetime, timezone

from em_ci_fetcher import ElectricityMapsClient, save_ci_to_csv


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    lat = 42.3757
    lon = -72.5199
    year = 2024

    out_path = Path(f"ci_{year}_{lat}_{lon}.csv")
    logging.info("Target location: lat=%.4f lon=%.4f year=%d", lat, lon, year)
    logging.info("Output file: %s", out_path)

    client = ElectricityMapsClient()
    points = client.fetch_year_hourly(lat=lat, lon=lon, year=year)

    logging.info("Fetched %d hourly CI points.", len(points))
    save_ci_to_csv(points, out_path.as_posix())
    logging.info("Saved CSV to %s", out_path)


if __name__ == "__main__":
    main()
