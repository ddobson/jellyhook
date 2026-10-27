from enum import StrEnum

from workers.results import Outcome, ProcessingResult


class Disposition(StrEnum):
    """What the consumer does with a message once processing has finished."""

    ACK = "ack"  # Done, drop it from the queue
    RETRY = "retry"  # Park it on the retry queue; it returns after a delay
    DEAD = "dead"  # Park it on the dead-letter queue for a human to look at


def decide(result: ProcessingResult, attempts: int, max_attempts: int) -> Disposition:
    """Choose what happens to a message after a processing attempt.

    Args:
        result (ProcessingResult): The outcome of the attempt that just finished.
        attempts (int): How many attempts have been made so far, including this one.
        max_attempts (int): The total number of attempts allowed before giving up.

    Returns:
        Disposition: ACK on success, RETRY while attempts remain for a retryable failure,
            otherwise DEAD.
    """
    if result.outcome is Outcome.SUCCESS:
        return Disposition.ACK
    if result.outcome is Outcome.RETRY and attempts < max_attempts:
        return Disposition.RETRY
    return Disposition.DEAD
