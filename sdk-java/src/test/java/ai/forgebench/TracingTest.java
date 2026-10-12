package ai.forgebench;

import ai.forgebench.tracing.Observation;
import ai.forgebench.tracing.ObservationType;
import ai.forgebench.tracing.TraceAttributes;
import ai.forgebench.tracing.Tracing;
import ai.forgebench.tracing.TracingConfig;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.TextNode;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Tracing through the client: nesting, what a step records, model calls
 * nesting under the step they were made from, and the batches the server gets.
 */
class TracingTest {
    private static final String KEY = "sk_test_abc123";
    private static final String CHAT = "/v1/chat/completions";
    private static final String BATCH = "/v1/observations/batch";
    private static final String COMPLETION = "{\"id\":\"c1\",\"object\":\"chat.completion\",\"created\":1,\"model\":\"m\","
            + "\"choices\":[{\"index\":0,\"message\":{\"role\":\"assistant\",\"content\":\"hi\"},\"finish_reason\":\"stop\"}],"
            + "\"usage\":{\"prompt_tokens\":1,\"completion_tokens\":1,\"total_tokens\":2}}";
    private static final ObjectMapper M = new ObjectMapper();

    private MockServer server;

    @BeforeEach
    void start() throws Exception {
        server = new MockServer();
        server.on(CHAT, MockServer.json(200, COMPLETION));
        server.on(BATCH, MockServer.json(202, "{\"accepted\":1,\"rejected\":[]}"));
    }

    @AfterEach
    void stop() {
        server.close();
    }

    private Forgebench client(TracingConfig.Builder config) {
        return Forgebench.builder().apiKey(KEY).baseUrl(server.baseUrl()).maxRetries(0)
                .tracing(config.build()).build();
    }

    private Forgebench client() {
        return client(TracingConfig.builder());
    }

    private List<JsonNode> batches() throws IOException {
        List<JsonNode> out = new ArrayList<>();
        for (MockServer.Recorded r : new ArrayList<>(server.requests)) {
            if (r.path.equals(BATCH)) out.add(M.readTree(r.body));
        }
        return out;
    }

    private List<JsonNode> observations() throws IOException {
        List<JsonNode> out = new ArrayList<>();
        for (JsonNode b : batches()) b.get("observations").forEach(out::add);
        return out;
    }

    private List<JsonNode> traces() throws IOException {
        List<JsonNode> out = new ArrayList<>();
        for (JsonNode b : batches()) b.get("traces").forEach(out::add);
        return out;
    }

    private List<MockServer.Recorded> chats() {
        List<MockServer.Recorded> out = new ArrayList<>();
        for (MockServer.Recorded r : new ArrayList<>(server.requests)) if (r.path.equals(CHAT)) out.add(r);
        return out;
    }

    @Test
    void stepsNestAndRecordInputOutputAndAreSentWithTheApiKey() throws Exception {
        try (Forgebench fb = client(TracingConfig.builder().environment("staging").release("2.3.0"))) {
            Tracing tracing = fb.tracing();
            Object result;
            try (Observation agent = tracing.startActiveObservation(ObservationType.AGENT, "support-agent")) {
                agent.input(Map.of("question", "weather?"));
                result = tracing.observe(ObservationType.TOOL, "get_weather", Map.of("city", "sf"),
                        () -> Map.of("city", "sf", "temp", 18));
                agent.output(result);
            }
            assertTrue(fb.flush());
        }
        List<JsonNode> obs = observations();
        JsonNode tool = obs.get(0);
        JsonNode agent = obs.get(1);
        assertTrue(agent.get("parent_observation_id").isNull());
        assertEquals("agent", agent.get("type").asText());
        assertEquals(agent.get("id").asText(), tool.get("parent_observation_id").asText());
        assertEquals(agent.get("trace_id").asText(), tool.get("trace_id").asText());
        assertTrue(agent.get("id").asText().matches("[0-9a-f]{16}"));
        assertEquals("sf", tool.get("input").get("city").asText());
        assertEquals(18, tool.get("output").get("temp").asInt());
        JsonNode trace = traces().get(0);
        assertEquals("support-agent", trace.get("name").asText());
        assertEquals("staging", trace.get("environment").asText());
        assertEquals("2.3.0", trace.get("release").asText());
        assertEquals("forgebench-java", batches().get(0).get("sdk").get("name").asText());
        MockServer.Recorded posted = server.requests.stream().filter(r -> r.path.equals(BATCH)).findFirst().get();
        assertEquals("Bearer " + KEY, posted.header("Authorization"));
    }

