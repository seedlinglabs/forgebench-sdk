package ai.forgebench.types;

/** One choice of a {@link ChatCompletionChunk}. */
public final class ChatCompletionChunkChoice {
    private int index;
    private ChatCompletionChunkDelta delta = new ChatCompletionChunkDelta();
    private String finishReason;

    public int getIndex() { return index; }
    public ChatCompletionChunkDelta getDelta() { return delta; }
    public String getFinishReason() { return finishReason; }
}
