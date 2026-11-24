"""Phase 3-7: policy, NLI wrapper, gate orchestration, LLM fallback, repair loop, submit_batch wiring."""

import asyncio
import threading
from types import SimpleNamespace
from typing import Sequence

import pytest

from app.core import config as _config  # noqa: F401
from app.schemas.ingestion.coverage import ChunkCoverage, PreparedChunk
from app.schemas.ingestion.grounding import Evidence, PropertyFact
from app.schemas.ingestion.ontology import OntologyProjection
from app.schemas.ingestion.semantic_patch import (
    SemanticGraphEdge,
    SemanticGraphNode,
    SemanticGraphPatchFragment,
)
from app.services.ingestion.engine import operations
from app.services.ingestion.schema.graph_patch_compiled import GraphPatchCompiler
from app.services.ingestion.schema.semantic_validation import (
    CachingNliVerifier,
    DecisionPolicy,
    FallbackRequest,
    FallbackVerdict,
    NliPair,
    NliScores,
    NliUnavailableError,
    SemanticEvidenceGate,
    Verdict,
    aggregate,
)

ENT = NliScores(0.98, 0.01, 0.01)
CON = NliScores(0.01, 0.01, 0.98)
NEU = NliScores(0.02, 0.96, 0.02)
AMB = NliScores(0.50, 0.30, 0.20)


class FakeNli:
    """Scripted NLI: scores chosen by substring of the hypothesis (claim)."""

    model_version = "fake"

    def __init__(self, script=None, default=ENT) -> None:
        self.script, self.default, self.calls = script or {}, default, []

    def verify_batch(self, pairs: Sequence[NliPair]):
        self.calls.append(list(pairs))
        return [
            next((s for key, s in self.script.items() if key in p.hypothesis), self.default)
            for p in pairs
        ]


class BrokenNli:
    model_version = "broken"

    def verify_batch(self, pairs):
        raise NliUnavailableError("no model")


class FakeLlm:
    def __init__(self, decisions) -> None:
        self.decisions, self.requests = decisions, []

    async def judge(self, requests: Sequence[FallbackRequest]):
        self.requests.extend(requests)
        return {r.key: self.decisions[r.key] for r in requests if r.key in self.decisions}


# ----------------------------------------------------------------- fixtures
SOURCE = "Đai đỏ tối thiểu 12 tuổi."


def _projection() -> OntologyProjection:
    return OntologyProjection(
        version_id="version-1", version="v1", digest="d1", scope_key="general",
        scope_keys=["general"],
        entity_types=[{"technicalName": "belt", "identityStrategy": {"required": ["name"]}}],
        properties=[
            {"entityType": "belt", "technicalName": "name", "dataType": "STRING", "required": False},
            {"entityType": "belt", "technicalName": "minimum_age", "dataType": "INTEGER", "required": False},
        ],
        relationships=[], aliases=[],
    )


def _chunk(text: str = SOURCE) -> PreparedChunk:
    return PreparedChunk(
        chunk_id="c0", chunk_index=0, text=text, content_hash="h", token_count=5,
        source_anchor="doc.md",
    )


def _semantic(age: int) -> SemanticGraphPatchFragment:
    ev = [Evidence(chunk_index=0, text=SOURCE)]
    return SemanticGraphPatchFragment(
        nodes=[
            SemanticGraphNode(
                temp_id="belt_red",
                class_name="belt",
                properties=[
                    PropertyFact(property_name="name", value="đỏ", evidence=ev),
                    PropertyFact(property_name="minimum_age", value=age, evidence=ev),
                ],
            )
        ],
        edges=[],
        coverage=[ChunkCoverage(chunk_index=0, decision="MAPPED", reason="r")],
        warnings=[],
    )


def _compiled(age: int):
    return GraphPatchCompiler().compile(_semantic(age), _projection()).fragment


def _verify(gate, age=12, chunk_text=SOURCE):
    return asyncio.run(gate.verify(_compiled(age), [_chunk(chunk_text)]))


