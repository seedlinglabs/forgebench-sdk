package ai.forgebench;

/** The chat resource group: {@code client.chat().completions()}. */
public final class Chat {
    private final Completions completions;

    Chat(Transport transport) {
        this.completions = new Completions(transport);
    }

    public Completions completions() {
        return completions;
    }
}
