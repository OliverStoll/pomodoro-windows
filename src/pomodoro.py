# fix working directory if running from a pyinstaller executable
import src._utils.distribution.pyinstaller_fix  # noqa

# global
import threading
import pystray
from pystray import Menu, MenuItem as Item
from datetime import datetime
from time import sleep
from threading import Thread
from pprint import pformat

# local
from src.systray.utils import draw_icon_text, draw_icon_circle, trigger_webhook
from src._utils.distribution.file_printout import print_root_files
from src._utils.common import config
from src._utils.common import secret, ROOT_DIR
from src._utils.logger import create_logger
from src._utils.apis.spotify import SpotifyHandler
from src._utils.apis.firebase import FirebaseHandler
from src._utils.system.sound import play_sound
from src._utils.system.programs_windows import WindowHandler
from src.ticktick_habits import TicktickHabitApi


class State:
    """Class to represent the current state of the timer. Loads corresponding color and webhook from config."""

    WORK = config["states"]["WORK"]
    PAUSE = config["states"]["PAUSE"]
    READY = config["states"]["READY"]
    DONE = config["states"]["DONE"]
    STARTING = config["states"]["STARTING"]


class PomodoroApp:
    def __init__(
        self, firebase_rdb_url: str | None = None, spotify_info: dict[str, str] | None = None
    ):
        self.log = create_logger("Pomodoro Timer")
        self.name = config["app_name"]
        self.firebase_time_done_ref = config["FIREBASE_TIME_DONE_REF"]
        self._update_icon_lock = threading.Lock()
        self._init_stub()
        # try load real settings
        self.firebase = FirebaseHandler(realtime_db_url=firebase_rdb_url)
        self.habit_handler = TicktickHabitApi(cookies_path=f"{ROOT_DIR}/.ticktick_cookies")
        self._load_settings()
        # apis
        self.window_handler = WindowHandler()
        self.spotify = self._init_spotify(spotify_info)
        # todo: add homeassistant handler
        # timer values
        self.state = State.READY
        today = datetime.now().strftime("%Y-%m-%d")
        time_worked_ref = f"{self.firebase_time_done_ref}/{today}/time_worked"
        try:
            self.time_worked = int(self.firebase.get_entry(ref=time_worked_ref))
        except Exception as e:
            self.log.warning(f"Can't load {today}: time_worked from firebase, setting to 0 [{e}]")
            self.time_worked = 0
        self.time_worked_date = today
        self.work_timer_duration = self.settings["work_timer"]
        self.daily_work_time_goal = self.settings["daily_work_time_goal"]
        self.pause_timer_duration = self.settings["pause_timer"]
        self.current_timer_value = self.work_timer_duration
        # systray app
        self.stop_timer_thread_flag = False
        self.timer_thread = None
        self.update_icon()
        self.update_menu()
        self.log.info("Pomodoro Timer initialised.")

    def _init_stub(self):
        self.settings = config["default_settings"]
        self.state = State.STARTING
        self.systray_app = pystray.Icon(self.name)
        self.time_worked = 0
        self.work_timer_duration = 60
        self.spotify = None
        self.update_icon()
        self.run()

    def _load_settings(self):
        self.setting_ref = config["settings_reference"]
        try:
            self.settings = self.firebase.get_entry(ref=self.setting_ref)
            assert isinstance(self.settings, dict)
            self.log.info(f"Loaded settings from firebase: \n{pformat(self.settings)}")
        except Exception as e:
            self.log.warning(
                f"Can't load settings from firebase: [{e}], "
                f"using default settings {pformat(config['default_settings'])}."
            )
            self.settings = config["default_settings"]
            try:
                self.firebase.set_entry(ref=self.setting_ref, data=self.settings)
            except Exception as e:
                self.log.warn(f"Could not save default settings to Firebase: {e}")

    def _init_spotify(self, spotify_info):
        try:
            return SpotifyHandler(**spotify_info)
        except Exception as e:
            self.log.warning(f"Can't connect to Spotify [{e}]")
            self.settings["Spotify"] = False
            return None

    def update_menu(self):
        def _get_feature_setting_item(feature, enabled=True):
            return Item(
                feature,
                lambda: self._toggle_feature_setting(feature),
                checked=lambda item: self.settings[feature],
                enabled=enabled,
            )

        step_size = self.settings["step_size"]
        self.systray_app.menu = Menu(
            Item(
                "Start",
                self.menu_button_start,
                default=(self.state == State.READY),
                enabled=lambda item: self.state != State.WORK,
            ),
            Item(
                "Stop",
                self.menu_button_stop,
                default=(self.state == State.WORK),
                enabled=lambda item: self.state != State.READY and self.state != State.DONE,
            ),
            Item(
                "Settings",
                Menu(
                    Item(
                        f"Worked {self.time_worked / self.work_timer_duration:.1f} blocks",
                        action=None,
                    ),
                    pystray.Menu.SEPARATOR,
                    Item(
                        f"Work +{step_size}", lambda: self._change_timer_setting("WORK", step_size)
                    ),
                    Item(
                        f"Work -{step_size}", lambda: self._change_timer_setting("WORK", -step_size)
                    ),
                    Item(
                        f"Pause +{step_size}",
                        lambda: self._change_timer_setting("PAUSE", step_size),
                    ),
                    Item(
                        f"Pause -{step_size}",
                        lambda: self._change_timer_setting("PAUSE", -step_size),
                    ),
                    pystray.Menu.SEPARATOR,
                    _get_feature_setting_item("Hide Windows"),
                    _get_feature_setting_item(
                        "Spotify", enabled=lambda item: self.spotify is not None
                    ),
                    _get_feature_setting_item("Home Assistant"),
                    pystray.Menu.SEPARATOR,
                    Item("Exit", self.exit, enabled=True),
                ),
            ),
        )

    def update_icon(self):
        with self._update_icon_lock:
            self.update_menu()
            if self.state in [State.DONE, State.STARTING]:
                self.systray_app.icon = draw_icon_circle(color=self.state["color"])
            else:
                self.systray_app.icon = draw_icon_text(
                    text=str(self.current_timer_value), color=self.state["color"]
                )

    def _change_timer_setting(self, timer, value):
        assert timer in ["WORK", "PAUSE"]
        if (timer == "WORK" and self.state != State.PAUSE) or (
            timer == "PAUSE" and self.state == State.PAUSE
        ):
            self.current_timer_value += value
            self.update_icon()
        if timer == "WORK":
            self.work_timer_duration += value
            self.firebase.update_value(
                ref=self.setting_ref, key="work_timer", value=self.work_timer_duration
            )
        elif timer == "PAUSE":
            self.pause_timer_duration += value
            self.firebase.update_value(
                ref=self.setting_ref, key="pause_timer", value=self.pause_timer_duration
            )

    def _toggle_feature_setting(self, feature_name):
        self.settings[feature_name] = not self.settings[feature_name]
        self.firebase.update_value(
            ref=self.setting_ref, key=feature_name, value=self.settings[feature_name]
        )

    def run(self):
        self.systray_app.run_detached()

    def exit(self):
        """Function that is called when the exit button is pressed. Sets the stop_timer_thread_flag for the timer thread."""
        self.log.info("Exiting Pomodoro Timer")
        self.stop_timer_thread_flag = True
        sleep(0.11)
        self.systray_app.stop()

    def menu_button_start(self):
        """Function that is called when the start button is pressed"""
        self.log.info("Menu Start pressed.")
        self.state = State.WORK
        # self.update_icon()
        play_sound(config["sounds"]["start"], volume=self.settings["VOLUME"])
        if self.spotify and self.settings["Spotify"]:
            self.spotify.play_playlist_thread(playlist_uri=config["work_playlist"])
        if self.settings["Home Assistant"]:
            trigger_webhook(url=self.state["webhook"])
        self.timer_thread = Thread(target=self.run_timer)
        self.timer_thread.start()
        self.update_icon()

    def menu_button_stop(self):
        """Function that is called when the stop button is pressed"""
        self.log.info("Menu Stop pressed.")
        self.state = State.READY
        self.update_icon()
        self.stop_timer_thread_flag = True
        # features
        if self.spotify and self.settings["Spotify"]:
            self.spotify.play_playlist_thread(playlist_uri=config["pause_playlist"])
        if self.settings["Home Assistant"]:
            trigger_webhook(url=self.state["webhook"])
        self.update_icon()
        # Thread.join(self.timer_thread)  # should terminate within 0.1s

    def _switch_to_next_state(self):
        """Switch to the next state and update the icon."""
        if self.state == State.WORK:
            self.state = State.PAUSE
            self.current_timer_value = self.pause_timer_duration
            play_sound(config["sounds"]["pause"])
            if self.settings["Hide Windows"]:
                sleep(0.5)
                self.window_handler.minimize_open_windows()
            if self.spotify and self.settings["Spotify"]:
                sleep(1)
                self.spotify.play_playlist_thread(playlist_uri=config["pause_playlist"])
        elif self.state == State.PAUSE and self.time_worked < self.daily_work_time_goal:
            self.state = State.READY
            self.current_timer_value = self.work_timer_duration
            if self.settings["Hide Windows"]:
                self.window_handler.restore_windows()
        elif self.state == State.PAUSE and self.time_worked >= self.daily_work_time_goal:
            self.state = State.DONE
            self.current_timer_value = self.work_timer_duration

        # reset the timer, update the icon and trigger webhook
        self.update_icon()
        if self.settings["Home Assistant"]:
            trigger_webhook(url=self.state["webhook"])
        if self.state == State.PAUSE:
            self.run_timer()

    def _increase_time_worked(self):
        """Increase the time_worked counter and update it in firebase"""
        current_date = datetime.now().strftime("%Y-%m-%d")
        if self.time_worked_date != current_date:
            self.log.info(f"New day: {current_date}. Resetting time_done.")
            self.time_worked_date = current_date
            self.time_worked = 1
        else:
            self.time_worked += 1
        ref = f"{self.firebase_time_done_ref}/{current_date}"
        self.firebase.update_value(ref=ref, key="time_worked", value=self.time_worked)

    def run_timer(self):
        """Function that runs the timer of the Pomodoro App (in a separate thread).

        Every 0.1s the stop_timer_thread_flag is checked if the timer was stopped. Otherwise, every minute the time and
        the icon is updated. If the timer is done, the next state is switched to.
        """
        self.update_icon()
        while self.current_timer_value > 0:
            for i in range(600):
                sleep(0.1)
                if self.stop_timer_thread_flag:
                    self.state = State.READY
                    self.current_timer_value = self.work_timer_duration
                    self.update_icon()
                    self.stop_timer_thread_flag = False
                    return
            self.current_timer_value -= 1
            if self.state == State.WORK:
                self._increase_time_worked()
            if self.time_worked == self.daily_work_time_goal:
                try:
                    self.habit_handler.checkin_simple(
                        config["HABIT_NAME"], datetime.now().strftime("%Y%m%d"), 2
                    )
                    self.log.info("Daily work goal reached. Marked habit as completed.")
                except Exception as e:
                    self.log.warning(f"Could not check-in habit: {e}")
            self.update_icon()
        if self.current_timer_value == 0:
            self.log.info("Timer done. Switching to next state.")
            self._switch_to_next_state()


if __name__ == "__main__":
    print_root_files()
    _firebase_db_url = secret("FIREBASE_DB_URL")
    _spotify_info = {
        "device_name": secret("SPOTIFY_DEVICE_NAME"),
        "client_id": secret("SPOTIFY_CLIENT_ID"),
        "client_secret": secret("SPOTIFY_CLIENT_SECRET"),
        "redirect_uri": config["SPOTIFY"]["redirect_uri"],
        "scope": config["SPOTIFY"]["scope"],
    }
    pomo_app = PomodoroApp(firebase_rdb_url=_firebase_db_url, spotify_info=_spotify_info)
