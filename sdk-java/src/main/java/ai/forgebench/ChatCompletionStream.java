package ai.forgebench;

import ai.forgebench.types.ChatCompletionChunk;

import java.util.Iterator;
import java.util.NoSuchElementException;
import java.util.Spliterator;
import java.util.Spliterators;
import java.util.stream.Stream;
import java.util.stream.StreamSupport;

/**
 * A streamed chat completion: iterate it for {@link ChatCompletionChunk}s.
 * Close it (try-with-resources) to release the connection when you stop early.
 *
 * <pre>{@code
 * try (ChatCompletionStream stream = client.chat().completions().createStream(req)) {
 *     for (ChatCompletionChunk chunk : stream) {
 *         String delta = chunk.getChoices().get(0).getDelta().getContent();
 *         if (delta != null) System.out.print(delta);
 *     }
 * }
 * }</pre>
 */
public final class ChatCompletionStream implements Iterable<ChatCompletionChunk>, AutoCloseable {
    private final SseStream events;
    private final Transport transport;
    private boolean iterated;

    ChatCompletionStream(SseStream events, Transport transport) {
        this.events = events;
        this.transport = transport;
    }

    /**
     * This call's id on the ledger, from the {@code X-Call-Id} response header —
     * the only place it can ride on the streaming path. Pass it as
     * {@code parentCallId} on the calls this one causes. Null on a server older
     * than the lineage feature.
     */
    public String getCallId() {
        return events.headers().firstValue(Trace.CALL_ID_HEADER).orElse(null);
    }

    /** Single-use: the chunks are read off the wire as you iterate. */
    @Override
    public Iterator<ChatCompletionChunk> iterator() {
        if (iterated) throw new IllegalStateException("a ChatCompletionStream can only be iterated once");
        iterated = true;
        return new Iterator<ChatCompletionChunk>() {
            @Override public boolean hasNext() { return events.hasNext(); }
            @Override public ChatCompletionChunk next() {
                if (!events.hasNext()) throw new NoSuchElementException();
                return transport.convert(events.next(), ChatCompletionChunk.class);
            }
        };
    }

    /** The chunks as a {@link Stream}; closing it closes the connection. */
    public Stream<ChatCompletionChunk> stream() {
        return StreamSupport.stream(Spliterators.spliteratorUnknownSize(iterator(), Spliterator.ORDERED), false)
                .onClose(this::close);
    }

    @Override
    public void close() {
        events.close();
    }
}
