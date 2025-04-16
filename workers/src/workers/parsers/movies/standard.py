import re

from workers.parsers.base import BaseParser


class StandardMovieParser(BaseParser):
    """Parser for standard movie naming convention: Title.Year.Quality.Source.etc."""

    @classmethod
    def parse(cls, filename: str) -> dict | None:
        """Parse a filename and return attributes or None if not matched.

        Args:
            filename (str): The filename to parse.

        Returns:
            dict | None: A dictionary with parsed attributes or None if not matched.
        """
        result = {}

        # Remove file extension
        name_no_ext = re.sub(r"\.\w+$", "", filename)

        # Replace dots with spaces for easier parsing
        clean_name = name_no_ext.replace(".", " ")

        # Try to find year
        year_match = re.search(r"\b(19|20)\d{2}\b", clean_name)
        if not year_match:
            return None

        year = year_match.group(0)
        result["year"] = year

        # Title is everything before the year
        title_parts = clean_name.split(year)[0].strip()
        result["title"] = title_parts.strip()

        # Try to extract IMDB ID (tt followed by digits)
        imdb_match = re.search(r"\b(tt\d{7,8})\b", clean_name)
        if imdb_match:
            result["imdb_id"] = imdb_match.group(1)

        # Try to extract TMDB ID
        tmdb_match = re.search(r"\btmdb[-:]?(\d+)\b", clean_name, re.IGNORECASE)
        if tmdb_match:
            result["tmdb_id"] = tmdb_match.group(1)

        # Try to extract quality
        quality_match = re.search(r"\b(720p|1080p|2160p|4K)\b", clean_name, re.IGNORECASE)
        if quality_match:
            result["quality"] = quality_match.group(0)

        # Try to extract common codecs
        codec_match = re.search(r"\b(x264|x265|HEVC|AVC|XVID)\b", clean_name, re.IGNORECASE)
        if codec_match:
            result["video_codec"] = codec_match.group(0)

        # Fix audio parsing to handle DTS-HD correctly
        audio_match = re.search(
            r"\b(DTS-HD|DTS|AC3|AAC|TrueHD|FLAC|MP3)\b", clean_name, re.IGNORECASE
        )
        if audio_match:
            result["audio"] = audio_match.group(0)

        # Add is_3d detection
        result["is_3d"] = bool(re.search(r"\b(3D|3-D)\b", clean_name, re.IGNORECASE))

        # Dynamic range
        dr_match = re.search(r"\b(DV|HDR|HDR10)\b", clean_name, re.IGNORECASE)
        if dr_match:
            result["dynamic_range"] = dr_match.group(0)

        # Release group - often at the end in brackets or after a dash
        release_match = re.search(r"[-\[]([A-Za-z0-9]+)[\]\s]*$", clean_name)
        if release_match:
            result["release_group"] = release_match.group(1)

        return result
