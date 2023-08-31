"""Classes for channel operation and VOD analysis."""
# pylint: disable=multiple-statements,invalid-name,import-outside-toplevel
from collections import defaultdict, UserDict
from dataclasses import dataclass, field
from itertools import chain
from math import sqrt
import os
import re
from statistics import mean
from string import printable
from time import strftime, localtime, time
from typing import Literal, Any

from chat_downloader import ChatDownloader
# https://chat-downloader.readthedocs.io/en/latest/source/chat_downloader.html
import matplotlib.pyplot as plt
import numpy as np

from managers import (MAX_PLACEMENT, is_subscription_message, EmoteManager,
                      PresetManager, VODManager, PresetError, VODError,
                      VOD_MESSAGE_PATTERN, DURATION_PATTERN, sort_dict, dict_placements)
from webhook import Webhook


COMMAND_MESSAGE_PATTERN = re.compile(r"^!\w+")
MINUTE_S = 60
HOUR_S = 60 * MINUTE_S


class Duration:
    """Handle numerical data for a time duration."""

    def __init__(self, seconds: int | float):
        self.total_seconds: float = float(seconds)
        quotients = self.divmods(float(seconds), hour=3600, minute=60, second=1)
        self.hours = int(quotients["hour"])
        self.minutes = int(quotients["minute"])
        self.seconds = int(quotients["second"])
        self.ms = int(round(quotients["remainder"]*1000))

    def as_timestamp(self, include_ms: bool = True) -> str:
        """Create a formatted timestamp string."""
        timestamp = ':'.join(
            tuple(str(num).zfill(2)
                  for num in (self.hours, self.minutes, self.seconds))
            )
        if include_ms:
            timestamp += f".{str(self.ms).zfill(3)}"
        return timestamp

    @staticmethod
    def divmods(dividend: int | float, **divisors: int | float) -> dict[str, int | float]:
        """Chain divmod calculations together with multiple divisors."""
        divisors = sort_dict(divisors, key=1, reverse=True)
        quotients: dict[str, int | float] = {}
        remainder = float(dividend)
        for divisor_name, divisor in divisors.items():
            quotient, remainder = divmod(remainder, float(divisor))
            quotients[divisor_name] = int(quotient)
        quotients["remainder"] = float(remainder)
        return quotients


def write_logs(directory: str, channel: str, vod_id: str):
    """Create a new logs file for a VOD."""
    def is_complete_message(msg: dict) -> bool:
        """Check whether a message has all necessary parts for writing to logs."""
        return "message" in msg and "time_in_seconds" in msg and "name" in msg["author"]

    chat = ChatDownloader().get_chat(f"https://www.twitch.tv/videos/{vod_id}")
    with open(f"{directory}/{channel}/{vod_id}.txt",
                'a', encoding='UTF-8', errors='replace') as file:
        msg_i = 0
        for msg in chat:  # type: ignore
            msg_i += 1
            if not is_complete_message(msg): continue
            vod_time = Duration(float(msg["time_in_seconds"]))
            timestamp = vod_time.as_timestamp()
            file.write(f'[{timestamp}] {msg["author"]["name"]}: {msg["message"]}\n')
            if not msg_i % 100:
                print(f"Writing logs in {channel}/{vod_id}.txt [{timestamp}]", end='\r')


@dataclass(slots=True)
class GraphParams:
    """Parameters for VOD chart generation."""
    minor_tick: int
    major_range: np.ndarray
    minor_range: np.ndarray


@dataclass(slots=True)
class DataParams:
    """Parameters for VOD analysis."""
    bots: set[str]
    interval: int
    minimum: int
    spacing: int
    msg_results: int
    txt_results: int


@dataclass(slots=True)
class OutputParams:
    """Parameters for output of analysis results."""
    to_discord_msg: bool
    to_discord_txt: bool
    to_discord_graph: bool
    condense: bool
    extend: bool
    aggregate: bool


@dataclass(slots=True)
class Params:
    """All parameters for handling VODs."""
    data: DataParams
    output: OutputParams
    allow_non_api: bool


@dataclass(slots=True)
class Current:
    """Base data for the current VOD search."""
    vod_id: str = ''
    category: str = ''
    preset_name: str = ''
    queries: list[str | list[str]] = field(default_factory=list)
    file_outputs: list[str] = field(default_factory=list)
    end_s: int = 0


