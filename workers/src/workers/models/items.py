import pathlib
import re
import shlex

from workers.config import WorkerConfig
from workers.parsers.movies import (
    FallbackMovieParser,
    MovieNameParser,
    StandardMovieParser,
    TrashMovieParser,
)

MOVIE_PARSERS: dict[str, MovieNameParser] = {
    "fallback": FallbackMovieParser,
    "standard": StandardMovieParser,
    "trash": TrashMovieParser,
}


class MediaStream:
    """Represents a media stream with metadata from ffprobe."""

    def __init__(
        self,
        index: int,
        codec_type: str,
        language: str | None = None,
        is_default: bool = False,
        is_original: bool = False,
    ) -> None:
        """Initialize a MediaStream.

        Args:
            index: The index of the stream.
            codec_type: The type of the stream (audio, subtitle, etc.).
            language: The language of the stream.
            is_default: Whether this stream is marked as default.
            is_original: Whether this stream is marked as original.
        """
        self.index = index
        self.codec_type = codec_type
        self.language = language or "und"  # Default to undefined if not specified
        self.is_default = is_default
        self.is_original = is_original

    @classmethod
    def from_ffprobe(cls, stream_data: dict) -> "MediaStream":
        """Create a MediaStream from ffprobe stream data.

        Args:
            stream_data: The stream data from ffprobe.

        Returns:
            A MediaStream instance.
        """
        # Extract basic info
        index = stream_data["index"]
        codec_type = stream_data["codec_type"]

        # Extract language from tags if available
        language = None
        if "tags" in stream_data and "language" in stream_data["tags"]:
            language = stream_data["tags"]["language"]

        # Extract disposition flags
        is_default = False
        is_original = False
        if "disposition" in stream_data:
            is_default = bool(stream_data["disposition"].get("default", 0))
            is_original = bool(stream_data["disposition"].get("original", 0))

        return cls(
            index=index,
            codec_type=codec_type,
            language=language,
            is_default=is_default,
            is_original=is_original,
        )


class MediaTrackCleanConfig:
    """Configuration for the media track cleaning service."""

    def __init__(
        self,
        keep_original: bool = True,
        keep_default: bool = True,
        keep_audio_langs: list[str] = None,
        keep_sub_langs: list[str] = None,
    ) -> None:
        """Initialize the configuration.

        Args:
            keep_original: Whether to keep original tracks.
            keep_default: Whether to keep default tracks.
            keep_audio_langs: Languages to keep for audio tracks.
            keep_sub_langs: Languages to keep for subtitle tracks.
        """
        self.keep_original = keep_original
        self.keep_default = keep_default
        self.keep_audio_langs = keep_audio_langs or []
        self.keep_sub_langs = keep_sub_langs or []

    @classmethod
    def from_dict(cls, config: dict) -> "MediaTrackCleanConfig":
        """Create a configuration from a dictionary.

        Args:
            config: The configuration dictionary.

        Returns:
            A MediaTrackCleanConfig instance.
        """
        return cls(
            keep_original=config.get("keep_original", True),
            keep_default=config.get("keep_default", True),
            keep_audio_langs=config.get("keep_audio_langs", []),
            keep_sub_langs=config.get("keep_sub_langs", []),
        )


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

        parser = MOVIE_PARSERS[parser_type]
        movie_attrs = cls.parse_movie_filename(file.name, parser)

        if not movie_attrs and parser_type != "fallback":
            # Fallback to the default parser if no attributes were found
            parser = MOVIE_PARSERS["fallback"]
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

        # If the specified parser didn't match, use the fallback parser
        if result is None:
            return FallbackMovieParser.parse(filename)

        return result
