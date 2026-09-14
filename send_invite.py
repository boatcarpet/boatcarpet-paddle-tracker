"""
Sunday Paddle Tennis emails.

Three sends, decided automatically by the day it runs:
  - MONDAY    -> sign-up email to the original group only (tier "core")
  - WEDNESDAY -> same invite, to everyone on the list (core + extended)
  - FRIDAY    -> the roster: who's playing, to everyone on the list

Before every send, the Detroit Lions schedule is checked and the paddle start
time is computed from it (see lions_time.py). The computed time is written to
Firebase at paddle/meta so the tracker page shows exactly what the email says.

MODE can be forced from the workflow's "Run workflow" button
(auto / monday / wednesday / friday) for testing on demand.
"""
import os
import re
import json
import smtplib
import datetime
from zoneinfo import ZoneInfo
from email.message import EmailMessage

import firebase_admin
from firebase_admin import credentials, db

from lions_time import paddle_start, next_sunday

DATABASE_URL = "https://wednesday-tennis-tracker-default-rtdb.firebaseio.com"
TRACKER_URL = "https://boatcarpet.github.io/boatcarpet-paddle-tracker/"

# Kevin is the sender, so the group email never really lands in his inbox -
# Gmail files a self-addressed copy into the Sent thread. He gets a short
# send receipt here instead. Blank this out to turn receipts off.
ADMIN_EMAIL = "kevin@meadedistributing.com"

# A deliberately loose check: no spaces, exactly one @, a dot in the domain,
# plain ASCII only. Enough to catch typos and stray characters that make
# Gmail reject the address outright (555 5.5.2).
EMAIL_OK = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def clean_email(raw):
    """Return a tidy address, or None if it can't be used."""
    if not isinstance(raw, str):
        return None
    # Strip whitespace, zero-width characters, and smart quotes that phones add.
    e = raw.strip().strip("'\"‘’“”")
    e = e.replace("​", "").replace("﻿", "").replace(" ", "")
    if not e:
        return None
    if not EMAIL_OK.match(e):
        return None
    return e


def tier_of(person):
    """Everyone is a guest unless explicitly marked as one of the regulars."""
    return "core" if (person or {}).get("tier") == "core" else "extended"


# --- What time is it in Michigan? ---
# GitHub's cron only speaks UTC, so the workflow fires TWICE on each send day:
# 13:17 UTC (9:17am EDT) and 14:17 UTC (9:17am EST). Exactly one of those is
# 9am local, and this guard drops the other. Without it the send would drift to
# 8am when daylight saving ends on the first Sunday in November - mid-season.
ET = ZoneInfo("America/New_York")
now_et = datetime.datetime.now(ET)
today = now_et.date()
SEND_HOUR = 9

# --- Decide which send this is ---
mode = os.environ.get("MODE", "auto").strip().lower()
if mode not in ("monday", "wednesday", "friday"):
    mode = {0: "monday", 2: "wednesday", 4: "friday"}.get(today.weekday(), "monday")

# The guard applies to scheduled runs only - a manual "Run workflow" should
# fire whatever time you press the button.
event = os.environ.get("GITHUB_EVENT_NAME", "")
if event == "schedule" and now_et.hour != SEND_HOUR:
    print("Scheduled run at %s local - not the %d o'clock hour in Michigan, "
          "so this is the daylight-saving twin. Nothing sent."
          % (now_et.strftime("%-I:%M %p %Z"), SEND_HOUR))
    raise SystemExit(0)

print("[%s] Running at %s" % (mode, now_et.strftime("%a %-I:%M %p %Z")))

# --- Connect to Firebase with the service account (bypasses public rules) ---
service_account = json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"])
cred = credentials.Certificate(service_account)
firebase_admin.initialize_app(cred, {"databaseURL": DATABASE_URL})

# --- Mail credentials (read early so the sender can be filtered out below) ---
user = os.environ["SMTP_USER"]
password = os.environ["SMTP_PASS"]

