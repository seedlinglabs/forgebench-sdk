package ai.forgebench.tracing;

import ai.forgebench.Version;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.TextNode;

import java.lang.System.Logger;
import java.lang.System.Logger.Level;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ThreadLocalRandom;
import java.util.function.Supplier;

/**
 * Tracing: record your agent's own steps as one nested trace. Model calls made
 * inside a step nest under it automatically, with tokens and cost.
 *
 * <pre>{@code
 * Tracing tracing = forgebench.tracing();
 *
 * try (Observation agent = tracing.startActiveObservation(ObservationType.AGENT, "support-agent")) {
 *     tracing.updateCurrentTrace(TraceAttributes.builder().userId("u_42").sessionId("chat_7").build());
 *     Map<String, Object> weather = tracing.observe(ObservationType.TOOL, "get_weather",
 *             Map.of("city", "sf"), () -> getWeather("sf"));
 *     ChatCompletion reply = forgebench.chat().completions().create(request);   // nested
 *     agent.output(reply.getContent());
 * }
 * }</pre>
 *
 * <p>Steps are sent in the background with the client's API key — no other
 * keys, no extra setup — and only ever WRITTEN: the SDK never reads traces
 * back. The current step is per thread; hand work to a pool with
 * {@link #wrap(Runnable)} (the async client does this for you).
 */
public final class Tracing {
    static final String BATCH_PATH = "/v1/observations/batch";
    private static final Logger LOG = System.getLogger("forgebench.tracing");
    private static final ThreadLocal<Observation> CURRENT = new ThreadLocal<>();

    private final TracingConfig config;
    private final BatchSender sender;
    private final Map<String, Object> sdk;
    private final Set<String> warned = ConcurrentHashMap.newKeySet();
    private volatile BatchExporter exporter;

    /** Created by {@link ai.forgebench.Forgebench}; use {@code forgebench.tracing()}. */
    public Tracing(TracingConfig config, BatchSender sender) {
        this.config = config;
        this.sender = sender;
        Map<String, Object> sdk = new LinkedHashMap<>();
        sdk.put("name", "forgebench-java");
        sdk.put("version", Version.VERSION);
        this.sdk = sdk;
    }

    public TracingConfig getConfig() {
        return config;
    }

    // --- starting steps -------------------------------------------------------

    /**
     * A step under the current one (or the root of a new trace) that does NOT
     * become current — for work that starts and ends in different places.
     * End it, or it is never sent.
     */
    public Observation startObservation(ObservationType type, String name) {
        Observation current = current();
        // Steps of ANOTHER client's trace are not nested under: each client
        // only ever sends its own traces, with its own key.
        return startObservation(type, name, current != null && current.trace().tracing == this ? current : null);
    }

    /**
     * A step that is current until closed: steps and model calls made on this
     * thread inside it nest under it. Use it in try-with-resources.
     */
    public Observation startActiveObservation(ObservationType type, String name) {
        return activate(startObservation(type, name));
    }

    /**
     * The root of a step in an existing trace (one begun elsewhere — another
     * process or request), current until closed.
     */
    public Observation startActiveObservation(ObservationType type, String name, String traceId) {
        return activate(newRoot(type, name, traceId));
    }

    /**
     * Run {@code call} as a step: its return value is the output, an exception
     * is recorded as an error and re-thrown unchanged (checked ones too).
     */
    public <T, E extends Exception> T observe(ObservationType type, String name, ThrowingSupplier<T, E> call) throws E {
        return observe(type, name, null, call);
    }

    /** As {@link #observe(ObservationType, String, ThrowingSupplier)}, recording {@code input}. */
    public <T, E extends Exception> T observe(ObservationType type, String name, Object input,
                                              ThrowingSupplier<T, E> call) throws E {
        try (Observation step = startActiveObservation(type, name)) {
            if (input != null) step.input(input);
            try {
                T result = call.get();
                step.output(result);
                return result;
            } catch (Exception | Error e) {
                step.recordError(e);
                throw e;
            }
        }
    }

    /** {@link #observe} for a call with no result. */
    public <E extends Exception> void run(ObservationType type, String name, ThrowingRunnable<E> call) throws E {
        observe(type, name, null, () -> {
            call.run();
            return null;
        });
    }

    // --- updating -----------------------------------------------------------------

