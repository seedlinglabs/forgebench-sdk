package ai.forgebench;

import ai.forgebench.errors.ApiException;
import ai.forgebench.errors.AuthenticationException;
import ai.forgebench.errors.BudgetExceededException;
import ai.forgebench.errors.ForgebenchConnectionException;
import ai.forgebench.errors.PermissionDeniedException;
import ai.forgebench.errors.ServerException;
import ai.forgebench.types.ChatCompletion;
import ai.forgebench.types.ChatCompletionChunk;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Offline tests for the chat surface, against a scripted local control plane.
 * They mirror the Python SDK's chat tests: request shaping (auth header, no
 * invented credentials), response parsing, typed error mapping (notably 402),
 * SSE streaming, trace/lineage correlation, and retries.
 */
class ChatCompletionsTest {
    private static final String KEY = "sk_test_abc123";
    private static final String PATH = "/v1/chat/completions";
    private static final ObjectMapper M = new ObjectMapper();

    private MockServer server;

    @BeforeEach
    void start() throws Exception {
        server = new MockServer();
    }

    @AfterEach
    void stop() {
        server.close();
    }

    private Forgebench client() {
        return Forgebench.builder().apiKey(KEY).baseUrl(server.baseUrl()).maxRetries(0).build();
    }

    private static ChatCompletionRequest hi() {
        return ChatCompletionRequest.builder().addMessage(ChatMessage.user("hi")).build();
    }

    private static String chatBody(String extraFields) {
        return "{\"id\":\"chatcmpl-1\",\"object\":\"chat.completion\",\"created\":1700000000,"
                + "\"model\":\"mock-gpt\",\"choices\":[{\"index\":0,\"message\":{\"role\":\"assistant\","
                + "\"content\":\"Hello from the Forgebench mock model.\"},\"finish_reason\":\"stop\"}],"
                + "\"usage\":{\"prompt_tokens\":5,\"completion_tokens\":7,\"total_tokens\":12}" + extraFields + "}";
    }

    private JsonNode lastBody() throws Exception {
        return M.readTree(server.last().body);
    }

    @Test
    void chatCompletionSendsAuthAndParsesResponse() throws Exception {
        server.on(PATH, MockServer.json(200, chatBody("")));
        ChatCompletion resp = client().chat().completions().create(
                ChatCompletionRequest.builder().model("mock-gpt").addMessage(ChatMessage.user("hi")).build());

        assertEquals("Bearer " + KEY, server.last().header("Authorization"));
        assertTrue(server.last().header("User-Agent").startsWith("forgebench-sdk-java/"));
        JsonNode body = lastBody();
        assertEquals("mock-gpt", body.get("model").asText());
        assertFalse(body.get("stream").asBoolean());
        assertEquals("user", body.at("/messages/0/role").asText());
        assertEquals("hi", body.at("/messages/0/content").asText());
        assertEquals("Hello from the Forgebench mock model.", resp.getChoices().get(0).getMessage().getContent());
        assertEquals("Hello from the Forgebench mock model.", resp.getContent());
        assertEquals("stop", resp.getChoices().get(0).getFinishReason());
        assertEquals(12, resp.getUsage().getTotalTokens());
        assertEquals(5, resp.getUsage().getPromptTokens());
    }

    @Test
    void sdkNeverInventsProviderCredentials() throws Exception {
        server.on(PATH, MockServer.json(200, chatBody("")));
        client().chat().completions().create(hi());
        JsonNode body = lastBody();
        assertFalse(body.has("api_key"));
        assertFalse(body.has("api_base"));
        assertFalse(body.has("trace_id"));
        assertFalse(body.has("temperature"));
        assertFalse(body.has("max_tokens"));
    }

    @Test
    void optionalParamsAreSentWhenSet() throws Exception {
        server.on(PATH, MockServer.json(200, chatBody("")));
        client().chat().completions().create(hi().toBuilder().temperature(0.2).maxTokens(64).build());
        JsonNode body = lastBody();
        assertEquals(0.2, body.get("temperature").asDouble());
        assertEquals(64, body.get("max_tokens").asInt());
    }

    @Test
    void budgetGateMapsTo402Exception() {
        server.on(PATH, MockServer.json(402, "{\"detail\":\"monthly budget exceeded\",\"code\":\"budget_exceeded\"}"));
        BudgetExceededException e = assertThrows(BudgetExceededException.class,
                () -> client().chat().completions().create(hi()));
        assertEquals(402, e.getStatusCode());
        assertEquals("budget_exceeded", e.getCode());
        assertTrue(e.getMessage().contains("budget"));
    }

