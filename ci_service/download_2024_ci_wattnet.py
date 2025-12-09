"""
Download Wattnet carbon intensity (CI) for the full year 2024 at a given location.

Location:
    lat = 42.3757
    lon = -72.5199

Output:
    wattnet_ci_2024_42.3757_-72.5199.csv
"""

from __future__ import annotations

import logging
from pathlib import Path
from datetime import datetime, timezone

from wattnet_ci_fetcher import WattnetClient, save_wattnet_ci_to_csv


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Amsterdam coordinates
    lat = 52.3730
    lon = 4.8924
    year = 2024

    # Use same naming convention as EM script for your sanity
    out_path = Path(f"wattnet_ci_{year}_{lat}_{lon}.csv")

    logging.info("Target location: lat=%.4f lon=%.4f year=%d", lat, lon, year)
    logging.info("Output file: %s", out_path)

    client = WattnetClient()

    # The paper uses carbon intensity; here we explicitly request footprint_type="carbon".
    # Scope can be "life-cycle" (default) or "operational" depending on what you need.
    points = client.fetch_year_coords(
        lat=lat,
        lon=lon,
        year=year,
        footprint_type="carbon",
        scope="life-cycle",
        aggregate=False,
        use_global=True,
        max_days_per_call=30,
    )

    logging.info("Fetched %d Wattnet CI points.", len(points))
    save_wattnet_ci_to_csv(points, out_path.as_posix())
    logging.info("Saved CSV to %s", out_path)


if __name__ == "__main__":
    main()
