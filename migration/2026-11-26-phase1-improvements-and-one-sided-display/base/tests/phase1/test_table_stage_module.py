"""Stage-10 (s10_tables) module-safety and behavior tests.

Three properties are pinned here, following the s07/s08/s09 module-safety
pattern:

1. Importing ``s10_tables`` must have **no** side effects: it must not
   resolve or create a workspace root, read or write pipeline files, print,
   or mutate the environment.  Historically the module resolved
   ``find_project_root()`` (which *creates* the root directory), printed,
   and then crashed on the missing ``output/old_document_structured.json``
   at import time.
2. The pure table helpers must stay callable after import with no pipeline
   data, and the conservative normalization that routes table cells to
   review must keep its meaning: layout hyphenation is preserved, selection
   states are distinct, and numeric skeletons unify only under the
   aggressive artifact path.
3. Running the module as ``__main__`` stays the documented Stage-10 entry
   point: it reads the three Step-3/9 seeds, writes a validated
   ``output/table_comparison_result.json``, and prints the CLI-equivalent
   report.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
S10 = ROOT / "src" / "document_compare" / "phase1" / "stages" / "s10_tables.py"
PYTHON = Path(sys.executable)
SAMPLE_OLD = ROOT / "output_sample" / "old_document_structured.json"
SAMPLE_NEW = ROOT / "output_sample" / "new_document_structured.json"
SAMPLE_MOVEMENT = ROOT / "output_sample" / "comparison_candidates_with_movement.json"
_SAMPLE_FILES = (
    "old_document_structured.json",
    "new_document_structured.json",
    "comparison_candidates_with_movement.json",
)
_MISSING_SAMPLES = [name for name in _SAMPLE_FILES if not (ROOT / "output_sample" / name).is_file()]


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


def _run(script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(PYTHON), "-c", script],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


class ImportSafetyTest(unittest.TestCase):
    """Importing s10_tables must not touch the workspace or print."""

    def test_import_has_no_workspace_side_effects(self):
        with tempfile.TemporaryDirectory(prefix="crr-s10-import-") as directory:
            workspace = Path(directory) / "empty-root"
            script = f"""
import importlib, os
from pathlib import Path
workspace = Path({str(workspace)!r})
os.environ["CRR_PROJECT_ROOT"] = str(workspace)
env_before = dict(sorted(os.environ.items()))
module = importlib.import_module("document_compare.phase1.stages.s10_tables")
env_after = dict(sorted(os.environ.items()))
created = sorted(
    str(path.relative_to(workspace))
    for path in workspace.rglob("*")
    if "__pycache__" not in path.parts
)
assert env_before == env_after, "import mutated the environment"
assert created == [], f"import created files under the empty root: {{created!r}}"
print("IMPORT_OK", module.STEP10_VERSION)
"""
            completed = _run(script, _env())
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            self.assertIn("IMPORT_OK", completed.stdout)
            self.assertEqual(completed.stderr, "")
            self.assertFalse(
                workspace.exists(), "import resolved or created the workspace root"
            )


class ModuleHelpersUsableTest(unittest.TestCase):
    """Pure table helpers must work after import with no pipeline data.

    The normalization meanings that route uncertain cells to review are
    pinned exactly: layout hyphenation is preserved (not collapsed), the
    two selection states stay distinct, and the aggressive artifact
    skeleton unifies only transparently.
    """

    def test_helpers_usable_and_normalization_meanings_preserved(self):
        with tempfile.TemporaryDirectory(prefix="crr-s10-helpers-") as directory:
            empty = Path(directory) / "empty-root"
            script = f"""
import importlib, os
from pathlib import Path
os.environ["CRR_PROJECT_ROOT"] = str(Path({str(empty)!r}))
s10 = importlib.import_module("document_compare.phase1.stages.s10_tables")

# Conservative layout normalization preserves hyphenation.
assert s10.layout_text("Waren- und") != s10.layout_text("Warenund")
assert s10.layout_text("Beta-Faktor") != s10.layout_text("BetaFaktor")
assert s10.layout_text("10 - 20") == "10-20", "digit range spacing normalized"
# Selection states are distinct.
assert s10.selection_states(":selected:") != s10.selection_states(":unselected:")
# Aggressive artifact skeleton unifies true OCR/line-wrap artifacts only.
assert s10.alphanumeric_skeleton("Beta- Faktor") == s10.alphanumeric_skeleton("BetaFaktor")
assert s10.artifact_tolerant_text("Be- ta-Fak- tor") == s10.artifact_tolerant_text("BetaFaktor")

# Pure helpers usable with ordinary inputs.
assert s10.source_text("  a  b ") == "a b"
assert s10.header_similarity(("A", "B"), ("A", "B")) == 1.0
assert s10.compact_cell(None) is None
assert s10.compare_cell(None, {{"text": "x"}})["classification"] == "ADDED"
assert s10.cell_structure({{"row_span": 2, "column_span": 3, "kind": "data"}}) == (2, 3, "data")
assert s10.compact_token_diff("a b", "a c")["change_operation_count"] == 1
assert s10.compare_cell(
    {{"text": "same"}}, {{"text": "same"}}
)["classification"] == "UNCHANGED"
assert s10.residual_context_key({{"nearest_provisional_context_key": "K1"}}) == "K1"
assert s10.residual_context_key({{}}) is None

# Core table assembly entry points usable on empty inputs.
old_tables, audit = s10.make_logical_tables("old", [], {{"": ""}})
assert old_tables == [] and audit == []
matches, unmatched_old, unmatched_new, match_audit = s10.match_logical_tables([], [])
assert matches == [] and unmatched_old == [] and unmatched_new == []
assert match_audit["exact_matches"] == 0 and match_audit["fuzzy_matches"] == 0

