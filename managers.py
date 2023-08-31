"""Classes for channel/VOD data management and automation."""
# pylint: disable=multiple-statements,invalid-name
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass, asdict
from itertools import chain
import json
import os
import re
from statistics import mean
from typing import TypedDict

import requests


VOD_MESSAGE_PATTERN = re.compile(r"^\[([\d:]*)\.\d+\] ([a-z\d_]*): (.*)$")
DURATION_PATTERN = re.compile(r"^\[([\d:\.]+)]")
API_VOD_PATTERN = re.compile(r"^(.*) - https://www.twitch.tv/videos/(\d+)$")
LOGS_FILE_PATTERN = re.compile(r"(\d+).txt")
MAX_PLACEMENT = 300  # sorted placement (#1 highest, etc.) default value if not present
SUGGESTION_COUNT = 50  # preset term suggestions


def sort_dict(dictionary: dict, key: int = 0, reverse: bool = False) -> dict:
    """Shorthand for sorting dictionary by key or value."""
    return dict(sorted(dictionary.items(), key=lambda item: item[key], reverse=reverse))

def dict_placements(dictionary: dict) -> dict:
    """
    Assign numerical placements to entries in a dictionary. \\
    The dictionary should initially be reverse-sorted by value.
    """
    return {key: i for i, key in enumerate(dictionary.keys(), start=1)}

def is_subscription_message(user: str, message: str) -> bool:
    """
    Check whether a message is a Twitch system message for subscriptions. \\
    Twitch stores these as regular messages in VOD chat replays with a predictable format.
    """
    if not (words := message.split()): return False
    sub_terms = {"subscribed", "gifted", "gifting", "paying", "continuing", "converted"}
    username_first = words[0].lower() == user  # username: UserName ...
    has_sub_terms = any(word in sub_terms for word in words)
    has_punctuation = ('.' in message or '!' in message)
    return username_first and has_sub_terms and has_punctuation


class Manager:
    """Hold basic information for local Twitch channel data management."""

    def __init__(self, channel: str, directory: str):
        self.channel = channel
        self.directory = directory


class APIEmoteURL(TypedDict):
    """Image URL for a specific emote size."""
    size: str
    url: str


class APIEmote(TypedDict):
    """Emote from API JSON response."""
    provider: int
    code: str
    url: list[APIEmoteURL]


APIEmotes = list[APIEmote]


@dataclass(slots=True)
class ProviderEmotes:
    """Emotes in a channel by a specific provider, sorted by scope."""
    global_: list[str]
    local: list[str]


@dataclass(slots=True)
class SortedEmotes:
    """Emotes in a channel, sorted by provider."""
    twitch: ProviderEmotes
    stv: ProviderEmotes
    bttv: ProviderEmotes
    ffz: ProviderEmotes


@dataclass(slots=True)
class Emotes:
    """Database of a channel's emotes, unsorted and sorted by provider and scope."""
    sorted: SortedEmotes
    unsorted: list[str]


