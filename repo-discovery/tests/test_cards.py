from __future__ import annotations

from pathlib import Path

import pytest

from repo_discovery.cards import (
    DEFAULT_BUDGET,
    SECTIONS,
    SHARES,
    allocate,
    build_card,
    cards_for,
    contract_names,
    headings,
    prose_paragraphs,
    word_count,
)
from repo_discovery.contract import Universe
from repo_discovery.evidence import Evidence, Manifest, TextFile, read_evidence

EVIDENCE_UNIVERSE = Path(__file__).parent / "fixtures" / "evidence_universe"


def fixture_evidence(repo: str) -> Evidence:
    view = Universe.load(EVIDENCE_UNIVERSE).view()
    candidate = next(c for c in view.candidates if c.repo == repo)
    return read_evidence(candidate.repo, candidate.evidence_dir)


def test_fixture_cards_are_deterministic_and_within_budget() -> None:
    view = Universe.load(EVIDENCE_UNIVERSE).view()
    first = list(cards_for(view))
    assert [c.repo for c in first] == [c.repo for c in view.candidates]
    assert [c.text for c in cards_for(view)] == [c.text for c in first]
    for card in first:
        assert 0 < card.words <= DEFAULT_BUDGET
        assert card.text.splitlines()[0].startswith(card.repo)
    assert all(c.words <= 30 for c in cards_for(view, budget=30))


def test_billing_card_sections() -> None:
    card = build_card(fixture_evidence("acme/billing"))
    s = card.sections
    assert list(s) == list(SECTIONS)
    assert s["title"] == "acme/billing (packages: @acme/billing)"
    assert s["about"].startswith("About: Invoices, tax and refunds for Acme subscriptions.")
    assert "Keywords: billing, invoices, tax." in s["about"]
    assert s["overview"].startswith("Overview: Acme Billing turns subscriptions into monthly")
    for noise in ("http", "shields", "<img", "npm install", "**", "MIT"):
        assert noise not in s["overview"]
    assert s["topics"] == "Topics: Tax rounding; Rounding modes"
    assert s["api"] == "API: listInvoices, createRefund, /invoices, /invoices/{id}/refunds"
    assert s["dependencies"] == "Depends on: decimal.js, express, jest"
    assert s["layout"].startswith("Layout: directories src, test, api, docs.")
    assert "github" not in s["layout"]
    assert card.to_dict()["words"] == word_count(card.text)


def test_card_uses_docs_titles_and_skips_toc_lists() -> None:
    portal = build_card(fixture_evidence("acme/portal")).sections
    assert portal["topics"] == "Topics: Subscription schedule"
    assert "renewal dates" in portal["overview"]
    cli = build_card(fixture_evidence("globex/cli")).sections
    assert "Install" not in cli["overview"]
    assert cli["overview"].startswith("Overview: The globex command line tool.")


def test_budget_is_filled_when_evidence_is_plentiful() -> None:
    lots = " ".join(f"w{i}" for i in range(2000))
    rich = Evidence(
        repo="a/b",
        readme=lots,
        docs=(TextFile("docs/x.md", "# Topic\n\n" + lots),),
        manifests=(
            Manifest("package.json", "", ("pkg",), lots, ("k",), tuple(f"d{i}" for i in range(99))),
        ),
        paths=tuple(f"dir{i}/f.py" for i in range(50)),
        contracts=(TextFile("a.proto", "\n".join(f"message M{i} {{}}" for i in range(99))),),
    )
    for budget in (32, 64, DEFAULT_BUDGET, 500):
        card = build_card(rich, budget)
        assert card.words == budget
        assert set(card.sections) == set(SECTIONS)


def test_unused_shares_flow_to_other_sections() -> None:
    lots = " ".join(f"w{i}" for i in range(2000))
    card = build_card(Evidence(repo="a/b", readme=lots))
    assert list(card.sections) == ["title", "overview"]
    assert card.words == DEFAULT_BUDGET
    assert build_card(Evidence(repo="a/b")).text == "a/b"


def test_allocate() -> None:
    long = {s: 1000 for s in SECTIONS}
    assert allocate(long, sum(SHARES.values())) == SHARES
    given = allocate({**long, "about": 0, "api": 0}, 256)
    assert given["about"] == given["api"] == 0
    assert sum(given.values()) == 256
    assert given["title"] == 16 + 40 + 25
    assert allocate({"title": 3, "dependencies": 2}, 256)["dependencies"] == 0


def test_prose_paragraphs() -> None:
    text = """
<!-- hidden -->
<h1 align="center">Name</h1>
[![CI](https://ci/badge.svg)](https://ci)

# Heading

First **real** paragraph with a [link](https://x.y) and `code`.

```python
print("no code")
```

| a | b |
|---|---|

- [Install](#install)
- [Usage](#usage)

- Feature lists survive when their items are long enough to read.
- A second item.

Setext
------

    indented code block

.. note:: rst directive

> Quoted text stays in.
"""
    assert list(prose_paragraphs(text)) == [
        "First real paragraph with a link and code.",
        "Feature lists survive when their items are long enough to read. A second item.",
        "Quoted text stays in.",
    ]


def test_headings() -> None:
    text = "# One\n\nTwo\n===\n\n## Three ##\n\nFour\n----\n\n```\n# not a heading\n```\n"
    html = '<h2 class="x">Five</h2>\n' + text
    assert list(headings(html)) == ["Five", "One", "Two", "Three", "Four"]


@pytest.mark.parametrize(
    ("path", "text", "names"),
    [
        (
            "a.proto",
            "service Ledger {\n  rpc Post(R) returns (E);\n}\nmessage Entry {}\nenum Kind {}\n",
            ("Ledger", "Post", "Entry", "Kind"),
        ),
        (
            "s.graphql",
            "type Query {\n  user: User\n}\ninput NewUser { name: String }\n",
            ("Query", "NewUser"),
        ),
        (
            "openapi.yaml",
            "paths:\n  /users:\n    get:\n      operationId: listUsers\n",
            ("listUsers", "/users"),
        ),
        ("openapi.json", '{"paths": {"/u": {"get": {"operationId": "getU"}}}}', ("getU",)),
        ("user.schema.json", '{"$id": "user", "title": "User"}', ("user", "User")),
        (
            "spec.yaml",
            "openapi: 3.1.0\ninfo:\n  title: T\npaths:\n  # users\n  '/users/{id}':\n"
            "    get: {}\n  /teams:  # comment\n    get: {}\ncomponents:\n  schemas: {}\n",
            ("/users/{id}", "/teams"),
        ),
        (
            "events.yaml",
            "asyncapi: 3.0.0\nchannels:\n  invoicePaid:\n    address: invoice/paid\n"
            "operations:\n  onInvoicePaid:\n    action: receive\n",
            ("invoicePaid", "onInvoicePaid"),
        ),
        (
            "deploy.yaml",
            "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  labels:\n    name: wrong\n"
            "  name: billing-api\nspec:\n  template:\n    metadata:\n      name: pod\n---\n"
            'apiVersion: v1\nkind: Service\nmetadata:\n  name: "billing"\n---\n'
            "apiVersion: v1\nkind: Namespace\n",
            ("Deployment/billing-api", "Service/billing", "Namespace"),
        ),
    ],
)
def test_contract_names(path: str, text: str, names: tuple[str, ...]) -> None:
    assert contract_names(Evidence("a/b", contracts=(TextFile(path, text),))) == names
