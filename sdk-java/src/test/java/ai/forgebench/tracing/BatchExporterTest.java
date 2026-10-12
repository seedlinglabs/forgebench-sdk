package ai.forgebench.tracing;

import org.junit.jupiter.api.Test;

import java.time.Duration;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** The background sender: batch limits, trace records with their steps, overflow, failures. */
class BatchExporterTest {

    private static Map<String, Object> step(int i, String traceId) {
        return Map.of("id", String.valueOf(i), "trace_id", traceId);
    }

    @Test
    void batchesNeverExceedTheServerLimitAndATraceRecordTravelsWithItsSteps() {
        List<String> sent = Collections.synchronizedList(new ArrayList<>());
        BatchExporter exporter = new BatchExporter((obs, traces) -> {
            List<Object> ids = new ArrayList<>();
            for (Map<String, Object> t : traces) ids.add(t.get("id"));
            sent.add(obs.size() + ":" + ids);
        }, 500, Duration.ofMinutes(1), 1000);
        for (int i = 0; i < 150; i++) exporter.add(step(i, "early"), null);
        exporter.add(step(999, "late"), Map.of("id", "late", "name", "run"));
        assertTrue(exporter.flush(Duration.ofSeconds(5)));
        assertEquals(List.of("100:[]", "51:[late]"), sent);
        exporter.shutdown(Duration.ofSeconds(1));
    }

    @Test
    void queueOverflowDropsTheOldest() {
        List<Object> sent = Collections.synchronizedList(new ArrayList<>());
        BatchExporter exporter = new BatchExporter((obs, traces) -> obs.forEach(o -> sent.add(o.get("id"))),
                100, Duration.ofMinutes(1), 2);
        for (int i = 0; i < 4; i++) exporter.add(step(i, "t"), null);
        assertEquals(2, exporter.dropped());
        exporter.flush(Duration.ofSeconds(5));
        assertEquals(List.of("2", "3"), sent);
        exporter.shutdown(Duration.ofSeconds(1));
    }

    @Test
    void aFailingSendNeverReachesTheCallerAndLaterBatchesStillGo() {
        List<Object> sent = Collections.synchronizedList(new ArrayList<>());
        boolean[] fail = {true};
        BatchExporter exporter = new BatchExporter((obs, traces) -> {
            if (fail[0]) throw new IllegalStateException("control plane down");
            obs.forEach(o -> sent.add(o.get("id")));
        }, 100, Duration.ofMinutes(1), 100);
        exporter.add(step(1, "t"), null);
        assertTrue(exporter.flush(Duration.ofSeconds(5)));
        fail[0] = false;
        exporter.add(step(2, "t"), null);
        assertTrue(exporter.flush(Duration.ofSeconds(5)));
        assertEquals(List.of("2"), sent);
        exporter.shutdown(Duration.ofSeconds(1));
    }

    @Test
    void aFullBatchIsSentWithoutWaitingForTheInterval() throws Exception {
        List<Object> sent = Collections.synchronizedList(new ArrayList<>());
        BatchExporter exporter = new BatchExporter((obs, traces) -> sent.add(obs.size()), 3, Duration.ofMinutes(10), 100);
        for (int i = 0; i < 3; i++) exporter.add(step(i, "t"), null);
        long deadline = System.currentTimeMillis() + 5_000;
        while (sent.isEmpty() && System.currentTimeMillis() < deadline) Thread.sleep(10);
        assertEquals(List.of(3), sent);
        exporter.shutdown(Duration.ofSeconds(1));
    }
}