class EmoteManager(Manager):
    """Manage emote database for a Twitch channel."""

    def __init__(self, channel: str, directory: str):
        super().__init__(channel, directory)
        self.emotes: Emotes = self.get_emotes()
        if not self.emotes:
            print("No emotes found, fetching emotes from the API...")
            self.fetch_emotes()
            self.store_emotes()

    def get_emotes(self) -> Emotes:
        """Pull emotes from local emote database."""
        try:
            with open(f"{self.directory}/{self.channel}/emotes.json",
                      'r', encoding="UTF-8") as file:
                emotes = json.loads(file.read())
            print("Emote database loaded.")
            return Emotes(emotes["sorted"], emotes["unsorted"])
        except FileNotFoundError:
            return Emotes(
                SortedEmotes(ProviderEmotes([], []),
                             ProviderEmotes([], []),
                             ProviderEmotes([], []),
                             ProviderEmotes([], [])),
                []
            )

    def fetch_emotes(self):
        """Sort API emotes into `self.emotes`."""
        global_emotes, local_emotes = self.pull_api_emotes()
        self.emotes.unsorted = (
            [emote["code"] for emote in global_emotes]
            + [emote["code"] for emote in local_emotes]
            )
        providers = ("twitch", "stv", "bttv", "ffz")
        sorted_emotes = {
            "twitch": ProviderEmotes([], []),
            "stv": ProviderEmotes([], []),
            "bttv": ProviderEmotes([], []),
            "ffz": ProviderEmotes([], [])
            }
        for emote in global_emotes:
            provider = providers[emote["provider"]]
            sorted_emotes[provider].global_.append(emote["code"])
        for emote in local_emotes:
            provider = providers[emote["provider"]]
            sorted_emotes[provider].local.append(emote["code"])
        self.emotes.sorted = SortedEmotes(sorted_emotes["twitch"], sorted_emotes["stv"],
                                          sorted_emotes["bttv"], sorted_emotes["ffz"])

    def pull_api_emotes(self) -> tuple[APIEmotes, APIEmotes]:
        """Pull emotes from the API."""
        twitch_id = requests.get(f"https://decapi.me/twitch/id/{self.channel}", timeout=5).text
        attempts = 0
        while True:
            try:
                global_emotes = requests.get(
                    "https://emotes.adamcy.pl/v1/global/emotes/all", timeout=5).json()
                local_emotes = requests.get(
                    f"https://emotes.adamcy.pl/v1/channel/"
                    f"{twitch_id}/emotes/all", timeout=5).json()
            except requests.Timeout as exc:
                attempts += 1
                print(f"Emote fetching timed out, retrying (Attempt #{attempts})")
                if attempts == 5:
                    raise RuntimeError("Fetching emotes timed out") from exc
            else:
                return (global_emotes, local_emotes)

    def store_emotes(self):
        """Save `self.emotes` to local emote database."""
        if self.emotes.sorted and self.emotes.unsorted:
            with open(f"{self.directory}/{self.channel}/emotes.json",
                      'w', encoding="UTF-8") as file:
                file.write(json.dumps(asdict(self.emotes), indent=4, separators=(',', ': ')))
            print("Emote database updated, check the file to make sure.")
        else:
            print("No emotes found, store_emotes failed.")


@dataclass(slots=True)
class RawTerms:
    """Unprocessed term data collected from VOD logs."""
    common: dict[str, dict[str, int]]
    burst: dict[str, dict[str, list[int]]]
    avg_burst: dict[str, dict[str, dict[str, list[int]]]]


@dataclass(slots=True)
class FinalTerms:
    """Processed suggested preset terms with individual and aggregate metrics."""
    common: dict[str, dict[str, int]]
    burst: dict[str, dict[str, float]]
    avg_burst: dict[str, dict[str, float]]
    aggregate: dict[str, dict[str, float]]


@dataclass(slots=True)
class Terms:
    """Database of preset terms and term data."""
    raw: RawTerms
    final: FinalTerms

    def process_raw(self, vod_count: int):
        """Process raw term data into usable metrics."""
        self.process_individuals(vod_count)
        self.process_aggregate()

    def process_individuals(self, vod_count: int):
        """Convert raw frequency and burst data into term statistics."""
        self.final.common = {
            "emote": sort_dict(self.raw.common["emote"], key=1, reverse=True),
            "word": sort_dict(self.raw.common["word"], key=1, reverse=True)
            }

        burst_emotes = self._process_burst(
            self.raw.burst["emote"], self.raw.common["emote"], vod_count)
        burst_words = self._process_burst(
            self.raw.burst["word"], self.raw.common["word"], vod_count)
        self.final.burst = {
            "emote": sort_dict(burst_emotes, key=1, reverse=True),
            "word": sort_dict(burst_words, key=1, reverse=True)
            }

        avg_burst_emotes = self._process_average_burst(
            self.raw.avg_burst["emote"],
            self.raw.common["emote"], vod_count)
        avg_burst_words = self._process_average_burst(
            self.raw.avg_burst["word"],
            self.raw.common["word"], vod_count)
        self.final.avg_burst = {
            "emote": sort_dict(avg_burst_emotes, key=1, reverse=True),
            "word": sort_dict(avg_burst_words, key=1, reverse=True)
            }

    def process_aggregate(self):
        """Combine individual term statistics into aggregate suggestions."""
        aggregate_emotes, aggregate_words = self._individual_to_aggregate()
        self.final.aggregate = {
            "emote": sort_dict(aggregate_emotes, key=1),
            "word": sort_dict(aggregate_words, key=1)
            }

    def _individual_to_aggregate(self):
        """Process sorted individual metric data into aggregate placements."""
        # get individual placements
        ce_places = dict_placements(self.final.common["emote"])
        cw_places = dict_placements(self.final.common["word"])
        be_places = dict_placements(self.final.burst["emote"])
        bw_places = dict_placements(self.final.burst["word"])
        abe_places = dict_placements(self.final.avg_burst["emote"])
        abw_places = dict_placements(self.final.avg_burst["word"])

        # calculate weighted placements
        aggregate_emotes = self._aggregate_places(ce_places, be_places, abe_places)
        aggregate_words = self._aggregate_places(cw_places, bw_places, abw_places)

        return (aggregate_emotes, aggregate_words)

    @staticmethod
    def _process_burst(burst: dict, common: dict, vod_count: int) -> dict:
        """Convert raw burst data to burst scores."""
        # slight bias towards frequency, stronger bias towards presence of longer bursts
        return {term: round(common[term]**(1/5) * (sum(counts) / vod_count)**(1/3), 3)
                for term, counts in burst.items()}

    @staticmethod
    def _process_average_burst(avg_burst: dict[str, dict],
                               common: dict, vod_count: int) -> dict[str, dict]:
        """Convert raw average burst data to average burst scores."""
        # slight bias towards frequency, stronger bias towards presence of longer bursts
        return {
            term: round(
                mean([round(common[term]**(1/5)
                            * (sum(counts) / (vod_count / len(data)))**(1/3), 3)
                      for counts in data.values()]),
                3)
            for term, data in avg_burst.items()
            }

    @staticmethod
    def _aggregate_places(common_places: dict, burst_places: dict, avg_burst_places: dict):
        """Calculate weighted aggregate placements for terms."""
        return {
            term: round(
                (common_places.get(term, MAX_PLACEMENT)
                 + 2*burst_places.get(term, MAX_PLACEMENT)
                 + 3*avg_burst_places.get(term, MAX_PLACEMENT)) / 6,
                3)
            for term in common_places
            }


