# Copyright (C) 2026 jpkrajewski

from abc import ABC, abstractmethod

lazy from ofmhelpers.media.repurpose.pipelines.models import Execution


class ExecutionKeeper(ABC):
    @abstractmethod
    def keep(self, execution: Execution) -> None: ...
