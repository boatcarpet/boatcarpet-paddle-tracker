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
  REPLACE "false" (default) = if paddle already has players, DON'T import -
          instead copy pickleball emails onto paddle players matched by
          name (only fills blanks; adds and removes nobody).
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
print("")

if not src_players:
    print("Nothing to copy - the pickleball roster came back empty.")
    raise SystemExit(0)

# --- Paddle list already built: copy EMAILS only, matched by name ---
# Adds nobody and removes nobody. Only fills in paddle players who don't have
# an email yet; an email already on the paddle list is never overwritten.
if dst_players and not REPLACE:
    def norm(n):
        return " ".join("".join(c for c in (n or "").lower() if c.isalpha() or c == " ").split())

    dst_contacts = db.reference("paddle/contacts").get() or {}

    pb_email = {}
    for key, person in src_players.items():
        if not isinstance(person, dict):
            continue
        c = src_contacts.get(key) or {}
        e = (c.get("email", "") if isinstance(c, dict) else "").strip()
        if e:
            pb_email.setdefault(norm(person.get("name")), e)

    copy, have, none, missing = [], [], [], []
    for key, person in dst_players.items():
        if not isinstance(person, dict):
            continue
        name = person.get("name", "")
        existing = dst_contacts.get(key) or {}
        if isinstance(existing, dict) and (existing.get("email") or "").strip():
            have.append(name)
            continue
        n = norm(name)
        if n in pb_email:
            copy.append((key, name, pb_email[n]))
        elif any(norm(p.get("name")) == n for p in src_players.values() if isinstance(p, dict)):
            none.append(name)
        else:
            missing.append(name)

    srt = lambda xs: sorted(xs, key=lambda x: (x[1] if isinstance(x, tuple) else x).lower())
    print("EMAIL COPY - the paddle list already has %d player(s); nobody is added or removed."
          % len(dst_players))
    print("")
    print("Will copy an email for %d player(s):" % len(copy))
    for _, name, e in srt(copy):
        print("   %-24s %s" % (name, e))
    if have:
        print("")
        print("Already have an email on the paddle list (left alone): %d" % len(have))
        for name in srt(have):
            print("   %s" % name)
    if none:
        print("")
        print("On the pickleball list but no email there either (%d):" % len(none))
        for name in srt(none):
            print("   %s" % name)
    if missing:
        print("")
        print("Not on the pickleball list - add their email from the organizer link (%d):" % len(missing))
        for name in srt(missing):
            print("   %s" % name)
    print("")

    if not APPLY:
        print("DRY RUN - nothing was written.")
        print("Happy with the list? Run it again with APPLY set to true.")
        raise SystemExit(0)

    upd_c, upd_p = {}, {}
    for key, _, e in copy:
        upd_c[key] = {"email": e}
        upd_p[key + "/hasEmail"] = True
    if upd_c:
        db.reference("paddle/contacts").update(upd_c)
        db.reference("paddle/players").update(upd_p)
    print("Done. Copied %d email(s) onto the paddle list." % len(upd_c))
    raise SystemExit(0)

# --- Build the new roster ---
print("Importing everyone as: %s" % TIER)
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
