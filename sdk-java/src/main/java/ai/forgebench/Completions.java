package ai.forgebench;

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
 */
public final class Completions {
    private static final String PATH = "/v1/chat/completions";

    private final Transport transport;

    Completions(Transport transport) {
        this.transport = transport;
    }

    /** A non-streamed chat completion. */
    public ChatCompletion create(ChatCompletionRequest request) {
        return transport.convert(
                transport.request("POST", PATH, request.toPayload(false), null, headers(request)),
                ChatCompletion.class);
    }

    /** A streamed chat completion (OpenAI-shaped SSE deltas). Close it when done. */
    public ChatCompletionStream createStream(ChatCompletionRequest request) {
        return new ChatCompletionStream(
                transport.streamSse("POST", PATH, request.toPayload(true), null, headers(request)),
                transport);
    }

    private static Map<String, String> headers(ChatCompletionRequest request) {
        Map<String, String> h = new HashMap<>();
        h.put(Trace.PARENT_CALL_HEADER, request.getParentCallId());
        return h;
    }
}
