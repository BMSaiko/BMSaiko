#!/usr/bin/env python3
"""Renderer determinístico: data/stats.json -> assets/*.svg + README.md.

Só stdlib. Os dados vêm da API do GitHub e são tratados como não fiáveis:
validação de schema, remoção de caracteres de controlo e escape XML/Markdown
em tudo o que entra nos ficheiros gerados. Sem rede, sem relógio, sem
aleatoriedade: a mesma entrada produz sempre a mesma saída.
"""
import datetime as dt
import json
import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import collect  # noqa: E402  (reutiliza validate/clean/regex do recolhedor)

STATS = ROOT / "data" / "stats.json"
CONFIG = ROOT / "config.json"
ASSETS = ROOT / "assets"
README = ROOT / "README.md"

MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()<>#+!|~&])")


class RenderError(Exception):
    pass


# ---------------------------------------------------------------- escapes

def text(value, limit=200):
    """Texto seguro para nós de texto e atributos XML."""
    value = collect.clean(value, limit) if isinstance(value, str) else str(value)
    return xml_escape(value, {'"': "&quot;", "'": "&#39;"})


def md(value, limit=200):
    """Texto seguro para Markdown (inclui HTML inline: < > & escapados)."""
    value = collect.clean(value, limit) if isinstance(value, str) else str(value)
    value = value.replace("\r", " ").replace("\n", " ")
    return MD_SPECIAL.sub(r"\\\1", value)


def wrap(value, width, lines):
    """Quebra por palavras em `lines` linhas de `width` caracteres (com reticências)."""
    words = collect.clean(value, 400).split()
    out, cur = [], ""
    for word in words:
        word = word[:width]
        if cur and len(cur) + 1 + len(word) > width:
            out.append(cur)
            cur = word
        else:
            cur = (cur + " " + word).strip()
    if cur:
        out.append(cur)
    if len(out) > lines:
        out = out[:lines]
        out[-1] = out[-1][: width - 1].rstrip() + "…"
    return out


def num(value):
    return "{:,}".format(int(value)).replace(",", " ")


def repo_url(login, name):
    """URL reconstruído a partir de login+nome validados; nunca o da API."""
    if not collect.LOGIN_RE.match(login) or not collect.NAME_RE.match(name):
        raise RenderError("login/repo inválido")
    return "https://github.com/%s/%s" % (login, name)


def link(href, inner, label):
    """<a> SVG; o href vem sempre de repo_url/profile_url (login+nome validados)."""
    return '<a href="%s" target="_blank" rel="noopener noreferrer" tabindex="0" aria-label="%s">%s</a>' % (href, text(label, 80), inner)


def profile_url(login, tab=""):
    if not collect.LOGIN_RE.match(login):
        raise RenderError("login inválido")
    return "https://github.com/%s%s" % (login, "?tab=" + tab if tab in ("repositories", "stars", "overview") else "")


def color(value):
    if not collect.COLOR_RE.match(value):
        raise RenderError("cor inválida")
    return value


# ---------------------------------------------------------------- terminal SVG

# Réplica do mockup A2 (terminal animado). Tokens e fonte iguais; animações SMIL
# (funcionam em <img> no README). Sem animação, o estado final já é o correcto.
FONT = "ui-monospace,Consolas,monospace"
STYLE = """<style>
:root{--bg:#0d1117;--fg:#c9d1d9;--mut:#8b949e;--bd:#30363d;--ac:#3fb950;--bl:#58a6ff;--pn:#161b22}
@media(prefers-color-scheme:light){:root{--bg:#fff;--fg:#24292f;--mut:#57606a;--bd:#d0d7de;--ac:#1a7f37;--bl:#0969da;--pn:#f6f8fa}}
text{font-family:%s;font-size:14px;fill:var(--fg)}
.mut{fill:var(--mut)}.ac{fill:var(--ac)}.bl{fill:var(--bl)}.sm{font-size:12px}.big{font-size:22px;fill:var(--ac)}.h1{font-size:20px;fill:var(--ac)}
.win{fill:var(--bg);stroke:var(--bd)}.bar{fill:var(--pn)}.line{stroke:var(--bd)}.pill{fill:var(--pn);stroke:var(--bd)}.card{fill:transparent;pointer-events:all;stroke:var(--bd)}
.card{transition:.2s}.card:hover{stroke:var(--ac);stroke-width:2}.peak{stroke:var(--bl);stroke-width:1}.cell:hover{stroke:var(--fg);stroke-width:1}.lg{font-size:14px}.acf{fill:var(--ac)}.spark{fill:none;stroke:var(--ac);stroke-width:1.5}.dash{stroke:var(--bd);stroke-dasharray:3 3}
a{cursor:pointer}a:hover .card,a:focus .card{stroke:var(--ac);stroke-width:2}a:hover text.go,a:focus text.go{text-decoration:underline}a:focus{outline:none}a:focus-visible .card{stroke-width:3}
@keyframes rise{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
@keyframes glow{0%%,100%%{opacity:.55}50%%{opacity:1}}
.langs:hover .lang{opacity:.35}.langs .lang:hover{opacity:1}.pt{fill:transparent;stroke:none}.pt:hover{fill:var(--ac)}a:hover .spark{stroke-width:2.5}
.rise{animation:rise .6s ease-out both}.glow{animation:glow 2.4s ease-in-out infinite}
@media(prefers-reduced-motion:reduce){.rise,.glow{animation:none}}
</style>""" % FONT

