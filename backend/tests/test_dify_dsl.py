import json
from pathlib import Path

import yaml


DSL_PATH = Path(__file__).resolve().parents[2] / "dify" / "nearby-go-chatflow.yml"


def test_dify_dsl_uses_current_canvas_shape():
    dsl = yaml.safe_load(DSL_PATH.read_text(encoding="utf-8"))
    graph = dsl["workflow"]["graph"]

    assert dsl["version"] == "0.7.0"
    assert dsl["app"]["mode"] == "advanced-chat"
    assert dsl["workflow"]["rag_pipeline_variables"] == []

    features = dsl["workflow"]["features"]
    assert "NearbyGo" in features["opening_statement"]
    assert len(features["suggested_questions"]) >= 4
    assert features["suggested_questions_after_answer"]["enabled"] is True
    assert "speech_to_text" not in features
    assert features["text_to_speech"]["enabled"] is False
    assert features["text_to_speech"]["autoPlay"] == "disabled"
    file_upload_config = features["file_upload"]["fileUploadConfig"]
    assert file_upload_config["attachment_image_file_size_limit"] == 2
    assert file_upload_config["workflow_file_upload_limit"] == 10

    dependency = dsl["dependencies"][0]["value"][
        "marketplace_plugin_unique_identifier"
    ]
    assert dependency.startswith("langgenius/deepseek:")

    environment_variables = {
        variable["name"]: variable
        for variable in dsl["workflow"]["environment_variables"]
    }
    assert environment_variables["BACKEND_BASE_URL"]["value"] == (
        "https://nearby-go-2.onrender.com"
    )
    assert environment_variables["INTERNAL_API_TOKEN"]["value"] == ""
    assert environment_variables["INTERNAL_API_TOKEN"]["value_type"] == "secret"

    conversation_variables = {
        variable["name"]: variable
        for variable in dsl["workflow"]["conversation_variables"]
    }
    profile = conversation_variables["user_profile"]
    assert profile["selector"] == ["conversation", "user_profile"]
    assert profile["value_type"] == "string"

    for edge in graph["edges"]:
        assert "isInIteration" in edge["data"]
        assert "isInLoop" in edge["data"]
        assert "zIndex" in edge

    for node in graph["nodes"]:
        assert "selected" in node
        assert node["data"]["title"]
        assert node["data"]["type"]

    model_nodes = [
        node["data"]["model"]
        for node in graph["nodes"]
        if "model" in node["data"]
    ]
    assert model_nodes
    assert all(
        model["provider"]
        == "langgenius/deepseek/deepseek"
        for model in model_nodes
    )
    assert all(model["name"] == "deepseek-v4-flash" for model in model_nodes)

    code_nodes = [node for node in graph["nodes"] if node["data"]["type"] == "code"]
    for code_node in code_nodes:
        for variable in code_node["data"]["variables"]:
            assert variable["value_selector"]
            assert "value" not in variable

    assert [(edge["source"], edge["target"]) for edge in graph["edges"]] == [
        ("start", "route"),
        ("route", "extract"),
        ("route", "general_chat"),
        ("extract", "memory_merge"),
        ("memory_merge", "remember"),
        ("remember", "normalize"),
        ("normalize", "recommend"),
        ("recommend", "validate"),
        ("validate", "map_cards"),
        ("map_cards", "explain"),
        ("explain", "inject_photos"),
        ("inject_photos", "answer"),
        ("general_chat", "general_answer"),
    ]

    classifier = next(node for node in graph["nodes"] if node["id"] == "route")
    assert classifier["data"]["type"] == "question-classifier"
    assert {item["id"] for item in classifier["data"]["classes"]} == {
        "nearby",
        "general",
    }
    assert {edge["sourceHandle"] for edge in graph["edges"] if edge["source"] == "route"} == {
        "nearby",
        "general",
    }

    extractor = next(node for node in graph["nodes"] if node["id"] == "extract")
    parameter_names = {item["name"] for item in extractor["data"]["parameters"]}
    assert {
        "avoid_terms",
        "companion_profile",
        "dietary_needs",
        "accessibility_needs",
        "ambience",
        "decision_priority",
        "plan_mode",
        "remember_preferences",
        "remember_avoid_terms",
        "forget_memory_terms",
        "memory_action",
    }.issubset(parameter_names)

    assert extractor["data"]["memory"]["window"] == {"enabled": True, "size": 8}
    remember = next(node for node in graph["nodes"] if node["id"] == "remember")
    assert remember["data"]["type"] == "assigner"
    assert remember["data"]["version"] == "2"
    assert remember["data"]["items"][0]["variable_selector"] == [
        "conversation",
        "user_profile",
    ]


