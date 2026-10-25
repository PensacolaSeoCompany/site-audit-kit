#!/usr/bin/env python3
"""Unit tests for onpage/variations.py, compare.py, schemacheck.py and the factor registry.
Run: python3 tests/test_onpage_units.py (no network, under a second).
Controls: a planted bad registry row and a planted bad result must FAIL the schema check."""
import copy, os, statistics, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'onpage'))
import variations as V  # noqa: E402
import compare as C  # noqa: E402
import schemacheck as S  # noqa: E402
from measure import load_registry, MEASURE_KINDS  # noqa: E402
from textutil import tokens  # noqa: E402

REG = load_registry()


class Variations(unittest.TestCase):
    def test_keyword_first_and_subphrases(self):
        v = V.build('Stump Removal')
        self.assertEqual(v[0], 'stump removal')
        self.assertEqual(set(v), {'stump removal', 'stump', 'removal'})

    def test_filler_edges_dropped(self):
        v = V.build('stump removal near me')
        self.assertIn('stump removal near me', v)
        self.assertIn('stump removal', v)
        for bad in ('near me', 'removal near', 'near', 'me'):
            self.assertNotIn(bad, v)

    def test_inflections_only_when_seen_on_a_page(self):
        pages = [tokens('Our lawyers handle DUI cases in Los Angeles.')]
        v = V.build('dui lawyer los angeles', page_tokens=pages)
        self.assertIn('lawyers', v)
        self.assertNotIn('angele', v, 'a form no page uses must not be added')
        self.assertNotIn('lawyers', V.build('dui lawyer los angeles'), 'no pages given = no inflected forms')

    def test_extra_variations_added_once(self):
        v = V.build('stump removal', extra=['Stump Grinding', 'stump grinding', ''])
        self.assertEqual(v.count('stump grinding'), 1)

    def test_empty_keyword(self):
        self.assertEqual(V.build(''), [])


# A small registry with all three bands, so the comparison can be tested whatever factors.json holds.
SYN = {'factors': [
    {'key': 'word_count', 'name': 'Word count', 'group': 'size', 'unit': 'words', 'band': 'length'},
    {'key': 'variations_p', 'name': 'Keyword variations in paragraphs', 'group': 'content', 'unit': 'count', 'band': 'length'},
    {'key': 'h2_count', 'name': 'Number of H2 tags', 'group': 'headings', 'unit': 'count', 'band': 'target'},
    {'key': 'variations_title', 'name': 'Keyword variations in the title', 'group': 'head', 'unit': 'count', 'band': 'target'},
    {'key': 'has_og_title', 'name': 'Has an OpenGraph title', 'group': 'head', 'unit': 'yes/no', 'band': 'target'},
    {'key': 'page_kb', 'name': 'Page size (KB)', 'group': 'size', 'unit': 'KB', 'band': 'context'},
    {'key': 'no_band', 'name': 'A row with no band', 'group': 'markup', 'unit': 'count'},
]}


def row(rows, key):
    return [x for x in rows if x['key'] == key][0]


