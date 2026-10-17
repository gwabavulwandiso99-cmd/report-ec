"""Create and manage staff logins for the dashboard.

    python add_staff.py add            create a new staff account
    python add_staff.py list           show all accounts
    python add_staff.py reset EMAIL    give someone a new password
    python add_staff.py remove EMAIL   delete an account

Accounts are saved in staff_accounts.json. Keep that file off GitHub.
"""

import getpass
import sys

from auth import hash_password, load_accounts, save_accounts

MIN_PASSWORD_LENGTH = 10


def ask_password():
    while True:
        password = getpass.getpass(f"Password (at least {MIN_PASSWORD_LENGTH} characters, hidden as you type): ")
        if len(password) < MIN_PASSWORD_LENGTH:
            print("Too short, try again.")
            continue
        if getpass.getpass("Type it again: ") != password:
            print("The passwords don't match, try again.")
            continue
        return password


def add():
    accounts = load_accounts()
    email = input("Work email: ").strip().lower()
    if not email or "@" not in email:
        print("That doesn't look like an email address.")
        return
    if email in accounts:
        print("That email already has an account. Use 'reset' to change the password.")
        return

    name = input("Full name (shown on the dashboard): ").strip() or email.split("@")[0]
    role = input("Role (for example Service coordinator): ").strip() or "Municipal staff"
    password = ask_password()

    accounts[email] = {"name": name, "role": role, "password_hash": hash_password(password), "active": True}
    save_accounts(accounts)
    print(f"Account created for {name} ({email}).")


def list_accounts():
    accounts = load_accounts()
    if not accounts:
        print("No accounts yet. Create one with: python add_staff.py add")
        return
    for email, account in sorted(accounts.items()):
        print(f"{email:35} {account['name']:25} {account.get('role', '')}")


def reset(email):
    accounts = load_accounts()
    email = email.strip().lower()
    if email not in accounts:
        print("No account with that email.")
        return
    accounts[email]["password_hash"] = hash_password(ask_password())
    save_accounts(accounts)
    print(f"Password changed for {email}.")


def remove(email):
    accounts = load_accounts()
    email = email.strip().lower()
    if accounts.pop(email, None) is None:
        print("No account with that email.")
        return
    save_accounts(accounts)
    print(f"Removed {email}.")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "add":
        add()
    elif command == "list":
        list_accounts()
    elif command == "reset" and len(sys.argv) > 2:
        reset(sys.argv[2])
    elif command == "remove" and len(sys.argv) > 2:
        remove(sys.argv[2])
    else:
        print(__doc__)
