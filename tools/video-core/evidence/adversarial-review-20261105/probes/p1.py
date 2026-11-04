import sys, os, json, traceback
sys.path.insert(0, "/home/user/claude/tools/video-core")
from video_core import numeric, limits
from video_core.audiomix import limits as al
def t(label, fn):
    try:
        print(label, "->", repr(fn())[:150])
    except BaseException as e:
        print(label, "-> EXC", type(e).__name__, str(e)[:100])
t("finite_seconds(10**400)", lambda: numeric.finite_seconds("x", 10**400))
t("Limits(max_runtime_seconds=10**400)", lambda: limits.Limits(max_runtime_seconds=10**400))
t("positive_int 10**5000", lambda: numeric.positive_int("x", 10**5000))
t("finite_seconds -inf", lambda: numeric.finite_seconds("x", -float('inf')))
t("finite_seconds 5e-324", lambda: numeric.finite_seconds("x", 5e-324))
from fractions import Fraction
t("Fraction('٣/٤')", lambda: Fraction('٣/٤'))
t("Fraction('1_0/3')", lambda: Fraction('1_0/3'))
