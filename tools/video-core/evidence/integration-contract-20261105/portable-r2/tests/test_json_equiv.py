"""Pure tests of the portable-r2 comparison rule (no children, no library). Run:  python -m unittest discover -s tests -t .   (from portable-r2/)"""
import decimal
import fractions
import datetime
import json
import math
import os
import random
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from json_equiv import MAX_DEPTH, UnsupportedValue, canon, equivalent  # noqa: E402


class Rule(unittest.TestCase):
    def test_tuple_and_list_are_the_same_kind_at_every_depth(self):
        self.assertTrue(equivalent((1, 2), [1, 2]))
        self.assertTrue(equivalent([1, (2, [3, (4,)])], (1, [2, (3, [4])])))
        self.assertTrue(equivalent({"a": (1, [2])}, {"a": [1, (2,)]}))
        self.assertTrue(equivalent((), []))

    def test_scalar_types_are_preserved_never_coerced(self):
        pairs = [(True, 1), (False, 0), (1, 1.0), (0, 0.0), (True, 1.0), ("1", 1), ("", None), (None, False), (None, 0), (None, ""), (None, []), (None, {}), ([], {}), ([], ""), (0.0, -0.0), ("True", True), ("None", None)]
        for a, b in pairs:
            with self.subTest(a=a, b=b):
                self.assertFalse(equivalent(a, b))
                self.assertFalse(equivalent(b, a))
                self.assertFalse(equivalent([a], [b]))
                self.assertFalse(equivalent({"k": a}, {"k": b}))
                self.assertFalse(equivalent((a,), [b]))

    def test_equal_scalars_are_equal(self):
        for v in (None, True, False, 0, 1, -1, 2 ** 80, 1.5, -0.0, 0.0, "", "x", "日本語", "\U0001F3AC", "é"):
            with self.subTest(v=v):
                self.assertTrue(equivalent(v, v))
        self.assertFalse(equivalent("é", "é"), "no Unicode normalisation")

    def test_order_length_and_nesting_matter_for_arrays(self):
        self.assertFalse(equivalent([1, 2], [2, 1]))
        self.assertFalse(equivalent([1], [1, 1]))
        self.assertFalse(equivalent([[1, 2]], [1, 2]))
        self.assertFalse(equivalent([1, [2, 3]], [1, [3, 2]]))
        self.assertFalse(equivalent([[]], []))

    def test_objects_compare_key_set_and_values_not_key_order(self):
        self.assertTrue(equivalent({"a": 1, "b": 2}, {"b": 2, "a": 1}))
        self.assertFalse(equivalent({"a": 1}, {"a": 1, "b": None}), "a missing key is not a null value")
        self.assertFalse(equivalent({"a": None}, {}))
        self.assertFalse(equivalent({"a": 1}, {"A": 1}))
        self.assertFalse(equivalent({"a": [1, 2]}, {"a": [2, 1]}))

    def test_int_keys_are_canonicalised_like_json_and_nothing_else(self):
        self.assertTrue(equivalent({0: "a", -1: "b", 7: (1,)}, {"0": "a", "-1": "b", "7": [1]}))
        self.assertFalse(equivalent({1: "a"}, {"2": "a"}))
        self.assertFalse(equivalent({1: "a"}, {"01": "a"}))
        with self.assertRaises(UnsupportedValue):
            canon({1: "a", "1": "b"})                       # collision after canonicalisation
        for bad in ({True: 1}, {None: 1}, {(1, 2): 1}, {1.5: 1}, {b"k": 1}):
            with self.subTest(bad=bad), self.assertRaises(UnsupportedValue):
                canon(bad)

    def test_unsupported_values_raise_on_either_side_even_if_both_sides_are_identical(self):
        unsupported = [b"x", bytearray(b"x"), {1, 2}, frozenset([1]), 1 + 2j, fractions.Fraction(1, 3), decimal.Decimal("1.5"), datetime.datetime(2026, 1, 1), object(), range(3), lambda: 0, float, ...]
        for v in unsupported:
            with self.subTest(v=type(v).__name__):
                with self.assertRaises(UnsupportedValue):
                    equivalent(v, v)
                with self.assertRaises(UnsupportedValue):
                    equivalent([v], 1)
                with self.assertRaises(UnsupportedValue):
                    equivalent(1, {"k": [v]})

    def test_non_finite_floats_are_rejected_never_compared(self):
        for v in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(v=v):
                with self.assertRaises(UnsupportedValue):
                    equivalent(v, v)
                with self.assertRaises(UnsupportedValue):
                    equivalent([1, [v]], [1, [2]])

    def test_lone_surrogates_are_unsupported_in_values_and_keys(self):
        with self.assertRaises(UnsupportedValue):
            canon("a\ud800b")
        with self.assertRaises(UnsupportedValue):
            canon({"a\udc80": 1})
        self.assertTrue(equivalent("\U0001F3AC", "\U0001F3AC"))          # a valid astral character is fine

    def test_depth_is_bounded(self):
        x = 1
        for _ in range(MAX_DEPTH):
            x = [x]
        canon(x)                                                          # exactly at the limit
        with self.assertRaises(UnsupportedValue):
            canon([x])

    def test_subclasses_of_builtin_types_are_not_silently_accepted(self):
        class MyInt(int):
            pass

        class MyStr(str):
            pass

        class MyList(list):
            pass
        for v in (MyInt(1), MyStr("a"), MyList([1])):
            with self.subTest(v=type(v).__name__), self.assertRaises(UnsupportedValue):
                canon(v)

    def test_reflexive_and_symmetric_on_a_corpus(self):
        corpus = [None, True, 0, 1, 1.0, "1", [], {}, (), [1, (2,)], {"a": 1}, {1: "a"}, ((1,),), {"a": [None]}]
        for a in corpus:
            self.assertTrue(equivalent(a, a))
            for b in corpus:
                self.assertEqual(equivalent(a, b), equivalent(b, a))

    @staticmethod
    def _gen(rng, depth=0):
        k = rng.randrange(10 if depth < 4 else 6)
        if k == 0:
            return None
        if k == 1:
            return rng.choice([True, False])
        if k == 2:
            return rng.choice([0, 1, -1, 7, 4805, 2 ** 70, -(2 ** 70)])
        if k == 3:
            return rng.choice([0.0, -0.0, 1.0, 0.5, -2.25, 1e300, 5e-324, 4800.0])
        if k == 4:
            return rng.choice(["", "a", "1", "日本語 スペース", "\U0001F3AC", "é", "line\nbreak", "quote\"back\\slash"])
        if k == 5:
            return rng.choice([[], (), {}])
        if k in (6, 7):
            n = rng.randrange(4)
            return [Rule._gen(rng, depth + 1) for _ in range(n)] if k == 6 else tuple(Rule._gen(rng, depth + 1) for _ in range(n))
        d = {}
        for _ in range(rng.randrange(4)):
            key = rng.choice(["a", "b", "k1", "日本", 0, 1, -5, 4805]) if k == 8 else rng.choice(["a", "b", "x y"])
            if str(key) in {str(x) for x in d}:
                continue
            d[key] = Rule._gen(rng, depth + 1)
        return d

    def test_invariant_under_a_json_write_read_round_trip_of_either_side(self):
        rng = random.Random(20261105)
        n = 0
        for _ in range(3000):
            x = self._gen(rng)
            text = json.dumps(x, allow_nan=False)
            y = json.loads(text)
            self.assertTrue(equivalent(x, y), (x, text))
            self.assertTrue(equivalent(y, x))
            self.assertTrue(equivalent(y, json.loads(json.dumps(y))))
            n += 1
        self.assertEqual(n, 3000)

    def test_a_single_change_anywhere_is_detected_after_a_round_trip(self):
        rng = random.Random(7)
        detected = tested = 0
        for _ in range(1500):
            x = self._gen(rng)
            y = json.loads(json.dumps(x))
            z = self._mutate(y, rng)
            if z is None:
                continue
            tested += 1
            if not equivalent(x, z):
                detected += 1
        self.assertGreater(tested, 300)
        self.assertEqual(detected, tested, "a changed value, type or shape must never compare equal")

    @staticmethod
    def _mutate(v, rng):
        """Return a structurally different JSON value derived from v (None if no change was possible)."""
        if v is None:
            return rng.choice([False, 0, "", []])
        if v is True or v is False:
            return int(v)                                  # bool -> int of the same value
        if isinstance(v, int):
            return float(v) if abs(v) < 2 ** 52 else v + 1
        if isinstance(v, float):
            return int(v) if v == int(v) and abs(v) < 2 ** 52 else (v * 2 if v != 0.0 else 1.0)      # a float whose value or type really changes (1e300 + 1.0 would not)
        if isinstance(v, str):
            return v + "x"
        if isinstance(v, list):
            if not v:
                return {}
            i = rng.randrange(len(v))
            if len(v) > 1 and v[0] != v[-1] and rng.random() < 0.3:
                return list(reversed(v)) if list(reversed(v)) != v and not all(equivalent(a, b) for a, b in zip(v, reversed(v))) else v + [None]
            m = Rule._mutate(v[i], rng)
            return v[:i] + [m] + v[i + 1:]
        if isinstance(v, dict):
            if not v:
                return []
            k = rng.choice(sorted(v))
            m = Rule._mutate(v[k], rng)
            out = dict(v)
            out[k] = m
            return out
        return None


if __name__ == "__main__":
    unittest.main()