# --- This week's upcoming Sunday ---
sunday = next_sunday(today)
when = sunday.strftime("%A, %B %-d")
week_key = "%d-%d-%d" % (sunday.year, sunday.month, sunday.day)

# --- What time are we playing? Ask the Lions schedule. ---
start_time, start_reason, confident = paddle_start(sunday)
print("[%s] %s -> start %s (%s)%s"
      % (mode, sunday, start_time, start_reason, "" if confident else "  [not confirmed]"))

# Publish it so the tracker page shows the same time this email does.
try:
    db.reference("paddle/meta").update({
        "startTime": start_time,
        "startReason": start_reason,
        "startConfident": bool(confident),
        "startWeek": week_key,
        "startCheckedAt": datetime.datetime.now().isoformat(timespec="seconds"),
    })
except Exception as err:
    # The email is the thing that matters; a failed meta write must not stop it.
    print("[%s] Could not publish start time to Firebase - %s" % (mode, type(err).__name__))

# A line of copy that's honest about how sure we are.
if confident:
    time_line = "Start time is %s (%s)." % (start_time, start_reason)
else:
    time_line = ("Start time is expected to be %s - %s. "
                 "Friday's email will have the confirmed time."
                 % (start_time, start_reason))

# --- Pull the list ---
players = db.reference("paddle/players").get() or {}
contacts = db.reference("paddle/contacts").get() or {}

# --- Who to email ---
# Monday is the original group only. Wednesday and Friday go to everyone.
core_only = (mode == "monday")

recipients = []
skipped = []
for key, contact in contacts.items():
    if key not in players:
        continue
    person = players[key] if isinstance(players[key], dict) else {}
    name = person.get("name", "")
    if core_only and tier_of(person) != "core":
        continue
    raw = (contact or {}).get("email", "") if isinstance(contact, dict) else ""
    if not (raw or "").strip():
        continue
    email = clean_email(raw)
    if not email:
        skipped.append((name, raw))
        continue
    recipients.append((name, email))

# de-duplicate by email
seen = set()
recipients = [(n, e) for (n, e) in recipients if not (e.lower() in seen or seen.add(e.lower()))]

# The sender is dropped from the group send - see ADMIN_EMAIL above.
recipients = [(n, e) for (n, e) in recipients if e.lower() != user.lower()]

# Report anything we had to leave out, so a bad address is visible in the log.
if skipped:
    print("[%s] Skipped %d unusable address(es):" % (mode, len(skipped)))
    for name, raw in skipped:
        print("    %s -> %r" % (name or "(no name)", raw))

if not recipients:
    print("[%s] No matching emails. Nothing to send." % mode)
    raise SystemExit(0)

# --- Message wording per send ---
if mode == "friday":
    in_players, maybe_players = [], []
    for person in players.values():
        if not isinstance(person, dict):
            continue
        st = person.get("status")
        nm = person.get("name", "")
        if st == "in":
            in_players.append(nm)
        elif st == "maybe":
            maybe_players.append(nm)
    in_players.sort(key=lambda s: s.lower())
    maybe_players.sort(key=lambda s: s.lower())

    lines = []
    lines.append("Here's who's playing paddle this Sunday (%s)." % when)
    lines.append(time_line)
    lines.append("")
    lines.append("PLAYING (%d):" % len(in_players))
    for nm in in_players:
        lines.append("  %s" % nm)
    if maybe_players:
        lines.append("")
        lines.append("MAYBE (%d):" % len(maybe_players))
        for nm in maybe_players:
            lines.append("  %s" % nm)
    lines.append("")
    lines.append("Haven't replied yet? There's still room - tap here to add yourself or change your reply:")
    lines.append(TRACKER_URL)
    lines.append("")
    lines.append("See you Sunday!")

    subject = "Sunday Paddle %s - who's playing (%s)" % (start_time, when)
    body = "\n".join(lines) + "\n"

