package ai.forgebench;

import ai.forgebench.types.Task;

/**
 * Handles one task claimed by {@code agents().serve(...)}.
 *
 * <p>Return a {@link ai.forgebench.types.TaskReply} ({@code task.done(...)},
 * {@code task.ask(...)}, {@code task.fail(...)}), or a plain {@code String} /
 * {@code Map} / {@code List} of artifacts, which completes the task. An
 * exception fails the task with its message. Inside, make governed calls with
 * {@code parentCallId(task.getCallId())} so they hang under the task.
 */
@FunctionalInterface
public interface TaskHandler {
    Object handle(Task task) throws Exception;
}
