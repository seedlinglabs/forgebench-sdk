package ai.forgebench;

import ai.forgebench.errors.ForgebenchStreamException;
import ai.forgebench.errors.PermissionDeniedException;
import ai.forgebench.types.SpeechAudio;
import ai.forgebench.types.Transcription;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Offline tests for the voice surface: audio, voice admin, streamed agent runs, voice headers. */
class VoiceTest {
    private static final ObjectMapper M = new ObjectMapper();

    private MockServer server;

    @BeforeEach
    void start() throws Exception {
        server = new MockServer();
    }

    @AfterEach
    void stop() {
        server.close();
    }

    private Forgebench client() {
        return Forgebench.builder().apiKey("sk_test_abc123").baseUrl(server.baseUrl()).maxRetries(0).build();
    }

    private JsonNode lastBody() throws Exception {
        return M.readTree(server.last().body);
    }

    private static MockServer.Responder withHeaders(String contentType, String body, String... kv) {
        return ex -> {
            for (int i = 0; i < kv.length; i += 2) ex.getResponseHeaders().set(kv[i], kv[i + 1]);
            MockServer.send(ex, 200, body, contentType);
        };
    }

    // --- audio ---------------------------------------------------------------

    @Test
    void transcriptionIsMultipartWithFieldsAndSession() throws Exception {
        server.on("/v1/audio/transcriptions",
                withHeaders("application/json", "{\"text\":\"hello\",\"duration\":1.5}",
                        "X-Forgebench-Trace-Id", "tr_1"));
        Transcription t = client().audio().transcriptions().create(TranscriptionParams.builder()
                .file("RIFFdata".getBytes(StandardCharsets.UTF_8), "a.wav", "audio/wav")
                .model("whisper-1").language("en").prompt("names").responseFormat("verbose_json")
                .metadata(Map.of("k", "v")).session("call-9").build());

        assertEquals("hello", t.getText());
        assertEquals("tr_1", t.getTraceId());
        assertEquals(1.5, ((Number) t.getRaw().get("duration")).doubleValue());

        MockServer.Recorded r = server.last();
        assertEquals("POST", r.method);
        assertTrue(r.header("Content-Type").startsWith("multipart/form-data; boundary="));
        assertEquals("Bearer sk_test_abc123", r.header("Authorization"));
        assertEquals("call-9", r.header("X-Forgebench-Session"));
        assertTrue(r.body.contains("name=\"file\"; filename=\"a.wav\""));
        assertTrue(r.body.contains("Content-Type: audio/wav\r\n\r\nRIFFdata\r\n"));
        assertTrue(r.body.contains("name=\"model\"\r\n\r\nwhisper-1\r\n"));
        assertTrue(r.body.contains("name=\"language\"\r\n\r\nen\r\n"));
        assertTrue(r.body.contains("name=\"prompt\"\r\n\r\nnames\r\n"));
        assertTrue(r.body.contains("name=\"response_format\"\r\n\r\nverbose_json\r\n"));
        assertTrue(r.body.contains("name=\"metadata\"\r\n\r\n{\"k\":\"v\"}\r\n"));
    }

    @Test
    void transcriptionPlainTextResponseAndNoOptionalFields() {
        server.on("/v1/audio/transcriptions",
                withHeaders("text/plain", "just text", "X-Forgebench-Trace-Id", "tr_2"));
        Transcription t = client().audio().transcriptions().create(TranscriptionParams.builder()
                .file(new byte[]{1, 2, 3}).model("whisper-1").build());
        assertEquals("just text", t.getText());
        assertTrue(t.getRaw().isEmpty());
        assertEquals("tr_2", t.getTraceId());
        assertFalse(server.last().body.contains("name=\"language\""));
        assertNull(server.last().header("X-Forgebench-Session"));
    }

