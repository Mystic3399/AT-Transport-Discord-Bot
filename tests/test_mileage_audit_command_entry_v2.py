"""Regression checks for the existing exclamation-mark mileage audit workflow."""
import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "bot.py"


def audit_function():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    return next(node for node in tree.body
                if isinstance(node, ast.AsyncFunctionDef) and node.name == "mileage_audit")


def test_prefix_audit_retains_batch_confirm_and_configured_sources():
    function = audit_function()
    text = ast.get_source_segment(SOURCE.read_text(encoding="utf-8"), function)
    assert 'str(message_limit).upper()==\'CONFIRM\'' in text
    assert "MILEAGE_AUDIT_CHANNEL_IDS" in text
    assert "create_mileage_audit_preview" in text
    assert "ctx.invoke(mileage_apply,confirmation='CONFIRM')" in text


def test_prefix_audit_can_review_held_trucky_without_running_registration():
    function = audit_function()
    text = ast.get_source_segment(SOURCE.read_text(encoding="utf-8"), function)
    assert 'str(message_limit).upper() == "HELD"' in text
    assert "ctx.invoke(reviewed_held_mileage" in text
    assert "!mileageaudit HELD @Driver JOB_ID CONFIRM" in text
    assert "/register" in text  # The comment states no registration call is made.
    assert "register_driver(" not in text
    assert "begin_registration(" not in text


def test_legacy_four_source_channels_are_not_modified():
    content = SOURCE.read_text(encoding="utf-8")
    assert '1549420615868481656: "job-tracker"' in content
    assert '1541473875773362276: "ets2-logbook"' in content
    assert '1541473916147605534: "ats-logbook"' in content
    assert '1541473951899852800: "trucky-tracker"' in content
