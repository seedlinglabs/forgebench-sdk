package ai.forgebench.tracing;

import com.fasterxml.jackson.databind.JsonNode;

import java.util.Map;

/**
 * Posts one batch to {@code /v1/observations/batch} and returns the reply.
 * Supplied by {@link ai.forgebench.Forgebench}; you never implement this.
 */
@FunctionalInterface
public interface BatchSender {
    JsonNode send(Map<String, Object> payload);
}
