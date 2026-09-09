import asyncio
from urllib.parse import parse_qs, urlparse

import pytest

from app.amap import AmapError
from app.models import RecommendationRequest
from app.recommendations import build_recommendations


DINING_POIS = [
    {
        "id": "food-1",
        "name": "测试川菜馆 <不可信>",
        "type": "餐饮服务;中餐厅;川菜",
        "address": "测试路1号",
        "location": "116.330000,40.010000",
        "distance": "600",
        "tag": "辣,适合聚餐",
        "biz_ext": {"rating": "4.7", "cost": "58"},
    },
    {
        "id": "food-2",
        "name": "较远餐厅",
        "type": "餐饮服务;中餐厅",
        "address": "测试路2号",
        "location": "116.350000,40.020000",
        "distance": "2400",
        "biz_ext": {"rating": "4.0", "cost": "120"},
    },
]

ACTIVITY_POIS = [
    {
        "id": "play-1",
        "name": "湖畔公园",
        "type": "风景名胜;公园广场;公园",
        "address": "游玩路8号",
        "location": "116.335000,40.012000",
        "distance": "480",
        "tag": "散步,风景",
        "biz_ext": {"rating": "4.6"},
    }
]


class FakeAmap:
    def __init__(self):
        self.searches = []
        self.routes = []

    async def convert_location(self, longitude, latitude, coordinate_system):
        return longitude + 0.001, latitude + 0.001

    async def search_around(self, longitude, latitude, **kwargs):
        self.searches.append(((longitude, latitude), kwargs))
        types = kwargs["types"]
        return ACTIVITY_POIS if any(item.startswith(("08", "11", "06")) for item in types) else DINING_POIS

    async def route(self, origin, destination, mode):
        self.routes.append((origin, destination, mode))
        return {"distance": 800, "duration": 720}


@pytest.mark.asyncio
async def test_build_recommendations_prefers_nearby_budget_match_and_safe_navigation_url():
    amap = FakeAmap()
    request = RecommendationRequest(
        longitude=116.326,
        latitude=40.003,
        coordinate_system="autonavi",
        categories=["美食"],
        preferences=["辣"],
        budget_per_person=80,
        radius_meters=3000,
        result_count=2,
    )

    result = await build_recommendations(request, amap)

    assert result.places[0].name == "测试川菜馆 <不可信>"
    assert result.places[0].route_duration_minutes == 12
    assert result.places[0].route_status == "available"
    assert result.places[0].cost_per_person == 58
    query = parse_qs(urlparse(result.places[0].navigation_url).query)
    assert query["to"][0].endswith(",测试川菜馆 <不可信>")
    assert query["mode"] == ["walk"]
    assert query["coordinate"] == ["gaode"]
    assert query["callnative"] == ["1"]


@pytest.mark.asyncio
async def test_preferences_rank_but_do_not_become_hard_amap_keywords():
    amap = FakeAmap()
    await build_recommendations(
        RecommendationRequest(
            longitude=116.326,
            latitude=40.003,
            categories=["美食"],
            preferences=["安静"],
        ),
        amap,
    )

    assert amap.searches[0][1]["keywords"] == []


@pytest.mark.asyncio
async def test_meal_and_activity_are_searched_separately_and_form_segmented_itinerary():
    amap = FakeAmap()
    result = await build_recommendations(
        RecommendationRequest(
            longitude=116.326,
            latitude=40.003,
            coordinate_system="autonavi",
            categories=["吃饭", "游玩"],
            radius_meters=3000,
            result_count=2,
            duration_minutes=180,
        ),
        amap,
    )

    assert [place.group for place in result.places] == ["dining", "activity"]
    assert result.duration_minutes == 180
    assert result.total_planned_minutes == 180
    assert result.total_travel_minutes == 24
    assert result.total_visit_minutes > result.total_travel_minutes
    assert result.itinerary_days[0].planned_minutes == 180
    assert sum(
        stop.suggested_stay_minutes for stop in result.itinerary_days[0].stops
    ) == result.itinerary_days[0].visit_minutes
    assert amap.searches[0][0] == (116.327, 40.004)
    assert amap.searches[1][0] == (116.33, 40.01)
    assert result.itinerary[0].from_name == "当前位置"
    assert result.itinerary[1].from_name == result.places[0].name
    assert amap.routes[1][0] == (result.places[0].longitude, result.places[0].latitude)


@pytest.mark.asyncio
async def test_explicit_keyword_zero_results_falls_back_to_category_search():
    class KeywordFallbackAmap(FakeAmap):
        async def search_around(self, longitude, latitude, **kwargs):
            self.searches.append(((longitude, latitude), kwargs))
            return [] if kwargs["keywords"] else DINING_POIS

    amap = KeywordFallbackAmap()
    result = await build_recommendations(
        RecommendationRequest(
            longitude=116.326,
            latitude=40.003,
            categories=["美食"],
            keywords=["不存在的精确词"],
        ),
        amap,
    )

    assert len(amap.searches) == 2
    assert amap.searches[1][1]["keywords"] == []
    assert result.places


