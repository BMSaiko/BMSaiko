import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_security as cs  # noqa: E402


def copia(tmp_path):
    dst = tmp_path / "repo"
    shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
    return dst


def test_repo_real_sem_achados():
    assert cs.auditar(ROOT) == []


def test_detecta_token(tmp_path):
    r = copia(tmp_path)
    (r / "scripts" / "x.py").write_text('T = "ghp_' + "a" * 30 + '"\n', encoding="utf-8")
    assert any("token" in e for e in cs.auditar(r))


def test_detecta_svg_ativo_e_href_externo(tmp_path):
    r = copia(tmp_path)
    (r / "assets" / "mau.svg").write_text(
        '<svg><script>x</script><a href="https://evil.example">a</a></svg>', encoding="utf-8")
    erros = cs.auditar(r)
    assert any("ativo" in e for e in erros) and any("externo" in e for e in erros)


def test_detecta_email_no_readme(tmp_path):
    r = copia(tmp_path)
    p = r / "README.md"
    p.write_text(p.read_text(encoding="utf-8") + "\nfoo@bar.com\n", encoding="utf-8")
    assert any("email" in e for e in cs.auditar(r))


def test_detecta_chave_extra_em_stats(tmp_path):
    r = copia(tmp_path)
    p = r / "data" / "stats.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    d["email"] = "x"
    p.write_text(json.dumps(d), encoding="utf-8")
    assert any("allowlist" in e for e in cs.auditar(r))


def test_detecta_workflow_inseguro(tmp_path):
    r = copia(tmp_path)
    p = r / ".github" / "workflows" / "update.yml"
    t = p.read_text(encoding="utf-8").replace("contents: write", "contents: write\n  issues: write")
    p.write_text(t, encoding="utf-8")
    assert any("workflow" in e for e in cs.auditar(r))