def test_extract_keeps_short_context_and_requires_explicit_memory_authorization():
    dsl = yaml.safe_load(DSL_PATH.read_text(encoding="utf-8"))
    graph = dsl["workflow"]["graph"]
    extract = next(node for node in graph["nodes"] if node["id"] == "extract")

    assert extract["data"]["memory"]["window"] == {"enabled": True, "size": 8}
    parameter_names = {
        parameter["name"] for parameter in extract["data"]["parameters"]
    }
    assert parameter_names == {
        "categories",
        "keywords",
        "preferences",
        "budget_per_person",
        "radius_meters",
        "transport",
        "duration_minutes",
        "duration_days",
        "avoid_terms",
        "companion_profile",
        "dietary_needs",
        "accessibility_needs",
        "ambience",
        "party_size",
        "decision_priority",
        "plan_mode",
        "start_time",
        "special_notes",
        "remember_preferences",
        "remember_avoid_terms",
        "remember_dietary_needs",
        "remember_accessibility_needs",
        "remember_companion_profile",
        "remember_notes",
        "forget_memory_terms",
        "memory_action",
    }
    instruction = extract["data"]["instruction"]
    assert "长期记忆授权规则" in instruction
    assert "当前消息的新条件永远覆盖历史临时条件" in instruction
    assert "不得保存具体经纬度" in instruction
    assert "具体业态不属于这八类，一律放入 keywords" in instruction


def test_question_classifier_preserves_nearby_and_general_branches():
    dsl = yaml.safe_load(DSL_PATH.read_text(encoding="utf-8"))
    graph = dsl["workflow"]["graph"]
    nodes = {node["id"]: node for node in graph["nodes"]}
    route = nodes["route"]["data"]

    assert route["type"] == "question-classifier"
    assert {item["id"] for item in route["classes"]} == {"nearby", "general"}
    assert "附近出行美食" in route["instruction"]
    assert "无法确定时" in route["instruction"]
    assert "优先选日常问答" in route["instruction"]

    edges = {
        (edge["source"], edge["sourceHandle"], edge["target"])
        for edge in graph["edges"]
    }
    assert ("route", "nearby", "extract") in edges
    assert ("route", "general", "general_chat") in edges
    assert ("general_chat", "source", "general_answer") in edges

    general_prompt = nodes["general_chat"]["data"]["prompt_template"][0]["text"]
    assert "不需要定位" in general_prompt
    assert "不要提及定位状态" in general_prompt
    assert "不要调用、伪造或暗示" in general_prompt


def test_normalizer_preserves_meal_and_activity_intent_and_duration():
    dsl = yaml.safe_load(DSL_PATH.read_text(encoding="utf-8"))
    graph = dsl["workflow"]["graph"]
    code_node = next(node for node in graph["nodes"] if node["id"] == "normalize")
    namespace = {}
    exec(code_node["data"]["code"], namespace)

    output = namespace["main"](
        query="帮我安排一个吃饭加游玩的三小时路线",
        longitude="116.326",
        latitude="40.003",
        coordinate_system="gps",
        categories=["美食"],
        keywords=[],
        preferences=[],
        budget_per_person=None,
        radius_meters=None,
        transport="walking",
        duration_minutes=None,
        duration_days=None,
    )
    body = json.loads(output["request_body"])
    assert "美食" in body["categories"]
    assert {"景点", "娱乐", "公园"}.intersection(body["categories"])
    assert body["duration_minutes"] == 180
    assert body["duration_days"] == 1
    assert body["result_count"] == 3


