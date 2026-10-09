package ai.forgebench.types;

import com.fasterxml.jackson.annotation.JsonAnySetter;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** A non-streamed chat completion from the governed chokepoint (OpenAI-shaped). */
public final class ChatCompletion {
    private String id = "";
    private String object = "chat.completion";
    private long created;
    private String model = "";
    private List<ChatChoice> choices = new ArrayList<>();
    private Usage usage = new Usage();
    // The trace_id this call was correlated under: the caller's own value if
    // passed on the request, else the id the server minted. Thread it into the
    // agent's own MCP client calls that follow, to nest them under this trace.
    private String traceId;
    // This call's id on the control plane's ledger. Pass it as parentCallId on
    // the governed calls this one causes and they are recorded as its
    // children — the ledger then holds the run as a tree. Null only on a
    // server older than the lineage feature.
    private String callId;

    private final Map<String, Object> extra = new LinkedHashMap<>();

    @JsonAnySetter
    private void putExtra(String key, Object value) {
        extra.put(key, value);
    }

    public String getId() { return id; }
    public String getObject() { return object; }
    public long getCreated() { return created; }
    public String getModel() { return model; }
    public List<ChatChoice> getChoices() { return choices; }
    public Usage getUsage() { return usage; }
    public String getTraceId() { return traceId; }
    public String getCallId() { return callId; }

    /** Response fields this SDK version does not model, keyed by their wire name. */
    public Map<String, Object> getExtra() { return Collections.unmodifiableMap(extra); }

    /** Content of the first choice, or null — the common single-choice case. */
    public String getContent() {
        return choices.isEmpty() ? null : choices.get(0).getMessage().getContent();
    }
}
