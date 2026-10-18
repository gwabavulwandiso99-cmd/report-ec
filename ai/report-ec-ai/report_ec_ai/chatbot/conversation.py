"""The Report EC chatbot: a state machine that guides a resident through reporting,
status checks and community verification.

It is channel-independent. WhatsApp, the community app and USSD all send an
IncomingMessage and get a BotReply back. Differences per channel are small:
USSD gets shorter prompts, a menu of common problems and no photo step.

Important rules built into this file:
- A reference number is shown ONLY after the backend confirms the report was saved.
- The same idempotency key is reused on every retry, so tapping "Submit" twice
  (or a webhook retry) cannot create two reports.
- Resident text is data. It is analysed, never executed or obeyed.
- Safety messages never claim that emergency services were alerted.
"""
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from report_ec_ai.chatbot import intents
from report_ec_ai.chatbot.intents import Command
from report_ec_ai.chatbot.prompts import menu, options_only, t
from report_ec_ai.chatbot.session_store import (
    DraftLocation,
    InMemorySessionStore,
    ReportDraft,
    Session,
    SessionStore,
    VerificationDraft,
    masked,
    session_key,
)
from report_ec_ai.chatbot.states import REPORT_STATES, State, check_transition
from report_ec_ai.config import SETTINGS, Settings, places, statuses, taxonomy
from report_ec_ai.integration.backend_adapter import BackendAdapter
from report_ec_ai.integration.schemas import (
    AISuggestion,
    BotReply,
    ComplaintAnalysis,
    IncomingMessage,
    ReportLocation,
    ReportPayload,
    StatusQuery,
    SubmissionResult,
    VerificationPayload,
    VerificationSubmitResult,
)
from report_ec_ai.intelligence.analyzer import ComplaintAnalyzer
from report_ec_ai.intelligence.duplicates import valid_coordinates
from report_ec_ai.intelligence.trends import canonical_status
from report_ec_ai.intelligence.verification import analyze_single_response
from report_ec_ai.language.locations import extract_location, is_dont_know
from report_ec_ai.language.normalization import clean_input

log = logging.getLogger("report_ec_ai.chatbot")

SAST = timezone(timedelta(hours=2), "SAST")   # South Africa has no daylight saving
_SAFE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-_/]{0,39}$")

MAIN_MENU_OPTIONS = ["menu.report", "menu.status", "menu.verify", "menu.help", "menu.language"]
CONFIRM_OPTIONS = ["confirm.submit", "confirm.edit", "confirm.cancel"]
RETRY_OPTIONS = ["retry.try_again", "retry.cancel"]
RESUME_OPTIONS = ["resume.continue", "resume.new"]
VERIFY_OPTIONS = ["verify.option.confirm", "verify.option.dispute", "verify.option.unsure"]
VERIFY_STANCES = ["confirm", "dispute", "unsure"]
EDIT_OPTIONS = {
    "default": ["description", "location", "ward", "evidence", "category", "back"],
    "ussd": ["description", "location", "ward", "category", "back"],
}
USSD_PROBLEMS = ["burst_pipe", "no_water", "no_power", "wires", "pothole", "sewage",
                 "rubbish", "streetlight", "other"]
SAFETY_MESSAGES = {
    "electrical_hazard": "safety.electrical",
    "fire": "safety.fire",
    "open_manhole": "safety.open_manhole",
    "flooding": "safety.flooding",
    "sewage_near_people": "safety.sewage",
    "traffic_risk": "safety.road",
}


@dataclass
class Turn:
    """Everything about the message being handled right now."""
    session: Session
    message: IncomingMessage
    text: str
    replies: list = field(default_factory=list)
    end: bool = False
    submitted_reference: Optional[str] = None
    safety: bool = False

    @property
    def lang(self) -> str:
        return self.session.language or SETTINGS.default_language

    @property
    def channel(self) -> str:
        return self.message.channel

    def say(self, key: str, **values):
        self.replies.append(t(self.lang, key, self.channel, **values))


