"""Extract one video link from Xiaoyu's selected share for channel delivery."""
import re
from urllib.parse import urlsplit

from .network import BV

URL = re.compile(r'https?://[^\s<>()\[\]{}"\'“”]+', re.I)
SHORT = re.compile(r'/[A-Za-z0-9]{1,20}\Z')


def _video_url(value):
    value = value.rstrip('，。！？、；：,.!?;:')
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or '').lower()
        if parsed.scheme.lower() != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443):
            return None
    except ValueError:
        return None
    if host in ('www.bilibili.com', 'm.bilibili.com', 'bilibili.com'):
        segments = parsed.path.strip('/').split('/')
        if len(segments) != 2 or segments[0] != 'video' or not BV.fullmatch(segments[1]):
            return None
        return 'https://www.bilibili.com/video/' + segments[1]
    elif host == 'b23.tv' and SHORT.fullmatch(parsed.path):
        return 'https://b23.tv' + parsed.path
    return None


def split_video_share(text):
    """Return (recommendation, canonical link), or None for ambiguous output."""
    if not isinstance(text, str):
        return None
    urls = list(URL.finditer(text))
    if len(urls) != 1:
        return None
    match = urls[0]
    link = _video_url(match.group())
    if not link:
        return None
    recommendation = text[:match.start()] + text[match.end():]
    recommendation = re.sub(r'\[([^\]]+)\]\(\s*\)', r'\1', recommendation)
    recommendation = re.sub(r'(?:[\s—–\-,，]*)?(?:想看的话(?:在这里|在这儿|点这里)|链接(?:在这里|在这儿|如下)?)\s*[：:]?\s*$', '', recommendation)
    recommendation = recommendation.strip(' \t\r\n—–-，,：:')
    return (recommendation, link) if recommendation else None


def separate_video_links(text, separator):
    """Place Bilibili links in their own bubbles in a manual browser reply."""
    if not isinstance(text, str) or not isinstance(separator, str):
        return text
    matches = list(URL.finditer(text))
    if len(matches) == 1:
        pair = split_video_share(text)
        if pair is not None:
            return separator.join(pair)
    links = [_video_url(match.group()) for match in matches]
    if not matches or not all(links):
        return text
    result, pos = [], 0
    for match, link in zip(matches, links):
        before = text[pos:match.start()]
        markdown = re.search(r'\[([^\]]+)\]\($', before)
        if markdown:
            before = before[:markdown.start()] + markdown.group(1)
        before = before.strip(' \t\r\n—–-，,：:')
        if before:
            result.append(before)
        result.append(link)
        pos = match.end() + (1 if markdown and text[match.end():].startswith(')') else 0)
    tail = text[pos:].strip(' \t\r\n—–-，,：:')
    if tail:
        result.append(tail)
    return separator.join(result) if len(result) > 1 else text
