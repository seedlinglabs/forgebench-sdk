package ai.forgebench;

import ai.forgebench.types.ToolBinding;
import ai.forgebench.types.ToolOutcomeReceipt;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * The agent-facing MCP surface ({@code /v1/agent-tools*}). Requires an AGENT
 * credential — the key this client was built with must have been issued to a
 * registered agent; a human/developer key has no allowlist and gets 403 here.
 */
public final class AgentTools {
    private final Transport t;

    AgentTools(Transport transport) {
        this.t = transport;
    }

    /**
     * Every tool the calling agent may currently call, resolved from its
     * allowlist server-side — build your tool list from this rather than
     * hardcoding it, so a central revoke actually reaches this agent.
     */
    public List<ToolBinding> list() {
        JsonNode data = t.request("GET", "/v1/agent-tools", null, null, null);
        List<ToolBinding> out = new ArrayList<>();
        if (data != null && data.has("tools")) for (JsonNode b : data.get("tools")) out.add(t.convert(b, ToolBinding.class));
        return out;
    }

    /** {@link #openaiSchema(Map)} with no typed parameters. */
    public List<Map<String, Object>> openaiSchema() {
        return openaiSchema(null);
    }

    /**
     * The agent's CURRENT allowlist as an OpenAI {@code tools} array, ready for
     * {@code ChatCompletionRequest.Builder.tools(...)}.
     *
     * <p>Built from {@link #list()} on every call, so a binding revoked centrally
     * drops out of the next turn's tool list. The control plane records only a
     * tool's name and description; its input schema lives with the agent's own
     * MCP client, so pass {@code parameters} ({@code tool name -> JSON schema})
     * for the tools the model should see typed arguments for. Unlisted tools
     * get a permissive object schema.
     */
    public List<Map<String, Object>> openaiSchema(Map<String, Map<String, Object>> parameters) {
        List<Map<String, Object>> out = new ArrayList<>();
        for (ToolBinding b : list()) {
            Map<String, Object> schema = parameters == null ? null : parameters.get(b.getTool());
            if (schema == null) {
                schema = new LinkedHashMap<>();
                schema.put("type", "object");
                schema.put("properties", Map.of());
                schema.put("additionalProperties", true);
            }
            Map<String, Object> function = new LinkedHashMap<>();
            function.put("name", b.getTool());
            function.put("description", b.getDescription() == null ? "" : b.getDescription());
            function.put("parameters", new LinkedHashMap<>(schema));
            Map<String, Object> tool = new LinkedHashMap<>();
            tool.put("type", "function");
            tool.put("function", function);
            out.add(tool);
        }
        return out;
    }

    /**
     * Record what a tool call the gate allowed actually returned.
     *
     * <p>The control plane recorded the DECISION when it relayed the model's
     * {@code tool_calls}; your MCP client did the call. This completes that
     * same ledger row with the outcome. {@code callId} is the chat response
     * that carried the tool_call. Client-asserted, stored as such — it never
     * changes the decision. Pass a {@code result} or an {@code error}.
     */
    public ToolOutcomeReceipt report(String callId, String toolName, Object result, String error, Long latencyMs) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("call_id", callId);
        body.put("tool_name", toolName);
        body.put("result", result);
        body.put("error", error);
        body.put("latency_ms", latencyMs);
        JsonNode data = t.request("POST", "/v1/agent-tools/report", body, null, null);
        return data == null ? new ToolOutcomeReceipt() : t.convert(data, ToolOutcomeReceipt.class);
    }

    /**
     * Run every tool_call in a governed response and report each outcome.
     *
     * <p>For each call this runs {@code execute(name, arguments)}, reports the
     * result or the exception against {@code callId} (the response the
     * tool_calls came from), and returns the {@code role: "tool"} messages to
     * append before the next model turn. An exception is reported as the
     * tool's error and surfaced to the model as {@code {"error": ...}} rather
     * than aborting the loop — the model decides what a failed tool means.
     */
    public List<Map<String, Object>> dispatch(List<Map<String, Object>> toolCalls, ToolExecutor execute, String callId) {
        List<Map<String, Object>> messages = new ArrayList<>();
        if (toolCalls == null) return messages;
        for (Map<String, Object> tc : toolCalls) {
            ParsedCall call = parseToolCall(tc);
            long started = System.nanoTime();
            Object content;
            try {
                Object result = execute.execute(call.name, call.arguments);
                report(callId, call.name, result, null, elapsedMs(started));
                content = result;
            } catch (Exception e) {
                if (e instanceof InterruptedException) Thread.currentThread().interrupt();
                String msg = String.valueOf(e.getMessage());
                report(callId, call.name, null, msg.length() > 4000 ? msg.substring(0, 4000) : msg, elapsedMs(started));
                content = Map.of("error", msg);
            }
            messages.add(toolMessage(call.id, call.name, content));
        }
        return messages;
    }

    private static long elapsedMs(long startedNanos) {
        return (System.nanoTime() - startedNanos) / 1_000_000;
    }

    static final class ParsedCall {
        final String id;
        final String name;
        final Map<String, Object> arguments;

        ParsedCall(String id, String name, Map<String, Object> arguments) {
            this.id = id;
            this.name = name;
            this.arguments = arguments;
        }
    }

    /**
     * (id, name, arguments) from an OpenAI-shaped tool_call. {@code arguments}
     * is a JSON string on the wire; a Map is taken as-is and an unparseable
     * string becomes {@code {"_raw": ...}} so the tool still sees it.
     */
    @SuppressWarnings("unchecked")
    static ParsedCall parseToolCall(Map<String, Object> tc) {
        Object fnObj = tc.get("function");
        Map<String, Object> fn = fnObj instanceof Map ? (Map<String, Object>) fnObj : Map.of();
        Object name = fn.get("name");
        if (name == null || name.toString().isEmpty()) throw new IllegalArgumentException("tool_call has no function.name");
        Object raw = fn.get("arguments");
        Map<String, Object> arguments;
        if (raw instanceof Map) {
            arguments = (Map<String, Object>) raw;
        } else if (raw == null || "".equals(raw)) {
            arguments = new LinkedHashMap<>();
        } else {
            try {
                Object parsed = Json.READER.readValue(raw.toString(), Object.class);
                arguments = parsed instanceof Map ? (Map<String, Object>) parsed : wrapRaw(parsed);
            } catch (JsonProcessingException e) {
                arguments = wrapRaw(raw);
            }
        }
        Object id = tc.get("id");
        return new ParsedCall(id == null ? null : id.toString(), name.toString(), arguments);
    }

    private static Map<String, Object> wrapRaw(Object raw) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("_raw", raw);
        return m;
    }

    static Map<String, Object> toolMessage(String toolCallId, String name, Object content) {
        Map<String, Object> msg = new LinkedHashMap<>();
        msg.put("role", "tool");
        msg.put("name", name);
        String text;
        if (content instanceof String) {
            text = (String) content;
        } else {
            try {
                text = Json.WRITER.writeValueAsString(content);
            } catch (JsonProcessingException e) {
                text = String.valueOf(content);
            }
        }
        msg.put("content", text);
        if (toolCallId != null) msg.put("tool_call_id", toolCallId);
        return msg;
    }
}
