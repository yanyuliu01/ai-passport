"""recall：小鱼翻以前的记录——跟主人聊过的话、记忆日志、最近的事和帮手交回的结果。

注入的记忆只有几十条；“上次说的那个”“前几天查的结果”要回去翻原文。这里用 Python 自带的
SQLite 全文检索（FTS5，trigram 分词：按三个字切，中文不用另装分词器）。SQLite 太旧没有
trigram、或者要找的词不到三个字时，退回逐条找关键词。数据只在本地，查完就丢（每次现建，
几千条也只要几十毫秒）。

以后要加向量检索（embedding + rerank）时，换掉 search() 的实现就行，工具接口不变。
"""

import re
import sqlite3
import time
from typing import Iterable, List, Tuple

MAX_HITS = 6
SNIPPET_CHARS = 160

Record = Tuple[str, str, float, str]  # （来源，标题/日期，时间戳，正文）


def _terms(query: str) -> List[str]:
    return [term for term in re.split(r"[\s，。,.!?！？、；;:：\"'“”‘’()（）]+", query or "") if term]


def _fts(records: List[Record], terms: List[str]) -> List[int]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("create virtual table hay using fts5(body, stamp UNINDEXED, tokenize='trigram')")
        connection.executemany("insert into hay(rowid, body, stamp) values (?, ?, ?)",
                               [(index, record[3], record[2]) for index, record in enumerate(records)])
        query = " OR ".join('"%s"' % term.replace('"', '""') for term in terms)
        rows = connection.execute("select rowid from hay where hay match ? order by rank, cast(stamp as real) desc, rowid limit ?",
                                  (query, MAX_HITS * 3)).fetchall()
        return [row[0] for row in rows]
    finally:
        connection.close()


def _plain(records: List[Record], terms: List[str]) -> List[int]:
    scored = []
    for index, record in enumerate(records):
        body = record[3].lower()
        score = sum(body.count(term.lower()) for term in terms)
        if score:
            scored.append((score, record[2], index))
    scored.sort(key=lambda item: (-item[0], -item[1]))
    return [index for _, _, index in scored[:MAX_HITS * 3]]


def _partial(records: List[Record], terms: List[str]) -> List[int]:
    """Find bounded literal phrase candidates when the full query misses."""
    glue = ("之前", "上次", "那次", "刚刚", "后来", "我们", "你说", "我说", "说过",
            "讨论", "什么", "怎么", "多少", "哪个", "哪些", "是不是", "有没有",
            "告诉我", "帮我", "查一下", "找一下", "记录", "对话", "聊天", "的", "了", "吗", "呢")
    chunks = []
    for term in terms:
        chunks.extend(part for part in re.split("|".join(map(re.escape, glue)), term[:80].lower())
                      if len(part) >= 2 and re.search(r"[\u4e00-\u9fff]", part))
    fragments = {}
    for chunk in chunks[:8]:
        for length in range(2, min(len(chunk), 12) + 1):
            fragments.setdefault(length, set()).update(
                chunk[pos:pos + length] for pos in range(len(chunk) - length + 1))
    choices = [(length, fragments[length]) for length in sorted(fragments, reverse=True)]
    scored = []
    for index, record in enumerate(records):
        body = record[3].lower()
        for length, pieces in choices:
            if any(piece in body for piece in pieces):
                scored.append((length, record[2], index))
                break
    scored.sort(key=lambda item: (-item[0], -item[1]))
    return [index for _, _, index in scored[:MAX_HITS]]


def search(query: str, records: Iterable[Record], *, limit: int = MAX_HITS) -> List[Record]:
    """找最相关的几条；新的优先。找不到返回空表。"""
    if type(limit) is not int or not 1 <= limit <= MAX_HITS:
        raise ValueError("Recall hit limit must be between 1 and MAX_HITS")
    records = [record for record in records if record[3].strip()]
    terms = _terms(query)
    if not terms or not records:
        return []
    hits: List[int] = []
    if all(len(term) >= 3 for term in terms):
        try:
            hits = _fts(records, terms)
        except sqlite3.Error:
            hits = []  # 没有 FTS5 或 trigram：下面逐条找
    if not hits:
        hits = _plain(records, terms)
    if not hits:
        hits = _partial(records, terms)
    # Pick the relevant window before ordering those hits for display. Sorting
    # a wider candidate set by age first can discard the strongest old evidence.
    chosen = [records[index] for index in hits[:limit]]
    chosen.sort(key=lambda record: -record[2])
    return chosen


def snippet(body: str, query: str, *, max_chars: int = SNIPPET_CHARS) -> str:
    """命中处前后的一段，不整段给。"""
    if type(max_chars) is not int or max_chars < 1:
        raise ValueError("Recall excerpt size must be a positive integer")
    body = " ".join(body.split())
    terms = _terms(query)
    matches = [re.search(re.escape(term), body, re.IGNORECASE) for term in terms]
    offsets = [match.start() for match in matches if match is not None]
    if not offsets:
        lower = body.lower()
        for term in terms:
            for length in range(min(len(term), 12), 1, -1):
                offsets = [lower.find(term[pos:pos + length].lower())
                           for pos in range(len(term) - length + 1)
                           if lower.find(term[pos:pos + length].lower()) >= 0]
                if offsets:
                    break
            if offsets:
                break
    at = min(offsets or [0])
    start = max(0, at - max_chars // 3)
    piece = body[start:start + max_chars]
    return ("…" if start else "") + piece + ("…" if start + max_chars < len(body) else "")


def render(query: str, hits: List[Record]) -> str:
    if not hits:
        return "没找到跟「%s」有关的记录（限本次提供的范围，不代表其他范围没有）。换关键词再找，或如实说明。" % query
    lines = ["找到 %d 条候选（新的在前，请核对原文；直接回答问题的记录可直接转述，不猜测说话动机）：" % len(hits)]
    for source, label, at, body in hits:
        when = time.strftime("%m-%d %H:%M", time.localtime(at)) if at else ""
        lines.append("- [%s%s%s] %s" % (source, " " + label if label else "", " " + when if when else "",
                                        snippet(body, query)))
    return "\n".join(lines)
