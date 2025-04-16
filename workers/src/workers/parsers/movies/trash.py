import re

from workers.parsers.base import BaseParser


class TrashMovieParser(BaseParser):
    """Parser for the trash naming format."""

    @classmethod
    def parse(cls, filename: str) -> dict | None:
        """Parse a filename and return attributes or None if not matched.

        Args:
            filename (str): The filename to parse.

        Returns:
            dict | None: A dictionary with parsed attributes or None if not matched.
        """
        result = {}

        # Title and year
        title_year_match = re.match(r"(.+?)\s*\((\d{4})\)", filename)
        if not title_year_match:
            return None

        result["title"] = title_year_match.group(1).strip()
        result["year"] = title_year_match.group(2)

        # TMDB ID
        tmdb_match = re.search(r"\[tmdbid-(\d+)\]", filename)
        if tmdb_match:
            result["tmdb_id"] = tmdb_match.group(1)

        # IMDB ID - using standard format tt followed by digits
        imdb_match = re.search(r"\[imdbid-(tt\d+)\]", filename)
        if imdb_match:
            result["imdb_id"] = imdb_match.group(1)

        # Edition (fixed pattern to match after ids)
        edition_match = re.search(
            r"\[((?:tmdbid|imdbid)-[^]]+)\]\s*-\s*([^[{\]]+?)(?=\s*[\[{]|$)",
            filename,
        )
        if edition_match:
            result["edition"] = edition_match.group(2).strip()

        formats = []

        # Parse bracketed attributes
        attributes = re.findall(r"\[(.*?)\]", filename)
        result["is_3d"] = "3D" in attributes
        for attr in attributes:
            if attr.startswith("tmdbid-") or attr.startswith("imdbid-"):
                continue
            if any(codec in attr for codec in ["x264", "x265", "HEVC"]):
                result["video_codec"] = attr
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
        release_match = re.search(r"-([^\[\]\s]+)(?=\.mkv$)", filename)
        result["release_group"] = release_match.group(1) if release_match else None

        return result