class AnalyticsDict(UserDict):
    """Store and manipulate data from VOD analysis results."""

    def filter(self, minimum: int | float, spacing: int | float):
        """Trim VOD analysis data according to specified parameters."""
        data = sort_dict(self.data, key=1, reverse=True)
        data = self._prune(data, minimum)
        self.data = self._space(data, spacing)

    @staticmethod
    def _prune(dictionary: dict[Any, int | float],
               minimum: int | float) -> dict[Any, int | float]:
        """Remove dictionary entries with values below a minimum threshold."""
        return {key: value for key, value in dictionary.items() if value >= minimum}

    @staticmethod
    def _space(dictionary: dict[Any, int | float],
               spacing: int | float) -> dict[Any, int | float]:
        """
        Remove dictionary entries whose keys are numerically
        too close to other keys with higher values.\n
        The dictionary should initially be reverse-sorted by value.
        """
        new_dict = {}
        for key, value in dictionary.items():
            if isinstance(key, int):
                if all(abs(key - new_key) >= spacing for new_key in new_dict):
                    new_dict[key] = value
            elif isinstance(key, tuple):
                # for collective spam, only space timestamps among identical messages
                time_s, message = key
                for new_key in new_dict:
                    if not isinstance(new_key, tuple): continue
                    new_time_s, new_message = new_key
                    if message == new_message and abs(time_s-new_time_s) < spacing: break
                else:
                    new_dict[key] = value
        return new_dict


