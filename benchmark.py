# SPDX-License-Identifier: GPL-3.0-or-later
"""Evaluate Ludwig on synthetic samples or a local, labeled JSON manifest."""

import argparse
import hashlib
import importlib.metadata
import json
import platform
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pytesseract

import ludwig


STATUSES = {"recognized", "unknown", "unsupported", "ambiguous", "low_confidence"}


def render_text(text: str) -> np.ndarray:
    """Render invented text, without recreating a real identity document."""
    image = np.full((700, 1600, 3), 255, dtype=np.uint8)
    for index, line in enumerate(text.splitlines()):
        cv2.putText(image, ludwig.normalize_text(line), (60, 100 + index * 100),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.25, (0, 0, 0), 2, cv2.LINE_AA)
    return image


def synthetic_cases():
    """Deterministic, generated regression data, not a real-document accuracy set."""
    for code, other in (("SC", "SP"), ("SP", "PR"), ("PR", "SC"), ("RS", "SP")):
        base = render_text(
            f"AMOSTRA SINTETICA - SEM VALIDADE\nESTADO DE {ludwig.UF_NAMES[code]}\n"
            f"NATURALIDADE: {ludwig.UF_NAMES[other]}"
        )
        variants = {f"rotate-{angle}": ludwig.rotate_image(base, angle) for angle in (0, 90, 180, 270)}
        variants["blur"] = cv2.GaussianBlur(base, (11, 11), 3)
        variants["margin"] = cv2.copyMakeBorder(base, 150, 150, 150, 150, cv2.BORDER_CONSTANT,
                                               value=(255, 255, 255))
        variants["low-contrast"] = (base.astype(np.float32) * 0.35 + 145).astype(np.uint8)
        success, encoded = cv2.imencode(".jpg", base, [cv2.IMWRITE_JPEG_QUALITY, 40])
        if not success:
            raise ValueError("Could not generate JPEG fixture")
        variants["jpeg"] = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        for variant, image in variants.items():
            yield {"id": f"{code.lower()}-{variant}", "status": "recognized", "state": code}, image
    negatives = (
        ("unsupported-birthplace", "ESTADO DE MINAS GERAIS\nNATURALIDADE: SAO PAULO", "unsupported"),
        ("ambiguous", "ESTADO DO PARANA\nESTADO DE SANTA CATARINA", "ambiguous"),
        ("bare-name", "SAO PAULO", "unknown"),
        ("birthplace", "NATURALIDADE: SANTA CATARINA", "unknown"),
        ("substring", "ESTADO DE PARANAPANEMA", "unknown"),
        ("empty", "", "unknown"),
        ("unrelated", "REPUBLICA FEDERATIVA DO BRASIL", "unknown"),
        ("unsupported-uf", "ORGAO EXPEDIDOR: SSP/RJ", "unsupported"),
    )
    for name, text, status in negatives:
        yield {"id": name, "status": status, "state": None}, render_text(text)


def load_manifest(path: Path) -> list[dict]:
    """Resolve local images relative to a manifest with explicit expected labels."""
    samples = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(samples, list) or not samples:
        raise ValueError("The manifest must be a nonempty JSON array.")
    seen = set()
    for sample in samples:
        if not isinstance(sample, dict) or not {"id", "image", "status", "state"} <= sample.keys():
            raise ValueError("Every sample needs id, image, status and state.")
        if not isinstance(sample["id"], str) or not sample["id"] or sample["id"] in seen:
            raise ValueError("Sample IDs must be unique, nonempty strings.")
        seen.add(sample["id"])
        if not isinstance(sample["status"], str) or sample["status"] not in STATUSES:
            raise ValueError(f"Invalid expected status for {sample['id']}")
        if sample["status"] == "recognized":
            if not isinstance(sample["state"], str) or sample["state"] not in ludwig.SUPPORTED:
                raise ValueError("Recognized samples need a supported state code.")
        elif sample["state"] is not None:
            raise ValueError("Abstention samples must have state: null.")
        if not isinstance(sample["image"], str) or not sample["image"]:
            raise ValueError("Every sample needs an image path.")
        sample["image"] = path.parent / sample["image"]
    return samples


