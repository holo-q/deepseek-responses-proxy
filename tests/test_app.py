import os
import subprocess
import unittest
from http import HTTPStatus
from unittest import mock

from deepseek_responses_proxy.app import ProxyConfig, ProxyError, resolve_api_key


def make_config() -> ProxyConfig:
    return ProxyConfig(
        bind="127.0.0.1",
        port=8787,
        chat_base_url="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        api_key_pass="api-keys/deepseek",
        trace_body=False,
        timeout_sec=1,
    )


class CredentialTests(unittest.TestCase):
    def test_env_key_wins_without_pass_lookup(self) -> None:
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "env-key"}, clear=True):
            with mock.patch("deepseek_responses_proxy.app.subprocess.run") as run:
                self.assertEqual(resolve_api_key(make_config(), "req"), "env-key")

        run.assert_not_called()

    def test_pass_lookup_uses_first_line(self) -> None:
        completed = subprocess.CompletedProcess(
            ["pass", "show", "api-keys/deepseek"],
            0,
            stdout="pass-key\nmetadata\n",
            stderr="",
        )
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("deepseek_responses_proxy.app.subprocess.run", return_value=completed) as run:
                self.assertEqual(resolve_api_key(make_config(), "req"), "pass-key")

        run.assert_called_once()

    def test_missing_key_names_env_and_pass_entry(self) -> None:
        completed = subprocess.CompletedProcess(
            ["pass", "show", "api-keys/deepseek"],
            1,
            stdout="",
            stderr="api-keys/deepseek is not in the password store",
        )
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("deepseek_responses_proxy.app.subprocess.run", return_value=completed):
                with self.assertRaises(ProxyError) as ctx:
                    resolve_api_key(make_config(), "req")

        self.assertEqual(ctx.exception.status, HTTPStatus.UNAUTHORIZED)
        self.assertIn("$DEEPSEEK_API_KEY", ctx.exception.message)
        self.assertIn("pass:api-keys/deepseek", ctx.exception.message)


if __name__ == "__main__":
    unittest.main()
