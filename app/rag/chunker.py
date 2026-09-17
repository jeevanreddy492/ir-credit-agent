def chunk_text(text: str, max_chars: int = 900, overlap: int = 120) -> list[str]:
    """Heading-aware: splits on '### ' / '## ' first so each error code stays in one chunk, then packs paragraphs."""
    sections, cur = [], ""
    for line in text.splitlines():
        if line.startswith(("## ", "### ")) and cur.strip():
            sections.append(cur.strip()); cur = ""
        cur += line + "\n"
    if cur.strip():
        sections.append(cur.strip())

    chunks: list[str] = []
    for s in sections:
        if len(s) <= max_chars:
            chunks.append(s); continue
        buf = ""
        for p in s.split("\n\n"):
            if len(buf) + len(p) + 2 <= max_chars:
                buf = f"{buf}\n\n{p}" if buf else p
            else:
                if buf: chunks.append(buf)
                buf = (buf[-overlap:] + "\n\n" + p) if buf else p
        if buf: chunks.append(buf)
    return chunks
