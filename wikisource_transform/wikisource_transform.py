#!/usr/bin/env python3
"""
Transform a MediaWiki action=parse JSON response (French Wikisource
"Texte entier" page) into the custom XML schema:

  <document>
    <t><pb num="X"/>HEADING</t>
    <p>...</p>
    <pb num="X"/>
    <v>tab-indented verse\nlines</v>
    <ref target="#nN">(N)</ref> / <note id="nN"><p>...</p></note>
    <sc>small caps</sc>, <i>italic</i>

Usage:
    python3 wikisource_transform.py input.json output.xml

Input: raw JSON text as returned by
  https://fr.wikisource.org/w/api.php?action=parse&page=...&prop=text&format=json
"""
import sys, json, re
from lxml import html as lhtml
from lxml import etree

# ---------- tunables you will likely want to adjust ----------
T_FONT_SIZE_MIN = 185       # % font-size threshold: >= this -> <t>, else <st>
ST_FONT_SIZE_MIN = 110      # % font-size threshold: >= this (and < T) -> <st>
INDENT_EM_PER_TAB = 2       # how many em of min-width == one tab in verse
# ---------------------------------------------------------------

NBSP = "\u00a0"
ZWSP = "\ufeff"


def get_font_size_pct(style):
    m = re.search(r"font-size\s*:\s*(\d+)%", style or "")
    return int(m.group(1)) if m else None


def is_centered_bold_heading(div):
    style = div.get("style", "")
    if "text-align:center" not in style:
        return False
    txt = "".join(div.itertext()).strip()
    if not txt:
        return False
    has_bold = div.find(".//b") is not None
    fs = get_font_size_pct(style)
    return bool(has_bold or fs)


def clean_text(s):
    s = s.replace(NBSP, " ")
    s = re.sub(r"\s+\n", "\n", s)
    s = re.sub(r"[ \t]+", " ", s)
    return s


def inline_to_target(el, out):
    """Append lxml Elements/text for inline content (i, sc, sup refs) into out (a list of mixed str/Element),
    mutating a running 'tail buffer' pattern isn't trivial with lxml, so we build a small parallel tree instead."""
    pass  # superseded by build_inline() below


def build_inline(src_el, notes_by_id, dest_parent):
    """
    Recursively copy inline content of src_el into dest_parent (an lxml Element),
    converting <i> -> <i>, span.sc/abbr -> <sc> (abbr uses its title as content per source convention
    already, so we just take text), sup.reference/a[href^=#cite_note] -> <ref target="#nN">(N)</ref>,
    span.coquille -> <choice><sic>/<corr></choice> style left as plain corrected text (flag with comment).
    Drops page-number spans (handled separately by caller) and decorative rule/thumbnail spans.
    """
    def append_text(text):
        if not text:
            return
        if len(dest_parent) == 0:
            dest_parent.text = (dest_parent.text or "") + text
        else:
            last = dest_parent[-1]
            last.tail = (last.tail or "") + text

    append_text(src_el.text)

    for child in src_el:
        cls = child.get("class", "") or ""
        tag = child.tag

        # page-number marker: <span class="pagenum ws-pagenum" ...> nested inside <span>
        if child.find('.//span[@class="pagenum ws-pagenum"]') is not None or "ws-pagenum" in cls:
            num = None
            marker = child if "ws-pagenum" in cls else child.find('.//span[@class="pagenum ws-pagenum"]')
            if marker is not None:
                num = marker.get("id")
            pb = etree.SubElement(dest_parent, "pb")
            if num:
                pb.set("num", num)
            append_text(child.tail)
            continue

        if tag == "i":
            e = etree.SubElement(dest_parent, "i")
            build_inline(child, notes_by_id, e)
            append_text(child.tail)
            continue

        if tag == "sup" and "reference" in cls:
            a = child.find(".//a")
            href = a.get("href") if a is not None else None
            note_key = href.lstrip("#").replace("cite_note-", "n") if href else None
            ref = etree.SubElement(dest_parent, "ref")
            num_txt = "".join(child.itertext())
            num_txt = re.sub(r"[\[\]]", "", num_txt).strip()
            if note_key:
                ref.set("target", "#" + note_key)
            ref.text = f"({num_txt})" if num_txt else "(*)"
            append_text(child.tail)
            continue

        if tag == "span" and "sc" == cls:
            e = etree.SubElement(dest_parent, "sc")
            e.text = "".join(child.itertext())
            append_text(child.tail)
            continue

        if tag == "abbr":
            # e.g. <abbr title="Monsieur">M.</abbr> -- keep visible text as-is
            append_text("".join(child.itertext()))
            append_text(child.tail)
            continue

        if tag == "sup" and cls == "":
            # e.g. M<sup>lle</sup>
            e = etree.SubElement(dest_parent, "sup")
            e.text = "".join(child.itertext())
            append_text(child.tail)
            continue

        if tag == "span" and "coquille" in cls:
            # OCR correction: title=original, text=corrected
            orig = child.get("title", "")
            corr = "".join(child.itertext())
            choice = etree.SubElement(dest_parent, "choice")
            sic = etree.SubElement(choice, "sic")
            sic.text = orig
            c = etree.SubElement(choice, "corr")
            c.text = corr
            append_text(child.tail)
            continue

        if tag == "span" and "romain" in cls:
            e = etree.SubElement(dest_parent, "sc")
            e.text = "".join(child.itertext())
            append_text(child.tail)
            continue

        # decorative / noise: rule images, template styles, links w/o content we care about
        if tag in ("style", "link"):
            continue
        if tag == "span" and ("mw-default-size" in cls or "mw-valign-middle" in cls):
            continue

        # default: recurse and flatten unknown wrappers
        build_inline(child, notes_by_id, dest_parent)
        append_text(child.tail)


