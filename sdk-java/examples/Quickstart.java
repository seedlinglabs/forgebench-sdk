import ai.forgebench.ChatCompletionRequest;
import ai.forgebench.ChatCompletionStream;
import ai.forgebench.ChatMessage;
import ai.forgebench.Forgebench;
import ai.forgebench.Trace;
import ai.forgebench.errors.ApiException;
import ai.forgebench.errors.BudgetExceededException;
import ai.forgebench.types.ChatCompletion;
import ai.forgebench.types.ChatCompletionChunk;

/**
 * The governed chat path end to end: a call, a streamed call, and a child call
 * nested under the first on the ledger.
 *
 *   export FORGEBENCH_API_KEY=sk_...          # an agent's key
 *   export FORGEBENCH_BASE_URL=http://localhost:8000
 *   ./mvnw -q compile dependency:build-classpath -Dmdep.outputFile=target/cp.txt
 *   java -cp "target/classes:$(cat target/cp.txt)" examples/Quickstart.java
 */
public class Quickstart {
    public static void main(String[] args) {
        Forgebench client = Forgebench.builder().build(); // key + base URL from the environment
        String trace = Trace.newTraceId();

        try {
            // 1. A governed model call.
            ChatCompletion plan = client.chat().completions().create(ChatCompletionRequest.builder()
                    .model("mock-gpt")
                    .addMessage(ChatMessage.user("Prove the governed path works."))
                    .traceId(trace)
                    .build());
            System.out.println("answer  : " + plan.getContent());
            System.out.println("tokens  : " + plan.getUsage().getTotalTokens());
            System.out.println("call_id : " + plan.getCallId());

            // 2. A streamed follow-up, recorded as a child of the first call.
            try (ChatCompletionStream stream = client.chat().completions().createStream(ChatCompletionRequest.builder()
                    .model("mock-gpt")
                    .addMessage(ChatMessage.user("Stream me a sentence."))
                    .traceId(trace)
                    .parentCallId(plan.getCallId())
                    .build())) {
                System.out.print("stream  : ");
                for (ChatCompletionChunk chunk : stream) {
                    if (chunk.getChoices().isEmpty()) continue;
                    String delta = chunk.getChoices().get(0).getDelta().getContent();
                    if (delta != null) System.out.print(delta);
                }
                System.out.println();
                System.out.println("child   : " + stream.getCallId() + " (parent = " + plan.getCallId() + ")");
            }
        } catch (BudgetExceededException e) {
            // The budget gate fired before any provider call: nothing was spent.
            System.out.println("over budget: " + e.getMessage());
        } catch (ApiException e) {
            System.out.println("refused: " + e);
            System.exit(1);
        }
    }
}
