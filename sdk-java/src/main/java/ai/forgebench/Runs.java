package ai.forgebench;

import ai.forgebench.errors.ForgebenchConnectionException;
import ai.forgebench.errors.ForgebenchException;
import ai.forgebench.types.Run;

import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Map;

/** The runs subsystem: {@code /v1/runs}. */
public final class Runs {
    private final Transport t;

    Runs(Transport transport) {
        this.t = transport;
    }

    /** A run of {@code mock-gpt} on this input, with no agent. */
    public Run create(Map<String, ?> input) {
        return create(null, input, "mock-gpt");
    }

    /**
     * Create a run. {@code agentId} may be null (a bare model run);
     * {@code model} defaults to {@code mock-gpt} when null without an agent.
     */
    public Run create(String agentId, Map<String, ?> input, String model) {
        Map<String, Object> body = new LinkedHashMap<>();
        if (model != null) body.put("model", model);
        else if (agentId == null) body.put("model", "mock-gpt");
        body.put("input", input == null ? Map.of() : input);
        if (agentId != null) body.put("agent_id", agentId);
        return t.convert(t.request("POST", "/v1/runs", body, null, null), Run.class);
    }

    public Run get(String runId) {
        return t.convert(t.request("GET", "/v1/runs/" + runId, null, null, null), Run.class);
    }

    /** Poll until the run is terminal: 60s timeout, every 500ms. */
    public Run waitFor(String runId) {
        return waitFor(runId, Duration.ofSeconds(60), Duration.ofMillis(500));
    }

    /**
     * Poll {@code GET /v1/runs/{id}} until the run is {@code succeeded} or
     * {@code failed}.
     *
     * @throws ForgebenchException when it is still running after {@code timeout}
     */
    public Run waitFor(String runId, Duration timeout, Duration pollInterval) {
        long deadline = System.nanoTime() + timeout.toNanos();
        while (true) {
            Run run = get(runId);
            if (run.isTerminal()) return run;
            if (System.nanoTime() >= deadline) {
                throw new ForgebenchException("run " + runId + " did not reach a terminal state within "
                        + Transport.seconds(timeout) + "s (last status: " + run.getStatus() + ")");
            }
            try {
                Thread.sleep(pollInterval.toMillis());
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new ForgebenchConnectionException("interrupted waiting for run " + runId, e);
            }
        }
    }
}
