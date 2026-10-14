package ai.forgebench;

import ai.forgebench.errors.NotFoundException;
import ai.forgebench.types.SpeechAudio;
import ai.forgebench.types.Transcription;
import com.fasterxml.jackson.databind.JsonNode;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionException;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** The async client must mirror the blocking one: audio, voice and streamed agent runs included. */
class AsyncParityTest {
    private MockServer server;

    @BeforeEach
    void start() throws Exception {
        server = new MockServer();
    }

    @AfterEach
    void stop() {
        server.close();
    }

    private AsyncForgebench client() {
        return Forgebench.builder().apiKey("sk_test_abc123").baseUrl(server.baseUrl()).maxRetries(0).buildAsync();
    }

    /** Every public method of each blocking resource has an async twin with the same name and parameters. */
    @Test
    void everyBlockingMethodHasAnAsyncTwin() {
        Map<Class<?>, Class<?>> pairs = Map.ofEntries(
                Map.entry(Forgebench.class, AsyncForgebench.class),
                Map.entry(Chat.class, AsyncForgebench.AsyncChat.class),
                Map.entry(Completions.class, AsyncForgebench.AsyncCompletions.class),
                Map.entry(Agents.class, AsyncForgebench.AsyncAgents.class),
                Map.entry(Runs.class, AsyncForgebench.AsyncRuns.class),
                Map.entry(AgentTools.class, AsyncForgebench.AsyncAgentTools.class),
                Map.entry(Account.class, AsyncForgebench.AsyncAccount.class),
                Map.entry(Audio.class, AsyncForgebench.AsyncAudio.class),
                Map.entry(Audio.Transcriptions.class, AsyncForgebench.AsyncTranscriptions.class),
                Map.entry(Audio.Speech.class, AsyncForgebench.AsyncSpeech.class),
                Map.entry(Voice.class, AsyncForgebench.AsyncVoice.class),
                Map.entry(Voice.Policy.class, AsyncForgebench.AsyncVoicePolicy.class),
                Map.entry(Voice.Assets.class, AsyncForgebench.AsyncVoiceAssets.class),
                Map.entry(Voice.Consents.class, AsyncForgebench.AsyncVoiceConsents.class));
        List<String> missing = new ArrayList<>();
        pairs.forEach((sync, async) -> {
            for (Method m : sync.getDeclaredMethods()) {
                if (!Modifier.isPublic(m.getModifiers()) || Modifier.isStatic(m.getModifiers())) continue;
                if (sync == Forgebench.class && (m.getName().equals("close") || m.getName().startsWith("build"))) continue;
                try {
                    Method twin = async.getMethod(m.getName(), m.getParameterTypes());
                    Class<?> r = twin.getReturnType();
                    // I/O comes back as a future; only resource accessors and in-memory getters may not.
                    boolean accessor = pairs.containsValue(r) || r == m.getReturnType();
                    if (r != CompletableFuture.class && !accessor) {
                        missing.add(sync.getSimpleName() + "." + m.getName() + " returns " + r.getSimpleName());
                    }
                } catch (NoSuchMethodException e) {
                    missing.add(sync.getSimpleName() + "." + m.getName());
                }
            }
        });
        assertTrue(missing.isEmpty(), "no async twin for: " + missing);
    }

    @Test
    void audioAndVoiceRunAsync() {
        server.on("/v1/audio/transcriptions", MockServer.json(200, "{\"text\":\"hello\"}"));
        server.on("/v1/audio/speech", MockServer.json(200, "abc"));
        server.on("/v1/voice/policy", MockServer.json(200, "{\"enabled\":true}"));
        server.on("/v1/voice/assets", MockServer.json(200, "[{\"id\":\"a1\"}]"));
        server.on("/v1/voice/consents", MockServer.json(200, "[{\"id\":\"c1\"}]"));
        try (AsyncForgebench c = client()) {
            Transcription t = c.audio().transcriptions().create(TranscriptionParams.builder()
                    .file("RIFF".getBytes(StandardCharsets.UTF_8), "a.wav", "audio/wav").model("whisper-1").build()).join();
            assertEquals("hello", t.getText());
            SpeechAudio s = c.audio().speech().create(
                    SpeechParams.builder().model("tts-1").input("hi").voice("alloy").build()).join();
            assertEquals("abc", new String(s.getAudio(), StandardCharsets.UTF_8));
            assertEquals(true, c.voice().policy().get().join().get("enabled"));
            assertEquals("a1", c.voice().assets().list().join().get(0).get("id"));
            assertEquals("c1", c.voice().consents().list().join().get(0).get("id"));
        }
    }

