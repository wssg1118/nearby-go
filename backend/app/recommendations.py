from __future__ import annotations

import asyncio
import math
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlencode
from urllib.parse import urlparse

from .amap import AmapClient, AmapError
from .models import (
    ItineraryDay,
    ItinerarySegment,
    ItineraryStop,
    PlaceRecommendation,
    RecommendationRequest,
    RecommendationResponse,
)


CATEGORY_TYPES = {
    "美食": "050000",
    "餐厅": "050000",
    "咖啡": "050500",
    "购物": "060000",
    "娱乐": "080000",
    "电影": "080600",
    "景点": "110000",
    "公园": "110100",
}
DINING_CATEGORIES = {"美食", "餐厅", "咖啡"}
ACTIVITY_CATEGORIES = {"购物", "娱乐", "电影", "景点", "公园"}
MAX_CONCURRENT_ROUTE_REQUESTS = 6


def _number(value: Any) -> float | None:
    try:
        if value in (None, "", []):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if item)
    return str(value or "")


def _image_urls(value: Any, limit: int = 3) -> list[str]:
    photos = value if isinstance(value, list) else []
    urls: list[str] = []
    for photo in photos:
        raw_url = photo.get("url") if isinstance(photo, dict) else photo
        try:
            parsed = urlparse(str(raw_url or ""))
        except ValueError:
            continue
        if parsed.scheme != "https" or not parsed.netloc:
            continue
        normalized = parsed.geturl()
        if normalized not in urls:
            urls.append(normalized)
        if len(urls) >= limit:
            break
    return urls


def _category_group(category: str) -> str:
    if category in DINING_CATEGORIES:
        return "dining"
    if category in ACTIVITY_CATEGORIES:
        return "activity"
    return "other"


def _score_place(
    poi: dict[str, Any], radius: int, budget: float | None, preferences: list[str], rank: int
) -> float:
    distance = _number(poi.get("distance")) or radius
    distance_score = max(0.0, 1 - distance / max(radius, 1))

    biz_ext = poi.get("biz_ext") if isinstance(poi.get("biz_ext"), dict) else {}
    rating = _number(biz_ext.get("rating"))
    rating_score = rating / 5 if rating is not None else 0.5

    cost = _number(biz_ext.get("cost"))
    if budget is None or cost is None:
        budget_score = 0.5
    elif cost <= budget:
        budget_score = 1.0
    else:
        budget_score = max(0.0, 1 - (cost - budget) / max(budget, 1))

    searchable = " ".join(
        [_text(poi.get("name")), _text(poi.get("tag")), _text(poi.get("type"))]
    ).lower()
    preference_score = (
        sum(1 for item in preferences if item.lower() in searchable) / len(preferences)
        if preferences
        else 0.5
    )
    source_rank_score = max(0.0, 1 - rank / 20)
    return round(
        100
        * (
            0.35 * distance_score
            + 0.20 * rating_score
            + 0.20 * budget_score
            + 0.15 * preference_score
            + 0.10 * source_rank_score
        ),
        1,
    )


def _navigation_url(
    origin: tuple[float, float],
    destination: tuple[float, float],
    name: str,
    mode: str,
    origin_name: str = "当前位置",
) -> str:
    query = urlencode(
        {
            "from": f"{origin[0]:.6f},{origin[1]:.6f},{origin_name}",
            "to": f"{destination[0]:.6f},{destination[1]:.6f},{name}",
            "mode": "car" if mode == "driving" else "walk",
            "policy": 1 if mode == "driving" else 0,
            "src": "nearby-go",
            "coordinate": "gaode",
            "callnative": 1,
        }
    )
    return f"https://uri.amap.com/navigation?{query}"


def _haversine_meters(
    origin: tuple[float, float], destination: tuple[float, float]
) -> int:
    lng1, lat1 = (math.radians(value) for value in origin)
    lng2, lat2 = (math.radians(value) for value in destination)
    delta_lng = lng2 - lng1
    delta_lat = lat2 - lat1
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lng / 2) ** 2
    )
    return round(6_371_000 * 2 * math.asin(math.sqrt(value)))


