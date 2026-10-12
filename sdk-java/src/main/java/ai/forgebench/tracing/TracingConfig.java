package ai.forgebench.tracing;

import com.fasterxml.jackson.databind.JsonNode;

import java.time.Duration;
import java.util.Locale;
import java.util.Objects;
import java.util.function.UnaryOperator;

/**
 * How a client records and sends traces.
 *
 * <pre>{@code
 * Forgebench fb = Forgebench.builder()
 *     .tracing(TracingConfig.builder().sampleRate(0.2).environment("prod").build())
 *     .build();
 * }</pre>
 *
 * <p>Without {@code .tracing(...)} the client uses {@link #fromEnv()}; a config
 * passed explicitly is used as-is (and validated: a bad value throws).
 */
public final class TracingConfig {
    private final boolean enabled;
    private final double sampleRate;
    private final String environment;
    private final String release;
    private final UnaryOperator<JsonNode> mask;
    private final int flushAt;
    private final Duration flushInterval;
    private final int maxQueueSize;

    private TracingConfig(Builder b) {
        if (!(b.sampleRate >= 0.0 && b.sampleRate <= 1.0)) {
            throw new IllegalArgumentException("sampleRate must be between 0.0 and 1.0");
        }
        if (b.flushAt < 1 || b.maxQueueSize < 1 || b.flushInterval.isZero() || b.flushInterval.isNegative()) {
            throw new IllegalArgumentException("flushAt, flushInterval and maxQueueSize must be positive");
        }
        this.enabled = b.enabled;
        this.sampleRate = b.sampleRate;
        this.environment = b.environment;
        this.release = b.release;
        this.mask = b.mask;
        this.flushAt = b.flushAt;
        this.flushInterval = b.flushInterval;
        this.maxQueueSize = b.maxQueueSize;
    }

    public static Builder builder() {
        return new Builder();
    }

    /**
     * {@code FORGEBENCH_TRACING_ENABLED}, {@code FORGEBENCH_SAMPLE_RATE},
     * {@code FORGEBENCH_ENVIRONMENT} and {@code FORGEBENCH_RELEASE}, else defaults.
     */
    public static TracingConfig fromEnv() {
        return fromEnv(System::getenv);
    }

    /** {@link #fromEnv()} over a given environment lookup (tests, embedded config). */
    public static TracingConfig fromEnv(UnaryOperator<String> env) {
        Builder b = builder();
        String enabled = env.apply("FORGEBENCH_TRACING_ENABLED");
        if (enabled != null) {
            String v = enabled.trim().toLowerCase(Locale.ROOT);
            b.enabled(!(v.equals("0") || v.equals("false") || v.equals("no") || v.equals("off")));
        }
        b.sampleRate(envSampleRate(env.apply("FORGEBENCH_SAMPLE_RATE")));
        b.environment(blankToNull(env.apply("FORGEBENCH_ENVIRONMENT")));
        b.release(blankToNull(env.apply("FORGEBENCH_RELEASE")));
        return b.build();
    }

    public boolean isEnabled() { return enabled; }
    public double getSampleRate() { return sampleRate; }
    public String getEnvironment() { return environment; }
    public String getRelease() { return release; }
    public UnaryOperator<JsonNode> getMask() { return mask; }
    public int getFlushAt() { return flushAt; }
    public Duration getFlushInterval() { return flushInterval; }
    public int getMaxQueueSize() { return maxQueueSize; }

    /**
     * The rate, or 1.0 when unset, blank or not a rate — a typo in the
     * environment is logged, never fatal.
     */
    private static double envSampleRate(String raw) {
        if (raw == null || raw.isBlank()) return 1.0;
        try {
            double rate = Double.parseDouble(raw.trim());
            if (rate >= 0.0 && rate <= 1.0) return rate;
        } catch (NumberFormatException ignored) {
            // falls through to the warning
        }
        System.getLogger("forgebench.tracing").log(System.Logger.Level.WARNING,
                "forgebench tracing: ignoring FORGEBENCH_SAMPLE_RATE={0} (want 0.0-1.0); using 1.0", raw);
        return 1.0;
    }

    private static String blankToNull(String s) {
        return s == null || s.isBlank() ? null : s;
    }

    public static final class Builder {
        private boolean enabled = true;
        private double sampleRate = 1.0;
        private String environment;
        private String release;
        private UnaryOperator<JsonNode> mask;
        private int flushAt = 50;
        private Duration flushInterval = Duration.ofSeconds(1);
        private int maxQueueSize = 10_000;

        private Builder() {}

        /** Off makes every tracing call a no-op; model calls still work. Default on. */
        public Builder enabled(boolean enabled) {
            this.enabled = enabled;
            return this;
        }

        /** Share of traces kept (0.0–1.0), decided once per trace. Default 1.0. */
        public Builder sampleRate(double sampleRate) {
            this.sampleRate = sampleRate;
            return this;
        }

        /** Stamped on every trace, for filtering (e.g. {@code production}). */
        public Builder environment(String environment) {
            this.environment = environment;
            return this;
        }

        /** Your app version, stamped on every trace. */
        public Builder release(String release) {
            this.release = release;
            return this;
        }

        /** Runs on every input, output and metadata before it leaves the process. */
        public Builder mask(UnaryOperator<JsonNode> mask) {
            this.mask = mask;
            return this;
        }

        /** Send once this many steps are waiting (default 50, at most 100 per request). */
        public Builder flushAt(int flushAt) {
            this.flushAt = flushAt;
            return this;
        }

        /** ...or at least this often (default 1s). */
        public Builder flushInterval(Duration flushInterval) {
            this.flushInterval = Objects.requireNonNull(flushInterval, "flushInterval");
            return this;
        }

        /** Beyond this many waiting steps the oldest are dropped (default 10000). */
        public Builder maxQueueSize(int maxQueueSize) {
            this.maxQueueSize = maxQueueSize;
            return this;
        }

        public TracingConfig build() {
            return new TracingConfig(this);
        }
    }
}
