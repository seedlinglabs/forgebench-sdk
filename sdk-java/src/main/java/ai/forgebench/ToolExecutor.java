package ai.forgebench;

import java.util.Map;

/**
 * Your tool runner for {@code agentTools().dispatch(...)} — typically a thin
 * wrapper over your MCP client.
 */
@FunctionalInterface
public interface ToolExecutor {
    Object execute(String name, Map<String, Object> arguments) throws Exception;
}
