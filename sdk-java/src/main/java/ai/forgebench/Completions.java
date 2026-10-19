package ai.forgebench;

import ai.forgebench.tracing.Tracing;
import ai.forgebench.types.ChatCompletion;

import java.util.HashMap;
import java.util.Map;

/**
 * {@code POST /v1/chat/completions} — the governed chokepoint.
 *
 * <p>The server authenticates, isolates the tenant via RLS, gates the budget
 * BEFORE any provider call (a 402 surfaces here as
 * {@link ai.forgebench.errors.BudgetExceededException}), calls the model
 * gateway, then meters and audits — all in one tenant transaction.
 *
 * <p>Inside a traced step ({@link Tracing}) the call joins that step's trace
 * as its child: {@code trace_id} and the trace's user / session are filled in
 * for you, and the step's id rides {@link Trace#PARENT_OBSERVATION_HEADER}.
 */
public final class Completions {
    private static final String PATH = "/v1/chat/completions";

    private final Transport transport;
    private final Tracing tracing;

    Completions(Transport transport, Tracing tracing) {
        this.transport = transport;
        this.tracing = tracing;
    }

    /** A non-streamed chat completion. */
    public ChatCompletion create(ChatCompletionRequest request) {
        return create(request, null);
    }

    /** As {@link #create(ChatCompletionRequest)} with headers for this call only (a workflow step's key). */
    ChatCompletion create(ChatCompletionRequest request, Map<String, String> overrideHeaders) {
        Map<String, Object> payload = request.toPayload(false);
        Map<String, String> headers = headers(request);
        if (overrideHeaders != null) headers.putAll(overrideHeaders);
        joinCurrentTrace(payload, headers);
        return transport.convert(transport.request("POST", PATH, payload, null, headers), ChatCompletion.class);
    }

    /** A streamed chat completion (OpenAI-shaped SSE deltas). Close it when done. */
    public ChatCompletionStream createStream(ChatCompletionRequest request) {
        Map<String, Object> payload = request.toPayload(true);
        Map<String, String> headers = headers(request);
        joinCurrentTrace(payload, headers);
        return new ChatCompletionStream(transport.streamSse("POST", PATH, payload, null, headers), transport);
    }

    private static Map<String, String> headers(ChatCompletionRequest request) {
        Map<String, String> h = new HashMap<>();
        h.put(Trace.PARENT_CALL_HEADER, request.getParentCallId());
        return h;
    }

    private void joinCurrentTrace(Map<String, Object> payload, Map<String, String> headers) {
        Object explicit = payload.get("trace_id");
        tracing.currentCallScope(explicit == null ? null : explicit.toString()).ifPresent(scope -> {
            payload.put("trace_id", scope.getTraceId());
            if (scope.getUserId() != null) payload.putIfAbsent("user", scope.getUserId());
            if (scope.getSessionId() != null) payload.putIfAbsent("session_id", scope.getSessionId());
            headers.put(Trace.PARENT_OBSERVATION_HEADER, scope.getParentObservationId());
        });
    }
}