class AnalyticsChannel:
    """Handle all VOD data and operations for a Twitch channel."""
    default_presets = {
        'all': "all messages",
        'collective': "collective spam",
        'users': "unique users",
        'spam': "spam score",
        'unique': "unique message score",
        'regulars': "regulars score",
        'caps': "caps",
        'caps-only': "caps only",
        'emote': "emote score",
        'word': "word score"
    }

    def __init__(self, name: str, directory: str, webhook: Webhook, params: Params):
        self.name = name
        self.directory = directory
        if not os.path.isdir(f"{self.directory}/{self.name}"):
            os.mkdir(f"{self.directory}/{self.name}")
        self.webhook = webhook
        self.params = params
        self.vod_manager = VODManager(self.name, self.directory)
        self.emote_manager = EmoteManager(self.name, self.directory)
        self.preset_manager = PresetManager(
            self.name,
            self.directory,
            self.params.data.bots,
            set(self.emote_manager.emotes.unsorted)
            )
        self.raw_datas: dict[str, dict[str, dict]] = defaultdict(dict)
        self.aggregate: dict[int, float] = {}
        self.current: Current = Current()

    def handle_vod(self, vod_id: str):
        """Process the specified VOD."""
        self.current = Current(vod_id=vod_id)
        if vod_id not in self.vod_manager.vods or not self.vod_manager.vods[vod_id]["local"]:
            self._new_local_vod(vod_id)
        if self.preset_manager.onetime[0]:
            self._handle_onetime_preset()
        else:
            self._handle_standard_presets()

        if (self.params.output.to_discord_graph
            and (graph_filepaths := GraphGenerator(self).generate_graphs(live_demo=False))):
            for category, filepath in graph_filepaths:
                self.webhook.send(f'[{category}] chart', filepath=filepath)

        if self.params.output.to_discord_txt and self.current.file_outputs:
            self._handle_txt_output()

    def _new_local_vod(self, vod_id: str):
        """Write logs for a VOD and update its VOD database entry."""
        if not self.vod_manager.vods[vod_id]["api"]:
            if not self.params.allow_non_api:
                raise VODError("VOD not available on Twitch")
            print("Warning: VOD not available on Twitch, links will be unavailable")
        write_logs(self.directory, self.name, vod_id)
        with open(f"{self.directory}/{self.name}/{vod_id}.txt",
                  'r', encoding='UTF-8', errors='replace') as file:
            last_line = file.read().splitlines()[-1]
            duration = (duration_match.group(1)
                        if (duration_match := DURATION_PATTERN.match(last_line))
                        else '?')
        self.vod_manager.vods[vod_id] = {
            "title": self.vod_manager.vods[vod_id]["title"],
            "local": True,
            "api": True,
            "duration": duration
            }
        self.vod_manager.store_vods()

    def _handle_onetime_preset(self):
        """Process a single VOD's logs for a one-time preset."""
        self.current.category = "one-time"
        if (file_output := self._handle_preset(self.preset_manager.onetime)):
            self.current.file_outputs.append(file_output)
        self.current.end_s = (
            max(self.raw_datas["one-time"][self.preset_manager.onetime[0]].keys()))

    def _handle_standard_presets(self):
        """Process a single VOD's logs for default and custom presets."""
        self.current.category = "default"
        for preset_name in self.default_presets:
            if (file_output := self._handle_preset((preset_name, []))):
                self.current.file_outputs.append(file_output)
        # strip values and duplicate timestamps, list associated default presets
        if self.params.output.condense:
            self._condense_defaults()
        all_customs = chain(self.preset_manager.presets.global_.items(),
                            self.preset_manager.presets.local.items())
        active_customs = [custom for custom in all_customs if custom[1].active]
        self.current.category = "custom"
        for preset_name, preset in active_customs:
            if (file_output := self._handle_preset((preset_name, preset.queries))):
                self.current.file_outputs.append(file_output)
        self.current.end_s = max(self.raw_datas["default"]["all"].keys())
        if self.params.output.aggregate or self.params.output.to_discord_graph:
            self._aggregate_default_presets()

    def _handle_preset(self, preset_data: tuple[str, list[str | list[str]]]) -> str | None:
        """Process the specified VOD using a particular preset."""
        self.current.preset_name, self.current.queries = preset_data
        if not (result := self._analyze_preset()): return None
        raw_data, total_count = result
        self.raw_datas[self.current.category][self.current.preset_name] = raw_data
        filterer = AnalyticsDict(raw_data)
        filterer.filter(self.params.data.minimum, self.params.data.spacing)
        output_handler = OutputHandler(self)
        output_handler.generate_output(filterer.data, total_count)
        if self.params.output.to_discord_msg:
            self.webhook.send(f"[{self.current.category}] {self.current.preset_name}",
                              "\n".join(output_handler.discord_output))
        return "\n".join(output_handler.text_output) if self.params.output.to_discord_txt else None

    def _condense_defaults(self):
        """
        Condense .txt output for default presets by removing
        duplicate timestamps and listing associated presets.
        """
        self.current.file_outputs.clear()
        times = defaultdict(list)
        results_limit = (self.params.data.msg_results
                         if self.params.output.condense and not self.params.output.extend
                         else self.params.data.txt_results)
        for preset_name, raw_data in self.raw_datas["default"].items():
            filterer = AnalyticsDict(raw_data)
            filterer.filter(self.params.data.minimum, self.params.data.spacing)
            for i, key in enumerate(filterer.data.keys()):
                if i == results_limit: break
                time_s = key[0] if isinstance(key, tuple) else key
                times[time_s].append(preset_name)
        times = sort_dict(times)
        file_output: list[str] = []
        for time_s, preset_names in times.items():
            vod_time = Duration(time_s)
            link = f"https://www.twitch.tv/videos/{self.current.vod_id}" \
                f"?t={vod_time.hours}h{vod_time.minutes}m{vod_time.seconds}s"
            file_output.append(
                f"{time_s}s ({vod_time.as_timestamp(include_ms=False)}): " \
                f"{', '.join(preset_names)} - {link}"
                )
        self.current.file_outputs.append('\n'.join(file_output))

    def _aggregate_default_presets(self):
        """Evaluate raw data for default presets to get aggregated "best" timestamps."""
        placements = {}
        for preset_name, preset_data in self.raw_datas["default"].items():
            sorted_data = sort_dict(preset_data, key=1, reverse=True)
            placements[preset_name] = dict_placements(sorted_data)
        self._aggregate_places(placements)
        output_handler = OutputHandler(self)
        output_handler.generate_output(self.aggregate)
        aggregate_text_output, aggregate_discord_output = (
            output_handler.text_output, output_handler.discord_output)
        if self.params.output.to_discord_msg:
            self.webhook.send("[default] aggregate", '\n'.join(aggregate_discord_output))
        if self.params.output.to_discord_txt and self.params.output.aggregate:
            self.current.file_outputs.append("\n".join(aggregate_text_output))

    def _handle_txt_output(self):
        """Save and upload .txt output."""
        timestamp = strftime(r"%Y-%m-%d-%H-%M-%S", localtime(time()))
        filepath = f"{self.directory}/{self.name}/{self.current.vod_id}_{timestamp}.txt"
        with open(filepath, 'w', encoding='UTF-8') as file:
            file.write("\n\n".join(self.current.file_outputs))
        self.webhook.send("txt upload", filepath=filepath)

    def _aggregate_places(self, placements: dict[str, dict[int, int]]):
        """Calculate and store overall aggregated placements from sorted preset placement data."""
        aggregate = {
            time_i: round(
                # square root emphasizes consistent high placements over low outlier
                mean([places.get(time_i, MAX_PLACEMENT)**0.5
                     for places in placements.values()]), 3)
            for time_i in range(0, self.current.end_s+1, self.params.data.interval)
            }
        self.aggregate = sort_dict(aggregate, key=1)

    def _analyze_preset(self) -> tuple[dict, int | None] | None:
        """
        Analyze a VOD's chat for a single preset. \\
        Return `None` if VOD is missing, preset is invalid, or no results were found.
        """
        if not self.current.queries:
            default_presets = set(self.default_presets.keys())
            global_presets = set(self.preset_manager.presets.global_.keys())
            local_presets = set(self.preset_manager.presets.local.keys())
            if self.current.preset_name not in (default_presets | global_presets | local_presets):
                raise PresetError("Invalid preset")

        if (self.current.preset_name in ('emote', 'word')
            and not self.emote_manager.emotes.unsorted):
            raise RuntimeError("Cannot calculate emote scores without emote database")
        vod_processor = VODProcessor(self)
        vod_processor.process_current_vod()
        if not vod_processor.total_count:
            print(f"No results found for VOD {self.current.vod_id}! {self.current.preset_name}")
            return None

        # save raw data for graphs and restructure
        data = vod_processor.times
        if vod_processor.messages:
            data = self._process_message_presets(vod_processor.messages)
        elif vod_processor.users:
            if self.current.preset_name == 'users':
                data = {time: len(userset) for time, userset in vod_processor.users.items()}
            elif self.current.preset_name == 'regulars':
                data = self._process_regulars_activity(vod_processor.users)
        if data:
            return (data, vod_processor.total_count)
        error_disp = self.default_presets.get(self.current.preset_name,
                                              f'preset "{self.current.preset_name}"')
        print(f"No results above minimum frequency for {error_disp}!")
        return None

    def _process_message_presets(self, messages: dict[int, dict[str, int]]) -> dict:
        """Process message data for various presets that require it."""
        messages_processor = MessagesProcessor(messages)
        match self.current.preset_name:
            case 'collective':
                return messages_processor.get_collective_times()
            case 'spam' | 'unique':
                option = "spam" if self.current.preset_name == 'spam' else "unique"
                return messages_processor.get_duplicate_scores(option)
            case 'emote' | 'word':
                emotes = set(self.emote_manager.emotes.unsorted)
                option = "emote" if self.current.preset_name == 'emote' else "word"
                return messages_processor.get_emote_scores(emotes, option)
        return {}

    @staticmethod
    def _process_regulars_activity(users: dict[int, set[str]]):
        """Determine collective activity of "regulars" based on user data."""
        AFK_S = 5 * MINUTE_S  # minimum time since last message before considered inactive
        TOP_ACTIVE_PERCENT = 20  # how much of top active chatters to average activity from
        activity: dict[str, int | float] = defaultdict(int)
        for user in set().union(*users.values()):
            last_active_time = 0
            for time_s, userset in users.items():
                if user in userset:
                    last_active_time = time_s
                if time_s - last_active_time <= AFK_S:
                    activity[user] += 1
        total_times = len(users)
        activity = {user: count/total_times for user, count in activity.items()}  # raw %
        activity = {user: activity_percent**(1 - activity_percent**0.3)  # scaled score
                    for user, activity_percent in activity.items()}
        activity = sort_dict(activity, key=1, reverse=True)
        top_count = min(round(len(activity) * TOP_ACTIVE_PERCENT/100), 500)
        top_activity = {user: activity_score for i, (user, activity_score)
                        in enumerate(activity.items()) if i < top_count}
        average_activity_score = sum(top_activity.values()) / len(top_activity)
        regulars = {
            user
            for user, activity_score in activity.items()
            if activity_score >= 0.9 * average_activity_score
        }
        return {time: len(regulars & userset) for time, userset in users.items()}


