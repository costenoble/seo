import asyncio

import pytest

from seo_audit.checks import run_checks
from seo_audit.crawler import Crawler
from seo_audit.links import check_external_links
from seo_audit.report import write_reports
from seo_audit.urls import normalize_url

from .site_de_test import start_server


def test_normalize_url():
    assert normalize_url("HTTPS://Example.com:443/a?utm_source=x&id=2#top") == "https://example.com/a?id=2"
    assert normalize_url("http://example.com") == "http://example.com/"


@pytest.fixture(scope="module")
def audit(tmp_path_factory):
    server, base = start_server()
    try:
        crawler = Crawler(base + "/", concurrency=3)
        asyncio.run(crawler.run())
        external = asyncio.run(check_external_links(sorted(crawler.external_sources), user_agent="test"))
        issues = run_checks(crawler, external)
        paths = write_reports(crawler, issues, tmp_path_factory.mktemp("rapport"), meta={})
    finally:
        server.shutdown()
    return base, crawler, issues, paths


def codes_for(issues, path, base):
    return {i.code for i in issues if i.url == base + path}


@pytest.mark.parametrize("path,code", [
    ("/sans-titre", "missing_title"),
    ("/a", "missing_description"),
    ("/a", "multiple_h1"),
    ("/a", "images_missing_alt"),
    ("/doublon-1", "duplicate_title"),
    ("/disparue", "http_4xx"),
    ("/disparue", "sitemap_non_200"),
    ("/", "broken_internal_link"),
    ("/", "broken_external_link"),
    ("/noindex", "sitemap_noindex"),
    ("/x-robots", "noindex"),
    ("/canon-cassee", "canonical_to_non_indexable"),
    ("/redir-1", "redirect_chain"),
    ("/redir-2", "temporary_redirect"),
    ("/prive/x", "sitemap_blocked"),
    ("/orpheline", "orphan_page"),
])
def test_detects(audit, path, code):
    base, _, issues, _ = audit
    assert code in codes_for(issues, path, base)


def test_clean_page_has_no_major_issue(audit):
    base, _, issues, _ = audit
    assert not {i.severity for i in issues if i.url == base + "/"} & {"critical"}


def test_tracking_params_deduplicated(audit):
    base, crawler, _, _ = audit
    assert not any("utm_" in u for u in crawler.results)


def test_reports_written(audit):
    *_, paths = audit
    for p in paths.values():
        assert p.exists() and p.stat().st_size > 0
    assert "__DATA__" not in paths["html"].read_text(encoding="utf-8")


def test_category_scores_and_comparison(audit, tmp_path):
    import json
    base, crawler, issues, paths = audit
    first = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert set(first["summary"]["category_scores"]) >= {"http", "contenu", "international", "donnees"}
    fixed = [i for i in issues if i.code != "missing_description"]
    out = write_reports(crawler, fixed, tmp_path, meta={}, previous=paths["json"])
    cmp = json.loads(out["json"].read_text(encoding="utf-8"))["summary"]["comparison"]
    assert cmp["fixed_count"] > 0 and cmp["new_count"] == 0 and cmp["score_delta"] >= 0


def test_hreflang_jsonld_duplicates():
    from seo_audit.checks_extra import run_extra_checks
    from seo_audit.models import PageResult
    from seo_audit.parser import parse_html

    def pr(url, head, body="<p>" + " ".join(f"m{i}" for i in range(80)) + "</p>"):
        return PageResult(url=url, depth=0, status=200,
                          parsed=parse_html(f"<html><head>{head}</head><body>{body}</body></html>", url))

    results = {
        "https://a.test/": pr("https://a.test/", '<link rel="alternate" hreflang="fr" href="https://a.test/">'
                              '<link rel="alternate" hreflang="en" href="https://a.test/en">'
                              '<link rel="alternate" hreflang="xx_YY" href="https://a.test/zz">'
                              '<script type="application/ld+json">{"@type":"Article"}</script>'
                              '<script type="application/ld+json">{oups</script>'),
        "https://a.test/en": pr("https://a.test/en", ""),
        "https://a.test/copie": pr("https://a.test/copie", ""),
    }
    issues = []
    run_extra_checks(results, lambda *a: issues.append(a[2]))
    assert {"hreflang_invalid_code", "hreflang_missing_return", "jsonld_invalid",
            "jsonld_missing_property", "duplicate_content"} <= set(issues)
