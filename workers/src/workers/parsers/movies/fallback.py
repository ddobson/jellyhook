import re
from typing import Literal

from typing_extensions import override

from workers.parsers.base import BaseParser

FallbackNamingScheme = Literal["fallback"]


class FallbackMovieParser(BaseParser):
    """Parser for movie filenames that do not match any known format."""

    @override
    @classmethod
    def parse(cls, filename: str) -> dict:
        """Fallback parser for movie filenames that do not match any known format.

        This method overrides the parse method of the BaseParser class to always return a dict.

        Args:
            filename (str): The filename to parse.

        Returns:
            dict: A dictionary with parsed attributes or an empty dict if not matched.
        """
        # Extract minimal information
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
