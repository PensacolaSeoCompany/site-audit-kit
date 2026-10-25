#!/usr/bin/env python3
"""Keyword variation rules (onpage/variations.py). Run: python3 tests/test_onpage_variations.py (no network)."""
import os, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'onpage'))
import variations as V  # noqa: E402
from textutil import tokens  # noqa: E402


class Core(unittest.TestCase):
    def test_runs_without_filler_edges(self):
        self.assertEqual(V.build('stump removal near me'), ['stump removal near me', 'stump removal', 'stump', 'removal'])

    def test_plural_kept_only_when_on_a_page(self):
        self.assertIn('stumps', V.build('stump removal', page_tokens=[tokens('we grind stumps')]))
        self.assertNotIn('stumps', V.build('stump removal', page_tokens=[tokens('we grind stump roots')]))

    def test_extra_phrases_added(self):
        self.assertEqual(V.build('stump', ['Tree Stump'])[-1], 'tree stump')


class FalseSingulars(unittest.TestCase):
    """"news" is not the plural of "new" and "new" must not gain an "s"."""
    def test_news_is_not_new(self):
        pt = [tokens('the new reporter and the news reporters')]
        got = V.build('news reporter', page_tokens=pt)
        self.assertNotIn('new reporter', got)
        self.assertNotIn('new', got)
        self.assertIn('news reporters', got)

    def test_new_does_not_become_news(self):
        got = V.build('new roof', page_tokens=[tokens('roof news and new roofs')])
        self.assertNotIn('news', got)
        self.assertNotIn('news roof', got)
        self.assertIn('new roofs', got)

    def test_us_is_endings_not_stripped(self):
        for w, bad in (('campus', 'campu'), ('bonus', 'bonu'), ('analysis', 'analysi'), ('glass', 'glas')):
            got = V.build(w + ' repair', page_tokens=[tokens(f'{bad} repair {bad} and {w}')])
            self.assertNotIn(bad, got, w)

    def test_es_singular_ending_in_e(self):
        # houses -> house (not "hous"); the form is kept only when a page has it
        self.assertEqual(V.build('nurses near me', page_tokens=[tokens('find a nurse today')]), ['nurses near me', 'nurses', 'nurse'])
        self.assertNotIn('hous', V.build('tiny houses', page_tokens=[tokens('one house')]))
        self.assertIn('tiny house', V.build('tiny houses', page_tokens=[tokens('a tiny house')]))

    def test_inflection_branches(self):
        # each branch of _inflect runs at least once
        self.assertEqual(V._inflect('companies'), {'companies', 'company'})
        self.assertEqual(V._inflect('boxes'), {'boxes', 'box', 'boxe'})   # 'boxe' is dropped later: no page has it
        self.assertEqual(V._inflect('caches'), {'caches', 'cach', 'cache'})
        self.assertEqual(V._inflect('houses'), {'houses', 'hous', 'house'})
        self.assertEqual(V._inflect('nurses'), {'nurses', 'nurs', 'nurse'})
        self.assertEqual(V._inflect('plumbers'), {'plumbers', 'plumber'})
        self.assertEqual(V._inflect('agency'), {'agency', 'agencies'})
        self.assertEqual(V._inflect('box'), {'box', 'boxes'})
        self.assertEqual(V._inflect('church'), {'church', 'churches'})
        self.assertEqual(V._inflect('attorney'), {'attorney', 'attorneys'})
        self.assertEqual(V._inflect('glass'), {'glass'})
        self.assertEqual(V._inflect('new'), {'new'})
        self.assertEqual(V._inflect('ox'), {'ox'})
        self.assertEqual(V._inflect('ties'), {'ties', 'tie'})
        self.assertEqual(V._inflect('flies'), {'flies', 'fly'})

    def test_leading_filler_dropped(self):
        self.assertEqual(V.build('plumber in dallas'), ['plumber in dallas', 'plumber', 'dallas'])
        # a run that starts with a place and ends with a filler word is dropped too
        self.assertNotIn('los angeles near', V.build('lawyer los angeles near me'))

    def test_word_next_to_a_place_is_inflected(self):
        got = V.build('dentist san diego', page_tokens=[tokens('top dentists san diego')])
        self.assertIn('dentists san diego', got)


class PlaceNames(unittest.TestCase):
    """a multi-word place stays whole: no "los", "angeles" or "lawyer los"."""
    def test_los_angeles_whole(self):
        got = V.build('lawyer los angeles')
        self.assertEqual(got, ['lawyer los angeles', 'lawyer', 'los angeles'])

    def test_three_word_place(self):
        got = V.build('roofing salt lake city')
        self.assertIn('salt lake city', got)
        for bad in ('salt', 'lake', 'city', 'salt lake', 'lake city', 'roofing salt'):
            self.assertNotIn(bad, got)

    def test_known_new_places_only(self):
        self.assertIn('new york', V.build('plumber new york'))
        self.assertNotIn('york', V.build('plumber new york'))
        # "new" before an ordinary word is not a place: "roof" stays its own variation
        self.assertIn('roof', V.build('new roof installation'))

    def test_place_not_inflected(self):
        got = V.build('dentist san diego', page_tokens=[tokens('san diegos dentists')])
        self.assertNotIn('san diegos', got)
        self.assertIn('dentists', got)



class ShortWords(unittest.TestCase):
    """A 1-2 letter word ("tx", "ac", "ny") is not a variation on its own: every state code or "AC" on a page would count."""

    def test_state_code_dropped(self):
        got = V.build('plumber austin tx')
        self.assertNotIn('tx', got)
        for keep in ('plumber austin tx', 'austin tx', 'plumber austin', 'plumber', 'austin'):
            self.assertIn(keep, got)

    def test_ac_dropped_but_phrases_kept(self):
        got = V.build('ac repair phoenix')
        self.assertNotIn('ac', got)
        self.assertIn('ac repair', got)
        self.assertIn('repair', got)

    def test_whole_keyword_kept(self):
        self.assertEqual(V.build('tx'), ['tx'])
        self.assertEqual(V.build('AC'), ['ac'])

    def test_extra_short_phrase_kept(self):
        self.assertIn('ny', V.build('lawyer ny', extra=['ny']), 'a phrase the caller adds is kept as given')
        self.assertNotIn('ny', V.build('lawyer ny'))

    def test_three_letters_kept(self):
        self.assertIn('dui', V.build('dui lawyer'))

    def test_two_digits_and_accented_letters(self):
        self.assertNotIn('24', V.build('24 hour plumber'), 'two digits alone are too short too')
        self.assertIn('24 hour plumber', V.build('24 hour plumber'))
        self.assertNotIn('öl', V.build('öl service'), 'a two-letter word in any spaced script')

    def test_one_character_tokens_of_unspaced_scripts_kept(self):
        got = V.build('歯医者')
        self.assertIn('歯', got, 'Chinese/Japanese match character by character: not a short word')


if __name__ == '__main__':
    unittest.main(verbosity=2)
