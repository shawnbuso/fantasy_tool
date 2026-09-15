"""Turn a recap into HTML that survives being pasted into Gmail.

Gmail strips <style> blocks and class attributes, so anything that isn't an inline
style attribute is lost the moment the paste lands. Everything here is therefore
inlined per element, which is ugly to read and the only thing that actually works.

Open the output in a browser, select all, copy, paste into the Gmail composer. The
formatting comes across because the clipboard carries the rendered HTML.

    uv run python .../to_email_html.py --recap week-01-recap.md --out week-01.html

The "Notes for you, not for the league" section is dropped -- it is addressed to the
author and must never reach the league.
"""

import argparse
import html
import re
from pathlib import Path

# One place to change the look. Georgia because it renders well in every mail client
# and reads less like a memo than the sans-serif default.
BODY = "font-family:Georgia,'Times New Roman',serif;font-size:15px;line-height:1.55;color:#1a1a1a;max-width:640px;"
H1 = "font-family:Georgia,serif;font-size:24px;font-weight:700;margin:0 0 4px;color:#111;"
H2 = "font-family:Georgia,serif;font-size:13px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:#8a6d3b;margin:28px 0 10px;border-bottom:1px solid #e0d8c8;padding-bottom:5px;"
H3 = "font-family:Georgia,serif;font-size:17px;font-weight:700;margin:20px 0 6px;color:#111;"
P = "margin:0 0 14px;"
LI = "margin:0 0 7px;"
TH = "text-align:left;padding:6px 10px;border-bottom:2px solid #8a6d3b;font-size:13px;font-weight:700;"
TD = "padding:5px 10px;border-bottom:1px solid #eee;font-size:14px;"
HR = "border:0;border-top:1px solid #e0d8c8;margin:26px 0;"


def inline(text: str) -> str:
    """Bold, italics and code, escaped first so stray angle brackets can't leak."""
    out = html.escape(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", out)
    out = re.sub(r"`(.+?)`", r"<code>\1</code>", out)
    return out


def convert(markdown: str) -> tuple[str, str]:
    """Returns the pasteable HTML body, and the subject line to type into Gmail.

    Everything above the `**Subject:` line is scaffolding for the author -- the draft
    number, what changed since last night -- and none of it belongs in the email. So
    the body starts *after* the subject, and the subject comes back separately rather
    than being pasted in as a heading nobody wants.
    """
    # The notes are for the author. Cutting them here rather than trusting anyone to
    # remember is the whole point of doing this in a script.
    markdown = markdown.split("## Notes for you")[0]

    subject = ""
    found = re.search(r"^\*\*Subject:\s*(.+?)\*\*\s*$", markdown, re.MULTILINE)
    if found:
        subject = found.group(1).strip()
        markdown = markdown[found.end() :]

    lines = markdown.split("\n")
    parts: list[str] = []
    paragraph: list[str] = []
    numbered: list[str] = []
    table: list[list[str]] = []

    def flush_paragraph() -> None:
        if paragraph:
            parts.append(f'<p style="{P}">{inline(" ".join(paragraph))}</p>')
            paragraph.clear()

    def flush_list() -> None:
        if numbered:
            items = "".join(f'<li style="{LI}">{inline(i)}</li>' for i in numbered)
            parts.append(f'<ol style="margin:0 0 16px;padding-left:22px;">{items}</ol>')
            numbered.clear()

    def flush_table() -> None:
        if not table:
            return
        head, *body = table
        cells = "".join(f'<th style="{TH}">{inline(c)}</th>' for c in head)
        rows = [f"<tr>{cells}</tr>"]
        for row in body:
            rows.append(
                "".join(f'<td style="{TD}">{inline(c)}</td>' for c in row).join(("<tr>", "</tr>"))
            )

        parts.append(
            '<table cellpadding="0" cellspacing="0" '
            'style="border-collapse:collapse;width:100%;margin:0 0 18px;">'
            + "".join(rows)
            + "</table>"
        )
        table.clear()

    def flush_all() -> None:
        flush_paragraph()
        flush_list()
        flush_table()

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()

        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):  # the |---|---| separator
                continue
            flush_paragraph()
            flush_list()
            table.append(cells)
            continue
        flush_table()

        if not stripped:
            flush_paragraph()
            flush_list()
            continue
        if stripped == "---":
            flush_all()
            parts.append(f'<hr style="{HR}">')
            continue
        if stripped.startswith("### "):
            flush_all()
            parts.append(f'<h3 style="{H3}">{inline(stripped[4:])}</h3>')
            continue
        if stripped.startswith("## "):
            flush_all()
            parts.append(f'<h2 style="{H2}">{inline(stripped[3:])}</h2>')
            continue
        if stripped.startswith("# "):
            flush_all()
            parts.append(f'<h1 style="{H1}">{inline(stripped[2:])}</h1>')
            continue
        if re.match(r"^\d+\.\s", stripped):
            flush_paragraph()
            numbered.append(re.sub(r"^\d+\.\s", "", stripped))
            continue
        # a wrapped continuation of the numbered item above
        if numbered and raw.startswith("   "):
            numbered[-1] += " " + stripped
            continue
        paragraph.append(stripped)

    flush_all()
    # A leading rule is left over from the scaffolding we just cut.
    while parts and parts[0].startswith("<hr"):
        parts.pop(0)
    return f'<div style="{BODY}">' + "\n".join(parts) + "</div>", subject


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recap", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    body, subject = convert(args.recap.read_text())
    args.out.write_text(
        "<!doctype html><meta charset='utf-8'>"
        "<title>Weekly recap</title>"
        "<body style='margin:32px;background:#fff;'>" + body + "</body>"
    )
    print(f"wrote {args.out}")
    if subject:
        print(f"\nSubject line:  {subject}")
    print("\nOpen the file in a browser, select all, copy, paste into the Gmail body.")


if __name__ == "__main__":
    main()
