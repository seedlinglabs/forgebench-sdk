package ai.forgebench.types;

/** One choice of a {@link ChatCompletion}. */
public final class ChatChoice {
    private int index;
    private ChatChoiceMessage message = new ChatChoiceMessage();
    private String finishReason;

    public int getIndex() { return index; }
    public ChatChoiceMessage getMessage() { return message; }
    public String getFinishReason() { return finishReason; }
}
