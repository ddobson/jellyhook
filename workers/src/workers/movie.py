import pathlib
import re
import shlex


class Movie:
    """A class to represent a movie."""

    def __init__(
        self,
        file: pathlib.Path,
        title: str,
        year: str,
        tmdb_id: str | None = None,
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

    @classmethod
    def from_file(cls, file: pathlib.Path) -> "Movie":
        """Create a Movie object from a file.

        Args:
            file (pathlib.Path): The movie file.

        Returns:
            Movie: The Movie object.
        """
        movie_attrs = cls.parse_movie_filename(file.name)
        return cls(file, **movie_attrs)

    @staticmethod
    def parse_movie_filename(filename: str) -> dict:
        """Parse a movie filename and extract attributes.

        Args:
            filename (str): The filename to parse.

        Returns:
            dict: The parsed attributes
        """
        import re

        result = {}

        # Title and year
        title_year_match = re.match(r"(.+?)\s*\((\d{4})\)", filename)
        if title_year_match:
            result["title"] = title_year_match.group(1).strip()
            result["year"] = title_year_match.group(2)

        # TMDB ID
        tmdb_match = re.search(r"\[tmdbid-(\d+)\]", filename)
        if tmdb_match:
            result["tmdb_id"] = tmdb_match.group(1)

        # Edition (fixed pattern to match after tmdbid)
        edition_match = re.search(
            r"\[tmdbid-\d+\]\s*-\s*([^[{\]]+?)(?=\s*[\[{]|$)",
            filename,
        )
        if edition_match:
            result["edition"] = edition_match.group(1).strip()

        formats = []

        # Parse bracketed attributes
        attributes = re.findall(r"\[(.*?)\]", filename)
        for attr in attributes:
            if attr.startswith("tmdbid-"):
                continue

            if any(codec in attr for codec in ["x264", "x265", "HEVC"]):
                result["video_codec"] = attr
            elif attr == "3D":
                result["is_3d"] = True
            elif any(dr in attr for dr in ["DV", "HDR", "HDR10"]):
                result["dynamic_range"] = attr
            elif any(audio in attr for audio in ["DTS", "AC3", "AAC", "TrueHD", "DTS-HD"]):
                result["audio"] = attr
            elif any(q in attr for q in ["p", "Bluray", "Remux"]):
                result["quality"] = attr
            elif "IMAX" in attr or "HYBRID" in attr:
                formats.append(attr)

        if formats:
            result["format_"] = " ".join(formats)

        # Release group
        release_match = re.search(r"-([^-\s]+?)(?:\.mkv)?$", filename)
        if release_match:
            result["release_group"] = release_match.group(1)

        return result

    def delete(self) -> None:
        """Delete the movie file."""
        self._file.unlink()
