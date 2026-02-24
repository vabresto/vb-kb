from __future__ import annotations

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
