"""Document processing uses a temporary database and synthetic identity fields."""
from concurrent.futures import Future
from io import BytesIO
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app import kyc_service
from tests.support import BankingTestCase


SAMPLE = b"SYNTHETIC DEMO - NOT VALID\nDocument type: CNIC\nFull Name: Ayesha Khan\nID Number: 42101-1234567-1"


class DocumentTests(BankingTestCase):
    def setUp(self):
        super().setUp()
        registered = self.client.post('/auth/register', json={'full_name': 'Ayesha Khan', 'email': 'ayesha@example.com', 'password': 'TestPassword!2026'})
        self.assertEqual(registered.status_code, 201, registered.text)
        response = self.client.post("/accounts/", json={"owner_name": "Ayesha Khan", "email": "ayesha@example.com"})
        self.assertEqual(response.status_code, 201, response.text)
        self.account_id = response.json()["id"]

    def upload(self, content=SAMPLE, filename="demo.txt", expected="42101-1234567-1", account_id=None):
        data = {} if expected is None else {"expected_id_number": expected}
        return self.client.post(
            f"/kyc/{account_id or self.account_id}/analyze-document",
            files={"file": (filename, content, "application/octet-stream")},
            data=data,
        )

    def test_matching_text_is_ready_for_human_review_without_creating_kyc(self):
        response = self.upload()
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["recommendation"], "ready_for_review")
        self.assertEqual(result["method"], "text_sample")
        self.assertEqual([check["status"] for check in result["checks"]], ["match", "match"])
        self.assertEqual(result["extracted"]["full_name"], "Ayesha Khan")
        self.assertEqual(self.client.get(f"/kyc/{self.account_id}").status_code, 404)

    def test_id_and_name_mismatch_are_flagged(self):
        response = self.upload(SAMPLE.replace(b"Ayesha Khan", b"Sara Ali").replace(b"1234567", b"7654321"))
        result = response.json()
        self.assertEqual(result["recommendation"], "review_required")
        self.assertEqual([check["status"] for check in result["checks"]], ["mismatch", "mismatch"])

    def test_id_normalizes_hyphens_and_name_normalizes_case_spacing(self):
        result = self.upload(SAMPLE.replace(b"Ayesha Khan", b"AYESHA   KHAN"), expected="4210112345671").json()
        self.assertEqual([check["status"] for check in result["checks"]], ["match", "match"])

    def test_missing_expected_id_is_explicit(self):
        result = self.upload(expected=None).json()
        self.assertEqual(result["checks"][1]["status"], "not_provided")
        self.assertEqual(result["recommendation"], "review_required")

    def test_existing_record_supplies_expected_id_and_is_not_modified(self):
        record = self.client.post(f"/kyc/{self.account_id}", json={
            "full_name": "Ayesha Khan", "id_number": "42101-1234567-1", "document_type": "CNIC"
        }).json()
        result = self.upload(expected=None).json()
        self.assertEqual(result["checks"][1]["status"], "match")
        self.assertEqual(self.client.get(f"/kyc/{self.account_id}").json(), record)
        self.assertEqual(record["status"], "pending")

    def test_explicit_expected_id_overrides_existing_record(self):
        self.client.post(f"/kyc/{self.account_id}", json={
            "full_name": "Ayesha Khan", "id_number": "OTHER123", "document_type": "CNIC"
        })
        self.assertEqual(self.upload().json()["checks"][1]["status"], "match")

    def test_missing_fields_are_not_invented(self):
        result = self.upload(b"SYNTHETIC DEMO - NOT VALID\nA document with no identifiable fields.").json()
        self.assertEqual(result["extracted"], {"full_name": None, "id_number": None, "document_type": None})
        self.assertEqual([check["status"] for check in result["checks"]], ["missing", "missing"])
        self.assertEqual(result["recommendation"], "review_required")

    def test_unknown_account_returns_404_before_parsing(self):
        self.assertEqual(self.upload(b"bad image", "demo.png", account_id="missing").status_code, 404)

    def test_file_validation(self):
        cases = (
            (b"", "demo.txt", 422),
            (b"\x00binary", "demo.txt", 415),
            (b"\xff\xfe", "demo.txt", 415),
            (b"   \n", "demo.txt", 422),
            (SAMPLE, "demo.pdf", 415),
            (SAMPLE, "demo.png", 415),
            (b"x" * (kyc_service.MAX_FILE_BYTES + 1), "demo.txt", 413),
            (b"x" * (kyc_service.MAX_TEXT_CHARS + 1), "demo.txt", 413),
        )
        for content, filename, status in cases:
            with self.subTest(filename=filename, status=status, size=len(content)):
                response = self.upload(content, filename)
                self.assertEqual(response.status_code, status, response.text)

    def test_oversized_pixel_count_is_rejected_before_ocr(self):
        from PIL import Image
        source = BytesIO()
        Image.new("RGB", (4000, 3001), "white").save(source, format="PNG")
        with patch("app.kyc_service._ocr") as ocr:
            response = self.upload(source.getvalue(), "large.png")
        self.assertEqual(response.status_code, 413)
        ocr.assert_not_called()

    def test_actual_image_validation_then_ocr_and_low_confidence(self):
        from PIL import Image
        source = BytesIO()
        Image.new("RGB", (100, 100), "white").save(source, format="PNG")
        rows = [[[], line, 0.99] for line in SAMPLE.decode().splitlines()]
        rows[-1][2] = 0.65
        with patch("app.kyc_service._ocr", return_value=rows) as ocr:
            response = self.upload(source.getvalue(), "demo.png")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["method"], "local_ocr")
        self.assertEqual(response.json()["checks"][1]["status"], "match")
        self.assertEqual(response.json()["recommendation"], "review_required")
        self.assertIn("low-confidence", " ".join(response.json()["flags"]))
        ocr.assert_called_once()

    def test_blank_image_has_clear_error(self):
        from PIL import Image
        source = BytesIO()
        Image.new("RGB", (20, 20), "white").save(source, format="PNG")
        with patch("app.kyc_service._ocr", return_value=[]):
            response = self.upload(source.getvalue(), "demo.png")
        self.assertEqual(response.status_code, 422)

    def test_real_local_ocr_on_synthetic_image(self):
        sample = Path(__file__).resolve().parents[1] / "samples" / "demo-id.png"
        response = self.upload(sample.read_bytes(), sample.name)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["method"], "local_ocr")
        self.assertEqual(result["extracted"]["full_name"], "Ayesha Khan")
        self.assertEqual([check["status"] for check in result["checks"]], ["match", "match"])


class ExtractionTests(unittest.TestCase):
    def test_split_labels_passport_and_unicode_name(self):
        result = kyc_service.extract_fields("Full Name:\nZoe O'Neil\nPassport Number:\nAB123456\nDocument type: Passport")
        self.assertEqual(result, {"full_name": "Zoe O'Neil", "id_number": "AB123456", "document_type": "Passport"})

    def test_empty_name_does_not_swallow_next_field(self):
        result = kyc_service.extract_fields("Full Name:\nID Number: 42101-1234567-1")
        self.assertIsNone(result["full_name"])

    def test_ocr_timeout_does_not_release_worker_until_done(self):
        future = Future()
        with patch("app.kyc_service._executor.submit", return_value=future), patch("app.kyc_service.OCR_TIMEOUT_SECONDS", 0):
            with self.assertRaises(HTTPException) as timed_out:
                kyc_service._ocr(b"image")
            self.assertEqual(timed_out.exception.status_code, 504)
            with self.assertRaises(HTTPException) as busy:
                kyc_service._ocr(b"image")
            self.assertEqual(busy.exception.status_code, 503)
            future.set_result([])
        self.assertTrue(kyc_service._ocr_slot.acquire(blocking=False))
        kyc_service._ocr_slot.release()
