package ai.forgebench.types;

import java.util.ArrayList;
import java.util.List;

/** One SSE event from a streamed chat completion (OpenAI-shaped). */
public final class ChatCompletionChunk {
    private String id = "";
    private String object = "chat.completion.chunk";
    private long created;
    private String model = "";
    private List<ChatCompletionChunkChoice> choices = new ArrayList<>();

    public String getId() { return id; }
    public String getObject() { return object; }
    public long getCreated() { return created; }
    public String getModel() { return model; }
    public List<ChatCompletionChunkChoice> getChoices() { return choices; }
}
