# TEMP(M0): probe Lambda for the Monty experiments (docs/m0-findings.md, E3).
# Runs the code it receives in a fresh Monty session and reports what happened.
# Delete once the real runner Lambdas exist (M3).
import time

from pydantic_monty import CollectString, Monty, MontyError

# Module-level code runs once per container ("init" phase). The worker pool is
# started here so warm invocations reuse it. Lambda serves one request per
# container at a time, so one worker is enough. MONTY_BIN is set by Terraform.
_INIT_START = time.perf_counter()
_POOL = Monty(min_processes=1, max_processes=1).__enter__()
_POOL_START_MS = round((time.perf_counter() - _INIT_START) * 1000, 1)


def handler(event, context):
    stdout = CollectString()
    started = time.perf_counter()
    result = error = None
    try:
        # A new session per invocation: no state carries over between calls.
        with _POOL.checkout() as session:
            result = session.feed_run(event["code"], print_callback=stdout)
    except MontyError as e:
        error = {"type": type(e).__name__, "message": str(e)}
    return {
        "result": repr(result),
        "stdout": stdout.output,
        "error": error,
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        "pool_start_ms": _POOL_START_MS,
    }
