# DeepSeek Responses Proxy

Local adapter for the awkward boundary between current Codex custom providers
and any upstream that speaks OpenAI-style Chat Completions. It ships with
DeepSeek V4 defaults because that is the first target we needed to hook into
Codex.

Codex `0.128.0` rejects `wire_api = "chat"` and expects Responses-shaped custom
providers. DeepSeek V4 officially exposes OpenAI Chat Completions and Anthropic
formats. This proxy owns that mismatch in one traceable process:

```text
Codex /responses
  -> deepseek-responses-proxy
      -> DeepSeek /chat/completions
```

## Run

```bash
pass insert api-keys/deepseek
uv run deepseek-responses-proxy --port 8787
```

The key source order is:

1. `$DEEPSEEK_API_KEY`
2. `pass show api-keys/deepseek`

Use `--api-key-env`, `--api-key-pass`, and `--chat-base-url` to point the
same adapter at another OpenAI Chat Completions upstream.

Point Codex at:

```toml
[model_providers.deepseek]
name = "DeepSeek"
base_url = "http://127.0.0.1:8787/v1"
experimental_bearer_token = "codex-deepseek-local"
wire_api = "responses"
```

The proxy accepts both `/responses` and `/v1/responses`.

## Spaceship Daemon

Spaceship owns the user service:

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

## Scope

Implemented now:

- Responses `input` to chat `messages`
- `instructions` / `developer` mapped into system messages
- function tool schema passthrough where possible
- non-streaming JSON Responses output
- synthesized SSE for `stream: true`

Known limits:

- Hosted Responses tools are dropped; only function schemas are forwarded.
- SSE is synthesized after a non-streaming upstream call, so it is compatible
  with streaming clients but not token-realtime yet.
- Complex multi-turn tool-call replay is best-effort until Codex/DeepSeek
  request traces prove the exact shapes needed.
