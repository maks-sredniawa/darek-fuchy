import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check  # noqa: E402
import sign  # noqa: E402

KNOWS_ALL = lambda _: True  # noqa: E731

OWN = check.to_fucha(
    {
        "id": "darek",
        "commands": [
            {"id": "say-something", "phrase": "powiedz coś", "action": "speak", "reply": "Co tam, mordo?"},
            {"id": "gate", "phrase": "otwórz bramę", "action": "call", "reply": "Otwieram bramę.", "decoys": ["otwórz", "zamknij", "brama"]},
        ],
    }
)


def fucha(**overrides) -> dict:
    source = {
        "format": 1,
        "id": "pogoda",
        "version": 1,
        "language": "pl",
        "name": "Pogoda",
        "description": "Opis.",
        "author": "MaksForge",
        "profanity": False,
        "permissions": ["internet"],
        "addresses": ["https://api.open-meteo.com/v1/forecast"],
        "sends": ["chosen_city"],
        "commands": [{"id": "jaka-pogoda", "phrase": "jaka pogoda", "action": "weather", "decoys": ["jaka przygoda"]}],
    }
    source.update(overrides)
    return source


def speak(phrase: str, cid: str = "c", **extra) -> dict:
    return {"id": cid, "phrase": phrase, "action": "speak", "reply": "Hej.", **extra}


def kinds(problems) -> list[str]:
    return [p.kind for p in problems]


class ParseTest(unittest.TestCase):
    def read(self, source) -> tuple:
        return check.parse(json.dumps(source).encode())

    def test_reads_a_good_fucha(self):
        source, problems = self.read(fucha())
        self.assertEqual(problems, [])
        self.assertEqual(source["id"], "pogoda")

    def test_a_newer_format_needs_a_newer_darek(self):
        _, problems = self.read({"format": 2, "anything": True})
        self.assertEqual(kinds(problems), ["newer_format"])

    def test_unreadable(self):
        cases = [
            b"\xff\xfe",
            b"[]",
            b"{",
            "﻿{}".encode(),
            json.dumps({"format": 0}).encode(),
            json.dumps({"format": "1"}).encode(),
            json.dumps(fucha(extra=1)).encode(),
            json.dumps(fucha(version=True)).encode(),
            json.dumps(fucha(version=2**31)).encode(),
            json.dumps(fucha(permissions=None)).encode(),
            json.dumps(fucha(commands=[{"id": "a", "phrase": "b"}])).encode(),
            json.dumps(fucha(commands=[{"id": "a", "phrase": "b", "action": "speak", "colour": "red"}])).encode(),
            b'{"format": 1, "format": 1}',
        ]
        for data in cases:
            with self.subTest(data=data):
                self.assertEqual(kinds(check.parse(data)[1]), ["unreadable"])

    def test_optional_lists_default_to_empty(self):
        source = fucha()
        for key in ("permissions", "addresses", "sends"):
            del source[key]
        source["commands"] = [speak("cześć")]
        parsed, problems = self.read(source)
        self.assertEqual(problems, [])
        self.assertEqual(check.refusals(parsed, KNOWS_ALL), [])


