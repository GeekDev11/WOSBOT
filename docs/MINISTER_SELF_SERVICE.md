# Minister Reservations — roster-based member booking

The self-service command uses the **existing alliance member roster**. Members do
not need `/register` or individual `/minister_approve` authorization.

## Member guide

1. Run `/minister` and click **Enter Player ID**, or use `/minister player_id:<FID>`.
2. Enter the in-game player ID already listed in the alliance roster.
3. Verify the displayed name and alliance, and confirm it is your character.
4. Select Construction, Research, and/or Troops Training (1–3 types).
5. Choose available UTC time slots and confirm all appointments once.

Open `/minister` with the same FID to reschedule your **own** booking to a
free slot. Members cannot cancel or displace someone else's booking.
Standard times run every 30 minutes, 00:00–23:30 UTC; existing offset mode
is also supported. Appointments can share a time on different dates,
but not overlap on the same day.

## Who owns a FID?

An existing roster Discord link takes priority. If a FID has no linked Discord
account, the **first successful** booking creates an exclusive Discord claim.
Failed/abandoned bookings do not claim the ID. Another account cannot take an
already linked or claimed FID or reschedule another person's reservation.

**Limitation:** a FID from a roster is not cryptographic proof of game account
ownership. First-claim locking does not prevent the first person from entering
someone else's unlinked ID. Alliance administrators should resolve disputed
claims and may use `/minister_claim_transfer player_id:<FID> member:@Member`
after verification. Transfers are recorded in a minister claim audit and
update reminder recipients. If an existing roster Discord link points to
another account, correct that link with existing account admin tools first.

Legacy administrator-created reservations without an associated owner cannot
be moved by an unverified first claimant; an administrator must resolve them.

## Administrator setup

1. Keep the existing minister scheduling channels, slot mode and administrative
   tools, including `/minister_add`.
2. Set a **member-visible** channel using `/minister_reminder_channel`.
3. Set UTC dates and open/close each minister type using `/minister_event_set`.
4. Verify all alliances and members already exist in this server's roster.

Personal reminders mention the booking Discord user **5 minutes before** and
**at appointment start**. No 30-minute reminder. They are mandatory for
confirmed self-service bookings and do not depend on notification roles.
Reminder delivery relies on the bot remaining online and having channel access;
there is a limited recovery window after downtime.

Changing an event date with bookings is intentionally blocked until the old
cycle is archived and cleared; historical records must not be silently lost.

## Release verification

Back up database files; verify the bot loads the new commands, the roster
lookup and FID claims, admin claim correction, multi-event booking,
rescheduling, and actual Discord personal mentions in a test server.
Run the full regression suite and SimCord before publishing.
