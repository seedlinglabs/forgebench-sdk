package ai.forgebench.tracing;

import com.fasterxml.jackson.databind.ObjectMapper;

import java.lang.System.Logger;
import java.lang.System.Logger.Level;
import java.time.Duration;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.HashSet;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CopyOnWriteArraySet;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.function.BiConsumer;

/**
 * Background sender: finished observations are queued here and posted from
 * one daemon thread — on a timer, or once a batch is full — so tracing never
 * makes the agent wait. Never throws into callers; a failed send is logged
 * and dropped.
 */
final class BatchExporter {
    /** The server takes at most this many observations per batch. */
    static final int MAX_BATCH = 100;
    /** The server 413s a body over 1,000,000 bytes; stay under it with room for the envelope. */
    static final int MAX_BATCH_BYTES = 900_000;

    private static final ObjectMapper SIZER = new ObjectMapper();

    private static final Logger LOG = System.getLogger("forgebench.tracing");
    // One JVM shutdown hook flushes every live exporter, so a program that
    // never calls flush() still sends its last steps.
    private static final Set<BatchExporter> LIVE = new CopyOnWriteArraySet<>();
    private static volatile boolean hookInstalled;

    private final BiConsumer<List<Map<String, Object>>, List<Map<String, Object>>> send;
    private final int flushAt;
    private final int maxQueueSize;
    private final Deque<Map<String, Object>> queue = new ArrayDeque<>();
    private final Map<String, Map<String, Object>> traces = new LinkedHashMap<>();
    private final ScheduledExecutorService worker;
    private boolean closed;
    private int dropped;

    BatchExporter(BiConsumer<List<Map<String, Object>>, List<Map<String, Object>>> send,
                  int flushAt, Duration flushInterval, int maxQueueSize) {
        this.send = send;
        this.flushAt = Math.min(flushAt, MAX_BATCH);
        this.maxQueueSize = maxQueueSize;
        this.worker = Executors.newSingleThreadScheduledExecutor(r -> {
            Thread t = new Thread(r, "forgebench-tracing");
            t.setDaemon(true);
            return t;
        });
        long ms = flushInterval.toMillis();
        worker.scheduleWithFixedDelay(this::drain, ms, ms, TimeUnit.MILLISECONDS);
        LIVE.add(this);
        installShutdownHook();
    }

    synchronized int dropped() {
        return dropped;
    }

    void add(Map<String, Object> observation, Map<String, Object> trace) {
        boolean full;
        synchronized (this) {
            if (closed) return;
            if (queue.size() >= maxQueueSize) {
                queue.pollFirst();
                if (++dropped == 1) {
                    LOG.log(Level.WARNING, "forgebench tracing: queue full ({0}), dropping the oldest observations",
                            maxQueueSize);
                }
            }
            queue.addLast(observation);
            if (trace != null) traces.merge((String) trace.get("id"), trace, BatchExporter::mergeRecords);
            full = queue.size() >= flushAt;
        }
        if (full) submitDrain();
    }

    /** Send everything queued now. True when it all went out within {@code timeout}. */
    boolean flush(Duration timeout) {
        try {
            worker.submit(this::drain).get(timeout.toMillis(), TimeUnit.MILLISECONDS);
            return true;
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return false;
        } catch (ExecutionException | TimeoutException | java.util.concurrent.RejectedExecutionException e) {
            return false;
        }
    }

    /** Flush, then stop the sender thread. Later observations are ignored. */
    void shutdown(Duration timeout) {
        flush(timeout);
        synchronized (this) {
            closed = true;
        }
        LIVE.remove(this);
        worker.shutdown();
    }

    private void submitDrain() {
        try {
            worker.execute(this::drain);
        } catch (java.util.concurrent.RejectedExecutionException e) {
            // Shut down: nothing more is sent.
        }
    }

    // Runs only on the worker thread, so batches go out one at a time, in order.
    private void drain() {
        while (true) {
            List<Map<String, Object>> observations;
            List<Map<String, Object>> records;
            synchronized (this) {
                if (queue.isEmpty() && traces.isEmpty()) return;
                observations = new ArrayList<>();
                long size = 0;
                while (!queue.isEmpty() && observations.size() < MAX_BATCH) {
                    long n = byteSize(queue.peekFirst());
                    if (!observations.isEmpty() && size + n > MAX_BATCH_BYTES) break;
                    size += n;
                    observations.add(queue.pollFirst());
                }
                records = takeRecords(observations, size);
            }
            try {
                send.accept(observations, records);
            } catch (RuntimeException e) {
                LOG.log(Level.WARNING, "forgebench tracing: sending {0} observations failed: {1}",
                        observations.size(), e.getMessage());
            }
        }
    }

    /**
     * The trace records for this batch: those of traces it has steps of (the
     * server attaches trace fields to a step in the same batch), or all that
     * are left once the queue is empty.
     */
    private List<Map<String, Object>> takeRecords(List<Map<String, Object>> observations, long size) {
        Set<Object> inBatch = new HashSet<>();
        for (Map<String, Object> o : observations) inBatch.add(o.get("trace_id"));
        List<Map<String, Object>> out = new ArrayList<>();
        Iterator<Map.Entry<String, Map<String, Object>>> it = traces.entrySet().iterator();
        while (it.hasNext() && out.size() < MAX_BATCH) {
            Map.Entry<String, Map<String, Object>> e = it.next();
            if (inBatch.contains(e.getKey()) || queue.isEmpty()) {
                long n = byteSize(e.getValue());
                if (!out.isEmpty() && size + n > MAX_BATCH_BYTES) break;
                size += n;
                out.add(e.getValue());
                it.remove();
            }
        }
        return out;
    }

    /** UTF-8 bytes of the JSON the record is sent as; 0 if it cannot be serialized (the send fails anyway). */
    private static long byteSize(Map<String, Object> record) {
        try {
            return SIZER.writeValueAsBytes(record).length;
        } catch (Exception e) {
            return 0;
        }
    }

    private static Map<String, Object> mergeRecords(Map<String, Object> older, Map<String, Object> newer) {
        Map<String, Object> merged = new LinkedHashMap<>(older);
        merged.putAll(newer);
        return merged;
    }

    private static synchronized void installShutdownHook() {
        if (hookInstalled) return;
        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            for (BatchExporter exporter : LIVE) exporter.flush(Duration.ofSeconds(5));
        }, "forgebench-tracing-shutdown"));
        hookInstalled = true;
    }
}