    @Test
    void speechReturnsBytesAndHeadersIncludingDisclosure() throws Exception {
        server.on("/v1/audio/speech", ex -> {
            ex.getResponseHeaders().set("Content-Type", "audio/mpeg");
            ex.getResponseHeaders().set("X-Forgebench-Trace-Id", "tr_3");
            ex.getResponseHeaders().set("X-Forgebench-Disclosure", "injected");
            ex.sendResponseHeaders(200, 3);
            ex.getResponseBody().write(new byte[]{(byte) 0xff, 0x00, 0x7f});
        });
        SpeechAudio s = client().audio().speech().create(SpeechParams.builder()
                .model("tts-1").input("hi there").voice("alloy").responseFormat("mp3").speed(1.25)
                .metadata(Map.of("a", 1)).session("call-9").build());

        assertArrayEquals(new byte[]{(byte) 0xff, 0x00, 0x7f}, s.getAudio());
        assertEquals("audio/mpeg", s.getContentType());
        assertEquals("tr_3", s.getTraceId());
        assertEquals("injected", s.getDisclosure());
        assertEquals(List.of("injected"), s.getHeaders().get("x-forgebench-disclosure"));

        assertEquals("/v1/audio/speech", server.last().path);
        assertEquals("call-9", server.last().header("X-Forgebench-Session"));
        JsonNode b = lastBody();
        assertEquals("tts-1", b.get("model").asText());
        assertEquals("hi there", b.get("input").asText());
        assertEquals("alloy", b.get("voice").asText());
        assertEquals("mp3", b.get("response_format").asText());
        assertEquals(1.25, b.get("speed").asDouble());
        assertEquals(1, b.get("metadata").get("a").asInt());
        assertFalse(b.has("session"));
    }

    @Test
    void speechPolicyRefusalIsTyped() {
        server.on("/v1/audio/speech", MockServer.json(403, "{\"detail\":\"no\",\"code\":\"voice_not_allowed\"}"));
        PermissionDeniedException e = assertThrows(PermissionDeniedException.class, () ->
                client().audio().speech().create(SpeechParams.builder().model("m").input("x").voice("v").build()));
        assertEquals("voice_not_allowed", e.getCode());
    }

    // --- voice admin -----------------------------------------------------------

    @Test
    void policyGetAndUpdateDropsUpdatedAtAndKeepsVoiceTraceContent() throws Exception {
        server.on("/v1/voice/policy", MockServer.json(200,
                "{\"disclosure_mode\":\"enforce\",\"voice_trace_content\":true,\"updated_at\":\"2026-01-01T00:00:00Z\"}"));
        Voice.Policy policy = client().voice().policy();

        Map<String, Object> got = policy.get();
        assertEquals("GET", server.last().method);
        assertEquals("2026-01-01T00:00:00Z", got.get("updated_at"));

        Map<String, Object> out = policy.update(got); // read-modify-write
        assertEquals("PUT", server.last().method);
        assertEquals("enforce", out.get("disclosure_mode"));
        JsonNode b = lastBody();
        assertFalse(b.has("updated_at"));
        assertTrue(b.get("voice_trace_content").asBoolean());
        assertEquals("enforce", b.get("disclosure_mode").asText());
    }

