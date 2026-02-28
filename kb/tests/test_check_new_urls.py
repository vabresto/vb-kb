from __future__ import annotations

import subprocess

from kb.tools import check_new_urls


def test_url_regex_skips_fstring_template_host() -> None:
    line = 'base_url = f"http://{normalize_public_host(host)}:{port}"'
    assert check_new_urls.URL_RE.findall(line) == []


def test_should_check_url_skips_template_url() -> None:
    assert check_new_urls.should_check_url("http://{normalize_public_host") is False


def test_should_check_url_skips_hostless_url() -> None:
    assert check_new_urls.should_check_url("http://") is False


def test_should_check_url_accepts_real_public_url() -> None:
    assert check_new_urls.should_check_url("https://openai.com/docs") is True


def test_should_skip_diff_file_matches_enrichment_snapshot_html() -> None:
    assert (
        check_new_urls._should_skip_diff_file(  # noqa: SLF001
            "data/source/en/source@enrichment-linkedin-com-foo/snapshot.html"
        )
        is True
    )
    assert check_new_urls._should_skip_diff_file("data/source/en/source@enrichment-linkedin-com-foo/index.md") is False  # noqa: SLF001


def test_staged_added_urls_skips_snapshot_html_entries(monkeypatch) -> None:
    blocked_url = "hxxps://media.licdn.com/dms/image/v2/abc".replace("hxxps://", "https://")
    profile_url = "hxxps://www.linkedin.com/in/jose-luis-avilez/".replace("hxxps://", "https://")
    diff = "\n".join(
        [
            "diff --git a/data/source/en/source@enrichment-x/snapshot.html b/data/source/en/source@enrichment-x/snapshot.html",
            "new file mode 100644",
            "index 0000000..1111111",
            "--- /dev/null",
            "+++ b/data/source/en/source@enrichment-x/snapshot.html",
            "@@ -0,0 +1 @@",
            f"+<img src=\"{blocked_url}\">",
            "diff --git a/data/person/jo/person@jose/index.md b/data/person/jo/person@jose/index.md",
            "index 1111111..2222222 100644",
            "--- a/data/person/jo/person@jose/index.md",
            "+++ b/data/person/jo/person@jose/index.md",
            "@@ -1 +1 @@",
            f"+See profile {profile_url}",
        ]
    )

    def _run(*_args, **_kwargs):
        return subprocess.CompletedProcess(args=["git"], returncode=0, stdout=diff, stderr="")

    monkeypatch.setattr(check_new_urls.subprocess, "run", _run)
    urls = check_new_urls.staged_added_urls()
    assert urls == [profile_url]
