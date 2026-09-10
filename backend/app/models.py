from typing import Literal

from pydantic import BaseModel, Field, field_validator


TransportMode = Literal["walking", "driving"]
CoordinateSystem = Literal["gps", "autonavi", "baidu", "mapbar"]


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    accuracy: float | None = Field(default=None, ge=0)
    coordinate_system: CoordinateSystem = "gps"
    position_name: str = Field(default="", max_length=80)
    radius_meters: int | None = Field(default=None, ge=100, le=10000)
    conversation_id: str = ""
    user: str = Field(min_length=1, max_length=128)


class RecommendationRequest(BaseModel):
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)
    coordinate_system: CoordinateSystem = "gps"
    categories: list[str] = Field(default_factory=lambda: ["美食"])
    keywords: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    budget_per_person: float | None = Field(default=None, ge=0, le=100000)
    radius_meters: int = Field(default=3000, ge=100, le=10000)
    transport: TransportMode = "walking"
    result_count: int = Field(default=3, ge=1, le=20)
    duration_minutes: int | None = Field(default=None, ge=15, le=10080)
    duration_days: int = Field(default=1, ge=1, le=7)

    @field_validator("keywords", "preferences", mode="before")
    @classmethod
    def normalize_string_lists(cls, value: object) -> object:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [part.strip() for part in value.replace("，", ",").split(",") if part.strip()]
        return value

    @field_validator("categories", mode="before")
    @classmethod
    def normalize_categories(cls, value: object) -> object:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            value = [part.strip() for part in value.replace("，", ",").split(",") if part.strip()]
        if not isinstance(value, list):
            return value

        aliases = {
            "吃饭": ["美食"],
            "餐饮": ["美食"],
            "游玩": ["景点", "娱乐", "公园"],
            "玩乐": ["景点", "娱乐", "公园"],
        }
        normalized: list[str] = []
        for item in value:
            text = str(item).strip()
            for category in aliases.get(text, [text]):
                if category and category not in normalized:
                    normalized.append(category)
        return normalized


class PlaceRecommendation(BaseModel):
    poi_id: str
    name: str
    category: str
    address: str
    longitude: float
    latitude: float
    group: Literal["dining", "activity", "other"] = "other"
    route_from: str = "当前位置"
    route_status: Literal["available", "straight_line_only"] = "straight_line_only"
    straight_distance_meters: int | None = None
    route_distance_meters: int | None = None
    route_duration_minutes: int | None = None
    rating: float | None = None
    cost_per_person: float | None = None
    tags: list[str] = Field(default_factory=list)
    image_urls: list[str] = Field(default_factory=list)
    score: float
    navigation_url: str


class ItinerarySegment(BaseModel):
    day_number: int = 1
    sequence: int = 1
    from_name: str
    to_name: str
    transport: TransportMode
    route_status: Literal["available", "straight_line_only"]
    route_distance_meters: int | None = None
    route_duration_minutes: int | None = None
    route_polyline: str | None = None
    straight_distance_meters: int | None = None
    planning_duration_minutes: int
    planning_duration_is_estimate: bool = False


class ItineraryStop(BaseModel):
    day_number: int
    sequence: int
    place_id: str
    name: str
    group: Literal["dining", "activity", "other"]
    arrival_offset_minutes: int
    departure_offset_minutes: int
    suggested_stay_minutes: int
    planning_basis: str
    segment: ItinerarySegment


class ItineraryDay(BaseModel):
    day_number: int
    theme: str
    available_minutes: int
    planned_minutes: int
    travel_minutes: int
    visit_minutes: int
    flexible_minutes: int
    stops: list[ItineraryStop] = Field(default_factory=list)


class RecommendationResponse(BaseModel):
    origin: dict[str, float | str]
    transport: TransportMode
    radius_meters: int
    duration_minutes: int | None = None
    duration_days: int = 1
    total_planned_minutes: int | None = None
    total_travel_minutes: int | None = None
    total_visit_minutes: int | None = None
    total_flexible_minutes: int | None = None
    route_map_path: str | None = None
    places: list[PlaceRecommendation]
    additional_places: list[PlaceRecommendation] = Field(default_factory=list)
    itinerary: list[ItinerarySegment] = Field(default_factory=list)
    itinerary_days: list[ItineraryDay] = Field(default_factory=list)
    planning_assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