# ------------------------------------------------------------------- policy
@pytest.mark.parametrize(
    "scores,expected",
    [
        (ENT, Verdict.SUPPORTED),
        (CON, Verdict.REFUTED),
        (NEU, Verdict.NEI),
        (AMB, Verdict.UNCERTAIN),
        (NliScores(0.91, 0.08, 0.01), Verdict.SUPPORTED),
        (NliScores(0.91, 0.01, 0.08), Verdict.SUPPORTED),
        (NliScores(0.89, 0.10, 0.01), Verdict.UNCERTAIN),  # dưới threshold
        (NliScores(0.91, 0.0, 0.09), Verdict.SUPPORTED),
        (NliScores(0.40, 0.55, 0.05), Verdict.UNCERTAIN),
    ],
)
def test_policy(scores, expected) -> None:
    assert DecisionPolicy().decide(scores) is expected


def test_policy_requires_margin() -> None:
    assert DecisionPolicy(support_threshold=0.5, minimum_margin=0.2).decide(
        NliScores(0.55, 0.45, 0.0)
    ) is Verdict.UNCERTAIN


S, R, N, U = Verdict.SUPPORTED, Verdict.REFUTED, Verdict.NEI, Verdict.UNCERTAIN


@pytest.mark.parametrize(
    "items,expected",
    [([S], S), ([S, N], S), ([S, U], S), ([S, R], U), ([R, N], R), ([R], R), ([N, N], N), ([N, U], U), ([], U)],
)
def test_aggregate(items, expected) -> None:
    assert aggregate(items) is expected


# --------------------------------------------------------------------- gate
def test_exact_quote_correct_claim_is_supported() -> None:
    report = _verify(SemanticEvidenceGate(nli=FakeNli()))
    assert report.findings and all(f.verdict is S for f in report.findings)
    assert report.to_issues() == []


def test_wrong_number_refuted_by_rule_without_nli_call() -> None:
    nli = FakeNli()
    report = _verify(SemanticEvidenceGate(nli=nli), age=10)
    [issue] = report.to_issues()
    assert issue.code == "EVIDENCE_NUMERIC_MISMATCH"
    assert issue.location == "nodes.0.properties.1"
    # chỉ claim `name` cần NLI; claim sai số bị rule chặn trước
    assert all("12" not in p.hypothesis and "10" not in p.hypothesis for batch in nli.calls for p in batch)


def test_wrong_subject_is_not_supported_by_nli() -> None:
    nli = FakeNli({"minimum age": CON})
    report = _verify(SemanticEvidenceGate(nli=nli))
    codes = [i.code for i in report.to_issues()]
    assert codes == ["EVIDENCE_SEMANTIC_REFUTED"]


def test_related_but_insufficient_is_nei_and_blocks() -> None:
    report = _verify(SemanticEvidenceGate(nli=FakeNli(default=NEU)))
    assert {i.code for i in report.to_issues()} == {"EVIDENCE_SEMANTIC_NEI"}


def test_uncertain_not_blocking_when_configured() -> None:
    gate = SemanticEvidenceGate(nli=FakeNli(default=AMB), block_uncertain=False)
    assert _verify(gate).to_issues(block_uncertain=gate.block_uncertain) == []


def test_nli_unavailable_fails_closed() -> None:
    report = _verify(SemanticEvidenceGate(nli=BrokenNli()))
    assert report.nli_available is False
    assert {i.code for i in report.to_issues()} == {"EVIDENCE_SEMANTIC_UNCERTAIN"}


def test_single_nli_batch_for_whole_fragment() -> None:
    nli = FakeNli()
    _verify(SemanticEvidenceGate(nli=nli))
    assert len(nli.calls) == 1


def test_long_evidence_uses_relevant_windows_only() -> None:
    filler = " ".join(f"Câu nhiễu số {i} không liên quan." for i in range(60))
    text = f"{filler} Đai đỏ tối thiểu 12 tuổi. {filler}"
    nli = FakeNli()
    asyncio.run(SemanticEvidenceGate(nli=nli).verify(_compiled(12), [_chunk(text)]))
    # evidence trong fragment là SOURCE; kiểm tra window cắt đúng trên text dài
    from app.services.ingestion.schema.semantic_validation.claim_builder import build_property_claim
    from app.services.ingestion.schema.semantic_validation.semantic_gate import evidence_windows

    claim = build_property_claim(
        class_name="belt", identity={"name": "đỏ"}, property_name="minimum_age",
        value=12, location="x",
    )
    windows = evidence_windows(claim, text)
    assert len(windows) <= 4 and any("12 tuổi" in w for w in windows)