class VODProcessor:
    """Handle extraction of data from locally saved VOD logs."""

    def __init__(self, channel: AnalyticsChannel):
        self.channel = channel
        self.total_count: int = 0
        self.times: dict[int, int] = defaultdict(int)
        self.users: dict[int, set[str]] = defaultdict(set)
        self.messages: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.channel_filters = self.get_channel_filters()

    def get_channel_filters(self) -> dict:
        """Load channel filters, if any exist."""
        try:
            from channel_filters import channel_filters
            return channel_filters
        except ModuleNotFoundError:
            return {}

    def process_current_vod(self):
        """Process a single VOD's logs for raw data."""
        emotes = set(self.channel.emote_manager.emotes.unsorted)
        with open(f"{self.channel.directory}/{self.channel.name}/" \
                  f"{self.channel.current.vod_id}.txt",
                  'r', encoding='UTF-8', errors='replace') as file:
            for lines_i, line in enumerate(file, start=1):
                line = line.strip()
                if not lines_i % 500:
                    print(f"Reading logs... (Line {lines_i})", end='\r')
                if not (msg := VOD_MESSAGE_PATTERN.fullmatch(line)): continue
                timestamp, user, message = msg.groups()
                if self.channel.name in self.channel_filters:
                    is_valid = self.channel_filters[self.channel.name]
                    if not is_valid(timestamp, user, message, emotes):
                        continue
                if self._check_message(user, message, emotes):
                    self.update_data(timestamp, user, message)

    def update_data(self, timestamp: str, user: str, message: str):
        """Update logs data for a single line of logs."""
        self.total_count += 1
        timestamp = timestamp.replace(':', '')
        time_s = (60*60*int(timestamp[:2]) + 60*int(timestamp[2:4]) + int(timestamp[4:]))
        quantized_s = time_s - (time_s % self.channel.params.data.interval)
        self.times[quantized_s] += 1
        preset_name = self.channel.current.preset_name
        if preset_name in {'users', 'regulars'}:
            self.users[quantized_s].add(user)
        elif preset_name in {'collective', 'spam', 'unique', 'emote', 'word'}:
            self.messages[quantized_s][message] += 1

    def _check_message(self, user: str, message: str, emotes: set[str]) -> bool:
        """Check if a message matches the current search preset."""
        message = ''.join(char for char in message if char in printable).strip()
        if (user in self.channel.params.data.bots
            or COMMAND_MESSAGE_PATTERN.match(message)
            or is_subscription_message(user, message)):
            return False
        preset_name = self.channel.current.preset_name
        match preset_name:
            case ('all' | 'collective' | 'users' | 'spam' |
                  'unique' | 'regulars' | 'emote' | 'word'):
                return True
            case 'caps' | 'caps-only':
                words = set(message.split())
                if preset_name == 'caps':  # remove emotes
                    message = ' '.join((word for word in words if word not in emotes))
                elif not words.isdisjoint(emotes):  # skip if emotes
                    return False
                return (len(message) > 1 and message.isalpha() and message.isupper())
            case _:
                for query in self.channel.current.queries:
                    match query:
                        case str():
                            if query not in emotes: continue  # only check added emotes
                            if self._match_query(query, message): return True
                        case [q_string, q_params]:
                            cs, em = [char == 'y' for char in q_params]
                            if self._match_query(q_string, message, cs, em): return True
                return False

    @staticmethod
    def _match_query(query: str, message: str,
                     case_sensitive: bool = True, exact_match: bool = True):
        """Check for presence of a query string in another string."""
        if case_sensitive:
            if exact_match:
                return bool(re.match(rf".*\b{query}\b.*", message))
            return query in message
        if exact_match:
            return bool(re.match(rf".*\b{query.lower()}\b.*", message.lower()))
        return query.lower() in message.lower()


