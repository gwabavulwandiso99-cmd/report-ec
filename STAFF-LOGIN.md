# Staff login

Municipal staff log in at `/login` before they can open the dashboard.
Residents never log in: they report through WhatsApp and follow their report
with its reference number.

## How it works

- Staff accounts are kept in `staff_accounts.json` in the project folder.
  **Nothing is stored in the database**, so the database team's tables are
  never touched.
- Passwords are stored as **scrypt hashes**, never as plain text.
- After **5 wrong passwords**, that email is locked for 10 minutes.
- A wrong email and a wrong password give the same message, so nobody can
  find out which emails have accounts.
- Staff stay signed in for **8 hours**. "Log out" ends the session straight away.
- Without logging in, `/dashboard/`, `/reports` and `/me` are blocked. The home
  page and the future WhatsApp webhook stay open, so the bot still works.

## Keep the accounts file off GitHub

Add this line to `.gitignore` (once):

```
staff_accounts.json
```

Each laptop that runs the server needs its own accounts file. On the server
used for the demo, create the accounts there.

## Managing accounts

Run these in the terminal, in the `report-ec` folder, with `(.venv)` showing:

| Task | Command |
|---|---|
| Create an account | `python add_staff.py add` |
| See all accounts | `python add_staff.py list` |
| Give someone a new password | `python add_staff.py reset name@example.org` |
| Remove someone's access | `python add_staff.py remove name@example.org` |

When you create an account, it asks for the email, full name, role and a
password of at least 10 characters. The password doesn't appear on screen
while you type; that's normal.

Give each person their password **privately**, not in a group chat.

## What a real deployment would add

Say this honestly if judges ask:

- Sign-in with existing municipal Microsoft 365 accounts (single sign-on)
- Accounts stored centrally instead of in a file on one server
- Roles that limit what each person sees (for example, a water team only sees water reports)
- Two-step verification
- An audit log of who changed each report and when
- Forcing a new password on first login