    @Test
    void badKeyMapsTo401() {
        server.on(PATH, MockServer.json(401, "{\"detail\":\"invalid api key\"}"));
        AuthenticationException e = assertThrows(AuthenticationException.class,
                () -> client().chat().completions().create(hi()));
        assertEquals(401, e.getStatusCode());
        assertEquals("invalid api key", e.getMessage());
    }

    @Test
    void structuredErrorDetailIsUnwrapped() {
        server.on(PATH, MockServer.json(403,
                "{\"detail\":{\"message\":\"tool not permitted\",\"code\":\"tool_denied\"}}"));
        PermissionDeniedException e = assertThrows(PermissionDeniedException.class,
                () -> client().chat().completions().create(hi()));
        assertEquals("tool not permitted", e.getMessage());
        assertEquals("tool_denied", e.getCode());
    }

    @Test
    void nonJsonErrorBodyAndUnmappedStatus() {
        server.on(PATH, ex -> {
            ex.getResponseHeaders().set("x-request-id", "req-9");
            MockServer.send(ex, 418, "teapot", "text/plain");
        });
        ApiException e = assertThrows(ApiException.class, () -> client().chat().completions().create(hi()));
        assertEquals(ApiException.class, e.getClass());
        assertEquals(418, e.getStatusCode());
        assertEquals("teapot", e.getMessage());
        assertEquals("req-9", e.getRequestId());
    }

    @Test
    void streamingChatCompletion() {
        String sse = "data: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":\"mock-gpt\","
                + "\"choices\":[{\"index\":0,\"delta\":{\"role\":\"assistant\",\"content\":\"Hello\"},\"finish_reason\":null}]}\n\n"
                + ": keep-alive comment\n\n"
                + "data: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":\"mock-gpt\","
                + "\"choices\":[{\"index\":0,\"delta\":{\"content\":\" world\"},\"finish_reason\":null}]}\n\n"
                + "data: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":\"mock-gpt\","
                + "\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}]}\n\n"
                + "data: [DONE]\n\n";
        server.on(PATH, MockServer.sse(sse));

        List<ChatCompletionChunk> chunks = new ArrayList<>();
        try (ChatCompletionStream stream = client().chat().completions().createStream(hi())) {
            stream.forEach(chunks::add);
        }
        String text = chunks.stream()
                .map(c -> c.getChoices().get(0).getDelta().getContent())
                .filter(s -> s != null)
                .collect(Collectors.joining());
        assertEquals("Hello world", text);
        assertEquals(3, chunks.size());
        assertEquals("stop", chunks.get(2).getChoices().get(0).getFinishReason());
        assertTrue(server.last().body.contains("\"stream\":true"));
        assertEquals("text/event-stream", server.last().header("Accept"));
    }

    @Test
    void streamingSendsParentCallHeaderAndExposesCallId() {
        server.on(PATH, ex -> {
            ex.getResponseHeaders().set("X-Call-Id", "call_child_456");
            MockServer.send(ex, 200, "data: {\"id\":\"c1\",\"created\":1,\"model\":\"mock-gpt\","
                    + "\"choices\":[{\"index\":0,\"delta\":{\"content\":\"hi\"},\"finish_reason\":\"stop\"}]}\n\n"
                    + "data: [DONE]\n\n", "text/event-stream");
        });
        try (ChatCompletionStream stream = client().chat().completions().createStream(
                hi().toBuilder().parentCallId("call_root_123").build())) {
            assertEquals("call_child_456", stream.getCallId());
            List<ChatCompletionChunk> chunks = stream.stream().collect(Collectors.toList());
            assertEquals("hi", chunks.get(0).getChoices().get(0).getDelta().getContent());
        }
        assertEquals("call_root_123", server.last().header("X-Parent-Call"));
    }

    @Test
    void streamedToolCallFragmentsSurviveOnTheDelta() {
        server.on(PATH, MockServer.sse(
                "data: {\"id\":\"c\",\"created\":1,\"model\":\"m\",\"choices\":[{\"index\":0,\"delta\":{\"role\":\"assistant\","
                + "\"tool_calls\":[{\"index\":0,\"id\":\"tc_1\",\"type\":\"function\",\"function\":{\"name\":\"query_docs\",\"arguments\":\"{\\\"q\"}}]}}]}\n\n"
                + "data: {\"id\":\"c\",\"created\":1,\"model\":\"m\",\"choices\":[{\"index\":0,\"delta\":{"
                + "\"tool_calls\":[{\"index\":0,\"function\":{\"arguments\":\"uery\\\":\\\"x\\\"}\"}}]},\"finish_reason\":\"tool_calls\"}]}\n\n"
                + "data: [DONE]\n\n"));
        List<ChatCompletionChunk> chunks;
        try (ChatCompletionStream stream = client().chat().completions().createStream(hi())) {
            chunks = stream.stream().collect(Collectors.toList());
        }
        List<Map<String, Object>> first = chunks.get(0).getChoices().get(0).getDelta().getToolCalls();
        assertEquals("tc_1", first.get(0).get("id"));
        assertEquals(0, first.get(0).get("index"));
        @SuppressWarnings("unchecked")
        Map<String, Object> fn = (Map<String, Object>) chunks.get(1).getChoices().get(0).getDelta().getToolCalls().get(0).get("function");
        assertEquals("uery\":\"x\"}", fn.get("arguments"));
        assertNull(chunks.get(1).getChoices().get(0).getDelta().getContent());
    }