class TermManager(Manager):
    """Manage potential terms for search presets for a Twitch channel."""

    def __init__(self, channel: str, directory: str, bots: set[str], emotes: set[str]):
        super().__init__(channel, directory)
        self.bots = bots
        with open("common_eng.txt", 'r', encoding='UTF-8', errors='replace') as file:
            self.common_eng = set(file.read().splitlines())
        self.emotes = emotes
        self.hidden: list[str] = self.get_hidden()
        raw_terms: RawTerms = RawTerms(
            {"emote": defaultdict(int), "word": defaultdict(int)},
            {"emote": defaultdict(list), "word": defaultdict(list)},
            {"emote": defaultdict(lambda: defaultdict(list)),
             "word": defaultdict(lambda: defaultdict(list))}
            )
        final_terms: FinalTerms = self.get_terms()
        self.terms = Terms(raw_terms, final_terms)

    def get_terms(self) -> FinalTerms:
        """Pull terms from local term database."""
        try:
            with open(f"{self.directory}/{self.channel}/terms.json",
                      'r', encoding="UTF-8") as file:
                terms = json.loads(file.read())
            print("Term database loaded.")
            return FinalTerms(terms["common"], terms["burst"],
                              terms["avg_burst"], terms["aggregate"])
        except FileNotFoundError:
            print("No terms found, generate some first.")
            return FinalTerms(
                {"emote": defaultdict(int), "word": defaultdict(int)},
                {"emote": defaultdict(float), "word": defaultdict(float)},
                {"emote": defaultdict(float), "word": defaultdict(float)},
                {}
                )

    def get_hidden(self) -> list[str]:
        """Pull hidden terms from local hidden terms database."""
        try:
            with open(f"{self.directory}/{self.channel}/hidden.json",
                      'r', encoding='UTF-8') as file:
                hidden = json.loads(file.read())
            return hidden
        except FileNotFoundError:
            print("No hidden terms found, hide some terms first.")
            with open(f"{self.directory}/{self.channel}/hidden.json",
                      'w', encoding='UTF-8') as file:
                file.write(json.dumps([], indent=4, separators=(',', ': ')))
            return []

    def store_terms(self):
        """Save `self.terms.final` and `self.hidden` to local term/hidden term databases."""
        if self.terms or self.hidden:
            # fixed in 3.12: https://github.com/python/cpython/pull/32056
            terms = FinalTerms(
                {"emote": dict(self.terms.final.common["emote"]),
                 "word": dict(self.terms.final.common["word"])},
                {"emote": dict(self.terms.final.burst["emote"]),
                 "word": dict(self.terms.final.burst["word"])},
                {"emote": dict(self.terms.final.avg_burst["emote"]),
                "word": dict(self.terms.final.avg_burst["word"])},
                dict(self.terms.final.aggregate)
                )
            with open(f"{self.directory}/{self.channel}/terms.json",
                      'w', encoding="UTF-8") as file:
                file.write(json.dumps(asdict(terms), indent=4, separators=(',', ': ')))
            with open(f"{self.directory}/{self.channel}/hidden.json",
                      'w', encoding='UTF-8') as file:
                file.write(json.dumps(self.hidden, indent=4, separators=(',', ': ')))
            print("Term lists updated, check the files to make sure.")
        else:
            print("No terms found, store_terms failed.")

    def process_terms(self):
        """Analyze local VOD logs to gather potential preset terms in `self.terms`."""
        vod_ids: tuple[str] = tuple(
            sorted([filematch[1] for filename in os.listdir(f"{self.directory}/{self.channel}")
                    if (filematch := LOGS_FILE_PATTERN.match(filename))])
        )
        vod_count = len(vod_ids)
        for vods_i, vod_id in enumerate(vod_ids, start=1):
            print(f"Reading VOD logs... ({vods_i}/{vod_count})", end='\r')
            self._analyze_vod(vod_id)
        self.terms.process_raw(vod_count)

    def get_suggested_terms(self, presets: Presets) -> tuple[tuple[str], tuple[str]]:
        """Collect available suggested terms not hidden or already in use."""
        current_emotes, current_words = presets.get_unique_terms()
        aggregate_terms = self.terms.final.aggregate
        suggest_emotes = tuple(emote for emote in list(aggregate_terms["emote"].keys())
                               if emote not in (current_emotes | set(self.hidden)))
        suggest_words = tuple(word for word in list(aggregate_terms["word"].keys())
                              if word not in (current_words | set(self.hidden)))
        return (suggest_emotes, suggest_words)

    def _analyze_vod(self, vod_id: str):
        """Analyze a single VOD for frequency and burst data."""
        with open(f"{self.directory}/{self.channel}/{vod_id}.txt",
                  'r', encoding='UTF-8', errors='replace') as file:
            burst_temp, last_words = defaultdict(int), []
            for line in file:
                if not (cmsg := VOD_MESSAGE_PATTERN.fullmatch(line.strip())): continue
                user, message = cmsg[2], cmsg[3]
                if (user.lower() in self.bots
                    or is_subscription_message(user, message)): continue
                words = [word for word in message.strip().split() if word != "\U000e0000"]
                for word in words:
                    self._update_frequency(word)
                repeated_terms = set(words) & set(last_words)
                # track word repetition across successive messages
                for term in (set(repeated_terms) | set(burst_temp)):
                    if term in repeated_terms:
                        burst_temp[term] += 1
                    elif (count := burst_temp.pop(term)) > 1:
                        self._update_bursts(vod_id, term, count)
                last_words = words

    def _update_frequency(self, word: str):
        """Update frequency data for a single term."""
        if self.emotes and word in self.emotes:
            self.terms.raw.common["emote"][word] += 1
        elif word.lower() not in self.common_eng:
            self.terms.raw.common["word"][word] += 1

    def _update_bursts(self, vod_id: str, term: str, count: int):
        """Update burst data for a single term. Counts are cubed to emphasis high values."""
        if self.emotes and term in self.emotes:
            self.terms.raw.burst["emote"][term].append(count**3)
            self.terms.raw.avg_burst["emote"][term][vod_id].append(count**3)
        else:
            self.terms.raw.burst["word"][term].append(count**3)
            self.terms.raw.avg_burst["word"][term][vod_id].append(count**3)