def test_normalizer_builds_active_time_budget_and_stop_count_for_multi_day_trip():
    dsl = yaml.safe_load(DSL_PATH.read_text(encoding="utf-8"))
    graph = dsl["workflow"]["graph"]
    code_node = next(node for node in graph["nodes"] if node["id"] == "normalize")
    namespace = {}
    exec(code_node["data"]["code"], namespace)

    output = namespace["main"](
        query="安排一个三天两夜的游玩攻略",
        longitude="116.326",
        latitude="40.003",
        coordinate_system="gps",
        categories=["景点"],
        keywords=[],
        preferences=[],
        budget_per_person=None,
        radius_meters=None,
        transport="walking",
        duration_minutes=None,
        duration_days=None,
    )
    body = json.loads(output["request_body"])
    assert body["duration_days"] == 3
    assert body["duration_minutes"] == 3 * 480
    assert body["result_count"] == 12
    assert "美食" in body["categories"]
    assert "景点" in body["categories"]

    weekend = namespace["main"](
        query="两天一夜的附近吃喝游玩攻略，每天约8小时",
        longitude="116.326",
        latitude="40.003",
        coordinate_system="gps",
        categories=["美食", "景点"],
        keywords=[],
        preferences=[],
        budget_per_person=None,
        radius_meters=None,
        transport="walking",
        duration_minutes=None,
        duration_days=None,
    )
    weekend_body = json.loads(weekend["request_body"])
    assert weekend_body["duration_days"] == 2
    assert weekend_body["duration_minutes"] == 2 * 480
    assert weekend_body["result_count"] == 8


def test_normalizer_routes_niche_business_terms_into_amap_keywords():
    dsl = yaml.safe_load(DSL_PATH.read_text(encoding="utf-8"))
    graph = dsl["workflow"]["graph"]
    code_node = next(node for node in graph["nodes"] if node["id"] == "normalize")
    namespace = {}
    exec(code_node["data"]["code"], namespace)

    output = namespace["main"](
        query="附近有没有环境好点的洗脚店或足疗",
        longitude="116.326",
        latitude="40.003",
        coordinate_system="gps",
        categories=["娱乐"],
        keywords=[],
        preferences=[],
        budget_per_person=None,
        radius_meters=None,
        transport="walking",
        duration_minutes=None,
        duration_days=None,
    )
    body = json.loads(output["request_body"])
    assert body["categories"] == ["娱乐"]
    assert "洗脚" in body["keywords"]
    assert "足疗" in body["keywords"]


def test_normalizer_prioritizes_explicit_current_time_and_handles_missing_location():
    dsl = yaml.safe_load(DSL_PATH.read_text(encoding="utf-8"))
    graph = dsl["workflow"]["graph"]
    code_node = next(node for node in graph["nodes"] if node["id"] == "normalize")
    namespace = {}
    exec(code_node["data"]["code"], namespace)

    output = namespace["main"](
        query="改成两天一夜，每天2小时30分钟，开车，吃饭加游玩，人均50元",
        longitude="",
        latitude="",
        coordinate_system="autonavi",
        categories=["美食"],
        keywords=[],
        preferences=["安静"],
        budget_per_person=None,
        radius_meters=None,
        transport="walking",
        duration_minutes=180,
        duration_days=1,
        avoid_terms=["辣"],
        dietary_needs=["花生过敏"],
        accessibility_needs=["少走路"],
        start_time="上午九点",
        fallback_location_name="清华大学",
    )

    body = json.loads(output["request_body"])
    context = json.loads(output["request_context"])
    assert body["duration_days"] == 2
    assert body["duration_minutes"] == 300
    assert body["transport"] == "driving"
    assert body["budget_per_person"] == 50
    assert body["longitude"] == 116.326
    assert body["latitude"] == 40.003
    assert context["response_mode"] == "multi_day"
    assert context["location_source"] == "fallback"
    assert context["avoid_terms"] == ["辣"]
    assert context["dietary_needs"] == ["花生过敏"]
    assert context["accessibility_needs"] == ["少走路"]


