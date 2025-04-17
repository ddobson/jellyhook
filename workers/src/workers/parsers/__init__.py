from workers.parsers.movies.fallback import FallbackNamingScheme
from workers.parsers.movies.standard import StandardNamingScheme
from workers.parsers.movies.trash import TrashNamingScheme

MovieNamingScheme = FallbackNamingScheme | TrashNamingScheme | StandardNamingScheme


__all__ = [
    "MovieNamingScheme",
    "FallbackNamingScheme",
    "TrashNamingScheme",
    "StandardNamingScheme",
]
