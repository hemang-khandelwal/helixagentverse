"""Drive real terminal review actions across process restarts."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from genegap.schemas import Fact, Flag
from genegap.store import Store


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "review" / "app.py"


def run(*args, commands=""):
    return subprocess.run(
        [sys.executable, str(APP), *(str(arg) for arg in args)],
        input=commands, capture_output=True, text=True, timeout=15,
    )


@pytest.fixture
def handoff(tmp_path):
    database = tmp_path / "review.sqlite"
    store = Store(database)
    fact = Fact(field="test_count", value=17, source="Synthetic software test", record_id="TEST")
    store.put_fact(fact)
    store.db.close()
    flag = Flag(
        variant_id="TEST-VARIANT", claim="Synthetic software-test finding.",
        evidence_tier=3, citations=["TEST-CITATION"], fact_ids=[fact.id], status="critic_passed",
    )
    path = tmp_path / "draft.json"
    path.write_text(json.dumps({"run_id": "cli-review", "flags": [flag.model_dump(mode="json")]}))
    return path, database


def test_empty_example_is_read_only_and_has_no_reviewed_findings(tmp_path):
    database = tmp_path / "review.sqlite"
    result = run("--db", database, "--list")
    assert result.returncode == 0, result.stderr
    assert "Task 6.3" in result.stdout
    assert "No findings supplied" in result.stdout
    store = Store(database)
    assert store.steps("report-template") == []
    store.db.close()


def test_reviewer_can_inspect_approve_export_and_resume(handoff, tmp_path):
    path, database = handoff
    destination = tmp_path / "final.md"
    result = run(
        path, "--db", database, "--reviewer", "Test reviewer",
        commands=f'view 1\napprove\nChecked synthetic source.\nback\nhistory\nfinal "{destination}"\nquit\n',
    )
    assert result.returncode == 0, result.stderr
    assert '"value": 17' in result.stdout
    assert "Recorded approve by Test reviewer" in result.stdout
    assert "Checked synthetic source." in result.stdout
    assert "**FINAL" in destination.read_text()
    assert "Test reviewer" in destination.read_text()
    resumed = run(path, "--db", database, "--list")
    assert resumed.returncode == 0
    assert "Approved" in resumed.stdout


def test_annotations_and_unfinished_decisions_do_not_grant_final_export(handoff, tmp_path):
    path, database = handoff
    destination = tmp_path / "final.md"
    result = run(
        path, "--db", database, "--reviewer", "Test reviewer",
        commands=f'view 1\nannotate\nNeed another source.\nback\nfinal "{destination}"\nquit\n',
    )
    assert result.returncode == 0
    assert "annotated but still needs" in result.stdout
    assert not destination.exists()
    assert "Annotated" in run(path, "--db", database, "--list").stdout


def test_no_auto_approval_on_end_of_input(handoff):
    path, database = handoff
    result = run(path, "--db", database, "--reviewer", "Test reviewer", commands="view 1\napprove\n")
    assert result.returncode == 0
    assert "Review closed" in result.stdout
    store = Store(database)
    assert store.steps("cli-review") == []
    store.db.close()


def test_draft_approval_is_blocked_without_critic_clearance(handoff, tmp_path):
    path, database = handoff
    payload = json.loads(path.read_text())
    payload["flags"][0]["status"] = "draft"
    path.write_text(json.dumps(payload))
    destination = tmp_path / "final.md"
    result = run(
        path, "--db", database, "--reviewer", "Test reviewer",
        commands=f'view 1\napprove\nTry before critic.\nback\nfinal "{destination}"\nquit\n',
    )
    assert "Approval requires critic_passed" in result.stdout
    assert "no audited human decision" in result.stdout
    assert not destination.exists()


def test_bad_handoff_exits_without_claiming_a_review(handoff):
    path, database = handoff
    path.write_text('{"flags": []}')
    result = run(path, "--db", database, "--list")
    assert result.returncode == 1
    assert "Review could not open" in result.stderr
