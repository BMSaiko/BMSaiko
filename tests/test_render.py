import copy
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import render  # noqa: E402

SNAP = Path(__file__).parent / "snapshots"
HOSTILE = '<script>alert(1)</script>"\'&\x07‮ [x](http://evil) `*_#|'


def sample():
    cal = [[{"date": "2026-01-%02d" % (d + 1), "count": (w * 7 + d) % 13} for d in range(7)] for w in range(53)]
    return {
        "schema_version": 1,
        "generated_at": "2026-11-02T12:00:00+00:00",
        "login": "BMSaiko",
        "name": "Bruno Silva",
        "bio": "Estudante de engenharia informática.",
        "stats": {"commits_year": 1149, "repos_public": 14, "stars": 1, "contributions_year": 2748},
        "languages": [
            {"name": "JavaScript", "bytes": 800, "percent": 50.0, "color": "#f1e05a"},
            {"name": "Java", "bytes": 500, "percent": 30.0, "color": "#b07219"},
            {"name": "C", "bytes": 300, "percent": 20.0, "color": "#555555"},
        ],
        "calendar": cal,
        "featured": [
            {"name": "FoodLister", "description": "Receitas.", "url": "x", "stars": 3,
             "language": "TypeScript", "sparkline": [0, 1, 2, 3, 4, 5, 6, 5, 4, 3, 2, 1]},
            {"name": "kaizen-flow", "description": "Fluxo.", "url": "x", "stars": 0,
             "language": "", "sparkline": [0] * 12},
        ],
        "recent": [{"name": "kaizen-flow", "pushed_at": "2026-11-02"}],
    }


def hostile():
    s = sample()
    s["name"] = HOSTILE
    s["bio"] = HOSTILE
    s["languages"][0]["name"] = HOSTILE
    s["featured"][0]["description"] = HOSTILE
    s["featured"][0]["language"] = HOSTILE
    return s


# ------------------------------------------------------------ snapshots

def test_snapshots():
    files = render.render_all(sample())
    for rel, content in files.items():
        snap = SNAP / rel.replace("/", "__")
        if os.environ.get("UPDATE_SNAPSHOTS") == "1":
            snap.parent.mkdir(exist_ok=True)
            snap.write_bytes(content.encode("utf-8"))
        assert snap.read_bytes().decode("utf-8") == content, rel


def test_deterministic():
    assert render.render_all(sample()) == render.render_all(copy.deepcopy(sample()))


def test_real_stats_render_and_parse():
    stats = json.loads((ROOT / "data" / "stats.json").read_text(encoding="utf-8"))
    for rel, content in render.render_all(stats).items():
        if rel.endswith(".svg"):
            ET.fromstring(content)


# ------------------------------------------------------------ segurança

FORBIDDEN_TAGS = {"script", "foreignobject", "iframe", "image", "use", "a", "animatemotion"}


def check_svg(content):
    root = ET.fromstring(content)  # XML bem formado
    for el in root.iter():
        tag = el.tag.split("}")[-1].lower()
        assert tag not in FORBIDDEN_TAGS, tag
        if tag in ("animate", "set", "animatetransform", "animatemotion"):
            name = el.attrib.get("attributeName", "").lower()
            assert name and not name.startswith("on") and "href" not in name, name
        for attr, val in el.attrib.items():
            name = attr.split("}")[-1].lower()
            assert not name.startswith("on"), attr
            assert name not in ("href", "src"), attr
            assert "javascript:" not in val.lower()
    low = content.lower()
    assert "@import" not in low and "url(http" not in low and "url('http" not in low
    for el in root.iter():  # URLs só como texto escapado, nunca em atributos
        for val in el.attrib.values():
            assert "http" not in val.lower().replace("http://www.w3.org/2000/svg", "")


@pytest.mark.parametrize("builder", [sample, hostile])
def test_svg_sem_conteudo_ativo(builder):
    for rel, content in render.render_all(builder()).items():
        if rel.endswith(".svg"):
            check_svg(content)


def test_escape_hostil_nos_svg():
    for rel, content in render.render_all(hostile()).items():
        if rel.endswith(".svg"):
            assert "<script" not in content and "\x07" not in content and "‮" not in content


def test_escape_hostil_no_readme():
    readme = render.render_all(hostile())["README.md"]
    assert "\x07" not in readme and "‮" not in readme
    assert "[x](http://evil)" not in readme  # link neutralizado
    assert not re.search(r"(?<!\\)[<>]", readme.replace("<sub>", "").replace("</sub>", ""))


def test_urls_reconstruidos():
    s = sample()
    s["featured"][0]["url"] = "javascript:alert(1)"
    readme = render.render_all(s)["README.md"]
    assert "javascript:" not in readme
    assert "(https://github.com/BMSaiko/FoodLister)" in readme


@pytest.mark.parametrize("mutate", [
    lambda s: s["languages"][0].update(color="red;}</style><script>"),
    lambda s: s.update(login="a/../b"),
    lambda s: s["featured"][0].update(name="x/../../y"),
    lambda s: s["featured"][0].update(sparkline=["<x>"] * 12),
    lambda s: s["calendar"][0][0].update(count="9<"),
    lambda s: s.update(schema_version=2),
    lambda s: s.pop("stats"),
])
def test_falha_fechada(mutate):
    s = sample()
    mutate(s)
    with pytest.raises((render.RenderError, render.collect.CollectError, KeyError)):
        render.render_all(s)


def test_main_falha_nao_escreve(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "stats.json"
    bad.write_text("{nao json", encoding="utf-8")
    monkeypatch.setattr(render, "STATS", bad)
    monkeypatch.setattr(render, "ROOT", tmp_path)
    assert render.main() == 1
    assert not (tmp_path / "README.md").exists() and not (tmp_path / "assets").exists()


# ------------------------------------------------------------ tema e fontes

def test_claro_escuro_e_sem_fontes_externas():
    for rel, content in render.render_all(sample()).items():
        if rel.endswith(".svg"):
            assert "prefers-color-scheme:light" in content
            assert "font-face" not in content and "fonts.googleapis" not in content


def test_write_all_lf(tmp_path):
    render.write_all({"README.md": "a\nb\n", "assets/x.svg": "<svg/>\n"}, tmp_path)
    assert (tmp_path / "README.md").read_bytes() == b"a\nb\n"


def test_cards_ligados_ao_repo():
    files = render.render_all(sample())
    assert "assets/card-1.svg" in files and "assets/card-2.svg" in files
    readme = files["README.md"]
    assert "[![FoodLister](assets/card-1.svg)](https://github.com/BMSaiko/FoodLister)" in readme
    assert ":hover" in files["assets/card-1.svg"]


def test_heatmap_largura_total():
    svg = render.render_all(sample())["assets/terminal.svg"]
    xs = [float(m) for m in re.findall(r'<rect class="acf cell" x="([0-9.]+)"', svg)]
    w = float(re.search(r'<rect class="acf cell" x="[0-9.]+" y="[0-9.]+" width="([0-9.]+)"', svg).group(1))
    assert max(xs) + w == pytest.approx(22 + 834, abs=1)


def test_readme_sem_titulo():
    readme = render.render_all(sample())["README.md"]
    assert not readme.startswith("# ")
    assert readme.splitlines()[0] != "# Bruno Silva"
    assert readme.startswith("![Terminal do perfil](assets/terminal.svg)") or readme.splitlines()[0] == sample()["bio"]
