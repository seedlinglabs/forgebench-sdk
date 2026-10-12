package ai.forgebench.tracing;

/** How a step went. {@code ERROR} is set for you when a traced call throws. */
public enum ObservationLevel {
    DEBUG, DEFAULT, WARNING, ERROR
}
