"""
One-time import: copy the pickleball roster into the paddle tracker.

Reads  pickleball/players + pickleball/contacts
Writes paddle/players     + paddle/contacts

What changes on the way across:
  - status is reset to "waiting" (nobody's pickleball reply carries over)
  - the "dinner" flag is dropped (paddle doesn't track it)
  - a "tier" is set on everyone (TIER input: extended or core)
  - names, emails and the hasEmail flag come over untouched

Sends no email and touches nothing under pickleball/ - it is read-only there.

Run it from the Actions tab. It defaults to a DRY RUN that only prints what it
would do, so you can read the list before anything is written.

Inputs (workflow_dispatch):
  APPLY   "false" (default) = dry run. "true" = actually write.
  TIER    "extended" (default) or "core" - what everyone comes in as.
  REPLACE "false" (default) = refuse to run if paddle already has players.
          "true" = wipe the paddle roster first and re-import.
"""
import os
import json

import firebase_admin
from firebase_admin import credentials, db

DATABASE_URL = "https://wednesday-tennis-tracker-default-rtdb.firebaseio.com"


def flag(name, default="false"):
    return os.environ.get(name, default).strip().lower() in ("true", "1", "yes")


APPLY = flag("APPLY")
REPLACE = flag("REPLACE")
TIER = os.environ.get("TIER", "extended").strip().lower()
if TIER not in ("core", "extended"):
    TIER = "extended"

service_account = json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"])
cred = credentials.Certificate(service_account)
firebase_admin.initialize_app(cred, {"databaseURL": DATABASE_URL})

src_players = db.reference("pickleball/players").get() or {}
src_contacts = db.reference("pickleball/contacts").get() or {}
dst_players = db.reference("paddle/players").get() or {}

print("Pickleball roster: %d player(s), %d saved email(s)"
      % (len(src_players), len(src_contacts)))
print("Paddle roster right now: %d player(s)" % len(dst_players))
print("Importing everyone as: %s" % TIER)
print("")

if not src_players:
    print("Nothing to copy - the pickleball roster came back empty.")
    raise SystemExit(0)

# --- Safety: never silently double-import or clobber an edited list ---
if dst_players and not REPLACE:
    print("STOPPING. The paddle roster already has %d player(s)." % len(dst_players))
    print("Importing again would duplicate them. If you meant to start over,")
    print("re-run with REPLACE set to true - that wipes paddle and re-imports.")
    raise SystemExit(1)

# --- Build the new roster ---
new_players, new_contacts = {}, {}
rows = []
for key, person in src_players.items():
    if not isinstance(person, dict):
        continue
    name = person.get("name", "")
    if not name:
        continue
    contact = src_contacts.get(key) or {}
    email = contact.get("email", "") if isinstance(contact, dict) else ""
    email = (email or "").strip()

    new_players[key] = {
        "name": name,
        "status": "waiting",
        "tier": TIER,
        "hasEmail": bool(email),
    }
    if email:
        new_contacts[key] = {"email": email}
    rows.append((name, email))

rows.sort(key=lambda r: r[0].lower())
with_email = sum(1 for _, e in rows if e)

print("Would import %d player(s), %d with an email:" % (len(rows), with_email))
for name, email in rows:
    print("   %-24s %s" % (name, email or "(no email - won't get the auto-send)"))
print("")

if not APPLY:
    print("DRY RUN - nothing was written.")
    print("Happy with the list? Run it again with APPLY set to true.")
    raise SystemExit(0)

# --- Write ---
if dst_players and REPLACE:
    print("REPLACE is on - clearing the existing paddle roster first.")
    db.reference("paddle/players").delete()
    db.reference("paddle/contacts").delete()

db.reference("paddle/players").update(new_players)
if new_contacts:
    db.reference("paddle/contacts").update(new_contacts)

print("Done. %d player(s) imported into the paddle tracker as %s."
      % (len(new_players), TIER))
print("")
print("Next: open the tracker and edit the list - remove anyone who doesn't")
print("play paddle, and mark the long-time regulars so they get Monday's email.")
print("https://boatcarpet.github.io/boatcarpet-paddle-tracker/")