@dataclass(slots=True)
class Preset:
    """Data for a single search preset."""
    queries: list[str | list[str]]
    active: bool


@dataclass(slots=True)
class Presets:
    """Database of a channel's presets, global and local."""
    global_: dict[str, Preset]
    local: dict[str, Preset]

    def get_unique_terms(self) -> tuple[set[str], set[str]]:
        """Get all unique emotes and words used in preset queries."""
        unique_emotes: set[str] = set()
        unique_words: set[str] = set()
        all_queries = list(chain(
            chain.from_iterable((preset.queries for preset in self.global_.values())),
            chain.from_iterable((preset.queries for preset in self.local.values()))
            ))
        for query in all_queries:
            match query:
                case str():
                    unique_emotes.add(query)
                case [query_term, _]:
                    unique_words.add(query_term)
        return (unique_emotes, unique_words)

    def toggle(self, presets: list[str]):
        """Toggle preset(s) on and off."""
        for preset_name in presets:
            if preset_name in self.global_:
                self.global_[preset_name].active = (
                    not self.global_[preset_name].active)
                print(f'Preset "{preset_name}" toggled ' \
                      f'{"on" if self.global_[preset_name].active else "off"}.')
            elif preset_name in self.local:
                self.local[preset_name].active = (
                    not self.local[preset_name].active)
                print(f'Preset "{preset_name}" toggled ' \
                      f'{"on" if self.local[preset_name].active else "off"}.')

    def delete(self, presets: list[str]):
        """Delete preset(s) entirely."""
        for preset_name in presets:
            if preset_name in self.global_:
                del self.global_[preset_name]
                print(f'Preset "{preset_name}" deleted.')
            elif preset_name in self.local:
                del self.local[preset_name]
                print(f'Preset "{preset_name}" deleted.')


