"""Stage-09 (s09_movement) module-safety and structural split/merge ownership.

Three properties are pinned here, following the s07/s08/s10 module-safety
pattern:

1. Importing ``s09_movement`` must have **no** side effects: it must not
   resolve or create a workspace root, read or write pipeline files, print,
   or mutate the environment.
2. Exact structural split/merge discovery must not be pre-empted by the
   generic one-to-one residual reconciliation.  Before the fix, a deleted
   structural element ``"Alpha beta gamma"`` next to added structural
   ``"Alpha beta"`` + ``"gamma"`` was consumed as a single
   ``RESEGMENTATION_REVIEW`` pair (old -> first new), so the exact ``1 -> 2``
   relationship was never reported and one element was silently reclassified.
   The reconciler is a cosmetic-noise suppressor; it must not own structural
   residuals, whose split/merge evidence is review-only and never consumes.
3. Structural review stays evidence-only and exactly-once: every OLD/NEW
   element stays accounted, a changed numeric boundary (21 -> 99) stays a
   genuine change instead of a near-twin equivalence, repeated segments surface
   all matches instead of a first winner, and upstream review reasons survive.

Every behavioral test runs the real ``s09_movement`` entry point in an
isolated temporary root (no mocks of the production code) and inspects the
saved ``comparison_candidates_with_movement.json``.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
S09 = ROOT / "crr_compare" / "stages" / "s09_movement.py"
PYTHON = sys.executable

INPUT_STAGE = "comparison_candidate_assembly_and_multi_representation_token_diff"
OUTPUT_STAGE = "movement_and_structural_reconciliation_evidence"


# ---------------------------------------------------------------------------
# Synthetic Step-8 candidate input construction
# ---------------------------------------------------------------------------
def _env(root: Path | None = None) -> dict[str, str]:
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "CRR_RUN_LLM": "mock",
        "CRR_DOMAIN_MODE": "generic",
        "PYTHONPATH": str(ROOT),
    }
    if root is not None:
        env["CRR_PROJECT_ROOT"] = str(root)
    return env


def _element(
    element_id: str,
    element_type: str,
    text: str,
    reading_order: int,
    *,
    page: int = 1,
) -> dict:
    return {
        "element_id": element_id,
        "element_type": element_type,
        "text": text,
        "normalized_text": text,
        "comparison_text": text,
        "original_role": None,
        "primary_page": page,
        "reading_order": reading_order,
    }


def _deleted(candidate_id: str, element: dict, reasons: list[str]) -> dict:
    return {
        "candidate_id": candidate_id,
        "candidate_type": "DELETED_CANDIDATE",
        "source_unmatched_reason": "no_exact_counterpart",
        "old": element,
        "new": None,
        "needs_review": False,
        "review_reasons": list(reasons),
        "movement_classification": None,
        "token_diff": None,
    }


def _added(candidate_id: str, element: dict, reasons: list[str]) -> dict:
    return {
        "candidate_id": candidate_id,
        "candidate_type": "ADDED_CANDIDATE",
        "source_unmatched_reason": "no_exact_counterpart",
        "old": None,
        "new": element,
        "needs_review": False,
        "review_reasons": list(reasons),
        "movement_classification": None,
        "token_diff": None,
    }


def _case(*, deleted: list[dict], added: list[dict]) -> dict:
    return {
        "schema_version": "0.3.0",
        "comparison_stage": INPUT_STAGE,
        "old_document": {"document_id": "OLD-DOC"},
        "new_document": {"document_id": "NEW-DOC"},
        "statistics": {},
        "unchanged": [],
        "modified": [],
        "modified_review": [],
        "duplicate_exact_review": [],
        "deleted": deleted,
        "added": added,
    }


def _run_stage(case: dict) -> tuple[dict, subprocess.CompletedProcess[str]]:
    """Run the real ``s09_movement`` ``__main__`` on ``case``; return output."""
    directory = tempfile.mkdtemp(prefix="crr-s09-movement-")
    root = Path(directory)
    output = root / "output"
    output.mkdir()
    (output / "comparison_candidates.json").write_text(
        json.dumps(case), encoding="utf-8"
    )
    script = f"import runpy\nrunpy.run_path({str(S09)!r}, run_name='__main__')\n"
    completed = subprocess.run(
        [PYTHON, "-c", script],
        cwd=str(ROOT),
        env=_env(root),
        capture_output=True,
        text=True,
    )
    try:
        if completed.returncode != 0:
            raise AssertionError(completed.stderr + completed.stdout)
        result_path = output / "comparison_candidates_with_movement.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(directory, ignore_errors=True)
    return result, completed


def _evidence_types(result: dict) -> list[str]:
    return [item["evidence_type"] for item in result["structural_split_merge_review"]]


def _ids(result: dict, side: str) -> list[str]:
    element_key = "old" if side == "deleted" else "new"
    return [
        record[element_key]["element_id"]
        for record in result[side]
        if isinstance(record.get(element_key), dict)
    ]


class ImportSafetyTest(unittest.TestCase):
    """Importing s09_movement must not touch the workspace or print."""

    def test_import_has_no_workspace_side_effects(self):
        with tempfile.TemporaryDirectory(prefix="crr-s09-import-") as directory:
            workspace = Path(directory) / "empty-root"
            script = f"""
