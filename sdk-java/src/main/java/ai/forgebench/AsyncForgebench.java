package ai.forgebench;

import ai.forgebench.types.Agent;
import ai.forgebench.types.ChatCompletion;
import ai.forgebench.types.MeteringSummary;
import ai.forgebench.types.Run;
import ai.forgebench.types.Task;
import ai.forgebench.types.TaskReply;
import ai.forgebench.types.ToolBinding;
import ai.forgebench.types.ToolOutcomeReceipt;
import ai.forgebench.types.WhoAmI;

import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.Supplier;

/**
 * Asynchronous client: the same resources and semantics as {@link Forgebench},
 * each call returning a {@link CompletableFuture}.
 *
 * <pre>{@code
 * try (AsyncForgebench client = Forgebench.builder().apiKey("sk_...").buildAsync()) {
 *     client.chat().completions().create(req)
 *           .thenAccept(r -> System.out.println(r.getContent()))
 *           .join();
 * }
 * }</pre>
 *
 * <p>Calls run on the executor given to {@link Forgebench.Builder#buildAsync(ExecutorService)},
 * or on a pool of daemon threads this client owns and shuts down on {@link #close()}.
 * A failed call completes its future exceptionally with the same typed
 * exception the sync client throws (wrapped in a {@code CompletionException} by
 * {@code join()}).
 */
public final class AsyncForgebench implements AutoCloseable {
    private final Forgebench sync;
    private final ExecutorService executor;
    private final boolean ownsExecutor;
    private final AsyncChat chat;
    private final AsyncAgents agents;
    private final AsyncRuns runs;
    private final AsyncAgentTools agentTools;
    private final AsyncAccount account;

    AsyncForgebench(Forgebench sync, ExecutorService executor) {
        this.sync = sync;
        this.ownsExecutor = executor == null;
        this.executor = executor != null ? executor : Executors.newCachedThreadPool(new DaemonThreads());
        this.chat = new AsyncChat();
        this.agents = new AsyncAgents();
        this.runs = new AsyncRuns();
        this.agentTools = new AsyncAgentTools();
        this.account = new AsyncAccount();
    }

    private <T> CompletableFuture<T> async(Supplier<T> call) {
        return CompletableFuture.supplyAsync(call, executor);
    }

    public String getBaseUrl() { return sync.getBaseUrl(); }
    public AsyncChat chat() { return chat; }
    public AsyncAgents agents() { return agents; }
    public AsyncRuns runs() { return runs; }
    public AsyncAgentTools agentTools() { return agentTools; }
    public AsyncAccount account() { return account; }

    /** The blocking client behind this one, sharing its connection pool. */
    public Forgebench sync() { return sync; }

    public CompletableFuture<WhoAmI> whoami() { return account.whoami(); }
    public CompletableFuture<MeteringSummary> meteringSummary() { return account.meteringSummary(); }

    /** Shuts down the executor if this client created it; a caller-supplied one is left alone. */
    @Override
    public void close() {
        if (ownsExecutor) executor.shutdown();
        sync.close();
    }

    public final class AsyncChat {
        private final AsyncCompletions completions = new AsyncCompletions();

        private AsyncChat() {}

        public AsyncCompletions completions() { return completions; }
    }

    public final class AsyncCompletions {
        private AsyncCompletions() {}

        public CompletableFuture<ChatCompletion> create(ChatCompletionRequest request) {
            return async(() -> sync.chat().completions().create(request));
        }

        /** Completes once the response headers arrive; iterate the stream on any thread. */
        public CompletableFuture<ChatCompletionStream> createStream(ChatCompletionRequest request) {
            return async(() -> sync.chat().completions().createStream(request));
        }
    }

    public final class AsyncAgents {
        private AsyncAgents() {}

