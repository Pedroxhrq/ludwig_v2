# SPDX-License-Identifier: GPL-3.0-or-later
import argparse
import contextlib
import io
import json
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import pytesseract
from PIL import Image

import ludwig


def ocr_data(text, score=95):
    data = {key: [] for key in ("text", "conf", "page_num", "block_num", "par_num", "line_num")}
    for line_number, line in enumerate(text.splitlines(), start=1):
        for word in line.split():
            for key, value in (("text", word), ("conf", score), ("page_num", 1),
                               ("block_num", 1), ("par_num", 1), ("line_num", line_number)):
                data[key].append(value)
    return data


class StateRecognitionTests(unittest.TestCase):
    def test_labeled_text_corpus(self):
        path = ludwig.REFERENCE_DIRECTORY / "benchmarks" / "text_cases.json"
        cases = json.loads(path.read_text(encoding="utf-8"))
        for case in cases:
            with self.subTest(case=case["id"]):
                actual = ludwig.identify_issuer(case["text"])
                self.assertEqual(actual.status, case["status"])
                self.assertEqual(actual.state.code if actual.state else None, case["state"])

    def test_normalization_preserves_unicode_text(self):
        self.assertEqual(ludwig.normalize_text(" São\nPaulo — Paraná "), "SAO PAULO — PARANA")

    def test_all_other_ufs_remain_unsupported(self):
        for code, name in ludwig.UF_NAMES.items():
            if code not in ludwig.SUPPORTED:
                with self.subTest(code=code):
                    result = ludwig.identify_issuer(f"ESTADO DE {name}")
                    self.assertEqual(result.status, "unsupported")
                    self.assertEqual(result.candidates, (code,))

    def test_compatibility_helper_abstains_on_conflicts(self):
        self.assertIsNone(ludwig.identify_state("ESTADO DE SAO PAULO\nESTADO DO PARANA"))

    def test_plain_text_does_not_invent_a_confidence(self):
        self.assertIsNone(ludwig.identify_issuer("ESTADO DE SÃO PAULO").confidence)

    def test_low_confidence_requires_review(self):
        result = ludwig.resolve_evidence([ludwig.Evidence("SP", 45, 0)])
        self.assertEqual(result.status, "low_confidence")
        self.assertIsNone(result.state)

    def test_weak_conflicting_evidence_is_not_silently_discarded(self):
        result = ludwig.resolve_evidence([ludwig.Evidence("SP", 95, 0), ludwig.Evidence("PR", 30, 90)])
        self.assertEqual(result.status, "ambiguous")
        self.assertEqual(result.candidates, ("PR", "SP"))

    def test_best_orientation_uses_highest_matching_score(self):
        result = ludwig.resolve_evidence([ludwig.Evidence("SP", 45, 0), ludwig.Evidence("SP", 93, 180)])
        self.assertEqual(result.status, "recognized")
        self.assertEqual(result.rotation, 180)

    def test_wrapped_heading_retains_lower_confidence(self):
        lines = [ludwig.OCRLine("ESTADO DE", 30), ludwig.OCRLine("SAO PAULO", 95)]
        self.assertEqual(ludwig.resolve_evidence(ludwig.extract_evidence(lines)).status, "low_confidence")


class ImageTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(directory) / "identificação-文.png"

    def test_unicode_path_and_exact_pixels(self):
        image = np.full((40, 80, 3), (10, 100, 200), dtype=np.uint8)
        success, encoded = cv2.imencode(".png", image)
        self.assertTrue(success)
        self.path.write_bytes(encoded.tobytes())
        np.testing.assert_array_equal(ludwig.load_image(self.path), image)

    def test_missing_empty_corrupt_and_truncated_images(self):
        with self.assertRaises(FileNotFoundError):
            ludwig.load_image(self.path)
        for content in (b"", b"not an image", b"\x89PNG\r\n\x1a\n"):
            self.path.write_bytes(content)
            with self.subTest(content=content), self.assertRaises((ValueError, OSError)):
                ludwig.load_image(self.path)

    def test_rejects_file_size_before_opening_decoder(self):
        self.path.write_bytes(b"x" * 1025)
        with patch.object(ludwig, "MAX_FILE_BYTES", 1024), patch.object(Image, "open") as decoder:
            with self.assertRaisesRegex(ValueError, "20 MiB"):
                ludwig.load_image(self.path)
        decoder.assert_not_called()

    def test_rejects_large_headers_before_pixel_allocation(self):
        stream = io.BytesIO()
        Image.new("RGB", (40, 40)).save(stream, format="PNG")
        original = stream.getvalue()
        for width, height in ((5000, 5000), (12001, 40)):
            data = bytearray(original)
            data[16:24] = struct.pack(">II", width, height)
            data[29:33] = struct.pack(">I", zlib.crc32(data[12:29]))
            self.path.write_bytes(data)
            with self.subTest(size=(width, height)), patch.object(ludwig.ImageOps, "exif_transpose") as decode:
                with self.assertRaisesRegex(ValueError, "megapixels"):
                    ludwig.load_image(self.path)
                decode.assert_not_called()

    def test_rejects_tiny_images(self):
        Image.new("RGB", (31, 100)).save(self.path)
        with self.assertRaisesRegex(ValueError, "32 pixels"):
            ludwig.load_image(self.path)

    def test_only_jpeg_and_png_are_supported(self):
        Image.new("RGB", (40, 40)).save(self.path, format="BMP")
        with self.assertRaisesRegex(ValueError, "JPEG ou PNG"):
            ludwig.load_image(self.path)

    def test_animated_png_is_rejected(self):
        Image.new("RGB", (40, 40), "white").save(
            self.path, format="PNG", save_all=True,
            append_images=[Image.new("RGB", (40, 40), "black")], duration=100,
        )
        with self.assertRaisesRegex(ValueError, "animação"):
            ludwig.load_image(self.path)

    def test_exif_orientation_is_applied(self):
        exif = Image.Exif()
        exif[274] = 6
        Image.new("RGB", (80, 40)).save(self.path, format="JPEG", exif=exif)
        self.assertEqual(ludwig.load_image(self.path).shape, (80, 40, 3))

    def test_transparent_pixels_are_composited_on_white(self):
        Image.new("RGBA", (40, 40), (0, 0, 0, 0)).save(self.path)
        self.assertTrue(np.all(ludwig.load_image(self.path) == 255))

    def test_preprocessing_bounds_both_dimensions(self):
        for shape in ((100, 200, 3), (200, 4000, 3), (4000, 200, 3)):
            with self.subTest(shape=shape):
                prepared = ludwig.prepare_ocr_image(np.zeros(shape, dtype=np.uint8))
                self.assertEqual(prepared.ndim, 2)
                self.assertLessEqual(max(prepared.shape), ludwig.OCR_MAX_SIDE)
                self.assertAlmostEqual(prepared.shape[1] / prepared.shape[0], shape[1] / shape[0], places=2)

    def test_clockwise_rotation_and_default_full_document(self):
        image = np.arange(100 * 200 * 3, dtype=np.uint8).reshape(100, 200, 3)
        rotated = ludwig.rotate_image(image, 90)
        np.testing.assert_array_equal(rotated[0, -1], image[0, 0])
        self.assertIs(ludwig.crop_document(rotated), rotated)
        with self.assertRaises(ValueError):
            ludwig.rotate_image(image, 45)

    def test_manual_crop_uses_oriented_image_coordinates(self):
        image = np.arange(100 * 200 * 3, dtype=np.uint8).reshape(100, 200, 3)
        crop = ludwig.crop_document(image, (0.1, 0.2, 0.5, 0.5))
        np.testing.assert_array_equal(crop, image[20:50, 20:100])

    def test_invalid_or_empty_crop_is_rejected(self):
        for value in ("0,0,1", "nan,0,1,1", "0,0,inf,1", "-1,0,1,1", "0.9,0,0.1,1", "0,0,1,2"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                ludwig.parse_crop(value)
        with self.assertRaisesRegex(ValueError, "2 pixels"):
            ludwig.crop_document(np.zeros((40, 40, 3), dtype=np.uint8), (0, 0, 0.001, 1))


class OCRTests(unittest.TestCase):
    def setUp(self):
        self.image = np.zeros((100, 200, 3), dtype=np.uint8)

    def test_checks_all_orientations_under_one_deadline(self):
        ticks = [0, 0, 1, 1, 2, 2, 3, 3, 4]
        with patch.object(ludwig.time, "monotonic", side_effect=ticks), patch.object(
            pytesseract, "image_to_data", return_value=ocr_data("ESTADO DE SAO PAULO")
        ) as ocr:
            result = ludwig.analyze_image(self.image)
        self.assertEqual(result.state.code, "SP")
        self.assertEqual([call.kwargs["timeout"] for call in ocr.call_args_list], [30, 29, 28, 27])
        for call in ocr.call_args_list:
            self.assertEqual(call.kwargs["lang"], "por")
            self.assertEqual(call.kwargs["config"], "--psm 6")

    def test_explicit_rotation_runs_once_and_passes_correct_image(self):
        with patch.object(pytesseract, "image_to_data", return_value=ocr_data("ESTADO DO PARANA")) as ocr:
            result = ludwig.analyze_image(self.image, rotation="90")
        self.assertEqual(result.rotation, 90)
        self.assertEqual(ocr.call_count, 1)
        self.assertEqual(ocr.call_args.args[0].shape, (400, 200))

    def test_conflicting_rotations_abstain(self):
        with patch.object(pytesseract, "image_to_data", side_effect=[
            ocr_data("ESTADO DE SAO PAULO"), ocr_data("ESTADO DO PARANA"), ocr_data(""), ocr_data("")
        ]):
            self.assertEqual(ludwig.analyze_image(self.image).status, "ambiguous")

    def test_ocr_timeout_becomes_clear_error(self):
        with patch.object(pytesseract, "image_to_data", side_effect=RuntimeError("Tesseract process timeout")):
            with self.assertRaisesRegex(TimeoutError, "Tempo limite"):
                ludwig.analyze_image(self.image)

    def test_deadline_is_enforced_even_after_an_earlier_match(self):
        with patch.object(ludwig.time, "monotonic", side_effect=[0, 0, 31]), patch.object(
            pytesseract, "image_to_data", return_value=ocr_data("ESTADO DO PARANA")
        ):
            with self.assertRaises(TimeoutError):
                ludwig.analyze_image(self.image)

    def test_invalid_timeouts_and_scores_do_not_start_ocr(self):
        cases = [{"timeout": value} for value in (0, -1, float("nan"), float("inf"), 301)]
        cases += [{"min_confidence": value} for value in (-1, 101, float("nan"))]
        cases += [{"rotation": "45"}]
        with patch.object(pytesseract, "image_to_data") as ocr:
            for kwargs in cases:
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    ludwig.analyze_image(self.image, **kwargs)
        ocr.assert_not_called()

    def test_line_scores_do_not_hide_low_confidence_tokens(self):
        data = ocr_data("ESTADO DE SAO PAULO")
        data["conf"][-1] = 20
        with patch.object(pytesseract, "image_to_data", return_value=data):
            self.assertEqual(ludwig.analyze_image(self.image).status, "low_confidence")

    def test_invalid_ocr_scores_are_treated_as_zero(self):
        for value in ("nan", "-1", "inf"):
            data = ocr_data("ESTADO DE SAO PAULO")
            data["conf"][-1] = value
            self.assertEqual(ludwig.ocr_lines(data)[0].confidence, 0)

    def test_heading_cannot_borrow_state_from_another_paragraph_or_block(self):
        for field in ("block_num", "par_num", "page_num"):
            data = ocr_data("ESTADO DE\nSAO PAULO")
            data[field][-2:] = [2, 2]
            with self.subTest(field=field):
                result = ludwig.resolve_evidence(ludwig.extract_evidence(ludwig.ocr_lines(data)))
                self.assertEqual(result.status, "unknown")


class CommandLineTests(unittest.TestCase):
    def setUp(self):
        self.input_path = ludwig.REFERENCE_DIRECTORY / "SC.jpg"
        self.stdout = self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.stderr = self.enterContext(contextlib.redirect_stderr(io.StringIO()))

    def test_headless_result_skips_display(self):
        with patch.object(pytesseract, "image_to_data", return_value=ocr_data("ESTADO DE SAO PAULO")), \
                patch.object(ludwig, "display_comparison") as display:
            self.assertEqual(ludwig.main([str(self.input_path), "--no-display"]), 0)
        self.assertIn("IIRGD", self.stdout.getvalue())
        display.assert_not_called()

    def test_correct_reference_is_loaded_from_another_directory(self):
        for state in ludwig.STATES:
            with self.subTest(state=state.code), tempfile.TemporaryDirectory() as directory, \
                    contextlib.chdir(directory), patch.object(
                        pytesseract, "image_to_data", return_value=ocr_data("ESTADO DE " + state.name)
                    ), patch.object(cv2, "imshow") as show, patch.object(cv2, "waitKey"), \
                    patch.object(cv2, "destroyAllWindows") as close:
                self.assertEqual(ludwig.main([str(self.input_path)]), 0)
                np.testing.assert_array_equal(
                    show.call_args.args[1], ludwig.load_image(ludwig.REFERENCE_DIRECTORY / f"{state.code}.jpg")
                )
                close.assert_called_once_with()

    def test_all_abstentions_return_one_without_display(self):
        for text, score in (("NATURALIDADE: SAO PAULO", 95), ("ESTADO DE MINAS GERAIS", 95),
                            ("ESTADO DO PARANA\nESTADO DE SAO PAULO", 95), ("ESTADO DO PARANA", 20)):
            with self.subTest(text=text), patch.object(
                pytesseract, "image_to_data", return_value=ocr_data(text, score)
            ), patch.object(ludwig, "display_comparison") as display:
                self.assertEqual(ludwig.main([str(self.input_path)]), 1)
                display.assert_not_called()

    def test_json_output_is_structured_and_contains_no_raw_ocr(self):
        text = "ESTADO DO PARANA\nNOME: PRIVATE PERSON"
        with patch.object(pytesseract, "image_to_data", return_value=ocr_data(text)), \
                patch.object(ludwig, "display_comparison") as display:
            self.assertEqual(ludwig.main([str(self.input_path), "--json"]), 0)
        result = json.loads(self.stdout.getvalue())
        self.assertEqual(result["state"]["code"], "PR")
        self.assertNotIn("PRIVATE PERSON", self.stdout.getvalue())
        display.assert_not_called()

    def test_default_input_uses_current_directory(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.chdir(directory):
            Path("di.jpg").write_bytes(self.input_path.read_bytes())
            with patch.object(pytesseract, "image_to_data", return_value=ocr_data("ESTADO DO PARANA")):
                self.assertEqual(ludwig.main(["--no-display"]), 0)

    def test_missing_input_does_not_run_ocr(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(pytesseract, "image_to_data") as ocr:
            self.assertEqual(ludwig.main([str(Path(directory) / "missing.jpg")]), 2)
        ocr.assert_not_called()
        self.assertIn("missing.jpg", self.stderr.getvalue())

    def test_errors_have_json_when_requested(self):
        with patch.object(pytesseract, "image_to_data", side_effect=RuntimeError("Tesseract process timeout")):
            self.assertEqual(ludwig.main([str(self.input_path), "--json"]), 2)
        self.assertEqual(json.loads(self.stdout.getvalue())["status"], "error")

    def test_tesseract_setup_errors_explain_remedy(self):
        for error, expected in ((pytesseract.TesseractNotFoundError(), "--tesseract-cmd"),
                                (pytesseract.TesseractError(1, "Missing por.traineddata"), "por")):
            with self.subTest(error=error), patch.object(pytesseract, "image_to_data", side_effect=error):
                self.assertEqual(ludwig.main([str(self.input_path), "--no-display"]), 2)
                self.assertIn(expected, self.stderr.getvalue())

    def test_custom_tesseract_path(self):
        with patch.object(pytesseract.pytesseract, "tesseract_cmd", "tesseract"), patch.object(
            pytesseract, "image_to_data", return_value=ocr_data("ESTADO DO PARANA")
        ):
            self.assertEqual(ludwig.main([str(self.input_path), "--no-display", "--tesseract-cmd", "custom.exe"]), 0)
            self.assertEqual(pytesseract.pytesseract.tesseract_cmd, "custom.exe")

    def test_display_failure_closes_windows_and_does_not_print_success(self):
        with patch.object(pytesseract, "image_to_data", return_value=ocr_data("ESTADO DO PARANA")), \
                patch.object(cv2, "imshow", side_effect=cv2.error("display unavailable")), \
                patch.object(cv2, "destroyAllWindows") as close:
            self.assertEqual(ludwig.main([str(self.input_path)]), 2)
        close.assert_called_once_with()
        self.assertEqual(self.stdout.getvalue(), "")

    def test_invalid_crop_fails_before_ocr(self):
        with patch.object(pytesseract, "image_to_data") as ocr, self.assertRaises(SystemExit) as error:
            ludwig.main([str(self.input_path), "--crop", "0,0,0,1"])
        self.assertEqual(error.exception.code, 2)
        ocr.assert_not_called()

    def test_import_has_no_side_effects(self):
        result = subprocess.run([sys.executable, "-c", "import ludwig; import benchmark"],
                                cwd=ludwig.REFERENCE_DIRECTORY, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout + result.stderr, "")


if __name__ == "__main__":
    unittest.main()
