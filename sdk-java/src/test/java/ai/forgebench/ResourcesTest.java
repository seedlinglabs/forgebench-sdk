package ai.forgebench;

import ai.forgebench.errors.AuthenticationException;
import ai.forgebench.errors.ForgebenchException;
import ai.forgebench.errors.NotFoundException;
import ai.forgebench.errors.PermissionDeniedException;
import ai.forgebench.types.Agent;
import ai.forgebench.types.MeteringSummary;
import ai.forgebench.types.MessageParts;
import ai.forgebench.types.Run;
import ai.forgebench.types.Task;
import ai.forgebench.types.TaskReply;
import ai.forgebench.types.ToolBinding;
import ai.forgebench.types.ToolOutcomeReceipt;
import ai.forgebench.types.WhoAmI;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletionException;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Offline tests for everything beyond chat, mirroring the Python SDK's suite. */
class ResourcesTest {
    private static final String KEY = "sk_test_abc123";
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

    private JsonNode lastBody() throws Exception {
        return M.readTree(server.last().body);
    }

    private static String task(String state, String extra) {
        return "{\"id\":\"t1\",\"context_id\":\"ctx1\",\"status\":{\"state\":\"" + state + "\"" + extra + "},"
                + "\"call_id\":\"call_task\",\"input\":{\"message\":{\"role\":\"user\",\"parts\":"
                + "[{\"text\":\"10 MCQs on photosynthesis\"},{\"data\":{\"grade\":9}},{\"text\":\"CBSE\"}]}},"
                + "\"artifacts\":[{\"name\":\"response\",\"parts\":[{\"text\":\"Q1...\"},{\"data\":{\"n\":10}}]},"
                + "{\"name\":\"notes\",\"parts\":[{\"text\":\"easy\"}]}]}";
    }

    // --- account -----------------------------------------------------------------

    @Test
    void whoamiAndMeteringSummary() {
        server.on("/v1/auth/whoami", MockServer.json(200,
                "{\"tenant_id\":\"t-1\",\"auth_method\":\"api_key\",\"identity_id\":\"id-7\",\"roles\":[\"admin\"],\"new\":1}"));
        server.on("/v1/metering/summary", MockServer.json(200,
                "{\"tenant_id\":\"t-1\",\"total_events\":3,\"total_tokens\":120,\"total_cost_usd\":0.42,"
                        + "\"by_model\":{\"mock-gpt\":0.42},\"monthly_limit_usd\":50,\"spent_usd\":0.42,\"remaining_usd\":49.58}"));
        Forgebench c = client();
        WhoAmI me = c.whoami();
        assertEquals("t-1", me.getTenantId());
        assertEquals("id-7", me.getIdentityId());
        assertEquals(List.of("admin"), me.getRoles());
        MeteringSummary m = c.meteringSummary();
        assertEquals(120, m.getTotalTokens());
        assertEquals(0.42, m.getByModel().get("mock-gpt"));
        assertEquals(49.58, m.getRemainingUsd());
    }

    @Test
    void badKeyMapsTo401() {
        server.on("/v1/auth/whoami", MockServer.json(401, "{\"detail\":\"invalid api key\"}"));
        assertEquals(401, assertThrows(AuthenticationException.class, () -> client().account().whoami()).getStatusCode());
    }

    // --- agents + runs -------------------------------------------------------------

    @Test
    void agentsListAndCreate() throws Exception {
        server.on("/v1/agents",
                MockServer.json(200, "[{\"id\":\"a1\",\"name\":\"Support bot\",\"model\":\"mock-gpt\"}]"),
                MockServer.json(201, "{\"id\":\"a3\",\"name\":\"New\",\"model\":\"mock-gpt\",\"config\":{\"k\":1}}"),
                MockServer.json(200, "{\"data\":[{\"id\":\"a2\",\"name\":\"Wrapped\",\"model\":\"m\"}]}"));
        Forgebench c = client();
        List<Agent> agents = c.agents().list();
        assertEquals("Support bot", agents.get(0).getName());

        Agent created = c.agents().create(AgentCreateParams.builder()
                .name("New").ownerIdentityId("id-7").systemPrompt("be brief").tags(List.of("team:x")).build());
        assertEquals("a3", created.getId());
        JsonNode body = lastBody();
        assertEquals("id-7", body.get("owner_identity_id").asText());
        assertEquals("mock-gpt", body.get("model").asText());
        assertEquals("be brief", body.get("system_prompt").asText());
        assertTrue(body.get("description").isNull());
        assertEquals("team:x", body.at("/tags/0").asText());

        assertEquals("Wrapped", c.agents().list().get(0).getName(), "a {data: [...]} envelope is accepted too");
        assertThrows(NullPointerException.class, () -> AgentCreateParams.builder().name("x").build());
    }

