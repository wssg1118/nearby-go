import hashlib
import hmac
import json
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException

from app.main import _route_map_path, _travel_cards, _verified_route_points
from app.models import (
    ItinerarySegment,
    PlaceRecommendation,
    RecommendationResponse,
)


def test_route_map_signature_limits_coordinates_without_exposing_secret():
    points = "116.326000,40.003000;116.330000,40.010000"
    secret = "test-secret"
    signature = hmac.new(
        secret.encode(), points.encode(), hashlib.sha256
    ).hexdigest()

    assert _verified_route_points(points, signature, secret) == [
        (116.326, 40.003),
        (116.33, 40.01),
    ]
    with pytest.raises(HTTPException):
        _verified_route_points(points, "bad-signature", secret)


def _recommendation_response(leg_count: int = 2) -> RecommendationResponse:
    places = [
        PlaceRecommendation(
            poi_id=str(index),
            name=f"地点{index}",
            category="美食",
            address="测试地址",
            longitude=116.330 + index * 0.01,
            latitude=40.000 + index * 0.01,
            score=90.0,
            navigation_url="https://uri.amap.com/navigation?to=1",
        )
        for index in range(1, leg_count + 1)
    ]
    itinerary = [
        ItinerarySegment(
            day_number=1,
            sequence=index,
            from_name="当前位置",
            to_name=f"地点{index}",
            transport="walking",
            route_status="available",
            planning_duration_minutes=10,
            route_polyline="116.326000,40.003000;116.327000,40.004000;116.328000,40.005000",
        )
        for index in range(1, leg_count + 1)
    ]
    return RecommendationResponse(
        origin={"longitude": 116.326, "latitude": 40.003},
        transport="walking",
        radius_meters=1000,
        places=places,
        itinerary=itinerary,
    )


def test_route_map_path_separates_markers_from_polyline_points():
    payload = _recommendation_response(leg_count=9)
    path = _route_map_path(payload, "test-secret")

    assert path and path.startswith("/api/route-map?")
    query = parse_qs(urlparse(path).query)
    points = _verified_route_points(query["points"][0], query["sig"][0], "test-secret")
    markers = _verified_route_points(query["markers"][0], query["msig"][0], "test-secret")

    # markers 只包含起点和每个推荐地点（高德静态地图上限 10 个）
    assert markers[0] == (116.326, 40.003)
    assert len(markers) == 10
    assert len(points) >= len(markers)
    # 路径点必须以实际 polyline 采样点为主体，而不是每个点都是标记
    assert len(points) > 10


def test_route_map_path_requires_secret_and_places():
    assert _route_map_path(_recommendation_response(), "") is None
    payload = _recommendation_response()
    payload.places = []
    assert _route_map_path(payload, "test-secret") is None


def test_mcp_visual_cards_include_map_transport_and_only_https_images():
    from app.main import _inject_place_photos

    payload = {
        "transport": "walking",
        "route_map_path": "/api/route-map?points=signed&sig=value",
        "itinerary": [
            {
                "from_name": "当前位置",
                "to_name": "测试公园",
                "route_duration_minutes": 12,
                "route_distance_meters": 800,
                "planning_duration_minutes": 12,
            }
        ],
        "places": [
            {
                "name": "测试]（不可信）",
                "image_urls": ["https://store.is.autonavi.com/photo.jpg"],
                "navigation_url": "https://uri.amap.com/navigation?to=p1",
            },
            {"name": "坏图", "image_urls": ["javascript:alert(1)"]},
        ],
        "additional_places": [
            {
                "name": "备选咖啡",
                "category": "餐饮服务;咖啡厅",
                "straight_distance_meters": 900,
                "rating": 4.5,
                "navigation_url": "https://uri.amap.com/navigation?to=116.3,40.0",
            }
        ],
    }
    cards, photos = _travel_cards(json.dumps(payload, ensure_ascii=False), "https://guide.example.com")

    assert "https://guide.example.com/api/route-map?" in cards
    assert "![map:附近候选与实际路线示意]" in cards
    assert "🚶 步行" in cards
    # 地点图片改为结构化输出，由注入节点插入到对应推荐下方
    assert "推荐地点图片" not in cards
    assert photos == [
        {
            "index": 1,
            "name": "测试 （不可信）",
            "image_url": "https://store.is.autonavi.com/photo.jpg",
            "navigation_url": "https://uri.amap.com/navigation?to=p1",
        }
    ]
    assert "javascript:" not in cards
    assert "其他候选" in cards
    assert "备选咖啡" in cards
    assert "咖啡厅" in cards
    assert "直线约 900 米" in cards
    assert "https://uri.amap.com/navigation?to=116.3,40.0" in cards

    explain = "**1. 测试公园** 适合散步。[打开高德导航](https://uri.amap.com/navigation?to=p1)"
    answer = _inject_place_photos(explain, cards, photos)
    assert "[打开高德导航](https://uri.amap.com/navigation?to=p1)\n\n![1·测试 （不可信）](https://store.is.autonavi.com/photo.jpg)" in answer
    assert "推荐地点图片" not in answer
