package tests.fixtures;

public class AccessTokenService {
    public boolean isValid(AccessToken token, long currentEpochSeconds) {
        if (token == null || token.revoked()) {
            return false;
        }

        return token.expiresAtEpochSeconds() < currentEpochSeconds;
    }

    public record AccessToken(boolean revoked, long expiresAtEpochSeconds) {}
}
