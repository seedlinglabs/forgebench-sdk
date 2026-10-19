package ai.forgebench;

import ai.forgebench.tracing.Tracing;
import ai.forgebench.types.Agent;
import ai.forgebench.types.ChatCompletion;
import ai.forgebench.types.MeteringSummary;
import ai.forgebench.types.Run;
import ai.forgebench.types.SpeechAudio;
import ai.forgebench.types.Task;
import ai.forgebench.types.TaskReply;
import ai.forgebench.types.ToolBinding;
import ai.forgebench.types.ToolOutcomeReceipt;
import ai.forgebench.types.Transcription;
import ai.forgebench.types.WhoAmI;

import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionException;
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
    private final AsyncAudio audio;
    private final AsyncVoice voice;
    private final AsyncWorkflows workflows;

    AsyncForgebench(Forgebench sync, ExecutorService executor) {
        this.sync = sync;
        this.ownsExecutor = executor == null;
        this.executor = executor != null ? executor : Executors.newCachedThreadPool(new DaemonThreads());
        this.chat = new AsyncChat();
        this.agents = new AsyncAgents();
        this.runs = new AsyncRuns();
        this.agentTools = new AsyncAgentTools();
        this.account = new AsyncAccount();
        this.audio = new AsyncAudio();
        this.voice = new AsyncVoice();
        this.workflows = new AsyncWorkflows();
    }

    // The current tracing step is per thread: carry it onto the executor so a
    // call made inside a step still nests under it.
    private <T> CompletableFuture<T> async(Supplier<T> call) {
        return CompletableFuture.supplyAsync(Tracing.wrapSupplier(call), executor);
    }

    /**
     * As {@link #async} for a call that opens a stream. If the future is
     * cancelled (or the call is rejected) before the caller receives the
     * stream, the stream is closed here: nobody else holds it, and an open
     * one keeps its connection and buffers the whole body.
     */
    private <T extends AutoCloseable> CompletableFuture<T> asyncStream(Supplier<T> open) {
        CompletableFuture<T> out = new CompletableFuture<>();
        Supplier<T> call = Tracing.wrapSupplier(open);
        try {
            executor.execute(() -> {
                T stream;
                try {
                    stream = call.get();
                } catch (Throwable t) {
                    // Same shape as supplyAsync: the typed exception wrapped in a CompletionException.
                    out.completeExceptionally(t instanceof CompletionException ? t : new CompletionException(t));
                    return;
                }
                if (!out.complete(stream)) {
                    try {
                        stream.close();
                    } catch (Exception ignored) {
                        // Best effort: the caller already walked away.
                    }
                }
            });
        } catch (RuntimeException rejected) {
            out.completeExceptionally(new CompletionException(rejected));
        }
        return out;
    }

    public String getBaseUrl() { return sync.getBaseUrl(); }
    public AsyncChat chat() { return chat; }
    public AsyncAgents agents() { return agents; }
    public AsyncRuns runs() { return runs; }
    public AsyncAgentTools agentTools() { return agentTools; }
    public AsyncAccount account() { return account; }
    public AsyncAudio audio() { return audio; }
    public AsyncVoice voice() { return voice; }
    public AsyncWorkflows workflows() { return workflows; }

    /** The workflow helper is blocking (each call is a chat completion); run it on your own executor. */
    public Workflow workflow(String slug, Map<String, String> keys) { return sync.workflow(slug, keys); }

    /** The tracing API, shared with the blocking client. See {@link Tracing}. */
    public Tracing tracing() { return sync.tracing(); }

    /** Send every finished step now, off the caller's thread. */
    public CompletableFuture<Boolean> flush() { return CompletableFuture.supplyAsync(sync::flush, executor); }

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
            return asyncStream(() -> sync.chat().completions().createStream(request));
        }
    }

    public final class AsyncAgents {
        private AsyncAgents() {}

        public CompletableFuture<List<Agent>> list() { return async(() -> sync.agents().list()); }
        public CompletableFuture<Agent> create(AgentCreateParams params) { return async(() -> sync.agents().create(params)); }
        public CompletableFuture<Map<String, Object>> register(Map<String, ?> body) { return async(() -> sync.agents().register(body)); }
        public CompletableFuture<Run> run(String agentId) { return async(() -> sync.agents().run(agentId)); }
        public CompletableFuture<Run> run(String agentId, RunParams params) { return async(() -> sync.agents().run(agentId, params)); }
        public CompletableFuture<Map<String, Object>> run(String agentId, MessageRunParams params) { return async(() -> sync.agents().run(agentId, params)); }

        /**
         * Completes once the response headers arrive; iterate the stream on any
         * thread and close it when done. Cancelling the future before then closes it for you.
         */
        public CompletableFuture<AgentRunStream> runStream(String agentId, MessageRunParams params) {
            return asyncStream(() -> sync.agents().runStream(agentId, params));
        }
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

    public final class AsyncAudio {
        private final AsyncTranscriptions transcriptions = new AsyncTranscriptions();
        private final AsyncSpeech speech = new AsyncSpeech();

        private AsyncAudio() {}

        public AsyncTranscriptions transcriptions() { return transcriptions; }
        public AsyncSpeech speech() { return speech; }
    }

    public final class AsyncTranscriptions {
        private AsyncTranscriptions() {}

        public CompletableFuture<Transcription> create(TranscriptionParams p) { return async(() -> sync.audio().transcriptions().create(p)); }
    }

    public final class AsyncSpeech {
        private AsyncSpeech() {}

        public CompletableFuture<SpeechAudio> create(SpeechParams p) { return async(() -> sync.audio().speech().create(p)); }
    }

    public final class AsyncWorkflows {
        private AsyncWorkflows() {}

        public CompletableFuture<List<Map<String, Object>>> list() { return async(() -> sync.workflows().list()); }
        public CompletableFuture<Map<String, Object>> get(String slug) { return async(() -> sync.workflows().get(slug)); }
        public CompletableFuture<Map<String, Object>> get(String slug, int days) { return async(() -> sync.workflows().get(slug, days)); }
        public CompletableFuture<Map<String, Object>> checks(String slug) { return async(() -> sync.workflows().checks(slug)); }
        public CompletableFuture<Map<String, Object>> register(Map<String, ?> body) { return async(() -> sync.workflows().register(body)); }
    }

    public final class AsyncVoice {
        private final AsyncVoicePolicy policy = new AsyncVoicePolicy();
        private final AsyncVoiceAssets assets = new AsyncVoiceAssets();
        private final AsyncVoiceConsents consents = new AsyncVoiceConsents();

        private AsyncVoice() {}

        public AsyncVoicePolicy policy() { return policy; }
        public AsyncVoiceAssets assets() { return assets; }
        public AsyncVoiceConsents consents() { return consents; }
    }

    public final class AsyncVoicePolicy {
        private AsyncVoicePolicy() {}

        public CompletableFuture<Map<String, Object>> get() { return async(() -> sync.voice().policy().get()); }
        public CompletableFuture<Map<String, Object>> update(Map<String, ?> fields) { return async(() -> sync.voice().policy().update(fields)); }
    }

    public final class AsyncVoiceAssets {
        private AsyncVoiceAssets() {}

        public CompletableFuture<List<Map<String, Object>>> list() { return async(() -> sync.voice().assets().list()); }
        public CompletableFuture<Map<String, Object>> create(Map<String, ?> fields) { return async(() -> sync.voice().assets().create(fields)); }
        public CompletableFuture<Map<String, Object>> update(String assetId, VoiceAssetUpdate update) { return async(() -> sync.voice().assets().update(assetId, update)); }
        public CompletableFuture<Map<String, Object>> revoke(String assetId) { return async(() -> sync.voice().assets().revoke(assetId)); }
    }

    public final class AsyncVoiceConsents {
        private AsyncVoiceConsents() {}

        public CompletableFuture<List<Map<String, Object>>> list() { return async(() -> sync.voice().consents().list()); }
        public CompletableFuture<Map<String, Object>> create(Map<String, ?> fields) { return async(() -> sync.voice().consents().create(fields)); }
        public CompletableFuture<Map<String, Object>> revoke(String consentId) { return async(() -> sync.voice().consents().revoke(consentId)); }
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
