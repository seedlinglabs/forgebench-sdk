package ai.forgebench.tracing;

import com.fasterxml.jackson.databind.JsonNode;

import java.security.SecureRandom;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * One step of a trace: the agent, a function, a tool call, a retrieval...
 *
 * <p>Sent once, when it ends ({@link #end()} or {@link #close()}); until then
 * the setters change it. {@link #isRecording()} is false when tracing is off
 * or the trace was sampled out — every call still works, nothing is sent.
 *
 * <pre>{@code
 * try (Observation step = tracing.startActiveObservation(ObservationType.CHAIN, "plan")) {
 *     step.input(question);
 *     ...
 *     step.output(plan);
 * }
 * }</pre>
 */
public final class Observation implements AutoCloseable {
    private static final SecureRandom RANDOM = new SecureRandom();
    private static final int MAX_STATUS_CHARS = 1_000;

    private final String id = randomHex(8);
    private final TraceState trace;
    private final String parentId;
    private final ObservationType type;
    private final Instant startTime = Instant.now();
    private String name;
    private Instant endTime;
    private JsonNode input;
    private JsonNode output;
    private final Map<String, JsonNode> metadata = new LinkedHashMap<>();
    private ObservationLevel level = ObservationLevel.DEFAULT;
    private String statusMessage;
    // While this is the current step: the step that was current before it.
    private volatile Observation previousActive;

    Observation(TraceState trace, String parentId, ObservationType type, String name) {
        this.trace = trace;
        this.parentId = parentId;
        this.type = type;
        this.name = name;
    }

    public String getId() { return id; }
    public String getTraceId() { return trace.id; }
    public String getParentId() { return parentId; }
    public ObservationType getType() { return type; }
    public synchronized String getName() { return name; }
    public boolean isRecording() { return trace.sampled; }
    public synchronized boolean isEnded() { return endTime != null; }

    TraceState trace() { return trace; }

    public synchronized Observation name(String name) {
        this.name = name;
        return this;
    }

    /**
     * Captured (serialised with Jackson, so getters are called) right away;
     * later changes to {@code value} are not seen.
     */
    public synchronized Observation input(Object value) {
        if (isRecording()) input = trace.tracing.prepare(value);
        return this;
    }

    /** Captured (serialised) right away; later changes to {@code value} are not seen. */
    public synchronized Observation output(Object value) {
        if (isRecording()) output = trace.tracing.prepare(value);
        return this;
    }

    public synchronized Observation metadata(String key, Object value) {
        if (isRecording()) metadata.put(key, trace.tracing.prepare(value));
        return this;
    }

    public synchronized Observation metadata(Map<String, ?> values) {
        values.forEach(this::metadata);
        return this;
    }

    public synchronized Observation level(ObservationLevel level) {
        this.level = level;
        return this;
    }

    /** Goes through the mask too: error messages often carry the values it exists for. */
    public synchronized Observation statusMessage(String statusMessage) {
        this.statusMessage = statusMessage == null ? null : trace.tracing.prepareText(statusMessage, MAX_STATUS_CHARS);
        return this;
    }

    /** Marks this step as failed with {@code error}'s type and message. */
    public Observation recordError(Throwable error) {
        level(ObservationLevel.ERROR);
        return statusMessage(error.getClass().getSimpleName() + ": " + error.getMessage());
    }

    /**
     * A child of this step that does NOT become the current one — for work
     * started and finished in different places. End it.
     */
    public Observation startObservation(ObservationType type, String name) {
        return trace.tracing.startObservation(type, name, this);
    }

    /**
     * Finish and queue this step for sending; if it is the current step on this
     * thread, its parent becomes current again. Later calls do nothing.
     */
    public void end() {
        synchronized (this) {
            if (endTime != null) return;
            endTime = Instant.now();
        }
        Tracing.deactivate(this);
        trace.tracing.finish(this);
    }

    /** Same as {@link #end()}, for try-with-resources. */
    @Override
    public void close() {
        end();
    }

    Observation previousActive() {
        return previousActive;
    }

    void previousActive(Observation previous) {
        this.previousActive = previous;
    }

    synchronized Map<String, Object> toWire() {
        Map<String, Object> wire = new LinkedHashMap<>();
        wire.put("id", id);
        wire.put("trace_id", trace.id);
        wire.put("parent_observation_id", parentId);
        wire.put("type", type.wireName());
        wire.put("name", name);
        wire.put("start_time", startTime.toString());
        wire.put("end_time", (endTime != null ? endTime : startTime).toString());
        wire.put("input", input);
        wire.put("output", output);
        wire.put("level", level.name());
        wire.put("status_message", statusMessage);
        wire.put("metadata", new LinkedHashMap<>(metadata));
        return wire;
    }

    static String randomHex(int bytes) {
        byte[] buf = new byte[bytes];
        RANDOM.nextBytes(buf);
        StringBuilder sb = new StringBuilder(bytes * 2);
        for (byte b : buf) sb.append(String.format("%02x", b & 0xff));
        return sb.toString();
    }
}
