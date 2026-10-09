package ai.forgebench;

import java.net.http.HttpClient;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Objects;
import java.util.function.UnaryOperator;

/**
 * Client for the Forgebench control plane.
 *
 * <pre>{@code
 * Forgebench client = Forgebench.builder().apiKey("sk_...").build();
 * ChatCompletion resp = client.chat().completions().create(
 *     ChatCompletionRequest.builder()
 *         .model("mock-gpt")
 *         .addMessage(ChatMessage.user("Prove the governed path works."))
 *         .build());
 * System.out.println(resp.getContent());
 * }</pre>
 *
 * <p>The API key defaults to the {@code FORGEBENCH_API_KEY} environment
 * variable and the base URL to {@code FORGEBENCH_BASE_URL} (falling back to
 * the production control plane at https://api.forgebench.ai), so
 * {@code Forgebench.builder().build()} works in production and local dev only
 * needs {@code .baseUrl("http://localhost:8000")}.
 *
 * <p>Thread-safe: share one client across threads.
 */
public final class Forgebench implements AutoCloseable {
    private final Transport transport;
    private final Chat chat;
    private final Agents agents;
    private final Runs runs;
    private final AgentTools agentTools;
    private final Account account;

    private Forgebench(Builder b) {
        String apiKey = b.apiKey != null ? b.apiKey : b.env.apply("FORGEBENCH_API_KEY");
        String baseUrl = b.baseUrl;
        if (baseUrl == null || baseUrl.isEmpty()) baseUrl = b.env.apply("FORGEBENCH_BASE_URL");
        if (baseUrl == null || baseUrl.isEmpty()) baseUrl = Transport.DEFAULT_BASE_URL;
        this.transport = new Transport(apiKey, baseUrl, b.timeout, b.streamTimeout, b.maxRetries,
                b.defaultHeaders, b.httpClient);
        this.chat = new Chat(transport);
        this.runs = new Runs(transport);
        this.agents = new Agents(transport, runs);
        this.agentTools = new AgentTools(transport);
        this.account = new Account(transport);
    }

    public static Builder builder() {
        return new Builder();
    }

    /** The resolved base URL, normalized with a single trailing slash. */
    public String getBaseUrl() {
        return transport.baseUrl();
    }

    public Chat chat() {
        return chat;
    }

    /** Agents: registry, runs, and agent-to-agent tasks (call / serve). */
    public Agents agents() {
        return agents;
    }

    public Runs runs() {
        return runs;
    }

    /** This agent's tool allowlist, and reporting what its tools returned. */
    public AgentTools agentTools() {
        return agentTools;
    }

    public Account account() {
        return account;
    }

    /** Shortcut for {@code account().whoami()} — verify auth + tenant. */
    public ai.forgebench.types.WhoAmI whoami() {
        return account.whoami();
    }

    /** Shortcut for {@code account().meteringSummary()} — budget state. */
    public ai.forgebench.types.MeteringSummary meteringSummary() {
        return account.meteringSummary();
    }

    /**
     * No-op today — {@link HttpClient} has no close before Java 21 and releases
     * idle connections on its own. Kept so callers can use try-with-resources
     * now and keep working if the client ever holds closable resources.
     */
    @Override
    public void close() {
    }

    public static final class Builder {
        private String apiKey;
        private String baseUrl;
        private Duration timeout = Transport.DEFAULT_TIMEOUT;
        private Duration streamTimeout = Transport.DEFAULT_STREAM_TIMEOUT;
        private int maxRetries = Transport.DEFAULT_MAX_RETRIES;
        private final Map<String, String> defaultHeaders = new LinkedHashMap<>();
        private HttpClient httpClient;
        private UnaryOperator<String> env = System::getenv;

        private Builder() {}

        /** An {@code sk_...} key. Defaults to {@code $FORGEBENCH_API_KEY}. */
        public Builder apiKey(String apiKey) {
            this.apiKey = apiKey;
            return this;
        }

        /** Defaults to {@code $FORGEBENCH_BASE_URL}, then https://api.forgebench.ai. */
        public Builder baseUrl(String baseUrl) {
            this.baseUrl = baseUrl;
            return this;
        }

        /** Connect timeout, time to response headers, and the per-read wait on a stream. Default 60s. */
        public Builder timeout(Duration timeout) {
            this.timeout = Objects.requireNonNull(timeout, "timeout");
            return this;
        }

        /** Overall wall-clock cap on one streamed call. Default 300s. */
        public Builder streamTimeout(Duration streamTimeout) {
            this.streamTimeout = Objects.requireNonNull(streamTimeout, "streamTimeout");
            return this;
        }

        /** Retries on connection errors and 429/500/502/503/504. Default 2. */
        public Builder maxRetries(int maxRetries) {
            this.maxRetries = maxRetries;
            return this;
        }

        /** A header sent on every request. */
        public Builder defaultHeader(String name, String value) {
            this.defaultHeaders.put(name, value);
            return this;
        }

        /** Bring your own {@link HttpClient} (proxy, SSL context, executor). */
        public Builder httpClient(HttpClient httpClient) {
            this.httpClient = httpClient;
            return this;
        }

        // Tests only: the Python suite monkeypatches the environment; Java can't.
        Builder env(UnaryOperator<String> env) {
            this.env = env;
            return this;
        }

        public Forgebench build() {
            return new Forgebench(this);
        }

        /** The asynchronous client, on a daemon thread pool it owns. */
        public AsyncForgebench buildAsync() {
            return new AsyncForgebench(build(), null);
        }

        /** The asynchronous client, on your executor (left running on close). */
        public AsyncForgebench buildAsync(java.util.concurrent.ExecutorService executor) {
            return new AsyncForgebench(build(), java.util.Objects.requireNonNull(executor, "executor"));
        }
    }
}