    @Test
    void assetsListCreateUpdateRevoke() throws Exception {
        server.on("/v1/voice/assets",
                MockServer.json(200, "[{\"id\":\"a1\"}]"),
                MockServer.json(200, "{\"data\":[{\"id\":\"a2\"}]}"),
                MockServer.json(201, "{\"id\":\"a3\"}"));
        Voice.Assets assets = client().voice().assets();
        assertEquals("a1", assets.list().get(0).get("id"));
        assertEquals("a2", assets.list().get(0).get("id")); // {"data": [...]} envelope

        Map<String, Object> fields = new LinkedHashMap<>();
        fields.put("provider", "elevenlabs");
        fields.put("provider_voice_id", "v1");
        fields.put("display_name", "Brand");
        fields.put("kind", "brand");
        fields.put("expires_at", null);
        assertEquals("a3", assets.create(fields).get("id"));
        assertEquals("POST", server.last().method);
        JsonNode b = lastBody();
        assertEquals("brand", b.get("kind").asText());
        assertFalse(b.has("expires_at")); // create omits nulls

        server.on("/v1/voice/assets/a/1", MockServer.json(200, "{\"id\":\"a/1\"}"));
        assets.update("a/1", VoiceAssetUpdate.builder().expiresAt(null).displayName("New"));
        assertEquals("PATCH", server.last().method);
        assertEquals("/v1/voice/assets/a%2F1", server.last().path);
        JsonNode p = lastBody();
        assertTrue(p.has("expires_at") && p.get("expires_at").isNull()); // null clears
        assertEquals("New", p.get("display_name").asText());
        assertFalse(p.has("licensed_uses")); // absent = unchanged
        assertFalse(p.has("allowed_agent_ids"));
        assertFalse(p.has("consent_record_id"));

        server.on("/v1/voice/assets/a1/revoke", MockServer.json(200, "{\"id\":\"a1\",\"revoked_at\":\"t\"}"));
        assertEquals("t", assets.revoke("a1").get("revoked_at"));
        assertEquals("POST", server.last().method);
    }

    @Test
    void consentsListCreateRevoke() throws Exception {
        server.on("/v1/voice/consents",
                MockServer.json(200, "[{\"id\":\"c1\"}]"),
                MockServer.json(201, "{\"id\":\"c2\"}"));
        Voice.Consents consents = client().voice().consents();
        assertEquals("c1", consents.list().get(0).get("id"));

        Map<String, Object> f = new HashMap<>();
        f.put("subject_name", "Ada");
        f.put("purpose", "voice_clone");
        f.put("scope", "brand");
        f.put("evidence_uri", null);
        assertEquals("c2", consents.create(f).get("id"));
        JsonNode b = lastBody();
        assertEquals("Ada", b.get("subject_name").asText());
        assertFalse(b.has("evidence_uri"));

        server.on("/v1/voice/consents/c2/revoke", MockServer.json(200, "{\"id\":\"c2\"}"));
        consents.revoke("c2");
        assertEquals("POST", server.last().method);
        assertEquals("/v1/voice/consents/c2/revoke", server.last().path);
    }

    // --- streamed agent runs -----------------------------------------------------

    private List<JsonNode> drain(AgentRunStream s) {
        List<JsonNode> out = new ArrayList<>();
        try (AgentRunStream stream = s) {
            for (JsonNode e : stream) out.add(e);
        }
        return out;
    }

    private AgentRunStream runStream() {
        return client().agents().runStream("ag_1", MessageRunParams.builder("hi").topK(3)
                .dimensions(Map.of("team", "x")).session("call-9").build());
    }

    @Test
    void runStreamIteratesEventsAndSendsRequestShape() throws Exception {
        server.on("/v1/agents/ag_1/run", MockServer.sse(
                "data: {\"type\":\"agent.run.delta\",\"text\":\"a\"}\n\n"
                        + "data: {\"type\":\"agent.run.completed\"}\n\n"));
        List<JsonNode> events = drain(runStream());
        assertEquals(2, events.size());
        assertEquals("agent.run.delta", events.get(0).get("type").asText());
        assertEquals("agent.run.completed", events.get(1).get("type").asText());

        assertEquals("text/event-stream", server.last().header("Accept"));
        assertEquals("call-9", server.last().header("X-Forgebench-Session"));
        JsonNode b = lastBody();
        assertEquals("hi", b.get("message").asText());
        assertTrue(b.get("stream").asBoolean());
        assertEquals(3, b.get("top_k").asInt());
        assertEquals("x", b.get("dimensions").get("team").asText());
    }

