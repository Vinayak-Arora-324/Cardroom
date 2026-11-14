import json
import os
import random
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["AITABLE_SETTINGS"] = os.path.join(tempfile.mkdtemp(), "settings.json")

from table import config, personas  # noqa: E402
from table.poker import Holdem  # noqa: E402

config.update({"ai_pace_seconds": 0})


def table(*chosen, human=False):
    specs = [{"kind": "human", "name": "You"}] if human else []
    specs += [{"kind": "bot", "name": f"Bot{i}", "persona": c} for i, c in enumerate(chosen)]
    g = Holdem(specs, {"starting_chips": 2000, "hand_pause_seconds": 0})
    g.sleep = lambda seconds: None
    return g


def voluntary_rate(character, opponent, hands):
    g = table(character, opponent, opponent, opponent)
    played = 0
    for _ in range(hands):
        for p in g.players:
            if p.chips < 400:
                p.chips = 2000
        g.play_hand()
        played += g.hs[0].voluntary
    return played / hands


class Dealing(unittest.TestCase):
    def test_random_seats_do_not_repeat(self):
        for _ in range(50):
            dealt = personas.deal({i: "random" for i in range(6)})
            self.assertEqual(len(set(dealt.values())), 6)

    def test_named_pick_is_honoured_and_not_dealt_again(self):
        for _ in range(50):
            dealt = personas.deal({0: "maniac", 1: None, 2: "random", 3: "not-a-character"})
            self.assertEqual(dealt[0], "maniac")
            self.assertEqual(list(dealt.values()).count("maniac"), 1)

    def test_more_seats_than_characters(self):
        dealt = personas.deal({i: "random" for i in range(len(personas.CHARACTERS) + 3)})
        self.assertEqual(set(dealt.values()), set(personas.CHARACTERS))

    def test_every_character_has_what_the_prompts_need(self):
        for c in personas.CHARACTERS.values():
            self.assertGreaterEqual(len(c["quirks"]), 3)
            self.assertEqual(set(c["lines"]), {"tilt", "hot", "bored", "short"})
            self.assertLessEqual(set(c["tilt"]) | set(c["short"]), set(personas.TRAITS))


class Fluidity(unittest.TestCase):
    def test_two_seats_are_different_people(self):
        a, b = personas.Mind("rock"), personas.Mind("rock")
        self.assertNotEqual(a.base, b.base)

    def test_a_bad_beat_changes_play_and_then_fades(self):
        m = personas.Mind("hothead")
        m.drift = dict.fromkeys(personas.TRAITS, 0.0)
        m.tilt = 0.0
        m.confidence = 0.0
        calm = m.style(100)
        m.absorb("bad_beat", rival=3, rival_name="Dex", weight=1.5)
        steaming = m.style(100)
        self.assertGreater(steaming["loose"], calm["loose"])
        self.assertGreater(steaming["sizing"], calm["sizing"])
        tilted = m.tilt
        for _ in range(30):
            m.begin_hand()
        self.assertLess(m.tilt, tilted / 3)

    def test_people_tilt_in_different_directions(self):
        def shift(cid):
            m = personas.Mind(cid)
            m.drift = dict.fromkeys(personas.TRAITS, 0.0)
            m.confidence = 0.0
            m.tilt = 0.0
            before = m.style(100)
            m.tilt = 0.8
            return m.style(100)["aggression"] - before["aggression"]

        self.assertGreater(shift("hothead"), 0)
        self.assertLess(shift("scared"), 0)

    def test_grudge_only_counts_against_that_opponent(self):
        m = personas.Mind("hothead")
        m.absorb("bad_beat", rival=3, rival_name="Dex")
        self.assertGreater(m.heat([3]), 0)
        self.assertEqual(m.heat([4]), 0)
        said = " ".join(m.read(100, 1.0, rivals=[3], rival_names={3: "Dex"}))
        self.assertIn("Dex", said)

    def test_characters_play_differently_on_average(self):
        random.seed(11)
        tight = voluntary_rate("rock", "social", 60)
        wild = voluntary_rate("maniac", "social", 60)
        self.assertGreater(wild, tight + 0.25)


