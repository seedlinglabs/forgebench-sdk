# Forgebench SDK

Official client SDKs for the [Forgebench](https://forgebench.ai) control plane —
the governed chokepoint for agent/LLM calls: auth, tenant isolation, budget
gating, tool/model authorization, and hash-chained audit metering, all before
a request reaches a provider.

Two SDKs, kept in sync from a private monorepo and released independently:

- [`sdk-py/`](./sdk-py) — Python client. See [`sdk-py/README.md`](./sdk-py/README.md).
- [`sdk-ts/`](./sdk-ts) — TypeScript client. See [`sdk-ts/README.md`](./sdk-ts/README.md).

## Install

```bash
# Python — pin a stable release tag
pip install "git+https://github.com/seedlinglabs/forgebench-sdk.git@py-v0.1.0#subdirectory=sdk-py"

# TypeScript — pin a stable release tag
npm install "github:seedlinglabs/forgebench-sdk#ts-v0.1.0&path:/sdk-ts"
```

See each SDK's own README for quick-start usage. Releases are listed under
[Releases](https://github.com/seedlinglabs/forgebench-sdk/releases) — `py-v*`
and `ts-v*` tags version each SDK independently.

## Channels

- `main` branch / non-prerelease tags — stable.
- `preview` branch / prerelease tags (`*-preview`) — latest build from active
  development; may change without notice.

## License

[Apache-2.0](./LICENSE)