WEEKDAYS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
CHAR = 0.6  # largura de um caractere monoespaçado, em em
HEAT = (0.12, 0.3, 0.55, 0.8, 1.0)


def anim(attr, values, start, dur, total, discrete=False):
    """<animate> que segura o 1.º valor até `start`; o valor base (estático) é o final."""
    values = [str(v) for v in values]
    if not discrete:
        kt = [0.0, start / total, 1.0]
        values = [values[0]] + values
        mode = ""
    else:
        n = len(values)
        kt = [0.0] + [(start + i * dur / max(1, n - 1)) / total for i in range(n)]
        values = [values[0]] + values
        mode = ' calcMode="discrete"'
    keytimes = ";".join("%.4f" % min(1.0, k) for k in kt)
    return ('<animate attributeName="%s" values="%s" keyTimes="%s"%s dur="%.2fs" begin="0s" fill="freeze"/>'
            % (attr, ";".join(values), keytimes, mode, total))


def counter(x, y, value):
    """Contador animado: 16 quadros discretos; o valor final é o estado estático."""
    frames = 16
    out = ['<text class="big" x="%d" y="%d">%s<set attributeName="opacity" to="0" begin="0s" dur="1.6s" fill="remove"/></text>'
           % (x, y, text(value))]
    for k in range(frames):
        v = round(value * (1 - (1 - k / frames) ** 2))
        out.append('<text class="big" x="%d" y="%d" opacity="0">%s<set attributeName="opacity" to="1" begin="%.1fs" dur="0.1s" fill="remove"/></text>'
                   % (x, y, text(v), k * 0.1))
    return "\n".join(out)


def rel_date(gen, pushed):
    import datetime as dt
    days = (dt.date.fromisoformat(gen[:10]) - dt.date.fromisoformat(pushed[:10])).days
    return "hoje" if days <= 0 else "ontem" if days == 1 else "há %d d" % days


def level(count, top):
    if count <= 0 or top <= 0:
        return 0
    return min(4, 1 + (count - 1) * 4 // top)


def render_heading(label):
    """Título de secção no estilo do terminal (`## label`), para usar fora dele."""
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="320" height="30" viewBox="0 0 320 30" role="img" aria-labelledby="t">\n'
            '<title id="t">%s</title>\n%s\n<text class="bl rise" x="2" y="20">## %s</text>\n</svg>\n'
            % (text(label, 40), STYLE, text(label, 40)))


def render_card(item, login):
    """Card de destaque autónomo (o README liga-o ao repo; hover só com o SVG aberto)."""
    W, H = 290, 150
    out = ['<rect class="card" x="1" y="1" width="%d" height="%d" rx="6"/>' % (W - 2, H - 2),
           '<text x="14" y="32" font-weight="bold" font-size="16">%s</text>' % text(item["name"], 24)]
    for j, ln in enumerate(wrap(item["description"], 32, 2)):
        out.append('<text class="mut" x="14" y="%d">%s</text>' % (56 + j * 18, text(ln, 34)))
    vals = [int(v) for v in item["sparkline"]]
    peak = max(vals) or 1
    step = (W - 28) / max(1, len(vals) - 1)
    pts = " ".join("%.1f,%.1f" % (14 + k * step, 92 + 24 * (1 - v / peak)) for k, v in enumerate(vals))
    out.append('<polyline class="spark glow" points="%s" pathLength="200" stroke-dasharray="200">%s</polyline>'
               % (pts, anim("stroke-dashoffset", [200, 0], 0.0, 1.6, 1.6)))
    for k, v in enumerate(vals):
        out.append('<circle class="pt" cx="%.1f" cy="%.1f" r="4"><title>semana %d: %d commits</title></circle>'
                   % (14 + k * step, 92 + 24 * (1 - v / peak), k + 1 - len(vals), v))
    meta = " · ".join(p for p in (item["language"], "★ %d" % item["stars"]) if p)
    out.append('<text class="mut sm" x="14" y="136">%s</text>' % text(meta, 40))
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" role="img" aria-labelledby="t">\n'
            '<title id="t">%s</title>\n%s\n%s\n</svg>\n'
            % (W, H, W, H, text(item["name"], 60), STYLE,
               link(repo_url(login, item["name"]), "\n".join(out), item["name"])))


