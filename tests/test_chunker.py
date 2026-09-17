from pathlib import Path

from app.rag.chunker import chunk_text

GUIDE = Path(__file__).resolve().parent.parent / "data/knowledge/ir_troubleshooting_guide.md"


def test_each_error_code_section_is_one_chunk():
    chunks = chunk_text(GUIDE.read_text())
    for code in ("TU-IDM-402", "EQ-TMO-504", "EQ-FRZ-301"):
        holders = [ch for ch in chunks if ch.startswith("### ") and code in ch.splitlines()[0]]
        assert len(holders) == 1, code
        assert "Procedure" in holders[0]
