from __future__ import annotations

import time
import uuid
from typing import Any


Json = dict[str, Any]


def new_response_id() -> str:
    return f"resp_{uuid.uuid4().hex}"


def now_unix() -> int:
    return int(time.time())


def flatten_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                parts.append(str(item))
                continue
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
                continue
            # Responses sometimes distinguishes input_text/output_text by type
            # while keeping the text payload under the same key.
            if item.get("type") in {"input_text", "output_text"}:
                parts.append(str(item.get("text", "")))
        return "\n".join(part for part in parts if part)
    return str(content)


def responses_input_to_chat_messages(payload: Json) -> tuple[list[Json], Json]:
    messages: list[Json] = []
    stats: Json = {
        "input_items": 0,
        "reasoning_items_dropped": 0,
        "function_outputs": 0,
        "function_calls_replayed": 0,
    }

    instructions = payload.get("instructions")
    if isinstance(instructions, str) and instructions:
        messages.append({"role": "system", "content": instructions})

    input_value = payload.get("input", "")
    if isinstance(input_value, str):
        stats["input_items"] = 1
        messages.append({"role": "user", "content": input_value})
        return messages, stats

    if not isinstance(input_value, list):
        messages.append({"role": "user", "content": flatten_content(input_value)})
        stats["input_items"] = 1
        return messages, stats

    stats["input_items"] = len(input_value)
    pending_assistant_tool_calls: list[Json] = []
    for item in input_value:
        if isinstance(item, str):
            messages.append({"role": "user", "content": item})
            continue
        if not isinstance(item, dict):
            messages.append({"role": "user", "content": str(item)})
            continue

        item_type = item.get("type")
        if item_type == "reasoning":
            stats["reasoning_items_dropped"] += 1
            continue

        if item_type == "function_call":
            pending_assistant_tool_calls.append(
                {
                    "id": item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex}",
                    "type": "function",
                    "function": {
                        "name": item.get("name", ""),
                        "arguments": item.get("arguments", "{}"),
                    },
                }
            )
            stats["function_calls_replayed"] += 1
            continue

        if item_type == "function_call_output":
            if pending_assistant_tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": pending_assistant_tool_calls,
                    }
                )
                pending_assistant_tool_calls = []
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": item.get("call_id") or item.get("id") or "",
                    "content": flatten_content(item.get("output", "")),
                }
            )
            stats["function_outputs"] += 1
            continue

        role = item.get("role", "user")
        if role == "developer":
            role = "system"
        if role not in {"system", "user", "assistant", "tool"}:
            role = "user"
        message: Json = {"role": role, "content": flatten_content(item.get("content", ""))}
        if role == "tool" and item.get("tool_call_id"):
            message["tool_call_id"] = item["tool_call_id"]
        messages.append(message)

    if pending_assistant_tool_calls:
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": pending_assistant_tool_calls,
            }
        )

    if not messages:
        messages.append({"role": "user", "content": ""})
    return messages, stats


def responses_tools_to_chat_tools(tools: Any) -> tuple[list[Json] | None, Json]:
    stats: Json = {"input_tools": 0, "forwarded_tools": 0, "dropped_tools": 0}
    if not isinstance(tools, list):
        return None, stats

    stats["input_tools"] = len(tools)
    chat_tools: list[Json] = []
    for tool in tools:
        if not isinstance(tool, dict):
            stats["dropped_tools"] += 1
            continue
        if tool.get("type") != "function":
            stats["dropped_tools"] += 1
            continue

        function = tool.get("function")
        if isinstance(function, dict):
            chat_tools.append({"type": "function", "function": function})
            stats["forwarded_tools"] += 1
            continue

        name = tool.get("name")
        if not isinstance(name, str) or not name:
            stats["dropped_tools"] += 1
            continue
        chat_tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool.get("description", ""),
                    "parameters": tool.get("parameters", {"type": "object", "properties": {}}),
                },
            }
        )
        stats["forwarded_tools"] += 1

    if not chat_tools:
        return None, stats
    return chat_tools, stats


