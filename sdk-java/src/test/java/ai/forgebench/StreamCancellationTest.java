package ai.forgebench;

import org.junit.jupiter.api.Test;

import java.util.concurrent.Flow;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class StreamCancellationTest {
    @Test
    void closingBeforeSubscriptionCancelsTheLatePublisherWithoutDemand() throws Exception {
        Transport.LineQueue lines = new Transport.LineQueue();
        lines.cancel();
        RecordingSubscription publisher = new RecordingSubscription();

        lines.onSubscribe(publisher);

        assertTrue(publisher.cancelled.get(), "a publisher attached after close must be cancelled");
        assertEquals(0, publisher.requested.get(), "a closed stream must not start the late publisher");
        assertEquals(Transport.LineQueue.END, lines.poll(100));
    }

    @Test
    void closingAfterSubscriptionCancelsThePublisherAndWakesTheReader() throws Exception {
        Transport.LineQueue lines = new Transport.LineQueue();
        RecordingSubscription publisher = new RecordingSubscription();
        lines.onSubscribe(publisher);

        lines.cancel();

        assertTrue(publisher.cancelled.get());
        assertEquals(Long.MAX_VALUE, publisher.requested.get());
        assertEquals(Transport.LineQueue.END, lines.poll(100));
    }

    private static final class RecordingSubscription implements Flow.Subscription {
        final AtomicBoolean cancelled = new AtomicBoolean();
        final AtomicLong requested = new AtomicLong();

        @Override public void request(long n) { requested.addAndGet(n); }
        @Override public void cancel() { cancelled.set(true); }
    }
}