class MessagesProcessor:
    """Process messages from VODs into various derivative metrics."""

    def __init__(self, messages: dict[int, dict[str, int]]):
        self.messages = messages

    def get_collective_times(self) -> dict[tuple[int, str], int]:
        """Calculate collective spam per timeslice."""
        coll_times: dict[tuple[int, str], int] = {}
        for time_s, message_data in self.messages.items():
            for message, count in message_data.items():
                coll_times[(time_s, message)] = count
        return coll_times

    def get_duplicate_scores(self, option: Literal["spam", "unique"]) -> dict[int, float]:
        """Calculate spam or unique score per timeslice."""
        dupe_scores: dict[int, float] = {}
        for time_s, message_data in self.messages.items():
            message_count, unique_count = sum(message_data.values()), len(message_data)
            if option == "spam":
                dupe_scores[time_s] = round(
                    (message_count**2) / (2 * (unique_count**1.1)))
            elif option == "unique":
                dupe_scores[time_s] = round(
                    (unique_count**2.7) / (2 * (message_count**1.1)))
        return dupe_scores

    def get_emote_scores(self, emotes: set[str],
                         option: Literal["emote", "word"]) -> dict[int, int]:
        """Calculate emote or non-emote score per timeslice."""
        emote_scores: dict[int, int] = {}
        for time_s, message_data in self.messages.items():
            emotes_count = len(
                [message for message in message_data.keys()
                if not set(message.split()).isdisjoint(emotes)]
                )
            words_count = len(message_data) - emotes_count
            if option == "emote":
                emote_scores[time_s] = int(
                    emotes_count / sqrt(max(1, words_count) / len(message_data)))
            elif option == "word":
                emote_scores[time_s] = int(
                    words_count / sqrt(max(1, emotes_count) / len(message_data)))
        return emote_scores


