# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2019 Pedro Henrique P.
"""Find issuing-state evidence in Brazilian identity-document images."""

import argparse
import io
import json
import math
import re
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np
import pytesseract
from PIL import Image, ImageOps, UnidentifiedImageError

REFERENCE_DIRECTORY = Path(__file__).resolve().parent
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 20_000_000
MAX_DIMENSION = 12_000
OCR_MAX_SIDE = 2000
DEFAULT_TIMEOUT = 30.0
DEFAULT_MIN_CONFIDENCE = 60.0


@dataclass(frozen=True)
class State:
    code: str
    name: str
    description: str


STATES = (
    State("SC", "Santa Catarina", "SSPSC - Secret. de Seg. Pública de Santa Catarina\n"
          "IGP-SC - Inst. Geral de Perícias de Santa Catarina\n"
          "SSP - Secretaria de Seg. Pública\n"
          "IISC - Inst. de Identificação de Santa Catarina"),
    State("SP", "São Paulo", "IIRGD - Inst. de Ident. Ricardo Gumbleton Daunt"),
    State("PR", "Paraná", "SESP - Secret. de Estado da Seg. Pública do Paraná\n"
          "IIPR - Inst. de Identificação de Paraná"),
    State("RS", "Rio Grande do Sul", "IIRS - Instituto de Ident. do Rio Grande do Sul\n"
          "DIRS - Dep. de Identificação. do Rio Grande do Sul"),
)
SUPPORTED = {state.code: state for state in STATES}
# Other UFs are unsupported issuers, never replaced by a birthplace.
UF_NAMES = {
    "AC": "ACRE", "AL": "ALAGOAS", "AP": "AMAPA", "AM": "AMAZONAS",
    "BA": "BAHIA", "CE": "CEARA", "DF": "DISTRITO FEDERAL",
    "ES": "ESPIRITO SANTO", "GO": "GOIAS", "MA": "MARANHAO",
    "MT": "MATO GROSSO", "MS": "MATO GROSSO DO SUL", "MG": "MINAS GERAIS",
    "PA": "PARA", "PB": "PARAIBA", "PR": "PARANA", "PE": "PERNAMBUCO",
    "PI": "PIAUI", "RJ": "RIO DE JANEIRO", "RN": "RIO GRANDE DO NORTE",
    "RS": "RIO GRANDE DO SUL", "RO": "RONDONIA", "RR": "RORAIMA",
    "SC": "SANTA CATARINA", "SP": "SAO PAULO", "SE": "SERGIPE", "TO": "TOCANTINS",
}
NAME_TO_UF = {name: code for code, name in UF_NAMES.items()}
NAME_PATTERN = re.compile(r"\b(?:" + "|".join(
    re.escape(name) for name in sorted(NAME_TO_UF, key=len, reverse=True)
) + r")\b")
CODE_PATTERN = re.compile(r"\b(?:" + "|".join(UF_NAMES) + r")\b")
ISSUER_LABEL = (
    r"ORGAO\s+(?:EXPEDIDOR|EMISSOR)|UF\s+(?:DE\s+)?(?:EMISSAO|EXPEDICAO)|"
    r"ESTADO\s+(?:EMISSOR|EXPEDIDOR)|EMITIDO\s+POR"
)
OTHER_LABEL = (
    r"NATURALIDADE|NACIONALIDADE|(?:LOCAL\s+(?:DE\s+)?)?NASCIMENTO|"
    r"DATA\s+(?:DE\s+)?(?:NASCIMENTO|EXPEDICAO|EMISSAO)|"
    r"ENDERECO|RESIDENCIA|DOMICILIO|FILIACAO|NOME|CPF|REGISTRO\s+GERAL"
)
FIELD_BOUNDARY = re.compile(r"\b(?=(?:" + ISSUER_LABEL + "|" + OTHER_LABEL + r")\b)")
OTHER_FIELD = re.compile(r"^(?:" + OTHER_LABEL + r")\b")
ISSUER_START = re.compile(
    r"^(?:" + ISSUER_LABEL + r"|ESTADO\s+D[EO]|GOVERNO\s+D[EO]\s+ESTADO(?:\s+D[EO])?|"
    r"SECRETARIA\b.*?SEGURANCA\s+PUBLICA|INSTITUTO\s+(?:DE\s+)?IDENTIFICACAO|"
    r"SSP|SESP|SSPSC|IGP(?:-SC)?|IISC|IIRGD|IIPR|IIRS|DIRS)\b"
)


