import unittest

from cd_assist.evidence_validation import (
    require_unique,
    validate_evidence_indices_in_range,
)


class EvidenceValidationTests(unittest.TestCase):
    def test_unique_values_are_returned_unchanged(self):
        values = [0, 2, 1]

        self.assertIs(values, require_unique(values, "Evidence indices"))

    def test_duplicate_values_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Evidence indices must be unique"):
            require_unique([0, 1, 0], "Evidence indices")

    def test_boundary_indices_are_valid(self):
        validate_evidence_indices_in_range([0, 2], evidence_count=3)

    def test_negative_and_upper_bound_indices_are_rejected(self):
        for index in (-1, 3):
            with self.subTest(index=index):
                with self.assertRaisesRegex(ValueError, f"Evidence index {index}"):
                    validate_evidence_indices_in_range([index], evidence_count=3)

    def test_supports_a_domain_specific_error_message(self):
        with self.assertRaisesRegex(ValueError, "Invalid reference: 2"):
            validate_evidence_indices_in_range(
                [2],
                evidence_count=1,
                error_message="Invalid reference: {index}",
            )


if __name__ == "__main__":
    unittest.main()