        public CompletableFuture<List<Agent>> list() { return async(() -> sync.agents().list()); }
        public CompletableFuture<Agent> create(AgentCreateParams params) { return async(() -> sync.agents().create(params)); }
        public CompletableFuture<Run> run(String agentId) { return async(() -> sync.agents().run(agentId)); }
        public CompletableFuture<Run> run(String agentId, RunParams params) { return async(() -> sync.agents().run(agentId, params)); }
        public CompletableFuture<Task> call(String callee, String text) { return async(() -> sync.agents().call(callee, text)); }
        public CompletableFuture<Task> call(String callee, CallParams params) { return async(() -> sync.agents().call(callee, params)); }
        public CompletableFuture<Task> task(String callee, String taskId) { return async(() -> sync.agents().task(callee, taskId)); }
        public CompletableFuture<Task> task(String callee, String taskId, Duration wait) { return async(() -> sync.agents().task(callee, taskId, wait)); }
        public CompletableFuture<Task> cancel(String callee, String taskId) { return async(() -> sync.agents().cancel(callee, taskId)); }
        public CompletableFuture<Task> nextTask() { return async(() -> sync.agents().nextTask()); }
        public CompletableFuture<Task> nextTask(Duration wait) { return async(() -> sync.agents().nextTask(wait)); }
        public CompletableFuture<Task> reply(String taskId, TaskReply reply) { return async(() -> sync.agents().reply(taskId, reply)); }

        /** The serve loop on the executor; completes with the number of tasks handled. */
        public CompletableFuture<Integer> serve(TaskHandler handler) { return async(() -> sync.agents().serve(handler)); }
        public CompletableFuture<Integer> serve(TaskHandler handler, ServeOptions options) { return async(() -> sync.agents().serve(handler, options)); }
    }

    public final class AsyncRuns {
        private AsyncRuns() {}

        public CompletableFuture<Run> create(Map<String, ?> input) { return async(() -> sync.runs().create(input)); }
        public CompletableFuture<Run> create(String agentId, Map<String, ?> input, String model) { return async(() -> sync.runs().create(agentId, input, model)); }
        public CompletableFuture<Run> get(String runId) { return async(() -> sync.runs().get(runId)); }
        public CompletableFuture<Run> waitFor(String runId) { return async(() -> sync.runs().waitFor(runId)); }
        public CompletableFuture<Run> waitFor(String runId, Duration timeout, Duration pollInterval) {
            return async(() -> sync.runs().waitFor(runId, timeout, pollInterval));
        }
    }

    public final class AsyncAgentTools {
        private AsyncAgentTools() {}

        public CompletableFuture<List<ToolBinding>> list() { return async(() -> sync.agentTools().list()); }
        public CompletableFuture<List<Map<String, Object>>> openaiSchema() { return async(() -> sync.agentTools().openaiSchema()); }
        public CompletableFuture<List<Map<String, Object>>> openaiSchema(Map<String, Map<String, Object>> parameters) {
            return async(() -> sync.agentTools().openaiSchema(parameters));
        }
        public CompletableFuture<ToolOutcomeReceipt> report(String callId, String toolName, Object result, String error, Long latencyMs) {
            return async(() -> sync.agentTools().report(callId, toolName, result, error, latencyMs));
        }
        public CompletableFuture<List<Map<String, Object>>> dispatch(List<Map<String, Object>> toolCalls, ToolExecutor execute, String callId) {
            return async(() -> sync.agentTools().dispatch(toolCalls, execute, callId));
        }
    }

    public final class AsyncAccount {
        private AsyncAccount() {}

        public CompletableFuture<WhoAmI> whoami() { return async(() -> sync.account().whoami()); }
        public CompletableFuture<MeteringSummary> meteringSummary() { return async(() -> sync.account().meteringSummary()); }
    }

    private static final class DaemonThreads implements ThreadFactory {
        private final AtomicInteger n = new AtomicInteger();

        @Override
        public Thread newThread(Runnable r) {
            Thread t = new Thread(r, "forgebench-async-" + n.incrementAndGet());
            t.setDaemon(true);
            return t;
        }
    }
}
