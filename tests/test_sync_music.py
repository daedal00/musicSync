import json
import tempfile
import unittest
from pathlib import Path

import sync_music


class MatchingTests(unittest.TestCase):
    def test_normalize_removes_feature_and_punctuation(self):
        self.assertEqual(sync_music.normalize("Song Title (feat. Someone) - Remastered!"), "song title")

    def test_best_match_uses_title_artist_and_duration(self):
        source = sync_music.Track("Midnight City", ("M83",), 244)
        candidates = [
            sync_music.Track("Midnight Train", ("Other",), 244),
            sync_music.Track("Midnight City", ("M83",), 245),
        ]
        self.assertEqual(sync_music.best_match(source, candidates, 0.72), candidates[1])

    def test_extract_headers_from_curl(self):
        raw = "curl 'https://music.youtube.com/youtubei/v1/browse' -H 'x-goog-authuser: 0' -H 'cookie: SID=abc'"
        self.assertEqual(sync_music.extract_headers_from_curl(raw), "x-goog-authuser: 0\ncookie: SID=abc")

    def test_youtube_music_playlist_url_is_normalized_to_list_id(self):
        url = "https://music.youtube.com/playlist?list=PLabc123&jct=invite-token"
        self.assertEqual(sync_music.youtube_music_playlist_id(url), "PLabc123")

    def test_spotify_playlist_url_is_normalized_to_id(self):
        url = "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc"
        self.assertEqual(sync_music.spotify_playlist_id(url), "37i9dQZF1DXcBWIGoYBM5M")

    def test_config_blocks_bidirectional_deletes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({
                "direction": "bidirectional",
                "spotify_playlist_id": "spotify-playlist",
                "youtube_music_playlist_id": "youtube-playlist",
                "spotify_client_id": "client-id",
                "spotify_client_secret": "client-secret",
                "delete_missing": True,
            }))
            with self.assertRaises(SystemExit):
                sync_music.load_config(path)


if __name__ == "__main__":
    unittest.main()
