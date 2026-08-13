#!/usr/bin/env python3
"""Create, publish, and verify the distinct Zenodo preregistration record.

The default command path only prepares a local request.  Commands that mutate
Zenodo require explicit production confirmation, and publication additionally
requires the exact record id and bundle SHA-256 on the command line.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
API = "https://zenodo.org/api"
BUNDLE_NAME = "loop-engineering-preregistration-v1.zip"
METADATA_NAME = "zenodo-deposition-metadata.json"
DOI_RE = re.compile(r"^10\.5281/zenodo\.\d+$")
RELATIONS = {
    "isSupplementTo": "issupplementto",
    "isDerivedFrom": "isderivedfrom",
}


class ZenodoError(RuntimeError):
    """A local contract or remote Zenodo response failed validation."""


def fail(message: str) -> None:
    raise ZenodoError(message)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def md5_bytes(payload: bytes) -> str:
    return hashlib.md5(payload, usedforsecurity=False).hexdigest()


def load_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ZenodoError(f"invalid JSON object: {path}") from exc
    if not isinstance(value, dict):
        fail(f"JSON root must be an object: {path}")
    return value


def parse_utc(value: object, field: str) -> dt.datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        fail(f"{field} must be a UTC timestamp ending in Z")
    try:
        parsed = dt.datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise ZenodoError(f"{field} is not a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo != dt.timezone.utc:
        fail(f"{field} must be UTC")
    return parsed


def write_new_json(path: Path, value: dict) -> None:
    if path.exists():
        fail(f"refusing to overwrite existing evidence: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def publication_date(value: str) -> str:
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("publication date must be YYYY-MM-DD") from exc
    return parsed.isoformat()


def creator(value: dict) -> dict:
    name = value.get("name")
    if not isinstance(name, str) or name.count(",") != 1:
        fail("creator name must have the form 'Family, Given'")
    family, given = [part.strip() for part in name.split(",", 1)]
    if not family or not given:
        fail("creator family and given names must be non-empty")
    person = {
        "type": "personal",
        "family_name": family,
        "given_name": given,
    }
    orcid = value.get("orcid")
    if orcid:
        person["identifiers"] = [{"scheme": "orcid", "identifier": orcid}]
    result = {"person_or_org": person}
    affiliation = value.get("affiliation")
    if affiliation:
        result["affiliations"] = [{"name": affiliation}]
    return result


def identifier_scheme(identifier: str) -> str:
    if identifier.startswith("10."):
        return "doi"
    if identifier.startswith(("https://", "http://")):
        return "url"
    fail(f"unsupported related identifier: {identifier}")


def invenio_payload(metadata: dict, date: str) -> dict:
    if (
        metadata.get("upload_type") != "software"
        or metadata.get("access_right") != "open"
        or metadata.get("version") != "prereg-v1"
    ):
        fail("preregistration metadata must be open software version prereg-v1")
    relations = []
    for item in metadata.get("related_identifiers", []):
        relation = RELATIONS.get(item.get("relation"))
        identifier = item.get("identifier")
        if not relation or not isinstance(identifier, str):
            fail("unsupported related identifier relation")
        relations.append(
            {
                "identifier": identifier,
                "scheme": identifier_scheme(identifier),
                "relation_type": {"id": relation},
            }
        )
    record_metadata = {
        "resource_type": {"id": "software"},
        "title": metadata["title"],
        "publication_date": date,
        "creators": [creator(item) for item in metadata["creators"]],
        "description": metadata["description"],
        "publisher": "Zenodo",
        "rights": [{"id": metadata["license"]}],
        "languages": [{"id": metadata["language"]}],
        "subjects": [{"subject": item} for item in metadata.get("keywords", [])],
        "related_identifiers": relations,
        "version": metadata["version"],
    }
    return {
        "access": {"record": "public", "files": "public"},
        "files": {"enabled": True},
        "metadata": record_metadata,
    }


def prepare_request(bundle_dir: Path, date: str) -> dict:
    bundle = bundle_dir / BUNDLE_NAME
    metadata_path = bundle_dir / METADATA_NAME
    if not bundle.is_file() or not metadata_path.is_file():
        fail("bundle directory lacks the preregistration ZIP or Zenodo metadata")
    payload = bundle.read_bytes()
    metadata = load_object(metadata_path)
    return {
        "schema_version": 1,
        "record_role": "pre-outcome preregistration timestamp",
        "api": API,
        "bundle": {
            "name": BUNDLE_NAME,
            "bytes": len(payload),
            "sha256": sha256_bytes(payload),
            "md5": md5_bytes(payload),
        },
        "record_payload": invenio_payload(metadata, date),
    }


def token() -> str:
    value = os.environ.get("ZENODO_TOKEN")
    if not value:
        fail("ZENODO_TOKEN is required for this authenticated operation")
    return value


def call_json(
    method: str,
    url: str,
    bearer: str | None = None,
    data: object | None = None,
    raw: bytes | None = None,
) -> dict:
    body = raw
    content_type = "application/octet-stream"
    if raw is None and data is not None:
        body = json.dumps(data).encode("utf-8")
        content_type = "application/json"
    request = urllib.request.Request(url, data=body, method=method)
    if bearer:
        request.add_header("Authorization", f"Bearer {bearer}")
    if body is not None:
        request.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(request) as response:
            response_body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:800]
        raise ZenodoError(f"Zenodo {method} {url} failed: HTTP {exc.code}: {detail}") from exc
    if not response_body:
        return {}
    try:
        value = json.loads(response_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ZenodoError(f"Zenodo returned invalid JSON for {method} {url}") from exc
    if not isinstance(value, dict):
        fail(f"Zenodo returned a non-object for {method} {url}")
    return value


def call_bytes(url: str, bearer: str | None = None) -> bytes:
    request = urllib.request.Request(url, method="GET")
    if bearer:
        request.add_header("Authorization", f"Bearer {bearer}")
    try:
        with urllib.request.urlopen(request) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise ZenodoError(f"Zenodo GET {url} failed: HTTP {exc.code}") from exc


def request_bundle(request: dict, bundle: Path) -> bytes:
    if request.get("schema_version") != 1:
        fail("Zenodo request must use schema 1")
    expected = request.get("bundle")
    payload = bundle.read_bytes()
    if not isinstance(expected, dict) or (
        bundle.name != expected.get("name")
        or len(payload) != expected.get("bytes")
        or sha256_bytes(payload) != expected.get("sha256")
        or md5_bytes(payload) != expected.get("md5")
    ):
        fail("bundle does not match the prepared Zenodo request")
    return payload


def extract_doi(record: dict) -> str:
    candidates = [
        record.get("doi"),
        (record.get("pids", {}).get("doi", {}) or {}).get("identifier"),
    ]
    for value in candidates:
        if isinstance(value, str) and DOI_RE.fullmatch(value):
            return value
    fail("Zenodo response lacks the reserved version DOI")


def file_entry(record: dict, name: str) -> dict:
    entries = record.get("entries")
    if entries is None:
        entries = record.get("files", {}).get("entries")
    matches = [item for item in entries or [] if item.get("key") == name]
    if len(matches) != 1:
        fail("Zenodo record must contain exactly the prepared single ZIP")
    return matches[0]


def verify_file_metadata(entry: dict, request: dict) -> None:
    expected = request["bundle"]
    checksum = entry.get("checksum")
    if (
        entry.get("status") not in (None, "completed")
        or int(entry.get("size", -1)) != expected["bytes"]
        or checksum != f"md5:{expected['md5']}"
    ):
        fail("Zenodo file metadata does not match the prepared ZIP")


def create_draft(request: dict, bundle: Path) -> dict:
    payload = request_bundle(request, bundle)
    bearer = token()
    record = call_json("POST", f"{API}/records", bearer, request["record_payload"])
    record_id = record.get("id")
    if not isinstance(record_id, str) or not record_id:
        fail("Zenodo did not return a draft record id")
    try:
        reserved = call_json(
            "POST", f"{API}/records/{record_id}/draft/pids/doi", bearer, {}
        )
        doi = extract_doi(reserved)
        files_url = f"{API}/records/{record_id}/draft/files"
        call_json("POST", files_url, bearer, [{"key": BUNDLE_NAME}])
        quoted = urllib.parse.quote(BUNDLE_NAME, safe="")
        file_url = f"{files_url}/{quoted}"
        call_json("PUT", f"{file_url}/content", bearer, raw=payload)
        committed = call_json("POST", f"{file_url}/commit", bearer, {})
        verify_file_metadata(committed, request)
        draft = call_json("GET", f"{API}/records/{record_id}/draft", bearer)
        if extract_doi(draft) != doi:
            fail("reserved DOI changed after upload")
    except Exception as exc:
        raise ZenodoError(
            f"draft {record_id} was created but setup did not finish: {exc}"
        ) from exc
    return {
        "schema_version": 1,
        "status": "draft-uploaded-doi-reserved-not-published",
        "record_id": record_id,
        "reserved_doi": doi,
        "draft_url": f"https://zenodo.org/uploads/{record_id}",
        "bundle": request["bundle"],
        "created_utc": utc_now(),
    }


def validate_receipt(request: dict, receipt: dict, bundle: Path) -> tuple[str, str]:
    request_bundle(request, bundle)
    if receipt.get("schema_version") != 1 or receipt.get("bundle") != request["bundle"]:
        fail("draft receipt does not match the prepared request")
    record_id = receipt.get("record_id")
    doi = receipt.get("reserved_doi")
    if not isinstance(record_id, str) or not record_id or not isinstance(doi, str):
        fail("draft receipt lacks record identity")
    if not DOI_RE.fullmatch(doi):
        fail("draft receipt DOI is invalid")
    return record_id, doi


def validate_publication_evidence(
    evidence_path: Path,
    bundle_path: Path,
    measurement_manifest: dict,
) -> dict:
    """Bind a public Zenodo verification to the exact frozen measurement contract."""
    evidence_payload = evidence_path.read_bytes()
    try:
        evidence = json.loads(evidence_payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ZenodoError("public preregistration evidence is invalid JSON") from exc
    if not isinstance(evidence, dict):
        fail("public preregistration evidence must be a JSON object")
    if (
        evidence.get("schema_version") != 1
        or evidence.get("status") != "public-preregistration-verified"
        or evidence.get("record_role") != "pre-outcome preregistration timestamp"
    ):
        fail("public preregistration evidence has the wrong contract")
    record_id = evidence.get("record_id")
    doi = evidence.get("doi")
    if not isinstance(record_id, str) or not record_id:
        fail("public preregistration evidence lacks a record id")
    if not isinstance(doi, str) or not DOI_RE.fullmatch(doi):
        fail("public preregistration evidence lacks a valid Zenodo DOI")
    if evidence.get("record_url") != f"https://zenodo.org/records/{record_id}":
        fail("public preregistration record URL does not match its id")
    if evidence.get("doi_url") != f"https://doi.org/{doi}":
        fail("public preregistration DOI URL does not match its DOI")
    related_dois = {
        item.get("identifier")
        for item in json.loads((ROOT / ".zenodo-prereg.json").read_text(encoding="utf-8"))[
            "related_identifiers"
        ]
        if isinstance(item, dict) and str(item.get("identifier", "")).startswith("10.")
    }
    if doi in related_dois:
        fail("public preregistration DOI must differ from the existing preprint DOI")
    verified = parse_utc(
        evidence.get("public_verification_utc"), "public_verification_utc"
    )
    if verified > dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5):
        fail("public preregistration verification timestamp is in the future")

    bundle_payload = bundle_path.read_bytes()
    bundle = evidence.get("bundle")
    if not isinstance(bundle, dict) or (
        bundle_path.name != bundle.get("name")
        or len(bundle_payload) != bundle.get("bytes")
        or sha256_bytes(bundle_payload) != bundle.get("sha256")
        or md5_bytes(bundle_payload) != bundle.get("md5")
    ):
        fail("public preregistration evidence does not match the local ZIP")
    member = (
        "loop-engineering-preregistration-v1/"
        "preregistration-bundle-manifest.json"
    )
    try:
        with zipfile.ZipFile(bundle_path) as archive:
            names = archive.namelist()
            if names.count(member) != 1:
                fail("preregistration ZIP lacks one bundle manifest")
            frozen = json.loads(archive.read(member))
    except (OSError, KeyError, UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise ZenodoError("preregistration ZIP is invalid") from exc
    if not isinstance(frozen, dict) or frozen.get("schema_version") != 1:
        fail("preregistration ZIP manifest must use schema 1")
    expected_manifest_digest = hashlib.sha256(
        json.dumps(
            measurement_manifest, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    if (
        frozen.get("preregistration_commit")
        != measurement_manifest.get("preregistration_commit")
        or frozen.get("measurement_manifest_digest") != expected_manifest_digest
    ):
        fail("public preregistration ZIP does not bind the measurement manifest")
    return {
        "external_preregistration_doi": doi,
        "external_preregistration_record_id": record_id,
        "external_preregistration_evidence_sha256": sha256_bytes(evidence_payload),
        "external_preregistration_bundle_sha256": bundle["sha256"],
        "external_preregistration_verified_utc": evidence["public_verification_utc"],
    }


def confirm_publication(
    request: dict,
    receipt: dict,
    bundle: Path,
    confirmed_record_id: str,
    confirmed_sha256: str,
) -> None:
    record_id, _ = validate_receipt(request, receipt, bundle)
    if confirmed_record_id != record_id:
        fail("publication confirmation record id does not match")
    if confirmed_sha256 != request["bundle"]["sha256"]:
        fail("publication confirmation SHA-256 does not match")


def verify_remote_file(record_id: str, request: dict, bearer: str | None, draft: bool) -> None:
    suffix = "/draft/files" if draft else "/files"
    listing = call_json("GET", f"{API}/records/{record_id}{suffix}", bearer)
    entry = file_entry(listing, BUNDLE_NAME)
    verify_file_metadata(entry, request)
    content_url = entry.get("links", {}).get("content")
    if not isinstance(content_url, str):
        quoted = urllib.parse.quote(BUNDLE_NAME, safe="")
        content_url = f"{API}/records/{record_id}{suffix}/{quoted}/content"
    if content_url.startswith("/"):
        content_url = "https://zenodo.org" + content_url
    if sha256_bytes(call_bytes(content_url, bearer)) != request["bundle"]["sha256"]:
        fail("downloaded Zenodo ZIP does not match the prepared SHA-256")


def publish_record(request: dict, receipt: dict, bundle: Path) -> dict:
    record_id, expected_doi = validate_receipt(request, receipt, bundle)
    bearer = token()
    draft = call_json("GET", f"{API}/records/{record_id}/draft", bearer)
    if extract_doi(draft) != expected_doi:
        fail("draft DOI does not match the local receipt")
    verify_remote_file(record_id, request, bearer, draft=True)
    started = utc_now()
    call_json(
        "POST", f"{API}/records/{record_id}/draft/actions/publish", bearer, {}
    )
    completed = utc_now()
    return verify_public(request, receipt, bundle, started, completed)


def verify_public(
    request: dict,
    receipt: dict,
    bundle: Path,
    publish_started_utc: str | None = None,
    publish_completed_utc: str | None = None,
) -> dict:
    record_id, expected_doi = validate_receipt(request, receipt, bundle)
    record = call_json("GET", f"{API}/records/{record_id}")
    if extract_doi(record) != expected_doi:
        fail("public record DOI does not match the reserved DOI")
    if record.get("is_published") is not True and record.get("status") != "published":
        fail("Zenodo record is not publicly published")
    verify_remote_file(record_id, request, None, draft=False)
    return {
        "schema_version": 1,
        "status": "public-preregistration-verified",
        "record_role": request["record_role"],
        "record_id": record_id,
        "doi": expected_doi,
        "record_url": f"https://zenodo.org/records/{record_id}",
        "doi_url": f"https://doi.org/{expected_doi}",
        "record_created_utc": record.get("created"),
        "record_updated_utc": record.get("updated"),
        "publish_request_started_utc": publish_started_utc,
        "publish_request_completed_utc": publish_completed_utc,
        "public_verification_utc": utc_now(),
        "bundle": request["bundle"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--bundle-dir", type=Path, required=True)
    prepare.add_argument("--publication-date", type=publication_date, required=True)
    prepare.add_argument("--output", type=Path, required=True)

    create = subparsers.add_parser("create-draft")
    create.add_argument("--request", type=Path, required=True)
    create.add_argument("--bundle", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--confirm-production", choices=["zenodo.org"], required=True)

    publish = subparsers.add_parser("publish")
    publish.add_argument("--request", type=Path, required=True)
    publish.add_argument("--receipt", type=Path, required=True)
    publish.add_argument("--bundle", type=Path, required=True)
    publish.add_argument("--output", type=Path, required=True)
    publish.add_argument("--confirm-record-id", required=True)
    publish.add_argument("--confirm-bundle-sha256", required=True)

    verify = subparsers.add_parser("verify-public")
    verify.add_argument("--request", type=Path, required=True)
    verify.add_argument("--receipt", type=Path, required=True)
    verify.add_argument("--bundle", type=Path, required=True)
    verify.add_argument("--output", type=Path, required=True)

    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare_request(args.bundle_dir, args.publication_date)
        elif args.command == "create-draft":
            result = create_draft(load_object(args.request), args.bundle)
        elif args.command == "publish":
            request = load_object(args.request)
            receipt = load_object(args.receipt)
            confirm_publication(
                request,
                receipt,
                args.bundle,
                args.confirm_record_id,
                args.confirm_bundle_sha256,
            )
            result = publish_record(request, receipt, args.bundle)
        else:
            result = verify_public(
                load_object(args.request), load_object(args.receipt), args.bundle
            )
        write_new_json(args.output, result)
    except (KeyError, OSError, TypeError, ZenodoError) as exc:
        print(f"Zenodo preregistration failed: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