class OutputHandler:
    """Handle processing results into .txt and Discord message output."""

    def __init__(self, channel: AnalyticsChannel):
        self.channel = channel
        self.text_output: list[str] = []
        self.discord_output: list[str] = []

    def generate_output(self, data: dict, total_count: int | None = None):
        """
        Generate .txt file and and Discord message output from processed VOD analysis data. \\
        `data` should already be pre-processed coming into the function.
        """
        vod_id = self.channel.current.vod_id
        params = self.channel.params
        results_limit = (params.data.msg_results
                         if params.output.condense and not params.output.extend
                         else params.data.txt_results)
        for i, (key, value) in enumerate(data.items()):
            if i == results_limit: break
            time_s, message = key if isinstance(key, tuple) else (key, '')
            message = f"{message[:7]}..." if len(message) > 7 else message
            vod_time, link, text_coll, discord_coll = (
                self._get_entry_details(vod_id, time_s, message))
            self.text_output.append(
                f"{int(vod_time.total_seconds)}s " \
                f"({vod_time.as_timestamp(include_ms=False)})" \
                f"{text_coll}: {value} - {link}"
                )
            if i < params.data.msg_results:
                self.discord_output.append(
                    f"{int(vod_time.total_seconds)}s " \
                    f"(**{vod_time.as_timestamp(include_ms=False)}**)" \
                    f"{discord_coll}: *{value}* - <{link}>"
                    )

        if self.text_output and self.discord_output:
            title = self.channel.vod_manager.vods[vod_id]["title"]
            self._wrap_output(title, total_count)

    def _wrap_output(self, title: str, total_count: int | None):
        """Add header/footer lines to a preset's output when applicable."""
        preset_name, vod_id = self.channel.current.preset_name, self.channel.current.vod_id
        preset_label = self._get_preset_label(preset_name)
        self.text_output.insert(
            0, f'{preset_label} in {self.channel.name}/{vod_id}.txt ({title}):')
        self.discord_output.insert(
            0, f'**{preset_label} in {self.channel.name}/{vod_id}.txt** (*{title}*):')
        if preset_name not in self.channel.default_presets and total_count:
            self.text_output.append(
                f"Total messages matching query in {vod_id}.txt: {total_count}")
            self.discord_output.append(
                f"**Total messages matching query in {vod_id}.txt: {total_count}**")

    def _get_preset_label(self, preset_name: str) -> str:
        """Determine the label for a preset in output text."""
        params, default_presets = self.channel.params, self.channel.default_presets
        if self.channel.aggregate:
            return "Top moments (aggregate):"
        if preset_name in default_presets:
            return f"Top moments [{params.data.interval}s] ({default_presets[preset_name]})"
        return f'Top "{preset_name}" moments [{params.data.interval}s]'

    @staticmethod
    def _get_entry_details(vod_id: str, time_s: int, message: str | None):
        """Generate various information for a single output entry."""
        vod_time = Duration(time_s)
        link = f"https://www.twitch.tv/videos/{vod_id}" \
               f"?t={vod_time.hours}h{vod_time.minutes}m{vod_time.seconds}s"
        text_coll = f" [{message}]" if message else ''
        discord_coll = f" `[{message}]`" if message else ''
        return (vod_time, link, text_coll, discord_coll)


