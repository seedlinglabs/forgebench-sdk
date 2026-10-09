package ai.forgebench.types;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.stream.Collectors;

/**
 * One task opened on an agent through the door — the A2A task object as the
 * control plane records it ({@code TaskInfo}).
 *
 * <p>{@link #getCallId()} is the task's own row on the ledger. A callee serving
 * this task passes it as {@code parentCallId} on every governed call it makes
 * while working on it, so those calls hang under the task in the tree.
 */
public final class Task {
    private String id;
    private String contextId;
    private String state;
    private String delivery = "pull";
    private Map<String, Object> input = new LinkedHashMap<>();
    private List<Map<String, Object>> artifacts;
    private Map<String, Object> statusMessage;
    private String error;
    private String callId;
    private String parentCallId;
    private String rootCallId;
    private String callerAgentId;
    private String calleeAgentId;
    private String traceId;
    private String createdAt;
    private String claimedAt;
    private String completedAt;

    private String statusState;

    // The wire nests the state under {"status": {"state", "message"}}.
    @JsonProperty("status")
    @SuppressWarnings("unchecked")
    private void setStatus(Map<String, Object> status) {
        if (status == null) return;
        Object s = status.get("state");
        if (s != null) statusState = s.toString();
        Object m = status.get("message");
        statusMessage = m instanceof Map ? (Map<String, Object>) m : null;
    }

    public String getId() { return id; }
    public String getContextId() { return contextId; }

    /** submitted | working | input_required | completed | failed | canceled */
    public String getState() {
        if (statusState != null) return statusState;
        return state != null ? state : "submitted";
    }

    public String getDelivery() { return delivery; }
    public Map<String, Object> getInput() { return input; }
    public List<Map<String, Object>> getArtifacts() { return artifacts; }
    public Map<String, Object> getStatusMessage() { return statusMessage; }
    public String getError() { return error; }
    public String getCallId() { return callId; }
    public String getParentCallId() { return parentCallId; }
    public String getRootCallId() { return rootCallId; }
    public String getCallerAgentId() { return callerAgentId; }
    public String getCalleeAgentId() { return calleeAgentId; }
    public String getTraceId() { return traceId; }
    public String getCreatedAt() { return createdAt; }
    public String getClaimedAt() { return claimedAt; }
    public String getCompletedAt() { return completedAt; }

    // --- reading the request -------------------------------------------------

    @SuppressWarnings("unchecked")
    public List<Map<String, Object>> getParts() {
        Object message = input == null ? null : input.get("message");
        if (!(message instanceof Map)) return Collections.emptyList();
        Object parts = ((Map<String, Object>) message).get("parts");
        return parts instanceof List ? new ArrayList<>((List<Map<String, Object>>) parts) : Collections.emptyList();
    }

    /** All text parts of the input message, joined. */
    public String getText() {
        return joinText(getParts());
    }

    /** All data parts of the input message, merged (later keys win). */
    @SuppressWarnings("unchecked")
    public Map<String, Object> getData() {
        Map<String, Object> merged = new LinkedHashMap<>();
        for (Map<String, Object> p : getParts()) {
            Object d = p.get("data");
            if (d instanceof Map) merged.putAll((Map<String, Object>) d);
        }
        return merged;
    }

    /** completed, failed or canceled. */
    public boolean isTerminal() {
        String s = getState();
        return "completed".equals(s) || "failed".equals(s) || "canceled".equals(s);
    }

    /** The callee's question when {@code state == "input_required"}, else null. */
    @SuppressWarnings("unchecked")
    public String getQuestion() {
        if (statusMessage == null || statusMessage.isEmpty()) return null;
        Object parts = statusMessage.get("parts");
        String q = parts instanceof List ? joinText((List<Map<String, Object>>) parts) : "";
        return q.isEmpty() ? null : q;
    }

    // --- reading the answer --------------------------------------------------

    public Map<String, Object> artifact(String name) {
        if (artifacts == null) return null;
        for (Map<String, Object> a : artifacts) {
            if (Objects.equals(a.get("name"), name)) return a;
        }
        return null;
    }

    /** Text parts of every artifact, joined. */
    public String artifactText() {
        return artifactText(null);
    }

    /** Text parts of the named artifact (or of every artifact when {@code name} is null), joined. */
    @SuppressWarnings("unchecked")
    public String artifactText(String name) {
        List<Map<String, Object>> arts = new ArrayList<>();
        if (name != null) {
            Map<String, Object> a = artifact(name);
            if (a != null) arts.add(a);
        } else if (artifacts != null) {
            arts.addAll(artifacts);
        }
        List<Map<String, Object>> parts = new ArrayList<>();
        for (Map<String, Object> a : arts) {
            Object p = a.get("parts");
            if (p instanceof List) parts.addAll((List<Map<String, Object>>) p);
        }
        return joinText(parts);
    }

    // --- replying, from inside serve() ---------------------------------------

    /** Complete the task with a text answer. */
    public TaskReply done(String text) {
        return done(text, null, null, "response");
    }

    /** Complete the task with a structured answer. */
    public TaskReply done(Map<String, Object> data) {
        return done(null, data, null, "response");
    }

    /**
     * Complete the task. {@code text}/{@code data} become one artifact named
     * {@code name} (default {@code "response"}), placed before {@code artifacts}.
     */
    public TaskReply done(String text, Map<String, Object> data, List<Map<String, Object>> artifacts, String name) {
        List<Map<String, Object>> arts = artifacts == null ? new ArrayList<>() : new ArrayList<>(artifacts);
        if (text != null || data != null) {
            Map<String, Object> a = new LinkedHashMap<>();
            a.put("name", name == null ? "response" : name);
            a.put("parts", MessageParts.of(text, data, null));
            arts.add(0, a);
        }
        return new TaskReply("completed", arts, null, null);
    }

    /** Pause the task and ask the caller a question ({@code input_required}). */
    public TaskReply ask(String question) {
        Map<String, Object> part = new LinkedHashMap<>();
        part.put("text", question);
        Map<String, Object> message = new LinkedHashMap<>();
        message.put("role", "agent");
        message.put("parts", Collections.singletonList(part));
        return new TaskReply("input_required", null, message, null);
    }

    /** Fail the task; the error is truncated to 4000 chars. */
    public TaskReply fail(String error) {
        String e = error == null ? "" : error;
        return new TaskReply("failed", null, null, e.length() > 4000 ? e.substring(0, 4000) : e);
    }

    private static String joinText(List<Map<String, Object>> parts) {
        return parts.stream()
                .map(p -> p.get("text"))
                .filter(Objects::nonNull)
                .map(Object::toString)
                .collect(Collectors.joining("\n"));
    }

    @Override
    public String toString() {
        return "Task{id=" + id + ", state=" + getState() + ", contextId=" + contextId + ", callId=" + callId + "}";
    }
}
