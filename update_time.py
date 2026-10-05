"""
Update the Sunday paddle start time from the Detroit Lions schedule.

Writes the computed time to Firebase at paddle/meta so the tracker page shows
it. SENDS NOTHING - the group is told by WhatsApp.
"""
import os
import json
import datetime
from zoneinfo import ZoneInfo

import firebase_admin
from firebase_admin import credentials, db

from lions_time import paddle_start, next_sunday

DATABASE_URL = "https://wednesday-tennis-tracker-default-rtdb.firebaseio.com"
TRACKER_URL = "https://boatcarpet.github.io/boatcarpet-paddle-tracker/"

# Runs every few hours, so a dropped scheduled run heals itself and a late
# Lions flex change gets picked up mid-week. Writing the same value again is
# harmless, so there is no hour to hit and nothing to miss.
ET = ZoneInfo("America/New_York")
now_et = datetime.datetime.now(ET)



      
      

print("Running at %s" % now_et.strftime("%a %-I:%M %p %Z"))

sunday = next_sunday(now_et.date())
week_key = "%d-%d-%d" % (sunday.year, sunday.month, sunday.day)

start_time, start_reason, confident = paddle_start(sunday)
print("%s -> paddle at %s (%s)%s" % (sunday, start_time, start_reason, "" if confident else "   [NOT CONFIRMED]"))

service_account = json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"])
cred = credentials.Certificate(service_account)
firebase_admin.initialize_app(cred, {"databaseURL": DATABASE_URL})

db.reference("paddle/meta").update({
      "startTime": start_time,
      "startReason": start_reason,
      "startConfident": bool(confident),
      "startWeek": week_key,
      "startCheckedAt": datetime.datetime.now().isoformat(timespec="seconds"),
})

print("Written to paddle/meta. The page shows it straight away:")
print(TRACKER_URL)

if not confident:
      print("")
      print("Heads up: the kickoff time isn't locked in yet (the NFL hasn't flexed it),")
      print("so the page shows this as expected rather than final. Worth re-running")
      print("later in the week before you send the WhatsApp.")
  
