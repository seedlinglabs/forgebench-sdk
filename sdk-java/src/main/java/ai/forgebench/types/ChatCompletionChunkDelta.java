package ai.forgebench.types;

import java.util.List;
import java.util.Map;

/** The incremental delta of one streamed choice. */
public final class ChatCompletionChunkDelta {
    private String role;
    private String content;
    // Fragments of the tool calls the model is emitting, OpenAI-shaped: each
    // carries an "index" naming which call it extends, and pieces of "id",
    // "function.name" and "function.arguments" to concatenate in order. Null
    // when this chunk carries none.
    private List<Map<String, Object>> toolCalls;

    public String getRole() { return role; }
    public String getContent() { return content; }
    public List<Map<String, Object>> getToolCalls() { return toolCalls; }
}