    @Test
    void runsCreateGetAndWaitPollsToTerminal() throws Exception {
        server.on("/v1/runs", MockServer.json(201, "{\"id\":\"r1\",\"status\":\"queued\",\"model\":\"mock-gpt\",\"input\":{}}"));
        server.on("/v1/runs/r1",
                MockServer.json(200, "{\"id\":\"r1\",\"status\":\"running\",\"model\":\"mock-gpt\"}"),
                MockServer.json(200, "{\"id\":\"r1\",\"status\":\"succeeded\",\"model\":\"mock-gpt\",\"output\":{\"ok\":true}}"));
        Forgebench c = client();
        Run run = c.runs().create(Map.of("x", 1));
        assertEquals("queued", run.getStatus());
        assertEquals("mock-gpt", lastBody().get("model").asText());
        assertEquals(1, lastBody().at("/input/x").asInt());
        assertFalse(lastBody().has("agent_id"));

        Run done = c.runs().waitFor("r1", Duration.ofSeconds(5), Duration.ZERO);
        assertTrue(done.isTerminal());
        assertEquals(true, done.getOutput().get("ok"));
    }

    @Test
    void waitForTimesOut() {
        server.on("/v1/runs/r2", MockServer.json(200, "{\"id\":\"r2\",\"status\":\"running\",\"model\":\"m\"}"));
        ForgebenchException e = assertThrows(ForgebenchException.class,
                () -> client().runs().waitFor("r2", Duration.ofMillis(50), Duration.ofMillis(10)));
        assertTrue(e.getMessage().contains("last status: running"), e.getMessage());
    }

    @Test
    void agentRunPostsAgentIdAndCanBlock() throws Exception {
        server.on("/v1/runs", MockServer.json(201, "{\"id\":\"r9\",\"status\":\"queued\",\"model\":\"mock-gpt\",\"agent_id\":\"a1\"}"));
        server.on("/v1/runs/r9", MockServer.json(200, "{\"id\":\"r9\",\"status\":\"failed\",\"model\":\"mock-gpt\",\"error\":\"boom\"}"));
        Forgebench c = client();
        Run queued = c.agents().run("a1", RunParams.builder().input(Map.of("q", "hello")).build());
        JsonNode body = lastBody();
        assertEquals("a1", body.get("agent_id").asText());
        assertEquals("hello", body.at("/input/q").asText());
        assertFalse(body.has("model"), "an agent run uses the agent's model unless overridden");
        assertEquals("queued", queued.getStatus());

        Run finished = c.agents().run("a1", RunParams.builder().waitForCompletion(true).pollInterval(Duration.ZERO).build());
        assertEquals("boom", finished.getError());
    }

    @Test
    void runsGetMissingMapsTo404() {
        server.on("/v1/runs/nope", MockServer.json(404, "{\"detail\":\"run not found\"}"));
        assertThrows(NotFoundException.class, () -> client().runs().get("nope"));
    }

    // --- A2A: caller side ------------------------------------------------------------