    @Test
    void streamingErrorRaisesBeforeAnyChunk() {
        server.on(PATH, MockServer.json(402, "{\"detail\":\"monthly budget exceeded\",\"code\":\"budget_exceeded\"}"));
        BudgetExceededException e = assertThrows(BudgetExceededException.class,
                () -> client().chat().completions().createStream(hi()));
        assertEquals("budget_exceeded", e.getCode());
    }

    @Test
    void stalledStreamHitsOverallDeadline() {
        server.on(PATH, ex -> {
            ex.getResponseHeaders().set("Content-Type", "text/event-stream");
            ex.sendResponseHeaders(200, 0);
            OutputStream os = ex.getResponseBody();
            try {
                // A live connection that keeps trickling comments but never
                // sends an event or ends.
                for (int i = 0; i < 50; i++) {
                    os.write(": ping\n".getBytes(StandardCharsets.UTF_8));
                    os.flush();
                    Thread.sleep(100);
                }
            } catch (Exception ignored) {
                // client hung up — expected
            }
        });
        Forgebench client = Forgebench.builder().apiKey(KEY).baseUrl(server.baseUrl()).maxRetries(0)
                .streamTimeout(Duration.ofMillis(500)).build();
        long started = System.nanoTime();
        ForgebenchConnectionException e = assertThrows(ForgebenchConnectionException.class, () -> {
            try (ChatCompletionStream stream = client.chat().completions().createStream(hi())) {
                stream.forEach(c -> { });
            }
        });
        assertTrue(e.getMessage().contains("overall timeout"), e.getMessage());
        assertTrue(Duration.ofNanos(System.nanoTime() - started).toMillis() < 4000);
    }

    @Test
    void parentCallIdSentAsHeaderOnlyWhenSet() {
        server.on(PATH, MockServer.json(200, chatBody("")));
        client().chat().completions().create(hi().toBuilder().parentCallId("call_root_123").build());
        assertEquals("call_root_123", server.last().header("X-Parent-Call"));

        client().chat().completions().create(hi());
        assertNull(server.last().header("X-Parent-Call"));
    }

    @Test
    void traceIdSentWhenSuppliedAndLineageReadBack() throws Exception {
        server.on(PATH, MockServer.json(200, chatBody(",\"trace_id\":\"t-server\",\"call_id\":\"call_1\",\"x_new\":7")));
        String trace = Trace.newTraceId();
        ChatCompletion resp = client().chat().completions().create(hi().toBuilder().traceId(trace).build());
        assertEquals(trace, lastBody().get("trace_id").asText());
        assertEquals("t-server", resp.getTraceId());
        assertEquals("call_1", resp.getCallId());
        assertEquals(7, resp.getExtra().get("x_new"));
    }

    @Test
    void newTraceIdIs32HexChars() {
        String id = Trace.newTraceId();
        assertTrue(id.matches("[0-9a-f]{32}"), id);
        assertFalse(id.equals(Trace.newTraceId()));
    }

