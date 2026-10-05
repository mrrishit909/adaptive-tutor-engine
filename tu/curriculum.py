"""A small pre-algebra course written for this project: 18 concepts with prerequisites, a generator of practice items
for each, and the wrong answers that four well-known misconceptions would produce. Every item's answer is computed,
never typed in, and each misconception is a rule that can be run on the same numbers ("buggy rules")."""
import random
from fractions import Fraction

# misconception -> (what the learner does, the rule to teach, a worked contrast)
MISCONCEPTIONS = {
    "sub_negative": ("treats subtracting a negative as subtracting", "Subtracting a negative number is the same as adding its opposite: a - (-b) = a + b.", "7 - (-3): moving 3 the other way on the number line gives 7 + 3 = 10, not 4."),
    "neg_times_neg": ("gives a negative for negative times negative", "A negative times a negative is positive: the two sign changes cancel.", "(-4) x (-5) = 20. Compare (-4) x 5 = -20: flipping the sign of the 5 flips the answer."),
    "left_to_right": ("works left to right, ignoring that multiplication comes first", "Multiply and divide before you add and subtract.", "2 + 3 x 4: do 3 x 4 = 12 first, then 2 + 12 = 14, not 20."),
    "distribute_first_only": ("multiplies only the first term inside the brackets", "The number outside multiplies every term inside the brackets.", "3(x + 4) = 3x + 12, not 3x + 4.")}
# key, name, prerequisites, the misconceptions its items can expose
CONCEPTS = [
    ("int_add", "Adding integers", [], []),
    ("int_sub", "Subtracting integers", ["int_add"], ["sub_negative"]),
    ("int_mul", "Multiplying integers", ["int_add"], ["neg_times_neg"]),
    ("order_ops", "Order of operations", ["int_add", "int_mul"], ["left_to_right"]),
    ("distribute", "Distributing over brackets", ["int_mul", "order_ops"], ["distribute_first_only"]),
    ("like_terms", "Combining like terms", ["int_add", "int_sub"], ["sub_negative"]),
    ("eq_add", "One-step equations: x + a = b", ["int_sub"], ["sub_negative"]),
    ("eq_mul", "One-step equations: ax = b", ["int_mul"], []),
    ("eq_two_step", "Two-step equations", ["eq_add", "eq_mul"], ["sub_negative"]),
    ("eq_brackets", "Equations with brackets", ["distribute", "eq_two_step"], ["distribute_first_only"]),
    ("eq_both_sides", "Variables on both sides", ["eq_two_step", "like_terms"], ["sub_negative"]),
    ("evaluate", "Evaluating expressions", ["int_mul", "order_ops"], ["neg_times_neg", "left_to_right"]),
    ("coordinates", "Reading coordinates", ["int_add"], []),
    ("slope", "Slope between two points", ["int_sub", "eq_mul", "coordinates"], ["sub_negative"]),
    ("percent", "Percent of a number", ["int_mul"], []),
    ("ratio", "Solving proportions", ["eq_mul"], []),
    ("perimeter", "Perimeter of a rectangle", ["int_add"], []),
    ("area", "Area of a rectangle", ["int_mul"], [])]
KEYS = [c[0] for c in CONCEPTS]
PREREQS = {c[0]: c[2] for c in CONCEPTS}
EXPOSES = {c[0]: c[3] for c in CONCEPTS}
NAME = {c[0]: c[1] for c in CONCEPTS}


def neg(n):
    return f"({n})" if n < 0 else str(n)