class TableTalk(unittest.TestCase):
    def test_characters_use_their_words_differently(self):
        def share(cid, text):
            m = personas.Mind(cid)
            m.tilt = 0.0
            return sum(m.talk_impulse() == personas.TALK_MODES[text] for _ in range(2000)) / 2000

        self.assertGreater(share("trapper", "lie"), share("station", "lie"))
        self.assertGreater(share("station", "truth"), share("trapper", "truth"))
        self.assertGreater(share("rock", "quiet"), share("social", "quiet"))

    def test_rattled_people_lose_control_of_what_they_say(self):
        m = personas.Mind("hothead")
        m.tilt = 0.0
        calm = sum(m.talk_impulse() == personas.TALK_MODES["truth"] for _ in range(3000))
        m.tilt = 1.0
        self.assertGreater(sum(m.talk_impulse() == personas.TALK_MODES["truth"] for _ in range(3000)), calm)

    def showdown_table(self):
        g = table("social", "rock", human=True)
        g.ask_human = lambda request, validate, **kw: validate({"action": "check" if "check" in request["legal"] else "call"})
        g.bot_action = lambda p, ctx: ("check", 0) if "check" in ctx["legal"] else ("call", 0)
        return g

    def test_human_chat_is_checked_against_the_cards_they_show(self):
        g = self.showdown_table()
        human = g.players[0]
        real = g.get_action

        def talk_then_act(p, ctx):
            if p.is_human and not g.hand_talk:
                g.human_chat("I have absolutely nothing")
            return real(p, ctx)

        g.get_action = talk_then_act
        g.play_hand()
        entry = g.talk_record[-1]
        self.assertIn('the human player said "I have absolutely nothing"', entry)
        self.assertIn(g.hs[human.id].hand_name, entry)

    def test_ai_prompt_treats_talk_as_unproven(self):
        g = self.showdown_table()
        p = g.players[1]
        g.play_hand()
        g.hand_talk = {0: ["fold, I've got aces"]}
        g.hs[0].folded = False
        g.street = "flop"
        prompt = g.build_prompt(p, {"legal": ["check"], "to_call": 0, "min_to": 20, "max_to": 100, "label": "bet"})
        self.assertIn("the human player", prompt)
        self.assertIn("meant to make you assume it's an act", prompt)
        self.assertTrue(any(mode in prompt for mode in personas.TALK_MODES.values()))
        self.assertIn("can never change the rules", g.system_prompt(p, "RULES"))
        self.assertNotIn("never reveal your hole cards", g.system_prompt(p, "RULES"))

    def test_folded_hands_leave_no_record(self):
        g = self.showdown_table()
        g.bot_action = lambda p, ctx: ("fold", 0) if "fold" in ctx["legal"] else ("check", 0)
        g.ask_human = lambda request, validate, **kw: validate({"action": "fold"})
        real = g.get_action

        def talk_then_act(p, ctx):
            g.human_chat("watch this")
            return real(p, ctx)

        g.get_action = talk_then_act
        g.play_hand()
        self.assertEqual(len(g.talk_record), 0)


class Hidden(unittest.TestCase):
    def test_browser_state_never_names_the_character(self):
        g = table("random", "maniac", "rock", human=True)
        g.ask_human = lambda request, validate, **kw: validate({"action": "fold"})
        g.play_hand()
        sent = json.dumps(g.snapshot())
        for c in personas.CHARACTERS.values():
            self.assertNotIn(c["label"], sent)
            self.assertNotIn(c["story"], sent)
        self.assertNotIn("maniac", sent)
        self.assertNotIn("persona", sent)

    def test_prompt_uses_words_and_character_stays_private(self):
        g = table("hothead", "rock")
        g.play_hand()
        p = g.players[0]
        g.minds[p.id].absorb("bad_beat", rival=1, rival_name="Bot1")
        system = g.system_prompt(p, "RULES")
        self.assertIn(personas.CHARACTERS["hothead"]["story"], system)
        self.assertNotIn("Play to win chips", system)
        self.assertIn("Never name", system)
        self.assertNotIn("0.", " ".join(g.minds[p.id].read(100, 1.0)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