    @Test
    void callOpensATaskThroughTheDoor() throws Exception {
        server.on("/v1/agents/qp-research/tasks", MockServer.json(200, task("completed", "")));
        Task t = client().agents().call("qp-research", CallParams.text("10 MCQs on photosynthesis")
                .data(Map.of("grade", 9)).contextId("ctx0").parentCallId("call_plan").wait(Duration.ofSeconds(30)).build());

        MockServer.Recorded req = server.last();
        assertEquals("/v1/agents/qp-research/tasks?wait=30", req.path);
        assertEquals("call_plan", req.header("X-Parent-Call"));
        JsonNode body = lastBody();
        assertEquals("user", body.at("/message/role").asText());
        assertEquals("10 MCQs on photosynthesis", body.at("/message/parts/0/text").asText());
        assertEquals(9, body.at("/message/parts/1/data/grade").asInt());
        assertEquals("ctx0", body.get("context_id").asText());

        assertEquals("completed", t.getState());
        assertTrue(t.isTerminal());
        assertEquals("Q1...\neasy", t.artifactText());
        assertEquals("easy", t.artifactText("notes"));
        assertEquals("call_task", t.getCallId());
    }

    @Test
    void callWithoutParentOrContextSendsNeither() throws Exception {
        server.on("/v1/agents/a2/tasks", MockServer.json(200, task("working", "")));
        Task t = client().agents().call("a2", "hi");
        assertNull(server.last().header("X-Parent-Call"));
        assertFalse(lastBody().has("context_id"));
        assertFalse(t.isTerminal());
    }

    @Test
    void unboundCalleeIsRefused() {
        server.on("/v1/agents/other/tasks", MockServer.json(403, "{\"detail\":{\"message\":\"not permitted\",\"code\":\"not_permitted\"}}"));
        PermissionDeniedException e = assertThrows(PermissionDeniedException.class, () -> client().agents().call("other", "hi"));
        assertEquals("not_permitted", e.getCode());
    }

    @Test
    void emptyTaskMessageIsRejectedClientSide() {
        assertThrows(IllegalArgumentException.class, () -> CallParams.builder().build());
    }

    @Test
    void taskPollAndCancel() {
        server.on("/v1/agents/a2/tasks/t1", MockServer.json(200,
                task("input_required", ",\"message\":{\"role\":\"agent\",\"parts\":[{\"text\":\"Which board?\"}]}")));
        server.on("/v1/agents/a2/tasks/t1/cancel", MockServer.json(200, task("canceled", "")));
        Forgebench c = client();
        Task t = c.agents().task("a2", "t1", Duration.ofMillis(1500));
        assertEquals("/v1/agents/a2/tasks/t1?wait=1.5", server.last().path);
        assertEquals("input_required", t.getState());
        assertEquals("Which board?", t.getQuestion());
        assertEquals("ctx1", t.getContextId());

        assertEquals("canceled", c.agents().cancel("a2", "t1").getState());
        assertEquals("POST", server.last().method);
    }

    // --- A2A: callee side --------------------------------------------------------------

    @Test
    void nextTaskReturnsNullWhenThereIsNone() {
        server.on("/v1/agents/me/tasks/next", MockServer.json(204, ""), MockServer.json(200, "{}"),
                MockServer.json(200, task("working", "")));
        Forgebench c = client();
        assertNull(c.agents().nextTask(Duration.ZERO));
        assertNull(c.agents().nextTask(Duration.ZERO));
        Task t = c.agents().nextTask();
        assertEquals("/v1/agents/me/tasks/next?wait=20", server.last().path);
        assertEquals("10 MCQs on photosynthesis\nCBSE", t.getText());
        assertEquals(Map.of("grade", 9), t.getData());
    }

    @Test
    void serveClaimsHandlesAndReplies() throws Exception {
        server.on("/v1/agents/me/tasks/next", MockServer.json(200, task("working", "")), MockServer.json(204, ""),
                MockServer.json(200, task("working", "")), MockServer.json(200, task("working", "")),
                MockServer.json(200, task("working", "")));
        server.on("/v1/agents/me/tasks/t1/result", MockServer.json(200, task("completed", "")));
        List<JsonNode> replies = new ArrayList<>();
        int[] n = {0};

        int handled = client().agents().serve(task -> {
            n[0]++;
            switch (n[0]) {
                case 1: return "plain answer";
                case 2: return task.ask("Which board?");
                case 3: return Map.of("score", 7);
                default: throw new IllegalStateException("handler broke");
            }
        }, ServeOptions.builder().wait(Duration.ZERO).maxTasks(4).build());

        assertEquals(4, handled, "an empty poll is not a task");
        for (MockServer.Recorded r : server.requests) {
            if (r.path.endsWith("/result")) replies.add(M.readTree(r.body));
        }
        assertEquals(4, replies.size());
        assertEquals("completed", replies.get(0).get("state").asText());
        assertEquals("plain answer", replies.get(0).at("/artifacts/0/parts/0/text").asText());
        assertEquals("response", replies.get(0).at("/artifacts/0/name").asText());
        assertEquals("input_required", replies.get(1).get("state").asText());
        assertEquals("Which board?", replies.get(1).at("/message/parts/0/text").asText());
        assertEquals(7, replies.get(2).at("/artifacts/0/parts/0/data/score").asInt());
        assertEquals("failed", replies.get(3).get("state").asText());
        assertEquals("IllegalStateException: handler broke", replies.get(3).get("error").asText());
    }

