from typing import Protocol, runtime_checkable

from ..domain.models import ReviewItem


@runtime_checkable
class ReviewQueuePort(Protocol):
    def enqueue(self, item: ReviewItem) -> None:
        """Persist a review item so no failed scan is silently lost.

        Implementations must guarantee the item is durably recorded before
        returning.  Raises ReviewQueueError on failure.
        """
        ...
