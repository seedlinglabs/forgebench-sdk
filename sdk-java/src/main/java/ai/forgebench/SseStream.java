package ai.forgebench;

import ai.forgebench.errors.ForgebenchConnectionException;
import com.fasterxml.jackson.databind.JsonNode;

import java.io.IOException;
import java.net.http.HttpHeaders;
import java.time.Duration;
import java.util.Iterator;
import java.util.NoSuchElementException;

/**
 * The {@code data:} events of a Server-Sent Events body, as JSON objects.
 *
 * <p>The chat chokepoint passes the gateway's SSE stream through unchanged:
 * each event is a {@code data: <json>} line, terminated by {@code data: [DONE]}.
 * Only the {@code data:} field is parsed (OpenAI/LiteLLM stream format);
 * comments, other fields, and unparseable payloads are skipped.
 */
final class SseStream implements Iterator<JsonNode>, AutoCloseable {
    private final Transport.LineQueue lines;
    private final HttpHeaders headers;
    private final long readTimeoutMillis;
    private final long deadlineNanos;
    private final Duration streamTimeout;
    private JsonNode next;
    private boolean done;

    SseStream(Transport.LineQueue lines, HttpHeaders headers, Duration readTimeout, Duration streamTimeout) {
        this.lines = lines;
        this.headers = headers;
        this.readTimeoutMillis = readTimeout.toMillis();
        this.streamTimeout = streamTimeout;
        this.deadlineNanos = System.nanoTime() + streamTimeout.toNanos();
    }

    HttpHeaders headers() {
        return headers;
    }

    @Override
    public boolean hasNext() {
        while (next == null && !done) {
            next = parseDataLine(readLine());
        }
        return next != null;
    }

    @Override
    public JsonNode next() {
        if (!hasNext()) throw new NoSuchElementException();
        JsonNode out = next;
        next = null;
        return out;
    }

    /** The next raw line, or null once the body has ended. */
    private String readLine() {
        long remainingMillis = (deadlineNanos - System.nanoTime()) / 1_000_000;
        if (remainingMillis <= 0) throw stalled();
        long wait = Math.min(readTimeoutMillis, remainingMillis);
        Object item;
        try {
            item = lines.poll(wait);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            close();
            throw new ForgebenchConnectionException("interrupted while reading the stream", e);
        }
        if (item == null) {
            close();
            if (wait < readTimeoutMillis) throw stalled();
            throw new ForgebenchConnectionException(
                    "stream read timed out after " + readTimeoutMillis + "ms with no data");
        }
        if (item == Transport.LineQueue.END) {
            done = true;
            return null;
        }
        if (item instanceof Throwable) {
            done = true;
            Throwable t = (Throwable) item;
            throw new ForgebenchConnectionException("stream failed: " + t, t);
        }
        return (String) item;
    }

    private ForgebenchConnectionException stalled() {
        close();
        return new ForgebenchConnectionException("stream exceeded overall timeout of "
                + streamTimeout.getSeconds() + "s (connection stayed open but stopped making progress)");
    }

    static JsonNode parseDataLine(String raw) {
        if (raw == null) return null;
        String line = raw.replaceAll("[\r\n]+$", "");
        if (line.isEmpty() || line.startsWith(":") || !line.startsWith("data:")) return null;
        String payload = line.substring("data:".length()).trim();
        if (payload.isEmpty() || payload.equals("[DONE]")) return null;
        try {
            JsonNode node = Json.READER.readTree(payload);
            return node != null && node.isObject() ? node : null;
        } catch (IOException e) {
            return null;
        }
    }

    @Override
    public void close() {
        done = true;
        lines.cancel();
    }
}
