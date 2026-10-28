from __future__ import annotations

from pathlib import Path

import pytest

from repo_discovery.api_contracts import (
    CONSUMES,
    PROVIDES,
    Identifier,
    asyncapi_identifiers,
    contract_identifiers,
    contract_text,
    doc_references,
    identifiers,
    normalize_path,
    openapi_identifiers,
    proto_files,
    proto_identifiers,
    resource_identifiers,
)
from repo_discovery.evidence import read_evidence


def ids(found) -> set[tuple[str, str, str]]:
    return {(i.type, i.value, i.role) for i in found}


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ("/api/v1/Users/{id}/", "/users/{}"),
        ("/users/42?pretty", "/users/{}"),
        ("/api/0/organizations/{org}/issues/", "/organizations/{}/issues"),
        ("/v2beta1/pets/:petId", "/pets/{}"),
        ("/_cluster/health#top", "/_cluster/health"),
        ("/status", None),
        ("/{a}/{b}", None),
        ("/users/$USER", None),
    ],
)
def test_normalize_path(raw: str, key: str | None) -> None:
    assert normalize_path(raw) == key


OPENAPI = """\
openapi: 3.0.0
info:
  title: Fleet
paths:
  /api/fleet/agents/{agentId}/checkin:
    post:
      operationId: agentCheckin
  /api/fleet/agents/enroll:
    post:
      operationId: AgentEnroll
components: {}
"""


def test_openapi_yaml_and_json() -> None:
    expected = {
        ("openapi.path", "/fleet/agents/{}/checkin", PROVIDES),
        ("openapi.path", "/fleet/agents/enroll", PROVIDES),
        ("openapi.operation", "agentcheckin", PROVIDES),
        ("openapi.operation", "agentenroll", PROVIDES),
    }
    assert ids(openapi_identifiers(OPENAPI)) == expected
    spec = '{"swagger": "2.0", "paths": {"/api/fleet/agents/enroll": {"post": {}}}}'
    assert ids(contract_identifiers("api/spec.json", spec)) == {
        ("openapi.path", "/fleet/agents/enroll", PROVIDES)
    }


def test_asyncapi_channels_and_addresses() -> None:
    v2 = "asyncapi: 2.6.0\nchannels:\n  user/signedup:\n    publish: {}\n"
    v3 = "asyncapi: 3.0.0\nchannels:\n  signup:\n    address: users.{id}.signup\n"
    assert ids(asyncapi_identifiers(v2)) == {("asyncapi.channel", "user/signedup", PROVIDES)}
    assert ids(asyncapi_identifiers(v3)) == {
        ("asyncapi.channel", "signup", PROVIDES),
        ("asyncapi.channel", "users.{id}.signup", PROVIDES),
    }
    json = '{"asyncapi": "2.0.0", "channels": {"orders": {}}}'
    assert ids(asyncapi_identifiers(json, json=True)) == {("asyncapi.channel", "orders", PROVIDES)}


PROTO = """\
syntax = "proto3";
// package commented.out;
package fleet.control.v1;
import "google/protobuf/timestamp.proto";
import "common/status.proto";

service AgentControl {
  rpc Check(CheckRequest) returns (common.v1.Status);
}
message CheckRequest {
  google.protobuf.Timestamp at = 1;
  repeated common.v1.Labels labels = 2;
  fleet.control.v1.CheckRequest self = 3;
}
enum Mode { MODE_UNSPECIFIED = 0; }
"""


def test_proto_declarations_imports_and_foreign_types() -> None:
    assert ids(proto_identifiers(PROTO)) == {
        ("proto.package", "fleet.control.v1", PROVIDES),
        ("proto.service", "fleet.control.v1.AgentControl", PROVIDES),
        ("proto.message", "fleet.control.v1.CheckRequest", PROVIDES),
        ("proto.message", "fleet.control.v1.Mode", PROVIDES),
        ("proto.file", "common/status.proto", CONSUMES),
        ("proto.package", "common.v1", CONSUMES),
        ("proto.message", "common.v1.Status", CONSUMES),
        ("proto.message", "common.v1.Labels", CONSUMES),
    }