    @Test
    void runStreamEndingWithDoneIsComplete() {
        server.on("/v1/agents/ag_1/run", MockServer.sse("data: {\"type\":\"x\"}\n\ndata: [DONE]\n\n"));
        assertEquals(1, drain(runStream()).size());
    }

    @Test
    void runStreamErrorFrameThrowsTypedError() {
        server.on("/v1/agents/ag_1/run", MockServer.sse(
                "data: {\"type\":\"agent.run.delta\"}\n\n"
                        + "event: error\ndata: {\"error\":{\"message\":\"boom\",\"code\":\"upstream_failed\"}}\n\n"));
        List<JsonNode> seen = new ArrayList<>();
        ForgebenchStreamException e = assertThrows(ForgebenchStreamException.class, () -> {
            try (AgentRunStream s = runStream()) {
                for (JsonNode ev : s) seen.add(ev);
            }
        });
        assertEquals(1, seen.size());
        assertEquals("boom", e.getMessage());
        assertEquals("upstream_failed", e.getCode());
    }

    @Test
    void runStreamUnparseableErrorFrameStillThrows() {
        server.on("/v1/agents/ag_1/run", MockServer.sse("event: error\ndata: not json\n\n"));
        ForgebenchStreamException e = assertThrows(ForgebenchStreamException.class, () -> drain(runStream()));
        assertTrue(e.getMessage().startsWith("stream error: not json"));
        assertNull(e.getCode());
    }

    @Test
    void runStreamTruncatedWithoutTerminalThrows() {
        server.on("/v1/agents/ag_1/run", MockServer.sse("data: {\"type\":\"agent.run.delta\"}\n\n"));
        ForgebenchStreamException e = assertThrows(ForgebenchStreamException.class, () -> drain(runStream()));
        assertEquals("stream_truncated", e.getCode());
    }

    @Test
    void runStreamEarlyCloseStopsIterationWithoutError() {
        server.on("/v1/agents/ag_1/run", MockServer.sse(
                "data: {\"type\":\"agent.run.delta\"}\n\ndata: {\"type\":\"agent.run.completed\"}\n\n"));
        AgentRunStream s = runStream();
        java.util.Iterator<JsonNode> it = s.iterator();
        assertTrue(it.hasNext());
        it.next();
        s.close(); // abandoning before the terminal frame is not a truncation error
        assertFalse(it.hasNext());
    }

    @Test
    void messageRunReturnsAnswerMap() throws Exception {
        server.on("/v1/agents/ag_1/run", MockServer.json(200, "{\"answer\":\"42\"}"));
        Map<String, Object> out = client().agents().run("ag_1", MessageRunParams.builder("q").build());
        assertEquals("42", out.get("answer"));
        JsonNode b = lastBody();
        assertFalse(b.has("stream"));
        assertFalse(b.has("top_k"));
        assertNull(server.last().header("X-Forgebench-Session"));
    }

    // --- voice headers / constants ---------------------------------------------------

    @Test
    void headerConstants() {
        assertEquals("X-Trace-Id", Trace.TRACE_ID_HEADER);
        assertEquals("X-Forgebench-Trace-Id", Trace.AUDIO_TRACE_ID_HEADER);
        assertEquals("X-Forgebench-Session", Trace.SESSION_HEADER);
        assertEquals("X-Forgebench-Turn", Trace.TURN_HEADER);
    }

    @Test
    void voiceHeadersBuildsAndValidates() {
        assertEquals(Map.of("X-Forgebench-Session", "call 9"), Trace.voiceHeaders("call 9"));
        Map<String, String> h = Trace.voiceHeaders("s1", 0, "00-aa-bb-01");
        assertEquals("s1", h.get("X-Forgebench-Session"));
        assertEquals("0", h.get("X-Forgebench-Turn"));
        assertEquals("00-aa-bb-01", h.get("traceparent"));

        assertEquals("a  b", Trace.voiceHeaders("a  b").get("X-Forgebench-Session")); // consecutive internal spaces
        assertEquals(128, Trace.voiceHeaders("x".repeat(128)).get("X-Forgebench-Session").length());

        for (String bad : new String[]{null, "", " a", "a ", "  ", "x".repeat(129), "café", "a\tb", "a\nb", "a\n"}) {
            assertThrows(IllegalArgumentException.class, () -> Trace.voiceHeaders(bad), String.valueOf(bad));
        }
        assertThrows(IllegalArgumentException.class, () -> Trace.voiceHeaders("s", -1));
    }