class Position(unittest.TestCase):
    """position comes from how many measured competitors have more (n of n) and the median."""

    def test_n_of_n(self):
        self.assertEqual(C.position(1, [3, 4, 5]), 'below all')
        self.assertEqual(C.position(9, [3, 4, 8]), 'above all')
        self.assertEqual(C.position(4, [4, 4, 4]), 'same as all')
        self.assertEqual(C.position(11, [29, 3, 3]), 'above most', 'beats 2 of 3 even though the mean (11.7) is higher')
        self.assertEqual(C.position(9, [13, 2, 10]), 'below most', '2 of 3 have more even though the mean (8.3) is lower')
        self.assertEqual(C.position(4, [3, 5]), 'middle')
        self.assertEqual(C.position(5, [3]), 'above all')
        self.assertEqual(C.position(2, [3]), 'below all')
        self.assertEqual(C.position(5, [5, 5, 9]), 'middle', 'a tie is not "more"')
        self.assertEqual(C.position(None, [1, 2]), 'not measured')
        self.assertEqual(C.position(1, [None, None]), 'not measured')

    def test_row_fields(self):
        r = row(C.compare(SYN, {'h2_count': 11}, [{'h2_count': 29}, {'h2_count': 3}, {'h2_count': 3}]), 'h2_count')
        self.assertEqual((r['n_measured'], r['n_more'], r['n_less']), (3, 1, 2))
        self.assertEqual(r['competitor_median'], 3)
        self.assertEqual((r['competitor_min'], r['competitor_max']), (3, 29))
        self.assertEqual(r['standing'], 'above 2 of 3')
        self.assertEqual(r['gap_to_median'], -8)
        self.assertEqual(r['band'], 'target')

    def test_standing_words(self):
        rows = C.compare(SYN, {'h2_count': 1, 'variations_title': 9, 'page_kb': 5},
                         [{'h2_count': 3, 'variations_title': 2, 'page_kb': 5}, {'h2_count': 4, 'variations_title': 10, 'page_kb': 5},
                          {'h2_count': 5, 'variations_title': 12, 'page_kb': 5}])
        self.assertEqual(row(rows, 'h2_count')['standing'], 'below all 3')
        self.assertEqual(row(rows, 'variations_title')['standing'], 'below 2 of 3')
        self.assertEqual(row(rows, 'page_kb')['standing'], 'same as all 3')

    def test_even_median(self):
        r = row(C.compare(SYN, {'h2_count': 1}, [{'h2_count': 2}, {'h2_count': 5}]), 'h2_count')
        self.assertEqual(r['competitor_median'], 3.5)

    def test_none_is_not_zero(self):
        r = row(C.compare(SYN, {'h2_count': 1}, [None, {'h2_count': 3}]), 'h2_count')
        self.assertEqual(r['competitors'], [None, 3])
        self.assertEqual((r['n_measured'], r['n_more'], r['competitor_median']), (1, 1, 3))
        self.assertEqual(r['position'], 'below all')

    def test_band_missing_is_context(self):
        rows = C.compare(SYN, {'no_band': 0}, [{'no_band': 5}] * 3)
        self.assertEqual(row(rows, 'no_band')['band'], 'context')
        self.assertNotIn('no_band', [r['key'] for r in C.actions(rows)])

    def test_formatting_and_small_numbers(self):
        self.assertEqual(C.fmt(3.0), '3')
        self.assertEqual(C.fmt(2.25), '2.2')
        self.assertEqual(C.span(2, 2), '2')
        self.assertEqual(C.span(2, 5), '2 to 5')
        self.assertEqual(C.median([0.333, 0.334]), 0.33)
        self.assertEqual(C.standing(None, [1]), 'not measured')
        r = row(C.compare(SYN, {'h2_count': 1}, [{'h2_count': 1.5}, {'h2_count': 2.25}, {'h2_count': 4}]), 'h2_count')
        self.assertEqual(r['competitor_avg'], 2.58)
        r = row(C.compare(SYN, {'h2_count': 1}, [{'h2_count': 1.5}, {'h2_count': 2.25}]), 'h2_count')
        self.assertEqual(r['gap_to_median'], 0.88)
        vp = row(C.compare(SYN, {'word_count': 300, 'variations_p': 7}, [{'word_count': 300, 'variations_p': 7}]), 'variations_p')
        self.assertEqual(vp['per_1k']['mine'], 23.3)

    def test_say_edges(self):
        nc = row(C.compare(SYN, {'h2_count': 1, 'has_og_title': 1}, [None, None]), 'h2_count')
        self.assertEqual(C.say(nc), 'Not measured on any competitor page.')
        yes = row(C.compare(SYN, {'has_og_title': 1}, [{'has_og_title': 1}, {'has_og_title': 0}]), 'has_og_title')
        self.assertEqual(C.say(yes), '1 of 2 competitors has it; your page has it.')

    def test_bool_is_not_a_number(self):
        self.assertIsNone(C._num(True))

    def test_per_1000_words_for_length_counts(self):
        rows = C.compare(SYN, {'word_count': 500, 'variations_p': 10, 'h2_count': 2},
                         [{'word_count': 2000, 'variations_p': 30, 'h2_count': 4}, {'word_count': 0, 'variations_p': 0, 'h2_count': 4}, None])
        vp = row(rows, 'variations_p')
        self.assertEqual(vp['per_1k']['mine'], 20.0)
        self.assertEqual(vp['per_1k']['competitors'], [15.0, None, None], 'no words or not measured = no rate, never 0')
        self.assertIsNone(row(rows, 'word_count')['per_1k'], 'word count per 1,000 words is meaningless')
        self.assertIsNone(row(rows, 'h2_count')['per_1k'], 'rates are for the length band only')
        reg = {'factors': [dict(SYN['factors'][0], unit='count')]}
        self.assertIsNone(C.compare(reg, {'word_count': 10}, [{'word_count': 20}])[0]['per_1k'], 'never a rate of word count itself')

    def test_say_quotes_range_not_average(self):
        r = row(C.compare(SYN, {'h2_count': 3}, [{'h2_count': 14}, {'h2_count': 4}, {'h2_count': 29}]), 'h2_count')
        self.assertEqual(C.say(r), 'Competitors have 4 to 29 (middle value 14); you have 3. 3 of 3 have more.')
        self.assertNotIn('average', C.say(r))
        cs = row(C.compare(SYN, {'has_og_title': 0}, [{'has_og_title': 1}, {'has_og_title': 1}, {'has_og_title': 0}]), 'has_og_title')
        self.assertEqual(C.say(cs), '2 of 3 competitors have it; your page does not have it.')
        one = row(C.compare(SYN, {'h2_count': 3}, [{'h2_count': 7}]), 'h2_count')
        self.assertEqual(C.say(one), 'The competitor has 7; you have 3.')
        same = row(C.compare(SYN, {'h2_count': 0}, [{'h2_count': 1}] * 3), 'h2_count')
        self.assertEqual(C.say(same), 'All 3 competitors have 1; you have 0.')
        nm = row(C.compare(SYN, None, [{'h2_count': 7}]), 'h2_count')
        self.assertEqual(C.say(nm), 'Not measured on your page.')


