"""Check the provider request boundary without invoking a paid model."""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from bedrock import CACHE, Session


class RequestTests(unittest.TestCase):
    def session(self, model):
        session = Session.__new__(Session)
        session.model = model
        session.models = [model]
        session.usage = {"inputTokens": 0, "outputTokens": 0, "calls": 0}
        session.client = Mock()
        session.client.converse.return_value = {
            "output": {"message": {"role": "assistant", "content": [{"text": "done"}]}},
            "usage": {"inputTokens": 12, "outputTokens": 3}, "stopReason": "end_turn"}
        return session

    def test_sol_removes_cache_points_without_mutating_history(self):
        session = self.session("us.openai.gpt-6-sol")
        messages = [
            {"role": "assistant", "content": [{"reasoningContent": {"redactedContent": b"signature"}},
                {"toolUse": {"toolUseId": "call1", "name": "read_pages", "input": {}}}]},
            {"role": "user", "content": [{"toolResult": {"toolUseId": "call1", "content": [{"text": "page"}]}}, CACHE]},
        ]
        original = copy.deepcopy(messages)
        tool = {"name": "read_pages", "inputSchema": {"json": {"type": "object"}}}
        session.converse("instructions", messages, [tool], max_tokens=16000)
        sent = session.client.converse.call_args.kwargs
        self.assertEqual(sent["modelId"], "us.openai.gpt-6-sol")
        self.assertEqual(sent["system"], [{"text": "instructions"}])
        self.assertEqual(sent["toolConfig"]["tools"], [{"toolSpec": tool}])
        self.assertEqual(sent["messages"][0], original[0])
        self.assertEqual(sent["messages"][1]["content"], original[1]["content"][:-1])
        self.assertEqual(messages, original)
        self.assertEqual(sent["inferenceConfig"], {"maxTokens": 16000})
        self.assertEqual(session.usage, {"inputTokens": 12, "outputTokens": 3, "calls": 1})

    def test_explicit_claude_override_retains_cache_support(self):
        session = self.session("us.anthropic.claude-opus-5")
        session.converse("instructions", [{"role": "user", "content": [{"text": "hello"}, CACHE]}])
        sent = session.client.converse.call_args.kwargs
        self.assertEqual(sent["system"][-1], CACHE)
        self.assertEqual(sent["messages"][0]["content"][-1], CACHE)


if __name__ == "__main__":
    unittest.main()
