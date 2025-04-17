from typing import Type

from workers.parsers.movies.fallback import FallbackMovieParser
from workers.parsers.movies.standard import StandardMovieParser
from workers.parsers.movies.trash import TrashMovieParser

MovieNameParser = Type[FallbackMovieParser | StandardMovieParser | TrashMovieParser]

__all__ = [
    "MovieNameParser",
    "FallbackMovieParser",
    "StandardMovieParser",
    "TrashMovieParser",
]
