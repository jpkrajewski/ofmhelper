import pytest

from ofmhelpers.background.recovery import RESTARTS
from ofmhelpers.background.registry import JOB_TIMEOUTS, QUEUES, RESUMABLE
from ofmhelpers.web.exceptions import REFUSALS
from ofmhelpers.web.responses import NO_STORE, PRIVATE_FOR_A_DAY
from ofmhelpers.web.routers.registry import DOWNLOAD_TOOLS, GENERATE_TOOLS, TOOLS


@pytest.mark.parametrize(
    "constant",
    [
        NO_STORE,
        PRIVATE_FOR_A_DAY,
        GENERATE_TOOLS,
        DOWNLOAD_TOOLS,
        TOOLS,
        RESUMABLE,
        JOB_TIMEOUTS,
        QUEUES,
        REFUSALS,
        RESTARTS,
    ],
)
def test_a_shared_constant_refuses_a_write(constant):
    with pytest.raises(TypeError):
        constant["x"] = "y"