def test_validation_compacts_untrusted_data_and_marks_constraint_conflicts():
    dsl = yaml.safe_load(DSL_PATH.read_text(encoding="utf-8"))
    graph = dsl["workflow"]["graph"]
    code_node = next(node for node in graph["nodes"] if node["id"] == "validate")
    namespace = {}
    exec(code_node["data"]["code"], namespace)

    body = {
        "origin": {"longitude": 116.326, "latitude": 40.003},
        "places": [
            {
                "poi_id": "p1",
                "name": "麻辣餐厅",
                "category": "餐饮服务",
                "address": "测试地址",
                "longitude": 116.3,
                "latitude": 40.0,
                "score": 99.0,
                "route_status": "straight_line_only",
                "straight_distance_meters": 300,
                "navigation_url": "https://uri.amap.com/navigation?to=116.3,40.0",
            }
        ],
        "itinerary_days": [],
        "warnings": [],
    }
    context = {
        "response_mode": "quick_pick",
        "avoid_terms": ["辣"],
        "dietary_needs": ["花生过敏"],
        "accessibility_needs": [],
        "location_source": "browser",
    }
    output = namespace["main"](
        body=json.dumps(body, ensure_ascii=False),
        status_code=200,
        request_context=json.dumps(context, ensure_ascii=False),
    )

    result = json.loads(output["validated_result"])
    assert output["response_state"] == "needs_caution"
    assert result["constraint_conflicts"][0]["place_name"] == "麻辣餐厅"
    assert "origin" not in result["data"]
    place = result["data"]["places"][0]
    assert "longitude" not in place
    assert "latitude" not in place
    assert "score" not in place
    assert result["unverified_constraints"] == [
        {"type": "饮食要求", "values": ["花生过敏"]}
    ]


def test_validation_returns_service_error_without_invented_places():
    dsl = yaml.safe_load(DSL_PATH.read_text(encoding="utf-8"))
    graph = dsl["workflow"]["graph"]
    code_node = next(node for node in graph["nodes"] if node["id"] == "validate")
    namespace = {}
    exec(code_node["data"]["code"], namespace)

    output = namespace["main"](body="not-json", status_code=503)
    result = json.loads(output["validated_result"])
    assert output["response_state"] == "service_error"
    assert result["data"]["places"] == []


def test_explanation_prompt_requires_valid_markdown_and_honest_route_fallback():
    dsl = yaml.safe_load(DSL_PATH.read_text(encoding="utf-8"))
    graph = dsl["workflow"]["graph"]
    explain = next(node for node in graph["nodes"] if node["id"] == "explain")
    system_prompt = explain["data"]["prompt_template"][0]["text"]

    assert "完整、规范的 Markdown" in system_prompt
    assert "straight_line_only" in system_prompt
    assert "total_planned_minutes" in system_prompt
    assert "itinerary_days" in system_prompt
    assert "像实用旅行攻略" in system_prompt
    assert "不能自行增删" in system_prompt
    assert "不得输出思考过程" in system_prompt
    assert "constraint_conflicts" in system_prompt
    assert "quick_pick" in system_prompt
    assert "compare" in system_prompt
    assert "Plan B" in system_prompt
    assert "memory_only" in system_prompt
    assert "记忆边界" in system_prompt
    assert "不得暗示记忆跨用户、跨设备或永久保存" in system_prompt
    assert "response_mode" in system_prompt
    assert "其他候选" in system_prompt
    assert "unverified_constraints" in system_prompt
    assert explain["data"]["memory"]["window"] == {"enabled": True, "size": 6}
    answer = next(node for node in graph["nodes"] if node["id"] == "answer")
    assert "inject_photos.final_answer" in answer["data"]["answer"]
    inject = next(node for node in graph["nodes"] if node["id"] == "inject_photos")
    injected_variables = {item["variable"]: item["value_selector"] for item in inject["data"]["variables"]}
    assert injected_variables["explain_text"] == ["explain", "text"]
    assert injected_variables["visual_cards"] == ["map_cards", "visual_cards"]
    assert injected_variables["place_photos"] == ["map_cards", "place_photos"]
    edges = {(edge["source"], edge["target"]) for edge in graph["edges"]}
    assert ("explain", "inject_photos") in edges
    assert ("inject_photos", "answer") in edges
    start_variables = {
        item["variable"]
        for item in next(node for node in graph["nodes"] if node["id"] == "start")["data"]["variables"]
    }
    assert "location_name" in start_variables


