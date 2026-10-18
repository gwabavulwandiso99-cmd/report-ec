"""Report EC AI demonstration (7 demos).

Run:  python demo.py            (all demos)
      python demo.py 3          (only demo 3)

Everything here runs locally. The "backend" is MockBackendAdapter: it is NOT
connected to Supabase, WhatsApp or USSD, and its reference numbers start with
MOCK- so they can't be mistaken for real ones. Demo 7 uses the SYNTHETIC
sample data in report_ec_ai/data/sample_complaints.json.
"""
import json
import sys
from datetime import datetime

from report_ec_ai.chatbot.conversation import ConversationEngine
from report_ec_ai.config import DATA_DIR
from report_ec_ai.integration.backend_adapter import MockBackendAdapter
from report_ec_ai.integration.schemas import ReportForMatching, VerificationRequest, VerificationStatement
from report_ec_ai.intelligence.analyzer import analyze_complaint
from report_ec_ai.intelligence.duplicates import DuplicateDetector
from report_ec_ai.intelligence.trends import summarize_trends
from report_ec_ai.intelligence.verification import analyze_verification

LINE = "=" * 78
MOCK_BANNER = "[MOCK BACKEND: local demo only, not connected to Supabase, WhatsApp or USSD]"


def title(number, text):
    print(f"\n{LINE}\nDEMO {number}: {text}\n{LINE}")


def converse(engine, user_id, messages, channel="whatsapp"):
    for message in messages:
        payload = message if isinstance(message, dict) else {"text": message}
        reply = engine.handle({"channel": channel, "user_id": user_id, **payload})
        shown = payload.get("text") or ("[photo]" if payload.get("media") else "[location pin]")
        print(f"\n  RESIDENT: {shown}")
        for text in reply.messages:
            print("  BOT:      " + text.replace("\n", "\n            "))
        if reply.submitted_reference:
            print(f"  >>> backend confirmed the report: {reply.submitted_reference}")
    return reply


def demo_conversation(number, label, user_id, messages):
    title(number, label)
    print(MOCK_BANNER)
    backend = MockBackendAdapter()
    engine = ConversationEngine(backend)
    reply = converse(engine, user_id, messages)
    if reply.submitted_reference:
        payload = backend.reports[reply.submitted_reference]["payload"]
        print("\n  Structured payload the backend received (shortened):")
        short = payload.model_dump(mode="json", include={
            "idempotency_key", "channel", "language", "description_original", "location", "ai_suggestion"})
        short["ai_suggestion"] = {k: short["ai_suggestion"][k] for k in
                                  ("category", "subcategory", "priority", "legacy_category",
                                   "legacy_urgency", "needs_human_review", "advisory_only")}
        print("  " + json.dumps(short, indent=2, ensure_ascii=False).replace("\n", "\n  "))


def demo1():
    demo_conversation(1, "English complaint (WhatsApp-style)", "27820000101", [
        "Hi", "1", "1",
        "There is a burst water pipe near the clinic.",
        "Zwide", "0", "0", "1",
    ])


def demo2():
    demo_conversation(2, "isiXhosa complaint", "27820000102", [
        "Molo", "2", "1",
        "Kukho umbhobho wamanzi ogqabhukileyo kufuphi nekliniki.",
        "eZwide", "andiyazi",
        {"media": [{"media_id": "demo-photo-1", "mime_type": "image/jpeg"}]},
        "1",
    ])


def demo3():
    demo_conversation(3, "Code-switched complaint (isiXhosa + English)", "27820000103", [
        "Molo", "2", "1",
        "Kukho water leak kufuphi neclinic.",
        "Walmer Township", "0", "0", "1",
    ])


def demo4():
    title(4, "Complaint intelligence: category + priority suggestions")
    examples = [
        "Exposed electrical cable near the school",
        "Major sewage overflow near our homes",
        "Dangerous pothole on the main road, cars swerving",
        "Please repaint the faded road sign when possible",
        "Akukho manzi kwiintsuku ezintathu",
        "My neighbour plays loud music",
    ]
    for text in examples:
        result = analyze_complaint(text)
        print(f"\n  \"{text}\"")
        print(f"    language:  {result.language.detected} (confidence {result.language.confidence})")
        print(f"    category:  {result.category.category}/{result.category.subcategory} "
              f"[{result.category.confidence} confidence]")
        print(f"    priority:  {result.priority.priority or 'not suggested'} [{result.priority.confidence}]")
        print(f"    why:       {result.priority.explanation}")
        if result.priority.safety_indicators:
            print(f"    safety:    {', '.join(result.priority.safety_descriptions)}")
        print(f"    ask resident to clarify: {result.needs_clarification} | staff review: {result.needs_human_review}")
    print(f"\n  Every result says: \"{result.advisory_notice}\"")