def summarize(rows: list[dict]) -> dict:
    total = len(rows)
    accepted = sum(row["actual_status"] == "recognized" for row in rows)
    correct = sum(row["actual_status"] == "recognized" and row["expected_status"] == "recognized"
                  and row["actual_state"] == row["expected_state"] for row in rows)
    expected_accepted = sum(row["expected_status"] == "recognized" for row in rows)
    matches = sum(row["actual_status"] == row["expected_status"]
                  and row["actual_state"] == row["expected_state"] for row in rows)
    errors = sum(row["actual_status"] == "error" for row in rows)
    return {
        "samples": total, "exact_matches": matches, "errors": errors,
        "accepted": accepted, "correct_accepts": correct, "false_accepts": accepted - correct,
        "missed_supported": expected_accepted - correct,
        "precision": correct / accepted if accepted else None,
        "supported_recall": correct / expected_accepted if expected_accepted else None,
        "coverage": accepted / total if total else None,
        "exact_match_rate": matches / total if total else None,
    }


def evaluate(samples, *, timeout: float, rotation: str, min_confidence: float) -> list[dict]:
    rows = []
    for sample, image_or_path in samples:
        start = time.monotonic()
        row = {"id": sample["id"], "expected_status": sample["status"], "expected_state": sample["state"]}
        try:
            image = ludwig.load_image(image_or_path) if isinstance(image_or_path, Path) else image_or_path
            # Tie measurements to image content without recording document text or paths.
            row["image_sha256"] = hashlib.sha256(image.tobytes()).hexdigest()
            row["image_shape"] = list(image.shape)
            result = ludwig.analyze_image(image, timeout=timeout, rotation=rotation, min_confidence=min_confidence)
            row.update(actual_status=result.status, actual_state=result.state.code if result.state else None,
                       confidence=result.confidence, rotation=result.rotation)
        except (OSError, ValueError, RuntimeError, cv2.error) as error:
            row.update(actual_status="error", actual_state=None, error_type=type(error).__name__)
        row["seconds"] = round(time.monotonic() - start, 3)
        rows.append(row)
        print(f"{sample['id']}: {row['actual_status']}", file=sys.stderr, flush=True)
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", nargs="?", type=Path, help="local JSON image manifest")
    parser.add_argument("--synthetic", action="store_true", help="use 40 generated OCR regression images")
    parser.add_argument("--output", type=Path, help="write the JSON report to this file")
    parser.add_argument("--check", action="store_true", help="fail unless every expected status/state matches")
    parser.add_argument("--tesseract-cmd", type=Path)
    parser.add_argument("--ocr-timeout", type=float, default=ludwig.DEFAULT_TIMEOUT)
    parser.add_argument("--rotation", choices=("auto", "0", "90", "180", "270"), default="auto")
    parser.add_argument("--min-confidence", type=float, default=ludwig.DEFAULT_MIN_CONFIDENCE)
    args = parser.parse_args(argv)
    if bool(args.manifest) == args.synthetic:
        parser.error("provide either a manifest or --synthetic")
    if args.tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = str(args.tesseract_cmd)
    try:
        ludwig.validate_ocr_options(args.rotation, args.ocr_timeout, args.min_confidence)
        version = str(pytesseract.get_tesseract_version())
        if "por" not in pytesseract.get_languages(config=""):
            raise ValueError("Tesseract Portuguese data (por) is required.")
        samples = synthetic_cases() if args.synthetic else (
            (sample, sample["image"]) for sample in load_manifest(args.manifest)
        )
        rows = evaluate(samples, timeout=args.ocr_timeout, rotation=args.rotation, min_confidence=args.min_confidence)
        report = {
            "dataset": "generated-synthetic-v1" if args.synthetic else "local-manifest",
            "real_document_accuracy_established": False,
            "environment": {
                "python": platform.python_version(), "platform": platform.platform(), "tesseract": version,
                "dependencies": {name: importlib.metadata.version(name) for name in
                                 ("numpy", "opencv-python", "pytesseract", "Pillow", "packaging")},
                "language": "por", "tesseract_config": "--psm 6",
            },
            "settings": {"rotation": args.rotation, "ocr_timeout": args.ocr_timeout,
                         "min_confidence": args.min_confidence},
            "summary": summarize(rows), "results": rows,
        }
        serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.write_text(serialized, encoding="utf-8")
        print(serialized, end="")
        return 1 if report["summary"]["errors"] or (
            args.check and report["summary"]["exact_matches"] != len(rows)
        ) else 0
    except (OSError, ValueError, RuntimeError, cv2.error) as error:
        print(f"Benchmark error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
