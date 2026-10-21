# SPDX-License-Identifier: GPL-3.0-or-later
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import benchmark
import ludwig


class BenchmarkTests(unittest.TestCase):
    def test_metrics_penalize_wrong_states_false_accepts_and_errors(self):
        rows = [
            {"expected_status": "recognized", "expected_state": "SP", "actual_status": "recognized", "actual_state": "SP"},
            {"expected_status": "recognized", "expected_state": "PR", "actual_status": "recognized", "actual_state": "SP"},
            {"expected_status": "unknown", "expected_state": None, "actual_status": "recognized", "actual_state": "SP"},
            {"expected_status": "recognized", "expected_state": "SC", "actual_status": "error", "actual_state": None},
            {"expected_status": "unknown", "expected_state": None, "actual_status": "unknown", "actual_state": None},
        ]
        result = benchmark.summarize(rows)
        self.assertEqual(result["false_accepts"], 2)
        self.assertEqual(result["missed_supported"], 2)
        self.assertEqual(result["errors"], 1)
        self.assertEqual(result["exact_matches"], 2)
        self.assertAlmostEqual(result["precision"], 1 / 3)
        self.assertAlmostEqual(result["supported_recall"], 1 / 3)
        self.assertAlmostEqual(result["coverage"], 3 / 5)

    def test_zero_acceptances_do_not_claim_perfect_precision(self):
        result = benchmark.summarize([
            {"expected_status": "unknown", "expected_state": None, "actual_status": "unknown", "actual_state": None}
        ])
        self.assertIsNone(result["precision"])
        self.assertIsNone(result["supported_recall"])
        self.assertEqual(result["coverage"], 0)

    def test_manifest_resolves_image_paths_relative_to_itself(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps([{"id": "sample", "image": "image.png", "status": "recognized", "state": "SP"}]))
            self.assertEqual(benchmark.load_manifest(path)[0]["image"], path.parent / "image.png")

    def test_manifest_rejects_invalid_labels_and_duplicates(self):
        valid = {"id": "sample", "image": "image.png", "status": "recognized", "state": "SP"}
        invalid = [[], {}, [valid, valid], [{**valid, "status": "maybe"}],
                   [{**valid, "state": "MG"}], [{**valid, "status": "unknown"}],
                   [{**valid, "image": 3}], [{"id": "missing-fields"}],
                   [{**valid, "status": []}], [{**valid, "state": {}}]]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            for value in invalid:
                with self.subTest(value=value):
                    path.write_text(json.dumps(value))
                    with self.assertRaises(ValueError):
                        benchmark.load_manifest(path)

    def test_image_failure_is_counted_instead_of_skipped(self):
        sample = {"id": "missing", "status": "recognized", "state": "SP"}
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stderr(io.StringIO()):
            rows = benchmark.evaluate([(sample, Path(directory) / "absent.png")],
                                      timeout=30, rotation="auto", min_confidence=60)
        self.assertEqual(benchmark.summarize(rows)["errors"], 1)
        self.assertNotIn("error", rows[0])  # No local path in public report.

    def test_check_flag_fails_a_wrong_prediction_and_keeps_report(self):
        sample = {"id": "mismatch", "status": "recognized", "state": "SP"}
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()), patch.object(
                    benchmark.pytesseract, "get_tesseract_version", return_value="test"
                ), patch.object(benchmark.pytesseract, "get_languages", return_value=["por"]), \
                patch.object(benchmark, "synthetic_cases", return_value=iter([
                    (sample, np.zeros((40, 40, 3), dtype=np.uint8))
                ])), patch.object(ludwig, "analyze_image", return_value=ludwig.Recognition("unknown")):
            output = Path(directory) / "report.json"
            self.assertEqual(benchmark.main(["--synthetic", "--check", "--output", str(output)]), 1)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["summary"]["missed_supported"], 1)

    def test_synthetic_cases_have_distinct_labels_and_include_all_rotations(self):
        cases = list(benchmark.synthetic_cases())
        self.assertEqual(len(cases), 40)
        ids = {sample["id"] for sample, _ in cases}
        self.assertEqual(len(ids), len(cases))
        for code in ludwig.SUPPORTED:
            for angle in (0, 90, 180, 270):
                self.assertIn(f"{code.lower()}-rotate-{angle}", ids)

    def test_invalid_ocr_options_fail_before_tesseract_lookup(self):
        with contextlib.redirect_stderr(io.StringIO()), patch.object(
            benchmark.pytesseract, "get_tesseract_version"
        ) as version:
            self.assertEqual(benchmark.main(["--synthetic", "--ocr-timeout", "nan"]), 2)
        version.assert_not_called()


if __name__ == "__main__":
    unittest.main()
