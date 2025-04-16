import pathlib
import re
import shlex

from workers.config import WorkerConfig
from workers.parsers.movies import MovieNameParser, StandardMovieParser, TrashMovieParser

MOVIE_PARSERS: dict[str, MovieNameParser] = {
    "standard": StandardMovieParser,
    "trash": TrashMovieParser,
}


class Movie:
    """A class to represent a movie."""

    def __init__(
        self,
        file: pathlib.Path,
        title: str,
        year: str,
        tmdb_id: str | None = None,
        imdb_id: str | None = None,
        edition: str | None = None,
        video_codec: str | None = None,
        audio: str | None = None,
        quality: str | None = None,
        format_: str | None = None,
        dynamic_range: str | None = None,
        release_group: str | None = None,
        is_3d: bool = False,
    ) -> None:
        """Initialize the Movie object."""
        self._file = file
        self.title = title
        self.year = year
        self.tmdb_id = tmdb_id
        self.imdb_id = imdb_id
        self.edition = edition
        self.video_codec = video_codec
        self.audio = audio
        self.quality = quality
        self.format_ = format_
        self.dynamic_range = dynamic_range
        self.release_group = release_group
        self.is_3d = is_3d

    @property
    def full_title(self) -> str:
        """Return the full title of the movie."""
        return f"{self.title} ({self.year})"

    @property
    def folder_title(self) -> str:
        """Return the folder title."""
        return self.full_title.replace(":", " -")

    @property
    def full_path(self) -> str:
        """Return the full path to the movie file."""
        return str(self._file.resolve())

    @property
    def escaped_path(self) -> str:
        """Return the escaped path of the movie file.

        Returns:
            str: The escaped path of the movie file.
        """
        return shlex.quote(self.full_path)

    @property
    def escaped_file_name(self) -> str:
        """Return the escaped file name."""
        return re.escape(str(self._file))

    def delete(self) -> None:
        """Delete the movie file."""
        self._file.unlink()

    @classmethod
    def from_file(cls, file: pathlib.Path) -> "Movie":
        """Create a Movie object from a file.

        Args:
            file (pathlib.Path): The movie file.

        Returns:
            Movie: The Movie object.
        """
        worker_config = WorkerConfig()
        parser_type = worker_config.get_naming_scheme("movie")

        try:
            parser = MOVIE_PARSERS[parser_type]
        except KeyError as err:
            raise ValueError(f"Unknown parser type '{parser_type}'.") from err

        movie_attrs = cls.parse_movie_filename(file.name, parser)
        return cls(file, **movie_attrs)

    @classmethod
    def parse_movie_filename(cls, filename: str, parser: MovieNameParser) -> dict:
        """Parse a movie filename using registered parsers.

        Args:
            filename (str): The filename to parse.
            parser (MovieNameParser): The parser to use.

        Returns:
            dict: The parsed attributes
        """
        result = parser.parse(filename)
        if result is not None:
            return result

        # Fallback - extract minimal information
        fallback = {}
        # At minimum, we need a title and year
        year_match = re.search(r"\b(19|20)\d{2}\b", filename)
        if year_match:
            fallback["year"] = year_match.group(0)
            # Find how the year is wrapped (in parentheses, brackets, etc.)
            year_pos = filename.find(fallback["year"])
            # Check characters before the year for brackets/parentheses
            if year_pos > 0 and filename[year_pos - 1] in "([":
                name_parts = filename[: year_pos - 1].strip()
            else:
                name_parts = filename.split(fallback["year"], 1)[0].strip()

            # Clean up the title - preserve underscores, replace dots with spaces
            fallback["title"] = re.sub(r"(?<!_)\.(?!_)", " ", name_parts).strip()
        else:
            # No year found, use filename without extension as title
            fallback["title"] = re.sub(r"\.\w+$", "", filename).replace(".", " ").strip()
            fallback["year"] = ""

        return fallback