def append_verse_p(p_el, dest_v, notes_by_id, collected_notes):
    """Append one <p> of verse (a stanza) into dest_v (the running <v> element) as mixed
    content: indent spans -> tabs, <br/> -> newline, footnote sup -> <ref> child + queue note,
    pagenum spans are handled by the caller between stanzas."""

    def append_text(text):
        if not text:
            return
        if len(dest_v) == 0:
            dest_v.text = (dest_v.text or "") + text
        else:
            dest_v[-1].tail = (dest_v[-1].tail or "") + text

    def walk(el):
        append_text(el.text)
        for child in el:
            cls = child.get("class", "") or ""
            if child.tag == "br":
                append_text("\n")
                tail = child.tail or ""
                append_text(re.sub(r"^\n", "", tail, count=1))
                continue
            elif child.tag == "span":
                style = child.get("style", "")
                m = re.search(r"min-width\s*:\s*(\d+)em", style)
                if m and (child.text or "").strip("\ufeff \u200b") == "":
                    n_em = int(m.group(1))
                    append_text("\t" * max(1, n_em // INDENT_EM_PER_TAB))
                elif "coquille" in cls:
                    append_text("".join(child.itertext()))
                else:
                    walk(child)
            elif child.tag == "sup" and "reference" in cls:
                a = child.find(".//a")
                href = a.get("href") if a is not None else None
                key = href.lstrip("#").replace("cite_note-", "n") if href else None
                ref = etree.SubElement(dest_v, "ref")
                num_txt = re.sub(r"[\[\]]", "", "".join(child.itertext())).strip()
                if key:
                    ref.set("target", "#" + key)
                    collected_notes.append(key)
                ref.text = f"({num_txt})" if num_txt else "(*)"
            elif child.tag == "sup":
                append_text("".join(child.itertext()))
            else:
                walk(child)
            append_text(child.tail)

    walk(p_el)
    append_text("\n")  # stanza break


def process(tree, out_root):
    notes_by_id = {}
    for li in tree.findall('.//li'):
        lid = li.get("id", "")
        if lid.startswith("cite_note-"):
            key = "n" + lid[len("cite_note-"):]
            rt = li.find('.//span[@class="reference-text"]')
            notes_by_id[key] = rt if rt is not None else li

    body = tree.find('.//div[@class="prp-pages-output"]')
    if body is None:
        body = tree

    last_heading_tag = None  # for merging consecutive heading blocks of the same level

    def emit_pb(marker):
        nonlocal last_heading_tag
        pb = etree.SubElement(out_root, "pb")
        pb.set("num", marker.get("id"))
        last_heading_tag = None

    def is_poem_carrier(e):
        if e.tag != "div":
            return False
        if "poem" in (e.get("class", "") or ""):
            return True
        return e.find('.//div[@class="poem"]') is not None

    def is_bare_pagenum_span(e):
        return e.tag == "span" and e.find('.//span[@class="pagenum ws-pagenum"]') is not None

    children = list(body)
    i = 0
    while i < len(children):
        el = children[i]
        tag = el.tag
        cls = el.get("class", "") or ""

        if tag == "ol" and "references" in cls:
            i += 1
            continue  # notes are inlined at citation point instead
        if tag == "style":
            i += 1
            continue

        # ---- merged verse run: poem div(s) interleaved with page-break markers ----
        if is_poem_carrier(el):
            v = etree.SubElement(out_root, "v")
            collected_notes = []
            j = i
            while j < len(children) and (is_poem_carrier(children[j]) or is_bare_pagenum_span(children[j])):
                cur = children[j]
                if is_bare_pagenum_span(cur):
                    marker = cur.find('.//span[@class="pagenum ws-pagenum"]')
                    pb = etree.SubElement(v, "pb")
                    pb.set("num", marker.get("id"))
                else:
                    cur_cls = cur.get("class", "") or ""
                    poem_divs = [cur] if "poem" in cur_cls else cur.findall('.//div[@class="poem"]')
                    for pd in poem_divs:
                        for p_el in pd.findall("./p"):
                            if not "".join(p_el.itertext()).strip():
                                continue
                            append_verse_p(p_el, v, notes_by_id, collected_notes)
                j += 1
            if v.text:
                v.text = v.text.strip("\n")
            i = j
            for key in collected_notes:
                note_src = notes_by_id.get(key)
                if note_src is None:
                    continue
                note = etree.Element("note")
                note.set("id", key)
                p = etree.SubElement(note, "p")
                build_inline(note_src, notes_by_id, p)
                out_root.append(note)
            last_heading_tag = None
            continue

        # top-level page markers that aren't wrapped in a heading/paragraph div
        if tag == "span":
            marker = el.find('.//span[@class="pagenum ws-pagenum"]')
            if marker is not None:
                emit_pb(marker)
            i += 1
            continue

        if tag == "div" and is_centered_bold_heading(el):
            fs = get_font_size_pct(el.get("style", "")) or 0
            level_tag = "t" if fs >= T_FONT_SIZE_MIN else ("st" if fs >= ST_FONT_SIZE_MIN else None)
            if level_tag is None:
                i += 1
                continue
            if level_tag == last_heading_tag and len(out_root) > 0:
                node = out_root[-1]
                if (node.text or "").strip():
                    node.text = (node.text or "").rstrip() + " "
            else:
                node = etree.SubElement(out_root, level_tag)
            build_inline(el, notes_by_id, node)
            last_heading_tag = level_tag
            i += 1
            continue

        if tag == "p":
            txt_probe = "".join(el.itertext()).strip()
            if not txt_probe:
                i += 1
                continue
            p = etree.SubElement(out_root, "p")
            build_inline(el, notes_by_id, p)
            _inline_notes_after(p, el, notes_by_id, out_root)
            last_heading_tag = None
            i += 1
            continue

        marker = el.find('.//span[@class="pagenum ws-pagenum"]') if tag == "div" else None
        if marker is not None and not "".join(el.itertext()).strip():
            emit_pb(marker)
            i += 1
            continue

        # skip decorative rules, wst-custom-rule, empty layout divs, TemplateStyles etc.
        i += 1


def _inline_notes_after(dest_el, src_el, notes_by_id, out_root):
    """Find any <ref target="#nN"> just added inside dest_el and append a <note id="nN"> right after
    the containing block, pulling text from the references list."""
    for ref in dest_el.iter("ref"):
        target = ref.get("target", "")
        key = target.lstrip("#")
        note_src = notes_by_id.get(key)
        if note_src is None:
            continue
        note = etree.Element("note")
        note.set("id", key)
        p = etree.SubElement(note, "p")
        build_inline(note_src, notes_by_id, p)
        out_root.append(note)


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    raw = open(sys.argv[1], encoding="utf-8").read()
    data = json.loads(raw)
    html_str = data["parse"]["text"]["*"]
    tree = lhtml.fromstring(html_str)

    root = etree.Element("document")
    process(tree, root)

    out = etree.ElementTree(root)
    out.write(sys.argv[2], encoding="UTF-8", xml_declaration=True, pretty_print=True)
    print(f"Wrote {sys.argv[2]}")


if __name__ == "__main__":
    main()
