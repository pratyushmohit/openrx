# TEMP(M0): hello-world Lambda used to prove the Terraform -> Floci -> Docker
# path works. Delete once the real runner Lambdas exist (M3).
import platform
import time

# Module-level code runs once per container (the "init" phase), not per call,
# so this timestamp shows whether an invoke reused a warm container.
_CONTAINER_STARTED_AT = time.time()


def handler(event, context):
    return {
        "message": "hello from openrx",
        "python": platform.python_version(),
        "received": event,
        "container_age_s": round(time.time() - _CONTAINER_STARTED_AT, 3),
    }