def render_terminal(s, cfg):
    W, PAD, IN = 880, 22, 834  # janela; recuo; largura útil
    gen = s["generated_at"]
    out = []
    y = 37
    # barra de título
    out.append('<rect class="bar" x="0.5" y="0.5" width="%d" height="%d"/>' % (W - 1, y))
    out.append('<line class="line" x1="1" y1="%d" x2="%d" y2="%d"/>' % (y, W - 1, y))
    for cx, c in ((20, "#ff5f56"), (39, "#ffbd2e"), (58, "#27c93f")):
        out.append('<circle cx="%d" cy="18" r="6" fill="%s"/>' % (cx, c))
    out.append('<text class="mut sm" x="76" y="22">~/%s — zsh</text>' % text(s["login"], 40))
    # $ whoami
    first = (s["name"] or s["login"]).split()[0]
    line = "whoami: " + collect.clean(first, 20)
    n = len(line) + 2
    cw = 20 * CHAR
    y += 22 + 20
    widths = [round(k * cw, 1) for k in range(n + 1)]
    out.append('<clipPath id="ty"><rect x="%d" y="%d" width="%.1f" height="30">%s</rect></clipPath>'
               % (PAD, y - 22, widths[-1], anim("width", widths, 0.2, 1.4, 1.6, True)))
    out.append('<text class="h1" x="%d" y="%d" clip-path="url(#ty)"><tspan class="mut">$ </tspan>%s</text>'
               % (PAD, y, text(line, 60)))
    out.append('<rect class="acf" x="%.1f" y="%d" width="12" height="24">%s'
               '<animate attributeName="opacity" values="1;0" calcMode="discrete" dur="0.8s" repeatCount="indefinite"/></rect>'
               % (PAD + widths[-1], y - 19, anim("x", [PAD + w for w in widths], 0.2, 1.4, 1.6, True)))
    # bio e etiquetas
    y += 8 + 22
    bio = s["bio"] or cfg["bio"]
    if bio:
        out.append('<text x="%d" y="%d">%s</text>' % (PAD, y, text(bio)))
    y += 14
    x = PAD
    for badge in cfg["badges"]:
        w = round(len(badge) * 12 * CHAR + 20, 1)
        out.append('<rect class="pill" x="%.1f" y="%d" width="%.1f" height="22" rx="11"/>' % (x + 0.5, y, w))
        out.append('<text class="sm" x="%.1f" y="%d" text-anchor="middle">%s</text>' % (x + w / 2, y + 15, text(badge, 24)))
        x += w + 6
    y += 22

    def h2(label):
        nonlocal y
        y += 26 + 14
        out.append('<text class="bl rise" x="%d" y="%d">## %s</text>' % (PAD, y, text(label, 40)))
        y += 12

    # stats
    h2("estatísticas")
    st = s["stats"]
    cols = [(st["commits_year"], "commits (ano)", "overview"), (st["repos_public"], "repos", "repositories"),
            (st["stars"], "estrelas", "repositories"), (st["contributions_year"], "contribuições (ano)", "overview")]
    cwid = (IN - 30) / 4
    for i, (value, label, tab) in enumerate(cols):
        cx = PAD + i * (cwid + 10)
        inner = ('<rect class="card" x="%.1f" y="%d" width="%.1f" height="68" rx="6"/>\n' % (cx + 0.5, y, cwid)
                 + counter(round(cx + 12), y + 36, value)
                 + '\n<text class="mut sm go" x="%d" y="%d">%s</text>' % (round(cx + 12), y + 56, text(label, 30)))
        out.append(link(profile_url(s["login"], tab), inner, label))
    y += 68
    # linguagens
    h2("linguagens")
    langs = s["languages"]
    total = sum(l["percent"] for l in langs) or 1
    avail = IN - 2 * (len(langs) - 1)
    out.append('<clipPath id="lc"><rect x="%d" y="%d" width="%d" height="10" rx="5"/></clipPath><g class="langs" clip-path="url(#lc)">'
               % (PAD, y, IN))
    x = float(PAD)
    for i, lang in enumerate(langs):
        w = round(avail * lang["percent"] / total, 2)
        out.append('<rect class="lang" x="%.2f" y="%d" width="%.2f" height="10" fill="%s">%s<title>%s: %.1f%%</title></rect>'
                   % (x, y, w, color(lang["color"]), anim("width", [0, w], 0.2 * i, 1.0, 1.0 + 0.2 * i),
                      text(lang["name"], 24), lang["percent"]))
        x += w + 2
    out.append("</g>")
    y += 10 + 18
    x = PAD
    for lang in langs:
        label = "%s %d%%" % (collect.clean(lang["name"], 16), round(lang["percent"]))
        out.append('<text class="mut lg" x="%.1f" y="%d">%s</text>' % (x, y, text(label, 24)))
        x += len(label) * 14 * CHAR + 18
    # contribuições
    h2("contribuições")
    weeks = s["calendar"]
    top = max((d["count"] for w in weeks for d in w), default=0)
    cs = round((IN - 2 * (len(weeks) - 1)) / max(1, len(weeks)), 2)  # células ocupam a largura toda
    peak = max(((wi, di) for wi, w in enumerate(weeks) for di in range(len(w))),
               key=lambda k: (weeks[k[0]][k[1]]["count"], -k[0], -k[1]), default=None) if top > 0 else None
    tips, rules, n = [], [], 0
    for wi, week in enumerate(weeks):
        for di, day in enumerate(week):
            op = HEAT[level(day["count"], top)]
            delay = (wi * 2 + di) * 0.01
            cx, cy = PAD + wi * (cs + 2), y + di * (cs + 2)
            out.append('<rect id="c%d" class="acf cell%s" x="%.2f" y="%.2f" width="%.2f" height="%.2f" rx="2" opacity="%s">%s</rect>'
                       % (n, " peak" if peak == (wi, di) else "", cx, cy, cs, cs, op, anim("opacity", [0, op], delay, 0.4, 0.4 + delay)))
            wd = WEEKDAYS[dt.date.fromisoformat(day["date"]).weekday()]
            label = "%s, %s · %s contribuições%s" % (wd, collect.clean(day["date"], 10), int(day["count"]),
                                                    " (pico)" if peak == (wi, di) else "")
            tw = round(len(label) * 7.6 + 18)
            tx = min(max(cx + cs / 2 - tw / 2, PAD), PAD + IN - tw)
            ty = cy - 30 if di >= 2 else cy + cs + 6
            tips.append('<g id="p%d" class="tip"><rect class="tipbg" x="%.1f" y="%.1f" width="%d" height="22" rx="4"/>'
                        '<text class="sm" x="%.1f" y="%.1f">%s</text></g>' % (n, tx, ty, tw, tx + 9, ty + 15, text(label, 60)))
            rules.append("#c%d:hover~#p%d" % (n, n))
            n += 1
    out.append("<style>.tip{display:none;pointer-events:none}.tipbg{fill:var(--pn);stroke:var(--ac)}%s{display:block}</style>"
               % ",".join(rules))
    out.extend(tips)
    y += int(7 * (cs + 2) - 2)
    # git log
    h2("git log --recent")
    for i, item in enumerate(s["recent"]):
        ly = y + 6 + i * 20
        label = "push · %s · %s" % (collect.clean(item["name"], 60), rel_date(gen, item["pushed_at"]))
        out.append(link(repo_url(s["login"], item["name"]),
                        '<text class="go" x="%d" y="%d"><tspan class="ac">&gt; </tspan>%s%s</text>'
                        % (PAD, ly, text(label, 100), anim("opacity", [0, 1], 1.8 + i * 0.3, 0.4, 2.2 + i * 0.3)),
                        item["name"]))
    y += 6 + max(1, len(s["recent"])) * 20 + 20
    # rodapé
    out.append('<line class="dash" x1="%d" y1="%d" x2="%d" y2="%d"/>' % (PAD, y, PAD + IN, y))
    out.append('<text class="mut sm" x="%d" y="%d">atualizado em %s · gerado por GitHub Action</text>'
               % (PAD, y + 22, text(gen[:10], 10)))
    height = y + 22 + 22
    head = ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" role="img" aria-labelledby="t">\n'
            '<title id="t">Terminal de %s: estatísticas, linguagens, contribuições e destaques</title>\n%s\n'
            '<rect class="win" x="0.5" y="0.5" width="%d" height="%d" rx="10"/>\n'
            '<clipPath id="w"><rect width="%d" height="%d" rx="10"/></clipPath><g clip-path="url(#w)">\n'
            % (W, height, W, height, text(s["login"], 40), STYLE, W - 1, height - 1, W, height))
    return head + "\n".join(out) + "\n</g>\n</svg>\n"