    @Test
    void toolLoopMessagesKeepTheirIds() throws Exception {
        server.on(PATH, MockServer.json(200, "{\"id\":\"c\",\"created\":1,\"model\":\"mock-gpt\",\"choices\":[{\"index\":0,"
                + "\"message\":{\"role\":\"assistant\",\"content\":null,\"tool_calls\":[{\"id\":\"tc_1\",\"type\":\"function\","
                + "\"function\":{\"name\":\"mcp_papers_search\",\"arguments\":\"{\\\"query\\\":\\\"cell\\\"}\"}}]},"
                + "\"finish_reason\":\"tool_calls\"}],\"usage\":{},\"call_id\":\"call_1\"}"));

        Map<String, Object> tool = new LinkedHashMap<>();
        tool.put("type", "function");
        tool.put("function", Map.of("name", "mcp_papers_search", "parameters", Map.of("type", "object")));
        ChatCompletion first = client().chat().completions().create(hi().toBuilder().tools(List.of(tool)).build());
        assertEquals("mcp_papers_search", lastBody().at("/tools/0/function/name").asText());

        List<Map<String, Object>> toolCalls = first.getChoices().get(0).getMessage().getToolCalls();
        assertEquals("tc_1", toolCalls.get(0).get("id"));
        assertNull(first.getContent());

        client().chat().completions().create(hi().toBuilder()
                .addMessage(ChatMessage.assistant(null, toolCalls))
                .addMessage(ChatMessage.tool("tc_1", "mcp_papers_search", "{\"hits\":[]}"))
                .parentCallId(first.getCallId())
                .build());
        JsonNode body = lastBody();
        JsonNode assistant = body.at("/messages/1");
        assertTrue(assistant.get("content").isNull(), "null content must stay null, not \"None\"/\"null\"");
        assertEquals("tc_1", assistant.at("/tool_calls/0/id").asText());
        JsonNode toolMsg = body.at("/messages/2");
        assertEquals("tool", toolMsg.get("role").asText());
        assertEquals("tc_1", toolMsg.get("tool_call_id").asText());
        assertEquals("mcp_papers_search", toolMsg.get("name").asText());
    }

    @Test
    void mapMessagesAndExtraBodyPassthrough() throws Exception {
        server.on(PATH, MockServer.json(200, chatBody("")));
        Map<String, Object> msg = new LinkedHashMap<>();
        msg.put("role", "user");
        msg.put("content", 42); // non-string, non-list content is sent as text
        Map<String, Object> extra = new LinkedHashMap<>();
        extra.put("tool_choice", "auto");
        extra.put("model", "should-not-win");
        client().chat().completions().create(ChatCompletionRequest.builder()
                .model("mock-gpt").addMessage(msg).extraBody(extra).build());
        JsonNode body = lastBody();
        assertEquals("42", body.at("/messages/0/content").asText());
        assertTrue(body.at("/messages/0/content").isTextual());
        assertEquals("auto", body.get("tool_choice").asText());
        assertEquals("mock-gpt", body.get("model").asText());
    }

    @Test
    void emptyMessagesRejected() {
        assertThrows(IllegalArgumentException.class, () -> ChatCompletionRequest.builder().build());
    }

    @Test
    void retriesTransientStatusThenSucceeds() {
        server.on(PATH,
                ex -> {
                    ex.getResponseHeaders().set("Retry-After", "0");
                    MockServer.send(ex, 503, "{\"detail\":\"busy\"}", "application/json");
                },
                MockServer.json(200, chatBody("")));
        Forgebench client = Forgebench.builder().apiKey(KEY).baseUrl(server.baseUrl()).maxRetries(2).build();
        assertEquals(12, client.chat().completions().create(hi()).getUsage().getTotalTokens());
        assertEquals(2, server.requests.size());
    }

    @Test
    void exhaustedRetriesSurfaceTheServerError() {
        server.on(PATH, ex -> {
            ex.getResponseHeaders().set("Retry-After", "0");
            MockServer.send(ex, 503, "{\"detail\":\"busy\"}", "application/json");
        });
        Forgebench client = Forgebench.builder().apiKey(KEY).baseUrl(server.baseUrl()).maxRetries(1).build();
        ServerException e = assertThrows(ServerException.class, () -> client.chat().completions().create(hi()));
        assertEquals(503, e.getStatusCode());
        assertEquals(2, server.requests.size());
    }

    @Test
    void unreachableServerIsAConnectionError() {
        Forgebench client = Forgebench.builder().apiKey(KEY).baseUrl("http://127.0.0.1:1").maxRetries(0).build();
        assertThrows(ForgebenchConnectionException.class, () -> client.chat().completions().create(hi()));
    }

    @Test
    void defaultBaseUrlIsProduction() {
        Forgebench client = Forgebench.builder().apiKey(KEY).env(k -> null).build();
        assertEquals("https://api.forgebench.ai/", client.getBaseUrl());
    }

    @Test
    void baseUrlAndKeyFromEnv() {
        server.on(PATH, MockServer.json(200, chatBody("")));
        Map<String, String> env = Map.of("FORGEBENCH_BASE_URL", server.baseUrl() + "/", "FORGEBENCH_API_KEY", "sk_env");
        Forgebench client = Forgebench.builder().env(env::get).maxRetries(0).build();
        assertEquals(server.baseUrl() + "/", client.getBaseUrl());
        client.chat().completions().create(hi());
        assertEquals("Bearer sk_env", server.last().header("Authorization"));
    }
}
