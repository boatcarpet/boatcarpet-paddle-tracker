"""
Put back players who were taken off the Sunday list by mistake.

Until Oct 16 2026 anyone could tap the x on the public page, and players were
using it to say "not this week" - which deleted them, email and all.

How it finds them: every invite/roster run logs a "Sent to NAME <...>" line for
each person emailed. This reads those lines from every "Sunday paddle invite"
run since SINCE (default: Monday Oct 12), and anyone who was emailed but is no
longer on paddle/players is "missing". Their email is looked up by name on the
pickleball list (pickleball/contacts).

DRY RUN by default: emails Kevin the list and writes nothing.
APPLY=true: adds each missing person back as No reply, with their email if
found. SKIP="Name One, Name Two" leaves those people out.

Reads pickleball/ only; never writes there.
"""
import os
import re
import json
import smtplib
from email.message import EmailMessage

import requests
import firebase_admin
from firebase_admin import credentials, db

DATABASE_URL = "https://wednesday-tennis-tracker-default-rtdb.firebaseio.com"
TRACKER_URL = "https://boatcarpet.github.io/boatcarpet-paddle-tracker/?admin"
ADMIN_EMAIL = "kevin@meadedistributing.com"


def flag(name):
    return os.environ.get(name, "false").strip().lower() in ("true", "1", "yes")


def norm(n):
    return " ".join("".join(c for c in (n or "").lower() if c.isalpha() or c == " ").split())


def masked(e):
    if not e or "@" not in e:
        return "(blank)"
    local, domain = e.split("@", 1)
    return local[:1] + "****@" + domain


APPLY = flag("APPLY")
SINCE = (os.environ.get("SINCE") or "2026-10-12").strip()
SKIP = {norm(s) for s in (os.environ.get("SKIP") or "").split(",") if s.strip()}

# --- 1. Who has been emailed? Read the invite runs' logs. ---
repo = os.environ["GITHUB_REPOSITORY"]
gh = requests.Session()
gh.headers.update({"Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
                   "Accept": "application/vnd.github+json"})
api = "https://api.github.com/repos/" + repo
runs = gh.get(api + "/actions/workflows/paddle-invite.yml/runs",
              params={"created": ">=" + SINCE, "per_page": 100}).json().get("workflow_runs", [])

SENT = re.compile(r"\] Sent to (.+?) <")
emailed = {}          # normalized name -> name as written
for run in runs:
    jobs = gh.get(api + "/actions/runs/%d/jobs" % run["id"]).json().get("jobs", [])
    for job in jobs:
        r = gh.get(api + "/actions/jobs/%d/logs" % job["id"])
        if r.status_code != 200:
            print("Could not read the log for run %d (HTTP %d)" % (run["id"], r.status_code))
            continue
        for m in SENT.finditer(r.text):
            name = m.group(1).strip()
            if name and name != "(no name)":
                emailed.setdefault(norm(name), name)

print("Read %d invite run(s) since %s - %d different people were emailed."
      % (len(runs), SINCE, len(emailed)))

# --- 2. Who is on the list now? ---
cred = credentials.Certificate(json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"]))
firebase_admin.initialize_app(cred, {"databaseURL": DATABASE_URL})

now = db.reference("paddle/players").get() or {}
on_list = {norm(p.get("name")) for p in now.values() if isinstance(p, dict)}

missing = sorted((n for k, n in emailed.items() if k not in on_list and k not in SKIP),
                 key=str.lower)
skipped = sorted((n for k, n in emailed.items() if k not in on_list and k in SKIP),
                 key=str.lower)

# --- 3. Find their emails on the pickleball list ---
pb_players = db.reference("pickleball/players").get() or {}
pb_contacts = db.reference("pickleball/contacts").get() or {}
pb_email = {}
for key, p in pb_players.items():
    if not isinstance(p, dict):
        continue
    c = pb_contacts.get(key) or {}
    e = (c.get("email", "") if isinstance(c, dict) else "").strip()
    if e:
        pb_email.setdefault(norm(p.get("name")), e)

found = [(n, pb_email[norm(n)]) for n in missing if norm(n) in pb_email]
nofind = [n for n in missing if norm(n) not in pb_email]

# Names only in the public log - addresses go in Kevin's email, not here.
print("")
print("On the list now: %d. Emailed since %s but no longer on the list: %d"
      % (len(on_list), SINCE, len(missing)))
for n, e in found:
    print("   %-24s %s" % (n, masked(e)))
for n in nofind:
    print("   %-24s (no email on the pickleball list)" % n)
if skipped:
    print("Left out on purpose (SKIP): " + ", ".join(skipped))

# --- 4. Put them back (APPLY only) ---
added = 0
if APPLY and missing:
    pref = db.reference("paddle/players")
    cref = db.reference("paddle/contacts")
    emails = dict(found)
    for n in missing:
        e = emails.get(n, "")
        ref = pref.push({"name": n, "status": "waiting", "tier": "extended", "hasEmail": bool(e)})
        if e:
            cref.child(ref.key).set({"email": e})
        added += 1
    print("")
    print("Done. Added %d player(s) back as No reply." % added)
elif not APPLY:
    print("")
    print("DRY RUN - nothing was changed.")

# --- 5. Tell Kevin ---
lines = []
if APPLY:
    lines.append("Added %d player(s) back to the Sunday list as No reply." % added)
else:
    lines.append("PREVIEW ONLY - nothing has been changed yet.")
lines.append("")
lines.append("Emailed since %s but no longer on the list: %d" % (SINCE, len(missing)))
lines.append("")
if found:
    lines.append("Email found on the pickleball list (%d):" % len(found))
    for n, e in found:
        lines.append("  %s - %s" % (n, e))
    lines.append("")
if nofind:
    lines.append("No email found - add theirs from the organizer link (%d):" % len(nofind))
    for n in nofind:
        lines.append("  %s" % n)
    lines.append("")
if skipped:
    lines.append("Left out because you listed them in SKIP: " + ", ".join(skipped))
    lines.append("")
if not missing:
    lines.append("Nobody is missing - everyone who was emailed is still on the list.")
    lines.append("")
if not APPLY and missing:
    lines.append("To put them back: Actions -> Restore removed players -> Run workflow,")
    lines.append("set apply to true. Anyone who should stay off, type their name in skip.")
    lines.append("")
lines.append(TRACKER_URL)

msg = EmailMessage()
msg["Subject"] = "[paddle] %s: %d removed player(s)" % ("Restored" if APPLY else "Preview", len(missing))
msg["From"] = os.environ["SMTP_USER"]
msg["To"] = ADMIN_EMAIL
msg.set_content("\n".join(lines) + "\n")
with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
    server.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
    server.send_message(msg)
print("Report emailed to %s" % masked(ADMIN_EMAIL))