def test_map_card_node_builds_visual_map_and_rejects_bad_photo_urls():
    dsl = yaml.safe_load(DSL_PATH.read_text(encoding="utf-8"))
    node = next(
        node for node in dsl["workflow"]["graph"]["nodes"] if node["id"] == "map_cards"
    )
    namespace = {}
    exec(node["data"]["code"], namespace)

    import json

    result = namespace["main"](
        json.dumps(
            {
                "route_map_path": "/api/route-map?points=signed&sig=value",
                "transport": "walking",
                "itinerary": [
                    {
                        "from_name": "当前位置",
                        "to_name": "测试公园",
                        "route_duration_minutes": 10,
                        "route_distance_meters": 700,
                    }
                ],
                "places": [
                    {
                        "name": "测试",
                        "image_urls": ["https://store.is.autonavi.com/p.jpg"],
                        "navigation_url": "https://uri.amap.com/navigation?to=p1",
                    },
                    {"name": "坏图", "image_urls": ["javascript:alert(1)"]},
                ],
                "additional_places": [
                    {
                        "name": "备选足疗店",
                        "category": "休闲娱乐;桑拿/洗浴;洗浴推拿场所",
                        "straight_distance_meters": 850,
                        "rating": 4.5,
                        "navigation_url": "https://uri.amap.com/navigation?to=116.3,40.0",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        "https://guide.example.com",
    )
    visual_cards = result["visual_cards"]
    photos = json.loads(result["place_photos"])

    assert "https://guide.example.com/api/route-map?" in visual_cards
    assert "实际路线" in visual_cards
    assert "🚶 步行" in visual_cards
    # 地点图片不再堆在卡片末尾，而是通过 place_photos 交给注入节点
    assert "推荐地点图片" not in visual_cards
    assert "javascript:" not in visual_cards
    assert "其他候选" in visual_cards
    assert "备选足疗店" in visual_cards
    assert "洗浴推拿场所" in visual_cards
    assert "https://uri.amap.com/navigation?to=116.3,40.0" in visual_cards
    assert photos == [
        {
            "index": 1,
            "name": "测试",
            "image_url": "https://store.is.autonavi.com/p.jpg",
            "navigation_url": "https://uri.amap.com/navigation?to=p1",
        }
    ]


def test_inject_photos_node_attaches_photo_below_each_recommendation():
    dsl = yaml.safe_load(DSL_PATH.read_text(encoding="utf-8"))
    node = next(
        node for node in dsl["workflow"]["graph"]["nodes"] if node["id"] == "inject_photos"
    )
    namespace = {}
    exec(node["data"]["code"], namespace)

    explain_text = "\n".join(
        [
            "# 附近晚餐推荐",
            "**1. 测试餐厅** 为什么适合：距离近。",
            "已知事实：人均 50 元。[打开高德导航](https://uri.amap.com/navigation?to=p1)",
            "**2. 备选咖啡** 适合聊天。[打开高德导航](https://uri.amap.com/navigation?to=p2)",
        ]
    )
    visual_cards = "## 高德位置概览\n![map:示意](https://example.com/api/route-map?sig=x)"
    place_photos = json.dumps(
        [
            {
                "index": 1,
                "name": "测试餐厅",
                "image_url": "https://store.is.autonavi.com/p1.jpg",
                "navigation_url": "https://uri.amap.com/navigation?to=p1",
            },
            {
                "index": 2,
                "name": "备选咖啡",
                "image_url": "https://store.is.autonavi.com/p2.jpg",
                "navigation_url": "https://uri.amap.com/navigation?to=p2",
            },
            {
                "index": 3,
                "name": "坏图",
                "image_url": "javascript:alert(1)",
                "navigation_url": "",
            },
        ],
        ensure_ascii=False,
    )

    answer = namespace["main"](explain_text, visual_cards, place_photos)["final_answer"]

    p1_line = "[打开高德导航](https://uri.amap.com/navigation?to=p1)\n\n![1·测试餐厅](https://store.is.autonavi.com/p1.jpg)"
    p2_line = "[打开高德导航](https://uri.amap.com/navigation?to=p2)\n\n![2·备选咖啡](https://store.is.autonavi.com/p2.jpg)"
    assert p1_line in answer
    assert p2_line in answer
    assert "javascript:" not in answer
    assert answer.rstrip().endswith(visual_cards)
    # 无法匹配导航行时不丢图：兜底追加在推荐区块之后
    orphan = namespace["main"]("### 推荐", "", json.dumps(
        [{"index": 1, "name": "孤立图", "image_url": "https://store.is.autonavi.com/x.jpg", "navigation_url": ""}]
    ))["final_answer"]
    assert "![1·孤立图](https://store.is.autonavi.com/x.jpg)" in orphan


def test_normalizer_builds_personalized_context_and_safe_location_fallback():
    dsl = yaml.safe_load(DSL_PATH.read_text(encoding="utf-8"))
    graph = dsl["workflow"]["graph"]
    code_node = next(node for node in graph["nodes"] if node["id"] == "normalize")
    namespace = {}
    exec(code_node["data"]["code"], namespace)

    output = namespace["main"](
        query="带老人步行10分钟内找安静的晚餐，不要太辣",
        longitude="",
        latitude="",
        coordinate_system="gps",
        categories=["美食"],
        keywords=[],
        preferences=["安静"],
        budget_per_person=80,
        radius_meters=None,
        transport="walking",
        duration_minutes=None,
        duration_days=1,
        avoid_terms=["太辣"],
        companion_profile=["老人"],
        accessibility_needs=["少走路"],
        ambience=["安静"],
        decision_priority="nearest",
        plan_mode="quick_pick",
        fallback_location_name="清华大学",
    )

    import json

    body = json.loads(output["request_body"])
    context = json.loads(output["request_context"])
    assert body["longitude"] == 116.326
    assert body["latitude"] == 40.003
    assert body["radius_meters"] == 800
    assert body["duration_minutes"] is None
    assert {"安静", "老人", "少走路"}.issubset(body["preferences"])
    assert context["location_source"] == "fallback"
    assert context["avoid_terms"] == ["太辣"]
    assert context["decision_priority"] == "nearest"


def test_long_term_memory_requires_structured_updates_and_supports_forget_and_clear():
    dsl = yaml.safe_load(DSL_PATH.read_text(encoding="utf-8"))
    graph = dsl["workflow"]["graph"]
    code_node = next(node for node in graph["nodes"] if node["id"] == "memory_merge")
    namespace = {}
    exec(code_node["data"]["code"], namespace)

    import json

    empty = json.dumps(
        {
            "version": 1,
            "preferences": [],
            "avoid_terms": [],
            "dietary_needs": [],
            "accessibility_needs": [],
            "companion_profile": [],
            "notes": [],
        },
        ensure_ascii=False,
    )
    unauthorized = namespace["main"](
        empty,
        memory_action="none",
        remember_preferences=["不应被保存"],
    )
    assert unauthorized["memory_changed"] == "false"
    assert json.loads(unauthorized["updated_profile"])["preferences"] == []

    remembered = namespace["main"](
        empty,
        memory_action="update",
        remember_preferences=["安静", "适合聊天"],
        remember_avoid_terms=["太辣"],
        remember_dietary_needs=["花生过敏"],
        remember_accessibility_needs=["少走路"],
        remember_companion_profile=["常带老人"],
        remember_notes=["优先室内"],
    )
    profile = json.loads(remembered["updated_profile"])
    assert remembered["memory_changed"] == "true"
    assert profile["preferences"] == ["安静", "适合聊天"]
    assert profile["dietary_needs"] == ["花生过敏"]

    forgotten = namespace["main"](
        remembered["updated_profile"],
        memory_action="forget",
        forget_memory_terms=["辣", "老人"],
    )
    forgotten_profile = json.loads(forgotten["updated_profile"])
    assert forgotten_profile["avoid_terms"] == []
    assert forgotten_profile["companion_profile"] == []

    cleared = namespace["main"](
        forgotten["updated_profile"], memory_action="clear"
    )
    cleared_profile = json.loads(cleared["updated_profile"])
    assert all(
        not value for key, value in cleared_profile.items() if key != "version"
    )


def test_long_term_profile_is_applied_without_treating_dietary_needs_as_soft_scoring():
    dsl = yaml.safe_load(DSL_PATH.read_text(encoding="utf-8"))
    graph = dsl["workflow"]["graph"]
    code_node = next(node for node in graph["nodes"] if node["id"] == "normalize")
    namespace = {}
    exec(code_node["data"]["code"], namespace)

    import json

    profile = json.dumps(
        {
            "version": 1,
            "preferences": ["安静"],
            "avoid_terms": ["酒吧"],
            "dietary_needs": ["花生过敏"],
            "accessibility_needs": ["少走路"],
            "companion_profile": ["老人"],
            "notes": ["优先室内"],
        },
        ensure_ascii=False,
    )
    output = namespace["main"](
        query="推荐附近晚餐",
        longitude="116.326",
        latitude="40.003",
        coordinate_system="gps",
        categories=["美食"],
        keywords=[],
        preferences=[],
        budget_per_person=None,
        radius_meters=None,
        transport="walking",
        duration_minutes=None,
        duration_days=1,
        long_term_profile=profile,
        memory_notice="已更新长期偏好。",
    )
    body = json.loads(output["request_body"])
    context = json.loads(output["request_context"])
    assert {"安静", "少走路", "老人"}.issubset(body["preferences"])
    assert "花生过敏" not in body["preferences"]
    assert context["dietary_needs"] == ["花生过敏"]
    assert context["avoid_terms"] == ["酒吧"]
    assert context["memory_notice"] == "已更新长期偏好。"

    overridden = namespace["main"](
        query="今天想找热闹的晚餐",
        longitude="116.326",
        latitude="40.003",
        coordinate_system="gps",
        categories=["美食"],
        keywords=[],
        preferences=["热闹"],
        budget_per_person=None,
        radius_meters=None,
        transport="walking",
        duration_minutes=None,
        duration_days=1,
        long_term_profile=profile,
    )
    overridden_body = json.loads(overridden["request_body"])
    assert "热闹" in overridden_body["preferences"]
    assert "安静" not in overridden_body["preferences"]


def test_result_auditor_handles_service_errors_and_constraint_conflicts():
    dsl = yaml.safe_load(DSL_PATH.read_text(encoding="utf-8"))
    graph = dsl["workflow"]["graph"]
    code_node = next(node for node in graph["nodes"] if node["id"] == "validate")
    namespace = {}
    exec(code_node["data"]["code"], namespace)

    import json

    failed = namespace["main"]("not-json", 503, "{}")
    assert failed["response_state"] == "service_error"

    audited = namespace["main"](
        json.dumps(
            {
                "places": [
                    {
                        "name": "热辣火锅",
                        "category": "餐饮服务",
                        "address": "示例路",
                        "tags": ["辣"],
                    }
                ],
                "itinerary_days": [],
                "warnings": [],
            },
            ensure_ascii=False,
        ),
        200,
        json.dumps({"avoid_terms": ["辣"], "response_mode": "quick_pick"}, ensure_ascii=False),
    )
    result = json.loads(audited["validated_result"])
    assert audited["response_state"] == "needs_caution"
    assert result["constraint_conflicts"][0]["place_name"] == "热辣火锅"
