"""Valida .github/workflows/update.yml (stdlib; usa actionlint se existir)."""
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows" / "update.yml"


def check(text):
    erros = []

    def exige(cond, msg):
        if not cond:
            erros.append(msg)

    exige(re.search(r"^\s*schedule:\s*\n\s*- cron:", text, re.M), "falta cron diário")
    exige(re.search(r"^\s*workflow_dispatch:", text, re.M), "falta workflow_dispatch")
    exige("pull_request_target" not in text, "pull_request_target proibido")
    exige(re.search(r"^permissions:\s*\n\s+contents: write\s*$", text, re.M), "permissions deve ser só contents: write")
    exige(len(re.findall(r"^\s+\w[\w-]*: (read|write)\s*$", text, re.M)) == 1, "permissões extra")
    exige(re.search(r"^concurrency:", text, re.M), "falta concurrency")
    exige(re.search(r"timeout-minutes:", text), "falta timeout-minutes")
    for m in re.finditer(r"uses:\s*(\S+)", text):
        exige(re.search(r"@[0-9a-f]{40}$", m.group(1)), "ação não fixada por SHA: " + m.group(1))
    # sem expressões ${{ }} dentro de run:
    em_run = False
    for linha in text.splitlines():
        s = linha.strip()
        if s.startswith("run:"):
            em_run = True
            if "${{" in s:
                erros.append("${{ }} em run: " + s)
            continue
        if em_run and (linha.startswith("      - ") or re.match(r"^\s{8}\w[\w-]*:", linha)):
            em_run = False
        if em_run and "${{" in linha:
            erros.append("${{ }} em run: " + s)
    exige("secrets.GITHUB_TOKEN" in text and "GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}" in text, "token deve ser o GITHUB_TOKEN efémero")
    exige(not re.search(r"\b(ghp_|github_pat_|gho_)", text), "token literal no workflow")
    exige("git diff --cached --quiet" in text, "falta verificação 'sem diff, sem commit'")
    exige("git add -- data assets README.md" in text, "commit deve limitar-se a data/, assets/ e README.md")
    exige("Co-Authored-By" not in text and "Generated with" not in text, "atribuição proibida")
    exige("set -x" not in text, "set -x proibido")
    return erros


def main():
    if not WF.exists():
        print("falta " + str(WF), file=sys.stderr)
        return 1
    erros = check(WF.read_text(encoding="utf-8"))
    if shutil.which("actionlint"):
        r = subprocess.run(["actionlint", str(WF)], capture_output=True, text=True)
        if r.returncode:
            erros.append("actionlint: " + (r.stdout + r.stderr).strip())
    else:
        print("aviso: actionlint não instalado; só validação própria")
    for e in erros:
        print("ERRO: " + e, file=sys.stderr)
    if not erros:
        print("workflow ok")
    return 1 if erros else 0


if __name__ == "__main__":
    sys.exit(main())
