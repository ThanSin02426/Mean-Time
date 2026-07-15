from pathlib import Path

import yaml


def load_yaml(path: Path):
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_publisher_workflow_has_only_requested_manual_inputs():
    path = Path(__file__).resolve().parents[2] / ".github/workflows/autoshorts-publisher.yml"
    data = load_yaml(path)
    dispatch = data["on"]["workflow_dispatch"]
    assert set(dispatch["inputs"]) == {"topic", "publish"}
    assert data["on"]["schedule"][0]["cron"] == "30 3 * * *"
    assert data["on"]["schedule"][1]["cron"] == "0 14 * * *"
    assert data["permissions"]["contents"] == "write"
    assert "concurrency" in data


def test_test_workflow_exists_and_runs_pytest():
    path = Path(__file__).resolve().parents[2] / ".github/workflows/autoshorts-tests.yml"
    text = path.read_text(encoding="utf-8")
    load_yaml(path)
    assert "pytest" in text
    assert "compileall" in text


def test_workflows_use_node24_action_generations():
    root = Path(__file__).resolve().parents[2]
    publisher = (root / ".github/workflows/autoshorts-publisher.yml").read_text(encoding="utf-8")
    tests = (root / ".github/workflows/autoshorts-tests.yml").read_text(encoding="utf-8")
    combined = publisher + "\n" + tests
    assert "actions/checkout@v4" not in combined
    assert "actions/setup-python@v5" not in combined
    assert "actions/cache@v4" not in combined
    assert "actions/upload-artifact@v4" not in combined
    assert "actions/checkout@v5" in combined
    assert "actions/setup-python@v6" in combined


def test_publisher_summary_includes_subtitle_diagnostics():
    path = Path(__file__).resolve().parents[2] / ".github/workflows/autoshorts-publisher.yml"
    text = path.read_text(encoding="utf-8")
    assert "Maximum active-speech caption gap" in text
    assert "Subtitle final alignment" in text
