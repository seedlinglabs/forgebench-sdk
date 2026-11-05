package ai.forgebench;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Offline tests for environments, agents.* and workflows.* environment methods. */
class EnvironmentsTest {
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

    private JsonNode body() throws Exception {
        return M.readTree(server.last().body);
    }

    @Test
    void environmentsCrud() throws Exception {
        server.on("/v1/environments", MockServer.json(200, "{\"environments\":[],\"entitlement\":{\"allowed\":true}}"),
                MockServer.json(201, "{\"slug\":\"dev\"}"));
        server.on("/v1/environments/dev", MockServer.json(200, "{\"slug\":\"dev\"}"), MockServer.json(204, ""));
        server.on("/v1/environments/order", MockServer.json(200, "[{\"slug\":\"prod\"}]"));
        Forgebench c = client();
        assertTrue(((Map<?, ?>) c.environments().list().get("entitlement")).get("allowed").equals(true));
        c.environments().create("Dev", null, false);
        assertEquals("POST", server.last().method);
        assertEquals("Dev", body().get("name").asText());
        assertEquals(false, body().get("is_production").asBoolean(true));
        c.environments().update("dev", null, true);
        assertEquals("PATCH", server.last().method);
        assertEquals("{\"is_production\":true}", server.last().body);
        assertEquals("prod", c.environments().reorder(List.of("prod", "dev")).get(0).get("slug"));
        assertEquals("PUT", server.last().method);
        c.environments().delete("dev");
        assertEquals("DELETE", server.last().method);
    }

    @Test
    void agentEnvironmentMethods() throws Exception {
        server.on("/v1/agents", MockServer.json(200, "[{\"id\":\"a\",\"name\":\"n\",\"model\":\"m\",\"environment\":{\"slug\":\"dev\"}}]"));
        server.on("/v1/agents/a1/environments", MockServer.json(200, "{\"environments\":[]}"), MockServer.json(201, "{\"mode\":\"added\"}"));
        server.on("/v1/agents/a1/promote", MockServer.json(200, "{\"fields\":[]}"));
        Forgebench c = client();
        assertEquals("dev", c.agents().list("dev").get(0).getEnvironment().get("slug"));
        assertEquals("/v1/agents?environment=dev", server.last().path);
        c.agents().environments("a1");
        assertEquals("added", c.agents().addToEnvironment("a1", "staging", Map.of("monthly_budget_usd", 5, "issue_credential", false)).get("mode"));
        assertEquals("staging", body().get("environment").asText());
        assertEquals(false, body().get("issue_credential").asBoolean(true));
        c.agents().promote("a1", "prod", true);
        assertEquals("/v1/agents/a1/promote", server.last().path);
        assertEquals("prod", body().get("to").asText());
        assertEquals(true, body().get("dry_run").asBoolean(false));
    }

    @Test
    void workflowEnvironmentMethods() throws Exception {
        server.on("/v1/workflows/qp", MockServer.json(200, "{\"slug\":\"qp\"}"));
        server.on("/v1/workflows/qp/checks", MockServer.json(200, "{\"checks\":[]}"));
        server.on("/v1/workflows/qp/environment", MockServer.json(200, "{\"slug\":\"qp\"}"));
        server.on("/v1/workflows/qp/promote", MockServer.json(200, "{\"steps\":[]}"));
        Forgebench c = client();
        c.workflows().get("qp", null, "dev");
        assertEquals("/v1/workflows/qp?environment=dev", server.last().path);
        c.workflows().checks("qp", "dev");
        assertEquals("/v1/workflows/qp/checks?environment=dev", server.last().path);
        c.workflows().placeEnvironment("qp", "dev");
        assertEquals("dev", body().get("environment").asText());
        c.workflows().promote("qp", "dev", "prod", false, true);
        JsonNode b = body();
        assertEquals("dev", b.get("from").asText());
        assertEquals("prod", b.get("to").asText());
        assertEquals(true, b.get("dry_run").asBoolean());
        assertEquals(false, b.get("add_missing").asBoolean(true));
    }
}
