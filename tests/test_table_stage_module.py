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

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
S10 = ROOT / "crr_compare" / "stages" / "s10_tables.py"
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
module = importlib.import_module("crr_compare.stages.s10_tables")
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
s10 = importlib.import_module("crr_compare.stages.s10_tables")

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
        import crr_compare.stages.s10_tables as s10

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
        import crr_compare.stages.s10_tables as s10

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
        import crr_compare.stages.s10_tables as s10

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
        import crr_compare.stages.s10_tables as s10

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
        import crr_compare.stages.s10_tables as s10

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
        import crr_compare.stages.s10_tables as s10

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
        import crr_compare.stages.s10_tables as s10

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
        import crr_compare.stages.s10_tables as s10

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
        import crr_compare.stages.s10_tables as s10

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
        import crr_compare.stages.s10_tables as s10

        table = _logical_table(
            "new", [[_cell(0, "99", cell_id="c1"), _cell(1, "keep", cell_id="c2")]]
        )
        accounted = [s10.compact_cell(cell) for cell in table["rows"][0]["cells"]]
        count = s10.require_exactly_once_cell_accounting("new", table, accounted, 1)
        self.assertEqual(count, 2)


class UnmatchedTableContentTest(unittest.TestCase):
    """A deleted/added table must keep its real cell content, not metadata only.

    ``compact_unmatched_table`` is the one-sided counterpart of
    ``compare_table_match``: every logical-row cell becomes exactly one
    one-sided cell change through the same ``compare_cell`` path and the same
    exactly-once accounting, so physical cell text, cell ids and source
    locations survive into the redline in the correct old/new orientation.
    """

    def test_deleted_table_keeps_numeric_cell_content_and_source_locations(self):
        import crr_compare.stages.s10_tables as s10

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
        import crr_compare.stages.s10_tables as s10

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
        import crr_compare.stages.s10_tables as s10

        compact = s10.compact_unmatched_table(_logical_table("old", []), "DELETED")
        self.assertEqual(compact["cell_changes"], [])
        self.assertEqual(compact["cell_content_status"], "available")

    def test_missing_cell_is_named_by_exactly_once_accounting(self):
        """A dropped physical cell is rejected, not silently omitted."""
        import crr_compare.stages.s10_tables as s10
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


if __name__ == "__main__":
    unittest.main()
