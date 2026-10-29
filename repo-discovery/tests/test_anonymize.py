from __future__ import annotations

import re

import pytest

from repo_discovery.aliases import AliasIndex, tokenize
from repo_discovery.anonymize import (
    REDACTED,
    AliasFrequency,
    Pseudonyms,
    Scrubber,
    ScrubStats,
    aliases_in,
    token_spans,
)

REPOS = [
    "getsentry/sentry",
    "getsentry/sentry-cli",
    "getsentry/sentry-javascript",
    "sveltejs/svelte",
    "sveltejs/kit",
    "elastic/beats",
    "acme/beats",
    "pallets/click",
]


@pytest.fixture(scope="module")
def names() -> Pseudonyms:
    return Pseudonyms.build(REPOS, b"test-salt")


@pytest.mark.parametrize(
    "text",
    [
        "EuiFlyout uses huggingface_hub/v2",
        "DoctrineMongoDBBundle and HTTPServer, getXMLHttpRequest2Go",
        "  sentry-cli\tSentry CLI -- github.com/getsentry/sentry-cli#L10 ",
        "",
    ],
)
def test_token_spans_match_tokenize(text: str) -> None:
    spans = token_spans(text)
    assert [t for _, _, t in spans] == tokenize(text)
    assert all(text[a:b].lower() == t for a, b, t in spans)


def test_pseudonyms_are_stable_and_keep_owners(names: Pseudonyms) -> None:
    again = Pseudonyms.build(reversed(REPOS), b"test-salt")
    assert again == names
    assert Pseudonyms.build(REPOS, b"other").repos != names.repos
    assert len(set(names.repos.values())) == len(REPOS)
    owners = {names.repo(r).split("/")[0] for r in REPOS if r.startswith("getsentry/")}
    assert owners == {names.owner("getsentry")}
    assert names.repo("GetSentry/Sentry-CLI") == names.repo("getsentry/sentry-cli")
    assert all(re.fullmatch(r"o[0-9a-f]{8}/[or][0-9a-f]{8}", a) for a in names.repos.values())


def test_repository_named_after_its_owner_takes_the_owner_id(names: Pseudonyms) -> None:
    assert (
        names.repo("getsentry/sentry") == f"{names.owner('getsentry')}/{names.owner('getsentry')}"
    )
    assert names.repo("sveltejs/svelte").split("/")[1] == names.owner("sveltejs")
    assert names.repo("getsentry/sentry-cli").split("/")[1].startswith("r")


def test_scrub_replaces_full_qualified_bare_and_owner_mentions(names: Pseudonyms) -> None:
    cli = names.repo("getsentry/sentry-cli")
    owner, name = cli.split("/")
    text = "Sentry CLI (getsentry/sentry-cli): run `sentry-cli upload`. Report to Sentry."
    stats = ScrubStats()
    scrubbed = Scrubber(names).scrub(text, "getsentry/sentry-cli", stats)
    assert scrubbed == f"{name} ({cli}): run `{name} upload`. Report to {owner}."
    assert stats.by_kind == {"full": 1, "name": 3}
    assert stats.by_scope == {"own": 3, "owner": 1}
    assert stats.by_alias == {"own:getsentrysentrycli": 1, "own:sentrycli": 2, "owner:sentry": 1}


def test_scrub_keeps_camel_case_boundaries(names: Pseudonyms) -> None:
    scrubbed = Scrubber(names).scrub("class MySentryClient", "getsentry/sentry-cli")
    assert tokenize(scrubbed) == ["class", "my", names.owner("getsentry"), "client"]


def test_nested_names_go_to_the_longer_mention(names: Pseudonyms) -> None:
    scrubbed = Scrubber(names).scrub("Built with SvelteKit and Svelte.")
    kit, svelte = names.repo("sveltejs/kit"), names.repo("sveltejs/svelte").split("/")[1]
    assert scrubbed == f"Built with {kit} and {svelte}."


