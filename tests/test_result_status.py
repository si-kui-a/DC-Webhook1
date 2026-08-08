import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ops.result_status import ResultStatus, TaskResult


class ResultStatusTests(unittest.TestCase):
    def test_serializes_business_status(self):
        result = TaskResult(ResultStatus.DEGRADED, "demo", "run-1", "AI unavailable")
        self.assertEqual(result.to_dict()["status"], "DEGRADED")
        self.assertEqual(result.to_dict()["task"], "demo")


if __name__ == "__main__":
    unittest.main()