def test_multi_chunk_evidence_evaluates_combined_and_passes_all_chunks_to_fallback() -> None:
    c1 = PreparedChunk(
        chunk_id="c31", chunk_index=31, text="10.1. Mục đích: Bù đắp nền tảng.",
        content_hash="h1", token_count=5, source_anchor="doc.md",
    )
    c2 = PreparedChunk(
        chunk_id="c32", chunk_index=32, text="10.2. Thời lượng: 3/5 buổi bổ trợ.",
        content_hash="h2", token_count=5, source_anchor="doc.md",
    )
    fragment = GraphPatchCompiler().compile(
        SemanticGraphPatchFragment(
            nodes=[
                SemanticGraphNode(
                    temp_id="prog",
                    class_name="belt",
                    properties=[
                        PropertyFact(
                            property_name="name",
                            value="Mục đích: Bù đắp nền tảng. Thời lượng: 3/5 buổi bổ trợ.",
                            evidence=[
                                Evidence(chunk_index=31, text=c1.text),
                                Evidence(chunk_index=32, text=c2.text),
                            ],
                        )
                    ],
                )
            ],
            edges=[],
            coverage=[
                ChunkCoverage(chunk_index=31, decision="MAPPED", reason="r"),
                ChunkCoverage(chunk_index=32, decision="MAPPED", reason="r"),
            ],
            warnings=[],
        ),
        _projection(),
    ).fragment

    class CombinedOnlyNli:
        model_version = "combined"

        def verify_batch(self, pairs: Sequence[NliPair]):
            return [
                ENT if ("10.1" in p.premise and "10.2" in p.premise) else NEU
                for p in pairs
            ]

    report = asyncio.run(
        SemanticEvidenceGate(nli=CombinedOnlyNli()).verify(fragment, [c1, c2])
    )
    assert report.to_issues() == []

    # Khi cả NLI vẫn NEI và xuống LLM fallback, source_text phải chứa đủ cả chunk 31 và 32
    llm = FakeLlm({
        "nodes.0.properties.0": FallbackVerdict(S, "10.2. Thời lượng: 3/5 buổi bổ trợ.")
    })
    report_fallback = asyncio.run(
        SemanticEvidenceGate(nli=FakeNli(default=NEU), llm_fallback=llm).verify(
            fragment, [c1, c2]
        )
    )
    assert len(llm.requests) == 1
    assert "10.1. Mục đích" in llm.requests[0].source_text
    assert "10.2. Thời lượng" in llm.requests[0].source_text
    assert report_fallback.to_issues() == []


# ------------------------------------------------------------- LLM fallback
def test_nli_uncertain_falls_back_to_llm_and_accepts_verbatim_quote() -> None:
    llm = FakeLlm({
        "nodes.0.properties.0": FallbackVerdict(S, "Đai đỏ tối thiểu 12 tuổi."),
        "nodes.0.properties.1": FallbackVerdict(S, "Đai đỏ tối thiểu 12 tuổi."),
    })
    report = _verify(SemanticEvidenceGate(nli=FakeNli(default=AMB), llm_fallback=llm))
    assert len(llm.requests) == 2
    assert all(f.verdict is S and f.source == "llm" for f in report.findings)


def test_llm_source_quote_not_in_source_is_rejected() -> None:
    llm = FakeLlm({
        "nodes.0.properties.0": FallbackVerdict(S, "câu bịa không có trong nguồn"),
        "nodes.0.properties.1": FallbackVerdict(S, None),
    })
    report = _verify(SemanticEvidenceGate(nli=FakeNli(default=AMB), llm_fallback=llm))
    assert all(f.verdict is U for f in report.findings)
    assert report.to_issues()


def test_llm_not_called_when_nli_decisive() -> None:
    llm = FakeLlm({})
    _verify(SemanticEvidenceGate(nli=FakeNli(), llm_fallback=llm))
    assert llm.requests == []


def test_llm_not_called_for_rule_refutation() -> None:
    llm = FakeLlm({})
    _verify(SemanticEvidenceGate(nli=FakeNli(), llm_fallback=llm), age=10)
    assert llm.requests == []


