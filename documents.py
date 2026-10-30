"""Turn a generated result into a print-ready web page and, if LaTeX is installed, a real PDF."""
import html
import re
import shutil
import subprocess
import tempfile
import unicodedata
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

TEMPLATES = Path(__file__).resolve().parent / "templates"
CV_PAGE_LIMIT, LETTER_PAGE_LIMIT = 2, 1

DOC_STYLE = """
@page { size: A4; margin: 14mm 15mm; }
* { box-sizing: border-box; }
body { margin: 0; background: #e9e9ec; color: #1b1b1f; font: 10.5pt/1.42 Georgia, "Times New Roman", serif; }
.sheet { width: 210mm; min-height: 297mm; margin: 16px auto; padding: 14mm 15mm; background: #fff; }
h1 { font-size: 21pt; font-weight: 400; margin: 0; }
.headline { color: #555; margin-top: 1px; }
.contact { font: 9pt/1.5 Arial, sans-serif; color: #444; margin: 6px 0 12px; }
a { color: inherit; }
h2 { font: 700 9.5pt Arial, sans-serif; letter-spacing: .07em; text-transform: uppercase; color: #1d4ed8;
     border-bottom: 1px solid #c7cbd6; margin: 14px 0 6px; padding-bottom: 2px; }
.entry { margin-bottom: 8px; break-inside: avoid; }
.top { display: flex; justify-content: space-between; gap: 12px; }
.when { font: 9pt Arial, sans-serif; color: #555; white-space: nowrap; }
.sub { font-style: italic; color: #333; }
ul { margin: 3px 0 0; padding-left: 15px; }
li { margin-bottom: 1.5px; }
.skills div { margin-bottom: 2px; }
.letter p { margin: 0 0 11px; }
.letter .date { color: #555; margin: 18px 0; }
.bar { position: sticky; top: 0; background: #1b1b1f; color: #fff; padding: 9px 16px; font: 13px Arial, sans-serif;
       display: flex; gap: 14px; align-items: center; }
.bar button { font: inherit; padding: 5px 12px; cursor: pointer; }
@media print { body { background: #fff; } .sheet { width: auto; min-height: 0; margin: 0; padding: 0; } .bar { display: none; } }
"""


def esc(s):
    return html.escape(str(s or ""))


def rich(s):
    """Escape, then turn **x** into bold."""
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc(s))


def safe_href(url):
    url = str(url or "").strip()
    if url.startswith(("mailto:", "tel:")):
        return url
    if not re.match(r"https?://", url, re.I):
        url = "https://" + url
    return url if urlparse(url).netloc else ""


def link_label(url):
    return re.sub(r"^https?://(www\.)?", "", url.strip()).rstrip("/")


def contact_html(p):
    bits = []
    if p.get("email"):
        bits.append(f'<a href="{esc("mailto:" + p["email"])}">{esc(p["email"])}</a>')
    for field in ("phone", "location"):
        if p.get(field):
            bits.append(esc(p[field]))
    for field in ("linkedin", "github", "website"):
        if p.get(field):
            bits.append(f'<a href="{esc(safe_href(p[field]))}">{esc(link_label(p[field]))}</a>')
    return " &nbsp;|&nbsp; ".join(bits)


def page_shell(title, body):
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{esc(title)}</title>'
            f"<style>{DOC_STYLE}</style></head><body>"
            '<div class="bar"><button onclick="print()">Save as PDF</button>'
            "<span>In the print window, choose Save as PDF and turn off headers and footers.</span></div>"
            f'<div class="sheet">{body}</div></body></html>')


def file_title(profile, job, kind):
    return re.sub(r"[^\w-]+", "_", f"{profile.get('name', '')}_{kind}_{job.get('company', '')}").strip("_")


