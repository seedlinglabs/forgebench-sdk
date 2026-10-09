package ai.forgebench;

import java.time.Duration;
import java.util.Objects;
import java.util.function.BooleanSupplier;

/** Options of {@code agents().serve(...)}. */
public final class ServeOptions {
    final Duration wait;
    final Integer maxTasks;
    final BooleanSupplier stop;

    private ServeOptions(Builder b) {
        this.wait = b.wait;
        this.maxTasks = b.maxTasks;
        this.stop = b.stop;
    }

    public static Builder builder() {
        return new Builder();
    }

    static ServeOptions defaults() {
        return builder().build();
    }

    public static final class Builder {
        private Duration wait = Duration.ofSeconds(20);
        private Integer maxTasks;
        private BooleanSupplier stop;

        private Builder() {}

        /** Long-poll window per claim attempt. Default 20s. */
        public Builder wait(Duration wait) {
            this.wait = Objects.requireNonNull(wait, "wait");
            return this;
        }

        /** Return after handling this many tasks. Default: serve forever. */
        public Builder maxTasks(int maxTasks) {
            this.maxTasks = maxTasks;
            return this;
        }

        /** Polled between tasks for a clean shutdown. */
        public Builder stop(BooleanSupplier stop) {
            this.stop = stop;
            return this;
        }

        public ServeOptions build() {
            return new ServeOptions(this);
        }
    }
}
