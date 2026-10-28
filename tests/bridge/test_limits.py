import pytest

from openrx.bridge.limits import CALL_BUDGET, BudgetExceeded, CallBudget, RateLimits, TokenBucket
from openrx.catalog.loader import load_catalog


class FakeTime:
    """A clock that only moves when sleep() is called or advance() says so."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def advance(self, seconds):
        self.now += seconds


# --- token bucket -------------------------------------------------------------------


def bucket(rate, time):
    return TokenBucket(rate, clock=time.clock, sleep=time.sleep)


def test_burst_of_one_seconds_worth_goes_out_without_waiting():
    time = FakeTime()
    b = bucket(4, time)

    assert [b.acquire() for _ in range(4)] == [0, 0, 0, 0]
    assert time.sleeps == []


def test_then_requests_are_spaced_at_the_rate():
    time = FakeTime()
    b = bucket(4, time)
    for _ in range(4):
        b.acquire()

    waits = [b.acquire() for _ in range(3)]

    assert waits == pytest.approx([0.25, 0.25, 0.25])
    assert time.now == pytest.approx(0.75)


def test_slow_source_waits_longer_than_a_second():
    time = FakeTime()
    b = bucket(0.8, time)  # ClinicalTrials.gov

    assert b.acquire() == 0  # capacity is at least one token
    assert b.acquire() == pytest.approx(1.25)


def test_idle_time_refills_but_not_beyond_capacity():
    time = FakeTime()
    b = bucket(3, time)
    for _ in range(3):
        b.acquire()

    time.advance(60)  # a long pause

    assert [b.acquire() for _ in range(3)] == [0, 0, 0]
    assert b.acquire() == pytest.approx(1 / 3)


def test_rate_limits_keep_one_bucket_per_source():
    time = FakeTime()
    limits = RateLimits(load_catalog(), clock=time.clock, sleep=time.sleep)

    for _ in range(4):
        limits.acquire("openfda")

    assert limits.acquire("pubmed") == 0  # a busy openFDA doesn't slow PubMed
    assert limits.acquire("openfda") == pytest.approx(0.25)


# --- call budget --------------------------------------------------------------------


def test_budget_counts_down_and_refuses_the_call_over_the_limit():
    budget = CallBudget(limit=3)

    assert [budget.charge("exec-1") for _ in range(3)] == [2, 1, 0]
    with pytest.raises(BudgetExceeded) as excinfo:
        budget.charge("exec-1")

    assert excinfo.value.code == "budget_exceeded"
    assert "used all 3 request() calls" in excinfo.value.message


def test_budget_is_per_execution():
    budget = CallBudget(limit=1)
    budget.charge("a")

    assert budget.charge("b") == 0


def test_default_budget_is_fifty():
    assert CallBudget().limit == CALL_BUDGET == 50


@pytest.mark.parametrize("bad", [None, "", "has space", "../x", "x" * 129, 42])
def test_execution_id_must_be_a_simple_token(bad):
    with pytest.raises(BudgetExceeded) as excinfo:
        CallBudget().charge(bad)

    assert excinfo.value.code == "bad_execution_id"


def test_executions_not_seen_for_the_ttl_are_forgotten():
    time = FakeTime()
    budget = CallBudget(limit=1, ttl=60, clock=time.clock)
    budget.charge("old")

    time.advance(61)

    assert budget.charge("old") == 0  # a fresh start, not "budget exceeded"


def test_recent_use_keeps_an_execution_alive():
    time = FakeTime()
    budget = CallBudget(limit=2, ttl=60, clock=time.clock)
    budget.charge("busy")
    time.advance(50)
    budget.charge("other")  # triggers clean-up; "busy" was seen 50 s ago

    with pytest.raises(BudgetExceeded):
        budget.charge("busy")
        budget.charge("busy")


def test_number_of_tracked_executions_is_capped():
    budget = CallBudget(limit=1, max_tracked=2)
    budget.charge("a")
    budget.charge("b")
    budget.charge("c")  # "a" is the oldest and gets dropped

    assert budget.charge("a") == 0
    with pytest.raises(BudgetExceeded):
        budget.charge("c")


def test_a_full_book_never_resets_an_execution_already_in_it():
    # Regression: with the book full, charging the oldest tracked id used to evict it
    # first and so reset its count, a way past the budget.
    budget = CallBudget(limit=2, max_tracked=2)
    budget.charge("a")
    budget.charge("b")  # the book is now full; "a" is the oldest

    assert budget.charge("a") == 0  # still counted: second call
    with pytest.raises(BudgetExceeded):
        budget.charge("a")
