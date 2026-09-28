"""
Result Contracts — Typed multi-layer result states and verification outcomes.

Replaces the single opaque float trust score with explicit execution,
availability, claim assessment, and quality gate layers.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class ExecutionStatus(str, Enum):
    """Execution status of the verification pipeline."""
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


class AssessmentAvailability(str, Enum):
    """Availability of assessable factual claims in the application output."""
    ASSESSED = "ASSESSED"
    NO_ASSESSABLE_CLAIMS = "NO_ASSESSABLE_CLAIMS"
    NOT_EVALUATED = "NOT_EVALUATED"


class ClaimAssessment(str, Enum):
    """Grounding assessment for an individual factual claim against evidence."""
    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    CONFLICTING_SOURCES = "CONFLICTING_SOURCES"


class QualityGate(str, Enum):
    """Final decision of the verification quality gate for CI or runtime policy."""
    PASS = "PASS"
    FAIL = "FAIL"
    INDETERMINATE = "INDETERMINATE"


class ClaimResult(BaseModel):
    """
    Verification outcome for a single decomposed claim.

    Attributes:
        claim_id: Unique identifier for the claim within the case.
        claim_text: Text of the factual claim that was verified.
        assessment: Evaluator assessment against supplied evidence.
        availability: Whether this claim was assessable.
        supporting_sources: List of source_ids that support this claim.
        contradicting_sources: List of source_ids that contradict this claim.
        confidence: Evaluator model confidence score (0.0 to 1.0).
        details: Explanation or rationale from the verifier.
        char_start: Character offset start in the parent response text (optional).
        char_end: Character offset end in the parent response text (optional).
    """

    model_config = ConfigDict(extra="ignore")

    claim_id: str
    claim_text: str
    assessment: ClaimAssessment
    availability: AssessmentAvailability = AssessmentAvailability.ASSESSED
    supporting_sources: List[str] = Field(default_factory=list)
    contradicting_sources: List[str] = Field(default_factory=list)
    confidence: float = 1.0
    details: Optional[str] = None
    char_start: Optional[int] = None
    char_end: Optional[int] = None


class CaseResult(BaseModel):
    """
    Full verification result for an application response across all claims.

    Attributes:
        schema_version: Version identifier for contract schema compatibility.
        case_id: Identifier of the evaluated test case or request (optional).
        execution: Pipeline execution status (SUCCESS, ERROR, TIMEOUT, etc.).
        availability: Claim availability status (ASSESSED, NO_ASSESSABLE_CLAIMS).
        quality_gate: Policy gate outcome (PASS, FAIL, INDETERMINATE).
        claims: Detailed assessment for each evaluated claim.
        trust_score: Backward-compatibility trust score (0.0 to 1.0).
        summary: Human-readable evaluation summary.
        error_message: Error description if execution failed.
        latency_ms: Total verification latency in milliseconds.
        metadata: Arbitrary user-defined key-value attributes.
    """

    model_config = ConfigDict(extra="ignore")

    schema_version: str = "1"
    case_id: Optional[str] = None
    execution: ExecutionStatus = ExecutionStatus.SUCCESS
    availability: AssessmentAvailability = AssessmentAvailability.ASSESSED
    quality_gate: QualityGate = QualityGate.PASS
    claims: List[ClaimResult] = Field(default_factory=list)
    trust_score: float = 0.0
    summary: str = ""
    error_message: Optional[str] = None
    latency_ms: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @property
    def supported_claims_count(self) -> int:
        """Count of claims with assessment == SUPPORTED."""
        return sum(1 for c in self.claims if c.assessment == ClaimAssessment.SUPPORTED)

    @property
    def contradicted_claims_count(self) -> int:
        """Count of claims with assessment == CONTRADICTED."""
        return sum(1 for c in self.claims if c.assessment == ClaimAssessment.CONTRADICTED)

    @property
    def ungrounded_claims_count(self) -> int:
        """Count of claims not fully supported."""
        return sum(1 for c in self.claims if c.assessment != ClaimAssessment.SUPPORTED)


class LegacyVerificationAdapter:
    """
    Bidirectional adapter between legacy VerificationResult and modern CaseResult.

    Ensures zero disruption to existing applications, downstream integrations,
    and storage engines.
    """

    @staticmethod
    def from_legacy(legacy: Any, case_id: Optional[str] = None) -> CaseResult:
        """
        Convert a legacy VerificationResult dataclass into a modern CaseResult.

        Handles:
          - Empty claims: maps to NO_ASSESSABLE_CLAIMS.
          - Flagged claims & hallucinations: maps to CONTRADICTED / INSUFFICIENT_EVIDENCE.
          - Verdict and trust_score preservation.
        """
        claims_list: List[ClaimResult] = []
        raw_claims = getattr(legacy, "claims", []) or []
        flagged_raw = getattr(legacy, "flagged_claims", []) or []
        hallucinations_raw = getattr(legacy, "hallucinations", []) or []

        # Identify texts flagged or hallucinated
        contradicted_texts = {
            h.get("claim", "") if isinstance(h, dict) else str(h)
            for h in hallucinations_raw
        }
        flagged_texts = {
            f.get("claim", "") if isinstance(f, dict) else str(f)
            for f in flagged_raw
        }

        for idx, item in enumerate(raw_claims):
            cid = f"claim_{idx + 1}"
            if isinstance(item, dict):
                ctext = item.get("claim", item.get("text", str(item)))
                sources = item.get("sources", [])
                conf = float(item.get("confidence", item.get("score", 1.0)))
            else:
                ctext = str(item)
                sources = []
                conf = 1.0

            if ctext in contradicted_texts:
                assessment = ClaimAssessment.CONTRADICTED
                contra = sources
                supp = []
            elif ctext in flagged_texts:
                assessment = ClaimAssessment.INSUFFICIENT_EVIDENCE
                contra = []
                supp = []
            else:
                assessment = ClaimAssessment.SUPPORTED
                contra = []
                supp = sources

            claims_list.append(
                ClaimResult(
                    claim_id=cid,
                    claim_text=ctext,
                    assessment=assessment,
                    supporting_sources=supp,
                    contradicting_sources=contra,
                    confidence=conf,
                )
            )

        if not claims_list:
            availability = AssessmentAvailability.NO_ASSESSABLE_CLAIMS
            gate = QualityGate.PASS if getattr(legacy, "all_supported", True) else QualityGate.FAIL
        else:
            availability = AssessmentAvailability.ASSESSED
            gate = QualityGate.PASS if getattr(legacy, "verdict", "PASS") == "PASS" else QualityGate.FAIL

        latency_stats = getattr(legacy, "latency_stats", None)
        latency_ms = None
        if isinstance(latency_stats, dict):
            latency_ms = latency_stats.get("total_ms")

        return CaseResult(
            case_id=case_id,
            execution=ExecutionStatus.SUCCESS,
            availability=availability,
            quality_gate=gate,
            claims=claims_list,
            trust_score=float(getattr(legacy, "trust_score", 0.0)),
            summary=str(getattr(legacy, "summary", "")),
            latency_ms=latency_ms,
        )

    @staticmethod
    def to_legacy(case_result: CaseResult) -> Any:
        """
        Convert a modern CaseResult back into a legacy VerificationResult dataclass.
        """
        # Lazy import to avoid circular dependency
        from longtracer.guard.verifier import VerificationResult

        claims_dicts = []
        flagged_dicts = []
        hallucinations_dicts = []

        for c in case_result.claims:
            cdict = {
                "claim": c.claim_text,
                "assessment": c.assessment.value,
                "confidence": c.confidence,
                "sources": c.supporting_sources,
            }
            claims_dicts.append(cdict)
            if c.assessment in (ClaimAssessment.INSUFFICIENT_EVIDENCE, ClaimAssessment.CONFLICTING_SOURCES):
                flagged_dicts.append(cdict)
            elif c.assessment == ClaimAssessment.CONTRADICTED:
                flagged_dicts.append(cdict)
                hallucinations_dicts.append(cdict)

        all_supported = (len(flagged_dicts) == 0)
        verdict = "PASS" if case_result.quality_gate == QualityGate.PASS else "FAIL"

        return VerificationResult(
            trust_score=case_result.trust_score,
            claims=claims_dicts,
            flagged_claims=flagged_dicts,
            hallucinations=hallucinations_dicts,
            all_supported=all_supported,
            hallucination_count=len(hallucinations_dicts),
            verdict=verdict,
            summary=case_result.summary,
            latency_stats={"total_ms": case_result.latency_ms} if case_result.latency_ms is not None else None,
        )