class Actions(unittest.TestCase):
    """the to-do list = TARGET rows where most measured competitors have more."""

    def setUp(self):
        mine = {'word_count': 300, 'variations_p': 2, 'h2_count': 1, 'variations_title': 1, 'has_og_title': 0, 'page_kb': 10}
        comps = [{'word_count': 1200, 'variations_p': 9, 'h2_count': 6, 'variations_title': 2, 'has_og_title': 1, 'page_kb': 90},
                 {'word_count': 5900, 'variations_p': 40, 'h2_count': 0, 'variations_title': 3, 'has_og_title': 1, 'page_kb': 80},
                 {'word_count': 1311, 'variations_p': 5, 'h2_count': 2, 'variations_title': 0, 'has_og_title': 0, 'page_kb': 70}]
        self.rows = C.compare(SYN, mine, comps)

    def test_only_target_rows_most_have_more(self):
        keys = [r['key'] for r in C.actions(self.rows)]
        self.assertNotIn('page_kb', keys, 'context row below all is never an action')
        self.assertNotIn('word_count', keys, 'length rows are summarised, not actions')
        self.assertNotIn('variations_p', keys)
        self.assertEqual(keys, ['has_og_title', 'h2_count', 'variations_title'])

    def test_order_n_more_then_relative_gap(self):
        rows = C.compare(SYN, {'h2_count': 1, 'variations_title': 1, 'has_og_title': 0},
                         [{'h2_count': 2, 'variations_title': 20, 'has_og_title': 1}] * 2 + [{'h2_count': 0, 'variations_title': 0, 'has_og_title': 1}])
        self.assertEqual([r['key'] for r in C.actions(rows)], ['has_og_title', 'variations_title', 'h2_count'],
                         '3 of 3 first; then 2 of 3 by gap relative to the median (19/20 before 1/2)')

    def test_half_is_not_most(self):
        rows = C.compare(SYN, {'h2_count': 4}, [{'h2_count': 5}, {'h2_count': 3}])
        self.assertEqual(C.actions(rows), [])

    def test_above_all_not_an_action(self):
        rows = C.compare(SYN, {'h2_count': 40}, [{'h2_count': 5}] * 3)
        self.assertEqual(C.actions(rows), [])

    def test_one_competitor_length_line(self):
        rows = C.compare(SYN, {'word_count': 5528}, [None, {'word_count': 1205}, None])
        self.assertEqual(C.length_line(rows), 'The competitor page is 1,205 words; yours is 5,528.')

    def test_two_competitors_none_behind(self):
        rows = C.compare(SYN, {'h2_count': 5, 'has_og_title': 1}, [{'h2_count': 5, 'has_og_title': 1}] * 2)
        self.assertEqual(C.actions(rows), [])
        self.assertTrue(C.why_no_actions(rows).startswith('None: on every keyword-placement factor'))

    def test_one_competitor_no_actions(self):
        rows = C.compare(SYN, {'h2_count': 0, 'has_og_title': 0}, [None, {'h2_count': 5, 'has_og_title': 1}, None])
        self.assertEqual(C.actions(rows), [])
        self.assertIn('only 1 competitor page was measured', C.why_no_actions(rows))

    def test_length_line(self):
        rows = C.compare(SYN, {'word_count': 3300}, [{'word_count': 1200}, {'word_count': 5900}, {'word_count': 1311}])
        self.assertEqual(C.length_line(rows), 'Competitor pages are 1,200-5,900 words; yours is 3,300.')
        self.assertIsNone(C.length_line(C.compare({'factors': SYN['factors'][2:]}, {}, [{}])))


