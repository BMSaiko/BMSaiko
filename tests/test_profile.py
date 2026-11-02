import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_profile as cp  # noqa: E402


def copia(tmp_path):
    dst = tmp_path / "repo"
    shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
    return dst


def test_perfil_real_ok():
    assert cp.verificar(ROOT) == []


def test_imagem_em_falta(tmp_path):
    r = copia(tmp_path)
    (r / "assets" / "terminal.svg").unlink()
    erros = cp.verificar(r)
    assert any("em falta" in e for e in erros)


def test_imagem_externa(tmp_path):
    r = copia(tmp_path)
    p = r / "README.md"
    p.write_text(p.read_text(encoding="utf-8") + "\n![x](https://evil.example/a.svg)\n", encoding="utf-8")
    assert any("não local" in e for e in cp.verificar(r))
