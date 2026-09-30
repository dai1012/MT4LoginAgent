"""The three README editions, their language navigation, and the screenshot wiring.

These assertions pin the facts that must hold for a reader switching between the
editions: that a file is actually written in its declared language, that the language
navigation points at all three files, that the non-affiliation disclaimer survived
translation, and that the screenshot references point at the agreed paths.

They deliberately do not pin whole paragraphs, so a wording change in one language
does not break the suite.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CODE_SPAN = re.compile(r"`[^`]*`|```.*?```", re.S)


def prose_only(text: str) -> str:
    """Drop inline code and fenced blocks before judging a document's language.

    A verbatim sample of a broker's window title can contain Japanese whatever the
    prose language is; that is a code sample, not a translation error.
    """
    return CODE_SPAN.sub(" ", text)



REPO_ROOT = Path(__file__).resolve().parent.parent
IMAGES_DIR = REPO_ROOT / "docs" / "images"

# The four screenshots the main README body is allowed to show.
README_IMAGES = (
    "docs/images/dashboard_overview.webp",
    "docs/images/accounts_list.webp",
    "docs/images/windows_test_overview.webp",
    "docs/images/slack_private.webp",
)
# The three that belong next to the topic document instead.
DIVERTED_IMAGES = {
    "docs/SLACK-SETUP.md": ("docs/images/slack_settings.webp",),
    "docs/NEW-MT4-ADAPTER.md": (
        "docs/images/account_config_basic.webp",
        "docs/images/account_config_win32.webp",
    ),
}

KANA = re.compile(r"[\u3040-\u30ff]")
HAN = re.compile(r"[\u4e00-\u9fff]")
# A token that must never be translated or reformatted.
LITERAL_TOKENS = ("WINDOWS_REAL_TEST_REQUIRED", "xapp-", "xoxb-")


def read(name: str) -> str:
    return (REPO_ROOT / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["README.md", "README.ja.md", "README.en.md"])
def test_all_three_readme_editions_exist(name: str) -> None:
    assert (REPO_ROOT / name).is_file(), f"{name} is missing"


def test_readme_is_simplified_chinese_by_default() -> None:
    text = read("README.md")
    assert HAN.search(text), "README.md must be written in Chinese"
    # Simplified-specific forms, not just any Han character.
    assert "简体中文" in text
    assert "远端" in text or "登录" in text
    # The default edition must not be an English or Japanese document. Verbatim code
    # samples are excluded: the published success regex carries a Japanese broker
    # title on purpose, and that is a code sample rather than a translation error.
    assert not KANA.search(prose_only(text)), "README.md prose must not contain kana"


def test_english_edition_contains_no_japanese_or_chinese() -> None:
    raw = read("README.en.md")
    # The language navigation necessarily names the other two editions in their own
    # scripts, so the header is excluded before the body is checked.
    body = "\n".join(raw.splitlines()[8:])
    prose = prose_only(body)
    assert not KANA.search(prose), "README.en.md prose must not contain kana"
    assert not HAN.search(prose), "README.en.md prose must not contain Han characters"
    assert "Slack" in raw


def test_japanese_edition_contains_kana() -> None:
    assert KANA.search(read("README.ja.md")), "README.ja.md must be written in Japanese"


@pytest.mark.parametrize("name", ["README.md", "README.ja.md", "README.en.md"])
def test_each_edition_navigates_to_all_three(name: str) -> None:
    text = read(name)
    head = "\n".join(text.splitlines()[:8])
    for target in ("README.md", "README.ja.md", "README.en.md"):
        assert f"]({target})" in head, f"{name} must link to {target} in its header"
    # The current language appears in the header, so a reader knows which one is open.
    expected = {"README.md": "简体中文", "README.ja.md": "日本語", "README.en.md": "English"}[name]
    assert expected in head, f"{name} must name itself in the header"


@pytest.mark.parametrize("name", ["README.md", "README.ja.md", "README.en.md"])
def test_non_affiliation_disclaimer_survives_in_every_language(name: str) -> None:
    text = read(name)
    assert "Rakuten" in text
    # Each edition must carry the denial in its own language. Checking a Japanese
    # phrase against the English file would prove nothing.
    denial = {
        "README.md": ("不是 Rakuten 的官方产品", "无任何关联"),
        "README.ja.md": ("Rakuten の公式製品ではなく", "推奨はまったくありません"),
        "README.en.md": ("not an official Rakuten", "no affiliation"),
    }[name]
    for phrase in denial:
        assert phrase in text, f"{name} is missing the disclaimer phrase: {phrase}"


@pytest.mark.parametrize("name", ["README.md", "README.ja.md", "README.en.md"])
def test_literal_tokens_are_not_translated(name: str) -> None:
    text = read(name)
    for token in LITERAL_TOKENS:
        assert token in text, f"{token} must stay verbatim in {name}"


@pytest.mark.parametrize("name", ["README.md", "README.ja.md", "README.en.md"])
def test_each_edition_references_exactly_the_four_core_screenshots(name: str) -> None:
    text = read(name)
    found = set(re.findall(r"docs/images/([A-Za-z0-9_]+\.webp)", text))
    assert found == {Path(item).name for item in README_IMAGES}, found


def test_the_three_topic_screenshots_live_in_their_topic_documents() -> None:
    for doc, images in DIVERTED_IMAGES.items():
        text = read(doc)
        for image in images:
            # A document inside docs/ must spell the path relative to itself, or the
            # link resolves to docs/docs/images/... and breaks on the rendered page.
            relative = image.removeprefix("docs/")
            assert f"]({relative})" in text, f"{image} must be referenced from {doc}"


def test_no_document_references_an_undeclared_screenshot() -> None:
    """Only the seven agreed images may ever be linked."""
    allowed = set(README_IMAGES)
    for images in DIVERTED_IMAGES.values():
        allowed.update(images)
    docs = [REPO_ROOT / "README.md", REPO_ROOT / "README.ja.md", REPO_ROOT / "README.en.md"]
    docs += [REPO_ROOT / item for item in DIVERTED_IMAGES]
    # A README at the repository root spells docs/images/..., a document inside docs/
    # spells images/...; both are the same file and both must be caught here.
    pattern = r"(?:docs/)?images/([A-Za-z0-9_]+\.webp)"
    for path in docs:
        for image in re.findall(pattern, path.read_text(encoding="utf-8")):
            assert f"docs/images/{image}" in allowed, f"{path.name} references {image}"


def test_every_relative_link_in_the_docs_resolves() -> None:
    """Catch a link spelled relative to the repository root inside docs/.

    Such a link is not missing on disk, so a check that only compares the target
    string against a pending list would miss it entirely.
    """
    docs = [
        REPO_ROOT / item
        for item in ("README.md", "README.ja.md", "README.en.md", "SECURITY.md")
    ]
    docs += sorted((REPO_ROOT / "docs").glob("*.md"))
    link = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
    for path in docs:
        for _label, target in link.findall(path.read_text(encoding="utf-8")):
            target = target.split("#")[0].strip()
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            resolved = (path.parent / target).resolve()
            assert resolved.exists(), f"{path.name} -> {target} does not resolve"


@pytest.mark.parametrize("name", ["README.md", "README.ja.md", "README.en.md"])
def test_each_edition_reaches_the_document_index(name: str) -> None:
    text = read(name)
    for doc in (
        "docs/WINDOWS-TEST-GUIDE.md",
        "docs/SLACK-SETUP.md",
        "docs/NEW-MT4-ADAPTER.md",
        "docs/TROUBLESHOOTING.md",
        "SECURITY.md",
    ):
        assert f"]({doc})" in text, f"{name} must link to {doc}"


def test_images_directory_is_present() -> None:
    assert IMAGES_DIR.is_dir(), "docs/images must exist for the screenshots"


@pytest.mark.parametrize("image", sorted(README_IMAGES + sum(DIVERTED_IMAGES.values(), ())))
def test_every_referenced_screenshot_exists_and_is_not_empty(image: str) -> None:
    path = REPO_ROOT / image
    assert path.is_file(), f"{image} is referenced but missing"
    assert path.stat().st_size > 0, f"{image} is empty"
    # A real RIFF/WEBP container, so a renamed placeholder cannot pass.
    header = path.read_bytes()[:12]
    assert header[:4] == b"RIFF" and header[8:12] == b"WEBP", f"{image} is not a WebP"


def test_images_directory_holds_only_the_seven_agreed_files() -> None:
    """No undeclared image may sit in the directory next to the referenced ones."""
    expected = {Path(item).name for item in README_IMAGES}
    for images in DIVERTED_IMAGES.values():
        expected.update(Path(item).name for item in images)
    present = {item.name for item in IMAGES_DIR.iterdir() if item.is_file()}
    assert present == expected, f"unexpected: {sorted(present - expected)}"