class RefusalsTest(unittest.TestCase):
    def test_the_weather_fucha_passes(self):
        self.assertEqual(check.refusals(fucha(), KNOWS_ALL), [])

    def test_language(self):
        self.assertEqual(kinds(check.refusals(fucha(language="en"), KNOWS_ALL)), ["wrong_language"])

    def test_id(self):
        for good in ("a", "0", "pogoda-2", "a" * 64):
            with self.subTest(good=good):
                self.assertEqual(check.refusals(fucha(id=good), KNOWS_ALL), [])
        for bad in ("", "-a", "Pogoda", "po_goda", "pogoda.json", "a" * 65, "ą", "a\n"):
            with self.subTest(bad=bad):
                self.assertEqual(kinds(check.refusals(fucha(id=bad), KNOWS_ALL)), ["bad_id"])

    def test_darek_is_reserved_unless_bundled(self):
        self.assertEqual(kinds(check.refusals(fucha(id="darek"), KNOWS_ALL)), ["reserved_id"])
        self.assertEqual(check.refusals(fucha(id="darek"), KNOWS_ALL, bundled=True), [])

    def test_unknown_list_values(self):
        problems = check.refusals(
            fucha(permissions=["internet", "location"], sends=["chosen_city", "location"], commands=[speak("hej", action="fly")]),
            KNOWS_ALL,
        )
        self.assertEqual(kinds(problems), ["unknown_permission", "unknown_sent_value", "unknown_action"])

    def test_every_action_needs_its_permission(self):
        self.assertEqual(kinds(check.refusals(fucha(permissions=[]), KNOWS_ALL)), ["undeclared"])
        self.assertEqual(check.refusals(fucha(permissions=[], commands=[speak("hej")]), KNOWS_ALL), [])

    def test_own_only_actions(self):
        call = {"id": "g", "phrase": "otwórz garaż", "action": "call", "reply": "Otwieram."}
        update = {"id": "u", "phrase": "zaktualizuj fuchy", "action": "update_fuchy"}
        self.assertEqual(kinds(check.refusals(fucha(permissions=["call"], commands=[call, update]), KNOWS_ALL)), ["own_only", "own_only"])
        self.assertEqual(check.refusals(fucha(permissions=["call"], commands=[call, update]), KNOWS_ALL, bundled=True), [])

    def test_call_without_its_permission_is_refused_twice(self):
        call = {"id": "g", "phrase": "otwórz garaż", "action": "call", "reply": "Otwieram."}
        self.assertEqual(kinds(check.refusals(fucha(permissions=[], commands=[call]), KNOWS_ALL)), ["undeclared", "own_only"])

    def test_addresses(self):
        good = ["https://api.open-meteo.com/v1/forecast", "https://maksforge.com", "https://a.example/", "https://a-b.example/x/y"]
        bad = [
            "http://api.open-meteo.com/v1/forecast",
            "HTTPS://api.open-meteo.com",
            "https://user@api.open-meteo.com/",
            "https://user:pw@api.open-meteo.com/",
            "https://api.open-meteo.com:443/v1",
            "https://api.open-meteo.com:/v1",
            "https://api.open-meteo.com/v1?x=1",
            "https://api.open-meteo.com/v1?",
            "https://api.open-meteo.com/v1#top",
            "https:///v1",
            "https:api.open-meteo.com",
            "https://exa mple.com/",
            "https://under_score.example/",
            "https://ąę.example/",
            "ftp://example.com/",
            "",
        ]
        for address in good:
            with self.subTest(good=address):
                self.assertTrue(check.is_allowed_address(address))
        for address in bad:
            with self.subTest(bad=address):
                self.assertFalse(check.is_allowed_address(address))
        self.assertEqual(kinds(check.refusals(fucha(addresses=["http://x.example"]), KNOWS_ALL)), ["bad_address"])

    def test_speak_needs_a_reply(self):
        for command in (
            {"id": "c", "phrase": "hej", "action": "speak"},
            {"id": "c", "phrase": "hej", "action": "speak", "reply": None},
            {"id": "c", "phrase": "hej", "action": "speak", "reply": "  "},
            {"id": "c", "phrase": "hej", "action": "speak", "reply": "Hej.", "reply_feminine": " "},
        ):
            with self.subTest(command=command):
                self.assertEqual(kinds(check.refusals(fucha(commands=[command]), KNOWS_ALL)), ["no_reply"])
        self.assertEqual(check.refusals(fucha(commands=[speak("hej", reply_feminine="Hej, mała.")]), KNOWS_ALL), [])

    def test_weather_needs_no_reply(self):
        self.assertEqual(check.refusals(fucha(), KNOWS_ALL), [])

    def test_empty_phrase(self):
        self.assertEqual(kinds(check.refusals(fucha(commands=[speak(" ?! ")]), KNOWS_ALL)), ["empty_phrase"])

    def test_cancel_words(self):
        for phrase in ("stop muzyka", "Anuluj!", "no STOP"):
            with self.subTest(phrase=phrase):
                self.assertEqual(kinds(check.refusals(fucha(commands=[speak(phrase)]), KNOWS_ALL)), ["cancel_word"])
        self.assertEqual(check.refusals(fucha(commands=[speak("stopa")]), KNOWS_ALL), [])

    def test_unknown_words_cover_phrases_and_decoys_once_each(self):
        problems = check.refusals(fucha(), lambda w: w not in ("pogoda", "przygoda"))
        self.assertEqual(kinds(problems), ["unknown_words"])
        self.assertIn("pogoda, przygoda", problems[0].message)


