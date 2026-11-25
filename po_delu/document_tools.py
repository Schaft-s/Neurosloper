import json
import os
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from policy import allowed_ranges, check
from consensus import locate

server = FastMCP("po-delu")
job = json.loads(Path(os.environ["EDITOR_INPUT"]).read_text())
blocks = {b["id"]: b for b in job["blocks"]}


@server.tool()
def read_blocks(start: int = 0, count: int = 8) -> dict:
    """Read consecutive original blocks with their location and editing permissions, max eight."""
    items = list(blocks.values())[max(0, start) : max(0, start) + max(1, min(count, 8))]
    return {
        "total": len(blocks),
        "blocks": [
            {k: b[k] for k in ("id", "label", "text", "edit", "page", "shape", "prefix") if k in b}
            for b in items
            if b.get("editorial", True)
        ],
        "visited": [b["id"] for b in items],
    }


@server.tool()
def locate_finding(
    block_id: str,
    quote: str,
    pattern: str,
    effect: str,
    normal_explanation: str,
    decision: str = "flag",
    occurrence: int | None = None,
    start: int | None = None,
    related_ids: list[str] | None = None,
) -> dict:
    """Anchor an exact quote. For repeated quotes supply the occurrence (1-based) or Unicode start offset."""
    block = blocks.get(block_id)
    if (
        not block
        or not block.get("editorial", True)
        or not quote.strip()
        or not all(s.strip() for s in [pattern, effect, normal_explanation])
    ):
        return {"accepted": False, "error": "Нужны цитата и основание"}
    if decision not in ("flag", "uncertain"):
        return {"accepted": False, "error": "Неизвестный вердикт"}
    try:
        if start is None:
            start = locate(block["text"], quote, occurrence)
        elif not 0 <= start <= len(block["text"]) or block["text"][start : start + len(quote)] != quote:
            raise ValueError("wrong_offset")
        elif occurrence is not None and locate(block["text"], quote, occurrence) != start:
            raise ValueError("wrong_occurrence")
    except ValueError as exc:
        return {"accepted": False, "error": str(exc)}
    return dict(
        accepted=True,
        block_id=block_id,
        quote=quote,
        start=start,
        end=start + len(quote),
        pattern=pattern,
        effect=effect,
        normal=normal_explanation,
        related_ids=[
            i
            for i in dict.fromkeys(related_ids or [])
            if i in blocks and blocks[i].get("editorial", True)
        ],
        decision=decision,
    )


@server.tool()
def check_patch(block_id: str, before: str, after: str, reason: str) -> dict:
    """Check exact original, author-selected intervals, literals, length and fitting for a replacement."""
    block = blocks.get(block_id)
    error = (
        "Исходник не совпадает"
        if not block or block["text"] != before
        else check(
            block,
            after,
            job["length"],
            job["terms"],
            reviewed_phrasing=job.get("fidelity_consensus", False),
        )
    )
    if not error and not allowed_ranges(before, after, job.get("selected", {}).get(block_id, [])):
        error = "Замена затронула текст вне выбранной цитаты"
    source = Path(os.environ["EDITOR_INPUT"]).with_name("source.pptx")
    if not error and source.exists():
        from formats import export

        try:
            export("source.pptx", source.read_bytes(), job["blocks"], {block_id: after}, strict=True)
        except Exception:
            error = "Рендер не подтвердил размещение текста в исходной фигуре"
    if not reason.strip():
        error = "Нужно объяснение"
    return {"accepted": not error, "reason": error or reason, "block_id": block_id}


if __name__ == "__main__":
    server.run(transport="stdio")
