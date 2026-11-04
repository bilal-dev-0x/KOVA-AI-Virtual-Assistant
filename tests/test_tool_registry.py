import pytest

from modules import tool_registry, tools  # noqa: F401  (registers the built-in tools)
from modules.tool_registry import CONTROLLED, Tool, tool


@pytest.fixture
def sample():
    @tool("Sample tool.", params={"name": "A name", "count": "How many", "mode": ("Mode", ["a", "b"])},
          level=CONTROLLED, confirm_text="do {name}", name="sample_tool")
    def sample_tool(name: str, count: int = 1, mode: str = "a", loud: bool = False):
        return f"{name} x{count}"
    yield sample_tool.tool
    tool_registry.REGISTRY.pop("sample_tool")


def test_schema_from_type_hints(sample):
    schema = sample.schema()
    assert schema["required"] == ["name"]
    assert schema["properties"]["count"] == {"type": "integer", "description": "How many"}
    assert schema["properties"]["mode"]["enum"] == ["a", "b"]
    assert schema["properties"]["loud"]["type"] == "boolean"


@pytest.mark.parametrize("args, clean", [
    ({"name": "x"}, {"name": "x"}),
    ({"name": "x", "count": 3.0}, {"name": "x", "count": 3}),
    ({"name": "x", "count": "2"}, {"name": "x", "count": 2}),
    ({"name": " x ", "mode": "b", "loud": True}, {"name": "x", "mode": "b", "loud": True}),
])
def test_valid_arguments(sample, args, clean):
    assert sample.validate(args) == (clean, None)


@pytest.mark.parametrize("args, error", [
    ({}, "missing argument: name"),
    ({"name": ""}, "must not be empty"),
    ({"name": "x", "count": 1.5}, "whole number"),
    ({"name": "x", "count": "lots"}, "must be a integer"),
    ({"name": "x", "mode": "c"}, "must be one of"),
    ({"name": "x", "loud": "yes"}, "true or false"),
    ({"name": {"nested": 1}}, "must be a string"),
    ({"name": "x", "command": "del *"}, "unknown argument"),
])
def test_invalid_arguments_rejected(sample, args, error):
    clean, problem = sample.validate(args)
    assert clean is None and error in problem


def test_describe_uses_confirm_text(sample):
    assert sample.describe({"name": "discord"}) == "do discord"


def test_builtin_tools_registered():
    names = {t.name for t in tool_registry.all_tools()}
    assert {"open_app", "close_app", "top_processes", "end_process", "web_search", "set_reminder"} <= names
    # The roadmap rule: no tool may run shell commands
    assert not any("shell" in n or "command" in n for n in names)


def test_every_builtin_schema_is_valid_json_schema():
    for t in tool_registry.all_tools():
        schema = t.schema()
        assert schema["type"] == "object"
        assert set(schema["required"]) <= set(schema["properties"])
        assert t.description


def test_levels_of_risky_tools():
    assert tool_registry.get("close_app").level == "controlled"
    assert tool_registry.get("end_process").level == "critical"
    assert tool_registry.get("open_app").level == "safe"


def test_plugins_load_and_broken_one_is_skipped(tmp_path):
    (tmp_path / "good.py").write_text(
        "from modules.tool_registry import tool\n"
        "@tool('Say hi.')\n"
        "def plugin_say_hi():\n"
        "    return 'hi'\n")
    (tmp_path / "broken.py").write_text("raise RuntimeError('oops')\n")
    (tmp_path / "_skipped.py").write_text("raise RuntimeError('should not load')\n")
    try:
        assert tool_registry.load_plugins(str(tmp_path)) == ["good.py"]
        assert tool_registry.get("plugin_say_hi").func() == "hi"
    finally:
        tool_registry.REGISTRY.pop("plugin_say_hi", None)


def test_example_dice_plugin():
    tool_registry.load_plugins()
    result = tool_registry.get("roll_dice").func(sides=6, count=3)
    assert result.success and "total" in result.message


def test_tool_dataclass_has_no_shell_escape():
    # Arguments are passed as keyword values, never formatted into a command line
    t = Tool("x", "d", lambda name: name, {"name": {"type": "string", "description": "", "required": True, "enum": None}})
    assert t.validate({"name": "a & del *"}) == ({"name": "a & del *"}, None)


def test_gpu_usage_from_counters():
    from modules import performance_monitor
    lines = [
        "pid_10_luid_0x0_0x1_phys_0_eng_0_engtype_3d|30.5",
        "pid_20_luid_0x0_0x1_phys_0_eng_0_engtype_3d|20",
        "pid_20_luid_0x0_0x1_phys_0_eng_3_engtype_videodecode|45",
        "garbage line",
    ]
    overall, per_pid = performance_monitor.gpu_usage(lines)
    assert overall == 50.5           # the busiest engine: 3D, 30.5 + 20
    assert per_pid == {10: 30.5, 20: 45}
