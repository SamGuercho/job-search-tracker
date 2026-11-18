"""The agent skill must stay in sync with the code: its examples and action names must exist."""
import json
import pathlib
import re
import unittest

from jobtracker import registry
from tests.test_core import reset  # noqa: F401  (sets the temp data/output env vars first)

SKILL = pathlib.Path(__file__).resolve().parent.parent / "skills" / "job-search" / "SKILL.md"


class SkillDocTests(unittest.TestCase):
    def test_json_example_matches_the_workflow_action(self):
        block = re.search(r"```json\n(.*?)```", SKILL.read_text(), re.S).group(1)
        registry.ACTIONS["workflow.new_application"].input_model.model_validate(json.loads(block))

    def test_every_action_named_in_the_skill_exists(self):
        named = set(re.findall(r"`((?:cv|company|process|application|workflow|job|analytics)\.[a-z_.]+)", SKILL.read_text()))
        self.assertTrue(named)
        result_fields = {"cv.docx", "cv.pdf", "cv.removed", "cv.warnings"}  # parts of a result, not actions
        self.assertEqual(sorted(n for n in named - result_fields if n not in registry.ACTIONS), [])


if __name__ == "__main__":
    unittest.main()