    @Test
    void serveStopsWhenAsked() {
        int handled = client().agents().serve(t -> "x", ServeOptions.builder().stop(() -> true).build());
        assertEquals(0, handled);
        assertTrue(server.requests.isEmpty());
    }

    @Test
    void taskReplyHelpers() {
        Task t = new com.fasterxml.jackson.databind.ObjectMapper().convertValue(Map.of("id", "t"), Task.class);
        assertEquals("submitted", t.getState(), "no status reads as submitted");
        TaskReply done = t.done("text", Map.of("k", 1), List.of(Map.of("name", "extra", "parts", List.of())), "answer");
        assertEquals("answer", done.getArtifacts().get(0).get("name"));
        assertEquals(2, ((List<?>) done.getArtifacts().get(0).get("parts")).size());
        assertEquals("extra", done.getArtifacts().get(1).get("name"));
        assertEquals(4000, t.fail("x".repeat(5000)).getError().length());
        assertThrows(IllegalArgumentException.class, () -> MessageParts.of(null, null, null));
        assertEquals("b", ((Map<?, ?>) MessageParts.of("a", null, List.of(Map.of("text", "b"))).get(1)).get("text"));
    }

    // --- agent tools ---------------------------------------------------------------------

    private static final String TOOLS = "{\"agent_id\":\"22222222\",\"tools\":["
            + "{\"server\":\"jira\",\"tool\":\"search_tickets\",\"description\":\"Search Jira\"},"
            + "{\"server\":\"docs\",\"tool\":\"query_docs\"}]}";

    @Test
    void agentToolsListAndOpenAiSchema() {
        server.on("/v1/agent-tools", MockServer.json(200, TOOLS));
        Forgebench c = client();
        List<ToolBinding> tools = c.agentTools().list();
        assertEquals("jira", tools.get(0).getServer());
        assertEquals("search_tickets", tools.get(0).getTool());

        Map<String, Object> typed = Map.of("type", "object", "properties", Map.of("q", Map.of("type", "string")));
        List<Map<String, Object>> schema = c.agentTools().openaiSchema(Map.of("search_tickets", typed));
        @SuppressWarnings("unchecked")
        Map<String, Object> fn0 = (Map<String, Object>) schema.get(0).get("function");
        assertEquals("function", schema.get(0).get("type"));
        assertEquals("search_tickets", fn0.get("name"));
        assertEquals("Search Jira", fn0.get("description"));
        assertEquals(typed, fn0.get("parameters"));
        @SuppressWarnings("unchecked")
        Map<String, Object> fn1 = (Map<String, Object>) schema.get(1).get("function");
        assertEquals(Map.of("type", "object", "properties", Map.of(), "additionalProperties", true), fn1.get("parameters"));
        assertEquals("", fn1.get("description"));
    }

    @Test
    void developerKeyHasNoAllowlist() {
        server.on("/v1/agent-tools", MockServer.json(403, "{\"detail\":\"not an agent credential\"}"));
        assertThrows(PermissionDeniedException.class, () -> client().agentTools().list());
    }

    @Test
    void reportSendsTheOutcome() throws Exception {
        server.on("/v1/agent-tools/report", MockServer.json(200, "{\"event_id\":\"e1\",\"audit_id\":\"au1\",\"reported\":true}"));
        ToolOutcomeReceipt r = client().agentTools().report("call_1", "query_docs", Map.of("hits", 2), null, 12L);
        assertEquals("e1", r.getEventId());
        assertEquals("au1", r.getAuditId());
        JsonNode body = lastBody();
        assertEquals("call_1", body.get("call_id").asText());
        assertEquals(2, body.at("/result/hits").asInt());
        assertTrue(body.get("error").isNull());
        assertEquals(12, body.get("latency_ms").asInt());
    }

