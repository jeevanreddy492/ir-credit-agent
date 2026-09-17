import pytest
from pydantic import ValidationError

from app.api.schemas import ActionDecision


def test_only_approve_or_reject():
    with pytest.raises(ValidationError):
        ActionDecision(decided_by="ops", decision="MAYBE")
    assert ActionDecision(decided_by="ops", decision="REJECT").decision == "REJECT"