class WordsTest(unittest.TestCase):
    def test_normalised(self):
        self.assertEqual(check.normalised("  Jaka, POGODA?! "), "jaka pogoda")
        self.assertEqual(check.normalised("Otwórz\tbramę\n2"), "otwórz bramę 2")
        self.assertEqual(check.normalised("pół_toru"), "półtoru")
        self.assertEqual(check.normalised("a b"), "ab")

    def test_edit_distance(self):
        self.assertEqual(check.edit_distance("", ""), 0)
        self.assertEqual(check.edit_distance("darek", "darek"), 0)
        self.assertEqual(check.edit_distance("darek", "dariak"), 2)
        self.assertEqual(check.edit_distance("pogoda", "przygoda"), 3)
        self.assertEqual(check.edit_distance("kitten", "sitting"), 3)


class ClashTest(unittest.TestCase):
    def candidate(self, *commands) -> check.Fucha:
        return check.to_fucha(fucha(id="nowa", commands=list(commands)))

    def test_the_weather_fucha_clashes_with_nothing_of_darek(self):
        self.assertEqual(check.clashes(check.to_fucha(fucha()), OWN.commands), [])

    def test_identical_and_containing_phrases_hold(self):
        for phrase in ("otwórz bramę", "Otwórz bramę!", "otwórz bramę dwa", "proszę otwórz bramę"):
            with self.subTest(phrase=phrase):
                self.assertIn("holds", kinds(check.clashes(self.candidate(speak(phrase)), OWN.commands)))
        self.assertIn("holds", kinds(check.clashes(self.candidate(speak("powiedz")), OWN.commands)))

    def test_containment_is_by_whole_adjacent_words(self):
        self.assertNotIn("holds", kinds(check.clashes(self.candidate(speak("otwórz szybko bramę")), OWN.commands)))

    def test_a_phrase_within_two_letters_sounds_like(self):
        self.assertIn("sounds_like", kinds(check.clashes(self.candidate(speak("otwórz bramy")), OWN.commands)))

    def test_a_word_near_a_persona_name(self):
        problems = check.clashes(self.candidate(speak("barek pełny", decoys=["maria"])), [])
        self.assertEqual(kinds(problems), ["near_name", "near_name"])

    def test_a_word_near_another_commands_word(self):
        problems = check.clashes(self.candidate(speak("brama wjazdowa")), OWN.commands)
        self.assertIn("near_word", kinds(problems))

    def test_an_equal_word_is_the_same_token(self):
        self.assertEqual(check.clashes(self.candidate(speak("otwórz okno")), OWN.commands), [])

    def test_a_fucha_does_not_clash_with_itself(self):
        mine = self.candidate(speak("jaka pogoda"), speak("jaka pogoda dziś", "d"))
        self.assertEqual(check.clashes(mine, mine.commands), [])


class RepoTest(unittest.TestCase):
    def write(self, root: Path, folder: str, source: dict) -> None:
        (root / folder).mkdir()
        (root / folder / "fucha.json").write_text(json.dumps(source, ensure_ascii=False))

    def test_every_fucha_is_checked_against_darek_and_the_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(root, "pogoda", fucha())
            self.write(root, "brama", fucha(id="brama", commands=[speak("otwórz bramę")]))
            self.write(root, "zla", fucha(id="inna", commands=[speak("powiedz kawał")]))
            (root / "luzem.json").write_text("{}")
            results = check.check_repo(KNOWS_ALL, OWN, root)
        self.assertEqual(results["pogoda"], [])
        self.assertIn("holds", kinds(results["brama"]))
        self.assertEqual(kinds(results["zla"]), ["folder"])
        self.assertEqual(kinds(results["luzem.json"]), ["stray"])

    def test_the_committed_fuchy_pass_everything_but_the_vocabulary(self):
        results = check.check_repo(KNOWS_ALL, OWN)
        self.assertTrue(results)
        self.assertEqual({k: [str(p) for p in v] for k, v in results.items() if v}, {})


class CatalogueTest(unittest.TestCase):
    def test_entry(self):
        self.assertEqual(
            sign.entry(fucha(commands=[speak("a"), speak("b", "b"), {"id": "w", "phrase": "c", "action": "weather"}])),
            {
                "id": "pogoda",
                "version": 1,
                "name": "Pogoda",
                "description": "Opis.",
                "author": "MaksForge",
                "profanity": False,
                "actions": ["speak", "weather"],
                "url": "https://maksforge.com/darek-fuchy/fuchy/pogoda.darek",
            },
        )


if __name__ == "__main__":
    unittest.main()
