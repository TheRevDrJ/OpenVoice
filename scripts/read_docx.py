"""Extract plain text from a .docx (stdlib only — no python-docx needed).
Prints word count, an audio-length estimate, and a head/tail preview so we can
sanity-check what would actually be voiced before rendering anything long.

Usage:  python scripts/read_docx.py <path.docx>
"""
import sys
import zipfile
from xml.etree import ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def extract(path: str) -> list[str]:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml")
    root = ET.fromstring(xml)
    paras = []
    for p in root.iter(f"{W}p"):
        text = "".join(t.text or "" for t in p.iter(f"{W}t"))
        if text.strip():
            paras.append(text.strip())
    return paras


def main() -> None:
    path = sys.argv[1]
    paras = extract(path)
    full = "\n".join(paras)
    words = len(full.split())
    # ~150 wpm narration → minutes
    mins = words / 150.0
    print(f"PARAGRAPHS {len(paras)}")
    print(f"WORDS {words}")
    print(f"EST_MINUTES {mins:.1f}")
    print("----- FIRST 5 PARAGRAPHS -----")
    for p in paras[:5]:
        print(p)
        print()
    print("----- LAST 2 PARAGRAPHS -----")
    for p in paras[-2:]:
        print(p)
        print()


if __name__ == "__main__":
    main()
