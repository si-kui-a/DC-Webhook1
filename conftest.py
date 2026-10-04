"""Keep the real .env out of the test run.

Several modules call load_dotenv() at import time (main.py, notify_telegram.py,
resume_matcher.py, resume_bot.py). Importing any of them while pytest collects
pulled ~40 real settings into os.environ, so tests that read env defaults (e.g.
RENTAL_* filters in tests/test_rental_search.py) passed or failed depending on
which other test files were collected. conftest.py loads before any test module,
so turning load_dotenv into a no-op here covers every import path.
"""
import dotenv

dotenv.load_dotenv = lambda *args, **kwargs: False
