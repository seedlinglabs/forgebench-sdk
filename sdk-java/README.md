# Forgebench Java SDK

Official Java client for the **Forgebench** control plane. A thin wrapper over
the api-key (`sk_...`) surface, built on the JDK's own `java.net.http`
client with a single dependency ([Jackson](https://github.com/FasterXML/jackson)).

Every call rides the **governed chokepoint**: the request is authenticated,
tenant-isolated via Postgres Row-Level Security, **budget-gated before any
provider call** (an over-budget request returns HTTP `402` *before* tokens are
spent), then metered and written to a per-tenant hash-chained audit log. The SDK
surfaces that 402 as a typed `BudgetExceededException`.

Requires Java 11+.

## Install

Not published to Maven Central — install from the public SDK repo through
[JitPack](https://jitpack.io), which builds it from the release tag:

```xml
<repositories>
  <repository>
    <id>jitpack.io</id>
    <url>https://jitpack.io</url>
  </repository>
</repositories>

<dependency>
  <groupId>com.github.seedlinglabs</groupId>
  <artifactId>forgebench-sdk</artifactId>
  <version>java-v1.0.0</version>
</dependency>
```

Gradle: `maven { url 'https://jitpack.io' }` and
`implementation 'com.github.seedlinglabs:forgebench-sdk:java-v1.0.0'`. Pick the
version from [Releases](https://github.com/seedlinglabs/forgebench-sdk/releases)
(`java-vX.Y.Z`).

Use `main-SNAPSHOT` as the version to follow the latest stable instead of pinning a tag
(what the Python/TypeScript no-ref installs do); a tag is reproducible.

Or build from source into your local Maven repository:

```bash
git clone https://github.com/seedlinglabs/forgebench-sdk.git
cd forgebench-sdk/sdk-java && ./mvnw install     # then depend on ai.forgebench:forgebench-sdk:<version>
```

Requires Java 11+.

## Get an API key

Sign up at [forgebench.ai](https://forgebench.ai) and create an `sk_...` key
from the console (Settings → API Keys). Agent-only endpoints (`agentTools()`,
the callee side of `agents()`) need a key issued to a registered agent.

## Quick start

```java
import ai.forgebench.*;
import ai.forgebench.types.ChatCompletion;

// apiKey defaults to $FORGEBENCH_API_KEY; baseUrl to $FORGEBENCH_BASE_URL
// (falling back to the production control plane, https://api.forgebench.ai).
// Use .baseUrl("http://localhost:8000") to run against a local stack.
Forgebench client = Forgebench.builder().apiKey("sk_...").build();

ChatCompletion resp = client.chat().completions().create(
    ChatCompletionRequest.builder()
        .model("mock-gpt")
        .addMessage(ChatMessage.user("Prove the governed path works."))
        .build());

System.out.println(resp.getContent());
System.out.println(resp.getUsage().getTotalTokens());
```

The client is thread-safe — build one and share it.

### Streaming

```java
try (ChatCompletionStream stream = client.chat().completions().createStream(req)) {
    for (ChatCompletionChunk chunk : stream) {
        String delta = chunk.getChoices().get(0).getDelta().getContent();
        if (delta != null) System.out.print(delta);
    }
}
```

Each read waits at most `timeout` (default 60s), and the whole stream is
capped by `streamTimeout` (default 300s), so a connection that stays open but
stops making progress is still closed out.

### Call lineage (who called what)

Every governed response carries a `call_id` — this call's row on the control
plane's ledger. Pass it as `parentCallId` on the calls it *causes* and the
ledger records them as children: cost rolls up to the root, and the console
draws the run as a tree.

```java
ChatCompletion plan = client.chat().completions().create(req);

ChatCompletion step = client.chat().completions().create(
    next.toBuilder().parentCallId(plan.getCallId()).build());
```

On the streaming path the id rides the `X-Call-Id` response header:
`stream.getCallId()`. `traceId(...)` groups the calls of one run
(`Trace.newTraceId()` mints one); `parentCallId` structures them. Neither ever
affects whether a call is allowed or what it costs.

### Tool calls in the chat loop

Pass an OpenAI `tools` array; the control plane gates the model's `tool_calls`
against this agent's allowlist, and only allowed ones come back. Echo them into
the history and answer each with a `tool` message:

```java
ChatCompletion turn = client.chat().completions().create(
    req.toBuilder().tools(tools).build());
List<Map<String, Object>> calls = turn.getChoices().get(0).getMessage().getToolCalls();

ChatCompletionRequest.Builder next = req.toBuilder()
    .addMessage(ChatMessage.assistant(null, calls))
    .parentCallId(turn.getCallId());
for (Map<String, Object> call : calls) {
    next.addMessage(ChatMessage.tool((String) call.get("id"), runMyTool(call)));
}
```

Any other OpenAI body field (`tool_choice`, `response_format`, ...) goes through
`extraBody(...)`. Messages can also be plain `Map`s in the wire shape.

### Tools, through the door

The control plane gates a model's `tool_calls` against this agent's allowlist;
your own MCP client executes them. Build the model's tool list from the live
allowlist, run what the gate let through, and report each outcome onto the
decision row it was gated on:

```java
List<Map<String, Object>> tools = client.agentTools().openaiSchema();   // GET /v1/agent-tools
ChatCompletion turn = client.chat().completions().create(req.toBuilder().tools(tools).build());

List<Map<String, Object>> toolMsgs = client.agentTools().dispatch(
    turn.getChoices().get(0).getMessage().getToolCalls(),
    (name, args) -> myMcp.call(name, args),
    turn.getCallId());
// append ChatMessage.assistant(null, toolCalls) and toolMsgs, then the next turn
```

`report(callId, toolName, result, error, latencyMs)` records one outcome by hand.

### Agent → agent (A2A tasks)

An agent bound to another by an operator can open a task on it through the
door — the callee runs wherever it runs, and its own calls hang under the task:

```java
Task task = client.agents().call("qp-research", CallParams.text("10 MCQs on photosynthesis")
    .data(Map.of("grade", 9)).parentCallId(plan.getCallId()).build());
if (task.getState().equals("input_required")) {          // the callee asked a question
    task = client.agents().call("qp-research", CallParams.text("CBSE").contextId(task.getContextId()).build());
}
String notes = task.artifactText();
```

Still `working` after the wait (30s by default)? Poll it:
`client.agents().task("qp-research", task.getId(), Duration.ofSeconds(20))`, or
`cancel(...)` it.

And to BE a callee with no inbound port (pull delivery):

```java
client.agents().serve(task -> {
    ChatCompletion answer = client.chat().completions().create(ChatCompletionRequest.builder()
        .addMessage(ChatMessage.user(task.getText()))
        .parentCallId(task.getCallId())                    // nests under the task
        .build());
    return task.done(answer.getContent());                 // or task.ask(...), task.fail(...), a String/Map
});                                                        // blocks; long-polls the door
```

`ServeOptions` sets the poll window, a task limit, and a stop condition. A
handler that throws fails that task, not the loop.

### Agents and runs

```java
for (Agent a : client.agents().list()) System.out.println(a.getId() + " " + a.getName());

Agent agent = client.agents().create(AgentCreateParams.builder()
    .name("Support bot")
    .ownerIdentityId(client.whoami().getIdentityId())     // the accountable owner — required
    .model("mock-gpt")
    .systemPrompt("You are a helpful support agent.")
    .tags(List.of("team:support"))
    .build());

Run run = client.agents().run(agent.getId(), RunParams.builder()
    .input(Map.of("q", "hello")).waitForCompletion(true).build());   // polls until succeeded/failed
System.out.println(run.getStatus() + " " + run.getOutput());
```

`client.runs()` has `create`, `get` and `waitFor` for runs directly.

### Account

```java
WhoAmI me = client.whoami();                     // tenant, identity, roles
MeteringSummary m = client.meteringSummary();    // spent / remaining against the monthly limit
```

### Async

Every call has a `CompletableFuture` twin on `AsyncForgebench`:

```java
try (AsyncForgebench client = Forgebench.builder().apiKey("sk_...").buildAsync()) {
    client.chat().completions().create(req).thenAccept(r -> System.out.println(r.getContent())).join();
}
```

It runs calls on a daemon thread pool it owns, or on yours:
`buildAsync(executorService)`.

## Errors

Every non-2xx response is a typed, unchecked exception (all extend
`ApiException`, which carries `getStatusCode()`, `getCode()`, `getRequestId()`):

| Status | Exception |
|---|---|
| 401 | `AuthenticationException` |
| 402 | `BudgetExceededException` — budget gate fired, nothing spent |
| 403 | `PermissionDeniedException` |
| 404 | `NotFoundException` |
| 409 | `ConflictException` |
| 422 | `ValidationException` |
| 429 | `RateLimitException` |
| 5xx | `ServerException` |

Network failures raise `ForgebenchConnectionException`. Connection errors and
429/500/502/503/504 are retried (`maxRetries`, default 2), honoring
`Retry-After`.

## Configuration

```java
Forgebench.builder()
    .apiKey("sk_...")
    .baseUrl("http://localhost:8000")
    .timeout(Duration.ofSeconds(60))
    .streamTimeout(Duration.ofMinutes(5))
    .maxRetries(2)
    .defaultHeader("X-My-Header", "v")
    .httpClient(myHttpClient)   // proxy, SSL context, executor
    .build();
```

## Example and tests

```bash
./mvnw test                         # offline: a scripted local control plane

export FORGEBENCH_API_KEY=sk_... FORGEBENCH_BASE_URL=http://localhost:8000
./mvnw -q compile dependency:build-classpath -Dmdep.outputFile=target/cp.txt
java -cp "target/classes:$(cat target/cp.txt)" examples/Quickstart.java
```
