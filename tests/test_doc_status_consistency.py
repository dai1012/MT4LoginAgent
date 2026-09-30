"""The public documentation must not drift from what has actually been verified.

A reader who installs this and follows the README should be told the truth about which
capabilities were accepted by hand on real Windows hardware and which were only covered
by the test suite. These tests pin the distinctions that matter, and deliberately do not
pin whole paragraphs: wording may be reworded, the status may not silently change.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS = REPO_ROOT / "docs"
LANGUAGES = ("README.md", "README.en.md", "README.ja.md")

# The one authenticated-title example the project publishes. It is a Rakuten Demo
# title with the broker's numeric prefix left as a positional wildcard, never a real
# account number.
RAKUTEN_SUCCESS_REGEX = r"^[0-9]+: RakutenSecurities-Demo - デモ口座 - Rakuten Securities, Inc\.$"

VERIFIED = "✅"
OPTIONAL = "⚠️"


def read(name: str) -> str:
    return (REPO_ROOT / name).read_text(encoding="utf-8")


MATRIX_HEADINGS = ("## 验证矩阵", "## Verification matrix", "## 検証マトリクス")


def matrix(language: str) -> str:
    """Return only the verification-matrix block of one edition.

    Scoping matters: the same words appear in the limitations table, where no status
    marker belongs, so a search over the whole file would assert the wrong thing.
    """
    text = read(language)
    start = next((text.index(h) for h in MATRIX_HEADINGS if h in text), -1)
    assert start >= 0, f"{language}: no verification matrix section"
    end = text.index("\n## 1.", start)
    return text[start:end]


def matrix_rows(language: str) -> list[str]:
    return [line for line in matrix(language).splitlines() if line.startswith("| ")]


def matrix_row(language: str, *needles: str) -> str:
    """Return the single matrix row mentioning every needle."""
    found = [line for line in matrix_rows(language) if all(n in line for n in needles)]
    assert len(found) == 1, (
        f"{language}: expected exactly one row for {needles}, got {len(found)}"
    )
    return found[0]


def markdown_files() -> list[Path]:
    files = [REPO_ROOT / name for name in LANGUAGES]
    files += [REPO_ROOT / "SECURITY.md", REPO_ROOT / "LICENSE"]
    files += sorted(DOCS.glob("*.md"))
    return [path for path in files if path.exists()]


# The Slack path to a real MT4 login is verified; Group and a live OTP are not.


@pytest.mark.parametrize("language", LANGUAGES)
def test_single_account_slack_e2e_is_reported_as_verified(language):
    row = matrix_row(language, "Slack", "E2E")
    assert VERIFIED in row, f"{language}: single-account Slack E2E must be verified: {row}"


@pytest.mark.parametrize("language", LANGUAGES)
def test_group_login_stays_optional_and_not_real_world_validated(language):
    row = matrix_row(language, "Group")
    assert OPTIONAL in row, f"{language}: Group must stay optional: {row}"
    assert VERIFIED not in row, f"{language}: Group must not be claimed as verified: {row}"


@pytest.mark.parametrize("language", LANGUAGES)
def test_production_live_account_is_not_claimed_as_verified(language):
    rows = [line for line in matrix_rows(language) if "Production" in line or "production" in line]
    assert rows, f"{language}: no row addresses the production/live account question"
    for row in rows:
        assert OPTIONAL in row, f"{language}: production must be marked optional: {row}"


@pytest.mark.parametrize("language", LANGUAGES)
def test_other_brokers_are_not_claimed_as_verified(language):
    tokens = ("roker", "証券会社", "券商", "ブローカー")
    rows = [line for line in matrix_rows(language) if any(k in line for k in tokens)]
    assert rows, f"{language}: no row addresses other brokers"
    for row in rows:
        assert VERIFIED not in row, f"{language}: other brokers must not be verified: {row}"
        assert OPTIONAL in row, f"{language}: other brokers must be marked optional: {row}"


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_language_uses_the_same_status_legend(language):
    text = read(language)
    for marker in (VERIFIED, "🧪", OPTIONAL):
        assert marker in text, f"{language}: status marker {marker!r} missing"


# The three editions must agree on substance, not merely exist.


def test_the_three_editions_cover_the_same_rows_in_the_same_order():
    """The labels are translated, so only the shape and the verdicts are comparable.

    Row N must carry the same status in all three editions, otherwise one language
    would tell a reader that a capability is verified while another says otherwise.
    """
    verdicts = {name: [_verdict(row) for row in matrix_rows(name)] for name in LANGUAGES}
    chinese, english, japanese = (verdicts[name] for name in LANGUAGES)
    assert chinese == english == japanese, (
        "the three editions disagree about a capability status:\n"
        f"  zh={chinese}\n  en={english}\n  ja={japanese}"
    )
    assert len(chinese) >= 15, "the matrix should cover the whole surface, not a token table"


def _verdict(row: str) -> str:
    """Map a matrix row onto the single status it asserts."""
    if VERIFIED in row:
        return "verified"
    if OPTIONAL in row:
        return "optional"
    if "🧪" in row:
        return "tested"
    return "unknown"


def test_all_three_editions_explain_the_same_three_boundaries():
    for language in LANGUAGES:
        text = read(language)
        # The Win32 inspector is an adaptation helper, not a login prerequisite.
        assert "MT4_DISCOVERY_READY" in text, f"{language}: inspector scope missing"
        # A credential is opaque, not a formatted OTP.
        assert "credential" in text.lower() or "凭据" in text or "認証情報" in text
        # The Slack ACL and the same-workspace boundary, in this edition's language.
        boundary = (
            "Workspace",
            "workspace",
            "ワークスペース",
            "Slack 成员目录",
            "Slack メンバー",
        )
        assert any(token in text for token in boundary), (
            f"{language}: the same-workspace boundary is not stated"
        )


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_published_success_regex_is_identical_in_every_language(language):
    text = read(language)
    assert RAKUTEN_SUCCESS_REGEX in text, f"{language}: published success regex differs"
    # The real account number must never be spelled out.
    assert not re.search(r"\b\d{6,10}:\s*Rakuten", text), (
        f"{language}: a concrete account number leaked into the success regex"
    )


# Detect Win32 scope, opaque credential, and the native verifier must be documented.


@pytest.mark.parametrize("language", LANGUAGES)
def test_detect_win32_is_documented_as_adaptation_only(language):
    text = read(language)
    assert "Detect Win32" in text
    for phrase in ("新券商", "New broker", "新しいブローカー"):
        if phrase in text:
            break
    else:
        pytest.fail(f"{language}: Detect Win32 is not scoped to a new broker/build")


def test_the_guide_documents_the_native_success_verification():
    text = (DOCS / "WINDOWS-TEST-GUIDE.md").read_text(encoding="utf-8")
    assert "原生" in text and "PID" in text, "the guide must state the native, PID-scoped check"
    assert "不再依赖 UIA" in text or "no longer" in (DOCS / "TROUBLESHOOTING.md").read_text(
        encoding="utf-8"
    )


def test_troubleshooting_states_the_bitness_warning_is_non_blocking():
    text = (DOCS / "TROUBLESHOOTING.md").read_text(encoding="utf-8")
    assert "32-bit application should be automated using 32-bit Python" in text
    assert "non-blocking notice" in text
    assert "You do not need a 32-bit Python" in text


# Nothing stale may come back.


# The button in the Web Admin is literally named "Start Full Slack E2E", so the guide
# has to be allowed to quote it. What must not survive is a *status claim* under that
# name, so a table row or a status line is what gets checked here.
STATUS_CLAIM = re.compile(r"^\s*\|")
VERDICT_WORD = ("未验证", "未验收", "not verified", "not real-world", "未検証", "未受入")


def test_no_document_keeps_the_old_full_slack_e2e_status():
    for path in markdown_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "Full Slack E2E" not in line:
                continue
            is_row = bool(STATUS_CLAIM.match(line))
            asserts_status = any(word in line for word in VERDICT_WORD)
            if not (is_row or asserts_status):
                continue
            assert any(
                marker in line
                for marker in ("旧", "old term", "旧称", "split", "分开", "分けて")
            ), f"{path.name}:{number} still reports a 'Full Slack E2E' status: {line}"


def test_the_guide_quotes_the_real_button_label():
    """Docs and UI must not drift: the label referenced must exist in the page."""
    page = (REPO_ROOT / "src" / "app" / "web" / "templates" / "index.html").read_text(
        encoding="utf-8"
    )
    guide = (DOCS / "WINDOWS-TEST-GUIDE.md").read_text(encoding="utf-8")
    quoted = set(re.findall(r"\*\*(Start [^*]+)\*\*", guide))
    assert quoted, "the guide should still name the buttons it tells you to press"
    for label in quoted:
        bare = label.split(" to ")[0].split(" and ")[0].strip()
        assert bare in page, f"the guide names a button the page does not have: {bare!r}"


def test_no_document_reports_a_stale_test_count_or_commit():
    for path in markdown_files():
        text = path.read_text(encoding="utf-8")
        for marker in ("105 passed", "aa47fc5", "fc12af2", "caf1f66", "READY_FOR_WINDOWS_TEST"):
            assert marker not in text, f"{path.name} still carries stale {marker!r}"


def test_no_document_claims_the_inspector_is_only_unit_tested():
    for path in markdown_files():
        text = path.read_text(encoding="utf-8")
        assert "IMPLEMENTATION-REPORT" not in text, f"{path.name} still links the removed report"
        for line in text.splitlines():
            if "Detect Win32" in line and line.startswith("|"):
                assert "仅 unit tested" not in line, (
                    f"{path.name}: inspector still marked unverified"
                )


def test_manifest_usage_hint_is_not_otp_only():
    manifest = (REPO_ROOT / "slack-app-manifest.yaml").read_text(encoding="utf-8")
    assert "<credential>" in manifest
    assert "<OTP>" not in manifest


def test_every_markdown_link_resolves():
    broken = []
    for path in markdown_files():
        for _, target in re.findall(
            r"\[([^\]]*)\]\(([^)]+)\)", path.read_text(encoding="utf-8")
        ):
            candidate = target.split("#")[0].strip()
            if not candidate or candidate.startswith(("http://", "https://", "mailto:")):
                continue
            if not (path.parent / candidate).resolve().exists():
                broken.append(f"{path.name} -> {target}")
    assert not broken, "broken relative links:\n  " + "\n  ".join(broken)