assert not Path({str(empty)!r}).exists(), "import resolved or created the workspace root"
print("HELPERS_OK")
"""
            completed = _run(script, _env())
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            self.assertIn("HELPERS_OK", completed.stdout)
            self.assertEqual(completed.stderr, "")
            self.assertFalse(empty.exists(), "import created the workspace root")


@unittest.skipUnless(
    not _MISSING_SAMPLES,
    "private output_sample fixtures are unavailable in this checkout: "
    + ", ".join(_MISSING_SAMPLES),
)
class MainScriptTest(unittest.TestCase):
    """s10_tables must run as a CLI-equivalent __main__ script.

    Seeds are read-only copies of the committed ``output_sample`` Step-3/9
    results; every run happens in an isolated temporary root with the fixed
    offline environment, so no originals and no network are touched.
    """

    def test_main_writes_table_comparison_result_and_reports(self):
        with tempfile.TemporaryDirectory(prefix="crr-s10-main-") as directory:
            root = Path(directory)
            output = root / "output"
            output.mkdir()
            shutil.copyfile(SAMPLE_OLD, output / "old_document_structured.json")
            shutil.copyfile(SAMPLE_NEW, output / "new_document_structured.json")
            shutil.copyfile(
                SAMPLE_MOVEMENT, output / "comparison_candidates_with_movement.json"
            )
            script = (
                f"import runpy\n"
                f"runpy.run_path({str(S10)!r}, run_name='__main__')\n"
            )
            completed = _run(script, _env(root))
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result_path = output / "table_comparison_result.json"
            self.assertTrue(result_path.is_file())
            result = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(result["schema_version"], "0.4.0")
            self.assertEqual(
                result["comparison_stage"],
                "logical_table_comparison_with_provisional_context",
            )
            self.assertGreaterEqual(result["statistics"]["matched_logical_tables"], 1)
            self.assertIn("Saved:", completed.stdout)
            self.assertIn("=== ADDED TABLE CANDIDATES ===", completed.stdout)


def _cell(
    column_index: int,
    text: str,
    *,
    cell_id: str | None = None,
    row_span: int = 1,
    column_span: int = 1,
    kind: str | None = None,
    page: int = 1,
) -> dict:
    """One logical/DI cell with an explicit, inspectable coordinate."""
    return {
        "cell_id": cell_id,
        "column_index": column_index,
        "column_span": column_span,
        "row_span": row_span,
        "kind": kind,
        "text": text,
        "normalized_text": text,
        "primary_page": page,
    }


def _physical_table(
    page: int,
    *,
    rows: list[list[dict]],
    element_id: str,
    reading_order: int | None = None,
    context_keys: list[str] | None = None,
) -> dict:
    """A document element wrapping a DI-shaped physical table fragment."""
    cells = []
    for row_index, row in enumerate(rows):
        for cell in row:
            copied = dict(cell)
            copied["row_index"] = row_index
            copied["_source_row_index"] = row_index
            cells.append(copied)
    return {
        "element_id": element_id,
        "primary_page": page,
        "reading_order": reading_order if reading_order is not None else page,
        "provisional_anchor_context": {"recent_anchor_ids": list(context_keys or [])},
        "table": {
            "row_count": len(rows),
            "column_count": max((len(row) for row in rows), default=0),
            "cells": cells,
        },
    }


def _logical_table(side: str, rows: list[list[dict]]) -> dict:
    """A minimal logical table matching ``build_logical_table``'s shape."""
    logical_rows = []
    for row_index, row in enumerate(rows):
        cells = []
        for cell in row:
            copied = dict(cell)
            copied["_source_table_id"] = f"{side.upper()}-PHY-0001"
            copied["_source_row_index"] = row_index
            copied["_logical_row_index"] = row_index
            cells.append(copied)
        logical_rows.append(
            {
                "logical_row_index": row_index,
                "source_table_id": f"{side.upper()}-PHY-0001",
                "source_row_index": row_index,
                "is_header": False,
                "cells": cells,
            }
        )
    return {
        "logical_table_id": f"{side.upper()}-LT0001",
        "side": side,
        "source_table_ids": [f"{side.upper()}-PHY-0001"],
        "source_fragment_count": 1,
        "source_pages": [1],
        "first_reading_order": 1,
        "provisional_context_keys": [],
        "nearest_provisional_context_key": None,
        "context_is_authoritative_hierarchy": False,
        "requires_extraction_review": False,
        "extraction_warning_flags": [],
        "extraction_review_flags": [],
        "column_count": max((len(row) for row in rows), default=0),
        "row_count": len(rows),
        "header_signature": [],
        "rows": logical_rows,
        "omitted_repeated_header_rows": 0,
        "omitted_repeated_header_cells": 0,
    }


def _match(old: dict, new: dict) -> dict:
    return {
        "old": old,
        "new": new,
        "match_method": "exact_logical_table_content",
        "matching_evidence": {},
    }


