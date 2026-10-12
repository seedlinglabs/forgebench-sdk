package ai.forgebench;

import ai.forgebench.errors.ForgebenchConnectionException;
import ai.forgebench.errors.ForgebenchStreamException;
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
    private final boolean requireTerminal;
    private JsonNode next;
    // done/closed are written by close() from another thread; the rest is reader-thread only.
    private volatile boolean done;
    private volatile boolean closed;
    private boolean terminal;
    private String event;

    SseStream(Transport.LineQueue lines, HttpHeaders headers, Duration readTimeout, Duration streamTimeout,
              boolean requireTerminal) {
        this.requireTerminal = requireTerminal;
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
            String raw = readLine();
            if (raw == null) {
                // Body ended (done is set). An agent run must end on a terminal frame,
                // unless the caller closed it (that is a stop, not a truncation).
                if (requireTerminal && !terminal && !closed) {
                    throw new ForgebenchStreamException(
                            "stream ended without agent.run.completed or [DONE] (truncated)", "stream_truncated");
                }
                break;
            }
            next = feed(raw);
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

    /**
     * Stateful line parse: tracks the {@code event:} field, raises on an
     * {@code event: error} frame, and notes {@code [DONE]} /
     * {@code agent.run.completed} as terminal.
     */
    private JsonNode feed(String raw) {
        String line = raw.replaceAll("[\r\n]+$", "");
        if (line.isEmpty()) {
            event = null;
            return null;
        }
        if (line.startsWith(":")) return null;
        if (line.startsWith("event:")) {
            event = line.substring("event:".length()).trim();
            return null;
        }
        if (!line.startsWith("data:")) return null;
        String payload = line.substring("data:".length()).trim();
        if (payload.equals("[DONE]")) {
            terminal = true;
            return null;
        }
        if (payload.isEmpty()) return null;
        boolean isError = "error".equals(event);
        JsonNode node;
        try {
            node = Json.READER.readTree(payload);
        } catch (IOException e) {
            if (isError) {
                close();
                throw new ForgebenchStreamException(
                        "stream error: " + payload.substring(0, Math.min(200, payload.length())), null);
            }
            return null;
        }
        if (isError) {
            close();
            JsonNode err = node != null && node.isObject() ? node.get("error") : null;
            JsonNode msg = err != null && err.isObject() ? err.get("message") : null;
            JsonNode code = err != null && err.isObject() ? err.get("code") : null;
            throw new ForgebenchStreamException(
                    msg != null && !msg.isNull() && !msg.asText().isEmpty() ? msg.asText() : "stream error",
                    code == null || code.isNull() ? null : code.asText());
        }
        if (node == null || !node.isObject()) return null;
        if ("agent.run.completed".equals(node.path("type").asText())) terminal = true;
        return node;
    }

    @Override
    public void close() {
        closed = true;
        done = true;
        lines.cancel();
    }
}