@dataclass(frozen=True)
class OCRLine:
    text: str
    confidence: float | None = None


@dataclass(frozen=True)
class Evidence:
    code: str
    confidence: float | None
    rotation: int


@dataclass(frozen=True)
class Recognition:
    status: str
    state: State | None = None
    candidates: tuple[str, ...] = ()
    confidence: float | None = None
    rotation: int | None = None
    evidence: tuple[Evidence, ...] = ()

    def to_dict(self) -> dict:
        """Export classification evidence, not OCR text or personal fields."""
        return asdict(self)


def normalize_text(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    unaccented = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(unaccented.upper().split())


def extract_evidence(lines: list[OCRLine], rotation: int = 0) -> list[Evidence]:
    """Require an issuer label; consume at most one immediately following line."""
    evidence = []
    pending: OCRLine | None = None
    for line in lines:
        normalized = normalize_text(line.text)
        if not normalized:
            pending = None
            continue
        for part in re.split(r"[;|]+", normalized):
            for segment in FIELD_BOUNDARY.split(part):
                segment = segment.strip(" :,-.")
                if not segment:
                    continue
                if OTHER_FIELD.match(segment):
                    pending = None
                    continue
                marker = ISSUER_START.match(segment)
                if not marker and pending is None:
                    continue
                context = segment if marker else f"{pending.text} {segment}"
                scores = [item for item in (
                    line.confidence, pending.confidence if pending and not marker else None,
                ) if item is not None]
                confidence = min(scores) if scores else None
                codes = {NAME_TO_UF[match.group()] for match in NAME_PATTERN.finditer(context)}
                codes.update(match.group() for match in CODE_PATTERN.finditer(context))
                evidence.extend(Evidence(code, confidence, rotation) for code in sorted(codes))
                # Only a bare issuer heading may continue onto the next OCR line.
                remainder = segment[marker.end():].strip(" :,-.") if marker else segment
                pending = OCRLine(segment, line.confidence) if marker and not codes and not remainder else None
    return evidence


def resolve_evidence(evidence: list[Evidence], min_confidence: float = DEFAULT_MIN_CONFIDENCE) -> Recognition:
    candidates = tuple(sorted({item.code for item in evidence}))
    details = tuple(evidence)
    if not candidates:
        return Recognition("unknown")
    if len(candidates) > 1:
        return Recognition("ambiguous", candidates=candidates, evidence=details)
    best = max(evidence, key=lambda item: item.confidence if item.confidence is not None else -1)
    if best.confidence is not None and best.confidence < min_confidence:
        status = "low_confidence"
    else:
        status = "recognized" if best.code in SUPPORTED else "unsupported"
    return Recognition(
        status, SUPPORTED.get(best.code) if status == "recognized" else None,
        candidates, best.confidence, best.rotation, details,
    )


def identify_issuer(text: str) -> Recognition:
    """Classify supplied text without claiming an OCR confidence score."""
    return resolve_evidence(extract_evidence([OCRLine(line) for line in text.splitlines()]))


def identify_state(text: str) -> State | None:
    """Compatibility helper: return a supported, unambiguous issuer or None."""
    return identify_issuer(text).state


def load_image(path: Path) -> np.ndarray:
    """Check encoded size and header dimensions before decoding JPEG/PNG pixels."""
    with path.open("rb") as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("A imagem excede o limite de 20 MiB.")
    try:
        with Image.open(io.BytesIO(data)) as source:
            width, height = source.size
            if source.format not in {"JPEG", "PNG"} or getattr(source, "n_frames", 1) != 1:
                raise ValueError("Use uma única imagem JPEG ou PNG, sem animação.")
            if min(width, height) < 32:
                raise ValueError("A imagem deve ter pelo menos 32 pixels em cada dimensão.")
            if width * height > MAX_PIXELS or max(width, height) > MAX_DIMENSION:
                raise ValueError("A imagem excede 20 megapixels ou 12000 pixels por dimensão.")
            corrected = ImageOps.exif_transpose(source)
            if corrected.mode in {"RGBA", "LA"} or "transparency" in corrected.info:
                rgba = corrected.convert("RGBA")
                corrected = Image.alpha_composite(Image.new("RGBA", rgba.size, "white"), rgba)
            rgb = np.asarray(corrected.convert("RGB"))
            return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    except (UnidentifiedImageError, Image.DecompressionBombError) as error:
        raise ValueError(f"Não foi possível ler a imagem: {path}") from error


def prepare_ocr_image(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    scale = min(2.0, OCR_MAX_SIDE / max(height, width))
    return cv2.resize(gray, (max(1, round(width * scale)), max(1, round(height * scale))),
                      interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)


def rotate_image(image: np.ndarray, degrees: int) -> np.ndarray:
    """Apply clockwise correction relative to the EXIF-corrected input."""
    if degrees == 0:
        return image
    rotations = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
    if degrees not in rotations:
        raise ValueError("A rotação deve ser 0, 90, 180 ou 270.")
    return cv2.rotate(image, rotations[degrees])


def ocr_lines(data: dict) -> list[OCRLine]:
    groups: dict[tuple, list[tuple[str, float]]] = {}
    for index, text in enumerate(data["text"]):
        if not text.strip():
            continue
        key = tuple(data[field][index] for field in ("page_num", "block_num", "par_num", "line_num"))
        score = float(data["conf"][index])
        groups.setdefault(key, []).append((text, score if math.isfinite(score) and score >= 0 else 0))
    lines = []
    previous = None
    for key, words in groups.items():
        if previous and (key[:3] != previous[:3] or key[3] != previous[3] + 1):
            lines.append(OCRLine(""))
        lines.append(OCRLine(" ".join(word for word, _ in words), min(score for _, score in words)))
        previous = key
    return lines


def validate_ocr_options(rotation: str, timeout: float, min_confidence: float) -> None:
    if not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise ValueError("O limite de OCR deve ser maior que 0 e no máximo 300 segundos.")
    if not math.isfinite(min_confidence) or not 0 <= min_confidence <= 100:
        raise ValueError("A confiança mínima deve estar entre 0 e 100.")
    if rotation not in {"auto", "0", "90", "180", "270"}:
        raise ValueError("Rotação inválida.")


def analyze_image(image: np.ndarray, *, rotation: str = "auto", timeout: float = DEFAULT_TIMEOUT,
                  min_confidence: float = DEFAULT_MIN_CONFIDENCE) -> Recognition:
    validate_ocr_options(rotation, timeout, min_confidence)
    angles = (0, 90, 180, 270) if rotation == "auto" else (int(rotation),)
    deadline = time.monotonic() + timeout
    evidence = []
    prepared = prepare_ocr_image(image)
    for angle in angles:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Tempo limite total de OCR excedido.")
        try:
            data = pytesseract.image_to_data(
                rotate_image(prepared, angle), lang="por", config="--psm 6",
                output_type=pytesseract.Output.DICT, timeout=remaining,
            )
        except RuntimeError as error:
            if "timeout" not in str(error).lower():
                raise
            raise TimeoutError("Tempo limite total de OCR excedido.") from error
        if time.monotonic() > deadline:
            raise TimeoutError("Tempo limite total de OCR excedido.")
        evidence.extend(extract_evidence(ocr_lines(data), angle))
    return resolve_evidence(evidence, min_confidence)


def parse_crop(value: str) -> tuple[float, float, float, float]:
    try:
        x0, y0, x1, y1 = (float(item) for item in value.split(","))
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise ValueError
    except ValueError as error:
        raise argparse.ArgumentTypeError("crop must be x0,y0,x1,y1 with 0 <= start < end <= 1") from error
    return x0, y0, x1, y1


def crop_document(image: np.ndarray, bounds: tuple[float, float, float, float] | None = None) -> np.ndarray:
    """Default to the whole oriented document; never guess a perforation region."""
    if bounds is None:
        return image
    x0, y0, x1, y1 = parse_crop(",".join(str(value) for value in bounds))
    height, width = image.shape[:2]
    crop = image[int(y0 * height):int(y1 * height), int(x0 * width):int(x1 * width)]
    if min(crop.shape[:2]) < 2:
        raise ValueError("O recorte deve ter pelo menos 2 pixels em cada dimensão.")
    return crop


def display_comparison(image: np.ndarray, state: State, bounds=None) -> None:
    crop = crop_document(image, bounds)
    reference = load_image(REFERENCE_DIRECTORY / f"{state.code}.jpg")
    try:
        cv2.imshow("Documento orientado - comparacao manual", crop)
        cv2.imshow(f"Referencia ilustrativa - {state.name}", reference)
        cv2.waitKey(0)
    finally:
        cv2.destroyAllWindows()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", nargs="?", type=Path, default=Path("di.jpg"),
                        help="single JPEG/PNG (default: di.jpg in the current directory)")
    parser.add_argument("--no-display", action="store_true", help="run without comparison windows")
    parser.add_argument("--json", action="store_true", help="emit JSON; implies --no-display")
    parser.add_argument("--tesseract-cmd", type=Path, help="path to the Tesseract executable")
    parser.add_argument("--rotation", choices=("auto", "0", "90", "180", "270"), default="auto",
                        help="clockwise correction after EXIF; auto checks all four angles")
    parser.add_argument("--ocr-timeout", type=float, default=DEFAULT_TIMEOUT,
                        help="total OCR budget in seconds (0 < value <= 300; default: 30)")
    parser.add_argument("--min-confidence", type=float, default=DEFAULT_MIN_CONFIDENCE,
                        help="minimum OCR line score, 0..100 (default: 60; not a probability)")
    parser.add_argument("--crop", type=parse_crop, help="comparison region x0,y0,x1,y1 in oriented image, 0..1")
    args = parser.parse_args(argv)
    if args.tesseract_cmd is not None:
        pytesseract.pytesseract.tesseract_cmd = str(args.tesseract_cmd)
    try:
        image = load_image(args.image)
        result = analyze_image(image, rotation=args.rotation, timeout=args.ocr_timeout,
                               min_confidence=args.min_confidence)
        if result.state and not (args.no_display or args.json):
            display_comparison(rotate_image(image, result.rotation), result.state, args.crop)
        if args.json:
            print(json.dumps(result.to_dict(), ensure_ascii=False))
        elif result.state:
            print(f"Estado emissor: {result.state.name} ({result.state.code})")
            print(result.state.description)
        else:
            messages = {
                "unknown": "Estado emissor não identificado: falta contexto de emissão legível.",
                "ambiguous": "Resultado ambíguo: há evidências de estados emissores diferentes.",
                "unsupported": "Estado emissor fora do escopo (SC, SP, PR ou RS).",
                "low_confidence": "Confiança do OCR insuficiente: revise a imagem.",
            }
            print(messages[result.status])
            if result.candidates:
                print("Candidatos: " + ", ".join(result.candidates))
        return 0 if result.status == "recognized" else 1
    except pytesseract.TesseractNotFoundError:
        message = "Tesseract não encontrado. Instale-o no PATH ou use --tesseract-cmd."
    except pytesseract.TesseractError as error:
        message = f"Erro no OCR. Verifique o idioma português (por) no Tesseract: {error}"
    except (OSError, ValueError, RuntimeError, cv2.error) as error:
        message = str(error)
    if args.json:
        print(json.dumps({"status": "error", "error": message}, ensure_ascii=False))
    else:
        print(f"Erro: {message}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
