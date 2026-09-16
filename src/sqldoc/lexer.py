"""Lexical helpers: comment stripping and depth-aware splitting.

Everything here is deliberately dependency-free. The goal is not a complete SQL
grammar - it is to chop a SELECT statement into its clauses without being fooled
by string literals, comments or nested parentheses.
"""

from __future__ import annotations

_QUOTES = {"'": "'", '"': '"', "`": "`", "[": "]"}


def strip_comments(sql: str) -> str:
    """Remove -- line comments and /* block comments */, preserving literals."""
    out = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]

        # string / identifier literal - copy verbatim
        if ch in _QUOTES:
            close = _QUOTES[ch]
            out.append(ch)
            i += 1
            while i < n:
                if sql[i] == close:
                    # doubled quote is an escape ('' or "")
                    if i + 1 < n and sql[i + 1] == close and close != "]":
                        out.append(sql[i:i + 2])
                        i += 2
                        continue
                    out.append(sql[i])
                    i += 1
                    break
                out.append(sql[i])
                i += 1
            continue

        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            while i < n and sql[i] != "\n":
                i += 1
            continue

        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            i += 2
            while i + 1 < n and not (sql[i] == "*" and sql[i + 1] == "/"):
                i += 1
            i += 2
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def scan_depth(sql: str):
    """Yield (index, char, depth, in_literal) for each character.

    ``depth`` counts round brackets only; square brackets are treated as quoting
    because T-SQL uses them for identifiers.
    """
    depth = 0
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]

        if ch in _QUOTES:
            close = _QUOTES[ch]
            start = i
            i += 1
            while i < n:
                if sql[i] == close:
                    if i + 1 < n and sql[i + 1] == close and close != "]":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            for j in range(start, min(i, n)):
                yield j, sql[j], depth, True
            continue

        if ch == "(":
            depth += 1
            yield i, ch, depth, False
        elif ch == ")":
            yield i, ch, depth, False
            depth -= 1
        else:
            yield i, ch, depth, False
        i += 1


def split_top_level(sql: str, separator: str = ",") -> list[str]:
    """Split on a separator that appears at bracket depth 0, outside literals."""
    parts = []
    buf = []
    sep = separator.lower()
    seplen = len(sep)
    lowered = sql.lower()

    skip_until = -1
    # A separator that already carries whitespace (e.g. " and ") enforces its own
    # boundary; only bare word separators need an alphanumeric-boundary check.
    check_before = bool(sep) and not sep[0].isspace()
    check_after = bool(sep) and not sep[-1].isspace()

    for i, ch, depth, in_lit in scan_depth(sql):
        if i < skip_until:
            buf.append(ch)
            continue
        if depth == 0 and not in_lit and lowered.startswith(sep, i):
            if seplen > 1:
                before_ok = True
                if check_before:
                    before_ok = i == 0 or not (
                        lowered[i - 1].isalnum() or lowered[i - 1] == "_"
                    )
                after_ok = True
                if check_after:
                    after = i + seplen
                    after_ok = after >= len(lowered) or not (
                        lowered[after].isalnum() or lowered[after] == "_"
                    )
                if not (before_ok and after_ok):
                    buf.append(ch)
                    continue
            parts.append("".join(buf))
            buf = []
            skip_until = i + seplen
            continue
        buf.append(ch)

    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def find_top_level(sql: str, keyword: str) -> int:
    """Index of the first depth-0 occurrence of a bare keyword, else -1."""
    lowered = sql.lower()
    kw = keyword.lower()
    klen = len(kw)
    for i, _ch, depth, in_lit in scan_depth(sql):
        if depth != 0 or in_lit:
            continue
        if not lowered.startswith(kw, i):
            continue
        before_ok = i == 0 or not (lowered[i - 1].isalnum() or lowered[i - 1] == "_")
        after = i + klen
        after_ok = after >= len(lowered) or not (lowered[after].isalnum() or lowered[after] == "_")
        if before_ok and after_ok:
            return i
    return -1


def split_statements(sql: str) -> list[str]:
    """Split a script into statements on depth-0 semicolons."""
    return [s for s in split_top_level(sql, ";") if s.strip()]


def normalise_ws(text: str) -> str:
    return " ".join(text.split())