def generate(key, rng, target=None):
    """One item for a concept. -> {concept, stem, answer, bugs: {misconception: wrong answer it produces}}
    `target` asks for numbers on which that misconception gives a different (wrong) answer."""
    r = rng.randint
    for _ in range(200):
        bugs = {}
        if key == "int_add":
            a, b = r(-12, 12), r(-12, 12)
            stem, ans = f"{neg(a)} + {neg(b)} = ?", a + b
        elif key == "int_sub":
            a, b = r(-12, 12), r(-12, 12)
            stem, ans = f"{neg(a)} - {neg(b)} = ?", a - b
            if b < 0:
                bugs["sub_negative"] = a - abs(b)
        elif key == "int_mul":
            a, b = r(-9, 9), r(-9, 9)
            stem, ans = f"{neg(a)} x {neg(b)} = ?", a * b
            if a < 0 and b < 0:
                bugs["neg_times_neg"] = -(a * b)
        elif key == "order_ops":
            a, b, c = r(1, 9), r(2, 9), r(2, 9)
            stem, ans = f"{a} + {b} x {c} = ?", a + b * c
            bugs["left_to_right"] = (a + b) * c
        elif key == "distribute":
            a, b, c = r(2, 9), r(1, 9), r(1, 9)
            stem, ans = f"Work out {a}({b} + {c}).", a * (b + c)
            bugs["distribute_first_only"] = a * b + c
        elif key == "like_terms":
            a, b = r(2, 12), r(-9, 9)
            stem, ans = f"Simplify {a}x - {neg(b)}x. Give the number in front of x.", a - b
            if b < 0:
                bugs["sub_negative"] = a - abs(b)
        elif key == "eq_add":
            a, b = r(-12, 12), r(-12, 12)
            stem, ans = f"Solve x + {neg(a)} = {b}.", b - a
            if a < 0:
                bugs["sub_negative"] = b - abs(a)
        elif key == "eq_mul":
            a, x = rng.choice([-6, -5, -4, -3, -2, 2, 3, 4, 5, 6]), r(-9, 9)
            stem, ans = f"Solve {a}x = {a * x}.", x
        elif key == "eq_two_step":
            a, x, b = r(2, 6), r(-9, 9), r(-12, 12)
            stem, ans = f"Solve {a}x + {neg(b)} = {a * x + b}.", x
            if b < 0 and (a * x + b - abs(b)) % a == 0:
                bugs["sub_negative"] = (a * x + b - abs(b)) // a
        elif key == "eq_brackets":
            a, x, b = r(2, 6), r(-6, 9), r(1, 9)
            stem, ans = f"Solve {a}(x + {b}) = {a * (x + b)}.", x
            if (a * (x + b) - b) % a == 0:
                bugs["distribute_first_only"] = (a * (x + b) - b) // a
        elif key == "eq_both_sides":
            a, c, x, b = r(4, 9), r(2, 3), r(-6, 9), r(-12, 12)
            d = (a - c) * x + b
            stem, ans = f"Solve {a}x + {neg(b)} = {c}x + {neg(d)}.", x
            if b < 0 and (d - abs(b)) % (a - c) == 0:
                bugs["sub_negative"] = (d - abs(b)) // (a - c)
        elif key == "evaluate":
            a, b, x = r(-5, 5), r(1, 9), r(-6, 6)
            stem, ans = f"Find the value of {b} + {neg(a)}x when x = {x}.", b + a * x
            bugs["left_to_right"] = (b + a) * x
            if a < 0 and x < 0:
                bugs["neg_times_neg"] = b - a * x
        elif key == "coordinates":
            a, b = r(-9, 9), r(-9, 9)
            stem, ans = f"A point is {abs(a)} {'left' if a < 0 else 'right'} and {abs(b)} {'down' if b < 0 else 'up'} from the origin. What is its y-coordinate?", b
        elif key == "slope":
            x1, y1, run, m = r(-6, 6), r(-9, 9), r(1, 4), r(-4, 4)
            x2, y2 = x1 + run, y1 + m * run
            stem, ans = f"Find the slope of the line through ({x1}, {y1}) and ({x2}, {y2}).", m
            if y1 < 0 and (y2 - abs(y1)) % run == 0:
                bugs["sub_negative"] = (y2 - abs(y1)) // run
        elif key == "percent":
            p, n = rng.choice([10, 20, 25, 50, 75]), 20 * r(1, 12)
            stem, ans = f"What is {p}% of {n}?", p * n // 100
        elif key == "ratio":
            a, k, b = r(2, 9), r(2, 6), r(2, 9)
            stem, ans = f"Solve x / {a * k} = {b} / {a}.", b * k
        elif key == "perimeter":
            a, b = r(2, 15), r(2, 15)
            stem, ans = f"A rectangle is {a} by {b}. What is its perimeter?", 2 * (a + b)
        elif key == "area":
            a, b = r(2, 15), r(2, 15)
            stem, ans = f"A rectangle is {a} by {b}. What is its area?", a * b
        else:
            raise KeyError(key)
        bugs = {m: v for m, v in bugs.items() if v != ans}          # a "wrong" rule that happens to give the right answer reveals nothing
        if target is None or target in bugs:
            return {"concept": key, "stem": stem, "answer": ans, "bugs": bugs}
    raise ValueError(f"{key} has no item that exposes {target}")


def check(item):
    """Independent re-computation for the item kinds that can be parsed back from their own text: a generated item is
    only served if this agrees with the stored answer."""
    import re
    m = re.fullmatch(r"\(?(-?\d+)\)? ([+x-]) \(?(-?\d+)\)? = \?", item["stem"])
    if m:
        a, op, b = Fraction(m[1]), m[2], Fraction(m[3])
        return {"+": a + b, "-": a - b, "x": a * b}[op] == item["answer"]
    m = re.fullmatch(r"(\d+) \+ (\d+) x (\d+) = \?", item["stem"])
    if m:
        return int(m[1]) + int(m[2]) * int(m[3]) == item["answer"]
    m = re.fullmatch(r"Solve (-?\d+)x \+ \(?(-?\d+)\)? = (-?\d+)\.", item["stem"])
    if m:
        return int(m[1]) * item["answer"] + int(m[2]) == int(m[3])
    m = re.fullmatch(r"Solve (\d+)\(x \+ (\d+)\) = (-?\d+)\.", item["stem"])
    if m:
        return int(m[1]) * (item["answer"] + int(m[2])) == int(m[3])
    return None                                                      # not a kind this checker reads


def remediation(misconception, rng=None):
    """An item that the misconception gets wrong, from the earliest concept that exposes it."""
    key = next(c[0] for c in CONCEPTS if misconception in c[3])
    return generate(key, rng or random.Random(0), target=misconception)


# One-line rule per concept, shown as a hint. Like the misconception texts above, this is the tutor's only source of
# explanation: it never writes free text.
HINT = {"int_add": "Same signs: add and keep the sign. Different signs: subtract and keep the sign of the larger.", "int_sub": "Subtracting is adding the opposite: a - b = a + (-b).",
        "int_mul": "Same signs give a positive product, different signs a negative one.", "order_ops": "Multiply and divide before you add and subtract.",
        "distribute": "The number outside multiplies every term inside the brackets.", "like_terms": "Add or subtract the numbers in front of x; the x stays.",
        "eq_add": "Undo the addition: subtract the same number from both sides.", "eq_mul": "Undo the multiplication: divide both sides by the number in front of x.",
        "eq_two_step": "Undo the addition first, then the multiplication.", "eq_brackets": "Divide both sides by the number outside, or expand the brackets first.",
        "eq_both_sides": "Collect the x terms on one side and the numbers on the other.", "evaluate": "Replace x by its value, multiply first, then add.",
        "coordinates": "The first number is across (x), the second is up or down (y).", "slope": "Slope is the change in y divided by the change in x.",
        "percent": "p% of n is p x n / 100.", "ratio": "Multiply both sides by the number under x.", "perimeter": "Add all four sides: 2 x (length + width).", "area": "Length times width."}
