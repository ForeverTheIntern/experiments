"""Topic groups and the stratified draw order that makes universes nested.

The pool is put into one global order such that every prefix is close to the
target stratum shares. A universe of size N takes the first eligible entries,
so U50 is contained in U250, which is contained in U1000 and U5000.
"""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from collections.abc import Hashable, Iterable, Sequence
from typing import TypeVar

T = TypeVar("T")

TOPIC_GROUPS: dict[str, frozenset[str]] = {
    "ml-ai": frozenset(
        [
            "machine-learning",
            "deep-learning",
            "ai",
            "artificial-intelligence",
            "llm",
            "llms",
            "nlp",
            "pytorch",
            "tensorflow",
            "computer-vision",
            "neural-network",
            "neural-networks",
            "transformers",
            "generative-ai",
            "openai",
            "chatgpt",
            "langchain",
            "rag",
            "reinforcement-learning",
            "data-science",
            "keras",
            "huggingface",
        ]
    ),
    "infra-devops": frozenset(
        [
            "kubernetes",
            "k8s",
            "docker",
            "devops",
            "cloud",
            "aws",
            "azure",
            "gcp",
            "terraform",
            "ansible",
            "helm",
            "ci",
            "cd",
            "ci-cd",
            "monitoring",
            "observability",
            "infrastructure",
            "prometheus",
            "grafana",
            "serverless",
            "cloud-native",
            "containers",
            "operator",
            "infrastructure-as-code",
            "sre",
            "logging",
            "tracing",
        ]
    ),
    "api-backend": frozenset(
        [
            "api",
            "rest-api",
            "rest",
            "graphql",
            "grpc",
            "microservices",
            "microservice",
            "backend",
            "server",
            "http",
            "web-server",
            "websocket",
            "openapi",
            "swagger",
            "protobuf",
            "rpc",
            "middleware",
        ]
    ),
    "web-frontend": frozenset(
        [
            "web",
            "webapp",
            "frontend",
            "react",
            "reactjs",
            "vue",
            "vuejs",
            "angular",
            "nextjs",
            "svelte",
            "javascript-framework",
            "website",
            "css",
            "html",
            "ui",
            "ui-components",
            "tailwindcss",
            "nodejs",
            "express",
            "django",
            "flask",
            "rails",
            "laravel",
            "symfony",
            "wordpress",
            "cms",
        ]
    ),
    "data-storage": frozenset(
        [
            "database",
            "sql",
            "nosql",
            "postgres",
            "postgresql",
            "mysql",
            "sqlite",
            "mongodb",
            "redis",
            "elasticsearch",
            "kafka",
            "etl",
            "big-data",
            "analytics",
            "data-engineering",
            "orm",
            "cache",
            "search-engine",
            "time-series",
            "spark",
        ]
    ),
    "devtools": frozenset(
        [
            "cli",
            "command-line",
            "command-line-tool",
            "tool",
            "tools",
            "developer-tools",
            "devtools",
            "library",
            "sdk",
            "framework",
            "testing",
            "test",
            "linter",
            "formatter",
            "parser",
            "compiler",
            "plugin",
            "vscode",
            "vscode-extension",
            "neovim",
            "vim",
            "emacs",
            "build-tool",
            "package-manager",
            "code-generation",
            "static-analysis",
        ]
    ),
    "security": frozenset(
        [
            "security",
            "pentest",
            "pentesting",
            "penetration-testing",
            "ctf",
            "hacking",
            "cryptography",
            "encryption",
            "privacy",
            "vulnerability",
            "malware",
            "authentication",
            "oauth",
        ]
    ),
    "apps-other": frozenset(
        [
            "game",
            "gamedev",
            "game-engine",
            "android",
            "ios",
            "mobile",
            "desktop",
            "electron",
            "flutter",
            "bot",
            "discord",
            "discord-bot",
            "telegram",
            "blockchain",
            "ethereum",
            "crypto",
            "iot",
            "embedded",
            "arduino",
            "raspberry-pi",
        ]
    ),
}
UNTAGGED = "untagged"
OTHER_TOPIC = "other"
TOPIC_GROUP_NAMES = (*TOPIC_GROUPS, OTHER_TOPIC, UNTAGGED)


def topic_group(topics: Iterable[str]) -> str:
    topics = [t.lower() for t in topics]
    if not topics:
        return UNTAGGED
    hits = Counter()
    for name, words in TOPIC_GROUPS.items():
        hits[name] = sum(t in words for t in topics)
    best = max(TOPIC_GROUPS, key=lambda name: hits[name])
    return best if hits[best] else OTHER_TOPIC


def interleave(
    groups: dict[Hashable, Sequence[T]], weights: dict[Hashable, float], rng: random.Random
) -> list[T]:
    """Merge ordered groups so any prefix holds about ``n * weight`` items from each group.

    Item ``j`` of group ``g`` gets key ``(j + u_g) / w_g`` with a random offset
    ``u_g`` in [0, 1); sorting by key spreads each group evenly. Groups with
    zero weight go last, in key order.
    """
    keyed = []
    for name in sorted(groups, key=str):
        items = groups[name]
        weight = weights.get(name, 0.0)
        offset = rng.random()
        for j, item in enumerate(items):
            key = (j + offset) / weight if weight > 0 else float("inf")
            keyed.append((weight <= 0, key, str(name), j, item))
    keyed.sort(key=lambda row: row[:4])
    return [row[4] for row in keyed]


def order_cell(items: list[dict], rng: random.Random) -> list[dict]:
    """Random order within a cell, interleaved by topic group in proportion to the cell's mix."""
    by_topic: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        by_topic[item["topic_group"]].append(item)
    for name in sorted(by_topic):
        rng.shuffle(by_topic[name])
    total = len(items)
    weights = {name: len(group) / total for name, group in by_topic.items()}
    return interleave(by_topic, weights, rng)


def order_pool(
    cells: dict[tuple[str, str], list[dict]],
    shares: dict[tuple[str, str], float],
    rng: random.Random,
) -> list[dict]:
    ordered_cells = {key: order_cell(items, rng) for key, items in sorted(cells.items())}
    ordered = interleave(ordered_cells, shares, rng)
    for rank, item in enumerate(ordered):
        item["rank"] = rank
    return ordered
