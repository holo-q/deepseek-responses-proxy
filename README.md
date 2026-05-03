# DeepSeek Responses Proxy

Local adapter for the awkward boundary between current Codex custom providers
and DeepSeek V4.

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
export DEEPSEEK_API_KEY=sk-...
uv run deepseek-responses-proxy --port 8787
```

Point Codex at:

```toml
[model_providers.deepseek]
name = "DeepSeek"
base_url = "http://127.0.0.1:8787/v1"
env_key = "DEEPSEEK_API_KEY"
wire_api = "responses"
```

The proxy accepts both `/responses` and `/v1/responses`.

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

