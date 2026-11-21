from pydantic import BaseModel


class Health(BaseModel):
    """
    Health check response body
    """

    version: str
    status: str
    # v0.16.37 (review P-8): honest sandbox reporting. landlock_abi is the
    # kernel's reported ABI; landlock_net_rules_enforced is the probe result —
    # on Docker Desktop the ABI reports but net rules don't apply, and
    # deployers must be able to see that. audit_write_failures counts audit
    # events lost to write failures (fail-open logging must be visible).
    landlock_abi: int | None = None
    landlock_net_rules_enforced: bool | None = None
    audit_write_failures: int | None = None
