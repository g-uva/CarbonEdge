"""
Wattnet carbon intensity (CI) fetcher.

Uses the /footprints endpoint to retrieve *time series* of carbon footprints,
flattened into timestamp/value pairs.

Granularity is whatever Wattnet provides (typically 15 minutes), which matches
the service design in the OpenAPI spec.

Requirements:
    pip install requests pandas python-dotenv

Environment variables:
    WATTNET_BASE_URL  -> e.g. https://wattnet.ifca.es/v1
    WATTNET_API       -> OPTIONAL, bearer token or API key

Usage example (sanity test):
    from datetime import datetime, timezone
    client = WattnetClient()
    pts = client.fetch_range_coords(
        lat=42.3757,
        lon=-72.5199,
        start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end=datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
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

LOGGER = logging.getLogger(__name__)

# Base URL *must* include the /v1 prefix, as per the OpenAPI `servers: [{url: "/v1"}]`.
WATTNET_BASE_URL = os.getenv("WATTNET_BASE_URL", "https://api.wattnet.eu/")
WATTNET_TOKEN_ENV = "WATTNET_TOKEN"

REQUEST_DELAY_SECONDS = 0.2  # throttle between chunked calls


@dataclass
class WattnetCIPoint:
    """
    Flattened datapoint from a Wattnet Footprint time series.

    For CI, this is carbon gCO2/kWh over some interval.
    """
    timestamp: datetime
    carbon_intensity: float
    footprint_type: str
    scope: str
    zone: str
    unit: str
    coverage: str
    valid: bool
    zone_status: str

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "carbon_intensity_gco2_per_kwh": self.carbon_intensity,
            "footprint_type": self.footprint_type,
            "scope": self.scope,
            "zone": self.zone,
            "unit": self.unit,
            "coverage": self.coverage,
            "valid": self.valid,
            "zone_status": self.zone_status,
        }


class WattnetClient:
    """
    Thin client for the Wattnet /footprints endpoint.

    Main entrypoints:
        - fetch_range_coords(...)
        - fetch_year_coords(...)
    """

    def __init__(self, base_url: Optional[str] = None, token: Optional[str] = None,
                 session: Optional[requests.Session] = None) -> None:
        self.base_url = base_url or WATTNET_BASE_URL
        if not self.base_url:
            raise RuntimeError(
                "WATTNET_BASE_URL is not set. "
                "Set it to something like 'https://wattnet.example.org/v1'."
            )
    
        self.token = token or os.getenv(WATTNET_TOKEN_ENV)
        print("SELF TOKEN", self.token)
        self.session = session or requests.Session()

        # Optional auth – OpenAPI does not require it, but your deployment might.
        if self.token:
            self.session.headers.update({"Authorization": f"Bearer {self.token}"})
        else:
            LOGGER.info(
                "WATTNET_API not set; proceeding without Authorization header."
            )

        self.logger = logging.getLogger(self.__class__.__name__)

    def _build_url(self, path: str) -> str:
        return self.base_url.rstrip("/") + "/" + path.lstrip("/")

    @staticmethod
    def _ensure_utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def _parse_footprints_response(self, data) -> List[WattnetCIPoint]:
        """
        Parse a list[Footprint] JSON into flat WattnetCIPoint objects.

        OpenAPI `Footprint` schema:
          - footprint_type, scope, zone, unit, coverage, series[]
          - series[].values is list of [timestamp, value] pairs
        """
        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected Wattnet response (not a list): {data}")

        results: List[WattnetCIPoint] = []

        for fp in data:
            # Expect a non-aggregated Footprint (series-based).
            footprint_type = fp.get("footprint_type")
            scope = fp.get("scope")
            zone = fp.get("zone")
            unit = fp.get("unit")
            coverage = fp.get("coverage")
            series_list = fp.get("series") or []

            for series in series_list:
                valid = bool(series.get("valid"))
                zone_status = series.get("zone_status")
                values = series.get("values") or []

                # Each value is [timestamp, numeric_value]
                for item in values:
                    if not isinstance(item, list) or len(item) != 2:
                        continue
                    ts_raw, value = item
                    if ts_raw is None or value is None:
                        continue

                    ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))

                    results.append(
                        WattnetCIPoint(
                            timestamp=ts,
                            carbon_intensity=float(value),
                            footprint_type=str(footprint_type),
                            scope=str(scope),
                            zone=str(zone),
                            unit=str(unit),
                            coverage=str(coverage),
                            valid=valid,
                            zone_status=str(zone_status),
                        )
                    )

        return results

    def fetch_range_coords(
        self,
        lat: float,
        lon: float,
        start: datetime,
        end: datetime,
        footprint_type: str = "carbon",
        scope: str = "life-cycle",
        aggregate: bool = False,
        use_global: bool = True,
    ) -> List[WattnetCIPoint]:
        """
        Fetch time series footprints for a given coordinate range.

        Returns a flat list of WattnetCIPoint.
        """
        start = self._ensure_utc(start)
        end = self._ensure_utc(end)

        url = self._build_url("/footprints")
        params = {
            "lat": lat,
            "lon": lon,
            "footprint_type": footprint_type,
            "scope": scope,
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
            "aggregate": str(aggregate).lower(),  # FastAPI parses "true"/"false"
            "use_global": str(use_global).lower(),
        }

        self.logger.debug("Requesting Wattnet footprints: %s %s", url, params)
        resp = self.session.get(url, params=params, timeout=30)

        # Log some debug info so we don't go blind next time
        self.logger.debug(
            "Wattnet response: status=%s content_type=%s length=%s",
            resp.status_code,
            resp.headers.get("Content-Type"),
            len(resp.content or b""),
        )

        # No data at all (e.g. 204 No Content or empty body): just return []
        if resp.status_code == 204 or not resp.text.strip():
            self.logger.warning(
                "Empty Wattnet response for lat=%.4f lon=%.4f (%s -> %s); "
                "returning no points.",
                lat,
                lon,
                start.isoformat(),
                end.isoformat(),
            )
            return []

        # Non-2xx with some body: blow up with a helpful message
        if not resp.ok:
            raise RuntimeError(
                f"Wattnet API error {resp.status_code}: {resp.text}"
            )

        # Try to parse JSON, but fail loudly if the server gives nonsense
        try:
            data = resp.json()
        except ValueError as e:
            raise RuntimeError(
                f"Failed to decode JSON from Wattnet (status={resp.status_code}). "
                f"Body was: {resp.text!r}"
            ) from e

        return self._parse_footprints_response(data)

    def fetch_range_zone(
        self,
        zone: str,
        start: datetime,
        end: datetime,
        footprint_type: str = "carbon",
        scope: str = "life-cycle",
        aggregate: bool = False,
        use_global: bool = True,
    ) -> List[WattnetCIPoint]:
        """
        Same as fetch_range_coords, but explicitly targeting a `zone` instead of lat/lon.
        """
        start = self._ensure_utc(start)
        end = self._ensure_utc(end)

        url = self._build_url("/footprints")
        params = {
            "zone": zone,
            "footprint_type": footprint_type,
            "scope": scope,
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
            "aggregate": str(aggregate).lower(),
            "use_global": str(use_global).lower(),
        }

        self.logger.debug("Requesting Wattnet footprints (zone): %s %s", url, params)
        resp = self.session.get(url, params=params, timeout=30)

        if not resp.ok:
            raise RuntimeError(
                f"Wattnet API error {resp.status_code}: {resp.text}"
            )

        data = resp.json()
        return self._parse_footprints_response(data)

    def fetch_year_coords(
        self,
        lat: float,
        lon: float,
        year: int,
        footprint_type: str = "carbon",
        scope: str = "life-cycle",
        aggregate: bool = False,
        use_global: bool = True,
        max_days_per_call: int = 30,
    ) -> List[WattnetCIPoint]:
        """
        Fetch a full year's CI for given coordinates, chunked into windows of
        at most `max_days_per_call` days.

        Returns a list of WattnetCIPoint sorted by timestamp, deduplicated.
        """
        start_year = datetime(year, 1, 1, tzinfo=timezone.utc)
        end_year = datetime(year + 1, 1, 1, tzinfo=timezone.utc)

        all_points: Dict[datetime, WattnetCIPoint] = {}

        cur_start = start_year
        delta = timedelta(days=max_days_per_call)

        while cur_start < end_year:
            cur_end = min(cur_start + delta, end_year)
            self.logger.info("Fetching Wattnet slice %s -> %s", cur_start, cur_end)

            slice_points = self.fetch_range_coords(
                lat=lat,
                lon=lon,
                start=cur_start,
                end=cur_end,
                footprint_type=footprint_type,
                scope=scope,
                aggregate=aggregate,
                use_global=use_global,
            )

            for p in slice_points:
                all_points[p.timestamp] = p

            cur_start = cur_end
            time.sleep(REQUEST_DELAY_SECONDS)

        points_sorted = sorted(all_points.values(), key=lambda p: p.timestamp)
        return points_sorted


def wattnet_ci_to_dataframe(points: Iterable[WattnetCIPoint]) -> pd.DataFrame:
    """
    Convert a collection of WattnetCIPoint into a pandas DataFrame.
    """
    rows = [p.to_dict() for p in points]
    df = pd.DataFrame(rows)
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def save_wattnet_ci_to_csv(points: Iterable[WattnetCIPoint], path: str) -> None:
    """
    Save CI time series to CSV.
    """
    df = wattnet_ci_to_dataframe(points)
    df.to_csv(path, index=False)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    from datetime import datetime, timezone

    client = WattnetClient()
    # Tiny sanity check window (change coords to a valid zone, or you'll get 404)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 1, 2, tzinfo=timezone.utc)
    pts = client.fetch_range_coords(42.3757, -72.5199, start, end)
    print(f"Fetched {len(pts)} Wattnet points for sanity check.")