# ---------------------------------------------------------------- README

def render_readme(s):
    login = s["login"]
    lines = []
    if s["bio"]:
        lines += [md(s["bio"]), ""]
    lines += ["![Terminal do perfil](assets/terminal.svg)", ""]
    cards = ["[![%s](assets/card-%d.svg)](%s)" % (md(it["name"], 100), i + 1, repo_url(login, it["name"]))
             for i, it in enumerate(s["featured"])]
    lines += ["![Destaques](assets/heading-destaques.svg)", ""]
    lines += [" ".join(cards), ""] if cards else ["Sem destaques configurados.", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------- GitHub Pages

PAGE = """<!doctype html>
<html lang="pt"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(login)s</title>
<style>
:root{--bg:#fff;--fg:#24292f}
@media(prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#c9d1d9}}
body{margin:0;padding:24px 16px;background:var(--bg);color:var(--fg);font:16px system-ui,sans-serif}
main{max-width:900px;margin:0 auto}.term{display:block;width:100%%;max-width:880px;height:auto;aspect-ratio:880/%(th)d}
h1,h2{font:600 20px ui-monospace,Consolas,monospace;color:#3fb950}h2{font-size:14px;color:#58a6ff;margin:28px 0 8px}
@media(prefers-color-scheme:light){h1{color:#1a7f37}h2{color:#0969da}}
.cards{display:flex;flex-wrap:wrap;gap:12px;margin-top:16px}
</style></head><body><main>
<h1>%(login)s</h1><p>%(bio)s</p>
<object class="term" type="image/svg+xml" data="assets/terminal.svg" aria-label="Terminal do perfil"></object>
<h2>## destaques</h2><div class="cards">%(cards)s</div>
</main></body></html>
"""


def render_page(s, th):
    cards = "".join('<object type="image/svg+xml" data="assets/card-%d.svg" aria-label="%s" width="290" height="150"></object>'
                    % (i + 1, text(it["name"], 60)) for i, it in enumerate(s["featured"]))
    return PAGE % {"login": text(s["login"], 40), "bio": text(s["bio"] or "", 200), "cards": cards, "th": th}


# ---------------------------------------------------------------- main

def validate(s):
    collect.validate(s)
    if not collect.LOGIN_RE.match(s["login"]):
        raise RenderError("login inválido")
    for w in s["calendar"]:
        if len(w) > 7:
            raise RenderError("calendário inválido")
        for d in w:
            if not collect.DATE_RE.match(d["date"]) or not isinstance(d["count"], int) or d["count"] < 0:
                raise RenderError("calendário inválido")
    for item in s["featured"]:
        if len(item["sparkline"]) > collect.SPARK_WEEKS or not all(isinstance(v, int) for v in item["sparkline"]):
            raise RenderError("sparkline inválida")
    return s


BADGE_RE = re.compile(r"^[A-Za-z0-9 .+#_/-]{1,24}$")


def check_cfg(cfg):
    cfg = cfg or {}
    bio = cfg.get("bio", "")
    badges = cfg.get("badges", [])
    if not isinstance(bio, str) or not isinstance(badges, list) or len(badges) > 8:
        raise RenderError("config inválida")
    if not all(isinstance(b, str) and BADGE_RE.match(b) for b in badges):
        raise RenderError("config inválida: badges")
    return {"bio": collect.clean(bio, 200), "badges": badges}


def render_all(s, cfg=None):
    validate(s)
    cfg = check_cfg(cfg)
    terminal = render_terminal(s, cfg)
    th = int(re.search(r'height="(\d+)"', terminal).group(1))
    return {
        "assets/terminal.svg": terminal,
        **{"assets/card-%d.svg" % (i + 1): render_card(it, s["login"]) for i, it in enumerate(s["featured"])},
        "assets/heading-destaques.svg": render_heading("destaques"),
        "README.md": render_readme(s),
        "index.html": render_page(s, th),
    }


def write_all(files, root=ROOT):
    root = Path(root)
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(content.encode("utf-8"))  # bytes: LF estável em Windows
        tmp.replace(path)


def main():
    try:
        stats = json.loads(STATS.read_text(encoding="utf-8"))
        files = render_all(stats, json.loads(CONFIG.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError, collect.CollectError, RenderError) as exc:
        print("render falhou (nada alterado): %s" % exc, file=sys.stderr)
        return 1
    write_all(files)
    print("gerados: " + ", ".join(sorted(files)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