def test_llm_refuted_and_failure_modes() -> None:
    llm = FakeLlm({"nodes.0.properties.0": FallbackVerdict(R, None, "sai")})
    report = _verify(SemanticEvidenceGate(nli=FakeNli(default=NEU), llm_fallback=llm))
    codes = {f.claim.location: f.code for f in report.findings}
    assert codes["nodes.0.properties.0"] == "EVIDENCE_SEMANTIC_REFUTED"
    assert codes["nodes.0.properties.1"] == "EVIDENCE_SEMANTIC_NEI"

    class Boom:
        async def judge(self, requests):
            raise RuntimeError("llm down")

    report = _verify(SemanticEvidenceGate(nli=FakeNli(default=NEU), llm_fallback=Boom()))
    assert not any(f.verdict is S for f in report.findings)


# --------------------------------------------------------------- NLI cache
def test_caching_nli_dedupes_and_is_thread_safe() -> None:
    inner = FakeNli()
    cache = CachingNliVerifier(inner, max_entries=2)
    pairs = [NliPair("p", "h1"), NliPair("p", "h1"), NliPair("p", "h2")]
    out = cache.verify_batch(pairs)
    assert len(out) == 3 and len(inner.calls[0]) == 2
    cache.verify_batch(pairs)
    assert len(inner.calls) == 1
    threads = [threading.Thread(target=cache.verify_batch, args=(pairs,)) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]


# ------------------------------------------- submit_batch + repair (Phase 6/7)
class FakeRepo:
    def __init__(self) -> None:
        self.batch = SimpleNamespace(
            batch_index=0, status="PENDING", scope_keys=["general"], chunk_indexes=[0],
            validation_issues=[], validated_baseline=None,
        )
        self.workspace = SimpleNamespace(
            job=SimpleNamespace(id="ing-1", ontology_version_id="version-1"),
            batches=[self.batch],
            chunks=[SimpleNamespace(**_chunk().model_dump())],
        )
        self.stored: list[list[dict]] = []

    async def get_workspace(self, _id):
        return self.workspace

    async def store_batch_result(self, _id, _idx, _bindings, _digest, _semantic, fragment, issues, **kw):
        self.stored.append(issues)
        self.batch.validation_issues = issues
        self.batch.status = "REPAIR_REQUIRED" if issues else "STAGED"
        return self.workspace


class FakeCache:
    def __init__(self) -> None:
        self.projection = _projection()

    async def get_many(self, keys, _version):
        return SimpleNamespace(
            scope_keys=["general"], digest="d1",
            entity_types=self.projection.entity_types, properties=self.projection.properties,
            relationships=[], aliases=[], version=self.projection.version,
            version_id=self.projection.version_id, scope_key="general",
            description=self.projection.description, compiler_version=None,
        )

    async def get(self, _key, _version):
        return SimpleNamespace(digest="d1")


def _submit(repo, gate, age):
    cache = FakeCache()

    async def run():
        cache.projection = _projection()
        return await operations.submit_batch(
            repo, cache, "ing-1", 0, ["general"], _semantic(age), semantic_gate=gate
        )

    return asyncio.run(run())


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(operations, "staged_entity_index", lambda *a, **k: {})
    monkeypatch.setattr(operations, "OntologyRegistry", operations.OntologyRegistry)
    return monkeypatch


def test_submit_rejects_wrong_fact_then_repair_is_verified_again(patched) -> None:
    repo = FakeRepo()
    gate = SemanticEvidenceGate(nli=FakeNli(), enforce=True)

    _submit(repo, gate, 10)  # extractor sai
    assert [i["code"] for i in repo.stored[-1]] == ["EVIDENCE_NUMERIC_MISMATCH"]
    assert repo.batch.status == "REPAIR_REQUIRED"

    _submit(repo, gate, 11)  # repair vẫn sai -> REFUTED lần nữa
    assert [i["code"] for i in repo.stored[-1]] == ["EVIDENCE_NUMERIC_MISMATCH"]

    _submit(repo, gate, 12)  # repair đúng -> SUPPORTED
    assert repo.stored[-1] == []
    assert repo.batch.status == "STAGED"


def test_submit_shadow_mode_logs_but_does_not_block(patched) -> None:
    repo = FakeRepo()
    _submit(repo, SemanticEvidenceGate(nli=FakeNli(), enforce=False), 10)
    assert repo.stored[-1] == []


def test_submit_without_gate_keeps_legacy_behaviour(patched) -> None:
    repo = FakeRepo()
    _submit(repo, None, 10)
    assert repo.stored[-1] == []