    /** Set trace-level fields. Model calls in the trace inherit its user and session. */
    public void updateCurrentTrace(TraceAttributes attributes) {
        Observation current = currentOrWarn("updateCurrentTrace");
        if (current != null && current.isRecording() && current.trace().tracing == this) {
            current.trace().update(attributes);
        }
    }

    /** The step code on this thread is inside of, if any. */
    public static Optional<Observation> currentObservation() {
        return Optional.ofNullable(current());
    }

    public static Optional<String> currentTraceId() {
        return currentObservation().map(Observation::getTraceId);
    }

    public static Optional<String> currentObservationId() {
        return currentObservation().map(Observation::getId);
    }

    /** A new 32-hex trace id; with a {@code seed} (e.g. your request id) always the same one. */
    public static String createTraceId(String seed) {
        if (seed == null) return UUID.randomUUID().toString().replace("-", "");
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(seed.getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder(32);
            for (int i = 0; i < 16; i++) sb.append(String.format("%02x", digest[i] & 0xff));
            return sb.toString();
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 is always available", e);
        }
    }

    // --- carrying the current step into other threads ----------------------------

    /** {@code task} running inside the step that is current HERE, on whatever thread runs it. */
    public static Runnable wrap(Runnable task) {
        Observation captured = current();
        return () -> {
            Observation previous = CURRENT.get();
            CURRENT.set(captured);
            try {
                task.run();
            } finally {
                restore(previous);
            }
        };
    }

    /** {@link #wrap(Runnable)} for a {@link Callable}. */
    public static <T> Callable<T> wrap(Callable<T> task) {
        Observation captured = current();
        return () -> {
            Observation previous = CURRENT.get();
            CURRENT.set(captured);
            try {
                return task.call();
            } finally {
                restore(previous);
            }
        };
    }

    /** {@link #wrap(Runnable)} for a {@link Supplier} (e.g. {@code CompletableFuture.supplyAsync}). */
    public static <T> Supplier<T> wrapSupplier(Supplier<T> task) {
        Observation captured = current();
        return () -> {
            Observation previous = CURRENT.get();
            CURRENT.set(captured);
            try {
                return task.get();
            } finally {
                restore(previous);
            }
        };
    }

    // --- governed calls inside a step --------------------------------------------

    /**
     * Where a governed call made now by THIS client belongs: the current
     * step's trace, as that step's child — unless {@code explicitTraceId}
     * names another trace, nothing is being recorded, or the step is another
     * client's (another workspace never lends its ids or user). Used by the
     * SDK's own chat calls.
     */
    public Optional<CallScope> currentCallScope(String explicitTraceId) {
        Observation current = current();
        if (current == null || !current.isRecording() || current.trace().tracing != this) return Optional.empty();
        if (explicitTraceId != null && !explicitTraceId.equals(current.getTraceId())) return Optional.empty();
        TraceState trace = current.trace();
        return Optional.of(new CallScope(current.getTraceId(), current.getId(), trace.userId(), trace.sessionId()));
    }

    /** See {@link #currentCallScope(String)}. */
    public static final class CallScope {
        private final String traceId;
        private final String parentObservationId;
        private final String userId;
        private final String sessionId;

        CallScope(String traceId, String parentObservationId, String userId, String sessionId) {
            this.traceId = traceId;
            this.parentObservationId = parentObservationId;
            this.userId = userId;
            this.sessionId = sessionId;
        }

        public String getTraceId() { return traceId; }
        public String getParentObservationId() { return parentObservationId; }
        public String getUserId() { return userId; }
        public String getSessionId() { return sessionId; }
    }

    // --- sending ---------------------------------------------------------------------

    /** Send every finished step now. True when it all went out within {@code timeout}. */
    public boolean flush(Duration timeout) {
        BatchExporter e = exporter;
        return e == null || e.flush(timeout);
    }

    /** Flush, then stop sending. Called by {@code Forgebench.close()}. */
    public void shutdown() {
        BatchExporter e = exporter;
        if (e != null) e.shutdown(Duration.ofSeconds(5));
    }

    /** A status message through the mask, cut to {@code max} chars. */
    String prepareText(String text, int max) {
        String out = text;
        if (config.getMask() != null) {
            try {
                JsonNode masked = config.getMask().apply(TextNode.valueOf(text));
                out = masked == null ? "" : masked.isTextual() ? masked.asText() : masked.toString();
            } catch (RuntimeException e) {
                warnOnce("mask", "forgebench tracing: mask failed, value withheld: " + e.getMessage());
                return "[withheld: mask failed]";
            }
        }
        return out.length() <= max ? out : out.substring(0, max);
    }

