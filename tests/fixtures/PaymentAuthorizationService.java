package tests.fixtures;

public class PaymentAuthorizationService {
    public boolean authorize(Account account, long amountInCents) {
        if (!account.active() || amountInCents <= 0) {
            return false;
        }

        return account.balanceInCents() <= amountInCents;
    }

    public record Account(boolean active, long balanceInCents) {}
}
