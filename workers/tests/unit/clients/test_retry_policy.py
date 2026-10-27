import pytest

from workers.clients.retry_policy import Disposition, decide
from workers.results import ProcessingResult


def test_success_is_acked_whatever_the_attempt_count():
    assert decide(ProcessingResult.success(), attempts=5, max_attempts=5) is Disposition.ACK


@pytest.mark.parametrize("attempts", [1, 2, 4])
def test_retryable_failure_retries_while_attempts_remain(attempts):
    result = ProcessingResult.retry("file not there yet")

    assert decide(result, attempts=attempts, max_attempts=5) is Disposition.RETRY


def test_retryable_failure_is_dead_lettered_on_the_last_attempt():
    result = ProcessingResult.retry("file never showed up")

    assert decide(result, attempts=5, max_attempts=5) is Disposition.DEAD


def test_permanent_failure_is_dead_lettered_immediately():
    result = ProcessingResult.failed("corrupt file")

    assert decide(result, attempts=1, max_attempts=5) is Disposition.DEAD


def test_single_attempt_budget_never_retries():
    assert decide(ProcessingResult.retry("x"), attempts=1, max_attempts=1) is Disposition.DEAD
