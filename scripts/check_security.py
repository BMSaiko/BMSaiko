"""Auditoria de segurança do pipeline (stdlib, só leitura).

Verifica: sem tokens em ficheiros, workflow com permissões mínimas, SVG sem
conteúdo ativo ou ligações externas, outputs só com dados públicos (sem emails)
e stats.json com o schema esperado. Sai com rc=1 se houver achados.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_workflow  # noqa: E402

TOKEN_RE = re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@(?!users\.noreply\.github\.com)[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
ATIVO_RE = re.compile(r"<\s*(script|foreignObject|iframe|object|embed)\b|\son\w+\s*=|@import|javascript:", re.I)
TOP_KEYS = {"schema_version", "generated_at", "login", "name", "bio", "stats", "languages",
            "calendar", "featured", "recent"}
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache"}


def ficheiros(root):
    for f in sorted(Path(root).rglob("*")):
        if f.is_file() and not SKIP_DIRS & set(f.relative_to(root).parts):
            yield f


def svg_erros(nome, texto):
    erros = []
    if ATIVO_RE.search(texto):
        erros.append("%s: conteúdo ativo no SVG" % nome)
    for m in re.finditer(r"(?:href|src)\s*=\s*[\"']([^\"']*)", texto, re.I):
        if not m.group(1).startswith("#"):
            erros.append("%s: href/src externo %r" % (nome, m.group(1)[:40]))
    if re.search(r"url\(\s*[\"']?https?:", texto, re.I):
        erros.append("%s: url() externo" % nome)
    return erros


def auditar(root=ROOT):
    root = Path(root)
    erros = []
    for f in ficheiros(root):
        rel = f.relative_to(root).as_posix()
        if rel.startswith("tests/") or rel in ("scripts/check_security.py", "scripts/check_workflow.py"):
            continue  # testes e verificadores contêm padrões de propósito
        if f.suffix not in {".py", ".json", ".md", ".yml", ".svg", ".txt"}:
            continue
        texto = f.read_text(encoding="utf-8", errors="ignore")
        if TOKEN_RE.search(texto):
            erros.append("%s: token" % rel)
        if rel in ("README.md", "data/stats.json") or rel.endswith(".svg"):
            if EMAIL_RE.search(texto):
                erros.append("%s: email em output público" % rel)
        if f.suffix == ".svg":
            erros += svg_erros(rel, texto)
    readme = root / "README.md"
    if readme.exists() and re.search(r"<\s*(script|iframe|style)\b|\son\w+\s*=|javascript:", readme.read_text(encoding="utf-8"), re.I):
        erros.append("README.md: HTML ativo")
    stats = root / "data" / "stats.json"
    if stats.exists():
        try:
            d = json.loads(stats.read_text(encoding="utf-8"))
            extra = set(d) - TOP_KEYS
            if extra or TOP_KEYS - set(d):
                erros.append("stats.json: chaves fora da allowlist %s" % sorted(extra | (TOP_KEYS - set(d))))
        except ValueError:
            erros.append("stats.json: JSON inválido")
    wf = root / ".github" / "workflows" / "update.yml"
    if wf.exists():
        erros += ["workflow: " + e for e in check_workflow.check(wf.read_text(encoding="utf-8"))]
    else:
        erros.append("falta update.yml")
    return erros


def main():
    erros = auditar()
    for e in erros:
        print("ACHADO: " + e, file=sys.stderr)
    print("segurança ok" if not erros else "%d achado(s)" % len(erros))
    return 1 if erros else 0


if __name__ == "__main__":
    sys.exit(main())