class CellCoordinateContractTest(unittest.TestCase):
    """Malformed table cells must be rejected, never silently deduplicated.

    ``cells_by_column`` used to build a plain ``{column_index: cell}`` dict,
    so a physical row with two cells at the same coordinate kept only the
    last one.  A changed numeric cell (21 -> 99) followed by an unchanged
    duplicate at the same column was therefore dropped from the comparison
    with no error.  These tests pin the contract: duplicate coordinates and
    duplicate cell ids are rejected with actionable identifiers, while valid
    merged spans and repeated-page continuation headers stay accepted.
    """

    def test_changed_numeric_cell_survives_in_wellformed_row(self):
        """Well-formed rows keep a 21 -> 99 numeric change verbatim."""
        import document_compare.phase1.stages.s10_tables as s10

        old = _logical_table(
            "old", [[_cell(0, "21"), _cell(1, "keep")]]
        )
        new = _logical_table(
            "new", [[_cell(0, "99"), _cell(1, "keep")]]
        )
        result = s10.compare_table_match(_match(old, new), 1)

        self.assertEqual(result["classification"], "MODIFIED")
        changed = [
            change
            for change in result["cell_changes"]
            if change["column_index"] == 0
        ]
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0]["classification"], "MODIFIED")
        self.assertEqual(changed[0]["old"]["text"], "21")
        self.assertEqual(changed[0]["new"]["text"], "99")
        self.assertEqual(result["statistics"]["modified_cells"], 1)

    def test_duplicate_column_in_matched_row_is_rejected(self):
        """A changed cell hidden behind a same-column duplicate is rejected."""
        import document_compare.phase1.stages.s10_tables as s10

        old = _logical_table(
            "old",
            [[_cell(0, "21", cell_id="c1"), _cell(0, "unchanged", cell_id="c2")]],
        )
        new = _logical_table(
            "new",
            [[_cell(0, "99", cell_id="c1"), _cell(0, "unchanged", cell_id="c2")]],
        )
        with self.assertRaises(s10.ContractError) as raised:
            s10.compare_table_match(_match(old, new), 4)
        message = str(raised.exception)
        self.assertIn("s10", message)
        self.assertIn("row=0", message)
        self.assertIn("column=0", message)
        self.assertIn("c1", message)
        self.assertIn("c2", message)

    def test_duplicate_column_in_deleted_row_is_rejected(self):
        import document_compare.phase1.stages.s10_tables as s10

        old = _logical_table(
            "old",
            [
                [_cell(0, "same")],
                [_cell(0, "gone", cell_id="d1"), _cell(0, "gone2", cell_id="d2")],
            ],
        )
        new = _logical_table("new", [[_cell(0, "same")]])
        with self.assertRaises(s10.ContractError) as raised:
            s10.compare_table_match(_match(old, new), 5)
        self.assertIn("d1", str(raised.exception))
        self.assertIn("d2", str(raised.exception))

    def test_duplicate_column_in_added_row_is_rejected(self):
        import document_compare.phase1.stages.s10_tables as s10

        old = _logical_table("old", [[_cell(0, "same")]])
        new = _logical_table(
            "new",
            [
                [_cell(0, "same")],
                [_cell(0, "new", cell_id="a1"), _cell(0, "new2", cell_id="a2")],
            ],
        )
        with self.assertRaises(s10.ContractError) as raised:
            s10.compare_table_match(_match(old, new), 6)
        self.assertIn("a1", str(raised.exception))
        self.assertIn("a2", str(raised.exception))

    def test_build_logical_table_rejects_duplicate_coordinates(self):
        """The earliest s10 boundary rejects malformed cells too.

        Exact-content table matching never calls ``compare_cell``, so the
        input coordinate contract is enforced while physical fragments are
        assembled into logical tables.
        """
        import document_compare.phase1.stages.s10_tables as s10

        fragment = _physical_table(
            1,
            rows=[
                [
                    _cell(0, "21", cell_id="x1"),
                    _cell(0, "unchanged", cell_id="x2"),
                ]
            ],
            element_id="OLD-PHY-0001",
        )
        with self.assertRaises(s10.ContractError) as raised:
            s10.make_logical_tables("old", [fragment], {"": ""})
        message = str(raised.exception)
        self.assertIn("s10", message)
        self.assertIn("OLD-PHY-0001", message)
        self.assertIn("row=0", message)
        self.assertIn("column=0", message)

    def test_duplicate_cell_id_is_rejected(self):
        import document_compare.phase1.stages.s10_tables as s10

        fragment = _physical_table(
            1,
            rows=[[_cell(0, "a", cell_id="dup"), _cell(1, "b", cell_id="dup")]],
            element_id="OLD-PHY-0002",
        )
        with self.assertRaises(s10.ContractError) as raised:
            s10.make_logical_tables("old", [fragment], {"": ""})
        self.assertIn("dup", str(raised.exception))

    def test_valid_merged_spans_are_not_rejected(self):
        """row_span/column_span cells are valid DI structure, not duplicates."""
        import document_compare.phase1.stages.s10_tables as s10

        fragment = _physical_table(
            1,
            rows=[
                [
                    _cell(0, "Group", row_span=2, kind="columnHeader"),
                    _cell(1, "Value", kind="columnHeader"),
                ],
                [_cell(1, "Alpha")],
            ],
            element_id="OLD-PHY-0003",
        )
        tables, _ = s10.make_logical_tables("old", [fragment], {"": ""})
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0]["rows"][0]["cells"][0]["row_span"], 2)
        # The merged cell occupies a single coordinate and is accounted once.
        rows = tables[0]["rows"]
        by_column = s10.cells_by_column(rows[0])
        self.assertEqual(sorted(by_column), [0, 1])

    def test_continuation_repeated_header_is_not_a_duplicate_coordinate(self):
        """Repeated continuation headers are omitted and documented."""
        import document_compare.phase1.stages.s10_tables as s10

        header = [_cell(0, "Region", kind="columnHeader"), _cell(1, "Volume", kind="columnHeader")]
        first = _physical_table(
            6, rows=[header, [_cell(0, "North"), _cell(1, "10")]], element_id="NEW-PHY-0001"
        )
        second = _physical_table(
            7, rows=[header, [_cell(0, "South"), _cell(1, "20")]], element_id="NEW-PHY-0002"
        )
        tables, _ = s10.make_logical_tables("new", [first, second], {"": ""})
        self.assertEqual(len(tables), 1)
        table = tables[0]
        self.assertEqual(table["source_fragment_count"], 2)
        self.assertEqual(table["omitted_repeated_header_rows"], 1)
        self.assertEqual(table["omitted_repeated_header_cells"], 2)
        self.assertEqual(
            [cell["text"] for row in table["rows"] for cell in row["cells"]],
            ["Region", "Volume", "North", "10", "South", "20"],
        )

    def test_exactly_once_accounting_names_a_dropped_cell(self):
        """Accounting is identity-based and names the dropped coordinate."""
        import document_compare.phase1.stages.s10_tables as s10

        table = _logical_table(
            "old", [[_cell(0, "21", cell_id="c1"), _cell(1, "keep", cell_id="c2")]]
        )
        accounted = [s10.compact_cell(table["rows"][0]["cells"][0])]
        with self.assertRaises(s10.ContractError) as raised:
            s10.require_exactly_once_cell_accounting("old", table, accounted, 1)
        message = str(raised.exception)
        self.assertIn("missing", message)
        self.assertIn("OLD-PHY-0001", message)
        self.assertIn(", 1)", message)

    def test_exactly_once_accounting_passes_for_complete_rows(self):
        import document_compare.phase1.stages.s10_tables as s10

        table = _logical_table(
            "new", [[_cell(0, "99", cell_id="c1"), _cell(1, "keep", cell_id="c2")]]
        )
        accounted = [s10.compact_cell(cell) for cell in table["rows"][0]["cells"]]
        count = s10.require_exactly_once_cell_accounting("new", table, accounted, 1)
        self.assertEqual(count, 2)


