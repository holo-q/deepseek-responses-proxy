# Security

`deepseek-responses-proxy` is intended to run as a local adapter between Codex
and an upstream Chat Completions API.

## Secrets

The proxy never needs credentials committed to the repository. It resolves the
upstream API key in this order:

1. The configured environment variable, defaulting to `DEEPSEEK_API_KEY`.
2. The configured `pass` entry, defaulting to `api-keys/deepseek`.

Do not enable `DEEPSEEK_PROXY_TRACE_BODY=1` when prompts, tool outputs, or
credentials may contain sensitive data. Body tracing is for local debugging.

## Network exposure

Bind to `127.0.0.1` unless you have a deliberate reason to expose the proxy.
Codex should talk to the local `/v1/responses` endpoint, and the proxy should
be the only process that talks to the upstream API with the real provider key.

## Reports

Open a private security advisory or contact the maintainers before publishing a
bug report that includes credentials, prompts, tool outputs, or request traces.