class ConversationEngine:
    def __init__(self, backend: BackendAdapter, store: Optional[SessionStore] = None,
                 analyzer: Optional[ComplaintAnalyzer] = None,
                 now: Optional[Callable[[], datetime]] = None,
                 settings: Settings = SETTINGS):
        self.backend = backend
        self.store = store if store is not None else InMemorySessionStore()
        self.analyzer = analyzer or ComplaintAnalyzer()
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.settings = settings

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    def handle(self, message) -> BotReply:
        if not isinstance(message, IncomingMessage):
            message = IncomingMessage.model_validate(message)
        key = session_key(message.channel, message.user_id)
        session = self.store.get(key)
        is_new = session is None
        if is_new:
            session = Session(key=key, channel=message.channel, created_at=self.now(), updated_at=self.now())

        # The same WhatsApp message delivered twice is answered once
        if message.message_id:
            if message.message_id in session.seen_message_ids:
                log.info("duplicate message ignored session=%s", masked(key))
                return BotReply(messages=[], state=session.state.value, language=session.language,
                                duplicate_message=True)
            session.seen_message_ids = (session.seen_message_ids + [message.message_id])[
                -self.settings.remembered_message_ids:]

        turn = Turn(session, message, clean_input(message.text))

        if not is_new and self._handle_timeouts(turn):
            return self._finish(turn)

        if len(turn.text) > self.settings.max_message_chars:
            turn.say("too_long", max_chars=self.settings.max_message_chars)
            return self._finish(turn)

        before = session.state
        self._dispatch(turn)
        if session.state != before:
            log.info("session=%s %s -> %s", masked(key), before.value, session.state.value)
        return self._finish(turn)

    def start_ussd_session(self, user_id: str) -> BotReply:
        """A new USSD dial. USSD sessions are short, so an unfinished USSD report is not resumed."""
        key = session_key("ussd", user_id)
        session = self.store.get(key) or Session(key=key, channel="ussd", created_at=self.now())
        session.draft = None
        session.verification = None
        session.previous_states = []
        session.editing = False
        session.state = State.MAIN_MENU if session.language else State.LANGUAGE_SELECTION
        turn = Turn(session, IncomingMessage(channel="ussd", user_id=user_id, text=""), "")
        self._prompt(turn)
        return self._finish(turn)

    def _finish(self, turn: Turn) -> BotReply:
        session = turn.session
        session.updated_at = self.now()
        self.store.save(session)
        return BotReply(
            messages=turn.replies,
            state=session.state.value,
            language=session.language,
            end_session=turn.end,
            submitted_reference=turn.submitted_reference,
            safety_notice=turn.safety,
        )

    def _handle_timeouts(self, turn: Turn) -> bool:
        session = turn.session
        idle = self.now() - session.updated_at
        if session.draft and idle > timedelta(hours=self.settings.draft_ttl_hours):
            session.draft = None
            session.previous_states = []
            session.state = State.MAIN_MENU if session.language else State.LANGUAGE_SELECTION
            return False
        if (session.draft and session.draft.description_original
                and session.state in REPORT_STATES
                and idle > timedelta(minutes=self.settings.session_idle_minutes)):
            # Recovery after an interruption: offer to continue instead of guessing
            session.return_state = session.state
            session.state = State.RESUME_PROMPT
            self._prompt(turn)
            return True
        return False

    # ------------------------------------------------------------------
    # Commands that work in any state
    # ------------------------------------------------------------------
    def _dispatch(self, turn: Turn):
        session = turn.session
        command = None
        if turn.text and not turn.message.media:
            command = intents.detect_command(turn.text)
        if command and not (session.state == State.LANGUAGE_SELECTION and command in (Command.ENGLISH, Command.XHOSA)):
            self._run_command(turn, command)
            return
        handler = getattr(self, "_on_" + session.state.value.lower())
        handler(turn)

    def _run_command(self, turn: Turn, command: Command):
        session = turn.session
        if command == Command.GREETING:
            if session.state in (State.CANCELLED, State.REPORT_SUBMITTED):
                self._go(turn, State.MAIN_MENU)
            self._prompt(turn)
        elif command == Command.HELP:
            turn.say("help")
            self._prompt(turn)
        elif command == Command.CANCEL:
            if session.draft or session.verification:
                session.draft = None
                session.verification = None
                session.previous_states = []
                session.editing = False
                self._go(turn, State.CANCELLED)
                turn.say("cancelled")
                turn.end = turn.channel == "ussd"
            else:
                self._go(turn, State.MAIN_MENU if session.language else State.LANGUAGE_SELECTION)
                self._prompt(turn)
        elif command == Command.RESTART:
            session.draft = None
            session.verification = None
            session.previous_states = []
            session.editing = False
            self._go(turn, State.MAIN_MENU if session.language else State.LANGUAGE_SELECTION)
            self._prompt(turn)
        elif command == Command.MENU:
            session.previous_states = []
            session.editing = False
            self._go(turn, State.MAIN_MENU if session.language else State.LANGUAGE_SELECTION)
            self._prompt(turn)
        elif command == Command.BACK:
            self._back(turn)
        elif command == Command.LANGUAGE:
            if session.state != State.LANGUAGE_SELECTION:
                session.return_state = session.state
            self._go(turn, State.LANGUAGE_SELECTION, remember=False)
            self._prompt(turn)
        elif command in (Command.ENGLISH, Command.XHOSA):
            session.language = "en" if command == Command.ENGLISH else "xh"
            turn.say("language_set")
            self._prompt(turn)

    def _back(self, turn: Turn):
        session = turn.session
        if session.previous_states:
            session.state = session.previous_states.pop()
            if session.state == State.AWAITING_LOCATION and session.draft:
                session.draft.awaiting_area_only = False
            self._prompt(turn)
        else:
            turn.say("back_not_possible")
            self._prompt(turn)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _go(self, turn: Turn, new_state: State, remember: bool = True):
        session = turn.session
        check_transition(session.state, new_state)
        if new_state != session.state:
            if remember and session.state in REPORT_STATES | {State.AWAITING_VERIFICATION_RESPONSE}:
                session.previous_states = (session.previous_states + [session.state])[-10:]
            session.invalid_count = 0
        if new_state in (State.MAIN_MENU, State.CANCELLED, State.REPORT_SUBMITTED):
            session.previous_states = []
        session.state = new_state

    def _invalid(self, turn: Turn):
        turn.session.invalid_count += 1
        turn.say("invalid_choice")
        if turn.session.invalid_count >= self.settings.max_invalid_attempts:
            turn.say("invalid_choice_repeated")
        self._prompt(turn)

    def _menu(self, turn: Turn, header: Optional[str], options: list, **values):
        if header:
            turn.replies.append(menu(turn.lang, header, options, turn.channel, **values))
        else:
            turn.replies.append(options_only(turn.lang, options, turn.channel))

    def _analysis(self, session: Session) -> Optional[ComplaintAnalysis]:
        if session.draft and session.draft.analysis:
            return ComplaintAnalysis.model_validate(session.draft.analysis)
        return None

    def _show_safety(self, turn: Turn, analysis: ComplaintAnalysis, already_shown: set):
        new = [i for i in analysis.priority.safety_indicators
               if i in SAFETY_MESSAGES and i not in already_shown]
        if not new:
            return
        turn.safety = True
        if turn.channel == "ussd":
            turn.say("safety.footer")
        else:
            lines = [t(turn.lang, SAFETY_MESSAGES[i], turn.channel) for i in new]
            lines.append(t(turn.lang, "safety.footer", turn.channel))
            turn.replies.append("\n".join(lines))

    def _category_label(self, turn: Turn, analysis: ComplaintAnalysis) -> str:
        if analysis.category.category == "electricity" and analysis.category.subcategory == "streetlight":
            return t(turn.lang, "category.streetlight", turn.channel)
        return t(turn.lang, taxonomy()["categories"][analysis.category.category]["label_key"], turn.channel)

    def _location_label(self, turn: Turn, draft: ReportDraft) -> str:
        loc = draft.location
        parts = []
        if loc.location_text:
            parts.append(loc.location_text)
        elif loc.area:
            parts.append(loc.area)
        if loc.landmark and not (loc.location_text and loc.landmark_words
                                 and loc.landmark_words.lower() in loc.location_text.lower()):
            label = t(turn.lang, f"landmark.{loc.landmark}", turn.channel)
            parts.append(t(turn.lang, "location.landmark", turn.channel, landmark=label))
        if loc.latitude is not None:
            parts.append(t(turn.lang, "value.pin_shared", turn.channel))
        return ", ".join(parts) or t(turn.lang, "value.not_known", turn.channel)

    def _show_summary(self, turn: Turn):
        draft = turn.session.draft
        analysis = self._analysis(turn.session)
        priority = analysis.priority.priority
        description = draft.description_original
        location = self._location_label(turn, draft)
        if turn.channel == "ussd":
            description = description if len(description) <= 35 else description[:34] + "…"
            location = location if len(location) <= 30 else location[:29] + "…"
        summary = t(
            turn.lang, "summary", turn.channel,
            description=description,
            category=self._category_label(turn, analysis),
            priority=t(turn.lang, f"priority.{priority}" if priority else "priority.unassessed", turn.channel),
            location=location,
            municipality=draft.location.municipality or places()["pilot_municipality"],
            ward=draft.ward or t(turn.lang, "value.not_known", turn.channel),
            photo=t(turn.lang, "value.yes" if draft.evidence else "value.no", turn.channel),
        )
        gap = "\n" if turn.channel == "ussd" else "\n\n"
        turn.replies.append(summary + gap + options_only(turn.lang, CONFIRM_OPTIONS, turn.channel))

    def _prompt(self, turn: Turn):
        """Ask the question that belongs to the current state."""
        session = turn.session
        state = session.state
        draft = session.draft
        if state == State.LANGUAGE_SELECTION:
            turn.say("welcome")
        elif state in (State.MAIN_MENU, State.CANCELLED):
            self._menu(turn, "menu.header", MAIN_MENU_OPTIONS)
        elif state == State.RESUME_PROMPT:
            turn.replies.append(
                t(turn.lang, "resume_prompt", turn.channel, description=draft.description_original)
                + "\n" + options_only(turn.lang, RESUME_OPTIONS, turn.channel))
        elif state == State.AWAITING_DESCRIPTION:
            if turn.channel == "ussd" and not (draft and draft.ussd_typing_other):
                self._menu(turn, "ask_description", [f"ussd.problem.{p}" for p in USSD_PROBLEMS])
            elif turn.channel == "ussd":
                turn.say("ussd.type_problem")
            else:
                turn.say("ask_description")
        elif state == State.AWAITING_CATEGORY_CLARIFICATION:
            keys = [item.get("label_key") or taxonomy()["categories"][item["category"]]["label_key"]
                    for item in taxonomy()["clarification_menu"]]
            self._menu(turn, "clarify_category", keys)
        elif state == State.AWAITING_LOCATION:
            turn.say("location_need_area" if draft and draft.awaiting_area_only else "ask_location")
        elif state == State.AWAITING_WARD:
            turn.say("ask_ward")
        elif state == State.AWAITING_EVIDENCE:
            turn.say("ask_evidence")
        elif state == State.AWAITING_CONFIRMATION:
            self._show_summary(turn)
        elif state == State.EDIT_MENU:
            options = EDIT_OPTIONS["ussd" if turn.channel == "ussd" else "default"]
            self._menu(turn, "edit.header", [f"edit.{o}" for o in options])
        elif state == State.SUBMISSION_FAILED:
            turn.replies.append(t(turn.lang, "submission_failed", turn.channel) + "\n"
                                + options_only(turn.lang, RETRY_OPTIONS, turn.channel))
        elif state == State.REPORT_SUBMITTED:
            turn.say("already_submitted", reference=session.last_reference)
        elif state == State.AWAITING_REFERENCE:
            if session.last_reference and turn.channel != "ussd":
                turn.say("ask_reference_with_last", reference=session.last_reference)
            else:
                turn.say("ask_reference")
        elif state == State.AWAITING_VERIFICATION_REFERENCE:
            turn.say("verify.ask_reference")
        elif state == State.AWAITING_VERIFICATION_RESPONSE:
            v = session.verification
            if turn.channel == "ussd":
                intro = t(turn.lang, "verify.intro", "ussd", reference=v.reference)
            elif v.claim:
                intro = t(turn.lang, "verify.intro", turn.channel, claim=v.claim)
            else:
                intro = t(turn.lang, "verify.intro_no_claim", turn.channel, reference=v.reference)
            turn.replies.append(intro + "\n" + options_only(turn.lang, VERIFY_OPTIONS, turn.channel))
        elif state == State.AWAITING_VERIFICATION_DETAILS:
            turn.say("verify.ask_details")
        elif state == State.VERIFICATION_FAILED:
            turn.replies.append(t(turn.lang, "verify.failed", turn.channel) + "\n"
                                + options_only(turn.lang, RETRY_OPTIONS, turn.channel))

    # ------------------------------------------------------------------
    # One handler per state
    # ------------------------------------------------------------------
    def _on_language_selection(self, turn: Turn):
        session = turn.session
        simple = (turn.text or "").strip().lower()
        choice = intents.parse_choice(turn.text, 2)
        if choice == 1 or simple in intents.COMMAND_WORDS[Command.ENGLISH]:
            session.language = "en"
        elif choice == 2 or simple in intents.COMMAND_WORDS[Command.XHOSA]:
            session.language = "xh"
        else:
            # The welcome text is bilingual, so just show it again
            self._prompt(turn)
            return
        turn.say("language_set")
        target = session.return_state
        session.return_state = None
        if target and target not in (State.LANGUAGE_SELECTION, State.CANCELLED, State.REPORT_SUBMITTED):
            session.state = target
        else:
            self._go(turn, State.MAIN_MENU)
        self._prompt(turn)

    def _on_main_menu(self, turn: Turn):
        session = turn.session
        choice = intents.parse_choice(turn.text, len(MAIN_MENU_OPTIONS))
        if choice == 1:
            self._start_report(turn)
        elif choice == 2:
            self._go(turn, State.AWAITING_REFERENCE)
            self._prompt(turn)
        elif choice == 3:
            self._go(turn, State.AWAITING_VERIFICATION_REFERENCE)
            self._prompt(turn)
        elif choice == 4:
            turn.say("help")
            self._prompt(turn)
        elif choice == 5:
            session.return_state = None
            self._go(turn, State.LANGUAGE_SELECTION, remember=False)
            self._prompt(turn)
        elif turn.text and turn.channel != "ussd" and self._looks_like_complaint(turn.text):
            # Shortcut: the resident typed the problem straight away
            session.draft = ReportDraft()
            self._go(turn, State.AWAITING_DESCRIPTION)
            self._on_awaiting_description(turn)
        else:
            self._invalid(turn)

    def _looks_like_complaint(self, text: str) -> bool:
        analysis = self.analyzer.analyze(text)
        return analysis.category.category != "other" and analysis.category.confidence != "low"

    def _start_report(self, turn: Turn):
        session = turn.session
        if session.draft and session.draft.description_original:
            session.return_state = State.AWAITING_CONFIRMATION if session.draft.location.has_any else State.AWAITING_LOCATION
            self._go(turn, State.RESUME_PROMPT)
        else:
            session.draft = ReportDraft()
            self._go(turn, State.AWAITING_DESCRIPTION)
        self._prompt(turn)

    def _on_resume_prompt(self, turn: Turn):
        session = turn.session
        choice = intents.parse_choice(turn.text, 2)
        if choice == 1 and session.draft:
            target = session.return_state if session.return_state in REPORT_STATES else State.AWAITING_DESCRIPTION
            session.return_state = None
            self._go(turn, target, remember=False)
            self._prompt(turn)
        elif choice in (1, 2):
            session.draft = ReportDraft()
            session.return_state = None
            session.previous_states = []
            self._go(turn, State.AWAITING_DESCRIPTION, remember=False)
            self._prompt(turn)
        else:
            self._invalid(turn)

    # --- Reporting ---------------------------------------------------
    def _on_awaiting_description(self, turn: Turn):
        session = turn.session
        draft = session.draft = session.draft or ReportDraft()
        text = turn.text
        message = turn.message

        self._take_photos(turn, draft)
        if message.location and not text:
            self._take_pin(turn, draft)
            turn.say("location_pin_received")
            self._prompt(turn)
            return
        if message.location:
            self._take_pin(turn, draft)

        source = "resident_text"
        if turn.channel == "ussd" and not draft.ussd_typing_other:
            choice = intents.parse_choice(text, len(USSD_PROBLEMS))
            if choice == len(USSD_PROBLEMS):          # "Other (type it)"
                draft.ussd_typing_other = True
                self._prompt(turn)
                return
            if choice:
                text = t(turn.lang, f"ussd.problem.{USSD_PROBLEMS[choice - 1]}", "whatsapp")
                source = "ussd_menu"
            elif text.isdigit() or not text:
                self._invalid(turn)
                return

        if not text:
            if message.media:
                turn.say("media_without_text")
            else:
                turn.say("description_missing")
            return
        if len(text) > self.settings.max_description_chars:
            turn.say("too_long", max_chars=self.settings.max_description_chars)
            return

        analysis = self.analyzer.analyze(text, session.language)
        words = len(analysis.normalized_text.split())
        meaningless = text.strip().isdigit() or intents.is_yes(text) or words == 0
        if meaningless or (analysis.needs_clarification and words < 3 and not draft.short_description_warned):
            draft.short_description_warned = True
            turn.say("description_too_short")
            return

        previous = self._analysis(session)
        already_shown = set(previous.priority.safety_indicators) if previous else set()
        draft.description_original = text
        draft.description_source = source
        draft.analysis = analysis.model_dump(mode="json")
        draft.resident_category = None
        draft.resident_subcategory = None
        self._merge_location_hints(draft, analysis)
        self._show_safety(turn, analysis, already_shown)

        if analysis.needs_clarification:
            self._go(turn, State.AWAITING_CATEGORY_CLARIFICATION)
            self._prompt(turn)
        else:
            self._after_category(turn)

    def _merge_location_hints(self, draft: ReportDraft, analysis: ComplaintAnalysis):
        hints = analysis.location_hints
        loc = draft.location
        if hints.area and not loc.area:
            loc.area = hints.area
            loc.municipality = hints.municipality
            loc.municipality_source = "recognised_area"
        if hints.landmark and not loc.landmark:
            loc.landmark = hints.landmark
            loc.landmark_words = hints.landmark_words
        if hints.ward and not draft.ward:
            draft.ward = hints.ward

    def _on_awaiting_category_clarification(self, turn: Turn):
        session = turn.session
        draft = session.draft
        items = taxonomy()["clarification_menu"]
        choice = intents.parse_choice(turn.text, len(items))
        if not choice:
            self._invalid(turn)
            return
        item = items[choice - 1]
        draft.resident_category = item["category"]
        draft.resident_subcategory = item["subcategory"]
        previous = self._analysis(session)
        analysis = self.analyzer.analyze(draft.description_original, session.language,
                                         forced_category=item["category"],
                                         forced_subcategory=item["subcategory"])
        draft.analysis = analysis.model_dump(mode="json")
        self._show_safety(turn, analysis, set(previous.priority.safety_indicators) if previous else set())
        self._after_category(turn)

    def _after_category(self, turn: Turn):
        session = turn.session
        draft = session.draft
        if session.editing:
            session.editing = False
            self._go(turn, State.AWAITING_CONFIRMATION, remember=False)
            self._prompt(turn)
            return
        loc = draft.location
        if loc.area or loc.latitude is not None:
            self._after_location(turn)
        else:
            draft.awaiting_area_only = bool(loc.landmark)
            self._go(turn, State.AWAITING_LOCATION)
            self._prompt(turn)

    def _take_pin(self, turn: Turn, draft: ReportDraft):
        point = turn.message.location
        draft.location.latitude = point.latitude
        draft.location.longitude = point.longitude
        if not valid_coordinates(point.latitude, point.longitude):
            draft.location.needs_review = True   # outside the Eastern Cape or (0,0)

    def _take_photos(self, turn: Turn, draft) -> str:
        """Returns 'added', 'unsupported' or 'none'."""
        result = "none"
        for item in turn.message.media:
            if item.mime_type.lower() in self.settings.allowed_photo_types:
                if len(draft.evidence) < self.settings.max_evidence_items:
                    draft.evidence.append(item)
                result = "added"
            elif result == "none":
                result = "unsupported"
        return result

    def _on_awaiting_location(self, turn: Turn):
        session = turn.session
        draft = session.draft
        loc = draft.location
        text = turn.text

        if turn.message.location:
            self._take_pin(turn, draft)
            if text and not intents.is_skip(text):
                loc.location_text = text
            turn.say("location_pin_received")
            self._after_location(turn)
            return

        if not text or is_dont_know(text) or intents.is_skip(text) or text.strip().isdigit():
            draft.location_attempts += 1
            turn.say("location_missing")
            return

        hints = extract_location(text)
        if draft.awaiting_area_only:
            loc.location_text = text
            if hints.area:
                loc.area = hints.area
                loc.municipality = hints.municipality
                loc.municipality_source = "recognised_area"
            else:
                loc.needs_review = True
            if hints.ward and not draft.ward:
                draft.ward = hints.ward
            self._after_location(turn)
            return

        loc.location_text = text
        if hints.area:
            loc.area = hints.area
            loc.municipality = hints.municipality
            loc.municipality_source = "recognised_area"
        elif hints.municipality:
            loc.municipality = hints.municipality
            loc.municipality_source = "resident"
        if hints.landmark:
            loc.landmark = hints.landmark
            loc.landmark_words = hints.landmark_words
        if hints.ward and not draft.ward:
            draft.ward = hints.ward

        if hints.area:
            self._after_location(turn)
        elif hints.landmark and draft.location_attempts < self.settings.max_location_attempts:
            draft.awaiting_area_only = True
            draft.location_attempts += 1
            turn.say("location_need_area")
        elif sum(ch.isalpha() for ch in text) >= 3:
            # Not a place we recognise. Our list is incomplete, so keep it as written.
            loc.needs_review = True
            turn.say("location_accepted_as_written")
            self._after_location(turn)
        else:
            draft.location_attempts += 1
            turn.say("location_missing")

    def _after_location(self, turn: Turn):
        session = turn.session
        draft = session.draft
        draft.awaiting_area_only = False
        if draft.location.location_text:
            # A school or clinic mentioned in the location can change the urgency
            previous = self._analysis(session)
            analysis = self.analyzer.analyze(
                draft.description_original, session.language,
                forced_category=draft.resident_category,
                forced_subcategory=draft.resident_subcategory,
                location_text=draft.location.location_text)
            draft.analysis = analysis.model_dump(mode="json")
            self._show_safety(turn, analysis, set(previous.priority.safety_indicators) if previous else set())
        if not draft.location.municipality:
            draft.location.municipality = places()["pilot_municipality"]
            draft.location.municipality_source = "pilot_default"
        if session.editing:
            session.editing = False
            self._go(turn, State.AWAITING_CONFIRMATION, remember=False)
        elif draft.ward or draft.ward_asked:
            self._after_ward(turn)
            return
        else:
            self._go(turn, State.AWAITING_WARD)
        self._prompt(turn)

    def _on_awaiting_ward(self, turn: Turn):
        session = turn.session
        draft = session.draft
        max_ward = places()["max_ward"]
        ward = intents.parse_ward(turn.text, max_ward)
        if ward:
            draft.ward = ward
        elif intents.is_skip(turn.text) or is_dont_know(turn.text):
            draft.ward = None
        else:
            turn.say("ward_invalid", max_ward=max_ward)
            return
        draft.ward_asked = True
        if session.editing:
            session.editing = False
            self._go(turn, State.AWAITING_CONFIRMATION, remember=False)
            self._prompt(turn)
            return
        self._after_ward(turn)

    def _after_ward(self, turn: Turn):
        draft = turn.session.draft
        draft.ward_asked = True
        if turn.channel == "ussd" or draft.evidence or draft.evidence_asked:
            self._go(turn, State.AWAITING_CONFIRMATION)
        else:
            self._go(turn, State.AWAITING_EVIDENCE)
        self._prompt(turn)

    def _on_awaiting_evidence(self, turn: Turn):
        session = turn.session
        draft = session.draft
        result = self._take_photos(turn, draft)
        if result == "unsupported":
            turn.say("evidence_unsupported_type")
            return
        if result == "added":
            turn.say("evidence_received")
        elif not intents.is_skip(turn.text):
            turn.say("evidence_invalid")
            return
        draft.evidence_asked = True
        session.editing = False
        self._go(turn, State.AWAITING_CONFIRMATION)
        self._prompt(turn)

    def _on_awaiting_confirmation(self, turn: Turn):
        choice = intents.parse_choice(turn.text, len(CONFIRM_OPTIONS))
        if choice == 1 or intents.is_yes(turn.text):
            self._submit(turn)
        elif choice == 2:
            self._go(turn, State.EDIT_MENU)
            self._prompt(turn)
        elif choice == 3:
            self._run_command(turn, Command.CANCEL)
        else:
            self._invalid(turn)

    def _on_edit_menu(self, turn: Turn):
        session = turn.session
        draft = session.draft
        options = EDIT_OPTIONS["ussd" if turn.channel == "ussd" else "default"]
        choice = intents.parse_choice(turn.text, len(options))
        if not choice:
            self._invalid(turn)
            return
        option = options[choice - 1]
        if option == "back":
            self._go(turn, State.AWAITING_CONFIRMATION, remember=False)
            self._prompt(turn)
            return
        session.editing = True
        target = {
            "description": State.AWAITING_DESCRIPTION,
            "location": State.AWAITING_LOCATION,
            "ward": State.AWAITING_WARD,
            "evidence": State.AWAITING_EVIDENCE,
            "category": State.AWAITING_CATEGORY_CLARIFICATION,
        }[option]
        if option == "location":
            draft.location = DraftLocation()
            draft.location_attempts = 0
        if option == "evidence":
            draft.evidence = []
        if option == "description":
            draft.short_description_warned = False
            draft.ussd_typing_other = False
        self._go(turn, target)
        self._prompt(turn)

    def _submit(self, turn: Turn):
        session = turn.session
        draft = session.draft
        if draft.idempotency_key is None:
            # Created once and reused on every retry of this report
            draft.idempotency_key = uuid.uuid4().hex
        key = draft.idempotency_key
        if key in session.submitted:
            session.last_reference = session.submitted[key]
            self._go(turn, State.SUBMITTING)
            self._go(turn, State.REPORT_SUBMITTED)
            turn.say("already_submitted", reference=session.last_reference)
            return

        self._go(turn, State.SUBMITTING)
        payload = self.build_payload(session, turn.channel)
        try:
            result = self.backend.submit_report(payload, turn.message.user_id)
            if not isinstance(result, SubmissionResult):
                result = SubmissionResult.model_validate(result)
        except Exception as error:   # the backend is outside our control: never crash, never pretend
            log.warning("submission failed session=%s error=%s", masked(session.key), type(error).__name__)
            result = SubmissionResult(success=False, error_code="backend_unreachable")

        reference = (result.reference or "").strip() if result.success else ""
        if reference and _SAFE_REFERENCE.match(reference):
            session.submitted[key] = reference
            session.last_reference = reference
            session.draft = None
            self._go(turn, State.REPORT_SUBMITTED)
            turn.say("submitted", reference=reference)
            turn.submitted_reference = reference
            turn.end = turn.channel == "ussd"
        else:
            if result.success:
                log.warning("backend said success but gave no valid reference session=%s", masked(session.key))
            self._go(turn, State.SUBMISSION_FAILED)
            self._prompt(turn)
            turn.end = turn.channel == "ussd"

    def build_payload(self, session: Session, channel: str) -> ReportPayload:
        draft = session.draft
        analysis = self._analysis(session)
        loc = draft.location
        return ReportPayload(
            idempotency_key=draft.idempotency_key,
            channel=channel,
            language=session.language or self.settings.default_language,
            description_original=draft.description_original,
            description_source=draft.description_source,
            resident_selected_category=draft.resident_category,
            location=ReportLocation(
                municipality=loc.municipality, municipality_source=loc.municipality_source,
                area=loc.area, landmark=loc.landmark, location_text=loc.location_text,
                latitude=loc.latitude, longitude=loc.longitude, ward=draft.ward,
                needs_review=loc.needs_review,
            ),
            evidence=draft.evidence,
            ai_suggestion=AISuggestion(
                category=analysis.category.category,
                subcategory=analysis.category.subcategory,
                category_confidence=analysis.category.confidence,
                legacy_category=analysis.category.legacy_category,
                priority=analysis.priority.priority,
                priority_confidence=analysis.priority.confidence,
                legacy_urgency=analysis.priority.legacy_urgency,
                explanation=f"{analysis.category.explanation} {analysis.priority.explanation}".strip(),
                safety_indicators=analysis.priority.safety_indicators,
                needs_human_review=analysis.needs_human_review,
                review_reasons=analysis.review_reasons,
            ),
            created_at=self.now(),
        )

    def _on_submitting(self, turn: Turn):
        # Only reachable if a previous turn crashed half-way; retry safely with the same key
        self._submit(turn)

    def _on_submission_failed(self, turn: Turn):
        choice = intents.parse_choice(turn.text, len(RETRY_OPTIONS))
        if choice == 1 or intents.is_yes(turn.text):
            self._submit(turn)
        elif choice == 2:
            self._run_command(turn, Command.CANCEL)
        else:
            self._invalid(turn)

    def _on_report_submitted(self, turn: Turn):
        # A second tap on "1"/"Submit" must not create a second report
        if intents.parse_choice(turn.text, 1) == 1 or intents.is_yes(turn.text):
            turn.say("already_submitted", reference=turn.session.last_reference)
            return
        # Anything else: back to the main menu (a menu number is used straight away)
        self._go(turn, State.MAIN_MENU)
        if intents.parse_choice(turn.text, len(MAIN_MENU_OPTIONS)):
            self._on_main_menu(turn)
        else:
            self._prompt(turn)

    def _on_cancelled(self, turn: Turn):
        self._go(turn, State.MAIN_MENU)
        if intents.parse_choice(turn.text, len(MAIN_MENU_OPTIONS)):
            self._on_main_menu(turn)
        else:
            self._prompt(turn)

    # --- Status lookup -----------------------------------------------
    def _lookup(self, turn: Turn, purpose: str):
        """Returns (reference, StatusResult) or (reference, None) if the backend failed."""
        session = turn.session
        reference = None
        if purpose == "status" and session.last_reference and intents.parse_choice(turn.text, 1) == 1:
            reference = session.last_reference
        reference = reference or intents.parse_reference(turn.text)
        if not reference:
            return None, None
        try:
            result = self.backend.get_report_status(StatusQuery(
                reference=reference, requester_id=turn.message.user_id,
                channel=turn.channel, purpose=purpose))
        except Exception as error:
            log.warning("status lookup failed session=%s error=%s", masked(session.key), type(error).__name__)
            return reference, None
        return reference, result

    def _status_key(self, turn: Turn, status: Optional[str]) -> str:
        canonical = canonical_status(status or "")
        key = canonical if canonical in statuses()["lifecycle"] else "unknown"
        return key

    def _on_awaiting_reference(self, turn: Turn):
        session = turn.session
        reference, result = self._lookup(turn, "status")
        if reference is None:
            session.invalid_count += 1
            turn.say("reference_invalid")
            return
        if result is None:
            turn.say("status_unavailable")
            self._go(turn, State.MAIN_MENU)
            self._prompt(turn)
            return
        if not result.found or not result.authorized:
            # Same answer for "does not exist" and "not yours", so nothing leaks
            turn.say("status_not_found")
            return
        status = self._status_key(turn, result.status)
        updated = result.updated_at.astimezone(SAST).strftime("%d %b %Y %H:%M") if result.updated_at else "-"
        lines = [t(turn.lang, "status_result", turn.channel,
                   reference=reference,
                   status_label=t(turn.lang, f"status.{status}.label", turn.channel),
                   status_explanation=t(turn.lang, f"status.{status}.explain", turn.channel),
                   updated=updated)]
        if turn.channel != "ussd":
            if result.municipal_note:
                lines.append(t(turn.lang, "status_note", turn.channel, note=result.municipal_note))
            if status in statuses()["verification_open_statuses"]:
                lines.append(t(turn.lang, "status_verify_hint", turn.channel))
        turn.replies.append("\n".join(lines))
        self._go(turn, State.MAIN_MENU)
        if turn.channel == "ussd":
            turn.end = True
        else:
            self._prompt(turn)

    # --- Community verification -------------------------------------
    def _on_awaiting_verification_reference(self, turn: Turn):
        session = turn.session
        reference, result = self._lookup(turn, "verification")
        if reference is None:
            turn.say("reference_invalid")
            return
        if result is None:
            turn.say("status_unavailable")
            self._go(turn, State.MAIN_MENU)
            self._prompt(turn)
            return
        if not result.found or not result.authorized:
            turn.say("status_not_found")
            return
        status = self._status_key(turn, result.status)
        if status not in statuses()["verification_open_statuses"]:
            turn.say("verify.not_open", reference=reference,
                     status_label=t(turn.lang, f"status.{status}.label", turn.channel))
            self._go(turn, State.MAIN_MENU)
            if turn.channel == "ussd":
                turn.end = True
            else:
                self._prompt(turn)
            return
        session.verification = VerificationDraft(reference=reference, claim=result.resolution_claim)
        self._go(turn, State.AWAITING_VERIFICATION_RESPONSE)
        self._prompt(turn)

    def _on_awaiting_verification_response(self, turn: Turn):
        choice = intents.parse_choice(turn.text, len(VERIFY_OPTIONS))
        if not choice:
            self._invalid(turn)
            return
        turn.session.verification.stance = VERIFY_STANCES[choice - 1]
        self._go(turn, State.AWAITING_VERIFICATION_DETAILS)
        self._prompt(turn)

    def _on_awaiting_verification_details(self, turn: Turn):
        v = turn.session.verification
        result = self._take_photos(turn, v)
        if result == "unsupported":
            turn.say("evidence_unsupported_type")
            return
        if turn.text and not intents.is_skip(turn.text):
            v.statement = turn.text
        elif not turn.text and result == "none":
            turn.say("verify.ask_details")
            return
        self._submit_verification(turn)

    def _submit_verification(self, turn: Turn):
        session = turn.session
        v = session.verification
        if v.idempotency_key is None:
            v.idempotency_key = uuid.uuid4().hex
        advisory = analyze_single_response(v.claim, v.statement, v.stance,
                                           has_new_evidence=bool(v.evidence), reference=v.reference)
        payload = VerificationPayload(
            idempotency_key=v.idempotency_key, reference=v.reference, channel=turn.channel,
            language=session.language or self.settings.default_language,
            stance_selected=v.stance, statement_original=v.statement, evidence=v.evidence,
            ai_advisory=advisory, created_at=self.now(),
        )
        try:
            result = self.backend.submit_verification(payload, turn.message.user_id)
            if not isinstance(result, VerificationSubmitResult):
                result = VerificationSubmitResult.model_validate(result)
        except Exception as error:
            log.warning("verification failed session=%s error=%s", masked(session.key), type(error).__name__)
            result = VerificationSubmitResult(success=False, error_code="backend_unreachable")

        if result.success:
            turn.say("verify.recorded", reference=v.reference)
            session.verification = None
            self._go(turn, State.MAIN_MENU)
            if turn.channel == "ussd":
                turn.end = True
            else:
                self._prompt(turn)
        else:
            self._go(turn, State.VERIFICATION_FAILED)
            self._prompt(turn)

    def _on_verification_failed(self, turn: Turn):
        choice = intents.parse_choice(turn.text, len(RETRY_OPTIONS))
        if choice == 1 or intents.is_yes(turn.text):
            self._submit_verification(turn)
        elif choice == 2:
            self._run_command(turn, Command.CANCEL)
        else:
            self._invalid(turn)
