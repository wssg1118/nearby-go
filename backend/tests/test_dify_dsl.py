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
    assert features["opening_statement"] == ""
    assert features["suggested_questions"] == []
    assert features["suggested_questions_after_answer"]["enabled"] is False
    file_upload_config = features["file_upload"]["fileUploadConfig"]
    assert file_upload_config["attachment_image_file_size_limit"] == 2
    assert file_upload_config["workflow_file_upload_limit"] == 10

    dependency = dsl["dependencies"][0]["value"][
        "marketplace_plugin_unique_identifier"
    ]
    assert dependency.startswith("langgenius/openai_api_compatible:")

    environment_variables = {
        variable["name"]: variable
        for variable in dsl["workflow"]["environment_variables"]
    }
    assert environment_variables["BACKEND_BASE_URL"]["value"] == (
        "https://nearby-go.onrender.com"
    )
    assert environment_variables["INTERNAL_API_TOKEN"]["value"] == ""
    assert environment_variables["INTERNAL_API_TOKEN"]["value_type"] == "secret"

    node_ids = [node["id"] for node in graph["nodes"]]
    assert node_ids == [
        "start",
        "route",
        "extract",
        "normalize",
        "recommend",
        "validate",
        "explain",
        "answer",
        "general_chat",
        "general_answer",
    ]
    assert dsl["workflow"]["conversation_variables"] == []
    assert ("recommend", "validate") in {
        (edge["source"], edge["target"]) for edge in graph["edges"]
    }
    assert ("validate", "explain") in {
        (edge["source"], edge["target"]) for edge in graph["edges"]
    }
    assert ("route", "extract") in {
        (edge["source"], edge["target"]) for edge in graph["edges"]
    }
    assert ("route", "general_chat") in {
        (edge["source"], edge["target"]) for edge in graph["edges"]
    }

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
        == "langgenius/openai_api_compatible/openai_api_compatible"
        for model in model_nodes
    )
    assert all(model["name"] == "DeepSeek-V4-Flash-0731" for model in model_nodes)

    code_node = next(node for node in graph["nodes"] if node["data"]["type"] == "code")
    for variable in code_node["data"]["variables"]:
        assert variable["value_selector"]
        assert "value" not in variable


def test_dify_uses_short_context_without_long_term_profile_storage():
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
        "dietary_needs",
        "accessibility_needs",
        "start_time",
    }
    assert not any(name.startswith("remember_") for name in parameter_names)
    assert "memory_action" not in parameter_names
    assert "当前消息永远优先" in extract["data"]["instruction"]
    assert "不能直接复用历史地点结果" in extract["data"]["instruction"]


def test_question_classifier_preserves_nearby_and_general_branches():
    dsl = yaml.safe_load(DSL_PATH.read_text(encoding="utf-8"))
    graph = dsl["workflow"]["graph"]
    nodes = {node["id"]: node for node in graph["nodes"]}
    route = nodes["route"]["data"]

    assert route["type"] == "question-classifier"
    assert {item["id"] for item in route["classes"]} == {"nearby", "general"}
    assert "换一个" in route["instruction"]
    assert "优先选择“附近实时推荐”" in route["instruction"]

    edges = {
        (edge["source"], edge["sourceHandle"], edge["target"])
        for edge in graph["edges"]
    }
    assert ("route", "nearby", "extract") in edges
    assert ("route", "general", "general_chat") in edges
    assert ("general_chat", "source", "general_answer") in edges

    general_prompt = nodes["general_chat"]["data"]["prompt_template"][0]["text"]
    assert "不需要定位" in general_prompt
    assert "不要读取、讨论或推断用户位置" in general_prompt
    assert "不建立长期用户画像" in general_prompt


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
    assert "response_state=service_error" in system_prompt
    assert "不要声称永久记住用户" in system_prompt
    assert "图片语法" in system_prompt
    user_prompt = explain["data"]["prompt_template"][1]["text"]
    assert "{{#validate.validated_result#}}" in user_prompt
    assert "{{#recommend.body#}}" not in user_prompt
