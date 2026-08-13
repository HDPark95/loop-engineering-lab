#!/usr/bin/env python3
"""Tests for the fail-closed Zenodo preregistration workflow."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import zenodo_preregistration as zenodo


METADATA = {
    "access_right": "open",
    "creators": [
        {
            "affiliation": "Independent Researcher",
            "name": "Park, Hyundoo",
            "orcid": "0009-0005-4870-8803",
        }
    ],
    "description": "Frozen preregistration.",
    "keywords": ["preregistration"],
    "language": "eng",
    "license": "mit",
    "related_identifiers": [
        {
            "identifier": "10.5281/zenodo.21594735",
            "relation": "isSupplementTo",
        },
        {
            "identifier": "https://github.com/HDPark95/loop-engineering-lab",
            "relation": "isDerivedFrom",
        },
    ],
    "title": "LOOP Frozen Preregistration",
    "upload_type": "software",
    "version": "prereg-v1",
}


class ZenodoPreregistrationTest(unittest.TestCase):
    def make_bundle(self, root: Path) -> tuple[Path, bytes]:
        bundle_dir = root / "release"
        bundle_dir.mkdir()
        payload = b"one immutable zip"
        (bundle_dir / zenodo.BUNDLE_NAME).write_bytes(payload)
        (bundle_dir / zenodo.METADATA_NAME).write_text(
            json.dumps(METADATA), encoding="utf-8"
        )
        return bundle_dir, payload

    def test_prepare_converts_metadata_and_binds_both_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle_dir, payload = self.make_bundle(root)
            request = zenodo.prepare_request(bundle_dir, "2026-08-15")
            self.assertEqual(request["bundle"]["sha256"], zenodo.sha256_bytes(payload))
            self.assertEqual(request["bundle"]["md5"], zenodo.md5_bytes(payload))
            metadata = request["record_payload"]["metadata"]
            self.assertEqual(metadata["resource_type"], {"id": "software"})
            self.assertEqual(metadata["publication_date"], "2026-08-15")
            self.assertEqual(
                metadata["creators"][0]["person_or_org"]["family_name"], "Park"
            )
            self.assertEqual(
                [item["relation_type"]["id"] for item in metadata["related_identifiers"]],
                ["issupplementto", "isderivedfrom"],
            )

    def test_changed_bundle_is_rejected_before_network_use(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle_dir, _ = self.make_bundle(root)
            request = zenodo.prepare_request(bundle_dir, "2026-08-15")
            bundle = bundle_dir / zenodo.BUNDLE_NAME
            bundle.write_bytes(b"changed")
            with self.assertRaisesRegex(zenodo.ZenodoError, "does not match"):
                zenodo.request_bundle(request, bundle)

    def test_create_draft_reserves_doi_and_uploads_only_the_zip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle_dir, payload = self.make_bundle(root)
            request = zenodo.prepare_request(bundle_dir, "2026-08-15")
            bundle = bundle_dir / zenodo.BUNDLE_NAME
            responses = [
                {"id": "12345678"},
                {"pids": {"doi": {"identifier": "10.5281/zenodo.12345678"}}},
                {},
                {},
                {
                    "key": zenodo.BUNDLE_NAME,
                    "size": len(payload),
                    "checksum": f"md5:{zenodo.md5_bytes(payload)}",
                    "status": "completed",
                },
                {"pids": {"doi": {"identifier": "10.5281/zenodo.12345678"}}},
            ]
            with mock.patch.object(zenodo, "token", return_value="secret"), mock.patch.object(
                zenodo, "call_json", side_effect=responses
            ) as call:
                receipt = zenodo.create_draft(request, bundle)
            self.assertEqual(receipt["reserved_doi"], "10.5281/zenodo.12345678")
            self.assertEqual(call.call_args_list[2].args[3], [{"key": zenodo.BUNDLE_NAME}])
            self.assertEqual(call.call_args_list[3].kwargs["raw"], payload)

    def test_public_verification_redownloads_and_checks_exact_sha256(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle_dir, payload = self.make_bundle(root)
            request = zenodo.prepare_request(bundle_dir, "2026-08-15")
            receipt = {
                "schema_version": 1,
                "record_id": "12345678",
                "reserved_doi": "10.5281/zenodo.12345678",
                "bundle": request["bundle"],
            }
            record = {
                "is_published": True,
                "created": "2026-08-15T09:00:00Z",
                "updated": "2026-08-15T09:01:00Z",
                "pids": {"doi": {"identifier": receipt["reserved_doi"]}},
            }
            listing = {
                "entries": [
                    {
                        "key": zenodo.BUNDLE_NAME,
                        "size": len(payload),
                        "checksum": f"md5:{zenodo.md5_bytes(payload)}",
                        "status": "completed",
                        "links": {"content": "/api/records/123/files/bundle/content"},
                    }
                ]
            }
            with mock.patch.object(zenodo, "call_json", side_effect=[record, listing]), mock.patch.object(
                zenodo, "call_bytes", return_value=payload
            ):
                evidence = zenodo.verify_public(request, receipt, bundle_dir / zenodo.BUNDLE_NAME)
            self.assertEqual(evidence["status"], "public-preregistration-verified")
            self.assertEqual(evidence["bundle"]["sha256"], zenodo.sha256_bytes(payload))

    def test_publication_confirmation_requires_exact_id_and_sha(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle_dir, _ = self.make_bundle(root)
            request = zenodo.prepare_request(bundle_dir, "2026-08-15")
            receipt = {
                "schema_version": 1,
                "record_id": "12345678",
                "reserved_doi": "10.5281/zenodo.12345678",
                "bundle": request["bundle"],
            }
            bundle = bundle_dir / zenodo.BUNDLE_NAME
            with self.assertRaisesRegex(zenodo.ZenodoError, "record id"):
                zenodo.confirm_publication(
                    request, receipt, bundle, "wrong", request["bundle"]["sha256"]
                )
            with self.assertRaisesRegex(zenodo.ZenodoError, "SHA-256"):
                zenodo.confirm_publication(
                    request, receipt, bundle, receipt["record_id"], "wrong"
                )
            zenodo.confirm_publication(
                request,
                receipt,
                bundle,
                receipt["record_id"],
                request["bundle"]["sha256"],
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