def test_common_aliases_are_only_replaced_in_their_own_packet(names: Pseudonyms) -> None:
    scrubber = Scrubber(names, common={"click"})
    text = "Click the button; click is great."
    assert scrubber.scrub(text, "acme/beats") == text
    assert scrubber.scrub(text, "pallets/other") == text
    click = names.repo("pallets/click").split("/")[1]
    assert (
        scrubber.scrub(text, "pallets/click")
        == f"{click[0]}{click[1:]} the button; {click} is great."
    )


def test_common_aliases_are_replaced_for_the_owner_where_they_are_its_flagship(
    names: Pseudonyms,
) -> None:
    text = "Report to Sentry."
    owner = names.owner("getsentry")
    plain = Scrubber(names, common={"sentry"})
    flagship = Scrubber(names, common={"sentry"}, flagships={("getsentry", "sentry")})
    assert plain.scrub(text, "getsentry/sentry-cli") == text
    assert flagship.scrub(text, "getsentry/sentry-cli") == f"Report to {owner}."
    assert flagship.scrub(text, "acme/beats") == text
    assert flagship.scrub(text, "getsentry/sentry") == f"Report to {owner}."


def test_proper_aliases_are_only_replaced_where_capitalized(names: Pseudonyms) -> None:
    scrubber = Scrubber(names, proper={"click"})
    click = names.repo("pallets/click").split("/")[1]
    assert scrubber.scrub("Use Click; click twice.") == f"Use {click}; click twice."


def test_common_owner_stems_are_kept_outside_flagships(names: Pseudonyms) -> None:
    scrubber = Scrubber(names, common={"elastic"})
    beats = names.repo("elastic/beats").split("/")[1]
    assert scrubber.scrub("Elastic ships beats", "elastic/beats") == f"Elastic ships {beats}"
    assert scrubber.scrub("Elastic Beats", "elastic/beats") == names.repo("elastic/beats")


def test_shared_names_are_redacted_unless_the_context_claims_one(names: Pseudonyms) -> None:
    scrubber = Scrubber(names)
    assert scrubber.scrub("uses beats") == f"uses {REDACTED}"
    assert (
        scrubber.scrub("uses beats", "elastic/beats")
        == f"uses {names.repo('elastic/beats').split('/')[1]}"
    )


def test_scrubbed_text_names_the_same_repositories_under_their_ids(names: Pseudonyms) -> None:
    text = "Fix sentry-javascript and the Sentry CLI; see github.com/getsentry/sentry-cli."
    original = AliasIndex.build(REPOS).matches(text)
    anonymized = AliasIndex.build(names.repos.values()).matches(Scrubber(names).scrub(text))
    assert {names.repo(r) for r in original} <= set(anonymized)


def test_alias_frequency_separates_other_owners_from_the_claiming_owner(
    names: Pseudonyms,
) -> None:
    scrubber = Scrubber(names)
    texts = {
        "getsentry/sentry": "Sentry, by Sentry",
        "getsentry/sentry-cli": "Upload to Sentry; click",
        "getsentry/sentry-javascript": "Sentry for the browser",
        "acme/beats": "click there; sentry",
        "pallets/click": "click here",
        "x/y": "Click",
        "x/z": "nothing",
    }
    assert aliases_in(scrubber, "Sentry CLI") >= {"sentry", "sentrycli"}
    freq = AliasFrequency.count(scrubber, [(r, aliases_in(scrubber, t)) for r, t in texts.items()])
    assert freq.docs == 7 and freq.owner_docs["getsentry"] == 3
    assert freq.other["click"] == 3 and freq.other["sentry"] == 1
    assert freq.owner["getsentry", "sentry"] == 2 and freq.claimant_docs["getsentry", "sentry"] == 1
    assert freq.share("click") == pytest.approx(3 / 7)
    assert freq.lift("getsentry", "sentry") == pytest.approx((2 / 2) / (1 / 4))
    assert freq.lift("pallets", "click") == 0.0
