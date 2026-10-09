package ai.forgebench;

import ai.forgebench.types.MeteringSummary;
import ai.forgebench.types.WhoAmI;

/** Account-level helpers: who this key is, and the tenant's spend. */
public final class Account {
    private final Transport t;

    Account(Transport transport) {
        this.t = transport;
    }

    /** {@code GET /v1/auth/whoami} — verify auth + tenant. */
    public WhoAmI whoami() {
        return t.convert(t.request("GET", "/v1/auth/whoami", null, null, null), WhoAmI.class);
    }

    /** {@code GET /v1/metering/summary} — budget state. */
    public MeteringSummary meteringSummary() {
        return t.convert(t.request("GET", "/v1/metering/summary", null, null, null), MeteringSummary.class);
    }
}
