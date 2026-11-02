import datetime as dt
import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import collect  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
NOW = dt.datetime(2026, 11, 2, 12, 0, 0, tzinfo=dt.timezone.utc)
CONFIG = {"login": "BMSaiko", "featured": [{"name": "foodlister", "description": "fallback"},
                                            {"name": "kaizen-flow", "description": "fallback kaizen"}]}


def fx(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def fake_post(query, variables, token):
    if "repositories" in query:
        return fx("repos_page.json")
    if "contributionCalendar" in query:
        return fx("contributions.json")
    if "history" in query:
        return fx("history.json")
    raise AssertionError("query inesperada")


def run():
    return collect.collect("tok", CONFIG, now=NOW, post_fn=fake_post)


def test_stats_values():
    s = run()
    assert s["stats"] == {"commits_year": 1149, "repos_public": 2, "stars": 1, "contributions_year": 2748}
    assert s["login"] == "BMSaiko"


def test_excludes_private_fork_archived():
    s = run()
    names = {r["name"] for r in s["recent"]}
    assert names == {"foodlister", "kaizen-flow"}
    assert "Rust" not in {l["name"] for l in s["languages"]}
    assert "Go" not in {l["name"] for l in s["languages"]}


def test_languages_percent_and_color_allowlist():
    langs = {l["name"]: l for l in run()["languages"]}
    assert langs["TypeScript"]["percent"] == 60.0
    assert langs["CSS"]["color"] == "#8b949e"  # cor maliciosa substituída


def test_sanitises_control_chars_and_keeps_text_for_escape_later():
    s = run()
    assert s["name"] == "Bruno Silva"
    assert "\x07" not in s["bio"]
    assert s["featured"][1]["description"] == "fallback kaizen"  # sem descrição -> fallback


def test_sparkline_buckets():
    spark = run()["featured"][0]["sparkline"]
    assert len(spark) == collect.SPARK_WEEKS
    assert spark[-1] == 2 and spark[-2] == 1 and sum(spark) == 3


def test_calendar():
    s = run()
    assert s["calendar"][1][1] == {"date": "2026-11-02", "count": 8}


def test_featured_must_be_public_listed_repo():
    cfg = {"login": "BMSaiko", "featured": [{"name": "segredo", "description": ""}]}
    with pytest.raises(collect.CollectError):
        collect.collect("tok", cfg, now=NOW, post_fn=fake_post)


def test_pagination_limit_fails_closed():
    page = fx("repos_page.json")
    page["user"]["repositories"]["pageInfo"] = {"hasNextPage": True, "endCursor": "x"}
    with pytest.raises(collect.CollectError):
        collect.fetch_repos("BMSaiko", "tok", lambda q, v, t: page)


def test_invalid_data_fails_closed():
    bad = fx("repos_page.json")
    bad["user"]["repositories"]["nodes"][0]["stargazerCount"] = "muitas"
    with pytest.raises(collect.CollectError):
        collect.collect("tok", CONFIG, now=NOW, post_fn=lambda q, v, t: bad if "repositories" in q else fake_post(q, v, t))


def test_graphql_errors_fail_closed():
    class Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def opener(req, timeout):
        return Resp(b'{"errors":[{"message":"x"}]}')

    with pytest.raises(collect.CollectError):
        collect.post("q", {}, "tok", opener=opener, sleep=lambda s: None)


def test_retries_with_backoff_then_fails_without_leaking_token():
    calls, sleeps = [], []

    def opener(req, timeout):
        calls.append(timeout)
        raise urllib.error.HTTPError("u", 502, "bad", {}, None)

    with pytest.raises(collect.CollectError) as err:
        collect.post("q", {}, "SEGREDO123", opener=opener, sleep=sleeps.append)
    assert len(calls) == collect.RETRIES and sleeps == [1, 2]
    assert "SEGREDO123" not in str(err.value)


def test_client_error_not_retried():
    calls = []

    def opener(req, timeout):
        calls.append(1)
        raise urllib.error.HTTPError("u", 401, "no", {}, None)

    with pytest.raises(collect.CollectError):
        collect.post("q", {}, "tok", opener=opener, sleep=lambda s: None)
    assert len(calls) == 1


def test_write_atomic_roundtrip_and_no_change_on_failure(tmp_path):
    out = tmp_path / "data" / "stats.json"
    collect.write_atomic(run(), out)
    first = out.read_text(encoding="utf-8")
    assert json.loads(first)["schema_version"] == 1
    with pytest.raises(collect.CollectError):
        collect.validate({"schema_version": 1})
    assert out.read_text(encoding="utf-8") == first


def test_config_validation(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"login": "x; rm -rf", "featured": []}', encoding="utf-8")
    with pytest.raises(collect.CollectError):
        collect.load_config(p)
    p.write_text('{"login": "BMSaiko", "featured": [{"name": "a"}, {"name": "b"}, {"name": "c"}, {"name": "d"}]}',
                 encoding="utf-8")
    with pytest.raises(collect.CollectError):
        collect.load_config(p)


def test_main_requires_token(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert collect.main() == 2
    assert "GITHUB_TOKEN" in capsys.readouterr().err


def test_no_tokens_in_repo_files():
    import re
    pat = re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}")
    for f in ROOT.rglob("*"):
        if f.is_file() and ".git" not in f.parts and f.suffix in {".py", ".json", ".md", ".yml"}:
            assert not pat.search(f.read_text(encoding="utf-8", errors="ignore")), f


def test_write_atomic_mantem_generated_at_sem_alteracoes(tmp_path):
    out = tmp_path / "stats.json"
    collect.write_atomic({"generated_at": "A", "x": 1}, out)
    antes = out.read_bytes()
    collect.write_atomic({"generated_at": "B", "x": 1}, out)
    assert out.read_bytes() == antes
    collect.write_atomic({"generated_at": "C", "x": 2}, out)
    assert '"C"' in out.read_text(encoding="utf-8")


def test_recent_exclui_repo_do_perfil():
    s = run()
    assert "bmsaiko" not in {r["name"].lower() for r in s["recent"]}
