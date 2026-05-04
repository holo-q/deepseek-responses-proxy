from deepseek_responses_proxy.protocol import (
    chat_completion_to_response,
    responses_payload_to_chat_payload,
    sse_events_for_response,
)
import unittest


class ProtocolTests(unittest.TestCase):
    def test_string_input_maps_to_user_message(self) -> None:
        chat, stats = responses_payload_to_chat_payload(
            {"model": "deepseek-v4-flash", "instructions": "be terse", "input": "hello"}
        )

        self.assertEqual(chat["model"], "deepseek-v4-flash")
        self.assertIs(chat["stream"], False)
        self.assertEqual(
            chat["messages"],
            [
                {"role": "system", "content": "be terse"},
                {"role": "user", "content": "hello"},
            ],
        )
        self.assertEqual(stats["messages"]["input_items"], 1)

    def test_responses_messages_and_function_tools_convert_to_chat_shape(self) -> None:
        chat, stats = responses_payload_to_chat_payload(
            {
                "model": "deepseek-v4-pro",
                "input": [
                    {"type": "message", "role": "developer", "content": "rules"},
                    {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "inspect"}]},
                    {"type": "reasoning", "summary": []},
                ],
                "tools": [
                    {
                        "type": "function",
                        "name": "read_file",
                        "description": "Read a file",
                        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                    },
                    {"type": "web_search_preview"},
                ],
            }
        )

        self.assertEqual(
            chat["messages"],
            [
                {"role": "system", "content": "rules"},
                {"role": "user", "content": "inspect"},
            ],
        )
        self.assertEqual(chat["tools"][0]["function"]["name"], "read_file")
        self.assertEqual(stats["messages"]["reasoning_items_dropped"], 1)
        self.assertEqual(stats["tools"]["forwarded_tools"], 1)
        self.assertEqual(stats["tools"]["dropped_tools"], 1)

    def test_custom_freeform_tools_convert_to_input_function_tools(self) -> None:
        chat, stats = responses_payload_to_chat_payload(
            {
                "model": "deepseek-v4-flash",
                "input": "patch the file",
                "tools": [
                    {
                        "type": "custom",
                        "name": "apply_patch",
                        "description": "Use the `apply_patch` tool to edit files.",
                        "format": {
                            "type": "grammar",
                            "syntax": "lark",
                            "definition": "start: /.+/",
                        },
                    }
                ],
            }
        )

        self.assertEqual(stats["tools"]["forwarded_tools"], 1)
        self.assertEqual(stats["tools"]["dropped_tools"], 0)
        self.assertEqual(chat["tools"][0]["type"], "function")
        function = chat["tools"][0]["function"]
        self.assertEqual(function["name"], "apply_patch")
        self.assertIn("custom/freeform", function["description"])
        self.assertEqual(function["parameters"]["required"], ["input"])
        self.assertFalse(function["parameters"]["additionalProperties"])

    def test_reasoning_content_replays_before_tool_calls(self) -> None:
        chat, stats = responses_payload_to_chat_payload(
            {
                "model": "deepseek-v4-flash",
                "input": [
                    {"type": "message", "role": "user", "content": "inspect"},
                    {
                        "type": "reasoning",
                        "content": [{"type": "reasoning_text", "text": "Need to read the file."}],
                    },
                    {
                        "type": "function_call",
                        "call_id": "call_123",
                        "name": "read_file",
                        "arguments": "{\"path\":\"README.md\"}",
                    },
                    {"type": "function_call_output", "call_id": "call_123", "output": "contents"},
                ],
            }
        )

        self.assertEqual(chat["messages"][1]["role"], "assistant")
        self.assertEqual(chat["messages"][1]["reasoning_content"], "Need to read the file.")
        self.assertEqual(chat["messages"][1]["tool_calls"][0]["id"], "call_123")
        self.assertEqual(stats["messages"]["reasoning_items_replayed"], 1)
        self.assertEqual(stats["messages"]["reasoning_items_dropped"], 0)

    def test_assistant_text_between_tool_call_and_output_merges_into_tool_call_message(self) -> None:
        chat, _stats = responses_payload_to_chat_payload(
            {
                "model": "deepseek-v4-pro",
                "input": [
                    {"type": "message", "role": "user", "content": "inspect"},
                    {
                        "type": "reasoning",
                        "content": [{"type": "reasoning_text", "text": "Need to read files."}],
                    },
                    {
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "read_file",
                        "arguments": "{\"path\":\"tests/test_simple.py\"}",
                    },
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Let me inspect the test."}],
                    },
                    {"type": "function_call_output", "call_id": "call_1", "output": "contents"},
                ],
            }
        )

        assistant = chat["messages"][1]
        self.assertEqual(assistant["role"], "assistant")
        self.assertEqual(assistant["content"], "Let me inspect the test.")
        self.assertEqual(assistant["reasoning_content"], "Need to read files.")
        self.assertEqual(assistant["tool_calls"][0]["id"], "call_1")
        self.assertEqual(chat["messages"][2]["role"], "tool")

    def test_chat_completion_maps_to_response_message_and_sse(self) -> None:
        response = chat_completion_to_response(
            {
                "model": "deepseek-v4-flash",
                "choices": [{"message": {"role": "assistant", "content": "DEEPSEEK_OK"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            }
        )

        self.assertEqual(response["status"], "completed")
        self.assertEqual(response["output_text"], "DEEPSEEK_OK")
        self.assertEqual(response["usage"], {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5})

        events = sse_events_for_response(response)
        self.assertEqual(events[0]["type"], "response.created")
        self.assertTrue(any(event["type"] == "response.output_text.delta" for event in events))
        self.assertEqual(events[-1]["type"], "response.completed")

    def test_tool_call_round_trip_shapes_are_preserved(self) -> None:
        response = chat_completion_to_response(
            {
                "model": "deepseek-v4-pro",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_123",
                                    "type": "function",
                                    "function": {"name": "read_file", "arguments": "{\"path\":\"README.md\"}"},
                                }
                            ],
                        }
                    }
                ],
            }
        )

        self.assertEqual(response["output"][0]["type"], "function_call")
        self.assertEqual(response["output"][0]["call_id"], "call_123")
        self.assertEqual(response["output"][0]["name"], "read_file")


if __name__ == "__main__":
    unittest.main()
