package ai.forgebench;

import java.time.Duration;
import java.util.Objects;
import java.util.function.BooleanSupplier;

/** Options of {@code agents().serve(...)}. */
public final class ServeOptions {
    final Duration wait;
    final Integer maxTasks;
    final BooleanSupplier stop;
    final Duration heartbeat;

    private ServeOptions(Builder b) {
        this.wait = b.wait;
        this.maxTasks = b.maxTasks;
        this.stop = b.stop;
        this.heartbeat = b.heartbeat;
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
        private Duration heartbeat = Duration.ofMinutes(5);

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

        /**
         * While a handler runs, post {@code state: working} this often so a
         * long task isn't taken for a crashed callee. Default 5 minutes;
         * {@link Duration#ZERO} turns it off.
         */
        public Builder heartbeat(Duration heartbeat) {
            this.heartbeat = Objects.requireNonNull(heartbeat, "heartbeat");
            return this;
        }

        public ServeOptions build() {
            return new ServeOptions(this);
        }
    }
}
