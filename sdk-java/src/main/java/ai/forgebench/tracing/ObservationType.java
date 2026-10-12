package ai.forgebench.tracing;

/** What kind of step an observation is. Model calls ({@code generation}) are recorded by the server. */
public enum ObservationType {
    SPAN, AGENT, TOOL, CHAIN, RETRIEVER, EMBEDDING, EVALUATOR, GUARDRAIL, EVENT;

    /** The wire value: the lowercase name. */
    public String wireName() {
        return name().toLowerCase(java.util.Locale.ROOT);
    }
}