class ContinuationEvidenceTest(unittest.TestCase):
    """Continuation grouping must preserve fragments and local evidence."""

    @staticmethod
    def _header() -> list[dict]:
        return [
            _cell(0, "Region", kind="columnHeader"),
            _cell(1, "Volume", kind="columnHeader"),
        ]

    def test_distinct_local_labels_reject_same_header_adjacent_tables(self):
        import document_compare.phase1.stages.s10_tables as s10

        first = _physical_table(
            6,
            rows=[self._header(), [_cell(0, "North"), _cell(1, "10")]],
            element_id="NEW-PHY-0001",
        )
        second = _physical_table(
            7,
            rows=[self._header(), [_cell(0, "South"), _cell(1, "20")]],
            element_id="NEW-PHY-0002",
        )
        context = {
            "by_table": {
                "NEW-PHY-0001": {
                    "nearby_labels": ["table 1: opening balances"],
                    "preceding_boundaries": [],
                    "following_boundaries": [],
                },
                "NEW-PHY-0002": {
                    "nearby_labels": ["table 2: closing balances"],
                    "preceding_boundaries": [],
                    "following_boundaries": [],
                },
            }
        }

        tables, audit = s10.make_logical_tables("new", [first, second], {}, context)

        self.assertEqual(len(tables), 2)
        self.assertEqual([table["source_fragment_count"] for table in tables], [1, 1])
        self.assertEqual(
            [table["source_table_ids"] for table in tables],
            [["NEW-PHY-0001"], ["NEW-PHY-0002"]],
        )
        self.assertEqual(len(audit), 1)
        self.assertFalse(audit[0]["joined"])
        self.assertIn("contradictory_local_boundary_evidence", audit[0]["reason_codes"])
        self.assertEqual(
            audit[0]["contradictory_evidence"]["previous_nearby_labels"],
            ["table 1: opening balances"],
        )
        self.assertEqual(
            audit[0]["contradictory_evidence"]["current_nearby_labels"],
            ["table 2: closing balances"],
        )

    def test_direct_neighbor_context_does_not_skip_over_unrelated_elements(self):
        import document_compare.phase1.stages.s10_tables as s10

        table_a = _physical_table(
            3, rows=[[_cell(0, "A")]], element_id="PHY-A", reading_order=2
        )
        table_a["element_type"] = "table"
        table_b = _physical_table(
            3, rows=[[_cell(0, "B")]], element_id="PHY-B", reading_order=4
        )
        table_b["element_type"] = "table"
        elements = [
            {"element_id": "CAP-A", "element_type": "paragraph", "primary_page": 3,
             "reading_order": 1, "text": "Table A: opening balances"},
            table_a,
            {"element_id": "P", "element_type": "paragraph", "primary_page": 3,
             "reading_order": 3, "text": "Ordinary intervening paragraph."},
            table_b,
            {"element_id": "CAP-B", "element_type": "paragraph", "primary_page": 3,
             "reading_order": 5, "text": "Table B: closing balances"},
        ]

        context = s10.build_continuation_context(elements)

        self.assertEqual(
            context["by_table"]["PHY-A"]["nearby_labels"],
            ["table a: opening balances"],
        )
        self.assertEqual(context["by_table"]["PHY-B"]["nearby_labels"], ["table b: closing balances"])
        self.assertEqual(context["by_table"]["PHY-A"]["following_boundaries"], [])
        self.assertEqual(context["by_table"]["PHY-B"]["preceding_boundaries"], [])

    def test_repeated_header_continuation_audit_and_cell_accounting(self):
        import document_compare.phase1.stages.s10_tables as s10

        first = _physical_table(
            6,
            rows=[
                [_cell(0, "Region", cell_id="h1", kind="columnHeader"),
                 _cell(1, "Volume", cell_id="h2", kind="columnHeader")],
                [_cell(0, "North", cell_id="n1"), _cell(1, "10", cell_id="n2")],
            ],
            element_id="NEW-PHY-0001",
        )
        second = _physical_table(
            7,
            rows=[
                [_cell(0, "Region", cell_id="h3", kind="columnHeader"),
                 _cell(1, "Volume", cell_id="h4", kind="columnHeader")],
                [_cell(0, "South", cell_id="s1"), _cell(1, "20", cell_id="s2")],
            ],
            element_id="NEW-PHY-0002",
        )

        tables, audit = s10.make_logical_tables("new", [first, second], {})

        self.assertEqual(len(tables), 1)
        table = tables[0]
        self.assertEqual(table["source_table_ids"], ["NEW-PHY-0001", "NEW-PHY-0002"])
        self.assertEqual(table["source_fragment_count"], 2)
        self.assertEqual(table["omitted_repeated_header_rows"], 1)
        self.assertEqual(table["omitted_repeated_header_cells"], 2)
        retained_ids = {cell["cell_id"] for row in table["rows"] for cell in row["cells"]}
        self.assertEqual(retained_ids, {"h1", "h2", "n1", "n2", "s1", "s2"})
        self.assertEqual(retained_ids | {"h3", "h4"}, {"h1", "h2", "n1", "n2", "h3", "h4", "s1", "s2"})
        self.assertEqual(len(audit), 1)
        self.assertTrue(audit[0]["joined"])
        self.assertEqual(audit[0]["decision"], "joined")
        self.assertIn("strong_repeated_header", audit[0]["reason_codes"])
        self.assertEqual(audit[0]["missing_evidence"], ["local_boundary_context"])
        self.assertIn("evidence", audit[0])
        self.assertIn("local_boundary_evidence", audit[0]["evidence"])

    def test_missing_caption_context_and_geometry_use_safe_header_fallback(self):
        import document_compare.phase1.stages.s10_tables as s10

        first = _physical_table(
            10,
            rows=[self._header(), [_cell(0, "East"), _cell(1, "30")]],
            element_id="NEW-PHY-0010",
        )
        second = _physical_table(
            11,
            rows=[self._header(), [_cell(0, "West"), _cell(1, "40")]],
            element_id="NEW-PHY-0011",
        )
        first["element_type"] = "table"
        second["element_type"] = "table"

        context = s10.build_continuation_context([first, second])
        tables, audit = s10.make_logical_tables("new", [first, second], {})

        self.assertEqual(set(context["by_table"]), {"NEW-PHY-0010", "NEW-PHY-0011"})
        self.assertEqual(len(tables), 1)
        self.assertTrue(audit[0]["joined"])
        self.assertIn("strong_repeated_header", audit[0]["reason_codes"])
        self.assertEqual(audit[0]["missing_evidence"], ["local_boundary_context"])