def test_well_known_protos_are_not_keys() -> None:
    timestamp = 'syntax = "proto3";\npackage google.protobuf;\nmessage Timestamp {}\n'
    assert not list(proto_identifiers(timestamp))
    paths = ["third_party/google/protobuf/any.proto", "api/v1/agent.proto", "README.md"]
    assert ids(proto_files(paths)) == {
        ("proto.file", "api/v1/agent.proto", PROVIDES),
        ("proto.file", "v1/agent.proto", PROVIDES),
        ("proto.file", "agent.proto", PROVIDES),
    }


RESOURCES = """\
apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  name: agents.agent.k8s.elastic.co
spec:
  group: agent.k8s.elastic.co
  names:
    kind: Agent
    plural: agents
---
apiVersion: agent.k8s.elastic.co/v1alpha1
kind: Agent
metadata:
  name: fleet-server
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: "{{ .Release.Name }}"
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: elastic-agent
"""


def test_crds_custom_resources_and_names() -> None:
    assert ids(resource_identifiers(RESOURCES)) == {
        ("k8s.name", "customresourcedefinition/agents.agent.k8s.elastic.co", PROVIDES),
        ("k8s.kind", "agent.k8s.elastic.co/agent", PROVIDES),
        ("k8s.name", "agent/fleet-server", PROVIDES),
        ("k8s.kind", "agent.k8s.elastic.co/agent", CONSUMES),
        ("k8s.name", "serviceaccount/elastic-agent", PROVIDES),
    }


def test_backstage_references() -> None:
    catalog = """\
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: fleet-server
  owner: group:fleet
spec:
  owner: group:fleet
  dependsOn:
    - component:package-registry
    - resource:default/buildkite-pipeline-fleet-server
"""
    assert ids(resource_identifiers(catalog)) == {
        ("k8s.name", "component/fleet-server", PROVIDES),
        ("k8s.kind", "backstage.io/component", CONSUMES),
        ("k8s.name", "component/package-registry", CONSUMES),
        ("k8s.name", "resource/buildkite-pipeline-fleet-server", CONSUMES),
    }


def test_doc_references() -> None:
    docs = """\
Enroll with `POST /api/fleet/agents/enroll`, then check
`curl -s http://localhost:9200/_cluster/health?pretty`.

```yaml
apiVersion: agent.k8s.elastic.co/v1alpha1
kind: Agent
```
A built-in: apiVersion: apps/v1
kind: Deployment
"""
    assert ids(doc_references(docs)) == {
        ("openapi.path", "/fleet/agents/enroll", CONSUMES),
        ("openapi.path", "/_cluster/health", CONSUMES),
        ("k8s.kind", "agent.k8s.elastic.co/agent", CONSUMES),
    }


def test_identifiers_from_a_packet(tmp_path: Path) -> None:
    packet = tmp_path / "repos" / "elastic__fleet-server" / ("0" * 40)
    (packet / "contracts").mkdir(parents=True)
    (packet / "contracts" / "openapi.yml").write_text(OPENAPI)
    (packet / "contracts" / "control.proto").write_text(PROTO)
    (packet / "README.md").write_text("Agents call `GET /api/status/agents`.")
    (packet / "paths.txt").write_text("contracts/openapi.yml\ncontracts/control.proto\n")
    found = identifiers(read_evidence("elastic/fleet-server", packet))
    assert Identifier("openapi.operation", "agentcheckin", PROVIDES) in found
    assert Identifier("proto.file", "contracts/control.proto", PROVIDES) in found
    assert Identifier("proto.file", "control.proto", PROVIDES) in found
    assert Identifier("openapi.path", "/status/agents", CONSUMES) in found
    text = contract_text(found)
    assert "fleet.control.v1.AgentControl" in text
    assert text.count("agent.k8s.elastic.co") == 0
    assert identifiers(read_evidence("x/missing", tmp_path / "nowhere")) == frozenset()