def _prepare_candidates(
    pois: Iterable[dict[str, Any]], request: RecommendationRequest, group: str
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rank, poi in enumerate(pois):
        location = str(poi.get("location") or "")
        if "," not in location:
            continue
        try:
            lng, lat = (float(part) for part in location.split(",", maxsplit=1))
        except ValueError:
            continue
        identity = str(poi.get("id") or f"{lng:.6f},{lat:.6f}")
        if identity in seen:
            continue
        seen.add(identity)
        candidate = dict(poi)
        candidate["_lng"] = lng
        candidate["_lat"] = lat
        candidate["_group"] = group
        candidate["_score"] = _score_place(
            poi,
            request.radius_meters,
            request.budget_per_person if group == "dining" else None,
            request.preferences,
            rank,
        )
        candidates.append(candidate)
    candidates.sort(key=lambda item: item["_score"], reverse=True)
    return candidates


async def _search_group(
    amap: AmapClient,
    center: tuple[float, float],
    request: RecommendationRequest,
    categories: list[str],
    group: str,
    target_count: int,
) -> list[dict[str, Any]]:
    types = list(dict.fromkeys(CATEGORY_TYPES.get(item, "080000") for item in categories))
    collected: list[dict[str, Any]] = []

    if request.keywords:
        collected.extend(
            await amap.search_around(
                *center,
                radius_meters=request.radius_meters,
                types=types,
                keywords=request.keywords,
                limit=20,
            )
        )
    if not request.keywords or len(collected) < target_count:
        collected.extend(
            await amap.search_around(
                *center,
                radius_meters=request.radius_meters,
                types=types,
                keywords=[],
                limit=20,
            )
        )
    return _prepare_candidates(collected, request, group)


async def _fetch_route(
    amap: AmapClient,
    semaphore: asyncio.Semaphore,
    origin: tuple[float, float],
    destination: tuple[float, float],
    mode: str,
) -> tuple[dict[str, int] | None, AmapError | None]:
    """Fetch one route without making independent route failures cancel the batch."""
    try:
        async with semaphore:
            return await amap.route(origin, destination, mode), None
    except AmapError as exc:
        return None, exc


def _effective_day_count(request: RecommendationRequest) -> int:
    if request.duration_days > 1:
        return request.duration_days
    if request.duration_minutes and request.duration_minutes > 720:
        return min(7, max(2, (request.duration_minutes + 240) // 480))
    return 1


def _total_available_minutes(
    request: RecommendationRequest, is_itinerary: bool, day_count: int
) -> int | None:
    if request.duration_minutes is not None:
        return request.duration_minutes
    if day_count > 1:
        return day_count * 480
    if is_itinerary:
        return 240
    return None


def _desired_result_count(
    request: RecommendationRequest,
    total_minutes: int | None,
    is_itinerary: bool,
    day_count: int,
) -> int:
    target = request.result_count
    if day_count > 1:
        daily_minutes = (total_minutes or day_count * 480) / day_count
        stops_per_day = max(3, min(4, math.ceil(daily_minutes / 120)))
        target = max(target, day_count * stops_per_day)
    elif is_itinerary and total_minutes:
        target = max(target, min(5, max(2, math.ceil(total_minutes / 60))))
    return min(20, target)


def _select_day_candidates(
    candidates_by_group: dict[str, list[dict[str, Any]]],
    target_count: int,
    day_count: int,
    origin: tuple[float, float],
) -> list[list[dict[str, Any]]]:
    days: list[list[dict[str, Any]]] = [[] for _ in range(day_count)]
    dining = candidates_by_group["dining"]
    activity = candidates_by_group["activity"]
    other = candidates_by_group["other"]

    def coordinates(item: dict[str, Any]) -> tuple[float, float]:
        return item["_lng"], item["_lat"]

    def nearest_order(
        items: list[dict[str, Any]], start: tuple[float, float]
    ) -> list[dict[str, Any]]:
        remaining = list(items)
        ordered: list[dict[str, Any]] = []
        current = start
        while remaining:
            nearest = min(
                remaining,
                key=lambda item: _haversine_meters(current, coordinates(item)),
            )
            ordered.append(nearest)
            remaining.remove(nearest)
            current = coordinates(nearest)
        return ordered

    if dining and activity:
        meal_count = min(len(dining), day_count, target_count)
        meals = dining[:meal_count]
        remaining_slots = target_count - len(meals)
        activities = activity[:remaining_slots]
        remaining_slots -= len(activities)
        extras = other[:remaining_slots]
        for index, meal in enumerate(meals):
            days[index].append(meal)

        day_targets = _distribute_minutes(target_count, day_count)
        remaining_activities = list(activities)
        # Give each day an activity before filling extra slots, then keep POIs near
        # that day's meal anchor together to reduce cross-city zigzags.
        for index in range(day_count):
            if not remaining_activities or len(days[index]) >= day_targets[index]:
                continue
            anchor = coordinates(meals[index]) if index < len(meals) else origin
            nearest = min(
                remaining_activities,
                key=lambda item: _haversine_meters(anchor, coordinates(item)),
            )
            days[index].append(nearest)
            remaining_activities.remove(nearest)

        for item in [*remaining_activities, *extras]:
            eligible = [
                index
                for index in range(day_count)
                if len(days[index]) < day_targets[index]
            ]
            if not eligible:
                break
            chosen_day = min(
                eligible,
                key=lambda index: _haversine_meters(
                    coordinates(meals[index]) if index < len(meals) else origin,
                    coordinates(item),
                ),
            )
            days[chosen_day].append(item)

        for index, items in enumerate(days):
            if day_count == 1 and meals and meals[0] in items:
                rest = [item for item in items if item is not meals[0]]
                days[index] = [meals[0], *nearest_order(rest, coordinates(meals[0]))]
            else:
                days[index] = nearest_order(items, origin)
        return days

    pool = sorted(
        [*dining, *activity, *other], key=lambda item: item["_score"], reverse=True
    )[:target_count]
    for index, item in enumerate(pool):
        days[index % day_count].append(item)
    for index, items in enumerate(days):
        days[index] = nearest_order(items, origin)
    return days


def _weighted_allocation(total: int, weights: list[int]) -> list[int]:
    if not weights:
        return []
    if total <= 0:
        return [0] * len(weights)
    weight_sum = sum(weights) or len(weights)
    exact = [total * weight / weight_sum for weight in weights]
    allocated = [math.floor(value) for value in exact]
    for index in sorted(
        range(len(weights)), key=lambda item: exact[item] - allocated[item], reverse=True
    )[: total - sum(allocated)]:
        allocated[index] += 1
    return allocated


def _preferred_stay_minutes(place: PlaceRecommendation) -> int:
    category = place.category
    if place.group == "dining":
        return 60
    if "电影院" in category or "影剧院" in category:
        return 120
    if "风景名胜" in category or "公园" in category:
        return 90
    if "购物" in category:
        return 75
    if "体育休闲" in category or "娱乐" in category:
        return 90
    return 75


def _allocate_stay_minutes(
    places: list[PlaceRecommendation], total: int
) -> list[int]:
    if not places:
        return []
    minimums = [45 if place.group == "dining" else 30 for place in places]
    preferred = [_preferred_stay_minutes(place) for place in places]
    if total < sum(minimums):
        return _weighted_allocation(total, preferred)
    extra = total - sum(minimums)
    extra_weights = [max(1, wanted - minimum) for wanted, minimum in zip(preferred, minimums)]
    extras = _weighted_allocation(extra, extra_weights)
    return [minimum + addition for minimum, addition in zip(minimums, extras)]


def _planning_basis(place: PlaceRecommendation) -> str:
    if place.group == "dining":
        return "停留时长是用餐节奏建议，已为点餐、用餐和可能的等位预留时间；实际排队情况需现场确认。"
    if "电影院" in place.category or "影剧院" in place.category:
        return "停留时长按一段完整文化娱乐体验预留；演出、场次、购票和开放信息需出发前核实。"
    if "风景名胜" in place.category or "公园" in place.category:
        return "停留时长按步行参观和休息节奏分配；开放区域、预约要求和天气影响需出发前核实。"
    return "停留时长按一般游览体验分配；营业时间、预约要求和现场活动需出发前核实。"


def _day_theme(places: list[PlaceRecommendation]) -> str:
    groups = {place.group for place in places}
    if {"dining", "activity"}.issubset(groups):
        return "餐饮补给与周边深度游"
    if "activity" in groups:
        return "周边景点与休闲体验"
    if "dining" in groups:
        return "本地餐饮探索"
    return "附近轻松漫游"


def _distribute_minutes(total: int, day_count: int) -> list[int]:
    base, remainder = divmod(total, day_count)
    return [base + (1 if index < remainder else 0) for index in range(day_count)]


async def build_recommendations(
    request: RecommendationRequest, amap: AmapClient
) -> RecommendationResponse:
    origin = await amap.convert_location(
        request.longitude, request.latitude, request.coordinate_system
    )
    effective_day_count = _effective_day_count(request)
    categories = list(request.categories or ["美食"])
    if effective_day_count > 1:
        if not any(category in DINING_CATEGORIES for category in categories):
            categories.append("美食")
        if not any(category in ACTIVITY_CATEGORIES for category in categories):
            categories.extend(["景点", "娱乐", "公园"])

    categories_by_group = {"dining": [], "activity": [], "other": []}
    for category in categories:
        group = _category_group(category)
        if category not in categories_by_group[group]:
            categories_by_group[group].append(category)

    requested_itinerary = bool(
        categories_by_group["dining"] and categories_by_group["activity"]
    )
    total_available = _total_available_minutes(
        request, requested_itinerary, effective_day_count
    )
    target_count = _desired_result_count(
        request, total_available, requested_itinerary, effective_day_count
    )
    candidates_by_group: dict[str, list[dict[str, Any]]] = {
        "dining": [],
        "activity": [],
        "other": [],
    }
    warnings: list[str] = []
    search_failures: list[AmapError] = []

    if categories_by_group["dining"]:
        try:
            candidates_by_group["dining"] = await _search_group(
                amap,
                origin,
                request,
                categories_by_group["dining"],
                "dining",
                target_count,
            )
        except AmapError as exc:
            search_failures.append(exc)

    activity_center = origin
    if candidates_by_group["dining"] and categories_by_group["activity"]:
        meal = candidates_by_group["dining"][0]
        activity_center = (
            origin
            if effective_day_count > 1
            else (meal["_lng"], meal["_lat"])
        )

    for group in ("activity", "other"):
        if not categories_by_group[group]:
            continue
        try:
            candidates_by_group[group] = await _search_group(
                amap,
                activity_center if group == "activity" else origin,
                request,
                categories_by_group[group],
                group,
                target_count,
            )
        except AmapError as exc:
            search_failures.append(exc)

    if search_failures and not any(candidates_by_group.values()):
        raise search_failures[0]
    for failure in search_failures:
        warnings.append(f"部分分类暂时无法搜索：{failure.public_detail}。")

    if requested_itinerary and not candidates_by_group["dining"]:
        warnings.append("未找到可用餐饮候选，当前行程无法完整覆盖吃喝与游玩。")
    if requested_itinerary and not candidates_by_group["activity"]:
        warnings.append("未找到可用游玩候选，当前行程无法完整覆盖吃喝与游玩。")

    plan_requested = bool(
        total_available is not None or effective_day_count > 1 or requested_itinerary
    )
    planning_day_count = effective_day_count if plan_requested else 1
    candidate_days = _select_day_candidates(
        candidates_by_group, target_count, planning_day_count, origin
    )
    incomplete_days = [
        index
        for index, candidates in enumerate(candidate_days, start=1)
        if requested_itinerary
        and not {"dining", "activity"}.issubset(
            {candidate["_group"] for candidate in candidates}
        )
    ]
    if incomplete_days:
        warnings.append(
            "以下日期因候选不足未能同时安排餐饮和游玩："
            + "、".join(f"第{day}天" for day in incomplete_days)
            + "。"
        )

    route_failures: list[AmapError] = []
    results: list[PlaceRecommendation] = []
    itinerary: list[ItinerarySegment] = []
    day_pairs: list[list[tuple[PlaceRecommendation, ItinerarySegment]]] = []

    route_specs: list[
        tuple[int, int, tuple[float, float], tuple[float, float]]
    ] = []
    for day_index, candidates in enumerate(candidate_days, start=1):
        segment_origin = origin
        for sequence, poi in enumerate(candidates, start=1):
            destination = (poi["_lng"], poi["_lat"])
            route_specs.append((day_index, sequence, segment_origin, destination))
            segment_origin = destination

    route_semaphore = asyncio.Semaphore(MAX_CONCURRENT_ROUTE_REQUESTS)
    route_results = await asyncio.gather(
        *(
            _fetch_route(
                amap,
                route_semaphore,
                segment_origin,
                destination,
                request.transport,
            )
            for _, _, segment_origin, destination in route_specs
        )
    )
    routes_by_stop = {
        (day_index, sequence): route_result
        for (day_index, sequence, _, _), route_result in zip(
            route_specs, route_results
        )
    }

    for day_index, candidates in enumerate(candidate_days, start=1):
        segment_origin = origin
        segment_origin_name = "当前位置" if day_index == 1 else f"第{day_index}天起点"
        pairs: list[tuple[PlaceRecommendation, ItinerarySegment]] = []
        for sequence, poi in enumerate(candidates, start=1):
            destination = (poi["_lng"], poi["_lat"])
            route, route_error = routes_by_stop[(day_index, sequence)]
            if route_error is not None:
                exc = route_error
                route_failures.append(exc)

            biz_ext = poi.get("biz_ext") if isinstance(poi.get("biz_ext"), dict) else {}
            tag_text = _text(poi.get("tag"))
            tags = [
                item.strip()
                for item in tag_text.replace("，", ",").split(",")
                if item.strip()
            ]
            straight_distance = _haversine_meters(segment_origin, destination)
            route_distance = route["distance"] if route else None
            route_duration = math.ceil(route["duration"] / 60) if route else None
            planning_duration = route_duration or max(
                1,
                math.ceil(
                    straight_distance / (350 if request.transport == "driving" else 80)
                ),
            )
            score = poi["_score"]
            if route_duration is not None:
                route_score = max(0.0, 1 - route_duration / 60)
                score = round(0.8 * score + 20 * route_score, 1)
            name = _text(poi.get("name")) or "未命名地点"
            result = PlaceRecommendation(
                poi_id=str(poi.get("id") or ""),
                name=name,
                category=_text(poi.get("type")),
                address=_text(poi.get("address")),
                longitude=poi["_lng"],
                latitude=poi["_lat"],
                group=poi["_group"],
                route_from=segment_origin_name,
                route_status="available" if route else "straight_line_only",
                straight_distance_meters=straight_distance,
                route_distance_meters=route_distance,
                route_duration_minutes=route_duration,
                rating=_number(biz_ext.get("rating")),
                cost_per_person=_number(biz_ext.get("cost")),
                tags=tags[:6],
                image_urls=_image_urls(poi.get("photos")),
                score=score,
                navigation_url=_navigation_url(
                    segment_origin,
                    destination,
                    name,
                    request.transport,
                    segment_origin_name,
                ),
            )
            segment = ItinerarySegment(
                day_number=day_index,
                sequence=sequence,
                from_name=segment_origin_name,
                to_name=name,
                transport=request.transport,
                route_status=result.route_status,
                route_distance_meters=route_distance,
                route_duration_minutes=route_duration,
                straight_distance_meters=straight_distance,
                planning_duration_minutes=planning_duration,
                planning_duration_is_estimate=route is None,
            )
            results.append(result)
            itinerary.append(segment)
            pairs.append((result, segment))
            segment_origin = destination
            segment_origin_name = name
        day_pairs.append(pairs)

    itinerary_days: list[ItineraryDay] = []
    planning_assumptions: list[str] = []
    if plan_requested and total_available is not None:
        day_budgets = _distribute_minutes(total_available, planning_day_count)
        planning_assumptions.extend(
            [
                "用户未提供每天的具体开始时间，日程使用从当天出发起算的相对分钟。",
                "建议停留时长由地点类别和总时间预算分配，是行程规划建议，不是高德实时数据。",
                "开放时间、门票、预约、排队和现场活动不在接口数据中，出发前需向场所核实。",
            ]
        )
        if planning_day_count > 1:
            planning_assumptions.append(
                "用户未提供住宿地点，多日行程暂按每天从当前位置重新出发规划。"
            )

        for day_number, (pairs, available_minutes) in enumerate(
            zip(day_pairs, day_budgets), start=1
        ):
            places = [place for place, _ in pairs]
            travel_minutes = sum(
                segment.planning_duration_minutes for _, segment in pairs
            )
            target_buffer = min(30, max(10, round(available_minutes * 0.08)))
            available_for_visits = max(0, available_minutes - travel_minutes)
            if available_for_visits < len(places) * 15:
                target_buffer = 0
            visit_budget = max(0, available_for_visits - target_buffer)
            stay_minutes = _allocate_stay_minutes(places, visit_budget)
            visit_minutes = sum(stay_minutes)
            flexible_minutes = max(
                0, available_minutes - travel_minutes - visit_minutes
            )
            offset = 0
            stops: list[ItineraryStop] = []
            for sequence, ((place, segment), stay) in enumerate(
                zip(pairs, stay_minutes), start=1
            ):
                offset += segment.planning_duration_minutes
                arrival = offset
                departure = arrival + stay
                stops.append(
                    ItineraryStop(
                        day_number=day_number,
                        sequence=sequence,
                        place_id=place.poi_id,
                        name=place.name,
                        group=place.group,
                        arrival_offset_minutes=arrival,
                        departure_offset_minutes=departure,
                        suggested_stay_minutes=stay,
                        planning_basis=_planning_basis(place),
                        segment=segment,
                    )
                )
                offset = departure
            planned_minutes = travel_minutes + visit_minutes + flexible_minutes
            itinerary_days.append(
                ItineraryDay(
                    day_number=day_number,
                    theme=_day_theme(places),
                    available_minutes=available_minutes,
                    planned_minutes=planned_minutes,
                    travel_minutes=travel_minutes,
                    visit_minutes=visit_minutes,
                    flexible_minutes=flexible_minutes,
                    stops=stops,
                )
            )

    if not results:
        warnings.append("当前位置和筛选条件下未找到合适地点，请扩大范围或减少限制。")
    if any(item.rating is None for item in results):
        warnings.append("部分地点缺少评分；推荐未将缺失评分视为真实低分。")
    if route_failures:
        codes = list(dict.fromkeys(item.code for item in route_failures if item.code))
        code_hint = f"（错误码：{'、'.join(codes)}）" if codes else ""
        warnings.append(
            f"部分地点未取得实时路线{code_hint}；已保留地点，所示直线距离不是实际路线，规划耗时为估算。"
        )

    total_planned = sum(day.planned_minutes for day in itinerary_days) if itinerary_days else None
    total_travel = sum(day.travel_minutes for day in itinerary_days) if itinerary_days else None
    total_visit = sum(day.visit_minutes for day in itinerary_days) if itinerary_days else None
    total_flexible = (
        sum(day.flexible_minutes for day in itinerary_days) if itinerary_days else None
    )
    return RecommendationResponse(
        origin={
            "longitude": origin[0],
            "latitude": origin[1],
            "coordinate_system": "autonavi",
        },
        transport=request.transport,
        radius_meters=request.radius_meters,
        duration_minutes=total_available,
        duration_days=effective_day_count,
        total_planned_minutes=total_planned,
        total_travel_minutes=total_travel,
        total_visit_minutes=total_visit,
        total_flexible_minutes=total_flexible,
        places=results,
        itinerary=itinerary if plan_requested else [],
        itinerary_days=itinerary_days,
        planning_assumptions=planning_assumptions,
        warnings=warnings,
    )
