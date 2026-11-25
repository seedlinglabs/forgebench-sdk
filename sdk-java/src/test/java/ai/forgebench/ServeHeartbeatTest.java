package ai.forgebench;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.time.Duration;
import java.util.List;
import java.util.stream.Collectors;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/** serve() keeps a long task alive with heartbeats, and survives a server without them. */
class ServeHeartbeatTest {
    private static final String TASK = "{\"id\":\"t1\",\"call_id\":\"call-of-t1\",\"context_id\":\"c1\",\"status\":{\"state\":\"working\"},"
            + "\"input\":{\"message\":{\"role\":\"user\",\"parts\":[{\"text\":\"go\"}]}},\"delivery\":\"pull\","
            + "\"created_at\":\"2026-11-10T00:00:00Z\",\"updated_at\":\"2026-11-10T00:00:00Z\"}";
    private static final String RESULT = "/v1/agents/me/tasks/t1/result";

    private MockServer server;

    @BeforeEach
    void start() throws Exception {
        server = new MockServer();
        server.on("/v1/agents/me/tasks/next", MockServer.json(200, TASK));
    }

    @AfterEach
    void stop() {
        server.close();
    }

    private Forgebench client() {
        return Forgebench.builder().apiKey("sk_test").baseUrl(server.baseUrl()).maxRetries(0).build();
    }

    private List<String> postedStates() {
        return server.requests.stream()
                .filter(r -> r.path.equals(RESULT))
                .map(r -> r.body.contains("\"working\"") ? "working" : r.body.contains("\"completed\"") ? "completed" : "other")
                .collect(Collectors.toList());
    }

    private static Object slow(ai.forgebench.types.Task task) throws InterruptedException {
        Thread.sleep(350);
        return "ok";
    }

    @Test
    void longHandlerSendsHeartbeatsThenTheReply() {
        server.on(RESULT, MockServer.json(200, TASK));
        int n = client().agents().serve(ServeHeartbeatTest::slow,
                ServeOptions.builder().maxTasks(1).heartbeat(Duration.ofMillis(100)).build());
        assertEquals(1, n);
        List<String> states = postedStates();
        assertEquals("completed", states.get(states.size() - 1));
        assertTrue(states.stream().filter("working"::equals).count() >= 2, states.toString());
    }

    @Test
    void serverWithoutHeartbeatsDoesNotBreakServe() {
        server.on(RESULT, ex -> {
            boolean beat = server.last().body.contains("\"working\"");
            MockServer.send(ex, beat ? 422 : 200, beat ? "{\"detail\":\"no\"}" : TASK, "application/json");
        });
        int n = client().agents().serve(ServeHeartbeatTest::slow,
                ServeOptions.builder().maxTasks(1).heartbeat(Duration.ofMillis(100)).build());
        assertEquals(1, n);
        List<String> states = postedStates();
        assertEquals(1, states.stream().filter("working"::equals).count(), states.toString());
        assertEquals("completed", states.get(states.size() - 1));
    }

    @Test
    void heartbeatsContinueAfterATransientServerError() {
        java.util.concurrent.atomic.AtomicInteger beats = new java.util.concurrent.atomic.AtomicInteger();
        server.on(RESULT, ex -> {
            boolean beat = server.last().body.contains("\"working\"");
            boolean fail = beat && beats.incrementAndGet() == 1;
            MockServer.send(ex, fail ? 502 : 200, fail ? "{\"detail\":\"bad gateway\"}" : TASK, "application/json");
        });
        int n = client().agents().serve(task -> {
            Thread.sleep(450);
            return "ok";
        }, ServeOptions.builder().maxTasks(1).heartbeat(Duration.ofMillis(100)).build());
        assertEquals(1, n);
        List<String> states = postedStates();
        assertTrue(states.stream().filter("working"::equals).count() >= 3, states.toString());
        assertEquals("completed", states.get(states.size() - 1));
    }

    @Test
    void firstHeartbeatGoesOutWhenTheTaskIsClaimed() {
        server.on(RESULT, MockServer.json(200, TASK));
        client().agents().serve(task -> {
            Thread.sleep(100);
            return "ok";
        }, ServeOptions.builder().maxTasks(1).heartbeat(Duration.ofSeconds(60)).build());
        assertEquals(List.of("working", "completed"), postedStates());
    }

    @Test
    void aReplyRefusedBecauseTheTaskEndedDoesNotStopServing() {
        server.on(RESULT, ex -> {
            boolean beat = server.last().body.contains("\"working\"");
            MockServer.send(ex, beat ? 200 : 409, beat ? TASK : "{\"detail\":\"task is canceled\"}", "application/json");
        });
        int n = client().agents().serve(task -> "ok", ServeOptions.builder().maxTasks(2).heartbeat(Duration.ZERO).build());
        assertEquals(2, n);
    }

    @Test
    void aCallMadeInsideAHandlerCarriesTheTaskAsItsParent() {
        server.on(RESULT, MockServer.json(200, TASK));
        server.on("/v1/agents/other/tasks", MockServer.json(201, TASK));
        Forgebench client = client();
        client.agents().serve(task -> {
            client.agents().call("other", CallParams.text("help").wait(Duration.ZERO).build());
            client.agents().call("other", CallParams.text("unrelated").wait(Duration.ZERO).parentCallId("").build());
            return "ok";
        }, ServeOptions.builder().maxTasks(1).heartbeat(Duration.ZERO).build());
        List<String> parents = server.requests.stream()
                .filter(r -> r.path.startsWith("/v1/agents/other/tasks"))
                .map(r -> r.header("X-Parent-Call"))
                .collect(Collectors.toList());
        assertEquals(java.util.Arrays.asList("call-of-t1", null), parents);
    }

    @Test
    void heartbeatZeroIsOff() {
        server.on(RESULT, MockServer.json(200, TASK));
        client().agents().serve(ServeHeartbeatTest::slow,
                ServeOptions.builder().maxTasks(1).heartbeat(Duration.ZERO).build());
        assertEquals(List.of("completed"), postedStates());
    }
}
