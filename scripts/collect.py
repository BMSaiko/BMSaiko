#!/usr/bin/env python3
"""Recolhe métricas públicas do GitHub e escreve data/stats.json.

Segurança (ver SP design): só stdlib; token só por variável de ambiente
(GITHUB_TOKEN; localmente `GITHUB_TOKEN=$(gh auth token)`), nunca escrito nem
registado; só dados públicos, sem forks/arquivados; allowlist de campos; dados
da API tratados como não fiáveis (schema, limites, controlo removido); falha
fechada: qualquer erro termina com RC!=0 sem alterar data/stats.json.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com/graphql"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "stats.json"
CONFIG = ROOT / "config.json"

SCHEMA_VERSION = 1
MAX_PAGES = 5
MAX_REPOS = 500
MAX_LANGS = 8
MAX_TEXT = 200
RECENT = 4
SPARK_WEEKS = 12
TIMEOUT = 20
RETRIES = 3
LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f‪-‮⁦-⁩]")

REPOS_QUERY = """
query($login:String!, $after:String) {
  user(login:$login) {
    name
    bio
    repositories(first:100, after:$after, ownerAffiliations:OWNER, privacy:PUBLIC,
                 orderBy:{field:PUSHED_AT, direction:DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        name description url stargazerCount isFork isArchived isPrivate pushedAt
        primaryLanguage { name }
        languages(first:20, orderBy:{field:SIZE, direction:DESC}) {
          edges { size node { name color } }
        }
      }
    }
  }
}
"""

CONTRIB_QUERY = """
query($login:String!) {
  user(login:$login) {
    contributionsCollection {
      totalCommitContributions
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""

HISTORY_QUERY = """
query($login:String!, $name:String!, $since:GitTimestamp!) {
  repository(owner:$login, name:$name) {
    isPrivate
    defaultBranchRef { target { ... on Commit {
      history(since:$since, first:100) { nodes { committedDate } }
    } } }
  }
}
"""


class CollectError(Exception):
    """Falha fechada: nada é escrito."""


def clean(value, limit=MAX_TEXT):
    """Texto não fiável -> str curta sem caracteres de controlo."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise CollectError("campo de texto inválido")
    return CTRL_RE.sub("", value).strip()[:limit]


def to_int(value, lo=0, hi=10**9):
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise CollectError("inteiro inválido")
    return value


def to_date(value):
    if not isinstance(value, str) or not DATE_RE.match(value):
        raise CollectError("data inválida")
    return value[:10]


def post(query, variables, token, opener=urllib.request.urlopen, sleep=time.sleep):
    """POST GraphQL com timeout e retries com backoff. Nunca regista o token."""
    body = json.dumps({"query": query, "variables": variables}).encode()
    last = "erro desconhecido"
    for attempt in range(RETRIES):
        req = urllib.request.Request(
            API,
            data=body,
            headers={
                "Authorization": "bearer " + token,
                "Content-Type": "application/json",
                "User-Agent": "BMSaiko-profile-stats",
            },
        )
        try:
            with opener(req, timeout=TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last = "HTTP %d" % exc.code
            if exc.code < 500 and exc.code != 429:
                break
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            last = type(exc).__name__
        else:
            if not isinstance(payload, dict) or payload.get("errors") or "data" not in payload:
                raise CollectError("resposta GraphQL com erros")
            return payload["data"]
        if attempt < RETRIES - 1:
            sleep(2 ** attempt)
    raise CollectError("API falhou: " + last)


def load_config(path=CONFIG):
    try:
        cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CollectError("config.json inválido") from exc
    login = cfg.get("login")
    if not isinstance(login, str) or not LOGIN_RE.match(login):
        raise CollectError("login inválido")
    featured = cfg.get("featured", [])
    if not isinstance(featured, list) or len(featured) > 3:
        raise CollectError("featured: máximo 3")
    items = []
    for item in featured:
        name = item.get("name") if isinstance(item, dict) else None
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise CollectError("featured: nome inválido")
        items.append({"name": name, "description": clean(item.get("description"))})
    return {"login": login, "featured": items}


def fetch_repos(login, token, post_fn=post):
    """Todos os repos públicos (paginado, com limite de páginas)."""
    nodes, after, user = [], None, None
    for _ in range(MAX_PAGES):
        data = post_fn(REPOS_QUERY, {"login": login, "after": after}, token)
        user = (data or {}).get("user")
        if not isinstance(user, dict):
            raise CollectError("utilizador não encontrado")
        page = user["repositories"]
        nodes.extend(page["nodes"])
        if len(nodes) > MAX_REPOS:
            raise CollectError("demasiados repos")
        if not page["pageInfo"]["hasNextPage"]:
            return user, nodes
        after = page["pageInfo"]["endCursor"]
    raise CollectError("limite de páginas excedido")


def fetch_contributions(login, token, post_fn=post):
    user = (post_fn(CONTRIB_QUERY, {"login": login}, token) or {}).get("user")
    if not isinstance(user, dict):
        raise CollectError("utilizador não encontrado")
    return user["contributionsCollection"]


def weekly_buckets(dates, now, weeks=SPARK_WEEKS):
    """Commits por semana, da mais antiga para a mais recente."""
    buckets = [0] * weeks
    for d in dates:
        age = (now - dt.date.fromisoformat(to_date(d))).days // 7
        if 0 <= age < weeks:
            buckets[weeks - 1 - age] += 1
    return buckets


def fetch_sparkline(login, name, token, now, post_fn=post):
    since = (now - dt.timedelta(weeks=SPARK_WEEKS)).isoformat() + "T00:00:00Z"
    data = post_fn(HISTORY_QUERY, {"login": login, "name": name, "since": since}, token)
    repo = (data or {}).get("repository")
    if not isinstance(repo, dict) or repo.get("isPrivate") is not False:
        raise CollectError("featured não é um repo público: " + name)
    branch = repo.get("defaultBranchRef") or {}
    nodes = ((branch.get("target") or {}).get("history") or {}).get("nodes") or []
    return weekly_buckets([n["committedDate"] for n in nodes], now)


def public_repos(nodes):
    """Allowlist: só públicos, não-fork, não-arquivados."""
    out = []
    for n in nodes:
        if n.get("isPrivate") is not False:
            continue
        if n.get("isFork") or n.get("isArchived"):
            continue
        name = n.get("name")
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise CollectError("nome de repo inválido")
        out.append(n)
    return out


def build_languages(repos):
    totals, colors = {}, {}
    for repo in repos:
        for edge in (repo.get("languages") or {}).get("edges") or []:
            lang = clean(edge["node"]["name"], 40)
            size = to_int(edge["size"])
            totals[lang] = totals.get(lang, 0) + size
            color = edge["node"].get("color")
            colors[lang] = color if isinstance(color, str) and COLOR_RE.match(color) else "#8b949e"
    total = sum(totals.values())
    ranked = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_LANGS]
    return [
        {"name": k, "bytes": v, "percent": round(100 * v / total, 1) if total else 0.0, "color": colors[k]}
        for k, v in ranked
    ]


def build_calendar(contrib):
    cal = contrib["contributionCalendar"]
    weeks = []
    for week in cal["weeks"][-53:]:
        weeks.append(
            [
                {"date": to_date(d["date"]), "count": to_int(d["contributionCount"], hi=100000)}
                for d in week["contributionDays"]
            ]
        )
    return to_int(cal["totalContributions"]), weeks


def build_stats(user, nodes, contrib, config, sparklines, now):
    repos = public_repos(nodes)
    total_contrib, weeks = build_calendar(contrib)
    by_name = {r["name"]: r for r in repos}
    featured = []
    for item in config["featured"]:
        repo = by_name.get(item["name"])
        if repo is None:
            raise CollectError("featured não encontrado entre os públicos: " + item["name"])
        featured.append(
            {
                "name": repo["name"],
                "description": clean(repo.get("description")) or item["description"],
                "url": "https://github.com/%s/%s" % (config["login"], repo["name"]),
                "stars": to_int(repo["stargazerCount"]),
                "language": clean((repo.get("primaryLanguage") or {}).get("name"), 40),
                "sparkline": sparklines[repo["name"]],
            }
        )
    recent = [
        {"name": r["name"], "pushed_at": to_date(r["pushedAt"])}
        for r in sorted(repos, key=lambda r: r.get("pushedAt") or "", reverse=True)
        if r.get("pushedAt") and r["name"].lower() != config["login"].lower()
    ][:RECENT]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.isoformat(),
        "login": config["login"],
        "name": clean(user.get("name"), 80),
        "bio": clean(user.get("bio")),
        "stats": {
            "commits_year": to_int(contrib["totalCommitContributions"]),
            "repos_public": len(repos),
            "stars": sum(to_int(r["stargazerCount"]) for r in repos),
            "contributions_year": total_contrib,
        },
        "languages": build_languages(repos),
        "calendar": weeks,
        "featured": featured,
        "recent": recent,
    }


def validate(stats):
    """Schema mínimo da saída; levanta CollectError se inválida."""
    keys = {"schema_version", "generated_at", "login", "name", "bio", "stats",
            "languages", "calendar", "featured", "recent"}
    if set(stats) != keys or stats["schema_version"] != SCHEMA_VERSION:
        raise CollectError("schema inválido")
    if set(stats["stats"]) != {"commits_year", "repos_public", "stars", "contributions_year"}:
        raise CollectError("schema inválido: stats")
    if len(stats["featured"]) > 3 or len(stats["recent"]) > RECENT or len(stats["languages"]) > MAX_LANGS:
        raise CollectError("schema inválido: limites")
    for lang in stats["languages"]:
        if not COLOR_RE.match(lang["color"]):
            raise CollectError("schema inválido: cor")
    return stats


def write_atomic(stats, out=OUT):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        antigo = json.loads(out.read_text(encoding="utf-8"))
        if {k: v for k, v in antigo.items() if k != "generated_at"} == \
                {k: v for k, v in stats.items() if k != "generated_at"}:
            stats = dict(stats, generated_at=antigo["generated_at"])  # sem diff, sem commit
    except (OSError, ValueError, KeyError, AttributeError):
        pass
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, out)


def collect(token, config, now=None, post_fn=post):
    now = now or dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    user, nodes = fetch_repos(config["login"], token, post_fn)
    contrib = fetch_contributions(config["login"], token, post_fn)
    today = now.date()
    sparklines = {
        item["name"]: fetch_sparkline(config["login"], item["name"], token, today, post_fn)
        for item in config["featured"]
    }
    return validate(build_stats(user, nodes, contrib, config, sparklines, now))


def main():
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("erro: defina GITHUB_TOKEN (localmente: GITHUB_TOKEN=$(gh auth token))", file=sys.stderr)
        return 2
    try:
        stats = collect(token, load_config())
        write_atomic(stats)
    except CollectError as exc:
        print("erro: %s; data/stats.json não foi alterado" % exc, file=sys.stderr)
        return 1
    print("ok: data/stats.json (%d repos públicos)" % stats["stats"]["repos_public"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