    @Test
    void runStreamYieldsEventsAndRunReturnsTheAnswer() {
        server.on("/v1/agents/ag_1/run", MockServer.sse(
                "data: {\"type\":\"agent.run.delta\",\"text\":\"a\"}\n\n"
                        + "data: {\"type\":\"agent.run.completed\"}\n\n"));
        try (AsyncForgebench c = client()) {
            List<String> types = new ArrayList<>();
            try (AgentRunStream s = c.agents().runStream("ag_1", MessageRunParams.builder("hi").build()).join()) {
                for (JsonNode e : s) types.add(e.get("type").asText());
            }
            assertEquals(List.of("agent.run.delta", "agent.run.completed"), types);
            assertEquals("text/event-stream", server.last().header("Accept"));
        }
    }

    /** A failed call completes the same way as every other async call: a CompletionException around the typed error. */
    @Test
    void streamsFailLikeEveryOtherAsyncCall() {
        server.on("/v1/agents/missing/run", MockServer.json(404, "{\"detail\":\"agent not found\"}"));
        server.on("/v1/runs/missing", MockServer.json(404, "{\"detail\":\"run not found\"}"));
        try (AsyncForgebench c = client()) {
            Throwable viaStream = c.agents().runStream("missing", MessageRunParams.builder("hi").build())
                    .handle((v, e) -> e).join();
            Throwable viaCall = c.runs().get("missing").handle((v, e) -> e).join();
            assertInstanceOf(CompletionException.class, viaCall);
            assertEquals(viaCall.getClass(), viaStream.getClass());
            assertInstanceOf(NotFoundException.class, viaStream.getCause());
            CompletionException joined = assertThrows(CompletionException.class,
                    () -> c.agents().runStream("missing", MessageRunParams.builder("hi").build()).join());
            assertInstanceOf(NotFoundException.class, joined.getCause());
        }
    }

    @Test
    void aRejectedCallIsAFailedFutureNotAThrow() {
        ExecutorService pool = Executors.newSingleThreadExecutor();
        AsyncForgebench c = Forgebench.builder().apiKey("sk_test_abc123").baseUrl(server.baseUrl()).buildAsync(pool);
        pool.shutdown();
        CompletionException e = assertThrows(CompletionException.class,
                () -> c.agents().runStream("ag_1", MessageRunParams.builder("hi").build()).join());
        assertInstanceOf(RejectedExecutionException.class, e.getCause());
    }

    /** Cancelling the future while the stream is still opening must not leave the connection open. */
    @Test
    void cancellingRunStreamClosesTheStreamWhenItOpens() throws Exception {
        CountDownLatch received = new CountDownLatch(1);
        CountDownLatch disconnected = new CountDownLatch(1);
        server.on("/v1/agents/ag_1/run", ex -> {
            received.countDown();
            try {
                Thread.sleep(300); // headers arrive after the caller has given up
                ex.getResponseHeaders().set("Content-Type", "text/event-stream");
                ex.sendResponseHeaders(200, 0);
                java.io.OutputStream os = ex.getResponseBody();
                for (int i = 0; i < 200; i++) { // an endless stream: only a client disconnect ends it
                    os.write(": keepalive\n\n".getBytes(StandardCharsets.UTF_8));
                    os.flush();
                    Thread.sleep(25);
                }
            } catch (java.io.IOException | InterruptedException gone) {
                disconnected.countDown();
            }
        });
        try (AsyncForgebench c = client()) {
            CompletableFuture<AgentRunStream> f = c.agents().runStream("ag_1", MessageRunParams.builder("hi").build());
            assertTrue(received.await(5, TimeUnit.SECONDS));
            f.cancel(true);
            assertTrue(disconnected.await(10, TimeUnit.SECONDS), "the abandoned stream was never closed");
        }
    }
}
