"""Descriptive citation metrics. Missing observations must be removed upstream, never zero-filled."""
import math
from collections import Counter
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def normalize_url(value):
    parsed = urlsplit(value)
    if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Citation must be a public HTTP(S) URL without credentials")
    host = parsed.hostname.lower().encode("idna").decode("ascii").removeprefix("www.")
    port = parsed.port
    authority = host + (f":{port}" if port and port != (443 if parsed.scheme == "https" else 80) else "")
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if not k.lower().startswith("utm_")]
    return urlunsplit((parsed.scheme, authority, parsed.path or "/", urlencode(query), ""))


def hosts(citations):
    return [urlsplit(url).hostname for url in {normalize_url(c["url"]) for c in citations}]


def domain_present(citations, target):
    return any(h == target or h.endswith("." + target) for h in hosts(citations))


def source_distribution(answers):
    """Each answer has mass one, divided between its unique source URLs, including no-citation mass."""
    if not answers:
        raise ValueError("No observations; cannot estimate a distribution")
    total = Counter()
    for answer in answers:
        values = hosts(answer["citations"]) or ["__no_citation__"]
        for host in values:
            total[host] += 1 / len(values) / len(answers)
    return dict(total)


def js_divergence(left, right):
    for value in (left, right):
        if any(not math.isfinite(p) or p < 0 for p in value.values()) or not math.isclose(sum(value.values()), 1):
            raise ValueError("JS requires normalized nonnegative probability distributions")
    score = 0.0
    for key in left.keys() | right.keys():
        a, b = left.get(key, 0), right.get(key, 0)
        middle = (a + b) / 2
        for p in (a, b):
            if p:
                score += 0.5 * p * math.log2(p / middle)
    return max(0.0, min(1.0, score))


def wilson_interval(successes, n, z=1.959963984540054):
    if n <= 0 or successes < 0 or successes > n:
        raise ValueError("Need observed Bernoulli counts with 0 <= successes <= n")
    p = successes / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return [max(0, center - half), min(1, center + half)]