import importlib, os, tempfile
from pathlib import Path
work = Path(tempfile.mkdtemp(prefix="s09-import-probe-"))
os.environ["CRR_PROJECT_ROOT"] = str(work)
before = dict(sorted(os.environ.items()))
module = importlib.import_module("crr_compare.stages.s09_movement")
after = dict(sorted(os.environ.items()))
created = sorted(
    str(path.relative_to(work))
    for path in work.rglob("*")
    if "__pycache__" not in path.parts
)
assert before == after, "import mutated the environment"
assert created == [], f"import created files under the empty root: {{created!r}}"
assert callable(module.element_text)
assert callable(module.validate_input)
print("IMPORT_OK", module.STEP9_VERSION)
"""
            completed = subprocess.run(
                [PYTHON, "-c", script],
                cwd=str(ROOT),
                env=_env(),
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            self.assertIn("IMPORT_OK", completed.stdout)
            self.assertEqual(completed.stderr, "")
            self.assertFalse(workspace.exists(), "import resolved a workspace root")


class StructuralSplitMergeOwnershipTest(unittest.TestCase):
    """Exact structural split/merge is review-owned, not reconciler-owned."""

    def test_one_old_to_two_new_exact_split_is_review_owned(self):
        result, _ = _run_stage(
            _case(
                deleted=[
                    _deleted(
                        "DEL-H",
                        _element("OLD-H1", "heading", "Alpha beta gamma", 0),
                        ["upstream_delete_reason"],
                    )
                ],
                added=[
                    _added(
                        "ADD-H1",
                        _element("NEW-H1", "heading", "Alpha beta", 5),
                        ["upstream_add_reason"],
                    ),
                    _added(
                        "ADD-H2",
                        _element("NEW-H2", "heading", "gamma", 6),
                        ["upstream_add_reason"],
                    ),
                ],
            )
        )

        self.assertEqual(result["comparison_stage"], OUTPUT_STAGE)
        # The 1 -> 2 exact split is discovered instead of pre-empted.
        self.assertEqual(
            _evidence_types(result), ["one_old_to_two_new_exact_concatenation"]
        )
        split = result["structural_split_merge_review"][0]
        self.assertEqual(split["old_element_ids"], ["OLD-H1"])
        self.assertEqual(split["new_element_ids"], ["NEW-H1", "NEW-H2"])
        self.assertEqual(split["classification"], "STRUCTURAL_SPLIT_MERGE_REVIEW")
        self.assertTrue(split["needs_review"])
        self.assertIs(split["consumes_candidates"], False)
        # No near-twin reconciler pair may claim the structural residual.
        self.assertEqual(result["resegmentation_review"], [])
        # Review-only: every element still accounted exactly once.
        self.assertEqual(_ids(result, "deleted"), ["OLD-H1"])
        self.assertEqual(_ids(result, "added"), ["NEW-H1", "NEW-H2"])
        # Upstream reasons survive the reviewer-facing output.
        deleted_reasons = {
            c["candidate_id"]: c["review_reasons"] for c in result["deleted"]
        }
        added_reasons = {c["candidate_id"]: c["review_reasons"] for c in result["added"]}
        self.assertEqual(deleted_reasons["DEL-H"], ["upstream_delete_reason"])
        self.assertEqual(added_reasons["ADD-H1"], ["upstream_add_reason"])
        self.assertEqual(result["statistics"]["remaining_deleted_candidates"], 1)
        self.assertEqual(result["statistics"]["remaining_added_candidates"], 2)
        self.assertEqual(result["statistics"]["resegmentation_review_pairs"], 0)

    def test_two_old_to_one_new_exact_merge_is_review_owned(self):
        result, _ = _run_stage(
            _case(
                deleted=[
                    _deleted(
                        "DEL-H1",
                        _element("OLD-H1", "heading", "Alpha beta", 0),
                        ["upstream_delete_reason"],
                    ),
                    _deleted(
                        "DEL-H2",
                        _element("OLD-H2", "heading", "gamma", 1),
                        ["upstream_delete_reason"],
                    ),
                ],
                added=[
                    _added(
                        "ADD-H",
                        _element("NEW-H1", "heading", "Alpha beta gamma", 5),
                        ["upstream_add_reason"],
                    )
                ],
            )
        )

        self.assertEqual(
            _evidence_types(result), ["two_old_to_one_new_exact_concatenation"]
        )
        split = result["structural_split_merge_review"][0]
        self.assertEqual(split["old_element_ids"], ["OLD-H1", "OLD-H2"])
        self.assertEqual(split["new_element_ids"], ["NEW-H1"])
        self.assertIs(split["consumes_candidates"], False)
        self.assertEqual(result["resegmentation_review"], [])
        self.assertEqual(_ids(result, "deleted"), ["OLD-H1", "OLD-H2"])
        self.assertEqual(_ids(result, "added"), ["NEW-H1"])
        self.assertEqual(result["statistics"]["remaining_deleted_candidates"], 2)
        self.assertEqual(result["statistics"]["remaining_added_candidates"], 1)

    def test_changed_numeric_boundary_is_not_nonsemantic_equivalence(self):
        """A 21 -> 99 edit near a split boundary stays a genuine change.

        The two added pieces are a 1 -> 2 shape but not an exact concatenation
        ("Alpha beta 99" != "Alpha beta 21"), so no split evidence may be
        invented; and the generic reconciler must not pair old/new by the
        shared "Alpha beta" prefix (it drops the short numeric tokens), which
        would hide the numeric edit as a near-twin equivalence.
        """
        result, _ = _run_stage(
            _case(
                deleted=[
                    _deleted(
                        "DEL-NUM",
                        _element("OLD-N1", "heading", "Alpha beta 21", 0),
                        ["upstream_delete_reason"],
                    )
                ],
                added=[
                    _added(
                        "ADD-NUM1",
                        _element("NEW-N1", "heading", "Alpha beta", 5),
                        ["upstream_add_reason"],
                    ),
                    _added(
                        "ADD-NUM2",
                        _element("NEW-N2", "heading", "99", 6),
                        ["upstream_add_reason"],
                    ),
                ],
            )
        )

        self.assertEqual(result["structural_split_merge_review"], [])
        self.assertEqual(result["resegmentation_review"], [])
        self.assertEqual(_ids(result, "deleted"), ["OLD-N1"])
        self.assertEqual(_ids(result, "added"), ["NEW-N1", "NEW-N2"])
        self.assertEqual(result["statistics"]["remaining_deleted_candidates"], 1)
        self.assertEqual(result["statistics"]["remaining_added_candidates"], 2)
        self.assertEqual(result["statistics"]["resegmentation_review_pairs"], 0)

    def test_repeated_segments_surface_all_matches_not_first_winner(self):
        """Duplicated segments yield every exact match; nothing is consumed."""
        result, _ = _run_stage(
            _case(
                deleted=[
                    _deleted(
                        "DEL-REP",
                        _element("OLD-R1", "heading", "Alpha beta gamma", 0),
                        ["upstream_delete_reason"],
                    )
                ],
                added=[
                    _added("ADD-R1", _element("NEW-R1", "heading", "Alpha", 5), []),
                    _added(
                        "ADD-R2", _element("NEW-R2", "heading", "beta gamma", 6), []
                    ),
                    _added("ADD-R3", _element("NEW-R3", "heading", "Alpha", 7), []),
                    _added(
                        "ADD-R4", _element("NEW-R4", "heading", "beta gamma", 8), []
                    ),
                ],
            )
        )

        # Two disjoint exact concatenations exist; both are reported, neither
        # candidate is consumed (no arbitrary "first winner").
        self.assertEqual(
            _evidence_types(result),
            [
                "one_old_to_two_new_exact_concatenation",
                "one_old_to_two_new_exact_concatenation",
            ],
        )
        reported_new = [
            item["new_element_ids"] for item in result["structural_split_merge_review"]
        ]
        self.assertEqual(reported_new, [["NEW-R1", "NEW-R2"], ["NEW-R3", "NEW-R4"]])
        for item in result["structural_split_merge_review"]:
            self.assertIs(item["consumes_candidates"], False)
        self.assertEqual(result["resegmentation_review"], [])
        self.assertEqual(_ids(result, "deleted"), ["OLD-R1"])
        self.assertEqual(
            _ids(result, "added"),
            ["NEW-R1", "NEW-R2", "NEW-R3", "NEW-R4"],
        )

    def test_non_structural_resegmentation_reconciliation_still_applies(self):
        """The generic reconciler still owns non-structural near-twins."""
        result, _ = _run_stage(
            _case(
                deleted=[
                    _deleted(
                        "DEL-P",
                        _element(
                            "OLD-P1",
                            "paragraph",
                            "The quarterly revenue recognition policy applies",
                            0,
                        ),
                        [],
                    )
                ],
                added=[
                    _added(
                        "ADD-P",
                        _element(
                            "NEW-P1",
                            "paragraph",
                            "The quarterly revenue recognition policy applies here",
                            5,
                        ),
                        [],
                    )
                ],
            )
        )

        self.assertEqual(result["structural_split_merge_review"], [])
        self.assertEqual(len(result["resegmentation_review"]), 1)
        pair = result["resegmentation_review"][0]
        self.assertEqual(pair["candidate_type"], "RESEGMENTATION_REVIEW")
        self.assertEqual(pair["old"]["element_id"], "OLD-P1")
        self.assertEqual(pair["new"]["element_id"], "NEW-P1")
        self.assertEqual(_ids(result, "deleted"), [])
        self.assertEqual(_ids(result, "added"), [])

    def test_unrelated_remaining_add_delete_is_accounted(self):
        """Unrelated non-structural residuals survive next to a split."""
        result, _ = _run_stage(
            _case(
                deleted=[
                    _deleted(
                        "DEL-H",
                        _element("OLD-H1", "heading", "Alpha beta gamma", 0),
                        [],
                    ),
                    _deleted(
                        "DEL-P",
                        _element("OLD-P1", "paragraph", "Obsolete clause text", 3),
                        ["upstream_delete_reason"],
                    ),
                ],
                added=[
                    _added("ADD-H1", _element("NEW-H1", "heading", "Alpha beta", 5), []),
                    _added("ADD-H2", _element("NEW-H2", "heading", "gamma", 6), []),
                    _added(
                        "ADD-P",
                        _element("NEW-P1", "paragraph", "Brand new clause text", 7),
                        ["upstream_add_reason"],
                    ),
                ],
            )
        )

        self.assertEqual(
            _evidence_types(result), ["one_old_to_two_new_exact_concatenation"]
        )
        self.assertEqual(_ids(result, "deleted"), ["OLD-H1", "OLD-P1"])
        self.assertEqual(_ids(result, "added"), ["NEW-H1", "NEW-H2", "NEW-P1"])
        self.assertEqual(result["statistics"]["remaining_deleted_candidates"], 2)
        self.assertEqual(result["statistics"]["remaining_added_candidates"], 3)


if __name__ == "__main__":
    unittest.main()
