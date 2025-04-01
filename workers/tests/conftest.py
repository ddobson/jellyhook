import pytest


@pytest.fixture
def media_dir(tmpdir):
    """Mocks a media directory."""
    # Create movie directory structure
    movie_dir = tmpdir.mkdir("movies")
    movie_folder = movie_dir.mkdir("Test Movie (2023)")
    standup_dir = tmpdir.mkdir("standup")
    standup_folder = standup_dir.mkdir("Comedy Special (2022)")
    tmp_path = tmpdir.mkdir("tmp")

    # Create movie files
    movie_file = movie_folder.join("Test Movie.mkv")
    movie_file.write("movie content")

    standup_file = standup_folder.join("Comedy Special.mp4")
    standup_file.write("standup content")

    # Setup environment
    return {
        "movie_path": str(movie_dir),
        "standup_path": str(standup_dir),
        "movie_file": str(movie_file),
        "standup_file": str(standup_file),
        "tmp_path": str(tmp_path),
    }