class GraphGenerator:
    """Handle all graphical output for preset data."""

    def __init__(self, channel: AnalyticsChannel):
        self.channel = channel
        self.params = self.set_params()

    def set_params(self):
        """Determine base numerical parameters for horizontal graph axis."""
        end_s = self.channel.current.end_s
        major_tick, minor_tick = self._get_ticks(end_s/HOUR_S)
        major_range = np.arange(0, end_s+minor_tick, major_tick)
        minor_range = np.arange(0, end_s+minor_tick, minor_tick)
        return GraphParams(minor_tick, major_range, minor_range)

    def generate_graphs(self, live_demo: bool = False) -> list[tuple[str, str]]:
        """Create graphs for a one-time preset or default/custom presets for the current VOD."""
        filepaths: list[tuple[str, str]] = []
        plt.figure(figsize=(18, 4))
        for category_name, category_data in self.channel.raw_datas.items():
            self._set_up_graph(category_name)
            self._plot_presets((category_name, category_data))
            if category_name == "default":
                self._configure_default_graph()
            plt.legend(loc='best')
            plt.tight_layout(pad=0.5)
            if live_demo:
                plt.show()
                continue
            timestamp = strftime(r"%Y-%m-%d-%H-%M-%S", localtime(time()))
            vod_id = self.channel.current.vod_id
            filename = f"{self.channel.directory}/{self.channel.name}" \
                       f"/{vod_id}_{category_name}_{timestamp}"
            plt.savefig(f"{filename}.png")
            plt.clf()
            filepaths.append((category_name, f"{filename}.png"))
        return filepaths

    def _set_up_graph(self, category_name: str):
        """Set up essential graph parameters."""
        current = self.channel.current
        vod_title = ''.join(
            char for char in self.channel.vod_manager.vods[current.vod_id]["title"]
            if char in printable
            ).strip()
        suffix = "preset" if category_name == "one-time" else "presets"
        plt.title(f'{category_name.title()} {suffix} ' \
                    f'({self.channel.name}/{current.vod_id}.txt - "{vod_title}")')
        plt.xlim(0, (current.end_s + self.params.minor_tick)/HOUR_S)
        plt.xlabel("Time (h)")
        plt.xticks(ticks=self.params.major_range,
                   labels=[(tick/HOUR_S if (tick/HOUR_S) % 1 else int(tick/HOUR_S))
                            for tick in self.params.major_range])
        plt.xticks(ticks=self.params.minor_range, minor=True)
        plt.grid(visible=True, which="major", axis="x")

    def _plot_presets(self, category: tuple[str, dict[str, dict]]):
        """Plot data for relevant active presets on the current VOD graph."""
        category_name, category_data = category
        for i, (preset_name, preset_data) in enumerate(category_data.items()):
            if category_name == "default":
                # only plot most interesting/cohesive presets
                if preset_name not in ('all', 'spam', 'regulars', 'emote'): continue
                label, style, yscale_args = (
                    self.channel.default_presets[preset_name], '-', {"value": "linear"})
            else:
                plt.ylabel("Frequency")
                label, style, yscale_args = (
                    f'preset "{preset_name}"', '+', {"value": "log", "base": 2})
            plt.yscale(**yscale_args)  # type: ignore
            peak_divisor = max(preset_data.values()) if category_name == "default" else 1
            time_array = np.array(tuple(preset_data.items()), dtype=int).transpose()
            plt.plot(time_array[0], time_array[1]/peak_divisor, style,
                     linewidth=0.5, zorder=len(self.channel.raw_datas)-i+1, label=label)

    def _configure_default_graph(self):
        """Modify y-axis and add vertical lines for default presets."""
        top_times = [time_s for k, time_s in enumerate(self.channel.aggregate) if k < 25]
        plt.vlines(x=top_times, ymin=0, ymax=1.05, color='black', linestyles='dotted',
                    label="Notable timestamps", linewidth=1, zorder=1)
        plt.ylim(bottom=0, top=1.05)
        plt.yticks([]) # hide y axis ticks/labels entirely, why? because:
        # preset data is normalized, and overall trend > exact value
        # units are mismatched anyways (some frequency and some score)

    @staticmethod
    def _get_ticks(duration_h: int | float) -> tuple[int, int]:
        """Get adjusted horizontal chart tick values based on VOD length."""
        if duration_h < 8:
            return 30*MINUTE_S, 10*MINUTE_S
        if duration_h < 16:
            return 1*HOUR_S, 15*MINUTE_S
        if duration_h < 32:
            return 1*HOUR_S, 30*MINUTE_S
        return 2*HOUR_S, 30*MINUTE_S  # max VOD length is 48h
