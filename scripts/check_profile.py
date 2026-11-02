"""Verifica o perfil publicável: README e SVGs presentes, imagens referenciadas
existem, sem tokens (reutiliza a auditoria de segurança). Só leitura."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_security  # noqa: E402


def verificar(root=ROOT):
    root = Path(root)
    erros = []
    readme = root / "README.md"
    if not readme.exists():
        return ["falta README.md"]
    texto = readme.read_text(encoding="utf-8")
    refs = re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", texto)
    if not refs:
        erros.append("README sem imagens")
    for ref in refs:
        if re.match(r"[a-z]+:", ref, re.I) or ".." in ref:
            erros.append("imagem não local: " + ref)
        elif not (root / ref).is_file():
            erros.append("imagem em falta: " + ref)
    if not (root / "assets" / "terminal.svg").is_file():
        erros.append("falta assets/terminal.svg")
    if not (root / ".github" / "workflows" / "update.yml").is_file():
        erros.append("falta update.yml")
    return erros + check_security.auditar(root)


def main():
    erros = verificar()
    for e in erros:
        print("ERRO: " + e, file=sys.stderr)
    print("perfil ok" if not erros else "%d erro(s)" % len(erros))
    return 1 if erros else 0


if __name__ == "__main__":
    sys.exit(main())