def render_cv(profile, job):
    cv = job["result"]["cv"]
    out = [f"<h1>{esc(profile.get('name'))}</h1>"]
    if cv.get("headline"):
        out.append(f'<div class="headline">{esc(cv["headline"])}</div>')
    out.append(f'<div class="contact">{contact_html(profile)}</div>')
    if cv.get("summary"):
        out.append(f"<p>{rich(cv['summary'])}</p>")
    for section in cv.get("sections", []):
        out.append(f"<h2>{esc(section.get('title'))}</h2>")
        for e in section.get("entries", []):
            where = " &middot; ".join(esc(x) for x in (e.get("subheading"), e.get("location")) if x)
            out.append(f'<div class="entry"><div class="top"><b>{esc(e.get("heading"))}</b>'
                       f'<span class="when">{esc(e.get("dates"))}</span></div>')
            if where:
                out.append(f'<div class="sub">{where}</div>')
            if e.get("bullets"):
                out.append("<ul>" + "".join(f"<li>{rich(b)}</li>" for b in e["bullets"]) + "</ul>")
            out.append("</div>")
    if cv.get("skills"):
        out.append('<h2>Skills</h2><div class="skills">')
        out += [f"<div><b>{esc(s.get('label'))}:</b> {esc(s.get('items'))}</div>" for s in cv["skills"]]
        out.append("</div>")
    return page_shell(file_title(profile, job, "CV"), "".join(out))


def render_letter(profile, job):
    letter = job["result"]["letter"]
    body = "".join(f"<p>{rich(p)}</p>" for p in letter.get("paragraphs", []))
    return page_shell(file_title(profile, job, "Cover_Letter"), (
        f'<h1>{esc(profile.get("name"))}</h1><div class="contact">{contact_html(profile)}</div>'
        f'<div class="letter"><div class="date">{date.today().strftime("%d %B %Y")}</div>'
        f'<p>{esc(letter.get("greeting"))}</p>{body}<p>Kind regards,<br>{esc(profile.get("name"))}</p></div>'))


# ---------------------------------------------------------------- LaTeX
TEX_CHARS = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
             "~": r"\textasciitilde{}", "^": r"\textasciicircum{}", "<": r"\textless{}", ">": r"\textgreater{}", "|": r"\textbar{}",
             '"': "''", "\u2192": r"$\rightarrow$", "\u2190": r"$\leftarrow$", "\u2022": r"\textbullet{}", "\u00d7": r"$\times$",
             "\u2264": r"$\leq$", "\u2265": r"$\geq$", "\u2248": r"$\approx$", "\u2212": "-", "\u00a0": "~",
             "\u2018": "`", "\u2019": "'", "\u201c": "``", "\u201d": "''", "\u2013": "--", "\u2014": "---", "\u2026": "..."}
TEX_ENCODABLE = re.compile(r"[\x20-\x7e\u00a1-\u017f]")


def tex(s, dropped=None):
    """Plain text to safe LaTeX. All text here came from an AI or a job posting, so none of it may run as code."""
    out = []
    for ch in unicodedata.normalize("NFC", str(s or "")):
        if ch in TEX_CHARS:
            out.append(TEX_CHARS[ch])
        elif TEX_ENCODABLE.fullmatch(ch):
            out.append(ch)
        elif ch in "\n\t":
            out.append(" ")
        elif dropped is not None:
            dropped.add(ch)
    return "".join(out)


def tex_rich(s, dropped=None):
    return re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", tex(s, dropped))


def tex_url(url):
    """Only characters that are safe in \\href, so a link can't break out into LaTeX code."""
    return re.sub(r"[^A-Za-z0-9:/.?=@+,;!_~-]", "", safe_href(url)).replace("_", r"\_").replace("~", r"\~")


def tex_contact(p):
    first, second = [], []
    if p.get("email"):
        first.append(rf"\href{{{tex_url('mailto:' + p['email'])}}}{{{tex(p['email'])}}}")
    first += [tex(p[f]) for f in ("phone", "location") if p.get(f)]
    for field in ("linkedin", "github", "website"):
        if p.get(field):
            second.append(rf"\href{{{tex_url(p[field])}}}{{{tex(link_label(p[field]))}}}")
    rows = [r" \hspace{4pt}|\hspace{4pt} ".join(row) for row in (first, second) if row]
    return r"\\[2pt]".join(rows)


