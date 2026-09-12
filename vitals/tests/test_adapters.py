"""Getting real exports in, and failing legibly when they are not what we think."""

import json
import os
import tempfile
import unittest

from vitals import adapters
from vitals.adapters import common


def write(text, name="events.csv"):
    folder = tempfile.mkdtemp(prefix="vitals-")
    path = os.path.join(folder, name)
    with open(path, "w") as fh:
        fh.write(text)
    return path


CSV = """timestamp,actor,surface,kind,id,parent_id,addressed,staff,words
2026-01-02T11:00:00Z,dana,,join,,,,1,
2026-01-02T11:30:00Z,sam,,join,,,,0,
2026-01-04T09:12:00Z,sam,general,message,m1,,,0,14
2026-01-04T09:20:00Z,dana,general,message,m2,m1,sam,1,8
2026-01-11T18:02:00Z,sam,general,message,m3,,,0,21
"""

DISCORD = {
    "guild": {"name": "Test Guild"},
    "channel": {"name": "general", "category": "Text Channels"},
    "messages": [
        {
            "id": "1", "type": "Default", "timestamp": "2026-01-04T09:12:00.000+00:00",
            "content": "hello everyone", "author": {
                "id": "u1", "name": "sam", "isBot": False,
                "roles": [{"name": "Member"}],
            },
            "mentions": [], "reactions": [],
        },
        {
            "id": "2", "type": "Default", "timestamp": "2026-01-04T09:20:00.000+00:00",
            "content": "hi sam", "author": {
                "id": "u2", "name": "dana", "isBot": False,
                "roles": [{"name": "Moderator"}],
            },
            "mentions": [{"id": "u1", "name": "sam"}],
            "reference": {"messageId": "1"},
            "reactions": [{"emoji": {"name": "wave"}, "count": 1,
                           "users": [{"id": "u1", "name": "sam"}]}],
        },
        {
            "id": "3", "type": "GuildMemberJoin",
            "timestamp": "2026-01-03T08:00:00.000+00:00",
            "content": "", "author": {"id": "u3", "name": "lee", "isBot": False},
        },
    ],
}


class Csv(unittest.TestCase):
    def test_a_csv_of_events_loads(self):
        c = adapters.load(write(CSV))
        self.assertEqual(len(c.messages), 3)
        self.assertTrue(c.has_roster)
        self.assertEqual(c.staff(), {"dana"})

    def test_join_rows_become_the_roster(self):
        c = adapters.load(write(CSV))
        self.assertIsNotNone(c.members["sam"].joined_at)

    def test_a_silent_room_can_be_declared(self):
        rooms = write("general\nattic\n", name="rooms.txt")
        c = adapters.load(write(CSV), surfaces_file=rooms)
        self.assertIn("attic", c.surfaces)

    def test_a_missing_column_is_a_sentence_not_a_traceback(self):
        with self.assertRaises(ValueError) as caught:
            adapters.load(write("who,when\nsam,2026-01-01\n"))
        self.assertIn("missing the column", str(caught.exception))

    def test_an_unknown_kind_names_the_row(self):
        bad = CSV.replace("message,m1", "shouted,m1")
        with self.assertRaises(ValueError) as caught:
            adapters.load(write(bad))
        self.assertIn("kind", str(caught.exception))

    def test_an_empty_file_says_so(self):
        header = CSV.splitlines()[0] + "\n"
        with self.assertRaises(ValueError) as caught:
            adapters.load(write(header))
        self.assertIn("no usable rows", str(caught.exception))


class Discord(unittest.TestCase):
    def setUp(self):
        self.path = write(json.dumps(DISCORD), name="general.json")

    def test_a_channel_export_loads(self):
        c = adapters.load(self.path, fmt="discord")
        self.assertEqual(c.name, "Test Guild")
        self.assertEqual(c.surfaces, ["Text Channels/general"])
        self.assertEqual(len(c.messages), 2)

    def test_roles_identify_staff_without_being_told(self):
        c = adapters.load(self.path, fmt="discord")
        self.assertEqual(c.staff(), {"u2"})

    def test_a_name_can_be_marked_as_staff_by_hand(self):
        c = adapters.load(self.path, fmt="discord", staff=("sam",))
        self.assertIn("u1", c.staff())

    def test_a_reply_and_a_mention_survive_the_import(self):
        c = adapters.load(self.path, fmt="discord")
        reply = [e for e in c.messages if e.id == "2"][0]
        self.assertEqual(reply.parent_id, "1")
        self.assertEqual(reply.addressed, ("u1",))

    def test_reactions_come_through_as_their_own_events(self):
        c = adapters.load(self.path, fmt="discord")
        reactions = [e for e in c.events if e.kind == "reaction"]
        self.assertEqual(len(reactions), 1)
        self.assertEqual(reactions[0].actor, "u1")

    def test_a_join_notice_becomes_a_roster_entry(self):
        c = adapters.load(self.path, fmt="discord")
        self.assertIsNotNone(c.members["u3"].joined_at)

    def test_no_message_text_is_kept(self):
        c = adapters.load(self.path, fmt="discord")
        blob = repr(c.events)
        self.assertNotIn("hello everyone", blob)
        self.assertEqual([e.words for e in c.messages], [2, 2])

    def test_the_wrong_kind_of_json_is_a_sentence(self):
        path = write(json.dumps({"hello": "world"}), name="wrong.json")
        with self.assertRaises(ValueError) as caught:
            adapters.load(path, fmt="discord")
        self.assertIn("DiscordChatExporter", str(caught.exception))

    def test_broken_json_names_the_file(self):
        path = write("{not json", name="broken.json")
        with self.assertRaises(ValueError) as caught:
            adapters.load(path, fmt="discord")
        self.assertIn("not valid JSON", str(caught.exception))