class Properties(unittest.TestCase):
    """Seeded random rows: the counts, the position, the standing and the action list agree with each other."""

    def test_random_rows(self):
        import random
        rnd = random.Random(7)
        for _ in range(400):
            n = rnd.randint(1, 5)
            comps = [None if rnd.random() < 0.15 else {k['key']: rnd.randint(0, 6) for k in SYN['factors']} for _ in range(n)]
            mine = {k['key']: rnd.randint(0, 6) for k in SYN['factors']}
            rows = C.compare(SYN, mine, comps)
            for r in rows:
                vals = [c for c in r['competitors'] if c is not None]
                self.assertEqual(r['n_measured'], len(vals))
                if not vals:
                    self.assertEqual(r['position'], 'not measured')
                    continue
                self.assertEqual(r['n_more'], sum(c > r['mine'] for c in vals))
                self.assertEqual(r['n_less'], sum(c < r['mine'] for c in vals))
                self.assertTrue(r['competitor_min'] <= r['competitor_median'] <= r['competitor_max'])
                self.assertEqual(r['competitor_median'], round(statistics.median(vals), 2))
                # standing agrees with the counts word for word (tie / above / one-competitor wording)
                k, mo, le = len(vals), r['n_more'], r['n_less']
                one = k == 1
                allk = 'both' if k == 2 else f'all {k}'
                want_st = (('below the 1 competitor' if one else f'below {allk}') if mo == k else
                           ('above the 1 competitor' if one else f'above {allk}') if le == k else
                           ('same as the 1 competitor' if one else f'same as {allk}') if mo == le == 0 else
                           f'below {mo} of {k}' if mo > le else f'above {le} of {k}' if le > mo else
                           f'in the middle ({mo} of {k} have more)')
                self.assertEqual(r['standing'], want_st)
                if r['position'] == 'below all':
                    self.assertTrue(r['standing'].startswith('below'))
                if r['position'] in ('below all', 'below most'):
                    self.assertGreater(r['n_more'], len(vals) / 2)
            acts = C.actions(rows)
            want = {r['key'] for r in rows if r['band'] == 'target' and r['n_measured'] >= C.MIN_FOR_ACTIONS
                    and r['n_more'] > r['n_measured'] / 2}
            self.assertEqual({r['key'] for r in acts}, want)
            self.assertEqual([r['n_more'] for r in acts], sorted([r['n_more'] for r in acts], reverse=True))


