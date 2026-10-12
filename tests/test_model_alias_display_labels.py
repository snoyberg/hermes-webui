"""Opaque alias routes share target display labels, never target routing state."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="node is required")

DRIVER = r"""
const fs = require('fs');
const ui = fs.readFileSync(process.argv[1], 'utf8');
const cmds = fs.readFileSync(process.argv[2], 'utf8');
const payload = JSON.parse(process.argv[3]);
const _dynamicModelLabels = payload.labels || {};
const start = ui.indexOf('function _fmtOllamaLabel(');
const end = ui.indexOf('\nfunction _gatewayProviderName(', start);
eval(ui.slice(start, end));
const resolveStart = cmds.indexOf('function _resolveModelAliasTarget(');
const resolveEnd = cmds.indexOf('\nfunction ', resolveStart + 1);
eval(cmds.slice(resolveStart, resolveEnd));
const target = payload.target;
const before = JSON.stringify(target);
const route = _resolveModelAliasTarget([], {}, target);
const initial = JSON.stringify(route);
const aliasLabel = getModelLabel(route.value);
console.log(JSON.stringify({
  aliasLabel, targetLabel: getModelLabel(target.model), route,
  unchanged: before === JSON.stringify(target) && initial === JSON.stringify(route),
}));
"""


def run_labels(model, labels=None):
    payload = {
        "target": {"model": model, "provider": "openai-codex", "route_provider": "model-alias-abc123"},
        "labels": labels or {},
    }
    result = subprocess.run(
        [NODE, "-e", DRIVER, str(ROOT / "static/ui.js"), str(ROOT / "static/commands.js"), json.dumps(payload)],
        capture_output=True, text=True, check=True, timeout=15,
    )
    return json.loads(result.stdout)


def test_alias_uses_catalog_target_label_without_rewriting_routing():
    result = run_labels("gpt-6.1-sol", {"gpt-6.1-sol": "GPT-6.1 Sol"})
    assert result["aliasLabel"] == result["targetLabel"] == "GPT-6.1 Sol"
    assert result["route"] == {
        "value": "@model-alias-abc123:gpt-6.1-sol", "provider": "model-alias-abc123",
    }
    assert result["unchanged"]


@pytest.mark.parametrize("model,labels,expected", [
    ("vendor/novel-model", {"vendor/novel-model": "Novel Model"}, "Novel Model"),
    ("anthropic/claude-sonnet-4.6", {}, "Sonnet 4.6"),
    ("ollamacloud/qwen3.5:397b", {"ollamacloud/qwen3.5:397b": "Qwen 397B"}, "Qwen 397B"),
    ("qwen3.5:397b", {}, "Qwen3.5 (397B)"),
    ("gpt://folder/deepseek-v4-flash/latest", {}, "deepseek-v4-flash"),
    ("vendor/unknown/path:tag", {}, "unknown/path:tag"),
    ("unknown-model", {}, "unknown-model"),
    ("gpt-6.1-sol", {"gpt-6.1-sol": "GPT-6.1 Sol", "@model-alias-abc123:gpt-6.1-sol": "Explicit Alias Label"}, "Explicit Alias Label"),
])
def test_alias_display_pipeline_preserves_paths_tags_and_catalog_precedence(model, labels, expected):
    result = run_labels(model, labels)
    assert result["aliasLabel"] == expected
    if not any(key.startswith("@model-alias-") for key in labels):
        assert result["aliasLabel"] == result["targetLabel"]
    assert result["route"]["value"] == f"@model-alias-abc123:{model}"
    assert result["route"]["provider"] == "model-alias-abc123"
    assert result["unchanged"]
