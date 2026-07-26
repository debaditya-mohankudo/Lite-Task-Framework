"""Shared TOON (Token-Oriented Object Notation) encoder for MCP tool responses.

TOON compresses arrays of uniform-schema dicts by declaring the field header
once instead of repeating keys per row, the way JSON does. Only worth it for
exactly that shape — a list of dicts sharing the same keys, returned to an
LLM caller rather than a human reading raw output. Extracted from
src/tools/hooks.py (task:d2165715) so src/tools/tasks.py (task:4b5bf21f) can
reuse it without duplicating the escaping logic.
"""


def toon_cell(value: object) -> str:
    s = "" if value is None else str(value)
    if any(c in s for c in (",", "\n", '"')):
        return '"' + s.replace('"', '""') + '"'
    return s


def rows_to_toon(rows: list[dict]) -> str:
    """Encode a list of uniform-schema dicts as TOON's tabular array format.

    header[N]{field1,field2,...}:
      val1,val2,...

    Callers must normalize rows to a consistent field set first — this
    encoder uses the first row's keys as the header and does not fill in
    missing keys for later rows.
    """
    if not rows:
        return "rows[0]{}:"
    fields = list(rows[0].keys())
    lines = [f"rows[{len(rows)}]{{{','.join(fields)}}}:"]
    for r in rows:
        lines.append("  " + ",".join(toon_cell(r.get(f)) for f in fields))
    return "\n".join(lines)
