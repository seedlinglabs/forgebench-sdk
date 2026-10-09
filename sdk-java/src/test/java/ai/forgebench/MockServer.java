package ai.forgebench;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Deque;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** A scripted stand-in for the control plane: queue responses per path, inspect requests. */
final class MockServer implements AutoCloseable {
    interface Responder {
        void respond(HttpExchange ex) throws IOException;
    }

    static final class Recorded {
        final String method;
        final String path;
        final Map<String, List<String>> headers;
        final String body;

        Recorded(String method, String path, Map<String, List<String>> headers, String body) {
            this.method = method;
            this.path = path;
            this.headers = headers;
            this.body = body;
        }

        String header(String name) {
            for (Map.Entry<String, List<String>> e : headers.entrySet()) {
                if (e.getKey().equalsIgnoreCase(name)) return e.getValue().get(0);
            }
            return null;
        }
    }

    private final HttpServer server;
    private final Map<String, Deque<Responder>> routes = new HashMap<>();
    final List<Recorded> requests = Collections.synchronizedList(new ArrayList<>());

    MockServer() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", this::handle);
        server.start();
    }

    String baseUrl() {
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    MockServer on(String path, Responder... responders) {
        Deque<Responder> q = routes.computeIfAbsent(path, k -> new ArrayDeque<>());
        Collections.addAll(q, responders);
        return this;
    }

    Recorded last() {
        return requests.get(requests.size() - 1);
    }

    private synchronized Responder next(String path) {
        Deque<Responder> q = routes.get(path);
        if (q == null || q.isEmpty()) return null;
        // The last scripted response repeats.
        return q.size() > 1 ? q.poll() : q.peek();
    }

    private void handle(HttpExchange ex) throws IOException {
        String body = new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
        requests.add(new Recorded(ex.getRequestMethod(), ex.getRequestURI().toString(),
                new HashMap<>(ex.getRequestHeaders()), body));
        Responder r = next(ex.getRequestURI().getPath());
        if (r == null) {
            send(ex, 404, "{\"detail\":\"no mock route\"}", "application/json");
            return;
        }
        try {
            r.respond(ex);
        } finally {
            ex.close();
        }
    }

    static Responder json(int status, String json) {
        return ex -> send(ex, status, json, "application/json");
    }

    static Responder sse(String body) {
        return ex -> send(ex, 200, body, "text/event-stream");
    }

    static void send(HttpExchange ex, int status, String body, String contentType) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().set("Content-Type", contentType);
        ex.sendResponseHeaders(status, bytes.length == 0 ? -1 : bytes.length);
        if (bytes.length > 0) {
            try (OutputStream os = ex.getResponseBody()) {
                os.write(bytes);
            }
        }
    }

    @Override
    public void close() {
        server.stop(0);
    }
}
