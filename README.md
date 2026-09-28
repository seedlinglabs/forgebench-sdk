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
# Python — always the latest stable release
pip install "git+https://github.com/seedlinglabs/forgebench-sdk.git#subdirectory=sdk-py"

# TypeScript — always the latest stable release
npm install "github:seedlinglabs/forgebench-sdk#path:/sdk-ts"
```

No `@ref` resolves to `main`, which only ever holds stable releases (never a
preview build) — this always installs current stable without needing to
know a version number. To pin an exact version instead, append `@py-vX.Y.Z`
/ `#ts-vX.Y.Z` — see [Releases](https://github.com/seedlinglabs/forgebench-sdk/releases)
for the current tags. See each SDK's own README for quick-start usage.

## Channels

- `main` branch / non-prerelease tags — stable.
- `preview` branch / prerelease tags (`*-preview`) — latest build from active
  development; may change without notice.

## License

[Apache-2.0](./LICENSE)
