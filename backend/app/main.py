from __future__ import annotations

import json
import hashlib
import hmac
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlparse

import httpx
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .amap import AmapClient, AmapError
from .config import Settings, get_settings
from .dify import DifyError, stream_chat, transcribe_audio
from .models import ChatRequest, RecommendationRequest, RecommendationResponse
from .recommendations import build_recommendations


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.http = httpx.AsyncClient(
        timeout=settings.request_timeout_seconds,
        trust_env=False,
    )
    yield
    await app.state.http.aclose()


app = FastAPI(title="NearbyGo", version="0.1.0", lifespan=lifespan)
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Internal-Token", "X-NearbyGo-User"],
)


_POINTS_PATTERN = re.compile(r"^-?\d{1,3}(?:\.\d{1,6})?,-?\d{1,2}(?:\.\d{1,6})?(?:;-?\d{1,3}(?:\.\d{1,6})?,-?\d{1,2}(?:\.\d{1,6})?){0,89}$")
ROUTE_MAP_MAX_POINTS = 90


def _parse_polyline(polyline: str) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for pair in polyline.split(";"):
        try:
            lng_text, lat_text = pair.split(",", maxsplit=1)
            lng, lat = float(lng_text), float(lat_text)
        except ValueError:
            continue
        if -180 <= lng <= 180 and -90 <= lat <= 90:
            points.append((lng, lat))
    return points


def _downsample(
    points: list[tuple[float, float]], cap: int
) -> list[tuple[float, float]]:
    if len(points) <= cap:
        return points
    step = (len(points) - 1) / (cap - 1)
    return [points[round(index * step)] for index in range(cap)]


def _verified_route_points(
    serialized_points: str, signature: str, secret: str
) -> list[tuple[float, float]]:
    if not secret or not _POINTS_PATTERN.fullmatch(serialized_points):
        raise HTTPException(status_code=400, detail="无效的地图路线")
    expected = hmac.new(
        secret.encode("utf-8"), serialized_points.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=403, detail="地图签名校验失败")
    points = []
    for pair in serialized_points.split(";"):
        longitude, latitude = (float(value) for value in pair.split(","))
        if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
            raise HTTPException(status_code=400, detail="无效的地图坐标")
        points.append((longitude, latitude))
    return points


def _serialize_points(points: list[tuple[float, float]]) -> str:
    return ";".join(f"{lng:.6f},{lat:.6f}" for lng, lat in points)


