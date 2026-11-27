"""Tests for parsed-artifact caching and response parity on a synthetic job."""

from __future__ import annotations

import hashlib
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from document_compare.phase2.agent.mock_reader import ScriptedPlanner
from document_compare.phase2.api import service
from document_compare.phase2.reader import result_reader as reader
from document_compare.phase2.viewer import artifact_cache
from document_compare.phase2.viewer.synthetic import seed_demo_job


class ArtifactCacheTests(unittest.TestCase):
    def setUp(self):
        artifact_cache.clear()
        self.addCleanup(artifact_cache.clear)
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root / "artifact.json"
        env = patch.dict(os.environ, {"CRR_ARTIFACT_CACHE_JOBS": "2"})
        env.start()
        self.addCleanup(env.stop)

    def test_hit_returns_same_object_for_resolved_alias(self):
        self.path.write_text('{"value": 1}', encoding="utf-8")
        alias = self.root / "alias.json"
        alias.symlink_to(self.path)
        value = artifact_cache.load_json(self.path)
        self.assertIs(artifact_cache.load_json(alias), value)
        self.assertIs(artifact_cache.load_json(self.path), value)

    def test_rewrite_invalidates_and_drops_previous_key(self):
        self.path.write_text('{"value": 1}', encoding="utf-8")
        original_stat = self.path.stat()
        first = artifact_cache.load_json(self.path)
        self.path.write_text('{"value": 2}', encoding="utf-8")
        os.utime(self.path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns + 1_000_000_000))
        second = artifact_cache.load_json(self.path)
        self.assertIsNot(second, first)
        self.assertEqual(second, {"value": 2})

        self.path.write_text('{"value": 3}', encoding="utf-8")
        os.utime(self.path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        self.assertEqual(artifact_cache.load_json(self.path), {"value": 3})

    def test_malformed_json_raises_and_is_not_cached(self):
        self.path.write_text('{"value": !}', encoding="utf-8")
        stat = self.path.stat()
        for _ in range(2):
            with self.assertRaises(json.JSONDecodeError):
                artifact_cache.load_json(self.path)
        self.path.write_text('{"value": 1}', encoding="utf-8")
        os.utime(self.path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertEqual(artifact_cache.load_json(self.path), {"value": 1})

    def test_missing_file_and_invalid_utf8_propagate(self):
        with self.assertRaises(FileNotFoundError):
            artifact_cache.load_json(self.path)
        self.path.write_bytes(b"\xff")
        with self.assertRaises(UnicodeDecodeError):
            artifact_cache.load_json(self.path)

    def test_evicts_least_recently_used_entry_at_capacity(self):
        with patch.dict(os.environ, {"CRR_ARTIFACT_CACHE_JOBS": "1"}):
            paths = [self.root / f"artifact-{i}.json" for i in range(7)]
            for path in paths:
                path.write_text("{}", encoding="utf-8")
            values = [artifact_cache.load_json(path) for path in paths[:6]]
            self.assertIs(artifact_cache.load_json(paths[0]), values[0])
            artifact_cache.load_json(paths[6])
            self.assertIs(artifact_cache.load_json(paths[0]), values[0])
            self.assertIsNot(artifact_cache.load_json(paths[1]), values[1])

    def test_zero_disables_cache_even_for_a_warm_entry(self):
        self.path.write_text("{}", encoding="utf-8")
        warm = artifact_cache.load_json(self.path)
        with patch.dict(os.environ, {"CRR_ARTIFACT_CACHE_JOBS": "0"}):
            first = artifact_cache.load_json(self.path)
            second = artifact_cache.load_json(self.path)
            self.assertIsNot(first, warm)
            self.assertIsNot(second, first)
            self.assertEqual(first, second)


class CachedArtifactEndpointTests(unittest.TestCase):
    def _responses(self, client, job_id, records):
        base = f"/jobs/{job_id}"
        responses = []

        def keep(response):
            self.assertEqual(response.status_code, 200, response.text if not response.is_success else "")
            responses.append((response.status_code, response.headers["content-type"], response.content))
            return response

        def get(path, **params):
            return keep(client.get(f"{base}/{path}", params=params))

        for path in ("report", "changes", "review-queue", "read/summary"):
            get(path)
        for expand in (False, True):
            get("view/outline", expand_blocks=expand)
            get("read/search", limit=reader.MAX_LIMIT, expand_blocks=expand)

        for kind, record_ids in records.items():
            search = get("read/search", kind=kind, limit=reader.MAX_LIMIT).json()
            self.assertTrue(search["returned"], kind)
            for record_id in record_ids:
                with self.subTest(kind=kind, record_id=record_id):
                    ref = {"kind": kind, "record_id": record_id}
                    if kind == "change":
                        get(f"changes/{record_id}")
                    get("read/details", **ref)
                    locations = get("view/locations", **ref).json()
                    for side, location in locations["sides"].items():
                        marker = location.get("insertion_marker") or {}
                        page = location["primary_page"] or marker.get("page_number") or 1
                        get(f"view/pages/{side}/{page}.png", **ref)
        for side in ("old", "new"):
            get(f"view/pages/{side}/1.png")

        planner = ScriptedPlanner(lambda observed: (
            {"action": "final", "answer": "Reviewed the synthetic summary."}
            if observed else {"action": "summarize", "arguments": {}}
        ))
        with patch.object(service, "_chat_client", return_value=planner):
            keep(client.post(f"{base}/chat", json={"question": "What changed?"}))
        return responses

    def test_view_and_read_endpoints_match_with_cache_enabled_and_disabled(self):
        artifact_cache.clear()
        self.addCleanup(artifact_cache.clear)
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        job_id = seed_demo_job(root, "scai_api_guide")
        output = root / job_id / "output"
        canonical_path = output / reader.CANONICAL_ARTIFACT
        canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
        modified = next(change for change in canonical["changes"] if change["change_type"] == "MODIFIED")
        added = next(change for change in canonical["changes"] if change["change_type"] == "ADDED")
        canonical["moves"] = [{
            "move_id": "MOVE-000001",
            **{f"{side}_element_ids": [modified[side]["element_id"]] for side in ("old", "new")},
            **{f"{side}_elements": [{"page": modified[side]["primary_page"], "text": modified[side]["text"]}]
               for side in ("old", "new")},
        }]
        canonical["change_blocks"] = [{
            "block_id": "BLOCK-000001", "side": "new", "change_type": "ADDED",
            "change_ids": [added["change_id"]], "new_element_ids": [added["new"]["element_id"]],
            "element_count": 1, "pages": [added["new"]["primary_page"]],
        }]
        canonical_path.write_text(json.dumps(canonical), encoding="utf-8")
        document_paths = [
            output / f"{side}_document{suffix}.json"
            for side in ("old", "new") for suffix in ("", "_structured")
        ]
        records = {
            kind: [record[reader.ID_FIELDS[kind]] for record in canonical[container]]
            for kind, container in (("change", "changes"), ("table", "table_changes"), ("review", "review_queue"),
                                    ("move", "moves"), ("block", "change_blocks"))
        }
        self.assertTrue(all(records.values()))

        with patch.object(service, "JOBS_ROOT", root), TestClient(service.app) as client:
            with patch.dict(os.environ, {"CRR_ARTIFACT_CACHE_JOBS": "2"}):
                cached_canonical = artifact_cache.load_json(canonical_path)
                cached_documents = {path: artifact_cache.load_json(path) for path in document_paths}

                def fingerprints():
                    return reader.snapshot_fingerprint(cached_canonical), {
                        path: hashlib.sha256(json.dumps(doc, sort_keys=True).encode("utf-8")).hexdigest()
                        for path, doc in cached_documents.items()
                    }

                before = fingerprints()
                enabled = self._responses(client, job_id, records)
                self.assertEqual(fingerprints(), before)
                self.assertIs(artifact_cache.load_json(canonical_path), cached_canonical)
                for path, doc in cached_documents.items():
                    self.assertIs(artifact_cache.load_json(path), doc)

            with patch.dict(os.environ, {"CRR_ARTIFACT_CACHE_JOBS": "0"}):
                disabled = self._responses(client, job_id, records)
        self.assertEqual(enabled, disabled)