def fill(template, **values):
    text = (TEMPLATES / template).read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace(f"<<{key}>>", value)
    return text


def cv_tex(profile, job, dropped=None):
    cv = job["result"]["cv"]
    body = []
    if cv.get("summary"):
        body.append(r"\section{Profile}" + "\n" + tex_rich(cv["summary"], dropped))
    for section in cv.get("sections", []):
        body.append(rf"\section{{{tex(section.get('title'), dropped)}}}")
        for e in section.get("entries", []):
            where = " | ".join(tex(x, dropped) for x in (e.get("subheading"), e.get("location")) if x)
            body.append(rf"\entry\jobhead{{{tex(e.get('heading'), dropped)}}}{{{tex(e.get('dates'), dropped)}}}{{{where}}}")
            if e.get("bullets"):
                items = "\n".join(rf"\item {tex_rich(b, dropped)}" for b in e["bullets"])
                body.append(r"\begin{itemize}[label=\textendash,leftmargin=0.16in,itemsep=1.5pt,parsep=0pt,topsep=3pt]"
                            f"\n{items}\n" + r"\end{itemize}")
    if cv.get("skills"):
        rows = r"\\[2pt]".join(rf"\textbf{{{tex(s.get('label'), dropped)}:}} {tex(s.get('items'), dropped)}" for s in cv["skills"])
        body.append(r"\section{Skills}" + "\n" + rows)
    return fill("cv.tex", NAME=tex(profile.get("name"), dropped), HEADLINE=tex(cv.get("headline"), dropped),
                CONTACT=tex_contact(profile), BODY="\n\n".join(body))


def letter_tex(profile, job, dropped=None):
    letter = job["result"]["letter"]
    return fill("letter.tex", NAME=tex(profile.get("name"), dropped), CONTACT=tex_contact(profile),
                DATE=date.today().strftime("%d %B %Y"), GREETING=tex(letter.get("greeting"), dropped),
                BODY="\n\n".join(tex_rich(p, dropped) for p in letter.get("paragraphs", [])))


def latex_ready():
    return bool(shutil.which("pdflatex"))


def compile_pdf(source, name, out_dir):
    """Returns (pdf path, pages). Runs in a throwaway folder with shell commands turned off."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "doc.tex"
        path.write_text(source, encoding="utf-8")
        log = ""
        for _ in range(2):
            try:
                log = subprocess.run(["pdflatex", "-no-shell-escape", "-interaction=nonstopmode", "doc.tex"], cwd=tmp,
                                     capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=180).stdout
            except subprocess.TimeoutExpired:
                return None, 0
        pdf = path.with_suffix(".pdf")
        if not pdf.exists():
            return None, 0
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"{name}.pdf"
        shutil.copyfile(pdf, target)
    pages = re.search(r"Output written on .*?\((\d+) pages?", log.replace("\n", ""))
    return target, int(pages[1]) if pages else 0


def build_pdfs(profile, job, out_dir):
    """Returns ({'cv': filename, 'letter': filename}, warnings). Web-page versions always remain as a fallback."""
    if not latex_ready():
        return {}, []
    made, warnings, dropped = {}, [], set()
    for kind, make, name, limit in (("cv", cv_tex, "CV", CV_PAGE_LIMIT), ("letter", letter_tex, "Cover_Letter", LETTER_PAGE_LIMIT)):
        filename = file_title(profile, job, name)
        source = make(profile, job, dropped)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{kind}.tex").write_text(source, encoding="utf-8")
        pdf, pages = compile_pdf(source, filename, out_dir)
        if pdf is None:
            warnings.append(f"LaTeX could not build the {name.replace('_', ' ')} PDF; use the web version instead")
            continue
        made[kind] = pdf.name
        if pages > limit:
            warnings.append(f"the {name.replace('_', ' ')} PDF is {pages} pages (aim for {limit})")
    if dropped:
        warnings.append("LaTeX can't show these characters, so they were left out of the PDFs: " + " ".join(sorted(dropped)))
    return made, warnings
