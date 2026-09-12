# Getting the data out

The instrument needs four facts per message: who, where, when, and who they
were answering. Here is how to obtain them, per platform, and what to do
about the one fact that is always missing.

## The fact that is always missing

**Join dates.** No chat export tool produces them, and they are the single
most valuable thing you can get. Without them, "arrival" has to be inferred
from somebody's first message, which excludes everybody who never posted —
and those people are the entire subject of an activation measure. A community
where nine in ten members have never spoken looks identical to a community
where everyone speaks, if all you have is the messages.

With a roster, two of the strongest measures switch on: the share of members
who have never posted, and the share of arrivals who post within a week.

A roster is a CSV of every member, whether or not they have ever said
anything:

```
actor,joined_at,staff,bot
u17,2026-01-04T11:02:00Z,0,0
u3,2025-11-30T08:00:00Z,1,0
```

`actor` has to match the ids in the chat export, which on Discord means user
ids rather than display names. Pass it with `--roster members.csv`.

For a Discord server you administer, the member list with join dates is
available to a bot with the members intent, or through the server's own
insights if it is a Community server above Discord's size threshold. Getting
it is a one-off job and it is worth doing before the first audit rather than
after.

## Discord

Export with **DiscordChatExporter**, which produces the JSON this instrument
reads. The shape of the command is:

```
DiscordChatExporter.Cli exportguild -t <TOKEN> -g <GUILD ID> -f Json -o export/
```

Check `--help` for the exact flags in your version, and note two things.

**Use a bot token, not your own.** Automating a user account is against
Discord's terms of service. Create an application, add its bot to the server
with permission to read message history, and use that token. It is also the
honest answer when a client asks what you connected to their server.

**Export every channel, including the quiet ones.** Graveyard rooms are a
finding, and they are invisible if the export only covers the channels with
traffic. If a channel produces no file at all, list its name in a text file
and pass `--surfaces rooms.txt` so it still appears in the report.

Then:

```
python3 -m vitals audit export/ --staff "<mod name>" --roster members.csv -o report.html
```

Staff are identified automatically from the roles the export records, so
`--staff` is only needed for people whose role is not obviously a staff role.

## Slack

A workspace export from the admin settings produces the directory layout this
instrument reads: `users.json`, `channels.json`, and a folder of daily JSON
files per channel. Point at the directory.

```
python3 -m vitals audit slack-export/ -o report.html
```

Slack is the easier case: it records threads explicitly and it names admins
and owners in `users.json`, so nothing has to be guessed. It still records no
join dates.

## Circle, Discourse, Facebook groups, anything else

Map it to the CSV. One row per thing that happened:

```
timestamp,actor,surface,kind,id,parent_id,addressed,staff,words
2026-01-04T09:12:00Z,u17,general,message,m1,,,0,14
2026-01-04T09:20:00Z,u3,general,message,m2,m1,u17,1,8
2026-01-02T11:00:00Z,u17,,join,,,,0,
```

Only `timestamp` and `actor` are required. `join` rows supply the roster
inline, which is the easiest route when the platform exports members and
messages together. `python3 -m vitals template -o events.csv` writes a
starting point.

If a platform has an API that returns posts with authors and timestamps, the
mapping is usually thirty lines of script. That is the whole reason the
instrument is built around a normalised event rather than around any one
platform.

## What to check before trusting a report

- **Does it say the roster is missing?** The method note at the foot of every
  report lists what could not be measured and why. If the arrival measures
  are absent, get the roster.
- **Are the quiet channels in it?** Compare the room count in the report
  header against the server.
- **Is staff identified?** If `share of posting done by staff` is near zero in
  a server the owner posts in constantly, the owner was not recognised as
  staff. Pass `--staff`.
- **Is the window long enough?** Retention at thirty days needs more than
  thirty days of history, and the trend measures need six weeks. The report
  says when it does not have enough.