    @Test
    void validateTraceId() {
        assertNull(Trace.validateTraceId(null));
        assertEquals("abc", Trace.validateTraceId("abc"));
        assertThrows(IllegalArgumentException.class, () -> Trace.validateTraceId(""));
        assertThrows(IllegalArgumentException.class, () -> Trace.validateTraceId("x".repeat(65)));
    }

    // --- review fixes: multipart bytes, error mapping, retries, close ----------------

    private static int indexOf(byte[] hay, byte[] needle, int from) {
        outer:
        for (int i = from; i <= hay.length - needle.length; i++) {
            for (int j = 0; j < needle.length; j++) if (hay[i + j] != needle[j]) continue outer;
            return i;
        }
        return -1;
    }

    @Test
    void multipartIsBinarySafeAndExactlyFramed() {
        server.on("/v1/audio/transcriptions", MockServer.json(200, "{\"text\":\"ok\"}"));
        byte[] audio = new byte[512];
        for (int i = 0; i < audio.length; i++) audio[i] = (byte) i; // every byte value, incl. CR/LF and invalid UTF-8
        client().audio().transcriptions().create(TranscriptionParams.builder()
                .file(audio, "a.wav", "audio/wav").model("whisper-1").build());

        MockServer.Recorded r = server.last();
        String ct = r.header("Content-Type");
        String boundary = ct.substring(ct.indexOf("boundary=") + "boundary=".length());
        byte[] body = r.rawBody;
        byte[] head = ("--" + boundary + "\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.wav\"\r\n"
                + "Content-Type: audio/wav\r\n\r\n").getBytes(StandardCharsets.UTF_8);
        assertEquals(0, indexOf(body, head, 0));
        // The file bytes sit verbatim between the part header and the closing CRLF.
        byte[] got = java.util.Arrays.copyOfRange(body, head.length, head.length + audio.length);
        assertArrayEquals(audio, got);
        byte[] rest = java.util.Arrays.copyOfRange(body, head.length + audio.length, body.length);
        assertEquals("\r\n--" + boundary + "\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nwhisper-1\r\n"
                + "--" + boundary + "--\r\n", new String(rest, StandardCharsets.UTF_8));
        assertEquals(String.valueOf(body.length), r.header("Content-Length"));
        // Boundary is unpredictable per request.
        client().audio().transcriptions().create(TranscriptionParams.builder().file(audio).model("m").build());
        assertFalse(server.last().header("Content-Type").equals(ct));
    }

    @Test
    void filenameCannotInjectHeadersAndContentTypeWithLineBreakIsRejected() {
        server.on("/v1/audio/transcriptions", MockServer.json(200, "{\"text\":\"ok\"}"));
        client().audio().transcriptions().create(TranscriptionParams.builder()
                .file(new byte[]{1}, "a\".wav\r\nX-Evil: 1\r\n\r\n", "audio/wav").model("m").build());
        String body = server.last().body;
        assertTrue(body.contains("filename=\"a%22.wavX-Evil: 1\"\r\nContent-Type: audio/wav\r\n\r\n"), body);
        assertFalse(body.contains("\r\nX-Evil"));

        assertThrows(IllegalArgumentException.class, () -> TranscriptionParams.builder()
                .file(new byte[]{1}, "a.wav", "audio/wav\r\nX-Evil: 1").model("m").build());
    }

