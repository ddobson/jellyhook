from typing import Type

from workers.parsers.movies.standard import StandardMovieParser
from workers.parsers.movies.trash import TrashMovieParser

MovieNameParser = Type[StandardMovieParser | TrashMovieParser]

__all__ = [
    "MovieNameParser",
    "StandardMovieParser",
    "TrashMovieParser",
]
