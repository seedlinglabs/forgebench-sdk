package ai.forgebench;

import ai.forgebench.errors.ApiException;
import ai.forgebench.errors.AuthenticationException;
import ai.forgebench.errors.BudgetExceededException;
import ai.forgebench.errors.ConflictException;
import ai.forgebench.errors.ForgebenchConnectionException;
import ai.forgebench.errors.ForgebenchException;
import ai.forgebench.errors.NotFoundException;
import ai.forgebench.errors.PermissionDeniedException;
import ai.forgebench.errors.RateLimitException;
import ai.forgebench.errors.ServerException;
import ai.forgebench.errors.ValidationException;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;

import java.io.IOException;
import java.math.BigDecimal;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpHeaders;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.Flow;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;

/**
 * HTTP transport behind every resource.
 *
 * <p>Centralizes: base-URL handling, the {@code Authorization: Bearer sk_...}
 * header (the SDK is the permanent programmatic/api-key path), JSON
 * (de)serialization, typed error mapping, bounded retries on transient
 * failures, and Server-Sent Events parsing for streamed chat completions.
 */
final class Transport {
    static final String DEFAULT_BASE_URL = "https://api.forgebench.ai";
    static final Duration DEFAULT_TIMEOUT = Duration.ofSeconds(60);
    static final int DEFAULT_MAX_RETRIES = 2;
    // The per-read timeout only bounds each individual wait for a line — a
    // connection that stays open but trickles data slower than that never
    // trips it. This is a SEPARATE, cumulative wall-clock deadline over the
    // WHOLE streamed call, so a stalled-but-alive stream still gets closed out.
    static final Duration DEFAULT_STREAM_TIMEOUT = Duration.ofSeconds(300);

    private static final Set<Integer> RETRY_STATUSES = Set.of(429, 500, 502, 503, 504);
    // Headroom over a long poll's server-side wait for the round trip itself.
    private static final Duration LONG_POLL_SLACK = Duration.ofSeconds(15);

    private final String baseUrl;
    private final Duration timeout;
    private final Duration streamTimeout;
    private final int maxRetries;
    private final Map<String, String> headers;
    private final HttpClient client;

    Transport(String apiKey, String baseUrl, Duration timeout, Duration streamTimeout, int maxRetries,
              Map<String, String> defaultHeaders, HttpClient httpClient) {
        // A single trailing slash so paths join onto it as a directory.
        this.baseUrl = baseUrl.replaceAll("/+$", "") + "/";
        this.timeout = timeout;
        this.streamTimeout = streamTimeout;
        this.maxRetries = Math.max(0, maxRetries);
        this.headers = buildHeaders(apiKey, defaultHeaders);
        this.client = httpClient != null ? httpClient : HttpClient.newBuilder()
                .connectTimeout(timeout)
                // HTTP/1.1 like the Python SDK: no h2c upgrade dance against a
                // plaintext local stack.
                .version(HttpClient.Version.HTTP_1_1)
                .build();
    }

    String baseUrl() {
        return baseUrl;
    }

    private static Map<String, String> buildHeaders(String apiKey, Map<String, String> extra) {
        Map<String, String> h = new LinkedHashMap<>();
        h.put("Accept", "application/json");
        h.put("User-Agent", "forgebench-sdk-java/" + Version.VERSION);
        if (apiKey != null && !apiKey.isEmpty()) h.put("Authorization", "Bearer " + apiKey);
        if (extra != null) h.putAll(extra);
        return h;
    }

    // --- JSON request/response ------------------------------------------------

    /** Sends a JSON request and returns the parsed body, or null on 204/empty. */
    JsonNode request(String method, String path, Object jsonBody, Map<String, ?> params,
                     Map<String, String> extraHeaders) {
        return request(method, path, jsonBody, params, extraHeaders, null);
    }

    /**
     * As {@link #request(String, String, Object, Map, Map)}, for a long poll:
     * the server holds the response up to {@code longPoll}, so the wait for
     * response headers is stretched to cover it.
     */
    JsonNode request(String method, String path, Object jsonBody, Map<String, ?> params,
                     Map<String, String> extraHeaders, Duration longPoll) {
        HttpRequest req = buildRequest(method, path, jsonBody, params, extraHeaders, false, longPoll);
        HttpResponse<byte[]> resp = send(req, HttpResponse.BodyHandlers.ofByteArray());
        raiseForResponse(resp.statusCode(), resp.headers(), resp.body());
        byte[] body = resp.body();
        if (resp.statusCode() == 204 || body == null || body.length == 0) return null;
        try {
            return Json.READER.readTree(body);
        } catch (IOException e) {
            throw new ForgebenchException("control plane returned a non-JSON body: " + e.getMessage(), e);
        }
    }

