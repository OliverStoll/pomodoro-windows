import threading
from spotipy.oauth2 import SpotifyOAuth
from spotipy import Spotify as SpotifyAPI

from src._utils.logger import create_logger


# Set up Spotify client
class SpotifyHandler:
    def __init__(
        self, device_name: str, client_id: str, client_secret: str, redirect_uri: str, scope: str
    ):
        """Spotify client to interact with the Spotify API. CURRENTLY ONLY SUPPORTS play_playlist

        Takes a specific device name to play music on. Due to a bug, we cannot reuse the api as
        it blocks the playback from other devices. Therefore, we re-initialize the api
        for every request."""
        self.log = create_logger("Spotify")
        self.api = None
        self.device_name = device_name
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.scope = scope
        self.device_ids = self._get_device_ids()
        assert device_name in self.device_ids.keys(), f"Device '{device_name}' not found"

    def _initialize_api(self):
        """Initialise the Spotify API"""
        if not self.api:
            auth = SpotifyOAuth(
                self.client_id, self.client_secret, self.redirect_uri, scope=self.scope
            )
            self.api = SpotifyAPI(auth_manager=auth)

    def _delete_api(self):
        """Delete the Spotify API"""
        del self.api
        self.api = None

    def _get_device_ids(self):
        self._initialize_api()
        device_ids = {device["name"]: device["id"] for device in self.api.devices()["devices"]}
        self.log.info(f"Found devices: {list(device_ids.keys())} and selected {self.device_name}")
        self._delete_api()
        return device_ids

    def _search_track(self, track_name):
        """Search for a track via its name and return the first result"""
        results = self.api.search(q=f"track:{track_name}", type="track")
        items = results["tracks"]["items"]
        if len(items) > 0:
            return items[0]

    def get_current_playback(self):
        """Get the current track item that is playing, or None if nothing is playing"""
        current_playback = self.api.current_user_playing_track()
        if current_playback is None or current_playback["is_playing"] is False:
            return None
        else:
            return current_playback["item"]

    def _play_track(self, track_name: str):
        """Play a track using its name, by searching it and playing it"""
        self._initialize_api()
        self.log.debug(f"Playing track {track_name} on {self.device_name}")
        search_result = self._search_track(track_name)
        self.api.start_playback(  # type: ignore
            uris=search_result["uri"], device_id=self.device_ids[self.device_name]
        )

    def _play_playlist(self, playlist_uri: str):
        """Play a playlist using its uri"""
        self._initialize_api()
        self.log.debug(f"Playing playlist {playlist_uri} on {self.device_name}")
        if playlist_uri is None or playlist_uri == "":
            self.pause_playback()
            self._delete_api()
        else:
            try:
                self.api.start_playback(  # type: ignore
                    context_uri=playlist_uri, device_id=self.device_ids[self.device_name]
                )
            except Exception as e:
                self.log.error(f"Failed to play playlist: {e}")
            finally:
                self._delete_api()

    def play_playlist(self, playlist_uri: str):
        """Play a playlist using its uri in a new thread"""
        self._initialize_api()
        thread = threading.Thread(target=self._play_playlist, args=(playlist_uri,))
        thread.start()

    def pause_playback(self):
        """Pause playback when currently playing something"""
        try:
            if self.get_current_playback() is not None:
                self.api.pause_playback()
        except Exception as e:
            self.log.error(f"Failed to pause playback: {e}")

    def _toggle_playback(self):
        """Toggle playback (might behave unexpectedly if nothing is playing)"""
        self.log.debug("Toggling playback")
        if self.api.current_playback() is None:
            self.api.start_playback()
        else:
            self.api.pause_playback()


if __name__ == "__main__":
    pass
    # spotify = SpotifyHandler(device_name="PC", )
    # spotify.play_track(name=None)
    # spotify.toggle_playback()
    # spotify.play_playlist(playlist_uri=config["pause_playlist"])
