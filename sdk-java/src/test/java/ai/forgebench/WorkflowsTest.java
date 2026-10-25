package ai.forgebench;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Offline tests for workflows and agents.register. */
class WorkflowsTest {
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
        return Forgebench.builder().apiKey("sk_test_abc123").baseUrl(server.baseUrl()).maxRetries(0).build();
    }

    @Test
    void listGetAndChecks() {
        server.on("/v1/workflows", MockServer.json(200, "[{\"slug\":\"qp\",\"step_count\":2}]"));
        server.on("/v1/workflows/qp", MockServer.json(200, "{\"slug\":\"qp\",\"steps\":[]}"));
        server.on("/v1/workflows/qp/checks", MockServer.json(200, "{\"slug\":\"qp\",\"checks\":[{\"id\":\"lineage_missing\",\"status\":\"pass\"}]}"));
        Forgebench c = client();
        assertEquals("qp", c.workflows().list().get(0).get("slug"));
        assertEquals("qp", c.workflows().get("qp").get("slug"));
        assertEquals("qp", c.workflows().get("qp", 7).get("slug"));
        List<?> checks = (List<?>) c.workflows().checks("qp").get("checks");
        assertEquals(1, checks.size());
        assertEquals("GET", server.last().method);
        assertEquals("/v1/workflows/qp/checks", server.last().path);
    }

    @Test
    void registerSendsStepsAndEdgesAndDropsNulls() throws Exception {
        server.on("/v1/workflows", MockServer.json(201, "{\"workflow\":{\"slug\":\"qp\"},\"credentials\":[]}"));
        Map<String, Object> body = new HashMap<>();
        body.put("name", "QP");
        body.put("owner_identity_id", "o1");
        body.put("description", null);
        body.put("steps", List.of(Map.of("name", "grading", "model", "m")));
        body.put("edges", List.of(Map.of("from", "grading", "to", "retry", "kind", "sequence")));
        body.put("issue_credential", false);
        Map<String, Object> out = client().workflows().register(body);
        assertEquals("POST", server.last().method);
        JsonNode b = M.readTree(server.last().body);
        assertFalse(b.has("description"));
        assertEquals("grading", b.get("steps").get(0).get("name").asText());
        assertEquals("grading", b.get("edges").get(0).get("from").asText());
        assertFalse(b.get("issue_credential").asBoolean());
        assertTrue(out.containsKey("workflow"));
    }

    @Test
    void suggestionsAcceptAndDismiss() throws Exception {
        String fp = "a".repeat(64);
        server.on("/v1/workflows/suggestions", MockServer.json(200, "[{\"fingerprint\":\"" + fp + "\"}]"));
        server.on("/v1/workflows/suggestions/" + fp + "/accept", MockServer.json(201, "{\"workflow\":{\"slug\":\"w\"}}"));
        server.on("/v1/workflows/suggestions/" + fp + "/dismiss", MockServer.json(200, "{\"status\":\"dismissed\"}"));
        Forgebench c = client();
        assertEquals(fp, c.workflows().suggestions().get(0).get("fingerprint"));
        Map<String, Object> body = new HashMap<>();
        body.put("name", "N");
        body.put("slug", null);
        body.put("edges", List.of(Map.of("from_agent_id", "a", "to_agent_id", "b", "kind", "task")));
        assertTrue(c.workflows().acceptSuggestion(fp, body).containsKey("workflow"));
        assertEquals("POST", server.last().method);
        JsonNode b = M.readTree(server.last().body);
        assertEquals("N", b.get("name").asText());
        assertFalse(b.has("slug"));
        assertEquals("task", b.get("edges").get(0).get("kind").asText());
        assertEquals("dismissed", c.workflows().dismissSuggestion(fp).get("status"));
        assertEquals("/v1/workflows/suggestions/" + fp + "/dismiss", server.last().path);
    }

    @Test
    void agentsRegisterPostsToTheRegisterRoute() throws Exception {
        server.on("/v1/agents/register", MockServer.json(201, "{\"agent\":{\"id\":\"a1\"},\"credential\":null}"));
        Map<String, Object> out = client().agents().register(Map.of("name", "bot", "owner_identity_id", "o1", "issue_credential", false));
        assertEquals("/v1/agents/register", server.last().path);
        assertFalse(M.readTree(server.last().body).get("issue_credential").asBoolean());
        assertTrue(out.containsKey("agent"));
    }

    @Test
    void workflowRunUsesEachStepsKeyAndThreadsTheParent() {
        int[] n = {0};
        server.on("/v1/chat/completions", ex -> MockServer.send(ex, 200,
                "{\"id\":\"c\",\"created\":1,\"model\":\"m\",\"choices\":[],\"usage\":{},\"call_id\":\""
                        + (++n[0] == 2 ? "" : "call-" + n[0]) + "\"}", "application/json"));
        Workflow.Run run = client().workflow("qp", Map.of("grading", "sk_grade", "retry", "sk_retry")).run();
        ChatCompletionRequest req = ChatCompletionRequest.builder().model("m").addMessage(Map.of("role", "user", "content", "x")).build();
        run.step("grading").chat(req);
        assertEquals("Bearer sk_grade", server.last().header("Authorization"));
        assertEquals(null, server.last().header("X-Parent-Call"));
        run.step("retry").chat(req);
        assertEquals("Bearer sk_retry", server.last().header("Authorization"));
        assertEquals("call-1", server.last().header("X-Parent-Call"));
        run.step("grading").chat(req); // the previous call had no id: the chain holds
        assertEquals("call-1", server.last().header("X-Parent-Call"));
        assertEquals("call-1", run.rootCallId());
        assertEquals("call-3", run.lastCallId());
        assertEquals(List.of("call-1", "call-3"), run.callIds());
        org.junit.jupiter.api.Assertions.assertThrows(IllegalArgumentException.class, () -> run.step("nope"));
    }

    @Test
    void asyncTwinsRun() {
        server.on("/v1/workflows", MockServer.json(200, "[{\"slug\":\"qp\"}]"));
        try (AsyncForgebench c = Forgebench.builder().apiKey("k").baseUrl(server.baseUrl()).maxRetries(0).buildAsync()) {
            assertEquals("qp", c.workflows().list().join().get(0).get("slug"));
        }
    }
}
