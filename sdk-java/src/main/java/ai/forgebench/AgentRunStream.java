package ai.forgebench;

import com.fasterxml.jackson.databind.JsonNode;

import java.util.Iterator;
import java.util.NoSuchElementException;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * The SSE events of a streamed agent run, as JSON objects in order. Iteration
 * throws {@link ai.forgebench.errors.ForgebenchStreamException} on an
 * {@code event: error} frame, and (code {@code stream_truncated}) when the
 * stream ends without {@code agent.run.completed} or {@code [DONE]}. Close it
 * (try-with-resources) to release the connection when you stop early.
 */
public final class AgentRunStream implements Iterable<JsonNode>, AutoCloseable {
    private final SseStream events;
    private final AtomicBoolean iterated = new AtomicBoolean();

    AgentRunStream(SseStream events) {
        this.events = events;
    }

    /** Single-use: events are read off the wire as you iterate. */
    @Override
    public Iterator<JsonNode> iterator() {
        if (!iterated.compareAndSet(false, true)) {
            throw new IllegalStateException("an AgentRunStream can only be iterated once");
        }
        return new Iterator<JsonNode>() {
            @Override public boolean hasNext() { return events.hasNext(); }
            @Override public JsonNode next() {
                if (!events.hasNext()) throw new NoSuchElementException();
                return events.next();
            }
        };
    }

    /** Stops the stream and releases the connection; safe to call from another thread, and repeatedly. */
    @Override
    public void close() {
        events.close();
    }
}
