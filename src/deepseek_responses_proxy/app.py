from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .protocol import chat_completion_to_response, responses_payload_to_chat_payload, sse_events_for_response


Json = dict[str, Any]


class ProxyConfig:
    def __init__(
        self,
        *,
        bind: str,
        port: int,
        chat_base_url: str,
        api_key_env: str,
        api_key_pass: str | None,
        trace_body: bool,
        timeout_sec: float,
    ) -> None:
        self.bind = bind
        self.port = port
        self.chat_base_url = chat_base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.api_key_pass = api_key_pass
        self.trace_body = trace_body
        self.timeout_sec = timeout_sec


def trace(event: str, **fields: Any) -> None:
    record = {"ts": time.time(), "event": event, **fields}
    print(json.dumps(record, sort_keys=True), file=sys.stderr, flush=True)


def sanitize_headers(headers: dict[str, str]) -> dict[str, str]:
    clean = dict(headers)
    for key in list(clean):
        if key.lower() in {"authorization", "cookie", "x-api-key"}:
            clean[key] = "<redacted>"
    return clean


class DeepSeekResponsesHandler(BaseHTTPRequestHandler):
    server_version = "deepseek-responses-proxy/0.1"

    def do_GET(self) -> None:
        if self.path in {"/health", "/v1/health"}:
            self._send_json({"status": "ok"})
            return
        if self.path in {"/models", "/v1/models"}:
            self._send_json(
                {
                    "object": "list",
                    "data": [
                        {"id": "deepseek-v4-pro", "object": "model"},
                        {"id": "deepseek-v4-flash", "object": "model"},
                    ],
                }
            )
            return
        self._send_json({"error": {"message": f"unknown path: {self.path}"}}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        request_id = uuid.uuid4().hex[:12]
        if self.path not in {"/responses", "/v1/responses"}:
            self._send_json({"error": {"message": f"unknown path: {self.path}"}}, status=HTTPStatus.NOT_FOUND)
            return

        try:
            payload = self._read_json()
            config: ProxyConfig = self.server.config  # type: ignore[attr-defined]
            trace(
                "request.received",
                request_id=request_id,
                path=self.path,
                model=payload.get("model"),
                stream=payload.get("stream", False),
                headers=sanitize_headers(dict(self.headers)),
                body=payload if config.trace_body else None,
            )
            response = handle_responses_request(payload, config, request_id)
            if payload.get("stream") is True:
                self._send_sse(response)
            else:
                self._send_json(response)
        except ProxyError as exc:
            trace("request.failed", request_id=request_id, status=exc.status, message=exc.message)
            self._send_json({"error": {"message": exc.message, "type": "proxy_error"}}, status=exc.status)
        except Exception as exc:  # pragma: no cover - defensive crash trace
            trace("request.crashed", request_id=request_id, message=str(exc), traceback=traceback.format_exc())
            self._send_json(
                {"error": {"message": "proxy crashed; see stderr trace", "type": "proxy_crash"}},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def log_message(self, format: str, *args: Any) -> None:
        trace("http.access", client=self.client_address[0], message=format % args)

    def _read_json(self) -> Json:
        length = int(self.headers.get("content-length", "0"))
        raw = self.rfile.read(length)
        if not raw:
            return {}
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ProxyError(HTTPStatus.BAD_REQUEST, "request body must be a JSON object")
        return value

    def _send_json(self, payload: Json, status: HTTPStatus = HTTPStatus.OK) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_sse(self, response: Json) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.end_headers()
        for event in sse_events_for_response(response):
            raw = f"data: {json.dumps(event)}\n\n".encode("utf-8")
            self.wfile.write(raw)
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


class ProxyError(Exception):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def handle_responses_request(payload: Json, config: ProxyConfig, request_id: str) -> Json:
    chat_payload, conversion_stats = responses_payload_to_chat_payload(payload)
    trace(
        "request.converted",
        request_id=request_id,
        stats=conversion_stats,
        upstream_model=chat_payload.get("model"),
        body=chat_payload if config.trace_body else None,
    )
    chat = call_upstream_chat(chat_payload, config, request_id)
    response = chat_completion_to_response(chat, request_model=chat_payload.get("model"))
    trace(
        "response.converted",
        request_id=request_id,
        output_items=len(response.get("output", [])),
        output_text_len=len(response.get("output_text", "")),
        usage=response.get("usage"),
        body=response if config.trace_body else None,
    )
    return response


def call_upstream_chat(chat_payload: Json, config: ProxyConfig, request_id: str) -> Json:
    api_key = resolve_api_key(config, request_id)

    url = f"{config.chat_base_url}/chat/completions"
    raw_payload = json.dumps(chat_payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=raw_payload,
        headers={
            "authorization": f"Bearer {api_key}",
            "content-type": "application/json",
            "accept": "application/json",
        },
        method="POST",
    )
    trace("upstream.start", request_id=request_id, url=url, bytes=len(raw_payload))
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=config.timeout_sec) as response:
            body = response.read()
            elapsed_ms = int((time.time() - started) * 1000)
            trace("upstream.done", request_id=request_id, status=response.status, bytes=len(body), elapsed_ms=elapsed_ms)
            value = json.loads(body)
            if not isinstance(value, dict):
                raise ProxyError(HTTPStatus.BAD_GATEWAY, "upstream returned non-object JSON")
            return value
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        trace("upstream.error", request_id=request_id, status=exc.code, body=body[:2000])
        raise ProxyError(HTTPStatus.BAD_GATEWAY, f"upstream HTTP {exc.code}: {body[:1000]}") from exc
    except urllib.error.URLError as exc:
        trace("upstream.network_error", request_id=request_id, reason=str(exc.reason))
        raise ProxyError(HTTPStatus.BAD_GATEWAY, f"upstream network error: {exc.reason}") from exc


def resolve_api_key(config: ProxyConfig, request_id: str) -> str:
    api_key = os.environ.get(config.api_key_env)
    if api_key:
        trace("credential.source", request_id=request_id, source="env", env=config.api_key_env)
        return api_key

    if config.api_key_pass:
        trace("credential.lookup", request_id=request_id, source="pass", entry=config.api_key_pass)
        try:
            completed = subprocess.run(
                ["pass", "show", config.api_key_pass],
                check=False,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=10,
            )
        except FileNotFoundError as exc:
            trace(
                "credential.failed",
                request_id=request_id,
                source="pass",
                entry=config.api_key_pass,
                reason="pass_not_found",
            )
            raise ProxyError(
                HTTPStatus.UNAUTHORIZED,
                f"missing API key: set ${config.api_key_env} or install pass and create {config.api_key_pass}",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            trace(
                "credential.failed",
                request_id=request_id,
                source="pass",
                entry=config.api_key_pass,
                reason="timeout",
            )
            raise ProxyError(
                HTTPStatus.UNAUTHORIZED,
                f"missing API key: set ${config.api_key_env} or fix pass entry {config.api_key_pass} lookup timeout",
            ) from exc

        first_line = completed.stdout.splitlines()[0].strip() if completed.stdout.splitlines() else ""
        if completed.returncode == 0 and first_line:
            trace("credential.source", request_id=request_id, source="pass", entry=config.api_key_pass)
            return first_line

        trace(
            "credential.failed",
            request_id=request_id,
            source="pass",
            entry=config.api_key_pass,
            returncode=completed.returncode,
            stderr=completed.stderr.strip()[:500],
        )

    attempted = f"${config.api_key_env}"
    if config.api_key_pass:
        attempted = f"{attempted} or pass:{config.api_key_pass}"
    raise ProxyError(HTTPStatus.UNAUTHORIZED, f"missing API key: set {attempted}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Codex Responses API shim for OpenAI Chat Completions upstreams")
    parser.add_argument("--bind", default=os.environ.get("DEEPSEEK_PROXY_BIND", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("DEEPSEEK_PROXY_PORT", "8787")))
    parser.add_argument(
        "--chat-base-url",
        "--deepseek-base-url",
        dest="chat_base_url",
        default=os.environ.get("CHAT_COMPLETIONS_BASE_URL", os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")),
    )
    parser.add_argument("--api-key-env", default=os.environ.get("DEEPSEEK_PROXY_API_KEY_ENV", "DEEPSEEK_API_KEY"))
    parser.add_argument("--api-key-pass", default=os.environ.get("DEEPSEEK_PROXY_API_KEY_PASS", "api-keys/deepseek"))
    parser.add_argument("--timeout-sec", type=float, default=float(os.environ.get("DEEPSEEK_PROXY_TIMEOUT_SEC", "180")))
    parser.add_argument("--trace-body", action="store_true", default=os.environ.get("DEEPSEEK_PROXY_TRACE_BODY") == "1")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    config = ProxyConfig(
        bind=args.bind,
        port=args.port,
        chat_base_url=args.chat_base_url,
        api_key_env=args.api_key_env,
        api_key_pass=args.api_key_pass,
        trace_body=args.trace_body,
        timeout_sec=args.timeout_sec,
    )
    server = ThreadingHTTPServer((config.bind, config.port), DeepSeekResponsesHandler)
    server.config = config  # type: ignore[attr-defined]
    trace(
        "server.start",
        bind=config.bind,
        port=config.port,
        chat_base_url=config.chat_base_url,
        api_key_env=config.api_key_env,
        api_key_pass=config.api_key_pass,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        trace("server.stop", reason="keyboard_interrupt")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
