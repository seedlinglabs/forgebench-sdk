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
  <version>java-v1.3.0</version>
</dependency>
```

Gradle: `maven { url 'https://jitpack.io' }` and
`implementation 'com.github.seedlinglabs:forgebench-sdk:java-v1.3.0'`. Pick the
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

### Tracing your agent

Mark the steps you want to see and every run becomes one nested trace — the
agent, its functions and tools, and each model call (with tokens and cost)
under the step that made it. Steps are sent in the background with the same
API key; no other keys or setup. The SDK only writes traces — read them in the
console.

```java
Tracing tracing = client.tracing();

try (Observation agent = tracing.startActiveObservation(ObservationType.AGENT, "support-agent")) {
    tracing.updateCurrentTrace(TraceAttributes.builder()
            .userId("customer-42").sessionId("chat-7").tags("support").build());

    Map<String, Object> weather = tracing.observe(ObservationType.TOOL, "get_weather",
            Map.of("city", "sf"), () -> getWeather("sf"));       // input, output, errors recorded

    ChatCompletion reply = client.chat().completions().create(request);   // nests under "support-agent"
    agent.output(reply.getContent());
}   // ends the step even on an exception
```

| API | What it does |
|---|---|
| `tracing.startActiveObservation(type, name)` | A step that is current until closed (try-with-resources); steps and model calls inside nest under it. |
| `tracing.observe(type, name, [input,] () -> ...)` / `tracing.run(...)` | Runs a call as a step: return value as output, an exception recorded and re-thrown unchanged (checked ones too). |
| `tracing.startObservation(type, name)` / `observation.end()` | A step that is not current, for work that starts and ends in different places. |
| `observation.input(..)`, `.output(..)`, `.metadata(k, v)`, `.level(..)`, `.statusMessage(..)`, `.recordError(e)` | Change a step before it ends. |
| `tracing.updateCurrentTrace(TraceAttributes...)` | Trace fields: `userId`, `sessionId`, `tags`, `metadata`, `name`. Model calls inherit user and session. |
| `Tracing.currentTraceId()` / `currentObservationId()` / `createTraceId(seed)` | Ids for logs and linking. |
| `Tracing.wrap(runnable / callable)`, `Tracing.wrapSupplier(..)` | Carry the current step into a thread pool. `AsyncForgebench` does this for you. |
| `client.flush()` / `client.close()` | Send now / send and stop. A JVM shutdown hook also flushes. |

`ObservationType` is `SPAN`, `AGENT`, `TOOL`, `CHAIN`, `RETRIEVER`,
`EMBEDDING`, `EVALUATOR`, `GUARDRAIL` or `EVENT`; names are yours to choose.
Configure with `Forgebench.builder().tracing(TracingConfig.builder()...build())`
— `enabled`, `sampleRate`, `environment`, `release`, `mask` (runs on every
input/output before it leaves), `flushAt`, `flushInterval`, `maxQueueSize` —
or the `FORGEBENCH_TRACING_ENABLED`, `FORGEBENCH_SAMPLE_RATE`,
`FORGEBENCH_ENVIRONMENT` and `FORGEBENCH_RELEASE` environment variables. The
current step is per thread. Tracing never fails your agent: a failed send is
logged (`System.Logger` "forgebench.tracing") and dropped. Create one client
and reuse it.

**Spring AI:** wrap each `ToolCallback` so `call(...)` runs inside
`tracing.observe(ObservationType.TOOL, name, input, () -> delegate.call(...))`,
and open a step per request around `chatClient.prompt()...call()`; the model
calls Spring AI makes in its tool loop nest on their own (same thread).

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

### Workflows

A workflow is a named group of agents (steps) an app runs in order. Records are plain maps.

```java
WhoAmI me = forgebench.whoami();
Map<String, Object> created = forgebench.workflows().register(Map.of(
    "name", "Question paper",
    "owner_identity_id", me.getIdentityId(),
    "steps", List.of(Map.of("name", "grading", "model", "mock-gpt"), Map.of("name", "retry", "model", "mock-gpt")),
    "edges", List.of(Map.of("from", "grading", "to", "retry", "kind", "sequence"))));
// created.credentials[].credential.api_key is shown once

