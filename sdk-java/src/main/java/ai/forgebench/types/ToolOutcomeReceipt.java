package ai.forgebench.types;

/**
 * Acknowledgement of {@code POST /v1/agent-tools/report}: the decision row this
 * outcome was recorded on, and the audit row it links to.
 */
public final class ToolOutcomeReceipt {
    private String eventId;
    private String auditId;
    private boolean reported = true;

    public String getEventId() { return eventId; }
    public String getAuditId() { return auditId; }
    public boolean isReported() { return reported; }
}