class BoundedTableRetrievalTest(unittest.TestCase):
    """Bounded retrieval must retain uncertainty rather than infer absence."""

    @staticmethod
    def _logical(side: str, index: int, value: str, columns: int = 2) -> dict:
        import document_compare.phase1.stages.s10_tables as s10

        headers = [_cell(0, "Risk register", kind="columnHeader")]
        data = [_cell(0, value)]
        for column in range(1, columns):
            headers.append(_cell(column, f"Field {column}", kind="columnHeader"))
            data.append(_cell(column, f"{value} {column}"))
        fragment = _physical_table(
            1, rows=[headers, data], element_id=f"{side.upper()}-PHY-{index:04}",
            reading_order=index,
        )
        table = s10.make_logical_tables(side, [fragment], {})[0][0]
        table["logical_table_id"] = f"{side.upper()}-LT{index:04}"
        return table

    def test_per_table_limit_records_excluded_endpoint_evidence(self):
        import document_compare.phase1.stages.s10_tables as s10

        old = [self._logical("old", 1, "changed source")]
        new = [self._logical("new", index, f"changed target {index}") for index in range(1, 14)]
        pairs, audit = s10.retrieve_table_candidate_pairs(old, new)

        self.assertEqual(audit["possible_residual_pairs"], 13)
        self.assertEqual(len(pairs), s10.MAX_RETRIEVED_CANDIDATES_PER_TABLE)
        self.assertEqual(audit["old_tables_with_incomplete_candidate_search"], [old[0]["logical_table_id"]])
        self.assertEqual(len(audit["excluded_candidate_pairs"]), 1)
        excluded = audit["excluded_candidate_pairs"][0]
        self.assertEqual(excluded["exclusion_reason"], "per_table_candidate_limit")
        self.assertIn(excluded["new_table_id"], audit["new_tables_with_incomplete_candidate_search"])

    def test_per_counterpart_limit_records_both_endpoints(self):
        import document_compare.phase1.stages.s10_tables as s10

        old = [self._logical("old", index, f"changed source {index}") for index in range(1, 14)]
        new = [self._logical("new", 1, "changed target")]
        pairs, audit = s10.retrieve_table_candidate_pairs(old, new)

        self.assertEqual(len(pairs), s10.MAX_RETRIEVED_CANDIDATES_PER_COUNTERPART)
        self.assertEqual(audit["new_tables_with_incomplete_candidate_search"], [new[0]["logical_table_id"]])
        excluded = audit["excluded_candidate_pairs"][0]
        self.assertEqual(excluded["exclusion_reason"], "per_counterpart_candidate_limit")
        self.assertIn(excluded["old_table_id"], audit["old_tables_with_incomplete_candidate_search"])

    def test_feature_poor_fallback_is_bounded(self):
        import document_compare.phase1.stages.s10_tables as s10

        old = _logical_table("old", [])
        old["column_count"] = 2
        new = []
        for index in range(1, 5):
            table = _logical_table("new", [])
            table["logical_table_id"] = f"NEW-LT{index:04}"
            table["column_count"] = 2
            new.append(table)
        pairs, audit = s10.retrieve_table_candidate_pairs([old], new)

        self.assertEqual(len(pairs), s10.FEATURE_POOR_FALLBACK_CANDIDATES)
        self.assertEqual(audit["candidate_pairs_pruned_by_limit"], 1)
        self.assertEqual(audit["excluded_candidate_pairs"][0]["retrieval_evidence"], ["feature_poor_shape_fallback"])

    def test_excluded_competitor_blocks_fuzzy_acceptance_and_bounds_scoring(self):
        import document_compare.phase1.stages.s10_tables as s10

        old = [self._logical("old", index, f"account {index} before") for index in range(1, 14)]
        new = [self._logical("new", index, f"account {index} after") for index in range(1, 14)]
        matches, unmatched_old, unmatched_new, audit = s10.match_logical_tables(old, new)

        self.assertEqual(audit["possible_residual_pairs"], 169)
        self.assertLess(audit["candidate_pairs_scored"], audit["possible_residual_pairs"])
        self.assertGreater(audit["candidate_pairs_pruned_by_limit"], 0)
        self.assertEqual(matches, [])
        self.assertEqual(len(unmatched_old), len(old))
        self.assertEqual(len(unmatched_new), len(new))

    def test_pruned_competitor_blocks_otherwise_supported_fuzzy_match(self):
        import document_compare.phase1.stages.s10_tables as s10

        old = self._logical("old", 1, "counterparty exposure revised")
        retained = self._logical("new", 1, "counterparty exposure revised today")
        omitted = self._logical("new", 2, "counterparty exposure revised tomorrow")

        with mock.patch.object(s10, "MAX_RETRIEVED_CANDIDATES_PER_TABLE", 1):
            matches, unmatched_old, unmatched_new, audit = s10.match_logical_tables(
                [old], [retained, omitted]
            )

        self.assertEqual(audit["candidate_pairs_scored"], 1)
        self.assertEqual(audit["candidate_pairs_pruned_by_limit"], 1)
        self.assertEqual(audit["fuzzy_candidate_pairs"], 1)
        self.assertEqual(audit["fuzzy_matches"], 0)
        self.assertEqual(matches, [])
        self.assertEqual(unmatched_old, [old])
        self.assertEqual(unmatched_new, [retained, omitted])

    def test_incomplete_search_groups_preserve_evidence_and_exclude_one_sided(self):
        import document_compare.phase1.stages.s10_tables as s10

        old = self._logical("old", 1, "counterparty exposure")
        new = self._logical("new", 1, "counterparty exposure revised")
        audit = {
            "old_tables_with_incomplete_candidate_search": [old["logical_table_id"]],
            "new_tables_with_incomplete_candidate_search": [new["logical_table_id"]],
            "excluded_candidate_pairs": [{
                "old_table_id": old["logical_table_id"],
                "new_table_id": new["logical_table_id"],
                "retrieval_evidence": ["shared_header_token", "shared_body_token"],
                "exclusion_reason": "per_table_candidate_limit",
            }],
        }

        groups, unresolved_old, unresolved_new = s10.incomplete_search_unresolved_groups(
            [old], [new], audit, starting_group_index=3
        )

        self.assertEqual(unresolved_old, {old["logical_table_id"]})
        self.assertEqual(unresolved_new, {new["logical_table_id"]})
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual(group["review_group_id"], "TABLE-REVIEW-0003")
        self.assertEqual(group["reason"], "incomplete_bounded_candidate_search_requires_review")
        self.assertEqual(group["old_tables"][0]["logical_table_id"], old["logical_table_id"])
        self.assertEqual(group["new_tables"][0]["logical_table_id"], new["logical_table_id"])
        self.assertEqual(group["excluded_candidate_evidence"], audit["excluded_candidate_pairs"])
        self.assertEqual(
            [table for table in [old] if table["logical_table_id"] not in unresolved_old],
            [],
        )
        self.assertEqual(
            [table for table in [new] if table["logical_table_id"] not in unresolved_new],
            [],
        )

    def test_moved_changed_column_count_is_retrieved_and_one_sided_stays_complete(self):
        import document_compare.phase1.stages.s10_tables as s10

        old = self._logical("old", 1, "counterparty exposure", 2)
        new = self._logical("new", 1, "counterparty exposure revised", 3)
        pairs, retrieval = s10.retrieve_table_candidate_pairs([old], [new])
        matches, unmatched_old, unmatched_new, audit = s10.match_logical_tables([old], [new])
        one_sided_matches, one_sided_old, one_sided_new, one_sided_audit = s10.match_logical_tables([old], [])

        self.assertEqual(len(pairs), 1)
        self.assertEqual(retrieval["old_tables_with_incomplete_candidate_search"], [])
        self.assertEqual(retrieval["new_tables_with_incomplete_candidate_search"], [])
        self.assertEqual(len(matches), 1)
        self.assertEqual(unmatched_old, [])
        self.assertEqual(unmatched_new, [])
        self.assertEqual(audit["fuzzy_matches"], 1)
        self.assertEqual(one_sided_matches, [])
        self.assertEqual(one_sided_old, [old])
        self.assertEqual(one_sided_new, [])
        self.assertEqual(one_sided_audit["old_tables_with_incomplete_candidate_search"], [])