    @Test
    void errorEnvelopesSurfaceTheCodeLikePyAndTs() {
        // 403 {"detail": {"error": "model_not_allowed", ...}} -- what the audio routes send.
        server.on("/v1/audio/speech", MockServer.json(403,
                "{\"detail\":{\"error\":\"model_not_allowed\",\"model\":\"m\",\"allowed_models\":[\"x\"]}}"));
        PermissionDeniedException e = assertThrows(PermissionDeniedException.class, () ->
                client().audio().speech().create(SpeechParams.builder().model("m").input("x").voice("v").build()));
        assertEquals("model_not_allowed", e.getCode());

        // Top-level OpenAI-style envelope.
        server.on("/v1/audio/transcriptions", MockServer.json(403,
                "{\"error\":{\"code\":\"model_not_allowed\",\"message\":\"nope\"}}"));
        PermissionDeniedException e2 = assertThrows(PermissionDeniedException.class, () ->
                client().audio().transcriptions().create(TranscriptionParams.builder().file(new byte[]{1}).model("m").build()));
        assertEquals("model_not_allowed", e2.getCode());
        assertEquals("nope", e2.getMessage());
    }

    @Test
    void audioHttpErrorsMapLikeTheOtherSdks() {
        int[] statuses = {402, 413, 415, 422};
        String[] codes = {"plan_required", "upload_too_large", "unsupported_audio", "disclosure_missing"};
        List<Class<? extends ai.forgebench.errors.ApiException>> types = List.of(
                ai.forgebench.errors.BudgetExceededException.class, ai.forgebench.errors.ApiException.class,
                ai.forgebench.errors.ApiException.class, ai.forgebench.errors.ValidationException.class);
        for (int i = 0; i < statuses.length; i++) {
            server.reset("/v1/audio/transcriptions");
            server.on("/v1/audio/transcriptions", MockServer.json(statuses[i],
                    "{\"detail\":{\"code\":\"" + codes[i] + "\",\"message\":\"msg " + i + "\"}}"));
            ai.forgebench.errors.ApiException e = assertThrows(types.get(i), () ->
                    client().audio().transcriptions().create(TranscriptionParams.builder().file(new byte[]{1}).model("m").build()));
            assertEquals(statuses[i], e.getStatusCode());
            assertEquals(codes[i], e.getCode());
            assertEquals("msg " + i, e.getMessage());
        }
    }

    private Forgebench retrying() {
        return Forgebench.builder().apiKey("sk_test_abc123").baseUrl(server.baseUrl()).maxRetries(2).build();
    }

    @Test
    void billedPostsAreNeverReplayedOnA5xxOrDroppedConnection() {
        MockServer.Responder busy = ex -> {
            ex.getResponseHeaders().set("Retry-After", "0");
            MockServer.send(ex, 503, "{\"detail\":\"busy\"}", "application/json");
        };
        server.on("/v1/audio/speech", busy);
        server.on("/v1/audio/transcriptions", busy);
        server.on("/v1/agents/ag_1/run", busy);
        server.on("/v1/voice/consents", busy);

        assertThrows(ai.forgebench.errors.ServerException.class, () -> retrying().audio().speech().create(
                SpeechParams.builder().model("m").input("x").voice("v").build()));
        assertThrows(ai.forgebench.errors.ServerException.class, () -> retrying().audio().transcriptions().create(
                TranscriptionParams.builder().file(new byte[]{1}).model("m").build()));
        assertThrows(ai.forgebench.errors.ServerException.class, () -> retrying().agents().runStream("ag_1",
                MessageRunParams.builder("hi").build()));
        assertThrows(ai.forgebench.errors.ServerException.class, () -> retrying().agents().run("ag_1",
                MessageRunParams.builder("hi").build()));
        assertThrows(ai.forgebench.errors.ServerException.class, () -> retrying().voice().consents().create(
                Map.of("subject_name", "a", "purpose", "recording", "scope", "s")));
        assertEquals(5, server.requests.size()); // exactly one attempt each
    }

