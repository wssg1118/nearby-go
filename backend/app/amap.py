from __future__ import annotations

from typing import Any

import httpx


class AmapError(RuntimeError):
    def __init__(self, message: str, *, operation: str = "", code: str = "") -> None:
        super().__init__(message)
        self.operation = operation
        self.code = code

    @property
    def public_detail(self) -> str:
        suffix = f"（错误码 {self.code}）" if self.code else ""
        return f"{self.args[0]}{suffix}"


MAX_ROUTE_POLYLINE_POINTS = 60


def compact_polyline(polyline: str, limit: int = MAX_ROUTE_POLYLINE_POINTS) -> str:
    """Evenly downsample a "lng,lat;lng,lat" polyline so payloads stay small."""
    points = [item for item in polyline.split(";") if item]
    if len(points) <= limit:
        return polyline
    step = (len(points) - 1) / (limit - 1)
    return ";".join(points[round(index * step)] for index in range(limit))


class AmapClient:
    base_url = "https://restapi.amap.com"

    def __init__(self, http: httpx.AsyncClient, api_key: str):
        self.http = http
        self.api_key = api_key

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise AmapError("AMAP_WEB_SERVICE_KEY 尚未配置", operation=path)
        try:
            response = await self.http.get(
                f"{self.base_url}{path}",
                params={**params, "key": self.api_key, "output": "JSON"},
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise AmapError(
                "高德服务暂时不可用",
                operation=path,
                code=f"HTTP_{exc.response.status_code}",
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise AmapError("高德服务网络或响应异常", operation=path) from exc
        if str(payload.get("status")) != "1":
            raise AmapError(
                str(payload.get("info") or "高德 API 调用失败"),
                operation=path,
                code=str(payload.get("infocode") or ""),
            )
        return payload

    async def convert_location(
        self, longitude: float, latitude: float, coordinate_system: str
    ) -> tuple[float, float]:
        if coordinate_system == "autonavi":
            return longitude, latitude
        payload = await self._get(
            "/v3/assistant/coordinate/convert",
            {
                "locations": f"{longitude:.6f},{latitude:.6f}",
                "coordsys": coordinate_system,
            },
        )
        converted = str(payload["locations"]).split(";")[0]
        lng, lat = converted.split(",")
        return float(lng), float(lat)

    async def search_around(
        self,
        longitude: float,
        latitude: float,
        radius_meters: int,
        types: list[str],
        keywords: list[str],
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        payload = await self._get(
            "/v3/place/around",
            {
                "location": f"{longitude:.6f},{latitude:.6f}",
                "radius": radius_meters,
                "types": "|".join(types),
                "keywords": "|".join(keywords),
                "sortrule": "weight",
                "offset": min(limit, 25),
                "page": 1,
                "extensions": "all",
            },
        )
        return list(payload.get("pois") or [])

    async def route(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        mode: str,
    ) -> dict[str, int | str] | None:
        path = "/v3/direction/driving" if mode == "driving" else "/v3/direction/walking"
        payload = await self._get(
            path,
            {
                "origin": f"{origin[0]:.6f},{origin[1]:.6f}",
                "destination": f"{destination[0]:.6f},{destination[1]:.6f}",
            },
        )
        route = payload.get("route") or {}
        paths = route.get("paths") or []
        if not paths:
            raise AmapError("未返回可用路线", operation=path, code="NO_ROUTE")
        first = paths[0]
        steps = first.get("steps") or []
        polyline = compact_polyline(
            ";".join(str(step.get("polyline") or "") for step in steps)
        )
        return {
            "distance": int(float(first.get("distance") or 0)),
            "duration": int(float(first.get("duration") or 0)),
            "polyline": polyline,
        }
