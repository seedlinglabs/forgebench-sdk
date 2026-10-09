package ai.forgebench;

import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Objects;

/**
 * Parameters of {@code agents().run(agentId, ...)} and {@code runs().create(...)}.
 *
 * <p>{@code waitForCompletion} makes {@code agents().run} block, polling until
 * the run is terminal or {@code timeout} passes.
 */
public final class RunParams {
    final Map<String, Object> input;
    final String model;
    final boolean waitForCompletion;
    final Duration timeout;
    final Duration pollInterval;

    private RunParams(Builder b) {
        this.input = b.input;
        this.model = b.model;
        this.waitForCompletion = b.waitForCompletion;
        this.timeout = b.timeout;
        this.pollInterval = b.pollInterval;
    }

    public static Builder builder() {
        return new Builder();
    }

    static RunParams defaults() {
        return builder().build();
    }

    public static final class Builder {
        private Map<String, Object> input;
        private String model;
        private boolean waitForCompletion;
        private Duration timeout = Duration.ofSeconds(60);
        private Duration pollInterval = Duration.ofMillis(500);

        private Builder() {}

        public Builder input(Map<String, ?> input) {
            this.input = input == null ? null : new LinkedHashMap<>(input);
            return this;
        }

        /** Overrides the agent's model (for {@code runs().create}, defaults to {@code mock-gpt}). */
        public Builder model(String model) {
            this.model = model;
            return this;
        }

        public Builder waitForCompletion(boolean wait) {
            this.waitForCompletion = wait;
            return this;
        }

        /** How long a blocking run may take. Default 60s. */
        public Builder timeout(Duration timeout) {
            this.timeout = Objects.requireNonNull(timeout, "timeout");
            return this;
        }

        /** Default 500ms. */
        public Builder pollInterval(Duration pollInterval) {
            this.pollInterval = Objects.requireNonNull(pollInterval, "pollInterval");
            return this;
        }

        public RunParams build() {
            return new RunParams(this);
        }
    }
}