    @Test
    void aDroppedConnectionIsNotReplayed() {
        server.on("/v1/audio/speech", ex -> { /* closed without a response */ });
        assertThrows(ai.forgebench.errors.ForgebenchConnectionException.class, () -> retrying().audio().speech().create(
                SpeechParams.builder().model("m").input("x").voice("v").build()));
        assertEquals(1, server.requests.size());
    }

    @Test
    void a429IsReplayedBecauseTheServerDidNotProcessIt() {
        server.on("/v1/audio/speech",
                ex -> {
                    ex.getResponseHeaders().set("Retry-After", "0");
                    MockServer.send(ex, 429, "{\"detail\":\"slow down\"}", "application/json");
                },
                withHeaders("audio/mpeg", "abc"));
        SpeechAudio s = retrying().audio().speech().create(SpeechParams.builder().model("m").input("x").voice("v").build());
        assertEquals(3, s.getAudio().length);
        assertEquals(2, server.requests.size());
    }

    private static MockServer.Responder status(int code) {
        return ex -> {
            ex.getResponseHeaders().set("Retry-After", "0");
            MockServer.send(ex, code, "{\"detail\":\"x\"}", "application/json");
        };
    }

    @Test
    void readsAndPutRetryOnA5xx() {
        server.on("/v1/voice/assets", status(503), MockServer.json(200, "[]"));
        assertEquals(0, retrying().voice().assets().list().size());
        assertEquals(2, server.requests.size());

        server.on("/v1/voice/policy", status(502), MockServer.json(200, "{\"a\":1}"));
        assertEquals(1, retrying().voice().policy().update(Map.of("a", 1)).get("a"));
        assertEquals(4, server.requests.size()); // PUT is replay-safe, like Py/TS
    }

    @Test
    void patchIsNotReplayedOn5xxButIsOn429() {
        server.on("/v1/voice/assets/a1", status(503));
        assertThrows(ai.forgebench.errors.ServerException.class,
                () -> retrying().voice().assets().update("a1", VoiceAssetUpdate.builder().displayName("n")));
        assertEquals(1, server.requests.size());

        server.on("/v1/voice/assets/a2", status(429), MockServer.json(200, "{}"));
        retrying().voice().assets().update("a2", VoiceAssetUpdate.builder().displayName("n"));
        assertEquals(3, server.requests.size());
    }

    @Test
    void connectFailureIsClassifiedAsReplaySafe() {
        assertTrue(Transport.isConnectFailure(new java.net.NoRouteToHostException("no route")));
        assertTrue(Transport.isConnectFailure(new javax.net.ssl.SSLHandshakeException("handshake")));
        assertTrue(Transport.isConnectFailure(new java.net.ConnectException("refused")));
        assertTrue(Transport.isConnectFailure(new java.net.http.HttpConnectTimeoutException("t")));
        assertTrue(Transport.isConnectFailure(new java.io.IOException("wrapped", new java.net.ConnectException("x"))));
        assertFalse(Transport.isConnectFailure(new java.io.IOException("reset")));
        assertFalse(Transport.isConnectFailure(new java.net.http.HttpTimeoutException("read timed out")));
    }

    @Test
    void idsAreEncodedAndRouteChangingIdsRejected() {
        server.on("/v1/voice/assets/a b/revoke", MockServer.json(200, "{}"));
        client().voice().assets().revoke("a b");
        assertEquals("/v1/voice/assets/a%20b/revoke", server.last().path);
        for (String bad : new String[]{"", ".", "..", null}) {
            assertThrows(IllegalArgumentException.class, () -> client().voice().assets().revoke(bad));
            assertThrows(IllegalArgumentException.class, () -> client().voice().consents().revoke(bad));
            assertThrows(IllegalArgumentException.class, () -> client().agents().runStream(bad, MessageRunParams.builder("x").build()));
        }
    }

