from __future__ import annotations

import pytest

from repo_discovery.aliases import AliasIndex, AliasKind, mentioned, owner_stems, tokenize


def test_tokenize_splits_camel_case_and_separators() -> None:
    assert tokenize("EuiFlyout uses huggingface_hub/v2") == [
        "eui",
        "flyout",
        "uses",
        "huggingface",
        "hub",
        "v2",
    ]
    assert tokenize("DoctrineMongoDBBundle") == ["doctrine", "mongo", "db", "bundle"]


def test_owner_stems() -> None:
    assert owner_stems("getsentry") == {"getsentry", "sentry"}
    assert owner_stems("sveltejs") == {"sveltejs", "svelte"}
    assert owner_stems("rust-lang") == {"rust-lang", "rust"}
    assert owner_stems("elastic") == {"elastic"}


@pytest.mark.parametrize(
    ("text", "repo"),
    [
        ("Fix the Fleet Server checkin", "elastic/fleet-server"),
        ("the HuggingFace Hub client", "huggingface/huggingface_hub"),
        ("see elastic/kibana#1", "elastic/kibana"),
        ("In Node.js 22", "nodejs/node"),
        ("the transformers JavaScript port", "huggingface/transformers.js"),
    ],
)
def test_name_variants_match(text: str, repo: str) -> None:
    assert repo in AliasIndex.build([repo]).matches(text)


def test_generic_names_need_owner_qualification() -> None:
    index = AliasIndex.build(["nextcloud/server", "home-assistant/core"])
    assert index.matches("The server must reject it, and core logic too.") == {}
    assert set(index.matches("Nextcloud Server and Home Assistant Core")) == {
        "nextcloud/server",
        "home-assistant/core",
    }


def test_nested_match_does_not_count() -> None:
    index = AliasIndex.build(
        ["sveltejs/svelte", "sveltejs/kit", "getsentry/sentry", "getsentry/sentry-react-native"]
    )
    assert set(index.matches("Since SvelteKit 2.21")) == {"sveltejs/kit"}
    assert set(index.matches("Sentry React Native labels")) == {"getsentry/sentry-react-native"}
    assert set(index.matches("Svelte and the Sentry dashboard")) == {
        "sveltejs/svelte",
        "getsentry/sentry",
    }


def test_shared_alias_goes_to_strongest_claim() -> None:
    index = AliasIndex.build(["getsentry/cli", "getsentry/sentry-cli"])
    assert index.matches("run sentry-cli upload") == {"getsentry/sentry-cli": AliasKind.NAME}
    assert AliasIndex.build(["getsentry/cli"]).matches("run sentry-cli upload") == {
        "getsentry/cli": AliasKind.QUALIFIED
    }


def test_mentioned_uses_universe_for_competition() -> None:
    text = "run sentry-cli upload"
    assert mentioned(text, ["getsentry/cli"]) == {"getsentry/cli"}
    assert mentioned(text, ["getsentry/cli"], universe=["getsentry/sentry-cli"]) == set()
