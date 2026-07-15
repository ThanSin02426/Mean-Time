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
