from pathlib import Path


def test_source_distribution_contains_required_entrypoints():
    root = Path(__file__).parents[1]
    for relative in (
        "install.bat",
        "start.bat",
        "test-windows.bat",
        "README.md",
        "slack-app-manifest.yaml",
        "docs/WINDOWS-MT4-TEST.md",
        ".gitattributes",
        "SECURITY.md",
    ):
        assert (root / relative).is_file()


def test_socket_mode_manifest_does_not_require_http_url():
    import yaml

    root = Path(__file__).parents[1]
    manifest = yaml.safe_load((root / "slack-app-manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["settings"]["socket_mode_enabled"] is True
    command = manifest["features"]["slash_commands"][0]
    assert command["command"] == "/mt4"
    assert "url" not in command
    assert "commands" in manifest["oauth_config"]["scopes"]["bot"]
    assert "chat:write" in manifest["oauth_config"]["scopes"]["bot"]


def test_windows_batch_files_use_crlf():
    root = Path(__file__).parents[1]
    for name in ("install.bat", "start.bat", "test-windows.bat"):
        data = (root / name).read_bytes()
        assert b"\r\n" in data
        assert b"\n" not in data.replace(b"\r\n", b"")


def test_local_runtime_data_paths_are_ignored_but_examples_are_not():
    import subprocess

    root = Path(__file__).parents[1]
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", ".local-data/secrets.json"],
        cwd=root,
        check=False,
    )
    temporary = subprocess.run(
        ["git", "check-ignore", "-q", ".local-data/.secrets.json.abc.tmp"],
        cwd=root,
        check=False,
    )
    swap = subprocess.run(
        ["git", "check-ignore", "-q", "config/examples/settings.json.swp"],
        cwd=root,
        check=False,
    )
    example = subprocess.run(
        ["git", "check-ignore", "-q", "config/examples/accounts.example.json"],
        cwd=root,
        check=False,
    )
    assert ignored.returncode == 0
    assert temporary.returncode == 0
    assert swap.returncode == 0
    assert example.returncode != 0
    reports = subprocess.run(
        ["git", "check-ignore", "-q", ".local-data/reports/run/report.html"],
        cwd=root,
        check=False,
    )
    assert reports.returncode == 0
