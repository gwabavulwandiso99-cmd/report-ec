"""Advisory triage of community verification responses (Module E).

Workflow it supports:
    municipality claims the problem is fixed -> report is pending_verification
    -> residents confirm or dispute -> the AUTHORISED workflow sets the status.

What this module does:
- reads each resident statement and says whether its wording confirms the fix,
  disputes it, or is unclear
- notices when a resident's menu choice and their words disagree
- flags a contradiction between the municipal claim and any dispute
- says when a human should review

What it never does:
- set or suggest a final status (the output has changes_status = False)
- count votes: one credible dispute is enough for review, and many
  confirmations do not prove a repair
- check photos (it only notes that a resident says they have one)
"""
from typing import Optional

from report_ec_ai.integration.schemas import (
    StatementAssessment,
    VerificationAnalysis,
    VerificationRequest,
    VerificationStatement,
)
from report_ec_ai.config import lexicon
from report_ec_ai.intelligence.guards import looks_like_instruction
from report_ec_ai.language.normalization import normalize
from report_ec_ai.language.vocabulary import verification_vocabulary


def read_stance(text: Optional[str]):
    """Returns (stance, cues, mentions_evidence) from the wording of one statement."""
    if not text or not text.strip():
        return "unclear", [], False
    normalized = normalize(text, fuzzy=False)
    tokens = normalized.tokens
    matches = verification_vocabulary().match(tokens)
    negations = set(lexicon()["negation_words_en"])

    disputes = 0.0
    confirms = 0.0
    cues = []
    mentions_evidence = False
    for m in matches:
        flags = verification_vocabulary().concepts[m.concept_id].get("flags", [])
        if "mentions_photo" in flags:
            mentions_evidence = True
            continue
        if "dispute" in flags:
            disputes += 1
            cues.append(f"dispute: '{m.text}'")
        elif "dispute_weak" in flags:
            disputes += 0.5
            cues.append(f"weak dispute: '{m.text}'")
        elif "confirm" in flags or "thanks" in flags:
            # "not fixed", "never repaired": a negation shortly before flips the meaning
            before = tokens[max(0, m.position - 3):m.position]
            if any(word in negations for word in before):
                disputes += 1
                cues.append(f"negated confirmation: '{' '.join(before)} {m.text}'")
            elif "thanks" in flags:
                confirms += 0.25          # "thank you" alone is weak evidence
                cues.append(f"thanks: '{m.text}'")
            else:
                confirms += 1
                cues.append(f"confirm: '{m.text}'")

    if disputes > confirms and disputes >= 0.5:
        stance = "disputes"
    elif confirms > disputes and confirms >= 1:
        stance = "confirms"
    else:
        stance = "unclear"
    return stance, cues, mentions_evidence


def _claim_says_resolved(claim: Optional[str]) -> bool:
    """A claim counts as 'resolved' unless its own words dispute it. No claim text = assume it was a fix claim."""
    if claim is None or not claim.strip():
        return True
    stance, _, _ = read_stance(claim)
    return stance != "disputes"


def analyze_verification(request: VerificationRequest) -> VerificationAnalysis:
    claim_resolved = _claim_says_resolved(request.municipal_claim)
    assessments = []
    counts = {"confirms": 0, "disputes": 0, "unclear": 0, "with_new_evidence": 0, "inconsistent": 0}

    for statement in request.statements:
        stance, cues, mentions_evidence = read_stance(statement.text)
        selected = statement.stance_selected
        # The resident's own menu choice counts when the words say nothing clear
        effective = stance
        if stance == "unclear" and selected in ("confirm", "dispute"):
            effective = "confirms" if selected == "confirm" else "disputes"
        inconsistent = (
            (selected == "confirm" and stance == "disputes")
            or (selected == "dispute" and stance == "confirms")
        )
        if inconsistent:
            # Never pick a side for the resident; treat it as unclear and ask a human
            effective = "unclear"
        counts[effective] += 1
        counts["with_new_evidence"] += int(statement.has_new_evidence)
        counts["inconsistent"] += int(inconsistent)
        assessments.append(StatementAssessment(
            statement_id=statement.statement_id,
            stance_from_text=stance,
            stance_selected=selected,
            mentions_evidence=mentions_evidence,
            has_new_evidence=statement.has_new_evidence,
            inconsistent_with_selection=inconsistent,
            instruction_like_text=looks_like_instruction(statement.text or ""),
            cues=cues,
        ))

    contradiction = claim_resolved and counts["disputes"] > 0
    instruction_like = any(a.instruction_like_text for a in assessments)
    needs_review = contradiction or counts["inconsistent"] > 0 or instruction_like or (
        counts["unclear"] > 0 and counts["confirms"] == 0)

    parts = []
    if not request.statements:
        parts.append("No community responses yet.")
    if contradiction:
        evidence_note = ""
        if counts["with_new_evidence"] or any(a.mentions_evidence for a in assessments):
            evidence_note = " At least one resident says they have new evidence (not checked by the AI)."
        parts.append(
            f"Possible contradiction: the municipality claims the problem is fixed, but "
            f"{counts['disputes']} response(s) say it is not.{evidence_note}"
        )
    if counts["confirms"]:
        parts.append(f"{counts['confirms']} response(s) agree that it is fixed.")
    if counts["unclear"]:
        parts.append(f"{counts['unclear']} response(s) are unclear.")
    if counts["inconsistent"]:
        parts.append(f"{counts['inconsistent']} response(s) chose one option but wrote the opposite.")
    if instruction_like:
        parts.append("A response contains text that looks like an instruction to the system; it was ignored as an instruction.")

    if contradiction:
        next_step = ("Human review needed: send someone to inspect the site, or ask the resident(s) "
                     "for details, before any final status is set.")
    elif needs_review:
        next_step = "Human review needed: the responses are unclear or inconsistent."
    elif counts["confirms"]:
        next_step = ("Responses so far support the claim. The authorised verification workflow "
                     "decides whether this is enough to close the report.")
    else:
        next_step = "Wait for community responses."

    uncertainty = ("Stance is read from wording only, using a small English/isiXhosa word list. "
                   "It can miss sarcasm, unusual wording or other languages. Photos are not analysed.")

    return VerificationAnalysis(
        reference=request.reference,
        claim_says_resolved=claim_resolved,
        contradiction_detected=contradiction,
        needs_human_review=needs_review,
        counts=counts,
        statements=assessments,
        explanation=" ".join(parts),
        suggested_next_step=next_step,
        uncertainty_note=uncertainty,
    )


def analyze_single_response(claim: Optional[str], text: Optional[str], stance_selected: str,
                            has_new_evidence: bool = False, reference: Optional[str] = None) -> VerificationAnalysis:
    """Convenience wrapper used by the chatbot for one resident's response."""
    return analyze_verification(VerificationRequest(
        reference=reference,
        municipal_claim=claim,
        statements=[VerificationStatement(text=text, stance_selected=stance_selected,
                                          has_new_evidence=has_new_evidence)],
    ))