class PresetManager(Manager):
    """Manage analyzer search presets for a Twitch channel."""

    def __init__(self, channel: str, directory: str, bots: set[str], emotes: set[str]):
        super().__init__(channel, directory)
        self.term_manager = TermManager(self.channel, self.directory, bots, emotes)
        self.onetime: tuple[str, list[str | list[str]]] = ('', [])
        self.presets: Presets = self.get_presets()

    def get_presets(self) -> Presets:
        """Pull global and per-channel presets from local preset database."""
        global_presets: dict[str, Preset] = {}
        local_presets: dict[str, Preset] = {}
        if os.path.isfile(f"{self.directory}/presets.json"):
            with open(f"{self.directory}/presets.json",
                      'r', encoding="UTF-8") as file:
                raw_global_presets: dict[str, dict] = json.loads(file.read())
                global_presets: dict[str, Preset] = {
                    preset_name: Preset(preset["queries"], preset["active"])
                    for preset_name, preset in raw_global_presets.items()
                    }
        if os.path.isfile(f"{self.directory}/{self.channel}/presets.json"):
            with open(f"{self.directory}/{self.channel}/presets.json",
                      'r', encoding="UTF-8") as file:
                raw_local_presets: dict[str, dict] = json.loads(file.read())
                local_presets: dict[str, Preset] = {
                    preset_name: Preset(preset["queries"], preset["active"])
                    for preset_name, preset in raw_local_presets.items()
                    }
        print("Preset database loaded."
              if global_presets or local_presets
              else "No presets found, save a preset to create one.")
        return Presets(global_presets, local_presets)

    def store_presets(self):
        """Save `self.presets` to local preset database."""
        if self.presets:
            with open(f"{self.directory}/presets.json", 'w', encoding="UTF-8") as file:
                file.write(json.dumps(self.presets.global_, indent=4, separators=(',', ': ')))
            with open(f"{self.directory}/{self.channel}/presets.json",
                      'w', encoding="UTF-8") as file:
                file.write(json.dumps(self.presets.local, indent=4, separators=(',', ': ')))
            print("Preset database updated, check the file to make sure.")
        else:
            print("No presets found, store_presets failed.")

    def write_preset(self):
        """Create a new preset or overwrite an existing one."""
        if input("Suggest terms from existing VOD logs? (y/n): ") == 'y':
            self.manage_suggested_terms()
        queries = input("\nInput terms separated by ';' " \
                        "[$emote for emotes (case-sensitive)]: ").split(';')
        if not (queries := (query.strip() for query in queries)): return
        new_queries: list[str | list[str]] = []
        for query in queries:
            query = query.strip()
            if query.startswith('$'):
                new_query = query[1:]
            else:
                check_case = input(f'["{query}"] Case-sensitive? (y/n): ')
                check_exact_word = input(
                    f'["{query}"] Enforce exact word match? ' \
                    "[disable if symbols in query] (y/n): "
                    )
                new_query = [query, check_case + check_exact_word]
            new_queries.append(new_query)
        preset_name = input("   Input a preset name: ")
        if input("Create/overwrite this preset? (y/n): ") == 'y':
            if input("Save to global or local presets? (g/l): ") == 'g':
                self.presets.global_[preset_name] = Preset(new_queries, True)
            else:
                self.presets.local[f"{self.channel}_{preset_name}"] = Preset(new_queries, True)
            self.store_presets()
        if input("Save this preset for one-time search? (y/n): ") == 'y':
            self.onetime = (preset_name, new_queries)

    def manage_suggested_terms(self):
        """Handle preset term generation, regeneration, and hiding."""
        while True:
            suggest_emotes, suggest_words = self.term_manager.get_suggested_terms(self.presets)
            print(f'\nEmotes: {" | ".join(suggest_emotes[:SUGGESTION_COUNT])}')
            print(f'\nTerms: {" | ".join(suggest_words[:SUGGESTION_COUNT])}')
            match input("[Re]generate or hide terms? [leave blank to continue] (r/h): "):
                case 'r':
                    self.term_manager.process_terms()
                    self.term_manager.store_terms()
                case 'h':
                    hide_terms = input(
                        "List terms (case-sensitive) to hide separated by ';': ").split(';')
                    for term in hide_terms:
                        self.term_manager.hidden.append(term.strip())
                    self.term_manager.store_terms()
                case _:
                    break