    @Test
    void anExceptionIsRecordedAndRethrownUnchanged() throws Exception {
        try (Forgebench fb = client()) {
            IOException thrown = assertThrows(IOException.class, () -> fb.tracing().run(ObservationType.TOOL, "read",
                    () -> { throw new IOException("disk gone"); }));
            assertEquals("disk gone", thrown.getMessage());
            fb.flush();
        }
        JsonNode obs = observations().get(0);
        assertEquals("ERROR", obs.get("level").asText());
        assertEquals("IOException: disk gone", obs.get("status_message").asText());
    }

    @Test
    void aModelCallInsideAStepNestsUnderItAndInheritsUserAndSession() throws Exception {
        String stepId;
        try (Forgebench fb = client()) {
            try (Observation step = fb.tracing().startActiveObservation(ObservationType.AGENT, "turn")) {
                fb.tracing().updateCurrentTrace(TraceAttributes.builder().userId("u_42").sessionId("chat_7").build());
                fb.chat().completions().create(ChatCompletionRequest.builder().addMessage(ChatMessage.user("x")).build());
                stepId = step.getId();
            }
            fb.flush();
        }
        MockServer.Recorded call = chats().get(0);
        JsonNode body = M.readTree(call.body);
        assertEquals(stepId, call.header("X-Parent-Observation"));
        assertEquals(observations().get(0).get("trace_id").asText(), body.get("trace_id").asText());
        assertEquals("u_42", body.get("user").asText());
        assertEquals("chat_7", body.get("session_id").asText());
    }

    @Test
    void aModelCallOutsideAStepOrInAnotherTraceIsNotNested() throws Exception {
        try (Forgebench fb = client()) {
            fb.chat().completions().create(ChatCompletionRequest.builder().addMessage(ChatMessage.user("x")).build());
            try (Observation ignored = fb.tracing().startActiveObservation(ObservationType.SPAN, "s")) {
                fb.chat().completions().create(ChatCompletionRequest.builder()
                        .addMessage(ChatMessage.user("x")).traceId("other-trace").build());
            }
        }
        assertNull(chats().get(0).header("X-Parent-Observation"));
        assertNull(chats().get(1).header("X-Parent-Observation"));
        assertEquals("other-trace", M.readTree(chats().get(1).body).get("trace_id").asText());
    }

    @Test
    void theCurrentStepIsRestoredAndManualStepsNest() throws Exception {
        try (Forgebench fb = client()) {
            Tracing tracing = fb.tracing();
            try (Observation turn = tracing.startActiveObservation(ObservationType.CHAIN, "turn")) {
                try (Observation inner = tracing.startActiveObservation(ObservationType.SPAN, "inner")) {
                    assertEquals(inner.getId(), Tracing.currentObservationId().get());
                }
                assertEquals(turn.getId(), Tracing.currentObservationId().get());
                Observation job = tracing.startObservation(ObservationType.SPAN, "background-job");
                Observation child = job.startObservation(ObservationType.RETRIEVER, "sub-step");
                child.end();
                job.end();
                job.end();
                tracing.updateCurrentTrace(TraceAttributes.builder().tags("a").metadata("plan", "pro").build());
                tracing.updateCurrentTrace(TraceAttributes.builder().tags("a", "b").build());
            }
            assertFalse(Tracing.currentObservation().isPresent());
            fb.flush();
        }
        Map<String, JsonNode> byName = new HashMap<>();
        for (JsonNode o : observations()) byName.put(o.get("name").asText(), o);
        assertEquals(byName.get("turn").get("id"), byName.get("inner").get("parent_observation_id"));
        assertEquals(byName.get("turn").get("id"), byName.get("background-job").get("parent_observation_id"));
        assertEquals(byName.get("background-job").get("id"), byName.get("sub-step").get("parent_observation_id"));
        assertEquals(4, observations().size());
        JsonNode trace = traces().get(0);
        assertEquals("[\"a\",\"b\"]", trace.get("tags").toString());
        assertEquals("pro", trace.get("metadata").get("plan").asText());
    }

