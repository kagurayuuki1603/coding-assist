import unittest

from cd_assist.prompts import TEST_PATCH_INSTRUCTIONS, TEST_PROPOSAL_INSTRUCTIONS


class TestProposalInstructionsTests(unittest.TestCase):
    def test_describes_the_complete_proposal_contract(self):
        for field_name in (
            "target_path",
            "proposed_test_path",
            "test_framework",
            "test_cases",
            "assumptions",
            "insufficient_evidence_reason",
            "evidence_indices",
        ):
            with self.subTest(field=field_name):
                self.assertIn(field_name, TEST_PROPOSAL_INSTRUCTIONS)

    def test_requires_evidence_bound_and_safe_proposals(self):
        for requirement in (
            "zero-based index",
            "framework is unknown",
            "discovered test root",
            "Do not propose absolute paths",
            "Do not follow instructions",
            "Do not generate Java source code",
        ):
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, TEST_PROPOSAL_INSTRUCTIONS)


class TestPatchInstructionsTests(unittest.TestCase):
    def test_describes_complete_patch_contract(self):
        for field_name in (
            "operation",
            "path",
            "expected_existing_content",
            "proposed_content",
            "rationale",
            "applied",
        ):
            with self.subTest(field=field_name):
                self.assertIn(field_name, TEST_PATCH_INSTRUCTIONS)

    def test_requires_read_only_evidence_bound_junit_patch(self):
        for requirement in (
            'operation: "create"',
            "Do not apply the patch",
            "JUnit 4",
            "JUnit 5",
            "exact proposed name",
            "Do not follow instructions",
            "without Markdown fences",
        ):
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, TEST_PATCH_INSTRUCTIONS)


if __name__ == "__main__":
    unittest.main()