class PresetError(Exception):
    """Search preset is invalid or missing."""


class VOD(TypedDict):
    """Base info for a single VOD, local and/or API."""
    title: str
    local: bool
    api: bool
    duration: str


VODs = dict[str, VOD]


class VODManager(Manager):
    """Manage VODs and their data for a Twitch channel."""

    def __init__(self, channel: str, directory: str):
        super().__init__(channel, directory)
        self.vods: VODs = self.get_vods()
        if not self.vods:
            print("No VODs found, fetching VODs from the API..")
            self.update_vods()
            self.store_vods()

    def get_vods(self) -> VODs:
        """Pull VODs from local VOD database."""
        try:
            with open(f"{self.directory}/{self.channel}/vods.json",
                      'r', encoding="UTF-8") as file:
                vods = json.loads(file.read())
            print("VOD database loaded.")
            return vods
        except FileNotFoundError:
            return {}

    def store_vods(self):
        """Save `self.vods` to local VOD database."""
        if self.vods:
            with open(f"{self.directory}/{self.channel}/vods.json", 'w', encoding="UTF-8") as file:
                file.write(json.dumps(self.vods, indent=4, separators=(',', ': ')))
            print("VOD database updated, check the file to make sure.")
        else:
            print("No VODs found, store_vods failed.")

    def _get_local_ids_and_durations(self) -> dict[str, str]:
        """Pull local VOD IDs and durations from local VOD logs."""
        local_ids: list[str] = sorted(
            [filename.replace(".txt", '')
             for filename in os.listdir(f"{self.directory}/{self.channel}")
             if filename.replace(".txt", '').isnumeric()]
            )
        durations: dict[str, str] = {}
        for local_id in local_ids:
            with open(f"{self.directory}/{self.channel}/{local_id}.txt",
                      'r', encoding='UTF-8', errors='replace') as file:
                last_line = file.read().splitlines()[-1]
            if (duration := DURATION_PATTERN.match(last_line)):
                durations[local_id] = duration.group(1)
        return durations

    def _fetch_api_ids_and_titles(self) -> dict[str, str]:
        """Fetch VOD IDs and titles from the API."""
        params = {"limit": 100, "broadcast_type": "archive", "separator": "%20$NEW%20"}
        api_vods = requests.get(
            f"https://decapi.me/twitch/videos/{self.channel}", params=params, timeout=1
            ).text.replace('\n', ' ').split("%20$NEW%20")
        api_titles: dict[str, str] = {}
        for api_vod in api_vods:
            api_vod = api_vod.strip()
            if not (fields := API_VOD_PATTERN.fullmatch(api_vod)): continue
            vod_title, vod_id = fields.groups()
            api_titles[vod_id] = vod_title
        return api_titles

    def update_vods(self):
        """Collect local and API VOD data into `self.vods`."""
        durations = self._get_local_ids_and_durations()
        api_titles = self._fetch_api_ids_and_titles()
        all_ids = sorted(set(durations) | set(api_titles))
        for vod_id in all_ids:
            title, duration = api_titles.get(vod_id, ''), durations.get(vod_id, '?')
            local, api = vod_id in set(durations), vod_id in set(api_titles)
            if vod_id in self.vods:
                self.edit_vod_entry(vod_id, (title, local, api, duration))
            else:
                self.vods[vod_id] = {
                    "title": title,
                    "local": local,
                    "api": api,
                    "duration": duration
                }

    def edit_vod_entry(self, vod_id: str, vod_data: tuple[str, bool, bool, str]):
        """Update existing VOD database entry for a specific VOD."""
        title, local, api, duration = vod_data
        if not self.vods[vod_id]["title"]:
            self.vods[vod_id]["title"] = title
        self.vods[vod_id]["local"] = local
        self.vods[vod_id]["api"] = api
        if duration != '?':
            self.vods[vod_id]["duration"] = duration


class VODError(Exception):
    """VOD logs are unavailable, or VOD is inaccessible on Twitch."""