class Slack(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="vitals-slack-")
        with open(os.path.join(self.root, "users.json"), "w") as fh:
            json.dump([
                {"id": "U1", "name": "sam", "is_admin": False},
                {"id": "U2", "name": "dana", "is_admin": True},
            ], fh)
        with open(os.path.join(self.root, "channels.json"), "w") as fh:
            json.dump([{"name": "general"}, {"name": "attic"}], fh)
        os.makedirs(os.path.join(self.root, "general"))
        with open(os.path.join(self.root, "general", "2026-01-04.json"), "w") as fh:
            json.dump([
                {"type": "message", "user": "U1", "ts": "1767517920.000100",
                 "text": "hello"},
                {"type": "message", "user": "U2", "ts": "1767518400.000200",
                 "text": "hi <@U1>", "thread_ts": "1767517920.000100",
                 "reactions": [{"name": "wave", "users": ["U1"], "count": 1}]},
                {"type": "message", "subtype": "channel_join", "user": "U1",
                 "ts": "1767517900.000100", "text": "joined"},
            ], fh)

    def test_a_workspace_export_loads(self):
        c = adapters.load(self.root)
        self.assertEqual(c.platform, "slack")
        self.assertEqual(len(c.messages), 2)
        self.assertEqual(c.staff(), {"U2"})

    def test_threads_and_mentions_survive(self):
        c = adapters.load(self.root)
        reply = [e for e in c.messages if e.actor == "U2"][0]
        self.assertEqual(reply.parent_id, "1767517920.000100")
        self.assertIn("U1", reply.addressed)

    def test_declared_but_silent_rooms_are_kept(self):
        c = adapters.load(self.root)
        self.assertIn("attic", c.surfaces)

    def test_the_format_is_detected(self):
        self.assertEqual(adapters.detect(self.root), "slack")


class Detection(unittest.TestCase):
    def test_a_csv_is_recognised(self):
        self.assertEqual(adapters.detect(write(CSV)), "csv")

    def test_an_unknown_extension_asks_to_be_told(self):
        with self.assertRaises(ValueError) as caught:
            adapters.detect(write("x", name="notes.txt"))
        self.assertIn("--format", str(caught.exception))

    def test_an_unknown_format_lists_the_real_ones(self):
        with self.assertRaises(ValueError) as caught:
            adapters.load(write(CSV), fmt="telepathy")
        self.assertIn("csv", str(caught.exception))


class Timestamps(unittest.TestCase):
    def test_every_shape_a_platform_might_write(self):
        for value in ("2026-01-02T03:04:05.123+00:00", "2026-01-02T03:04:05Z",
                      "2026-01-02 03:04:05", "2026-01-02", 1767322222,
                      "1767322222.000100"):
            with self.subTest(value=value):
                self.assertIsNotNone(common.parse_time(value).tzinfo)

    def test_nonsense_is_an_error_with_the_value_in_it(self):
        with self.assertRaises(ValueError) as caught:
            common.parse_time("last tuesday")
        self.assertIn("last tuesday", str(caught.exception))


if __name__ == "__main__":
    unittest.main()


class Roster(unittest.TestCase):
    """Join dates, merged in from wherever they could be found."""

    def setUp(self):
        from vitals import roster

        self.roster = roster
        self.community = adapters.load(write(json.dumps(DISCORD),
                                             name="general.json"), fmt="discord")

    def test_a_roster_switches_the_arrival_measures_on(self):
        from vitals.interactions import derive
        from vitals.metrics import measure

        before = measure(self.community, derive(self.community))
        self.assertFalse(before["activation"].measured)

        path = write("actor,joined_at,staff,bot\n"
                     "u1,2026-01-01T00:00:00Z,0,0\n"
                     "u2,2025-12-01T00:00:00Z,1,0\n"
                     "u9,2026-01-02T00:00:00Z,0,0\n", name="roster.csv")
        merged = self.roster.merge(self.community, path)
        self.assertTrue(merged.has_roster)
        after = measure(merged, derive(merged))
        self.assertTrue(after["never_spoke"].measured)
        # u9 never said a word and is now visible
        self.assertGreater(after["never_spoke"].value, 0.0)

    def test_members_who_never_posted_join_the_timeline(self):
        path = write("actor,joined_at\nu9,2026-01-02T00:00:00Z\n", name="r.csv")
        merged = self.roster.merge(self.community, path)
        self.assertIn("u9", merged.members)
        self.assertTrue(any(e.actor == "u9" for e in merged.events))

    def test_a_roster_without_the_columns_says_which(self):
        path = write("user,when\nu1,2026-01-01\n", name="bad.csv")
        with self.assertRaises(ValueError) as caught:
            self.roster.merge(self.community, path)
        self.assertIn("joined_at", str(caught.exception))

    def test_an_empty_roster_is_refused(self):
        path = write("actor,joined_at\n", name="empty.csv")
        with self.assertRaises(ValueError) as caught:
            self.roster.merge(self.community, path)
        self.assertIn("no usable rows", str(caught.exception))