forgebench.workflows().checks("question-paper");   // wired? threaded? seeing traffic?
forgebench.workflows().list(); forgebench.workflows().get("question-paper");
forgebench.agents().register(Map.of("name", "bot", "owner_identity_id", id, "issue_credential", false));
```

Drive it with one key per step; each call uses its step's key and names the previous call of the run as
its parent (`X-Parent-Call`). The first call is a root; a call without a `call_id` is skipped. Blocking
and chat completions only (no streaming); `AsyncForgebench.workflow(...)` returns the same blocking helper.

```java
Workflow.Run run = forgebench.workflow("question-paper", Map.of("grading", keyA, "retry", keyB)).run();
run.step("grading").chat(request);
run.step("retry").chat(request);   // parent = grading's call_id
run.rootCallId();
```

`AsyncForgebench.workflows()` and `agents().register` have async twins.

### Voice

Audio calls are Governed Calls (budget-gated, metered, audited); audio is never stored.
`session` sets `X-Forgebench-Session`, the Voice Conversation key.

```java
Transcription t = client.audio().transcriptions().create(TranscriptionParams.builder()
        .file(Path.of("call.wav")).model("whisper-1").language("en").session("call-9").build());
System.out.println(t.getText() + " trace=" + t.getTraceId());

SpeechAudio s = client.audio().speech().create(SpeechParams.builder()
        .model("tts-1").input("Hello!").voice("alloy").session("call-9").build());
Files.write(Path.of("out.mp3"), s.getAudio());   // s.getDisclosure(), s.getTraceId(), s.getHeaders()
```

Voice governance admin (reads need viewer+, writes admin). Records are plain maps:

```java
Map<String, Object> policy = client.voice().policy().get();
client.voice().policy().update(policy);           // read-modify-write; read-only updated_at is dropped
client.voice().policy().update(Map.of("disclosure_mode", "enforce", "voice_trace_content", false));

client.voice().assets().list();
client.voice().assets().create(Map.of("provider", "elevenlabs", "provider_voice_id", "v1",
        "display_name", "Brand", "kind", "brand"));
// Only the setters you call are sent; null CLEARS a field, an uncalled setter leaves it unchanged.
client.voice().assets().update(id, VoiceAssetUpdate.builder().expiresAt(null));
client.voice().assets().revoke(id);

client.voice().consents().list();
client.voice().consents().create(Map.of("subject_name", "Ada", "purpose", "voice_clone", "scope", "brand"));
client.voice().consents().revoke(consentId);
```

Stream a grounded agent answer. Iteration throws `ForgebenchStreamException` on an
`event: error` frame, and with code `stream_truncated` if the stream ends without
`agent.run.completed` / `[DONE]`. Close it to release the connection on early exit:

```java
try (AgentRunStream events = client.agents().runStream("ag_1",
        MessageRunParams.builder("What is our refund policy?").session("call-9").build())) {
    for (JsonNode e : events) System.out.println(e);
}
Map<String, Object> answer = client.agents().run("ag_1", MessageRunParams.builder("...").build()); // non-streaming
```

`close() on a stream may be called from another thread.

Tie your own voice pipeline's chat/audio calls to one conversation with
`Trace.voiceHeaders(session, turn)` (session: 1-128 printable ASCII; internal spaces ok).
Trace-id response headers: `Trace.TRACE_ID_HEADER` (`X-Trace-Id`, chat) and
`Trace.AUDIO_TRACE_ID_HEADER` (`X-Forgebench-Trace-Id`, audio).

### Async

Every blocking call, including audio, voice and streamed runs (`agents().runStream(...)`), has a `CompletableFuture` twin on `AsyncForgebench`. A stream future completes when the response headers arrive; close the stream when you are done (cancelling the future first closes it for you):

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

Network failures raise `ForgebenchConnectionException`. Retries are bounded
(`maxRetries`, default 2) and honor `Retry-After`. Reads (GET, PUT, DELETE) retry on
network errors, read timeouts and 408/425/429/500/502/503/504 (shared contract: `packages/sdk-contract/RETRY.md`). Writes (POST/PATCH: a chat call, an
A2A task, a tool report) retry **only** on a 429 or a failure to connect
(refused, unknown host, connect timeout, TLS handshake), when the server cannot
have processed them, never on a 5xx or a dropped connection: the control plane
has no idempotency key, so a replay would be a second billed call. A stream
follows the same rule before it opens. Trace batches, whose replay is harmless,
retry like reads.

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
