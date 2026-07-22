from assistant.automation.shell import SafeShell


class _FakeConfig:
    def __init__(self, allowlist):
        self.shell_allowlist = allowlist


def test_allowed_command_runs_and_captures_output():
    shell = SafeShell(_FakeConfig(["echo"]))
    result = shell.run("echo hello-world")
    assert "hello-world" in result


def test_disallowed_command_is_rejected_without_running():
    shell = SafeShell(_FakeConfig(["echo"]))
    result = shell.run("whoami")
    assert "isn't in the allowed command list" in result


def test_force_bypasses_allowlist():
    shell = SafeShell(_FakeConfig([]))
    result = shell.run("echo forced", force=True)
    assert "forced" in result


def test_empty_command_is_rejected():
    shell = SafeShell(_FakeConfig(["echo"]))
    assert shell.run("   ") == "No command given."