    @Test
    void dispatchRunsReportsAndBuildsToolMessages() throws Exception {
        server.on("/v1/agent-tools/report", MockServer.json(200, "{\"event_id\":\"e\"}"));
        List<Map<String, Object>> calls = List.of(
                Map.of("id", "tc_1", "type", "function", "function", Map.of("name", "query_docs", "arguments", "{\"query\":\"cell\"}")),
                Map.of("id", "tc_2", "type", "function", "function", Map.of("name", "broken", "arguments", "")),
                Map.of("id", "tc_3", "type", "function", "function", Map.of("name", "raw", "arguments", "not json")));
        List<String> seen = new ArrayList<>();

        List<Map<String, Object>> msgs = client().agentTools().dispatch(calls, (name, args) -> {
            seen.add(name + args);
            if (name.equals("broken")) throw new IllegalStateException("tool down");
            return name.equals("raw") ? "plain text" : Map.of("hits", List.of());
        }, "call_9");

        assertEquals(List.of("query_docs{query=cell}", "broken{}", "raw{_raw=not json}"), seen);
        assertEquals("tool", msgs.get(0).get("role"));
        assertEquals("tc_1", msgs.get(0).get("tool_call_id"));
        assertEquals("query_docs", msgs.get(0).get("name"));
        assertEquals("{\"hits\":[]}", msgs.get(0).get("content"));
        assertEquals("{\"error\":\"tool down\"}", msgs.get(1).get("content"));
        assertEquals("plain text", msgs.get(2).get("content"));

        List<JsonNode> reports = new ArrayList<>();
        for (MockServer.Recorded r : server.requests) reports.add(M.readTree(r.body));
        assertEquals(3, reports.size());
        assertEquals("call_9", reports.get(0).get("call_id").asText());
        assertEquals("tool down", reports.get(1).get("error").asText());
        assertTrue(reports.get(1).get("result").isNull());
        assertTrue(reports.get(0).get("latency_ms").asLong() >= 0);

        // The tool messages drop straight into the next chat request.
        Object sent = ChatCompletionRequest.builder().messages(msgs).build().toPayload(false).get("messages");
        assertEquals("tc_1", ((Map<?, ?>) ((List<?>) sent).get(0)).get("tool_call_id"));
    }

    // --- async ---------------------------------------------------------------------------

    @Test
    void asyncClientMirrorsTheSyncOne() {
        server.on("/v1/auth/whoami", MockServer.json(200, "{\"tenant_id\":\"t-1\",\"auth_method\":\"api_key\"}"));
        server.on("/v1/chat/completions", MockServer.json(200, "{\"id\":\"c\",\"created\":1,\"model\":\"mock-gpt\","
                + "\"choices\":[{\"index\":0,\"message\":{\"role\":\"assistant\",\"content\":\"hi\"}}],\"usage\":{\"total_tokens\":3}}"));
        server.on("/v1/runs/missing", MockServer.json(404, "{\"detail\":\"run not found\"}"));
        try (AsyncForgebench c = Forgebench.builder().apiKey(KEY).baseUrl(server.baseUrl()).maxRetries(0).buildAsync()) {
            assertEquals("t-1", c.whoami().join().getTenantId());
            assertEquals("hi", c.chat().completions().create(ChatCompletionRequest.builder()
                    .addMessage(ChatMessage.user("hi")).build()).join().getContent());
            CompletionException e = assertThrows(CompletionException.class, () -> c.runs().get("missing").join());
            assertInstanceOf(NotFoundException.class, e.getCause());
        }
    }

    @Test
    void secondsFormatting() {
        assertEquals("30", Transport.seconds(Duration.ofSeconds(30)));
        assertEquals("1.5", Transport.seconds(Duration.ofMillis(1500)));
        assertEquals("0", Transport.seconds(Duration.ZERO));
    }
}
