"""Talk to the Report EC chatbot in the terminal (mock backend).

Run:  python chat_cli.py            (WhatsApp-style)
      python chat_cli.py --ussd     (USSD-style screens)

Extra commands while chatting (these are for testing only):
  /photo        pretend to send a photo
  /pin          pretend to share a location pin in Zwide
  /fail         make the next submission fail
  /claim EC-1042  create a mock report EC-1042 that "the municipality says is fixed"
  /quit         stop
"""
import sys

from report_ec_ai.chatbot.conversation import ConversationEngine
from report_ec_ai.chatbot.ussd import UssdGateway
from report_ec_ai.integration.backend_adapter import MockBackendAdapter

USER = "27820000777"   # made-up test number


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ussd = "--ussd" in sys.argv
    backend = MockBackendAdapter()
    engine = ConversationEngine(backend)
    gateway = UssdGateway(engine)
    print("Report EC chatbot - MOCK backend (nothing is sent anywhere). Type /quit to stop.\n")

    ussd_inputs = []
    if ussd:
        print(gateway.handle("cli", USER, ""))

    photo_count = 0
    while True:
        try:
            text = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text == "/quit":
            break
        if text == "/fail":
            backend.fail_next_submissions = 1
            print("(next submission will fail)")
            continue
        if text.startswith("/claim"):
            reference = (text.split() + ["EC-1042"])[1].upper()
            backend.add_report(reference, "someone-else", "pending_verification",
                               resolution_claim="The burst pipe has been repaired.")
            print(f"(mock report {reference} now waits for community verification)")
            continue

        if ussd:
            ussd_inputs.append(text)
            screen = gateway.handle("cli", USER, "*".join(ussd_inputs))
            print(f"\n[{len(screen)} chars]\n{screen}")
            if screen.startswith("END"):
                ussd_inputs = []
                print("\n(USSD session ended - press Enter to dial again)")
            continue

        message = {"channel": "whatsapp", "user_id": USER, "text": text}
        if text == "/photo":
            photo_count += 1
            message = {"channel": "whatsapp", "user_id": USER,
                       "media": [{"media_id": f"cli-photo-{photo_count}", "mime_type": "image/jpeg"}]}
        elif text == "/pin":
            message = {"channel": "whatsapp", "user_id": USER,
                       "location": {"latitude": -33.8705, "longitude": 25.5602}}
        reply = engine.handle(message)
        for part in reply.messages:
            print(f"\nBot: {part}")
        print(f"\n   [state: {reply.state}]")


if __name__ == "__main__":
    main()
