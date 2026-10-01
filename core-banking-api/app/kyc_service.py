"""Local document extraction and comparison, never identity verification.

OCR models are loaded only on the first image request. One worker bounds CPU use;
timed-out work retains the slot until it actually finishes, avoiding queued jobs.
"""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from io import BytesIO
from pathlib import Path
import re
import threading
import unicodedata
import warnings

from fastapi import HTTPException

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 12_000_000
MAX_TEXT_CHARS = 20_000
OCR_TIMEOUT_SECONDS = 25
MIN_OCR_CONFIDENCE = 0.80
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kyc-ocr")
_ocr_slot = threading.BoundedSemaphore(1)
_ocr_engine = None


def _run_ocr(image_bytes: bytes):
    global _ocr_engine
    from rapidocr_onnxruntime import RapidOCR

    if _ocr_engine is None:
        _ocr_engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
    results, _timings = _ocr_engine(image_bytes)
    return results or []


def _ocr(image_bytes: bytes):
    if not _ocr_slot.acquire(blocking=False):
        raise HTTPException(503, "The document processor is busy. Try again shortly.")
    try:
        future = _executor.submit(_run_ocr, image_bytes)
    except Exception:
        _ocr_slot.release()
        raise
    future.add_done_callback(lambda _future: _ocr_slot.release())
    try:
        return future.result(timeout=OCR_TIMEOUT_SECONDS)
    except FutureTimeout:
        raise HTTPException(504, "Document OCR timed out. Try a smaller, clearer image.")
    except ImportError:
        raise HTTPException(503, "Local OCR is unavailable. Install the project requirements.")
    except Exception:
        raise HTTPException(422, "The image could not be read by OCR. Try a clearer image.")


def read_document(filename: str, content: bytes):
    """Validate bytes before parsing; return text, extraction method and flags."""
    if not content:
        raise HTTPException(422, "The uploaded file is empty.")
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(413, "Document exceeds the 5 MB limit.")

    suffix = Path(filename or "").suffix.lower()
    if suffix == ".txt":
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise HTTPException(415, "Text samples must contain UTF-8 text.")
        if any(not character.isprintable() and character not in "\r\n\t" for character in text):
            raise HTTPException(415, "Text samples cannot contain binary or control characters.")
        if len(text) > MAX_TEXT_CHARS:
            raise HTTPException(413, "Text samples must contain at most 20,000 characters.")
        if not text.strip():
            raise HTTPException(422, "The document contains no readable text.")
        return text.strip(), "text_sample", []

    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(415, "Supported formats are JPEG, PNG, WEBP, and UTF-8 TXT samples.")
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError:
        raise HTTPException(503, "Image support is unavailable. Install the project requirements.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as source:
                if source.format not in {"JPEG", "PNG", "WEBP"}:
                    raise HTTPException(415, "The file contents are not a supported image format.")
                if source.width * source.height > MAX_IMAGE_PIXELS:
                    raise HTTPException(413, "Images must contain at most 12 million pixels.")
                source.verify()
            with Image.open(BytesIO(content)) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
                # Reduce inference cost without enlarging small images.
                image.thumbnail((2000, 2000))
                sanitized = BytesIO()
                image.save(sanitized, format="PNG")
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(413, "Images must contain at most 12 million pixels.")
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise HTTPException(415, "The uploaded file is not a valid, readable image.")

    results = _ocr(sanitized.getvalue())
    text = "\n".join(str(row[1]).strip() for row in results if str(row[1]).strip())
    if not text:
        raise HTTPException(422, "No readable text was found. Try a clearer, upright image.")
    if len(text) > MAX_TEXT_CHARS:
        raise HTTPException(422, "Too much text was detected. Upload only the document crop.")
    flags = []
    if any(float(row[2]) < MIN_OCR_CONFIDENCE for row in results):
        flags.append("OCR has low-confidence text; compare the original document manually.")
    return text, "local_ocr", flags


def _labelled_value(text: str, labels: str):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    # OCR often puts a label and its value on separate lines.
    pattern = re.compile(rf"^(?:{labels})(?:\s*[:\-]\s*|\s+|$)(.*)$", re.IGNORECASE)
    for index, line in enumerate(lines):
        match = pattern.match(line)
        if match:
            value = match.group(1).strip()
            if not value and index + 1 < len(lines):
                value = lines[index + 1]
            # Do not mistake the next field's label for a missing field's value.
            if re.match(r"^(?:full\s+name|name|document\s+type|cnic|id\s+number|passport(?:\s+number)?)\s*:", value, re.I):
                return None
            return value or None
    return None


def extract_fields(text: str):
    full_name = _labelled_value(text, r"full\s+name|holder(?:'s)?\s+name|name")
    if full_name and (len(full_name) > 160 or not any(c.isalpha() for c in full_name)):
        full_name = None
    id_number = _labelled_value(
        text, r"(?:national\s+)?identity\s+(?:number|no\.?)|id\s+(?:number|no\.?)|cnic(?:\s+(?:number|no\.?))?|passport\s+(?:number|no\.?)"
    )
    if not id_number:
        match = re.search(r"(?<!\d)\d{5}\s*[- ]\s*\d{7}\s*[- ]\s*\d(?!\d)", text)
        id_number = match.group(0) if match else None
    if id_number and (len(id_number) > 80 or not re.fullmatch(r"[\w .\-/]+", id_number, re.UNICODE)):
        id_number = None
    document_type = _labelled_value(text, r"document\s+type")
    if not document_type:
        if re.search(r"\bCNIC\b", text, re.I):
            document_type = "CNIC"
        elif re.search(r"\bpassport\b", text, re.I):
            document_type = "Passport"
    if document_type and len(document_type) > 80:
        document_type = None
    return {"full_name": full_name, "id_number": id_number, "document_type": document_type}


def _normalize_name(value: str):
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join("".join(c if c.isalnum() else " " for c in value).split())


def _normalize_id(value: str):
    return "".join(c for c in unicodedata.normalize("NFKC", value).casefold() if c.isalnum())


def analyze_document(account_id, owner_name, expected_id_number, filename, content):
    text, method, flags = read_document(filename, content)
    extracted = extract_fields(text)
    checks = []
    for field, expected, normalize in (
        ("full_name", owner_name, _normalize_name),
        ("id_number", expected_id_number, _normalize_id),
    ):
        actual = extracted[field]
        if not actual:
            status = "missing"
            flags.append(f"Could not extract {field}; manual review is required.")
        elif not expected or not normalize(expected):
            status = "not_provided"
            flags.append(f"No expected {field} was provided for comparison.")
        elif normalize(actual) != normalize(expected):
            status = "mismatch"
            flags.append(f"Extracted {field} does not match the supplied record.")
        else:
            status = "match"
        checks.append({"field": field, "status": status, "expected": expected, "actual": actual})
    if not extracted["document_type"]:
        flags.append("Document type was not detected; confirm it manually.")
    return {
        "account_id": account_id,
        "extracted": extracted,
        "checks": checks,
        "flags": flags,
        "recommendation": "review_required" if flags else "ready_for_review",
        "method": method,
        "raw_text": text,
    }