def responses_payload_to_chat_payload(payload: Json) -> tuple[Json, Json]:
    messages, message_stats = responses_input_to_chat_messages(payload)
    tools, tool_stats = responses_tools_to_chat_tools(payload.get("tools"))

    chat_payload: Json = {
        "model": payload.get("model", "deepseek-v4-flash"),
        "messages": messages,
        "stream": False,
    }
    if tools is not None:
        chat_payload["tools"] = tools
        if payload.get("tool_choice") is not None:
            chat_payload["tool_choice"] = payload["tool_choice"]

    if payload.get("temperature") is not None:
        chat_payload["temperature"] = payload["temperature"]
    if payload.get("top_p") is not None:
        chat_payload["top_p"] = payload["top_p"]
    if payload.get("max_output_tokens") is not None:
        chat_payload["max_tokens"] = payload["max_output_tokens"]

    return chat_payload, {"messages": message_stats, "tools": tool_stats}


def chat_completion_to_response(chat: Json, request_model: str | None = None) -> Json:
    response_id = new_response_id()
    model = chat.get("model") or request_model or "deepseek"
    choice = _first_choice(chat)
    message = choice.get("message", {}) if isinstance(choice, dict) else {}
    output = chat_message_to_response_output(message)
    return {
        "id": response_id,
        "object": "response",
        "created_at": now_unix(),
        "status": "completed",
        "model": model,
        "output": output,
        "output_text": output_text_from_items(output),
        "usage": normalize_usage(chat.get("usage")),
    }


def chat_message_to_response_output(message: Json) -> list[Json]:
    output: list[Json] = []
    reasoning = message.get("reasoning_content")
    if isinstance(reasoning, str) and reasoning:
        output.append(
            {
                "type": "reasoning",
                "id": f"rs_{uuid.uuid4().hex}",
                "summary": [],
                "content": [{"type": "reasoning_text", "text": reasoning}],
                "status": "completed",
            }
        )

    for tool_call in message.get("tool_calls") or []:
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function") or {}
        output.append(
            {
                "type": "function_call",
                "id": f"fc_{uuid.uuid4().hex}",
                "call_id": tool_call.get("id") or f"call_{uuid.uuid4().hex}",
                "name": function.get("name", ""),
                "arguments": function.get("arguments", "{}"),
                "status": "completed",
            }
        )

    content = message.get("content")
    if isinstance(content, str) and content:
        output.append(
            {
                "type": "message",
                "id": f"msg_{uuid.uuid4().hex}",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": content, "annotations": []}],
            }
        )

    if not output:
        output.append(
            {
                "type": "message",
                "id": f"msg_{uuid.uuid4().hex}",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "", "annotations": []}],
            }
        )
    return output


def output_text_from_items(items: list[Json]) -> str:
    parts: list[str] = []
    for item in items:
        if item.get("type") != "message":
            continue
        parts.append(flatten_content(item.get("content", [])))
    return "".join(parts)


def normalize_usage(usage: Any) -> Json | None:
    if not isinstance(usage, dict):
        return None
    input_tokens = usage.get("prompt_tokens", usage.get("input_tokens", 0))
    output_tokens = usage.get("completion_tokens", usage.get("output_tokens", 0))
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": usage.get("total_tokens", input_tokens + output_tokens),
    }


def sse_events_for_response(response: Json) -> list[Json]:
    response_header = {k: v for k, v in response.items() if k != "output"}
    response_header["output"] = []
    events: list[Json] = [{"type": "response.created", "response": response_header}]

    for output_index, item in enumerate(response.get("output", [])):
        events.append({"type": "response.output_item.added", "output_index": output_index, "item": item})
        if item.get("type") == "message":
            for content_index, part in enumerate(item.get("content", [])):
                events.append(
                    {
                        "type": "response.content_part.added",
                        "item_id": item.get("id"),
                        "output_index": output_index,
                        "content_index": content_index,
                        "part": {**part, "text": ""},
                    }
                )
                text = part.get("text", "")
                if text:
                    events.append(
                        {
                            "type": "response.output_text.delta",
                            "item_id": item.get("id"),
                            "output_index": output_index,
                            "content_index": content_index,
                            "delta": text,
                        }
                    )
                events.append(
                    {
                        "type": "response.content_part.done",
                        "item_id": item.get("id"),
                        "output_index": output_index,
                        "content_index": content_index,
                        "part": part,
                    }
                )
        events.append({"type": "response.output_item.done", "output_index": output_index, "item": item})

    events.append({"type": "response.completed", "response": response})
    return events


def _first_choice(chat: Json) -> Json:
    choices = chat.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        return choices[0]
    return {}

