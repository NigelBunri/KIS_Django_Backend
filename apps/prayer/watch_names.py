# apps/prayer/watch_names.py
"""The 24 permanent watch names, verbatim from the product spec — kept as
a single literal list (not generated from an ordinal-word function) so
there is zero risk of an off-by-one or "Twenty-Twoth"-style naming bug.
Hour 0 is uniquely "The Final Hour Watch" (the last watch in the daily
sequence), not "The Zeroth Hour Watch" — every other hour uses its
ordinal position (1 = First, ... 23 = Twenty-Third)."""

WATCH_NAMES: list[tuple[int, str]] = [
    (0, "The Final Hour Watch: 12:00 AM"),
    (1, "The First Hour Watch: 01:00 AM"),
    (2, "The Second Hour Watch: 02:00 AM"),
    (3, "The Third Hour Watch: 03:00 AM"),
    (4, "The Fourth Hour Watch: 04:00 AM"),
    (5, "The Fifth Hour Watch: 05:00 AM"),
    (6, "The Sixth Hour Watch: 06:00 AM"),
    (7, "The Seventh Hour Watch: 07:00 AM"),
    (8, "The Eighth Hour Watch: 08:00 AM"),
    (9, "The Ninth Hour Watch: 09:00 AM"),
    (10, "The Tenth Hour Watch: 10:00 AM"),
    (11, "The Eleventh Hour Watch: 11:00 AM"),
    (12, "The Twelfth Hour Watch: 12:00 PM"),
    (13, "The Thirteenth Hour Watch: 01:00 PM"),
    (14, "The Fourteenth Hour Watch: 02:00 PM"),
    (15, "The Fifteenth Hour Watch: 03:00 PM"),
    (16, "The Sixteenth Hour Watch: 04:00 PM"),
    (17, "The Seventeenth Hour Watch: 05:00 PM"),
    (18, "The Eighteenth Hour Watch: 06:00 PM"),
    (19, "The Nineteenth Hour Watch: 07:00 PM"),
    (20, "The Twentieth Hour Watch: 08:00 PM"),
    (21, "The Twenty-First Hour Watch: 09:00 PM"),
    (22, "The Twenty-Second Hour Watch: 10:00 PM"),
    (23, "The Twenty-Third Hour Watch: 11:00 PM"),
]

assert len(WATCH_NAMES) == 24
assert sorted(h for h, _ in WATCH_NAMES) == list(range(24))

KIS_WELCOME_MESSAGE = """Welcome to the KIS Global Prayer Watch.

This is a shared prayer room where believers from around the world come together to pray. Our goal is simple: prayer should never stop.

Each watch runs for one hour and follows the same prayer structure. Anyone may volunteer to lead, subject to the leadership guidelines.

You may participate through text messages, voice calls or video calls, depending on the leading of the appointed prayer leader.

Please respect the prayer structure, the appointed leaders, fellow participants and the purpose of this room.

This is not a personal ministry platform or a place for anyone to establish ownership over the group. We are here to pray together.

Please use the prayer-point feature to share specific needs for prayer.

May the Lord strengthen us as we continue in prayer."""
