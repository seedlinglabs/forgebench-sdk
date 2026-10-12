# Forgebench SDK

Official client SDKs for the [Forgebench](https://forgebench.ai) control plane —
the governed chokepoint for agent/LLM calls: auth, tenant isolation, budget
gating, tool/model authorization, and hash-chained audit metering, all before
a request reaches a provider.

Three SDKs, kept in sync from a private monorepo and released independently:

- [`sdk-py/`](./sdk-py) — Python client. See [`sdk-py/README.md`](./sdk-py/README.md).
- [`sdk-ts/`](./sdk-ts) — TypeScript client. See [`sdk-ts/README.md`](./sdk-ts/README.md).
- [`sdk-java/`](./sdk-java) — Java client (Java 11+). See [`sdk-java/README.md`](./sdk-java/README.md).

## Install

```bash
# Python — always the latest stable release
pip install "git+https://github.com/seedlinglabs/forgebench-sdk.git#subdirectory=sdk-py"

# TypeScript — a release's prebuilt package (npm, pnpm or yarn)
npm install https://github.com/seedlinglabs/forgebench-sdk/releases/download/ts-v1.0.0/forgebench-sdk-ts.tgz
```

**Python:** no `@ref` resolves to `main`, which only ever holds stable
releases (never a preview build), so this always installs current stable.
Pin an exact version with `@py-vX.Y.Z`.

**TypeScript:** every `ts-v*` release carries a prebuilt
`forgebench-sdk-ts.tgz`; the command above installs `ts-v1.0.0`, the
current stable. Swap the tag for any other release, for example
`ts-v1.2.0-preview`, and use `pnpm add` or `yarn add` with the same URL if
you prefer. Installing straight from git (`github:…#path:/sdk-ts`) does not
work: npm cannot install a subfolder of a repository, and pnpm 10+ refuses to
build a git dependency.

**Java** always names its tag, `java-vX.Y.Z` (below). See
[Releases](https://github.com/seedlinglabs/forgebench-sdk/releases) for the
current tags, and each SDK's own README for quick-start usage.

### Java

Java 11+. [JitPack](https://jitpack.io) builds the SDK from this repo, so add its repository and the dependency.

Maven:

```xml
<repositories>
  <repository><id>jitpack.io</id><url>https://jitpack.io</url></repository>
</repositories>

<dependency>
  <groupId>com.github.seedlinglabs</groupId>
  <artifactId>forgebench-sdk</artifactId>
  <version>main-SNAPSHOT</version> <!-- always the latest stable; pin an exact release with java-vX.Y.Z -->
</dependency>
```

Gradle:

```groovy
repositories { maven { url 'https://jitpack.io' } }
dependencies { implementation 'com.github.seedlinglabs:forgebench-sdk:main-SNAPSHOT' }
```

`main-SNAPSHOT` is the Java equivalent of the no-ref `pip`/`npm` commands above: it resolves to the latest stable.
For a reproducible build, pin a release tag instead (for example `java-v1.0.0`) — see
[Releases](https://github.com/seedlinglabs/forgebench-sdk/releases).

## Channels

- `main` branch / non-prerelease tags — stable.
- `preview` branch / prerelease tags (`*-preview`) — latest build from active
  development; may change without notice.

## License

[Apache-2.0](./LICENSE)
