from typing import Protocol


class RefreshPublisher(Protocol):
    """Sends a Refresh message for one Random Playlist to the worker."""

    def publish(self, playlist_name: str) -> None:
        """Publish a Refresh for `playlist_name` and wait for the broker's confirm.

        Args:
            playlist_name (str): The Random Playlist's name, the only thing the message carries.

        Raises:
            Exception: Any failure, including a missing or negative confirm. The scheduler treats
                it as fatal: nothing is recorded and the process should exit non-zero.
        """