    @Test
    void emptyAssetUpdateSendsEmptyObject() throws Exception {
        server.on("/v1/voice/assets/a1", MockServer.json(200, "{\"id\":\"a1\"}"));
        client().voice().assets().update("a1", VoiceAssetUpdate.builder());
        assertEquals("{}", server.last().body);
    }

    @Test
    void policyUpdateKeepsFalseAndZeroValues() throws Exception {
        server.on("/v1/voice/policy", MockServer.json(200, "{}"));
        Map<String, Object> f = new LinkedHashMap<>();
        f.put("marking_required", false);
        f.put("marking_min_level", 0);
        f.put("inject_text", "");
        f.put("disclosure_phrases", List.of());
        f.put("default_language", null);
        client().voice().policy().update(f);
        JsonNode b = lastBody();
        assertFalse(b.get("marking_required").asBoolean());
        assertEquals(0, b.get("marking_min_level").asInt());
        assertEquals("", b.get("inject_text").asText());
        assertTrue(b.get("disclosure_phrases").isArray());
        assertFalse(b.has("default_language"));
    }

    // --- SSE parsing ------------------------------------------------------------------

    @Test
    void sseHandlesCrlfCommentsAndEventResetPerFrame() throws Exception {
        server.on("/v1/agents/ag_1/run", MockServer.sse(
                ": keep-alive\r\n\r\n"
                        + "event: message\r\ndata: {\"type\":\"a\"}\r\n\r\n"
                        // `event: error` applies to its own frame only; the next frame is data again.
                        + "event: other\ndata: {\"type\":\"b\"}\n\n"
                        + "data:{\"type\":\"agent.run.completed\"}\r\n\r\n"));
        List<JsonNode> ev = drain(runStream());
        assertEquals(3, ev.size());
        assertEquals("b", ev.get(1).get("type").asText());
    }

    @Test
    void eventErrorDoesNotLeakIntoTheNextFrame() {
        server.on("/v1/agents/ag_1/run", MockServer.sse(
                "event: error\n\n" // an error event with no data frame
                        + "data: {\"type\":\"agent.run.completed\"}\n\n"));
        assertEquals(1, drain(runStream()).size());
    }

    @Test
    void closingFromAnotherThreadWakesTheBlockedReaderWithoutError() throws Exception {
        java.util.concurrent.CountDownLatch release = new java.util.concurrent.CountDownLatch(1);
        server.on("/v1/agents/ag_1/run", ex -> {
            ex.getResponseHeaders().set("Content-Type", "text/event-stream");
            ex.sendResponseHeaders(200, 0);
            ex.getResponseBody().write("data: {\"type\":\"agent.run.delta\"}\n\n".getBytes(StandardCharsets.UTF_8));
            ex.getResponseBody().flush();
            try { release.await(10, java.util.concurrent.TimeUnit.SECONDS); } catch (InterruptedException ignored) { /* test over */ }
        });
        AgentRunStream s = runStream();
        java.util.Iterator<JsonNode> it = s.iterator();
        assertTrue(it.hasNext());
        it.next();
        new Thread(() -> {
            try { Thread.sleep(200); } catch (InterruptedException ignored) { /* ok */ }
            s.close();
        }).start();
        long t0 = System.nanoTime();
        assertFalse(it.hasNext()); // blocked on the wire until close() wakes it
        long ms = (System.nanoTime() - t0) / 1_000_000;
        release.countDown();
        assertTrue(ms < 3000, "close() should wake the reader promptly, took " + ms + "ms");
    }

    @Test
    void agentRunStreamIsSingleUse() {
        server.on("/v1/agents/ag_1/run", MockServer.sse("data: {\"type\":\"agent.run.completed\"}\n\n"));
        AgentRunStream s = runStream();
        s.iterator();
        assertThrows(IllegalStateException.class, s::iterator);
        s.close();
    }
}
