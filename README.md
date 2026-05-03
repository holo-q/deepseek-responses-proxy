# DeepSeek Responses Proxy

[![CI](https://github.com/holo-q/deepseek-responses-proxy/actions/workflows/ci.yml/badge.svg)](https://github.com/holo-q/deepseek-responses-proxy/actions/workflows/ci.yml)

Local adapter for the awkward boundary between current Codex custom providers
and an upstream that speaks OpenAI-style Chat Completions. It ships with
DeepSeek V4 defaults because that was the first target that needed a bridge,
but the upstream URL and API-key source are configurable.

Codex `0.128.0` rejects `wire_api = "chat"` and expects Responses-shaped custom
providers. DeepSeek V4 officially exposes OpenAI Chat Completions and Anthropic
formats. This proxy owns that mismatch in one traceable local process:

```text
Codex /responses
  -> deepseek-responses-proxy
      -> DeepSeek /chat/completions
```

## Status

This is alpha infrastructure for local Codex custom-provider experiments. It is
known to work for DeepSeek V4 Pro and DeepSeek V4 Flash through Codex custom
providers, including the reasoning replay shape DeepSeek requires when a
thinking-mode response includes tool calls.

Implemented now:

- Responses `input` to chat `messages`
- `instructions` and `developer` mapped into system messages
- function tool schema passthrough where possible
- DeepSeek `reasoning_content` replay across tool-call turns
- non-streaming JSON Responses output
- synthesized SSE for `stream: true`
- local health and model-list endpoints

Known limits:

- Hosted Responses tools are dropped; only function schemas are forwarded.
- SSE is synthesized after a non-streaming upstream call, so it is compatible
  with streaming clients but not token-realtime yet.
- The bridge follows the request shapes observed from Codex and DeepSeek V4; new
  Codex protocol changes should be captured as tests before widening behavior.

## Install

From a checkout:

```bash
uv sync
uv run deepseek-responses-proxy --help
```

As an executable package:

```bash
uv tool install git+https://github.com/holo-q/deepseek-responses-proxy
```

## Run Locally

```bash
pass insert api-keys/deepseek
uv run deepseek-responses-proxy --port 8787
```

The key source order is:

1. `$DEEPSEEK_API_KEY`
2. `pass show api-keys/deepseek`

Use `--api-key-env`, `--api-key-pass`, and `--chat-base-url` to point the
same adapter at another OpenAI Chat Completions upstream.

The proxy accepts both `/responses` and `/v1/responses`.

## Codex Provider

Point Codex at the local Responses endpoint:

```toml
[model_providers.deepseek]
name = "DeepSeek"
base_url = "http://127.0.0.1:8787/v1"
experimental_bearer_token = "codex-deepseek-local"
wire_api = "responses"
```

Example profiles:

```toml
[profiles.deepseek-v4-pro]
model_provider = "deepseek"
model = "deepseek-v4-pro"
model_context_window = 1000000
approval_policy = "untrusted"
sandbox_mode = "workspace-write"

[profiles.deepseek-v4-flash]
model_provider = "deepseek"
model = "deepseek-v4-flash"
model_context_window = 1000000
approval_policy = "untrusted"
sandbox_mode = "workspace-write"
```

The sandbox settings above are intentionally conservative for early testing:
allow edits inside the current workspace while requiring observation for broader
bash and tool use.

## Spaceship Daemon

In the Holo-Q Spaceship, the user service owns process lifetime:

```bash
spaceship start deepseek-responses-proxy
spaceship status deepseek-responses-proxy
spaceship logs deepseek-responses-proxy
```

The unit is installed at:

```text
~/Workspace/Daemons/deepseek-responses-proxy.service
```

Secrets stay outside git. The daemon reads the upstream API key from pass:

```bash
pass insert api-keys/deepseek
```

Then restart:

```bash
spaceship restart deepseek-responses-proxy
```

## Trace

Every request emits compact JSON lines on stderr. Set
`DEEPSEEK_PROXY_TRACE_BODY=1` only for local debugging when request bodies are
safe to inspect.

Important trace events:

- `server.start`
- `request.received`
- `request.converted`
- `credential.source`
- `upstream.start`
- `upstream.done`
- `response.converted`
- `request.failed`

## Development

```bash
uv run python -m unittest discover -s tests -v
uvx ruff check
uv build
```

## Publishing

See [PUBLISHING.md](PUBLISHING.md). The repository intentionally does not
declare an open-source license yet.
