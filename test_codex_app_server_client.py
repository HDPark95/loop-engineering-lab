"""Pure protocol-reduction tests for the Codex app-server measurement client."""

from __future__ import annotations

import unittest

import codex_app_server_client


class AppServerEvidenceTest(unittest.TestCase):
    def test_effective_model_usage_and_structured_report_are_retained(self):
        events = [
            {
                "method": "thread/settings/updated",
                "params": {
                    "threadSettings": {
                        "model": "gpt-5.6-terra",
                        "effort": "medium",
                    }
                },
            },
            {
                "method": "thread/tokenUsage/updated",
                "params": {
                    "tokenUsage": {
                        "total": {
                            "inputTokens": 100,
                            "cachedInputTokens": 20,
                            "outputTokens": 7,
                        }
                    }
                },
            },
            {
                "method": "item/completed",
                "params": {
                    "item": {
                        "type": "agentMessage",
                        "text": '{"improved":true,"confidence":0.8,"evidence":"tests pass"}',
                    }
                },
            },
            {
                "method": "turn/completed",
                "params": {"turn": {"id": "turn-1", "status": "completed"}},
            },
        ]
        result = codex_app_server_client.summarize(
            "gpt-5.6-terra",
            {"model": "gpt-5.6-terra", "reasoningEffort": "medium"},
            events,
        )
        self.assertEqual(result["model_served"], "gpt-5.6-terra")
        self.assertEqual(
            result["model_identity_evidence"],
            "app_server_effective_model_no_reroute",
        )
        self.assertEqual(result["reasoning_effort_served"], "medium")
        self.assertEqual(result["usage"]["input_tokens"], 100)
        self.assertTrue(result["self_report"]["improved"])

    def test_reroute_reports_the_actual_destination_model(self):
        result = codex_app_server_client.summarize(
            "gpt-5.6-terra",
            {"model": "gpt-5.6-terra"},
            [
                {
                    "method": "model/rerouted",
                    "params": {
                        "fromModel": "gpt-5.6-terra",
                        "toModel": "gpt-5.6-luna",
                        "reason": "capacity",
                    },
                }
            ],
        )
        self.assertEqual(result["model_served"], "gpt-5.6-luna")
        self.assertEqual(result["model_identity_evidence"], "app_server_model_rerouted")


if __name__ == "__main__":
    unittest.main()