def demo5():
    title(5, "Potential duplicate detection (nothing is merged or deleted)")
    now = datetime.fromisoformat("2026-10-15T09:00:00+00:00")
    existing = [
        ReportForMatching(reference="SAMPLE-A", description="Burst pipe near the clinic", area="Zwide",
                          created_at="2026-10-15T03:00:00+00:00"),
        ReportForMatching(reference="SAMPLE-B", description="Water leaking outside the clinic", area="Zwide",
                          created_at="2026-10-15T05:00:00+00:00"),
        ReportForMatching(reference="SAMPLE-C", description="Kukho umbhobho wamanzi ogqabhukileyo kufuphi nekliniki",
                          area="Zwide", created_at="2026-10-15T07:00:00+00:00"),
        ReportForMatching(reference="SAMPLE-D", description="Burst pipe near the clinic", area="Motherwell",
                          created_at="2026-10-15T08:00:00+00:00"),
        ReportForMatching(reference="SAMPLE-E", description="Pothole near the clinic", area="Zwide",
                          created_at="2026-10-15T08:00:00+00:00"),
    ]
    new = ReportForMatching(reference="SAMPLE-NEW", description="Broken water pipe at the clinic entrance",
                            area="Zwide", created_at=now)
    before = [r.model_dump() for r in existing]
    result = DuplicateDetector().find(new, existing)
    print(f"\n  New report: \"{new.description}\" (Zwide)")
    for candidate in result.candidates:
        print(f"\n  {candidate.reference}: score {candidate.score} -> {candidate.label}")
        for reason in candidate.reasons:
            print(f"      - {reason}")
    print(f"\n  Not suggested: {result.excluded}")
    print("    (SAMPLE-D is in another area; SAMPLE-E is a different category)")
    print(f"\n  Originals unchanged: {before == [r.model_dump() for r in existing]}")
    print(f"  {result.advisory_notice}")


def demo6():
    title(6, "Community accountability: municipal claim vs resident dispute")
    print(MOCK_BANNER)
    backend = MockBackendAdapter()
    backend.add_report("EC-1042", reporter_id="27820000999", status="pending_verification",
                       resolution_claim="The burst pipe has been repaired.")
    engine = ConversationEngine(backend)
    converse(engine, "27820000106", [
        "Hi", "1", "3", "EC-1042", "2",
        {"text": "The pipe is still leaking. I took another photograph today.",
         "media": [{"media_id": "demo-photo-2", "mime_type": "image/jpeg"}]},
    ])
    stored = list(backend.verifications.values())[0]["payload"]
    advisory = stored.ai_advisory
    print("\n  AI advisory stored with the resident's response:")
    print(f"    contradiction detected: {advisory.contradiction_detected}")
    print(f"    needs human review:     {advisory.needs_human_review}")
    print(f"    explanation:            {advisory.explanation}")
    print(f"    next step:              {advisory.suggested_next_step}")
    print(f"    changes status:         {advisory.changes_status}")
    print(f"  Report status in the backend is still: {backend.reports['EC-1042']['status']}")

    print("\n  No majority voting: 9 confirmations + 1 dispute with new evidence")
    many = analyze_verification(VerificationRequest(
        municipal_claim="The burst pipe has been repaired.",
        statements=[VerificationStatement(text="Fixed, thank you") for _ in range(9)]
        + [VerificationStatement(text="Not fixed, still leaking", has_new_evidence=True)]))
    print(f"    counts: {many.counts}")
    print(f"    needs human review: {many.needs_human_review}")


def demo7():
    title(7, "Community insights (trend summary)")
    data = json.loads((DATA_DIR / "sample_complaints.json").read_text(encoding="utf-8"))
    print(f"  {data['_notice']}\n")
    detector = DuplicateDetector()
    reports = [ReportForMatching.model_validate(r) for r in data["reports"]]
    clusters = detector.clusters(reports)
    summary = summarize_trends(data["reports"], data["data_label"], as_of=datetime.fromisoformat(data["as_of"]),
                               window_days=30, audience="public", duplicate_clusters=clusters)
    keep = ("data_label", "audience", "total_reports", "by_category", "by_area", "by_status", "unresolved",
            "recurring_issues", "potential_duplicate_clusters", "disputed_resolutions",
            "resolution_claims_awaiting_verification", "change_vs_previous_window", "privacy")
    print("  " + json.dumps({k: summary[k] for k in keep}, indent=2, ensure_ascii=False).replace("\n", "\n  "))


DEMOS = [demo1, demo2, demo3, demo4, demo5, demo6, demo7]

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # emojis on the Windows terminal
    chosen = [int(a) for a in sys.argv[1:] if a.isdigit()]
    for number, demo in enumerate(DEMOS, start=1):
        if not chosen or number in chosen:
            demo()
    print(f"\n{LINE}\nDone. All reference numbers above came from the MOCK backend.\n{LINE}")
