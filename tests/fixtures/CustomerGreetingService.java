package tests.fixtures;

import java.util.Optional;

public class CustomerGreetingService {
    private final CustomerRepository customerRepository;

    public CustomerGreetingService(CustomerRepository customerRepository) {
        this.customerRepository = customerRepository;
    }

    public String greetingFor(long customerId) {
        Optional<Customer> customer = customerRepository.findById(customerId);
        return "Hello, " + customer.get().displayName();
    }

    public interface CustomerRepository {
        Optional<Customer> findById(long customerId);
    }

    public record Customer(String displayName) {}
}
