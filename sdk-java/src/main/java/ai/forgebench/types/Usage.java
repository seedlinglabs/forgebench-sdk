package ai.forgebench.types;

/** Token usage of one governed chat completion. */
public final class Usage {
    private int promptTokens;
    private int completionTokens;
    private int totalTokens;

    public int getPromptTokens() { return promptTokens; }
    public int getCompletionTokens() { return completionTokens; }
    public int getTotalTokens() { return totalTokens; }

    @Override
    public String toString() {
        return "Usage{prompt=" + promptTokens + ", completion=" + completionTokens + ", total=" + totalTokens + "}";
    }
}
