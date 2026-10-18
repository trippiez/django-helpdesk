def load_tests(loader, tests, pattern):
    """Keep optional browser tests out of the default backend test discovery.

    Run browser modules explicitly, e.g. quicktest.py e2e.test_public_ticket_creation.
    """
    return tests
