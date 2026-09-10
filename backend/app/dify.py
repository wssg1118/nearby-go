from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator

import httpx

from .config import Settings
from .models import ChatRequest


class DifyError(RuntimeError):
    pass


_COMPLETE_THINK_BLOCK = re.compile(
    r"<think\b[^>]*>[\s\S]*?</think\s*>",
    re.IGNORECASE,
)
_OPEN_THINK_TAG = re.compile(r"<think\b[^>]*>", re.IGNORECASE)
_COMPLETE_DIFY_REASONING_BLOCK = re.compile(
    r"<!--\s*dify-deepseek-reasoning\s*-->[\s\S]*?"
    r"<!--\s*/dify-deepseek-reasoning\s*-->",
    re.IGNORECASE,
)
_OPEN_DIFY_REASONING_MARKER = re.compile(
    r"<!--\s*dify-deepseek-reasoning\s*-->",
    re.IGNORECASE,
)
_REASONING_TAGS = re.compile(
    r"</?think\b[^>]*>|<!--\s*/?dify-deepseek-reasoning\s*-->",
    re.IGNORECASE,
)
_REASONING_START_TOKENS = ("<think", "<!--dify-deepseek-reasoning")


def _remove_partial_reasoning_start(value: str) -> str:
    lowered = value.lower()
    for token in _REASONING_START_TOKENS:
        max_length = min(len(token), len(value))
        for length in range(max_length, 0, -1):
            if lowered.endswith(token[:length]):
                return value[:-length]
    return value


def strip_reasoning(value: str) -> str:
    result = _COMPLETE_THINK_BLOCK.sub("", value)
    open_think = _OPEN_THINK_TAG.search(result)
    if open_think:
        result = result[: open_think.start()]

    result = _COMPLETE_DIFY_REASONING_BLOCK.sub("", result)
    open_marker = _OPEN_DIFY_REASONING_MARKER.search(result)
    if open_marker:
        result = result[: open_marker.start()]

    result = _REASONING_TAGS.sub("", result)
    return _remove_partial_reasoning_start(result).lstrip()


class _StreamingAnswerFilter:
    def __init__(self) -> None:
        self.raw_answer = ""
        self.visible_answer = ""

    def add(self, delta: str) -> str:
        self.raw_answer += delta
        next_visible = strip_reasoning(self.raw_answer)
        if next_visible.startswith(self.visible_answer):
            visible_delta = next_visible[len(self.visible_answer) :]
        else:
            visible_delta = next_visible
        self.visible_answer = next_visible
        return visible_delta


async def stream_chat(
    payload: ChatRequest,
    http: httpx.AsyncClient,
    settings: Settings,
) -> AsyncIterator[bytes]:
    if not settings.dify_api_key:
        raise DifyError("DIFY_API_KEY 尚未配置")

    longitude = payload.longitude if payload.longitude is not None else settings.default_longitude
    latitude = payload.latitude if payload.latitude is not None else settings.default_latitude
    request_body = {
        "inputs": {
            "longitude": f"{longitude:.6f}",
            "latitude": f"{latitude:.6f}",
            "coordinate_system": payload.coordinate_system,
            "location_accuracy": str(payload.accuracy or ""),
            "location_name": payload.position_name.strip()[:80],
            "fallback_location_name": settings.default_location_name,
        },
        "query": payload.query,
        "response_mode": "streaming",
        "conversation_id": payload.conversation_id,
        "user": payload.user,
        "files": [],
    }
    request = http.build_request(
        "POST",
        f"{settings.dify_api_base_url.rstrip('/')}/chat-messages",
        headers={
            "Authorization": f"Bearer {settings.dify_api_key}",
            "Content-Type": "application/json",
        },
        json=request_body,
    )
    response = await http.send(request, stream=True)
    if response.status_code >= 400:
        body = await response.aread()
        await response.aclose()
        try:
            detail = json.loads(body).get("message")
        except (json.JSONDecodeError, AttributeError):
            detail = body.decode("utf-8", errors="replace")
        raise DifyError(detail or f"Dify 返回 HTTP {response.status_code}")

    try:
        answer_filter = _StreamingAnswerFilter()
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                yield f"{line}\n".encode()
                continue

            raw_event = line.removeprefix("data:").lstrip()
            try:
                event = json.loads(raw_event)
            except json.JSONDecodeError:
                yield f"{line}\n".encode()
                continue

            if (
                event.get("event") in {"message", "agent_message"}
                and isinstance(event.get("answer"), str)
            ):
                event["answer"] = answer_filter.add(event["answer"])

            encoded_event = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            yield f"data: {encoded_event}\n".encode()
    finally:
        await response.aclose()
