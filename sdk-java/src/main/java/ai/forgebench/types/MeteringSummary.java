package ai.forgebench.types;

import java.util.LinkedHashMap;
import java.util.Map;

/** {@code GET /v1/metering/summary} — the tenant's spend against its budget. */
public final class MeteringSummary {
    private String tenantId;
    private long totalEvents;
    private long totalTokens;
    private double totalCostUsd;
    private Map<String, Double> byModel = new LinkedHashMap<>();
    private double monthlyLimitUsd;
    private double spentUsd;
    private double remainingUsd;

    public String getTenantId() { return tenantId; }
    public long getTotalEvents() { return totalEvents; }
    public long getTotalTokens() { return totalTokens; }
    public double getTotalCostUsd() { return totalCostUsd; }
    public Map<String, Double> getByModel() { return byModel; }
    public double getMonthlyLimitUsd() { return monthlyLimitUsd; }
    public double getSpentUsd() { return spentUsd; }
    public double getRemainingUsd() { return remainingUsd; }

    @Override
    public String toString() {
        return "MeteringSummary{spentUsd=" + spentUsd + ", remainingUsd=" + remainingUsd
                + ", monthlyLimitUsd=" + monthlyLimitUsd + ", totalTokens=" + totalTokens + "}";
    }
}