    <T> T convert(JsonNode node, Class<T> type) {
        try {
            return Json.READER.treeToValue(node, type);
        } catch (JsonProcessingException e) {
            throw new ForgebenchException("could not parse " + type.getSimpleName() + ": " + e.getMessage(), e);
        }
    }

    // --- Server-Sent Events -----------------------------------------------------

    /**
     * Opens a streamed request. Non-2xx responses raise the typed error before
     * any event is read; transient failures retry like {@link #request}.
     */
    SseStream streamSse(String method, String path, Object jsonBody, Map<String, ?> params,
                        Map<String, String> extraHeaders) {
        HttpRequest req = buildRequest(method, path, jsonBody, params, extraHeaders, true, null);
        HttpResponse<StreamBody> resp = send(req, Transport::streamBodyHandler);
        StreamBody body = resp.body();
        if (body.errorBytes != null) {
            raiseForResponse(resp.statusCode(), resp.headers(), body.errorBytes);
        }
        return new SseStream(body.lines, resp.headers(), timeout, streamTimeout);
    }

    /** Either a live line queue (2xx) or the buffered error body (anything else). */
    static final class StreamBody {
        final LineQueue lines;
        final byte[] errorBytes;

        private StreamBody(LineQueue lines, byte[] errorBytes) {
            this.lines = lines;
            this.errorBytes = errorBytes;
        }
    }

    private static HttpResponse.BodySubscriber<StreamBody> streamBodyHandler(HttpResponse.ResponseInfo info) {
        if (info.statusCode() / 100 != 2) {
            return HttpResponse.BodySubscribers.mapping(
                    HttpResponse.BodySubscribers.ofByteArray(), b -> new StreamBody(null, b));
        }
        LineQueue lines = new LineQueue();
        HttpResponse.BodySubscriber<Void> inner = HttpResponse.BodySubscribers.fromLineSubscriber(lines);
        StreamBody body = new StreamBody(lines, null);
        // Hand the response back as soon as headers arrive — the lines are
        // consumed from the queue while the body is still streaming.
        return new HttpResponse.BodySubscriber<StreamBody>() {
            @Override public CompletionStage<StreamBody> getBody() { return CompletableFuture.completedFuture(body); }
            @Override public void onSubscribe(Flow.Subscription s) { inner.onSubscribe(s); }
            @Override public void onNext(List<ByteBuffer> item) { inner.onNext(item); }
            @Override public void onError(Throwable t) { inner.onError(t); }
            @Override public void onComplete() { inner.onComplete(); }
        };
    }

    /** Receives body lines on the HTTP client's thread; the caller polls them. */
    static final class LineQueue implements Flow.Subscriber<String> {
        static final Object END = new Object();

        private final BlockingQueue<Object> queue = new LinkedBlockingQueue<>();
        private volatile Flow.Subscription subscription;

        @Override public void onSubscribe(Flow.Subscription s) {
            subscription = s;
            s.request(Long.MAX_VALUE);
        }
        @Override public void onNext(String line) { queue.add(line); }
        @Override public void onError(Throwable t) { queue.add(t); }
        @Override public void onComplete() { queue.add(END); }

        /** The next line, {@link #END}, a {@link Throwable}, or null on timeout. */
        Object poll(long millis) throws InterruptedException {
            return queue.poll(millis, TimeUnit.MILLISECONDS);
        }

        void cancel() {
            Flow.Subscription s = subscription;
            if (s != null) s.cancel();
        }
    }

    // --- plumbing -----------------------------------------------------------------

    private HttpRequest buildRequest(String method, String path, Object jsonBody, Map<String, ?> params,
                                     Map<String, String> extraHeaders, boolean stream, Duration longPoll) {
        Duration t = longPoll == null ? timeout : max(timeout, longPoll.plus(LONG_POLL_SLACK));
        HttpRequest.Builder b = HttpRequest.newBuilder(URI.create(fullUrl(path, params)))
                // For a stream this bounds only the wait for response headers.
                .timeout(t);
        headers.forEach(b::setHeader);
        if (stream) b.setHeader("Accept", "text/event-stream");
        // Unset values are dropped, so a caller can pass {X-Parent-Call: parent}
        // unconditionally and an absent parent simply sends no header (the
        // server treats a missing header as "this call is a root").
        if (extraHeaders != null) {
            extraHeaders.forEach((k, v) -> { if (v != null) b.setHeader(k, v); });
        }
        if (jsonBody != null) {
            try {
                b.setHeader("Content-Type", "application/json");
                b.method(method, HttpRequest.BodyPublishers.ofByteArray(Json.WRITER.writeValueAsBytes(jsonBody)));
            } catch (JsonProcessingException e) {
                throw new ForgebenchException("could not serialize request body: " + e.getMessage(), e);
            }
        } else {
            b.method(method, HttpRequest.BodyPublishers.noBody());
        }
        return b.build();
    }

