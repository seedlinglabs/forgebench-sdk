package ai.forgebench;

import ai.forgebench.types.Agent;
import ai.forgebench.types.Run;
import ai.forgebench.types.Task;
import ai.forgebench.types.TaskReply;
import com.fasterxml.jackson.databind.JsonNode;

import java.time.Duration;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Agents: registry ({@code /v1/agents}), runs through the runs subsystem, and
 * agent-to-agent tasks through the door (A2A).
 */
public final class Agents {
    private final Transport t;
    private final Runs runs;

    Agents(Transport transport, Runs runs) {
        this.t = transport;
        this.runs = runs;
    }

    public List<Agent> list() {
        JsonNode data = t.request("GET", "/v1/agents", null, null, null);
        JsonNode items = data == null ? null : data.isArray() ? data : data.get("data");
        List<Agent> out = new ArrayList<>();
        if (items != null) for (JsonNode a : items) out.add(t.convert(a, Agent.class));
        return out;
    }

    public Agent create(AgentCreateParams params) {
        return t.convert(t.request("POST", "/v1/agents", params.toBody(), null, null), Agent.class);
    }

    /** Runs an agent with the defaults: empty input, the agent's model, no waiting. */
    public Run run(String agentId) {
        return run(agentId, RunParams.defaults());
    }

    /**
     * Run an agent. Agents execute through the runs subsystem
     * ({@code POST /v1/runs} with an {@code agent_id}), which routes back
     * through the same governed chokepoint (budget re-gated per model hop,
     * metered, audited). With {@code waitForCompletion} this blocks, polling
     * until the run is terminal.
     */
    public Run run(String agentId, RunParams params) {
        Run run = runs.create(agentId, params.input, params.model);
        if (params.waitForCompletion && !run.isTerminal()) {
            return runs.waitFor(run.getId(), params.timeout, params.pollInterval);
        }
        return run;
    }

    // --- Agent -> agent through the door (A2A tasks) ---------------------------

    /** Open a text task on another agent, waiting up to 30s for an answer. */
    public Task call(String callee, String text) {
        return call(callee, CallParams.text(text).build());
    }

    /**
     * Open a task on another agent, through the door (A2A {@code SendMessage}).
     *
     * <p>Requires an AGENT credential bound to {@code callee} (an agent id or
     * name) by an operator — the control plane refuses otherwise, with one
     * collapsed {@code not_permitted} whatever the reason. The callee runs
     * wherever it runs: the door relays to its endpoint, or the callee's own
     * {@link #serve} loop claims the task. Blocks up to {@code wait} for an
     * answer ({@code completed}, {@code failed}, or a question —
     * {@code input_required}); a task still {@code working} is returned as-is
     * and can be polled with {@link #task}. Answer a question by calling again
     * with the task's {@code contextId}.
     */
    public Task call(String callee, CallParams params) {
        Map<String, String> headers = new HashMap<>();
        headers.put(Trace.PARENT_CALL_HEADER, params.parentCallId);
        JsonNode resp = t.request("POST", "/v1/agents/" + callee + "/tasks", params.toBody(),
                Map.of("wait", Transport.seconds(params.wait)), headers, params.wait);
        return t.convert(resp, Task.class);
    }

    /** Read a task you opened or were asked to do (A2A {@code GetTask}), without waiting. */
    public Task task(String callee, String taskId) {
        return task(callee, taskId, Duration.ZERO);
    }

    /** Read a task, long-polling up to {@code wait} for it to change. */
    public Task task(String callee, String taskId, Duration wait) {
        JsonNode resp = t.request("GET", "/v1/agents/" + callee + "/tasks/" + taskId, null,
                Map.of("wait", Transport.seconds(wait)), null, wait);
        return t.convert(resp, Task.class);
    }

    /** Cancel a task you opened (A2A {@code CancelTask}). Idempotent. */
    public Task cancel(String callee, String taskId) {
        return t.convert(t.request("POST", "/v1/agents/" + callee + "/tasks/" + taskId + "/cancel", null, null, null),
                Task.class);
    }

    // --- The callee side: pull delivery ------------------------------------------

    /** Claim the next task opened on THIS agent, long-polling up to 20s. */
    public Task nextTask() {
        return nextTask(Duration.ofSeconds(20));
    }

    /**
     * Claim the next task opened on THIS agent, long-polling up to
     * {@code wait}. Null when there is none. The claim is exclusive: N replicas
     * polling at once each get a different task.
     */
    public Task nextTask(Duration wait) {
        JsonNode resp = t.request("GET", "/v1/agents/me/tasks/next", null,
                Map.of("wait", Transport.seconds(wait)), null, wait);
        if (resp == null || resp.isNull() || (resp.isObject() && resp.isEmpty())) return null;
        return t.convert(resp, Task.class);
    }

    /** Finish, pause, or fail a task this agent claimed. */
    public Task reply(String taskId, TaskReply reply) {
        return t.convert(t.request("POST", "/v1/agents/me/tasks/" + taskId + "/result", reply.toMap(), null, null),
                Task.class);
    }

    /** {@link #serve(TaskHandler, ServeOptions)} with the defaults: 20s polls, forever. */
    public int serve(TaskHandler handler) {
        return serve(handler, ServeOptions.defaults());
    }

    /**
     * Run this agent as a callee: claim tasks, hand each to {@code handler},
     * post its reply. Blocks; returns how many tasks were handled.
     *
     * <p>No inbound port is needed — this is a pull loop against the door.
     * {@code stop} is polled between tasks for a clean shutdown; an interrupt
     * of the serving thread also ends the loop.
     */
    public int serve(TaskHandler handler, ServeOptions options) {
        int handled = 0;
        while (options.maxTasks == null || handled < options.maxTasks) {
            if (options.stop != null && options.stop.getAsBoolean()) break;
            if (Thread.currentThread().isInterrupted()) break;
            Task task = nextTask(options.wait);
            if (task == null) continue;
            TaskReply reply;
            try {
                reply = coerceReply(task, handler.handle(task));
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                reply = task.fail("InterruptedException: " + e.getMessage());
            } catch (Exception e) {
                // A failing handler fails the task, not the loop.
                reply = task.fail(e.getClass().getSimpleName() + ": " + e.getMessage());
            }
            reply(task.getId(), reply);
            handled++;
        }
        return handled;
    }

    @SuppressWarnings("unchecked")
    static TaskReply coerceReply(Task task, Object out) {
        if (out instanceof TaskReply) return (TaskReply) out;
        if (out == null) return task.done("");
        if (out instanceof String) return task.done((String) out);
        if (out instanceof Map) return task.done((Map<String, Object>) out);
        if (out instanceof List) return task.done(null, null, (List<Map<String, Object>>) out, "response");
        return task.done(String.valueOf(out));
    }
}