else:
    who = ("the regulars" if mode == "monday" else "everyone on the list")
    lines = []
    lines.append("Paddle this Sunday (%s) at %s." % (when, start_time))
    lines.append(time_line)
    lines.append("")
    lines.append("Tap the link, add your name, say IN / MAYBE / OUT:")
    lines.append(TRACKER_URL)
    lines.append("")
    if mode == "monday":
        lines.append("This one goes to the original group. A second email goes out Wednesday "
                     "to everyone on the list, and Friday's will have the final roster.")
    else:
        lines.append("Friday's email will list who's playing. Feel free to pass this link "
                     "along to anyone you'd like to add - they can sign themselves up.")
    lines.append("")
    lines.append("Want off the list, or prefer a text instead? Just reply and let me know.")

    subject = "Paddle %s - %s" % (when, start_time)
    body = "\n".join(lines) + "\n"
    print("[%s] Sending to %s." % (mode, who))

# --- Send one message per person (so nobody sees anyone else's address) ---

sent = 0
failed = []
with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
    server.login(user, password)
    for name, email in recipients:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = user
        msg["To"] = email
        msg.set_content(body)
        try:
            server.send_message(msg)
        except smtplib.SMTPRecipientsRefused as err:
            # Gmail rejected this one address. Note it and keep going -
            # one bad address must never stop everyone else's email.
            failed.append((name, email, "refused: %s" % err))
            print("[%s] REFUSED %s <%s> - skipping" % (mode, name or "(no name)", email))
            continue
        except smtplib.SMTPException as err:
            failed.append((name, email, "smtp error: %s" % err))
            print("[%s] FAILED %s <%s> - %s" % (mode, name or "(no name)", email, type(err).__name__))
            continue
        sent += 1
        print("[%s] Sent to %s <%s>" % (mode, name or "(no name)", email))

print("Done. [%s] %d email(s) sent for %s." % (mode, sent, when))

# --- Send receipt: a short summary to ADMIN_EMAIL, not to the group ---
if ADMIN_EMAIL:
    receipt = ["%s send for %s." % (mode.capitalize(), when), ""]
    receipt.append("Start time: %s (%s)%s"
                   % (start_time, start_reason, "" if confident else "  <- NOT CONFIRMED"))
    receipt.append("Emails sent: %d of %d" % (sent, len(recipients)))
    if mode == "monday":
        receipt.append("Audience: the original group only")
    else:
        receipt.append("Audience: everyone on the list")
    if skipped:
        receipt.append("")
        receipt.append("Unusable addresses (%d) - fix these in the tracker:" % len(skipped))
        for name, raw in skipped:
            receipt.append("  %s -> %r" % (name or "(no name)", raw))
    if failed:
        receipt.append("")
        receipt.append("Not delivered (%d):" % len(failed))
        for name, email, why in failed:
            receipt.append("  %s <%s> - %s" % (name or "(no name)", email, why))
    if not skipped and not failed:
        receipt.append("")
        receipt.append("No problems - every address on the list went out.")
    receipt.append("")
    receipt.append(TRACKER_URL)

    note = EmailMessage()
    note["Subject"] = "[paddle] %s send - %d email(s) for %s" % (mode, sent, when)
    note["From"] = user
    note["To"] = ADMIN_EMAIL
    note.set_content("\n".join(receipt) + "\n")
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(user, password)
            server.send_message(note)
        print("[%s] Receipt sent to %s" % (mode, ADMIN_EMAIL))
    except smtplib.SMTPException as err:
        # A receipt problem must never mask the real send result.
        print("[%s] Could not send receipt to %s - %s" % (mode, ADMIN_EMAIL, type(err).__name__))

# Everyone who could be reached has been. Problems are listed below,
# but the run still exits clean - the receipt carries the same list by email.
if skipped or failed:
    print("")
    print("--- Needs attention ---")
    for name, raw in skipped:
        print("  Unusable address: %s -> %r" % (name or "(no name)", raw))
    for name, email, why in failed:
        print("  Not delivered: %s <%s> - %s" % (name or "(no name)", email, why))
    print("Fix these in the tracker, then they'll be included next time.")
