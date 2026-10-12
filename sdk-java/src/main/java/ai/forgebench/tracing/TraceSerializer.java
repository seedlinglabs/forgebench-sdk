package ai.forgebench.tracing;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.databind.json.JsonMapper;
import com.fasterxml.jackson.databind.module.SimpleModule;
import com.fasterxml.jackson.databind.node.TextNode;
import com.fasterxml.jackson.databind.ser.std.ToStringSerializer;

import java.io.IOException;
import java.io.Writer;
import java.time.temporal.TemporalAccessor;

/**
 * Turns whatever a traced call took or returned into JSON the server accepts.
 * Never throws, and never does unbounded work: values are written through a
 * writer that stops at {@link #MAX_FIELD_CHARS}, so a huge collection is cut
 * while serialising instead of being built whole first. What Jackson cannot
 * represent (cycles, odd beans) becomes its {@code toString()}.
 */
final class TraceSerializer {
    /** Same cap the server applies to each input / output / metadata field. */
    static final int MAX_FIELD_CHARS = 64_000;
    static final String TRUNCATED = "…[truncated]";
    private static final int MAX_DESCRIPTION_CHARS = 1_000;

    // Plain Jackson defaults (camelCase, as the developer wrote the fields) —
    // NOT the SDK's snake_case wire mapper. java.time values as their ISO text.
    private static final ObjectMapper MAPPER = JsonMapper.builder()
            .disable(SerializationFeature.FAIL_ON_EMPTY_BEANS)
            .disable(SerializationFeature.FAIL_ON_SELF_REFERENCES)
            .addModule(new SimpleModule().addSerializer(TemporalAccessor.class, ToStringSerializer.instance))
            .build();

    private TraceSerializer() {}

    static JsonNode toJson(Object value) {
        if (value == null) return null;
        if (value instanceof JsonNode) return (JsonNode) value;
        if (value instanceof CharSequence) return TextNode.valueOf(cut(value.toString()));
        BoundedWriter out = new BoundedWriter(MAX_FIELD_CHARS);
        try {
            MAPPER.writeValue(out, value);
            return MAPPER.readTree(out.toString());
        } catch (IOException | RuntimeException | StackOverflowError e) {
            if (out.isFull()) return TextNode.valueOf(out + TRUNCATED);
            return TextNode.valueOf(describe(value));
        }
    }

    /** {@code node} when its JSON fits {@link #MAX_FIELD_CHARS}, else cut to size with a marker. */
    static JsonNode capped(JsonNode node) {
        if (node == null || node.isNull()) return null;
        if (node.isTextual()) {
            String text = node.asText();
            return text.length() <= MAX_FIELD_CHARS ? node : TextNode.valueOf(cut(text));
        }
        String json = node.toString();
        return json.length() <= MAX_FIELD_CHARS ? node : TextNode.valueOf(cut(json));
    }

    private static String cut(String text) {
        return text.length() <= MAX_FIELD_CHARS ? text : text.substring(0, MAX_FIELD_CHARS) + TRUNCATED;
    }

    private static String describe(Object value) {
        try {
            String text = String.valueOf(value);
            return text.length() <= MAX_DESCRIPTION_CHARS ? text : text.substring(0, MAX_DESCRIPTION_CHARS);
        } catch (RuntimeException e) {
            return "<" + value.getClass().getSimpleName() + ">";
        }
    }

    /** A {@link Writer} that keeps the first {@code limit} chars, then refuses more. */
    private static final class BoundedWriter extends Writer {
        private final StringBuilder buf = new StringBuilder();
        private final int limit;
        private boolean full;

        BoundedWriter(int limit) {
            this.limit = limit;
        }

        boolean isFull() {
            return full;
        }

        @Override
        public void write(char[] cbuf, int off, int len) throws IOException {
            int room = limit - buf.length();
            if (len > room) {
                buf.append(cbuf, off, Math.max(room, 0));
                full = true;
                throw new IOException("trace value larger than " + limit + " chars");
            }
            buf.append(cbuf, off, len);
        }

        @Override
        public void flush() {
        }

        @Override
        public void close() {
        }

        @Override
        public String toString() {
            return buf.toString();
        }
    }
}
