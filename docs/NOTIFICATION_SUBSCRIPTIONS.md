# Self-service event notification subscriptions

Members can opt in or out of event pings without administrator permissions.

## Server setup

1. Create a **dedicated role with no server-wide permissions** for each event you want members to subscribe to, e.g. `Bear Trap Alerts`. Check that channel permission overrides do not give these roles unintended access.
2. Give the bot **Manage Roles**, and put the bot's highest role above every subscription role.
3. Ensure the bot can mention the roles: make them mentionable or grant the bot the required mention permission.
4. A member with Discord **Manage Server** permission runs `/alerts_setup`, selects an existing event (autocomplete), then selects its Discord role.
5. Repeat for other events. Use `Custom` for notifications without a named event type.
6. Members run `/alerts` and select events to subscribe or unsubscribe. The panel also offers **Unsubscribe from all**.

Members cannot add or remove arbitrary roles; only roles explicitly mapped by server administrators appear. Managed roles, @everyone, and roles carrying server-wide permissions cannot be configured.

## Delivery behavior

- Configuring a subscription role overrides the event's previously configured `mention_type` **only for that event's outgoing reminder**.
- Messages still post to the configured channel even when no one is subscribed.
- The underlying schedule, repeat logic, board updates, templates, and deletion logic remain unchanged.
- Removing a mapping with `/alerts_remove` restores the previously configured mention type. **Review any legacy @everyone settings before removing mappings.**
- Role changes apply to all configured notifications of the matching event type in the server, including multiple instances of the same event.
- The preference is represented by Discord role membership, so it persists across bot restarts. Mappings are saved in `db/beartime.sqlite`.
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

This is an opt-in **ping** feature, not a private delivery or notification delivery guarantee. It does not rewrite the existing notification scheduling implementation or automatically create Discord roles. The bot requires the proper Discord permissions and role hierarchy to add and remove roles.
