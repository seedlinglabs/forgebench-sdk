package ai.forgebench.types;

import java.util.ArrayList;
import java.util.List;

/** {@code GET /v1/auth/whoami} — the identity and tenant behind this API key. */
public final class WhoAmI {
    private String tenantId;
    private String authMethod;
    private String identityId;
    private String email;
    private List<String> roles = new ArrayList<>();
    private List<String> scopes = new ArrayList<>();

    public String getTenantId() { return tenantId; }
    public String getAuthMethod() { return authMethod; }
    public String getIdentityId() { return identityId; }
    public String getEmail() { return email; }
    public List<String> getRoles() { return roles; }
    public List<String> getScopes() { return scopes; }

    @Override
    public String toString() {
        return "WhoAmI{tenantId=" + tenantId + ", authMethod=" + authMethod + ", identityId=" + identityId
                + ", roles=" + roles + "}";
    }
}
