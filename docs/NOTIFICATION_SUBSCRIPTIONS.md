# Default-on event notification subscriptions

All human members receive configured event role pings by default. Members can opt out or resubscribe using /alerts; choices are preserved across restarts.

## Server setup

1. Create a **dedicated role with no server-wide permissions** for each event you want members to subscribe to, e.g. `Bear Trap Alerts`. Check that channel permission overrides do not give these roles unintended access.
2. Give the bot **Manage Roles**, and put the bot's highest role above every subscription role. Enable **Server Members Intent** under Discord Developer Portal → Bot → Privileged Gateway Intents before restarting; main.py now requests it.
3. Ensure the bot can mention the roles: make them mentionable or grant the bot the required mention permission.
4. A member with Discord **Manage Server** permission runs `/alerts_setup`, selects an existing event (autocomplete), then selects its Discord role.
5. Repeat for other events. Use `Custom` for notifications without a named event type.
6. The bot backfills roles to existing human members after setup and on startup, and assigns roles to new joiners. This may take time in larger servers. Members run `/alerts` to unsubscribe or resubscribe; the panel offers **Unsubscribe from all**.

Members cannot add or remove arbitrary roles; only roles explicitly mapped by server administrators appear. Managed roles, @everyone, and roles carrying server-wide permissions cannot be configured.

## Delivery behavior

- Configuring a subscription role overrides the event's previously configured `mention_type` **only for that event's outgoing reminder**.
- Messages still post to the configured channel even when no one is subscribed.
- The underlying schedule, repeat logic, board updates, templates, and deletion logic remain unchanged.
- Removing a mapping with `/alerts_remove` restores the previously configured mention type. **Review any legacy @everyone settings before removing mappings.**
- Role changes apply to all configured notifications of the matching event type in the server, including multiple instances of the same event.
- The default is subscribed. Discord role membership and explicit opt-out records are persisted. Mappings and opt-outs are saved in `db/beartime.sqlite`; do not delete the database on deployment.
- Discord channel mutes and notification preferences may still suppress user-side alerts.
- Existing templates containing literal `@everyone` or other direct mentions are not rewritten by this feature; administrators should remove such mentions if opting out is expected to suppress every ping.
- Deleted or moved roles may prevent delivery. Reconfigure the mapping if this occurs.

## Commands

| Command | Who | Action |
| --- | --- | --- |
| `/alerts` | Any server member | Manage own subscriptions |
| `/alerts_setup event role` | Member with Manage Server | Configure event subscription role |
| `/alerts_remove event` | Member with Manage Server | Remove role mapping |

## Limitations

This is a default-on, opt-out **ping** feature, not a private delivery or notification delivery guarantee. It does not rewrite the existing notification scheduling implementation or automatically create Discord roles. The bot requires the proper Discord permissions and role hierarchy to add and remove roles.

## Operational note

Because `main.py` now requests privileged Server Members Intent, the application **must have this enabled in the Discord Developer Portal before the next restart**, or Discord may reject its gateway connection. Existing members are enrolled in a rate-limited background pass; consult notification logs for failed assignments. If it is not possible to enable this intent yet, do not restart the updated bot until the configuration is ready.
