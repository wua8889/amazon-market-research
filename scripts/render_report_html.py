# -*- coding: utf-8 -*-
"""Render a Markdown report into a standalone, print-ready HTML file.

Dependency-free: a small Markdown subset renderer (headings, tables, lists, blockquotes,
bold / italic / inline code, links, horizontal rules, code fences) plus a print stylesheet.
All CSS is inlined, so the output opens offline and prints to A4 as-is.

Usage:
    python scripts/render_report_html.py --input output/example/example_market_report.md \
                                         --output output/example/example_market_report.html
"""

from __future__ import annotations

import argparse
import html
import re
from pathlib import Path

CSS = """
:root { --ink:#1a1a1a; --muted:#6b6b6b; --line:#dcdcdc; --accent:#2f6b4f; --soft:#f6f8f6; --bg:#ffffff; }
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font-family: "Microsoft YaHei", "PingFang SC", "Hiragino Sans GB",
               "Source Han Sans SC", "Noto Sans CJK SC", sans-serif;
  font-size: 15px; line-height: 1.85; -webkit-font-smoothing: antialiased;
}
.page { max-width: 860px; margin: 0 auto; padding: 56px 40px 80px; }
h1 { font-size: 27px; margin: 0 0 22px; }
h2 { font-size: 21px; margin: 42px 0 16px; padding-bottom: 8px; border-bottom: 2px solid var(--accent); page-break-after: avoid; }
h3 { font-size: 17px; margin: 28px 0 12px; color: var(--accent); page-break-after: avoid; }
p { margin: 12px 0; }
blockquote {
  margin: 18px 0; padding: 12px 18px; background: var(--soft);
  border-left: 4px solid var(--accent); color: var(--muted);
}
table { width: 100%; border-collapse: collapse; margin: 18px 0; font-size: 14px; page-break-inside: avoid; }
th, td { border: 1px solid var(--line); padding: 8px 10px; text-align: left; vertical-align: top; }
th { background: var(--soft); font-weight: 600; }
code { background: var(--soft); padding: 1px 5px; border-radius: 3px; font-size: 13px;
       font-family: Consolas, "Cascadia Mono", monospace; }
pre { background: var(--soft); padding: 14px 16px; overflow-x: auto; border: 1px solid var(--line); }
pre code { background: none; padding: 0; }
ul, ol { margin: 12px 0; padding-left: 26px; }
hr { border: none; border-top: 1px solid var(--line); margin: 34px 0; }
a { color: var(--accent); }
@media print { .page { padding: 0; } h2 { page-break-after: avoid; } }
"""

BLOCK_START = re.compile(r"^(#{1,6}\s|[-*+]\s|\d+\.\s|\||>|```)")


def inline(text: str) -> str:
    t = html.escape(text, quote=False)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<!\*)\*([^*\s][^*]*)\*(?!\*)", r"<em>\1</em>", t)
    t = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', t)
    return t


def split_row(line: str):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def render_markdown(md: str) -> str:
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out, i, n = [], 0, len(lines)

    while i < n:
        raw = lines[i]
        s = raw.strip()

        if s.startswith("```"):
            i += 1
            buf = []
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(html.escape(lines[i]))
                i += 1
            i += 1
            out.append("<pre><code>%s</code></pre>" % "\n".join(buf))
            continue

        if not s:
            i += 1
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            lvl = len(m.group(1))
            out.append("<h%d>%s</h%d>" % (lvl, inline(m.group(2)), lvl))
            i += 1
            continue

        if re.match(r"^([-*_])\1{2,}$", s):
            out.append("<hr />")
            i += 1
            continue

        if s.startswith("|") and i + 1 < n and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            header = split_row(s)
            i += 2
            body = []
            while i < n and lines[i].strip().startswith("|"):
                body.append(split_row(lines[i].strip()))
                i += 1
            tbl = ["<table><thead><tr>"]
            tbl += ["<th>%s</th>" % inline(c) for c in header]
            tbl.append("</tr></thead><tbody>")
            for row in body:
                tbl.append("<tr>" + "".join("<td>%s</td>" % inline(c) for c in row) + "</tr>")
            tbl.append("</tbody></table>")
            out.append("".join(tbl))
            continue

        if re.match(r"^[-*+]\s+", s):
            items = []
            while i < n and re.match(r"^[-*+]\s+", lines[i].strip()):
                items.append(re.sub(r"^[-*+]\s+", "", lines[i].strip()))
                i += 1
            out.append("<ul>" + "".join("<li>%s</li>" % inline(x) for x in items) + "</ul>")
            continue

        if re.match(r"^\d+\.\s+", s):
            items = []
            while i < n and re.match(r"^\d+\.\s+", lines[i].strip()):
                items.append(re.sub(r"^\d+\.\s+", "", lines[i].strip()))
                i += 1
            out.append("<ol>" + "".join("<li>%s</li>" % inline(x) for x in items) + "</ol>")
            continue

        if s.startswith(">"):
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append("<blockquote>%s</blockquote>" % inline(" ".join(buf)))
            continue

        buf = []
        while i < n and lines[i].strip() and not BLOCK_START.match(lines[i].strip()):
            buf.append(lines[i].strip())
            i += 1
        if buf:
            out.append("<p>%s</p>" % inline(" ".join(buf)))
        else:
            i += 1

    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Render a Markdown report to standalone HTML.")
    ap.add_argument("--input", "-i", required=True, help="source Markdown file")
    ap.add_argument("--output", "-o", required=True, help="destination HTML file")
    ap.add_argument("--title", default="", help="page title (default: first heading)")
    args = ap.parse_args(argv)

    src = Path(args.input)
    if not src.exists():
        print("INPUT_NOT_FOUND:", src)
        return 1

    md = src.read_text(encoding="utf-8")
    body = render_markdown(md)

    title = args.title
    if not title:
        m = re.search(r"^#\s+(.*)$", md, re.M)
        title = m.group(1).strip() if m else src.stem

    page = (
        "<!doctype html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"utf-8\" />\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />\n"
        "<title>%s</title>\n<style>%s</style>\n</head>\n<body>\n<div class=\"page\">\n%s\n</div>\n</body>\n</html>\n"
        % (html.escape(title), CSS, body)
    )

    dst = Path(args.output)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(page, encoding="utf-8")
    print("HTML ->", dst)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())