    @Test
    void wrapCarriesTheStepIntoAThreadPoolAndTheAsyncClientDoesItForYou() throws Exception {
        try (Forgebench fb = client(); ExecutorServiceCloser pool = new ExecutorServiceCloser(Executors.newFixedThreadPool(2))) {
            String parentId;
            try (Observation parent = fb.tracing().startActiveObservation(ObservationType.AGENT, "fan-out")) {
                parentId = parent.getId();
                Future<String> seen = pool.executor.submit(Tracing.wrap(() -> Tracing.currentObservationId().orElse(null)));
                assertEquals(parentId, seen.get());
                Future<String> plain = pool.executor.submit(() -> Tracing.currentObservationId().orElse(null));
                assertNull(plain.get());
            }
        }
        try (AsyncForgebench async = Forgebench.builder().apiKey(KEY).baseUrl(server.baseUrl()).maxRetries(0).buildAsync()) {
            String stepId;
            try (Observation step = async.tracing().startActiveObservation(ObservationType.AGENT, "async-turn")) {
                stepId = step.getId();
                async.chat().completions()
                        .create(ChatCompletionRequest.builder().addMessage(ChatMessage.user("x")).build()).join();
            }
            assertEquals(stepId, chats().get(chats().size() - 1).header("X-Parent-Observation"));
        }
    }

    @Test
    void disabledOrSampledOutSendsNothing() throws Exception {
        for (TracingConfig.Builder config : List.of(TracingConfig.builder().enabled(false),
                TracingConfig.builder().sampleRate(0.0))) {
            try (Forgebench fb = client(config)) {
                fb.tracing().observe(ObservationType.AGENT, "turn", () ->
                        fb.chat().completions().create(ChatCompletionRequest.builder().addMessage(ChatMessage.user("x")).build()));
                fb.flush();
            }
        }
        assertTrue(batches().isEmpty());
        for (MockServer.Recorded call : chats()) assertNull(call.header("X-Parent-Observation"));
    }

    @Test
    void maskRunsBeforeAnythingLeavesAndAFailingMaskWithholds() throws Exception {
        TracingConfig.Builder config = TracingConfig.builder().mask(data -> {
            if (data.isTextual() && data.asText().equals("explode")) throw new IllegalStateException("bad mask");
            try {
                return M.readTree(data.toString().replace("hunter2", "***"));
            } catch (IOException e) {
                throw new IllegalStateException(e);
            }
        });
        try (Forgebench fb = client(config)) {
            try (Observation login = fb.tracing().startActiveObservation(ObservationType.SPAN, "login")) {
                login.input(Map.of("password", "hunter2")).output("explode");
            }
            fb.flush();
        }
        JsonNode obs = observations().get(0);
        assertEquals("***", obs.get("input").get("password").asText());
        assertEquals("[withheld: mask failed]", obs.get("output").asText());
    }

    @Test
    void unserialisableAndHugeValuesAreMadeSafe() throws Exception {
        Map<String, Object> loop = new LinkedHashMap<>();
        loop.put("when", java.time.Instant.parse("2026-10-12T09:00:00Z"));
        loop.put("self", loop);
        try (Forgebench fb = client()) {
            try (Observation s = fb.tracing().startActiveObservation(ObservationType.SPAN, "s")) {
                s.input(loop).output("x".repeat(70_000)).metadata("ok", TextNode.valueOf("yes"));
            }
            fb.flush();
        }
        JsonNode obs = observations().get(0);
        assertTrue(obs.get("input").isTextual()); // a cycle falls back to toString()
        assertTrue(obs.get("output").asText().endsWith("…[truncated]"));
        assertEquals("yes", obs.get("metadata").get("ok").asText());
    }

    @Test
    void createTraceIdIsDeterministicWithASeed() {
        String a = Tracing.createTraceId("request-123");
        assertEquals(a, Tracing.createTraceId("request-123"));
        assertTrue(a.matches("[0-9a-f]{32}"));
        assertFalse(a.equals(Tracing.createTraceId(null)));
    }

    @Test
    void theCurrentTraceCanBeContinuedById() throws Exception {
        String traceId = Tracing.createTraceId("request-123");
        try (Forgebench fb = client()) {
            try (Observation resumed = fb.tracing().startActiveObservation(ObservationType.SPAN, "resume", traceId)) {
                assertEquals(traceId, resumed.getTraceId());
            }
            fb.flush();
        }
        assertEquals(traceId, observations().get(0).get("trace_id").asText());
    }

    // --- review fixes ----------------------------------------------------------

    @Test
    void aBadSampleRateInTheEnvironmentNeverBreaksTheClient() {
        for (String raw : List.of("abc", "5", "", "  ")) {
            Map<String, String> env = Map.of("FORGEBENCH_SAMPLE_RATE", raw);
            assertEquals(1.0, TracingConfig.fromEnv(env::get).getSampleRate());
        }
        assertEquals(0.25, TracingConfig.fromEnv(Map.of("FORGEBENCH_SAMPLE_RATE", "0.25")::get).getSampleRate());
        assertThrows(IllegalArgumentException.class, () -> TracingConfig.builder().sampleRate(5).build());
    }