@pytest.mark.asyncio
async def test_route_requests_run_concurrently_without_changing_result_order():
    class ConcurrentRouteAmap(FakeAmap):
        def __init__(self):
            super().__init__()
            self.in_flight = 0
            self.max_in_flight = 0

        async def route(self, origin, destination, mode):
            self.routes.append((origin, destination, mode))
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            await asyncio.sleep(0.01)
            self.in_flight -= 1
            return {"distance": 800, "duration": 720}

    amap = ConcurrentRouteAmap()
    result = await build_recommendations(
        RecommendationRequest(
            longitude=116.326,
            latitude=40.003,
            coordinate_system="autonavi",
            categories=["美食", "景点"],
            result_count=3,
        ),
        amap,
    )

    assert amap.max_in_flight > 1
    assert [place.name for place in result.places] == [
        "测试川菜馆 <不可信>",
        "湖畔公园",
    ]


@pytest.mark.asyncio
async def test_route_failure_keeps_place_and_labels_straight_line_fallback():
    class RouteFailureAmap(FakeAmap):
        async def route(self, origin, destination, mode):
            raise AmapError("USER_DAILY_QUERY_OVER_LIMIT", operation="route", code="10044")

    result = await build_recommendations(
        RecommendationRequest(
            longitude=116.326,
            latitude=40.003,
            duration_minutes=90,
        ),
        RouteFailureAmap(),
    )

    assert result.places
    assert result.places[0].route_status == "straight_line_only"
    assert result.places[0].route_distance_meters is None
    assert result.places[0].straight_distance_meters > 0
    assert result.itinerary[0].planning_duration_is_estimate is True
    assert result.itinerary_days[0].planned_minutes == 90
    assert "直线距离不是实际路线" in result.warnings[-1]
    assert "10044" in result.warnings[-1]


@pytest.mark.asyncio
async def test_empty_search_returns_warning():
    class EmptyAmap(FakeAmap):
        async def search_around(self, *args, **kwargs):
            return []

    result = await build_recommendations(
        RecommendationRequest(longitude=116.326, latitude=40.003), EmptyAmap()
    )

    assert result.places == []
    assert "扩大范围" in result.warnings[0]


@pytest.mark.asyncio
async def test_multi_day_plan_has_daily_meals_activities_and_full_time_budgets():
    dining = [
        {
            "id": f"food-{index}",
            "name": f"第{index}餐厅",
            "type": "餐饮服务;中餐厅",
            "address": f"餐饮路{index}号",
            "location": f"{116.330 + index * 0.001:.6f},{40.010 + index * 0.001:.6f}",
            "distance": str(400 + index * 80),
            "biz_ext": {"rating": "4.5", "cost": "50"},
        }
        for index in range(1, 5)
    ]
    activities = [
        {
            "id": f"play-{index}",
            "name": f"第{index}游玩点",
            "type": "风景名胜;公园广场;公园",
            "address": f"游玩路{index}号",
            "location": f"{116.340 + index * 0.001:.6f},{40.020 + index * 0.001:.6f}",
            "distance": str(500 + index * 90),
            "biz_ext": {"rating": "4.6"},
        }
        for index in range(1, 9)
    ]

    class MultiDayAmap(FakeAmap):
        async def search_around(self, longitude, latitude, **kwargs):
            self.searches.append(((longitude, latitude), kwargs))
            types = kwargs["types"]
            return (
                activities
                if any(item.startswith(("08", "11", "06")) for item in types)
                else dining
            )

        async def route(self, origin, destination, mode):
            self.routes.append((origin, destination, mode))
            return {"distance": 700, "duration": 600}

    amap = MultiDayAmap()
    result = await build_recommendations(
        RecommendationRequest(
            longitude=116.326,
            latitude=40.003,
            coordinate_system="autonavi",
            categories=["景点"],
            duration_minutes=960,
            result_count=8,
        ),
        amap,
    )

    assert len(result.itinerary_days) == 2
    assert len(result.places) == 8
    assert result.total_planned_minutes == 960
    assert all(day.planned_minutes == 480 for day in result.itinerary_days)
    assert all({stop.group for stop in day.stops} == {"dining", "activity"} for day in result.itinerary_days)
    assert all(day.visit_minutes > day.travel_minutes for day in result.itinerary_days)
    assert amap.routes[0][0] == (116.327, 40.004)
    assert amap.routes[4][0] == (116.327, 40.004)
    assert "每天从当前位置重新出发" in result.planning_assumptions[-1]