def _sign_payload(value: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def _route_map_path(payload: RecommendationResponse, secret: str) -> str | None:
    if not secret or not payload.places:
        return None
    origin = (float(payload.origin["longitude"]), float(payload.origin["latitude"]))
    legs = list(zip(payload.places[:9], payload.itinerary[:9]))
    leg_cap = max(4, ROUTE_MAP_MAX_POINTS // max(1, len(legs)))
    # 优先使用每段高德实际路线的 polyline；未取得路线的路段退回直线示意。
    coordinates = [origin]
    current = origin
    markers = [origin]
    for place, segment in legs:
        end = (place.longitude, place.latitude)
        leg = _parse_polyline(segment.route_polyline or "")
        leg = _downsample(leg, leg_cap) if len(leg) >= 2 else [current, end]
        coordinates.extend(leg[1:])
        markers.append(end)
        current = leg[-1]
    if len(coordinates) < 2:
        return None
    # 高德静态地图 markers 上限 10 个：只标注起点与推荐地点，polyline 点仅进 paths。
    markers = markers[:10]
    serialized_points = _serialize_points(coordinates)
    serialized_markers = _serialize_points(markers)
    return "/api/route-map?" + urlencode(
        {
            "points": serialized_points,
            "sig": _sign_payload(serialized_points, secret),
            "markers": serialized_markers,
            "msig": _sign_payload(serialized_markers, secret),
        }
    )


def _safe_markdown_text(value: object, limit: int = 80) -> str:
    return re.sub(r"[\[\]()`<>\r\n]+", " ", str(value or "")).strip()[:limit]


def _travel_cards(raw_payload: str, public_base_url: str) -> str:
    """Build deterministic map/photo cards without trusting POI text as Markdown."""
    try:
        payload = json.loads(raw_payload)
    except (TypeError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    blocks: list[str] = []
    route_path = str(payload.get("route_map_path") or "")
    if route_path.startswith("/api/route-map?"):
        map_url = urljoin(public_base_url.rstrip("/") + "/", route_path.lstrip("/"))
        if urlparse(map_url).scheme == "https":
            blocks.extend(
                [
                    "## 高德位置概览",
                    f"![map:附近候选与实际路线示意]({map_url})",
                    "> 地图已按高德实际路线绘制；未取得路线的路段以直线示意，导航请以高德 App 实时路线为准。",
                ]
            )

    transport = "🚗 驾车" if payload.get("transport") == "driving" else "🚶 步行"
    itinerary = payload.get("itinerary") if isinstance(payload.get("itinerary"), list) else []
    if itinerary:
        summaries = []
        for segment in itinerary[:6]:
            if not isinstance(segment, dict):
                continue
            start = _safe_markdown_text(segment.get("from_name"), 30)
            end = _safe_markdown_text(segment.get("to_name"), 30)
            minutes = segment.get("route_duration_minutes") or segment.get("planning_duration_minutes")
            distance = segment.get("route_distance_meters")
            detail = f"{minutes} 分钟" if isinstance(minutes, (int, float)) else "耗时待确认"
            if isinstance(distance, (int, float)):
                detail += f"、{round(distance)} 米"
            summaries.append(f"- {transport}：{start} → {end}（{detail}）")
        if summaries:
            blocks.extend(["### 路线摘要", *summaries])

    additional = (
        payload.get("additional_places")
        if isinstance(payload.get("additional_places"), list)
        else []
    )
    one_liners: list[str] = []
    for place in additional:
        if not isinstance(place, dict):
            continue
        name = _safe_markdown_text(place.get("name"), 30) or "附近地点"
        category = str(place.get("category") or "").split(";")[-1].strip()
        details = [category] if category else []
        distance = place.get("straight_distance_meters")
        if isinstance(distance, (int, float)):
            details.append(f"直线约 {round(distance)} 米")
        rating = place.get("rating")
        if isinstance(rating, (int, float)):
            details.append(f"评分 {rating}")
        cost = place.get("cost_per_person")
        if isinstance(cost, (int, float)):
            details.append(f"人均 {round(cost)} 元")
        url = str(place.get("navigation_url") or "")
        line = f"- **{name}**（{' · '.join(details)}）"
        if url.startswith("https://uri.amap.com/"):
            line += f" — [高德导航]({url})"
        one_liners.append(line)
    if one_liners:
        blocks.extend(["### 其他候选", *one_liners])

    places = payload.get("places") if isinstance(payload.get("places"), list) else []
    photos: list[str] = []
    for index, place in enumerate(places, start=1):
        if not isinstance(place, dict):
            continue
        name = _safe_markdown_text(place.get("name"), 30) or "附近地点"
        image_urls = place.get("image_urls") if isinstance(place.get("image_urls"), list) else []
        if not image_urls:
            continue
        raw_url = str(image_urls[0])
        try:
            parsed = urlparse(raw_url)
        except ValueError:
            continue
        if parsed.scheme == "https" and parsed.netloc:
            photos.append(f"![{index}·{name}]({parsed.geturl()})")
    if photos:
        blocks.extend(["### 推荐地点图片", *photos])
    return "\n\n".join(blocks)


def verify_internal_token(
    x_internal_token: str = Header(default=""),
    config: Settings = Depends(get_settings),
) -> None:
    if not config.internal_api_token or x_internal_token != config.internal_api_token:
        raise HTTPException(status_code=401, detail="无效的内部调用凭据")


@app.get("/api/health")
async def health(config: Settings = Depends(get_settings)) -> dict[str, object]:
    return {
        "status": "ok",
        "configured": {
            "dify": bool(config.dify_api_key),
            "amap": bool(config.amap_web_service_key),
            "internal_token": bool(config.internal_api_token),
        },
    }


@app.post("/api/chat")
async def chat(
    payload: ChatRequest,
    request: Request,
    config: Settings = Depends(get_settings),
) -> StreamingResponse:
    async def generate():
        try:
            async for chunk in stream_chat(payload, request.app.state.http, config):
                yield chunk
        except DifyError as exc:
            error_event = json.dumps(
                {"event": "error", "message": str(exc)}, ensure_ascii=False
            )
            yield f"data: {error_event}\n\n".encode()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/audio-to-text")
async def audio_to_text(
    request: Request,
    audio: UploadFile = File(...),
    user: str = Header(default="", alias="X-NearbyGo-User"),
    config: Settings = Depends(get_settings),
) -> dict[str, str]:
    if not user or len(user) > 128:
        raise HTTPException(status_code=400, detail="无效的用户标识")
    allowed_types = {
        "audio/mpeg",
        "audio/mp3",
        "audio/mp4",
        "audio/m4a",
        "audio/wav",
        "audio/x-wav",
        "audio/webm",
        "video/webm",
    }
    content_type = (audio.content_type or "application/octet-stream").split(";", 1)[0].lower()
    if content_type not in allowed_types:
        raise HTTPException(status_code=415, detail="不支持的录音格式")
    content = await audio.read(15 * 1024 * 1024 + 1)
    if not content or len(content) > 15 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="录音为空或超过 15MB")
    try:
        text = await transcribe_audio(
            content=content,
            filename=Path(audio.filename or "voice.webm").name,
            content_type=content_type,
            user=user,
            http=request.app.state.http,
            settings=config,
        )
    except DifyError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"text": text}


_PLACE_SEARCH_LIMIT = 30
_PLACE_SEARCH_WINDOW_SECONDS = 60.0
_place_search_hits: dict[str, list[float]] = {}


def _check_place_search_rate(user: str) -> None:
    now = time.monotonic()
    hits = [t for t in _place_search_hits.get(user, []) if now - t < _PLACE_SEARCH_WINDOW_SECONDS]
    if len(hits) >= _PLACE_SEARCH_LIMIT:
        raise HTTPException(status_code=429, detail="搜索太频繁，请稍后再试")
    hits.append(now)
    _place_search_hits[user] = hits


@app.get("/api/place-search")
async def place_search(
    query: str,
    request: Request,
    user: str = Header(default="", alias="X-NearbyGo-User"),
    config: Settings = Depends(get_settings),
) -> dict[str, list[dict[str, object]]]:
    if not user or len(user) > 128:
        raise HTTPException(status_code=400, detail="无效的用户标识")
    keyword = query.strip()[:50]
    if not keyword:
        raise HTTPException(status_code=400, detail="请输入地点关键词")
    if not config.amap_web_service_key:
        raise HTTPException(status_code=503, detail="高德地图尚未配置")
    _check_place_search_rate(user)
    amap = AmapClient(request.app.state.http, config.amap_web_service_key)
    try:
        payload = await amap._get(
            "/v3/assistant/inputtips", {"keywords": keyword, "datatype": "poi"}
        )
    except AmapError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    tips: list[dict[str, object]] = []
    for tip in (payload.get("tips") or [])[:8]:
        location = str(tip.get("location") or "")
        if location.count(",") != 1:
            continue
        try:
            lng_text, lat_text = location.split(",", maxsplit=1)
            longitude, latitude = float(lng_text), float(lat_text)
        except ValueError:
            continue
        name = str(tip.get("name") or "").strip()
        if not name:
            continue
        district = str(tip.get("district") or "").strip()
        address = str(tip.get("address") or "").strip()
        tips.append(
            {
                "name": name[:60],
                "district": district[:40],
                "address": (address or district)[:80],
                "longitude": round(longitude, 6),
                "latitude": round(latitude, 6),
            }
        )
    return {"tips": tips}


@app.get("/api/route-map")
async def route_map(
    points: str,
    sig: str,
    request: Request,
    markers: str = "",
    msig: str = "",
    config: Settings = Depends(get_settings),
) -> Response:
    verified = _verified_route_points(points, sig, config.internal_api_token)
    if not config.amap_web_service_key:
        raise HTTPException(status_code=503, detail="高德地图尚未配置")
    # 高德静态地图 markers 上限 10 个；未传 markers 时仅标注起点。
    if markers and msig:
        marker_points = _verified_route_points(markers, msig, config.internal_api_token)[:10]
    else:
        marker_points = verified[:1]
    marker_groups = []
    for index, (longitude, latitude) in enumerate(marker_points):
        if index == 0:
            marker_groups.append(f"mid,0x14532D,A:{longitude:.6f},{latitude:.6f}")
        else:
            marker_groups.append(f"mid,0xFC6054,{index}:{longitude:.6f},{latitude:.6f}")
    params = {
        "key": config.amap_web_service_key,
        "size": "750*420",
        "scale": 2,
        "markers": "|".join(marker_groups),
        "paths": "5,0x14532D,0.75,,:" + ";".join(
            f"{longitude:.6f},{latitude:.6f}" for longitude, latitude in verified
        ),
    }
    try:
        upstream = await request.app.state.http.get(
            "https://restapi.amap.com/v3/staticmap", params=params
        )
        upstream.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="地图图片暂时不可用") from exc
    media_type = upstream.headers.get("content-type", "image/png").split(";", 1)[0]
    if not media_type.startswith("image/"):
        raise HTTPException(status_code=502, detail="地图服务返回异常")
    return Response(
        content=upstream.content,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.post(
    "/api/recommendations",
    response_model=RecommendationResponse,
    dependencies=[Depends(verify_internal_token)],
)
async def recommendations(
    payload: RecommendationRequest,
    request: Request,
    config: Settings = Depends(get_settings),
) -> RecommendationResponse:
    try:
        amap = AmapClient(request.app.state.http, config.amap_web_service_key)
        result = await build_recommendations(payload, amap)
        result.route_map_path = _route_map_path(result, config.internal_api_token)
        return result
    except AmapError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
