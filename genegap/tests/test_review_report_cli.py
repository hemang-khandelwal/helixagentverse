"""Exercise the human-facing review flow across separate command invocations."""
import json
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from genegap.schemas import Fact, Flag
from genegap.signoff import SignOffGate
from genegap.store import Store


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "review_report.py"


def run_cli(*args, user_input=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *(str(arg) for arg in args)],
        input=user_input, text=True, capture_output=True, timeout=15,
    )


@pytest.fixture
def handoff(tmp_path):
    db = tmp_path / "facts.sqlite"
    store = Store(db)
    fact = Fact(
        field="test_count", value=17, source="Synthetic software-test source",
        record_id="TEST", url="https://example.org/test",
    )
    store.put_fact(fact)
    store.db.close()
    flag = Flag(
        variant_id="TEST-VARIANT", claim="Synthetic finding for a software test.",
        evidence_tier=3, citations=["TEST-CITATION"], fact_ids=[fact.id],
        status="critic_passed",
    )
    draft = tmp_path / "draft.json"
    draft.write_text(json.dumps({"run_id": "cli-test", "flags": [flag.model_dump(mode="json")]}))
    return draft, db, flag, fact


def test_default_template_previews_but_cannot_finalize(tmp_path):
    db = tmp_path / "review.sqlite"
    preview = run_cli("--db", db)
    assert preview.returncode == 0
    assert "**DRAFT" in preview.stdout
    assert "No findings supplied." in preview.stdout
    output = tmp_path / "final.md"
    final = run_cli("--db", db, "--final", "--output", output)
    assert final.returncode == 1
    assert not output.exists()


def test_finalization_requires_human_input_and_can_resume_persisted_decisions(handoff, tmp_path):
    draft, db, flag, _ = handoff
    output = tmp_path / "final.md"
    blocked = run_cli(draft, "--db", db, "--final", "--output", output)
    assert blocked.returncode == 1
    assert "no audited human decision" in blocked.stderr
    assert not output.exists()

    reviewed = run_cli(
        draft, "--db", db, "--review", "--reviewer", "Test reviewer", "--final", "--output", output,
        user_input="approve\nChecked synthetic evidence.\n",
    )
    assert reviewed.returncode == 0, reviewed.stderr
    assert "**FINAL" in output.read_text()
    store = Store(db)
    decision = SignOffGate(store, "cli-test").decision_for(flag)
    assert decision["reviewer"] == "Test reviewer"
    assert decision["note"] == "Checked synthetic evidence."
    store.db.close()

    # The original handoff still says critic_passed. The stored human decision
    # must let a later process export this same reviewed version.
    resumed = run_cli(draft, "--db", db, "--final")
    assert resumed.returncode == 0, resumed.stderr
    assert "**FINAL" in resumed.stdout

    resumed_review = run_cli(
        draft, "--db", db, "--review", "--reviewer", "Another test reviewer", "--final",
        user_input="skip\n",
    )
    assert resumed_review.returncode == 0, resumed_review.stderr
    assert "**FINAL" in resumed_review.stdout
    assert "Test reviewer" in resumed_review.stdout


def test_annotation_and_interrupted_input_never_export_final(handoff, tmp_path):
    draft, db, _, _ = handoff
    output = tmp_path / "final.md"
    annotated = run_cli(
        draft, "--db", db, "--review", "--reviewer", "Test reviewer", "--final", "--output", output,
        user_input="annotate\nPlease check the evidence.\nskip\n",
    )
    assert annotated.returncode == 1
    assert "annotated but still needs" in annotated.stderr
    assert not output.exists()
    interrupted = run_cli(
        draft, "--db", db, "--review", "--reviewer", "Test reviewer", "--final", "--output", output,
        user_input="",
    )
    assert interrupted.returncode == 1
    assert "Review input ended" in interrupted.stderr
    assert not output.exists()


def test_approval_cannot_skip_the_upstream_critic(handoff, tmp_path):
    draft, db, flag, _ = handoff
    flag.status = "draft"
    draft.write_text(json.dumps({"run_id": "cli-test", "flags": [flag.model_dump(mode="json")]}))
    output = tmp_path / "reviewed.md"
    result = run_cli(
        draft, "--db", db, "--review", "--reviewer", "Test reviewer", "--final", "--output", output,
        user_input="approve\nTry without critic.\nreject\nCritic review is missing.\n",
    )
    assert result.returncode == 0, result.stderr
    assert "Approval requires critic_passed" in result.stderr
    assert "No findings approved." in output.read_text()
    store = Store(db)
    assert [step["payload"]["decision"] for step in store.steps("cli-test")] == ["reject"]
    store.db.close()


