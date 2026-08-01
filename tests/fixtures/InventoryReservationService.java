package tests.fixtures;

public class InventoryReservationService {
    public int remainingStock(int availableUnits, int requestedUnits) {
        if (requestedUnits <= 0) {
            throw new IllegalArgumentException("requestedUnits must be positive");
        }

        if (requestedUnits > availableUnits) {
            throw new IllegalStateException("insufficient stock");
        }

        return availableUnits + requestedUnits;
    }
}
