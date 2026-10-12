package ai.forgebench;

import ai.forgebench.tracing.Tracing;

/** The chat resource group: {@code client.chat().completions()}. */
public final class Chat {
    private final Completions completions;

    Chat(Transport transport, Tracing tracing) {
        this.completions = new Completions(transport, tracing);
    }

    public Completions completions() {
        return completions;
    }
}