def test_resumed_export_blocks_changed_source_records(handoff, tmp_path):
    draft, db, flag, fact = handoff
    store = Store(db)
    SignOffGate(store, "cli-test").review(flag, "approve", "Test reviewer")
    store.put_fact(fact.model_copy(update={"url": "https://example.org/changed-source"}))
    store.db.close()
    output = tmp_path / "final.md"
    result = run_cli(draft, "--db", db, "--final", "--output", output)
    assert result.returncode == 1
    assert "content changed" in result.stderr
    assert not output.exists()


@pytest.mark.parametrize("destination_kind", [
    "draft", "database", "wal", "shm", "journal", "hardlink_draft", "hardlink_database", "symlink_draft",
])
def test_report_output_cannot_destroy_input_or_database_files(handoff, tmp_path, destination_kind):
    draft, db, _flag, fact = handoff
    original = draft.read_bytes()
    destinations = {
        "draft": draft,
        "database": db,
        "wal": Path(str(db) + "-wal"),
        "shm": Path(str(db) + "-shm"),
        "journal": Path(str(db) + "-journal"),
    }
    if destination_kind.startswith("hardlink_"):
        destination = tmp_path / "linked-output.md"
        source = draft if destination_kind == "hardlink_draft" else db
        os.link(source, destination)
    elif destination_kind == "symlink_draft":
        destination = tmp_path / "linked-output.md"
        destination.symlink_to(draft)
    else:
        destination = destinations[destination_kind]
    result = run_cli(draft, "--db", db, "--output", destination)
    assert result.returncode == 1
    assert "cannot overwrite" in result.stderr
    assert draft.read_bytes() == original
    assert db.read_bytes().startswith(b"SQLite format 3\x00")
    store = Store(db)
    assert store.get_fact(fact.id).value == fact.value
    assert store.steps("cli-test") == []
    store.db.close()


@pytest.mark.parametrize("status", ["draft", "rejected"])
def test_withdrawn_upstream_clearance_is_not_restored_from_an_old_approval(handoff, tmp_path, status):
    draft, db, flag, _fact = handoff
    store = Store(db)
    SignOffGate(store, "cli-test").review(flag, "approve", "Software-test reviewer")
    store.db.close()
    payload = json.loads(draft.read_text())
    payload["flags"][0]["status"] = status
    draft.write_text(json.dumps(payload))
    output = tmp_path / "final.md"
    result = run_cli(draft, "--db", db, "--final", "--output", output)
    assert result.returncode == 1
    assert not output.exists()


@pytest.mark.parametrize("reapprove", [False, True])
def test_review_rejects_changed_sources_until_redisplayed_and_explicitly_reapproved(
    handoff, tmp_path, monkeypatch, capsys, reapprove,
):
    draft, db, _flag, fact = handoff
    source = Store(db)
    original = fact.model_copy(update={"note": "Original source context shown to the reviewer."})
    source.put_fact(original)
    specification = importlib.util.spec_from_file_location("review_report_race_test", SCRIPT)
    module = importlib.util.module_from_spec(specification)
    monkeypatch.setitem(sys.modules, specification.name, module)
    specification.loader.exec_module(module)
    answers = iter(["approve", "Reviewed the original source.", *( ["approve", "Reviewed the redisplayed source."] if reapprove else ["skip"] )])
    changed = []
    originally_displayed = []

    def answer_after_display(prompt):
        if prompt.startswith("Decision") and not changed:
            originally_displayed.append(capsys.readouterr().err)
            source.put_fact(original.model_copy(update={"note": "Replacement source context."}))
            changed.append(True)
        return next(answers)

    monkeypatch.setattr(module, "_ask", answer_after_display)
    output = tmp_path / "final.md"
    try:
        status = module.main([
            str(draft), "--db", str(db), "--review", "--reviewer", "Software-test reviewer",
            "--final", "--output", str(output),
        ])
        displayed = capsys.readouterr().err
        assert "Original source context shown to the reviewer." in originally_displayed[0]
        assert "Replacement source context." not in originally_displayed[0]
        assert "Decision not recorded" in displayed
        assert "Replacement source context." in displayed
        decisions = [step["payload"] for step in source.steps("cli-test")]
        if reapprove:
            assert status == 0
            assert len(decisions) == 1
            assert decisions[0]["note"] == "Reviewed the redisplayed source."
            assert "Replacement source context." in output.read_text()
        else:
            assert status == 1
            assert decisions == []
            assert not output.exists()
    finally:
        source.db.close()