class Registry(unittest.TestCase):
    def test_bands_once_present_are_on_every_row(self):
        bands = [f.get('band') for f in REG['factors']]
        if not any(bands):
            self.skipTest('factors.json has no band yet')
        self.assertTrue(set(bands) <= {'target', 'length', 'context'}, set(bands))

    def test_registry_valid(self):
        self.assertEqual(S.validate(REG, S.load('factor.schema.json')), [])
        self.assertEqual(len({f['key'] for f in REG['factors']}), 50)
        self.assertTrue({f['measure'] for f in REG['factors']} <= MEASURE_KINDS)

    def test_control_bad_registry_fails(self):
        bad = copy.deepcopy(REG)
        bad['factors'][0]['measure'] = 'vibes'
        self.assertTrue(S.validate(bad, S.load('factor.schema.json')))
        bad = copy.deepcopy(REG)
        bad['factors'].pop()
        self.assertTrue(S.validate(bad, S.load('factor.schema.json')))
        bad = copy.deepcopy(REG)
        bad['factors'][3]['surprise'] = 1
        self.assertTrue(S.validate(bad, S.load('factor.schema.json')))

    def test_control_bad_result_fails(self):
        res = {'tool': 'site-audit-kit onpage', 'version': '1', 'created': 'x', 'job': {}, 'complete': True, 'notes': [],
               'pages': [], 'factors': []}
        errs = S.validate(res, S.load('result.schema.json'))
        self.assertTrue(any('fewer than' in e for e in errs))


class FamilyCollapse(unittest.TestCase):
    """One edit on the page (e.g. the H1) gives one action line, not one per overlapping factor."""

    def test_same_family_collapses(self):
        reg = {"factors": [
            {"key": "variations_h1", "name": "H1 variations", "group": "headings", "band": "target", "family": "h1", "unit": "count"},
            {"key": "starts_with_variation_h1", "name": "H1 starts with a variation", "group": "headings", "band": "target", "family": "h1", "unit": "count"},
            {"key": "variations_title", "name": "Title variations", "group": "head", "band": "target", "family": "title", "unit": "count"}]}
        mine = {"variations_h1": 0, "starts_with_variation_h1": 0, "variations_title": 0}
        comps = [{"variations_h1": 2, "starts_with_variation_h1": 1, "variations_title": 1}] * 3
        acts = C.actions(C.compare(reg, mine, comps))
        self.assertEqual(len(acts), 2, [a["key"] for a in acts])
        h1 = [a for a in acts if a["family"] == "h1"][0]
        self.assertEqual(len(h1["also"]), 1)

    def test_every_target_factor_has_a_family(self):
        self.assertTrue(all(f.get("family") for f in REG["factors"] if f["band"] == "target"))



class PrintedPrecision(unittest.TestCase):
    """Two values that print the same are the same: never 'above' or 'below' between equal printed numbers."""

    def test_fmt_small_decimals(self):
        self.assertEqual(C.fmt(0.14), '0.14')
        self.assertEqual(C.fmt(0.1), '0.1')
        self.assertEqual(C.fmt(0.061), '0.06')
        self.assertEqual(C.fmt(1234.56), '1,234.6')
        self.assertEqual(C.fmt(2.25), '2.2')

    def test_equal_printed_values_are_equal(self):
        # the user test: 0.1 shown for yours and two competitors, yet "above all 3"
        mine, comps = 0.1004, [0.0996, 0.1001, 0.04]
        self.assertEqual(C.standing(mine, comps), 'above 1 of 3')
        self.assertEqual(C.position(mine, comps), 'middle')
        r = row(C.compare(SYN, {'page_kb': mine}, [{'page_kb': c} for c in comps]), 'page_kb')
        self.assertEqual((r['n_more'], r['n_less']), (0, 1))

    def test_property_printed_ties(self):
        import random
        rnd = random.Random(7)
        for _ in range(3000):
            mine = rnd.choice([rnd.random() * 0.3, rnd.random() * 30, float(rnd.randint(0, 5))])
            comps = [rnd.choice([mine + rnd.uniform(-0.004, 0.004), rnd.random() * 0.3, rnd.random() * 30]) for _ in range(3)]
            n, more, less = C.counts(mine, comps)
            ties = sum(1 for c in comps if C.fmt(c) == C.fmt(mine))
            self.assertEqual(more + less + ties, n, (mine, comps))

    def test_span_and_say_on_printed_values(self):
        self.assertEqual(C.span(0.141, 0.144), '0.14')
        r = row(C.compare(SYN, {'page_kb': 0.05}, [{'page_kb': 0.141}, {'page_kb': 0.144}, {'page_kb': 0.142}]), 'page_kb')
        self.assertEqual(C.say(r), 'All 3 competitors have 0.14; you have 0.05.')


if __name__ == '__main__':
    unittest.main(verbosity=2)
