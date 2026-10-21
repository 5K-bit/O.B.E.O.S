from blackfong_ecosystem.workspace_command_center import STALE_AFTER_SECONDS, age_text, classify_state

NOW = 1_000_000.0


def test_missing_state_is_unknown_not_ok():
    assert classify_state(None, NOW, now=NOW) == ("unknown", "state_unknown")
    assert classify_state("", NOW, now=NOW) == ("unknown", "state_unknown")


def test_fresh_ready_is_ok():
    assert classify_state("ready", NOW - 5, now=NOW) == ("ready", "state_ok")


def test_stale_ready_is_never_ok():
    label, token = classify_state("ready", NOW - STALE_AFTER_SECONDS - 1, now=NOW)
    assert token == "state_unknown"
    assert label.startswith("stale")


def test_offline_and_degraded_are_flagged():
    assert classify_state("offline", NOW, now=NOW)[1] == "state_bad"
    assert classify_state("degraded", NOW, now=NOW)[1] == "state_warn"


def test_unrecognised_state_is_unknown():
    assert classify_state("banana", NOW, now=NOW)[1] == "state_unknown"


def test_age_text():
    assert age_text(None) == "no timestamp"
    assert age_text(NOW - 30, now=NOW) == "30s ago"
    assert age_text(NOW - 600, now=NOW) == "10m ago"