    private String fullUrl(String path, Map<String, ?> params) {
        StringBuilder url = new StringBuilder(baseUrl).append(path.replaceAll("^/+", ""));
        if (params != null) {
            char sep = '?';
            for (Map.Entry<String, ?> e : params.entrySet()) {
                if (e.getValue() == null) continue;
                url.append(sep)
                        .append(URLEncoder.encode(e.getKey(), StandardCharsets.UTF_8)).append('=')
                        .append(URLEncoder.encode(String.valueOf(e.getValue()), StandardCharsets.UTF_8));
                sep = '&';
            }
        }
        return url.toString();
    }

    private <T> HttpResponse<T> send(HttpRequest req, HttpResponse.BodyHandler<T> handler) {
        for (int attempt = 0; ; attempt++) {
            HttpResponse<T> resp;
            try {
                resp = client.send(req, handler);
            } catch (IOException e) {
                if (attempt < maxRetries) {
                    sleep(backoff(attempt));
                    continue;
                }
                throw new ForgebenchConnectionException(
                        "failed to reach control plane at " + req.uri() + ": " + e, e);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new ForgebenchConnectionException("interrupted calling " + req.uri(), e);
            }
            if (RETRY_STATUSES.contains(resp.statusCode()) && attempt < maxRetries) {
                discard(resp);
                sleep(retryAfter(resp.headers(), attempt));
                continue;
            }
            return resp;
        }
    }

    private static void discard(HttpResponse<?> resp) {
        Object body = resp.body();
        if (body instanceof StreamBody && ((StreamBody) body).lines != null) {
            ((StreamBody) body).lines.cancel();
        }
    }

    private static Duration max(Duration a, Duration b) {
        return a.compareTo(b) >= 0 ? a : b;
    }

    /** A wait as the control plane reads it: seconds, as a decimal. */
    static String seconds(Duration d) {
        return BigDecimal.valueOf(d.toMillis()).movePointLeft(3).stripTrailingZeros().toPlainString();
    }

    private static Duration backoff(int attempt) {
        return Duration.ofMillis((long) Math.min(500 * Math.pow(2, attempt), 8000));
    }

    private static Duration retryAfter(HttpHeaders headers, int attempt) {
        String ra = headers.firstValue("retry-after").orElse(null);
        if (ra != null) {
            try {
                return Duration.ofMillis((long) (Math.min(Double.parseDouble(ra.trim()), 30.0) * 1000));
            } catch (NumberFormatException ignored) {
                // An HTTP-date Retry-After falls back to exponential backoff.
            }
        }
        return backoff(attempt);
    }

    private static void sleep(Duration d) {
        try {
            Thread.sleep(d.toMillis());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new ForgebenchConnectionException("interrupted while backing off", e);
        }
    }

    /**
     * Raise the most specific {@link ApiException} for a non-2xx response.
     * 2xx responses return without raising.
     */
    static void raiseForResponse(int status, HttpHeaders headers, byte[] raw) {
        if (status / 100 == 2) return;

        String code = null;
        String detail = null;
        Object body = null;
        String text = raw == null ? "" : new String(raw, StandardCharsets.UTF_8);
        try {
            JsonNode node = Json.READER.readTree(text);
            body = node == null ? null : Json.READER.convertValue(node, Object.class);
            if (node != null && node.isObject()) {
                // Two response shapes reach this code:
                //   generic ErrorResponse:  {"detail": str, "code": str | null}
                //   structured error body:  {"detail": {"message": ..., "code": ...}}
                // FastAPI nests a dict detail under "detail", so code is read
                // from wherever the raiser actually put it.
                JsonNode d = node.get("detail");
                JsonNode c;
                if (d != null && d.isObject()) {
                    JsonNode m = d.get("message");
                    detail = m != null && !m.isNull() ? m.asText() : d.toString();
                    c = d.get("code");
                } else {
                    detail = d == null || d.isNull() ? null : (d.isTextual() ? d.asText() : d.toString());
                    c = node.get("code");
                }
                code = c == null || c.isNull() ? null : c.asText();
            }
        } catch (IOException e) {
            body = text.isEmpty() ? null : text;
            detail = text.isEmpty() ? null : text;
        }

        String message = detail != null ? detail : "HTTP " + status;
        String requestId = headers.firstValue("x-request-id").orElse(null);

        switch (status) {
            case 401: throw new AuthenticationException(message, status, code, body, requestId);
            case 402: throw new BudgetExceededException(message, status, code, body, requestId);
            case 403: throw new PermissionDeniedException(message, status, code, body, requestId);
            case 404: throw new NotFoundException(message, status, code, body, requestId);
            case 409: throw new ConflictException(message, status, code, body, requestId);
            case 422: throw new ValidationException(message, status, code, body, requestId);
            case 429: throw new RateLimitException(message, status, code, body, requestId);
            default:
                if (status >= 500) throw new ServerException(message, status, code, body, requestId);
                throw new ApiException(message, status, code, body, requestId);
        }
    }
}
