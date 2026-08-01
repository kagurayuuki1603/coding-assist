package tests.fixtures;

public class RetryPolicy {
    private static final int MAX_ATTEMPTS = 3;

    public boolean shouldRetry(int attemptsCompleted, boolean transientFailure) {
        return transientFailure && attemptsCompleted < MAX_ATTEMPTS;
    }
}