    /** Serialise, mask and size-cap one input / output / metadata value. */
    JsonNode prepare(Object value) {
        JsonNode data = TraceSerializer.toJson(value);
        if (data != null && config.getMask() != null) {
            try {
                data = config.getMask().apply(data);
            } catch (RuntimeException e) {
                warnOnce("mask", "forgebench tracing: mask failed, value withheld: " + e.getMessage());
                data = TextNode.valueOf("[withheld: mask failed]");
            }
        }
        return TraceSerializer.capped(data);
    }

    /** Queue an ended step (and, when due, its trace record). */
    void finish(Observation observation) {
        if (!observation.isRecording()) return;
        Map<String, Object> record = observation.trace().takeRecord(observation.getParentId() == null,
                observation.getName(), config.getEnvironment(), config.getRelease());
        exporterOrStart().add(observation.toWire(), record);
    }

    Observation startObservation(ObservationType type, String name, Observation parent) {
        if (parent == null) return newRoot(type, name, null);
        return new Observation(parent.trace(), parent.getId(), type, name);
    }

    // --- internals -------------------------------------------------------------------

    private Observation newRoot(ObservationType type, String name, String traceId) {
        if (traceId != null && (traceId.isEmpty() || traceId.length() > 64)) {
            throw new IllegalArgumentException("traceId must be 1-64 characters");
        }
        String id = traceId != null ? traceId : createTraceId(null);
        return new Observation(new TraceState(id, sample(), this), null, type, name);
    }

    private static Observation activate(Observation observation) {
        observation.previousActive(current());
        CURRENT.set(observation);
        return observation;
    }

    /** When {@code observation} is the current step on this thread, make its parent current. */
    static void deactivate(Observation observation) {
        if (CURRENT.get() == observation) restore(observation.previousActive());
    }

    /**
     * The current step on this thread. A step that ended without being
     * deactivated here (ended on another thread) is never current: the
     * nearest live step it was nested in is, so a pooled thread can never
     * carry one request's step into the next.
     */
    private static Observation current() {
        Observation o = CURRENT.get();
        if (o == null || !o.isEnded()) return o;
        while (o != null && o.isEnded()) o = o.previousActive();
        restore(o);
        return o;
    }

    private static void restore(Observation previous) {
        if (previous == null) CURRENT.remove();
        else CURRENT.set(previous);
    }

    private boolean sample() {
        return config.isEnabled() && ThreadLocalRandom.current().nextDouble() < config.getSampleRate();
    }

    private BatchExporter exporterOrStart() {
        BatchExporter e = exporter;
        if (e != null) return e;
        synchronized (this) {
            if (exporter == null) {
                exporter = new BatchExporter(this::send, config.getFlushAt(), config.getFlushInterval(),
                        config.getMaxQueueSize());
            }
            return exporter;
        }
    }

    private void send(List<Map<String, Object>> observations, List<Map<String, Object>> traces) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("observations", observations);
        payload.put("traces", traces);
        payload.put("sdk", sdk);
        JsonNode reply = sender.send(payload);
        if (reply == null) return;
        for (JsonNode rejection : reply.path("rejected")) {
            String reason = rejection.path("reason").asText();
            warnOnce("rejected:" + reason, "forgebench tracing: the server rejected observation "
                    + rejection.path("id").asText("?") + " (" + reason + ")");
        }
        String dropped = reply.path("dropped_reason").asText(null);
        if (dropped != null && !dropped.isEmpty()) {
            warnOnce("dropped:" + dropped, "forgebench tracing: traces are not stored (" + dropped + ")");
        }
    }

    private Observation currentOrWarn(String method) {
        Observation current = current();
        if (current == null) warnOnce(method, "forgebench tracing: " + method + "() called outside any observation; ignored");
        return current;
    }

    private void warnOnce(String key, String message) {
        if (warned.add(key)) LOG.log(Level.WARNING, message);
    }

    /** A {@link Supplier} that may throw. */
    @FunctionalInterface
    public interface ThrowingSupplier<T, E extends Exception> {
        T get() throws E;
    }

    /** A {@link Runnable} that may throw. */
    @FunctionalInterface
    public interface ThrowingRunnable<E extends Exception> {
        void run() throws E;
    }
}
