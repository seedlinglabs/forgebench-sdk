package ai.forgebench;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.net.ConnectException;
import java.net.http.HttpHeaders;
import java.net.http.HttpTimeoutException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Cross-SDK retry contract (packages/sdk-contract/retry.json): same table in py and ts. */
class RetryContractTest {
    private static final JsonNode CONTRACT = load();

    private static JsonNode load() {
        try {
            return new ObjectMapper().readTree(Files.readString(Path.of("../sdk-contract/retry.json")));
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
    }

    private static Set<Integer> ints(String field) {
        Set<Integer> out = new HashSet<>();
        CONTRACT.get(field).forEach(n -> out.add(n.asInt()));
        return out;
    }

    private static IOException errorOf(String kind) {
        switch (kind) {
            case "connect": return new ConnectException("refused");
            case "timeout": return new HttpTimeoutException("request timed out");
            default: return new IOException("connection reset");
        }
    }

    @Test
    void statusTableMatchesTheContract() {
        for (JsonNode c : CONTRACT.get("status_cases")) {
            assertEquals(c.get("retry").asBoolean(),
                    Transport.shouldRetryStatus(c.get("method").asText(), c.get("idempotent").asBoolean(),
                            c.get("status").asInt()), c.toString());
        }
    }

    @Test
    void everyStatusFollowsTheContractRules() {
        Set<String> unsafeMethods = new HashSet<>();
        CONTRACT.get("unsafe_methods").forEach(n -> unsafeMethods.add(n.asText()));
        for (String method : List.of("GET", "PUT", "DELETE", "POST", "PATCH")) {
            for (boolean idempotent : new boolean[] {false, true}) {
                boolean unsafe = unsafeMethods.contains(method) && !idempotent;
                Set<Integer> allowed = ints(unsafe ? "unsafe_retry_statuses" : "retryable_statuses");
                for (int s = 100; s < 600; s++) {
                    assertEquals(allowed.contains(s), Transport.shouldRetryStatus(method, idempotent, s),
                            method + " idempotent=" + idempotent + " " + s);
                }
            }
        }
        assertEquals(CONTRACT.get("default_max_retries").asInt(), Transport.DEFAULT_MAX_RETRIES);
    }

    @Test
    void transportErrorTableMatchesTheContract() {
        for (JsonNode c : CONTRACT.get("error_cases")) {
            assertEquals(c.get("retry").asBoolean(),
                    Transport.shouldRetryError(c.get("method").asText(), c.get("idempotent").asBoolean(),
                            errorOf(c.get("error").asText())), c.toString());
        }
    }

    private static double[] bounds(int attempt) {
        JsonNode b = CONTRACT.get("backoff");
        double base = Math.min(b.get("base_ms").asDouble() * Math.pow(2, attempt), b.get("cap_ms").asDouble());
        return new double[] {base / 2, base};
    }

    @Test
    void backoffIsJitteredWithinTheContractBounds() {
        for (int attempt = 0; attempt < 7; attempt++) {
            double[] b = bounds(attempt);
            Set<Long> seen = new HashSet<>();
            for (int i = 0; i < 200; i++) {
                long ms = Transport.backoff(attempt).toMillis();
                assertTrue(ms >= Math.floor(b[0]) && ms <= b[1], "attempt " + attempt + ": " + ms);
                seen.add(ms);
            }
            assertTrue(seen.size() > 1, "jittered, not constant");
        }
    }

    @Test
    void retryAfterTableMatchesTheContract() {
        JsonNode ra = CONTRACT.get("retry_after");
        assertEquals(30, ra.get("max_seconds").asInt());
        for (JsonNode c : ra.get("cases")) {
            HttpHeaders h = HttpHeaders.of(Map.of("Retry-After", List.of(c.get("header").asText())), (k, v) -> true);
            Duration got = Transport.retryAfter(h, 1);
            if (c.get("seconds").isNull()) {
                double[] b = bounds(1);
                assertTrue(got.toMillis() >= b[0] && got.toMillis() <= b[1], c + " -> " + got);
            } else {
                assertEquals(Math.round(c.get("seconds").asDouble() * 1000), got.toMillis(), c.toString());
            }
        }
    }
}
