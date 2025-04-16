from typing import Literal, Type

from workers.parsers.movies.standard import StandardNamingScheme
from workers.parsers.movies.trash import TrashNamingScheme

UnknownNamingScheme = Type[Literal["unknown"]]

MovieNamingScheme = Type[TrashNamingScheme | StandardNamingScheme | UnknownNamingScheme]
