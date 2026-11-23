# Minister booking — staged operational setup

This feature is part of a combined pending update with Player Sync history.
**Do not enable it in production until the combined PR passes regression checks.**

## Member experience

Run `/minister` in the designated minister scheduling server. Approved registered
characters are selected automatically, or selected from a menu when there is more than one.
Choose 1–3 types (Construction, Research, Troops Training), then choose each UTC time.
The **Same time for all** switch lets members share one clock time across separate dates.
The last step shows a recap and commits all requested changes together.

Standard time slots are 00:00, 00:30, ..., 23:30 UTC, 48 slots each 30 minutes.

- A member may book an empty slot for their approved player ID.
- A member may move **their own** booking to another empty slot.
- A member may not cancel, replace another player's booking, or modify another FID.
- A member cannot hold overlapping appointments on the same UTC date/time.
- A conflicted multi-event submission changes nothing.
- Administrator-only legacy scheduling commands, menu, archives, and logs remain.

## Administrator setup

1. Configure the usual minister appointment channels and a log server using
   existing Minister Scheduling setup.
2. Set a **member-visible** reminder channel using
   `/minister_reminder_channel channel:#minister-reminders`.
   The bot needs Send Messages and permission to mention users.
3. Confirm each player's identity. Player linking via `/register` alone is **not**
   ownership proof. An authorized alliance admin runs
   `/minister_approve player_id:123456789` after verifying the member.
   The approval is scoped to the guild and linked player ID.
4. Set each occurrence date and open it:
   `/minister_event_set appointment_type:"Research Day" date_utc:2026-12-04 enabled:True`
   Repeat for Construction and Training as needed.
5. Members run `/minister` once to book 1, 2, or all 3 types.

Only global admins can set dates and the reminder channel. Alliance admins can
approve player IDs in their assigned alliance.

**Important:** The legacy schema only stores one active cycle per minister type.
Do not set a new event date over an old cycle. Archive and clear its existing
reservations with the existing admin tools first. The event-date command refuses
to change dates while bookings are present.

If an admin books a player with no linked Discord account, there is no individual
recipient to mention. Link and verify that account before relying on personal
reminders.

## Mandatory personal reminders

The bot sends each booked Discord member a direct `<@user_id>` mention in
the configured member-visible channel:

- 5 minutes before their appointment.
- At the appointment start time.

**There is no 30-minute reminder.** Role notification opt-outs do not disable
these personal appointment alerts. Canceled bookings are excluded, and a moved
booking derives future reminders from the new reserved time.

Reminder delivery is stored in SQLite. Discord sending and database writing
cannot form one atomic transaction, so a crash in the tiny window between those
steps could cause a duplicate on retry; operators should monitor delivery logs.
The scheduler sends reminders within the first two minutes after each due time;
an outage longer than that may prevent timely delivery. This is a known
reliability limitation requiring operational monitoring.

## Player Sync field history

The separate `/sync_history` and `/sync_history_export` admin commands expose
durable per-field changes by permitted alliance, including former/new values.
They do not reconstruct changes from before the audit feature was installed.
Existing Sync Log auto-delete is **off by default**.

## Release checklist

- Confirm SQLite backup and event date migration.
- Resolve any legacy duplicate minister slot entries before enabling member booking.
- Run staged minister/Sync History tests and full CI including SimCord tests.
- Verify bot startup, slash-command synchronization, database persistence,
  member-visible reminder channel and actual mention delivery in a test server.
- Keep automatic release publishing paused until final approval.
