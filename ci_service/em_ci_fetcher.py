"""
Electricity Maps carbon intensity fetcher.

Grabs hourly carbon intensity (gCO2eq/kWh) using the /v3/carbon-intensity/past-range
endpoint, chunked into <=10-day windows (API limit) for a full year.

Requirements:
    pip install requests pandas

Env:
    ELECTRICITYMAPS_API_TOKEN
"""

from __future__ import annotations

import os
import time
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Iterable, Optional

import requests
import pandas as pd

BASE_URL = "https://api.electricitymap.org/v3/carbon-intensity/past-range"
API_TOKEN_ENV = "ELECTRICITYMAPS_API_TOKEN"

# Be nice to the API, even if humans rarely are nice to anything.
REQUEST_DELAY_SECONDS = 0.2  # adjust depending on your rate limit


@dataclass
class CIPoint:
    timestamp: datetime
    carbon_intensity: float
    zone: Optional[str] = None
    is_estimated: Optional[bool] = None
    estimation_method: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "carbon_intensity_gco2_per_kwh": self.carbon_intensity,
            "zone": self.zone,
            "is_estimated": self.is_estimated,
            "estimation_method": self.estimation_method,
        }

class ElectricityMapsClient:
    def __init__(self, api_token: Optional[str] = None, session: Optional[requests.Session] = None):
        # Priority: explicit arg > EM_API > ELECTRICITYMAPS_API_TOKEN
        if api_token is None:
            api_token = (
                os.getenv("EM_API_KEY") or
                os.getenv(API_TOKEN_ENV)  # ELECTRICITYMAPS_API_TOKEN
            )
        if not api_token:
            raise RuntimeError(
                "No API token provided. Set EM_API or ELECTRICITYMAPS_API_TOKEN in the environment."
            )

        self.api_token = api_token
        self.session = session or requests.Session()
        self.session.headers.update({"auth-token": self.api_token})
        self.logger = logging.getLogger(self.__class__.__name__)

    def fetch_past_range(
        self,
        lat: float,
        lon: float,
        start: datetime,
        end: datetime,
    ) -> List[CIPoint]:
        """
        Fetch carbon intensity for [start, end) at hourly resolution.

        Electricity Maps past-range default granularity: 1 hour.
        The API typically limits the time range to ~10 days per call.
        """
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)

        params = {
            "lat": lat,
            "lon": lon,
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
        }

        self.logger.debug("Requesting CI: %s", params)
        response = self.session.get(BASE_URL, params=params, timeout=30)
        if not response.ok:
            raise RuntimeError(
                f"Electricity Maps API error {response.status_code}: {response.text}"
            )

        data = response.json()
        points_raw = data.get("carbonIntensity", []) or data.get("history", [])
        if not isinstance(points_raw, list):
            raise RuntimeError(f"Unexpected API response format: {data}")

        results: List[CIPoint] = []
        for item in points_raw:
            # Typical fields: datetime, carbonIntensity, zone, isEstimated, estimationMethod
            ts_raw = item.get("datetime") or item.get("timestamp")
            ci = item.get("carbonIntensity")
            if ts_raw is None or ci is None:
                continue

            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            results.append(
                CIPoint(
                    timestamp=ts,
                    carbon_intensity=float(ci),
                    zone=item.get("zone"),
                    is_estimated=item.get("isEstimated"),
                    estimation_method=item.get("estimationMethod"),
                )
            )

        return results

    def fetch_year_hourly(
        self,
        lat: float,
        lon: float,
        year: int,
        max_days_per_call: int = 10,
    ) -> List[CIPoint]:
        """
        Fetch a full year's hourly CI, chunked into <= max_days_per_call windows.

        Returns a list of CIPoint sorted by timestamp with duplicates removed.
        """
        start_year = datetime(year, 1, 1, tzinfo=timezone.utc)
        end_year = datetime(year + 1, 1, 1, tzinfo=timezone.utc)

        all_points: Dict[datetime, CIPoint] = {}

        cur_start = start_year
        delta = timedelta(days=max_days_per_call)

        while cur_start < end_year:
            cur_end = min(cur_start + delta, end_year)
            self.logger.info("Fetching CI slice %s -> %s", cur_start, cur_end)

            slice_points = self.fetch_past_range(lat, lon, cur_start, cur_end)
            for p in slice_points:
                # overwrite if duplicate timestamp
                all_points[p.timestamp] = p

            cur_start = cur_end
            time.sleep(REQUEST_DELAY_SECONDS)

        points_sorted = sorted(all_points.values(), key=lambda p: p.timestamp)
        return points_sorted


def ci_points_to_dataframe(points: Iterable[CIPoint]) -> pd.DataFrame:
    """
    Convert list of CIPoint to a pandas DataFrame.
    """
    rows = [p.to_dict() for p in points]
    df = pd.DataFrame(rows)
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def save_ci_to_csv(points: Iterable[CIPoint], path: str) -> None:
    """
    Save CIPoint list to CSV.
    """
    df = ci_points_to_dataframe(points)
    df.to_csv(path, index=False)


if __name__ == "__main__":
    # Minimal "it runs" check, but not doing a full year here.
    logging.basicConfig(level=logging.INFO)
    client = ElectricityMapsClient()
    lat, lon = 42.3757, -72.5199

    # Example: first 2 days of 2024
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 1, 3, tzinfo=timezone.utc)
    pts = client.fetch_past_range(lat, lon, start, end)
    print(f"Fetched {len(pts)} points for sanity check.")
