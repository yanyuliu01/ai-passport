"""Stable message segmentation shared by presentation and channel delivery."""
import re


def parts(text, split):
    """Legacy Runtime message markers, never separators inside fenced code.

    Native messages and owner input preserve their upstream item boundaries.
    Newlines within an item do not create another message.
    """
    if not split:
        return [(text, False)] if text else []
    parts, lines, fence = [], [], None
    for line in text.splitlines(keepends=True):
        mark = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line.rstrip('\r\n'))
        if mark:
            token, rest = mark.groups()
            if fence is None:
                fence = (token[0], len(token))
            elif token[0] == fence[0] and len(token) >= fence[1] and not rest.strip():
                fence = None
        if fence is None and re.fullmatch(r'[ \t]*-{3,}[ \t]*(?:\r?\n)?', line):
            value = ''.join(lines).strip()
            if value:
                parts.append((value, True))
            lines = []
        else:
            lines.append(line)
    value = ''.join(lines).strip()
    if value:
        parts.append((value, False))
    return parts
