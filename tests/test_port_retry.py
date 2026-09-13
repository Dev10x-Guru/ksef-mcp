from datetime import timedelta

import pytest

from ksef_mcp.ksef_port import NO_AUTOMATIC_RETRY, KsefRateLimited, RetryPolicy


class Refusing:
    def __init__(self, *, times: int, retry_after: int | None) -> None:
        self.left = times
        self.retry_after = retry_after
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        if self.left > 0:
            self.left -= 1
            raise KsefRateLimited("limit", retry_after=self.retry_after)
        return "answered"


class Recorder:
    def __init__(self) -> None:
        self.waits: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def patient(recorder: Recorder) -> RetryPolicy:
    return RetryPolicy(attempts=3, max_wait=timedelta(minutes=10), sleep=recorder)


def test_a_call_that_works_is_made_once() -> None:
    call = Refusing(times=0, retry_after=None)

    assert NO_AUTOMATIC_RETRY.run(call) == "answered"
    assert call.calls == 1


def test_the_default_policy_hands_a_refusal_straight_back() -> None:
    call = Refusing(times=1, retry_after=60)

    with pytest.raises(KsefRateLimited):
        NO_AUTOMATIC_RETRY.run(call)

    assert call.calls == 1


def test_the_wait_is_the_one_ksef_asked_for_not_a_backoff_of_ours(
    patient: RetryPolicy,
    recorder: Recorder,
) -> None:
    patient.run(Refusing(times=1, retry_after=180))

    assert recorder.waits == [180]


def test_a_retry_returns_the_answer_it_waited_for(patient: RetryPolicy) -> None:
    assert patient.run(Refusing(times=2, retry_after=30)) == "answered"


def test_attempts_run_out_rather_than_hammering(patient: RetryPolicy) -> None:
    call = Refusing(times=3, retry_after=30)

    with pytest.raises(KsefRateLimited):
        patient.run(call)

    assert call.calls == 3


def test_a_refusal_without_retry_after_is_never_guessed_at(
    patient: RetryPolicy,
    recorder: Recorder,
) -> None:
    with pytest.raises(KsefRateLimited):
        patient.run(Refusing(times=1, retry_after=None))

    assert recorder.waits == []


def test_a_wait_longer_than_the_policy_allows_goes_to_the_person(
    patient: RetryPolicy,
    recorder: Recorder,
) -> None:
    with pytest.raises(KsefRateLimited):
        patient.run(Refusing(times=1, retry_after=601))

    assert recorder.waits == []