class UnmatchedTableContentTest(unittest.TestCase):
    """A deleted/added table must keep its real cell content, not metadata only.

    ``compact_unmatched_table`` is the one-sided counterpart of
    ``compare_table_match``: every logical-row cell becomes exactly one
    one-sided cell change through the same ``compare_cell`` path and the same
    exactly-once accounting, so physical cell text, cell ids and source
    locations survive into the redline in the correct old/new orientation.
    """

    def test_deleted_table_keeps_numeric_cell_content_and_source_locations(self):
        import document_compare.phase1.stages.s10_tables as s10

        old = _logical_table(
            "old",
            [
                [_cell(0, "BTO 1", cell_id="c1", page=8), _cell(1, "62", cell_id="c2", page=8)],
                [_cell(0, "BTO 1.1", cell_id="c3", page=8), _cell(1, "41", cell_id="c4", page=8)],
            ],
        )
        compact = s10.compact_unmatched_table(old, "DELETED")

        self.assertEqual(compact["cell_content_status"], "available")
        self.assertEqual(compact["logical_table_id"], "OLD-LT0001")
        changes = compact["cell_changes"]
        self.assertEqual(len(changes), 4)
        self.assertTrue(all(change["classification"] == "DELETED" for change in changes))
        by_text = {change["old"]["text"]: change for change in changes}
        numeric = by_text["41"]
        self.assertIsNone(numeric["new"])
        self.assertEqual(numeric["old"]["cell_id"], "c4")
        self.assertEqual(numeric["old"]["source_table_id"], "OLD-PHY-0001")
        self.assertEqual(numeric["old"]["source_row_index"], 1)
        self.assertEqual(numeric["old"]["primary_page"], 8)
        self.assertEqual(numeric["column_index"], 1)
        self.assertEqual(numeric["row_match_type"], "deleted_table")
        self.assertEqual(
            numeric["change_id"], "TABLE-DELETED-OLD-LT0001-R00001-C001"
        )
        self.assertEqual(
            len({change["change_id"] for change in changes}), 4
        )

    def test_added_table_keeps_content_in_new_orientation(self):
        import document_compare.phase1.stages.s10_tables as s10

        new = _logical_table(
            "new",
            [[_cell(0, "Emerging", cell_id="a1"), _cell(1, "17", cell_id="a2")]],
        )
        compact = s10.compact_unmatched_table(new, "ADDED")

        self.assertEqual(compact["cell_content_status"], "available")
        changes = compact["cell_changes"]
        self.assertEqual(len(changes), 2)
        self.assertTrue(all(change["classification"] == "ADDED" for change in changes))
        added = {change["new"]["text"]: change for change in changes}["17"]
        self.assertIsNone(added["old"])
        self.assertEqual(added["new"]["cell_id"], "a2")
        self.assertEqual(added["new"]["source_table_id"], "NEW-PHY-0001")
        self.assertEqual(added["new"]["source_row_index"], 0)
        self.assertEqual(
            added["change_id"], "TABLE-ADDED-NEW-LT0001-R00000-C001"
        )

    def test_empty_deleted_table_is_available_without_cells(self):
        import document_compare.phase1.stages.s10_tables as s10

        compact = s10.compact_unmatched_table(_logical_table("old", []), "DELETED")
        self.assertEqual(compact["cell_changes"], [])
        self.assertEqual(compact["cell_content_status"], "available")

    def test_missing_cell_is_named_by_exactly_once_accounting(self):
        """A dropped physical cell is rejected, not silently omitted."""
        import document_compare.phase1.stages.s10_tables as s10
        from unittest import mock

        old = _logical_table("old", [[_cell(0, "21", cell_id="c1"), _cell(1, "keep", cell_id="c2")]])
        real_cells_by_column = s10.cells_by_column

        def truncated(row):
            cells = real_cells_by_column(row)
            return {0: cells[0]} if 0 in cells else cells

        with mock.patch.object(s10, "cells_by_column", truncated):
            with self.assertRaises(s10.ContractError) as raised:
                s10.compact_unmatched_table(old, "DELETED")
        self.assertIn("missing", str(raised.exception))


