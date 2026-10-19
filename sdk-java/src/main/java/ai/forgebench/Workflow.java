package ai.forgebench;

import ai.forgebench.types.ChatCompletion;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * A registered workflow driven with one api key per step:
 * {@code forgebench.workflow("question-paper", keys).run()}, then
 * {@code run.step("grading").chat(request)}. Each step's calls use that step's key and
 * name the previous call of the run as their parent ({@code X-Parent-Call}); the first
 * call is a root. Blocking; streaming is not offered here.
 */
public final class Workflow {
    private final Completions completions;
    private final String slug;
    private final Map<String, String> keys;

    Workflow(Completions completions, String slug, Map<String, String> keys) {
        this.completions = completions;
        this.slug = slug;
        this.keys = new LinkedHashMap<>(keys);
        List<String> bad = new ArrayList<>();
        this.keys.forEach((name, key) -> {
            if (key == null || key.isEmpty()) bad.add(name);
        });
        if (!bad.isEmpty()) {
            throw new IllegalArgumentException("workflow " + slug + ": no api key for step(s) " + String.join(", ", bad));
        }
    }

    public String slug() {
        return slug;
    }

    /** The step names, in the order the keys were given. */
    public List<String> steps() {
        return new ArrayList<>(keys.keySet());
    }

    /** Start a run; a new run starts a new root. */
    public Run run() {
        return new Run();
    }

    /** One execution of the workflow. */
    public final class Run {
        private final List<String> callIds = new ArrayList<>();

        private Run() {}

        public Step step(String name) {
            if (!keys.containsKey(name)) {
                throw new IllegalArgumentException(
                        "workflow " + slug + ": no api key for step " + name + " (keys: " + String.join(", ", keys.keySet()) + ")");
            }
            return new Step(name, keys.get(name));
        }

        /** The {@code call_id} of every call so far that had one, in order. */
        public synchronized List<String> callIds() {
            return new ArrayList<>(callIds);
        }

        /** The newest known call id (a call without one does not break the chain), or null. */
        public synchronized String lastCallId() {
            return callIds.isEmpty() ? null : callIds.get(callIds.size() - 1);
        }

        /** The first call's id, or null. */
        public synchronized String rootCallId() {
            return callIds.isEmpty() ? null : callIds.get(0);
        }

        private synchronized void record(ChatCompletion c) {
            if (c != null && c.getCallId() != null && !c.getCallId().isEmpty()) callIds.add(c.getCallId());
        }

        /** One step of the run. */
        public final class Step {
            private final String name;
            private final String key;

            private Step(String name, String key) {
                this.name = name;
                this.key = key;
            }

            public String name() {
                return name;
            }

            /**
             * A governed chat call with THIS step's key, recorded as the child of the run's previous
             * call. Any {@code parentCallId} already on the request is replaced by the run's.
             */
            public ChatCompletion chat(ChatCompletionRequest request) {
                ChatCompletionRequest threaded = request.toBuilder().parentCallId(lastCallId()).build();
                ChatCompletion out = completions.create(threaded, Map.of("Authorization", "Bearer " + key));
                record(out);
                return out;
            }
        }
    }
}