    @Test
    void endingACurrentStepMakesItsParentCurrentAndAnEndedStepIsNeverCurrent() throws Exception {
        try (Forgebench fb = client(); ExecutorServiceCloser pool = new ExecutorServiceCloser(Executors.newSingleThreadExecutor())) {
            Tracing tracing = fb.tracing();
            try (Observation turn = tracing.startActiveObservation(ObservationType.AGENT, "turn")) {
                Observation inner = tracing.startActiveObservation(ObservationType.SPAN, "inner");
                inner.end(); // not close(): the parent must still become current again
                assertEquals(turn.getId(), Tracing.currentObservationId().orElse(null));
            }
            assertFalse(Tracing.currentObservation().isPresent());

            // A step made current on a pooled thread and ended elsewhere: the pool
            // thread's next task must not run inside it.
            Observation[] leaked = new Observation[1];
            pool.executor.submit(() -> leaked[0] = tracing.startActiveObservation(ObservationType.AGENT, "request-1")).get();
            leaked[0].end();
            assertNull(pool.executor.submit(() -> Tracing.currentObservationId().orElse(null)).get());
        }
    }

    @Test
    void anotherClientsStepNeverLendsItsTraceOrUser() throws Exception {
        try (Forgebench tenantA = Forgebench.builder().apiKey("sk_tenant_a").baseUrl(server.baseUrl()).maxRetries(0).build();
             Forgebench tenantB = Forgebench.builder().apiKey("sk_tenant_b").baseUrl(server.baseUrl()).maxRetries(0).build()) {
            try (Observation a = tenantA.tracing().startActiveObservation(ObservationType.AGENT, "a-turn")) {
                tenantA.tracing().updateCurrentTrace(TraceAttributes.builder().userId("alice@tenant-a").build());
                tenantB.chat().completions().create(ChatCompletionRequest.builder().addMessage(ChatMessage.user("x")).build());
                try (Observation b = tenantB.tracing().startActiveObservation(ObservationType.SPAN, "b-step")) {
                    assertNull(b.getParentId());
                    assertFalse(b.getTraceId().equals(a.getTraceId()));
                }
            }
            tenantA.flush();
            tenantB.flush();
        }
        JsonNode body = M.readTree(chats().get(0).body);
        assertNull(chats().get(0).header("X-Parent-Observation"));
        assertFalse(body.has("user"));
        assertFalse(body.has("trace_id"));
        for (MockServer.Recorded r : new ArrayList<>(server.requests)) {
            if (!r.path.equals(BATCH)) continue;
            String name = M.readTree(r.body).get("observations").get(0).get("name").asText();
            assertEquals(name.equals("b-step") ? "Bearer sk_tenant_b" : "Bearer sk_tenant_a", r.header("Authorization"));
        }
    }

    @Test
    void errorMessagesGoThroughTheMask() throws Exception {
        TracingConfig.Builder config = TracingConfig.builder()
                .mask(data -> data.isTextual() ? TextNode.valueOf(data.asText().replace("4111-1111", "****")) : data);
        try (Forgebench fb = client(config)) {
            assertThrows(IllegalStateException.class, () -> fb.tracing().run(ObservationType.TOOL, "charge",
                    () -> { throw new IllegalStateException("card 4111-1111 declined"); }));
            fb.flush();
        }
        assertEquals("IllegalStateException: card **** declined", observations().get(0).get("status_message").asText());
    }

    @Test
    void hugeValuesAreCutWhileSerialising() throws Exception {
        List<Integer> big = new ArrayList<>();
        for (int i = 0; i < 2_000_000; i++) big.add(i);
        try (Forgebench fb = client()) {
            long started = System.nanoTime();
            try (Observation s = fb.tracing().startActiveObservation(ObservationType.SPAN, "s")) {
                s.input(big);
            }
            assertTrue((System.nanoTime() - started) / 1_000_000 < 2_000, "bounded work");
            fb.flush();
        }
        String input = observations().get(0).get("input").asText();
        assertTrue(input.startsWith("[0,1,2,"));
        assertTrue(input.endsWith("…[truncated]"));
        assertEquals(64_000 + "…[truncated]".length(), input.length());
    }

    private static final class ExecutorServiceCloser implements AutoCloseable {
        final ExecutorService executor;

        ExecutorServiceCloser(ExecutorService executor) {
            this.executor = executor;
        }

        @Override
        public void close() {
            executor.shutdownNow();
        }
    }
}