class IntegratedBoundedRetrievalValidationTest(unittest.TestCase):
    """T4 synthetic integration evidence; no provider or extraction inputs."""

    @staticmethod
    def _fragments(side: str, *, distinguishable: bool) -> list[dict]:
        elements = []
        for index in range(1, 14):
            label = f"portfolio{index:03d}" if distinguishable else "account"
            header = [
                _cell(
                    0,
                    f"{label} heading{index:03d}" if distinguishable else "Risk register",
                    kind="columnHeader",
                ),
                _cell(1, f"measure{index:03d}" if distinguishable else "Measure", kind="columnHeader"),
            ]
            changed = (
                f"{label} legacy{index:03d}" if side == "old"
                else f"{label} revised{index:03d}"
            )
            first_id = f"{side.upper()}-PHY-{index:03d}A"
            second_id = f"{side.upper()}-PHY-{index:03d}B"
            elements.append(_physical_table(
                index * 3 - 2,
                rows=[header, [_cell(0, label), _cell(1, changed)]],
                element_id=first_id,
                reading_order=index * 3 - 2,
                context_keys=[label] if distinguishable else [],
            ))
            elements.append(_physical_table(
                index * 3 - 1,
                rows=[header, [_cell(0, label), _cell(1, f"{label} retained{index:03d}")]],
                element_id=second_id,
                reading_order=index * 3 - 1,
                context_keys=[label] if distinguishable else [],
            ))
        return elements

    @classmethod
    def _logical_pair(cls, *, distinguishable: bool):
        import document_compare.phase1.stages.s10_tables as s10

        old, old_audit = s10.make_logical_tables(
            "old", cls._fragments("old", distinguishable=distinguishable), {}
        )
        new, new_audit = s10.make_logical_tables(
            "new", cls._fragments("new", distinguishable=distinguishable), {}
        )
        return old, new, old_audit, new_audit

    def test_distinguishable_repeated_header_fixture_retrieves_and_accepts_labels(self):
        import document_compare.phase1.stages.s10_tables as s10

        old, new, old_audit, new_audit = self._logical_pair(distinguishable=True)
        matches, unmatched_old, unmatched_new, audit = s10.match_logical_tables(old, new)
        pairs = {
            (item["old"]["logical_table_id"], item["new"]["logical_table_id"])
            for item in matches
        }
        expected = {(f"OLD-LT{index:04d}", f"NEW-LT{index:04d}") for index in range(1, 14)}

        self.assertEqual(len(old), 13)
        self.assertEqual(len(new), 13)
        self.assertTrue(all(table["source_fragment_count"] == 2 for table in old + new))
        self.assertTrue(all(table["omitted_repeated_header_rows"] == 1 for table in old + new))
        self.assertEqual(sum(item["joined"] for item in old_audit + new_audit), 26)
        self.assertEqual(pairs, expected, audit)
        self.assertEqual(unmatched_old, [])
        self.assertEqual(unmatched_new, [])
        self.assertEqual(audit["fuzzy_matches"], 13)
        self.assertEqual(audit["candidate_pairs_scored"], 13)
        self.assertEqual(audit["candidate_pairs_pruned_by_limit"], 0)
        self.assertEqual(audit["old_tables_with_incomplete_candidate_search"], [])
        self.assertEqual(audit["new_tables_with_incomplete_candidate_search"], [])

    def test_ambiguous_fixture_baseline_comparison_and_incomplete_uncertainty(self):
        import document_compare.phase1.stages.s10_tables as current

        old, new, _, _ = self._logical_pair(distinguishable=False)
        with tempfile.TemporaryDirectory(prefix="crr-s10-baseline-") as directory:
            baseline_path = Path(directory) / "s10_tables_prefx.py"
            completed = subprocess.run(
                ["git", "show", "HEAD:src/document_compare/phase1/stages/s10_tables.py"],
                cwd=str(ROOT), capture_output=True, text=True, check=True,
            )
            baseline_path.write_text(completed.stdout, encoding="utf-8")
            spec = importlib.util.spec_from_file_location("s10_prefx_validation", baseline_path)
            baseline = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(baseline)

            def measure(module):
                calls = 0
                original = module.table_similarity

                def counted(left, right):
                    nonlocal calls
                    calls += 1
                    return original(left, right)

                with mock.patch.object(module, "table_similarity", side_effect=counted):
                    start = time.perf_counter()
                    result = module.match_logical_tables(copy.deepcopy(old), copy.deepcopy(new))
                    elapsed = time.perf_counter() - start
                return result, calls, elapsed

            baseline_result, baseline_calls, _ = measure(baseline)
            current_result, current_calls, _ = measure(current)

        matches, unmatched_old, unmatched_new, audit = current_result
        groups, unresolved_old, unresolved_new = current.incomplete_search_unresolved_groups(
            unmatched_old, unmatched_new, audit, starting_group_index=1
        )
        self.assertEqual(baseline_calls, 169)
        self.assertEqual(current_calls, 144)
        self.assertEqual(len(matches), 0)
        self.assertEqual(audit["possible_residual_pairs"], 169)
        self.assertEqual(audit["candidate_pairs_scored"], 144)
        self.assertEqual(audit["candidate_pairs_pruned_by_limit"], 25)
        self.assertEqual(len(unresolved_old), 13)
        self.assertEqual(len(unresolved_new), 13)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["reason"], "incomplete_bounded_candidate_search_requires_review")
        self.assertTrue(groups[0]["excluded_candidate_evidence"])

    def test_stage10_entrypoint_preserves_incomplete_groups_through_routing_and_canonical(self):
        from document_compare.phase1.stages import s11_routing, s14_canonical_result

        with tempfile.TemporaryDirectory(prefix="crr-s10-t4-entrypoint-") as directory:
            root = Path(directory)
            output = root / "output"
            output.mkdir()
            old_elements = self._fragments("old", distinguishable=False)
            new_elements = self._fragments("new", distinguishable=False)
            for element in old_elements + new_elements:
                element["element_type"] = "table"
            old_document = {"schema_version": "0.3.0", "document": {"document_id": "OLD-SYN", "elements": old_elements}}
            new_document = {"schema_version": "0.3.0", "document": {"document_id": "NEW-SYN", "elements": new_elements}}
            movement = {
                "comparison_stage": s11_routing.EXPECTED_STEP9_STAGE,
                "old_document": {"document_id": "OLD-SYN"},
                "new_document": {"document_id": "NEW-SYN"},
                "modified": [], "modified_review": [], "duplicate_exact_review": [],
                "relocated": [], "deleted": [], "added": [], "movement_review": [],
                "structural_split_merge_review": [], "structural_boundary_review": [],
                "resegmentation_review": [],
            }
            for name, payload in (
                ("old_document_structured.json", old_document),
                ("new_document_structured.json", new_document),
                ("comparison_candidates_with_movement.json", movement),
            ):
                (output / name).write_text(json.dumps(payload), encoding="utf-8")
            completed = _run(
                f"import runpy; runpy.run_path({str(S10)!r}, run_name='__main__')",
                _env(root),
            )
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            tables = json.loads((output / "table_comparison_result.json").read_text(encoding="utf-8"))

        groups = tables["unresolved_table_groups"]
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual(group["reason"], "incomplete_bounded_candidate_search_requires_review")
        self.assertTrue(group["excluded_candidate_evidence"])
        self.assertEqual(len(group["old_tables"]), 13)
        self.assertEqual(len(group["new_tables"]), 13)
        self.assertTrue(all(table["source_table_ids"] for table in group["old_tables"] + group["new_tables"]))
        unresolved_old = {table["logical_table_id"] for table in group["old_tables"]}
        unresolved_new = {table["logical_table_id"] for table in group["new_tables"]}
        self.assertFalse(unresolved_old & {table["logical_table_id"] for table in tables["deleted_table_candidates"]})
        self.assertFalse(unresolved_new & {table["logical_table_id"] for table in tables["added_table_candidates"]})

        generic = s11_routing.build_generic_routing(movement, tables)
        records = s11_routing.build_domain_verification_records(
            generic["routes"], {"mode": "generic"}, None
        )
        routing = {
            "comparison_stage": "deterministic_review_routing", "schema_version": "1.0.0",
            **generic,
            "domain_profile": {"mode": "generic"},
            "domain_verification": {"enabled": False, "records": records, "status_counts": {}},
            "domain_verification_review": [], "statistics": {},
        }
        canonical = s14_canonical_result.build_comparison_result(movement, tables, routing)
        self.assertTrue(any(route["candidate_id"] == group["review_group_id"] and route["route"] == "UNCERTAIN_RESULT" for route in generic["table_routes"]))
        self.assertEqual(len(canonical["unresolved_table_reviews"]), 1)
        review = canonical["unresolved_table_reviews"][0]
        self.assertEqual(review["source_candidate_id"], group["review_group_id"])
        self.assertEqual(review["resolution"]["result_state"], "uncertain")
        self.assertEqual(review["evidence"]["reason"], group["reason"])
        self.assertTrue(review["evidence"]["old_tables"][0]["source_table_ids"])


if __name__ == "__main__":
    unittest.main()
