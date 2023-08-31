"""Channel setup, automation, execution of VOD analysis."""
# pylint: disable=invalid-name,multiple-statements
from itertools import chain
import json
import os
import re
import sys

import requests

from analyzer import write_logs, AnalyticsChannel, VODError, DataParams, OutputParams, Params
from config import make_config
from webhook import Webhook


STATUS_CODE_OK = 200

config: dict = {}
try:
    with open("config.json", 'r', encoding="UTF-8") as file:
        config = json.loads(file.read())
except FileNotFoundError:
    print("Configuration file missing, starting first-time setup:")
    config = make_config()

try:
    with open("bots.txt", 'r', encoding="UTF-8") as file:
        BOTS = set(file.read().splitlines())
except FileNotFoundError:
    with open("bots.txt", 'w', encoding="UTF-8") as file:
        pass
    print("Bot list was missing and has been created, " \
          "please add bot usernames and restart " \
          "the program (one lowercase username per line).")
    sys.exit()

webhook = Webhook(config["webhook_url"], config["webhook_username"], config["webhook_avatar"])
data_params = DataParams(BOTS, config["interval"], config["minimum"],config["spacing"],
                         config["msg_results"], config["txt_results"])
output_params = OutputParams(
    config["to_discord_msg"], config["to_discord_txt"], config["to_discord_graph"],
    config["condense"], config["extend"], config["aggregate"]
    )
params = Params(data_params, output_params, config["allow_non_api"])

if not os.path.isdir(config["directory"]):
    os.mkdir(config["directory"])
channels = [item for item in os.scandir(config["directory"]) if item.is_dir()]
channels_disp = [f"{i} - {channel.name}" for i, channel in enumerate(channels, start=1)]
print(f'Valid channels:\n{", ".join(channels_disp)}')
channel_prompt = ("\nInput exact channel username or number from list: "
                  if channels else "Input exact channel username:")
while not (channel := input(channel_prompt).strip().lower()): pass
if channels and channel.isnumeric() and int(channel)-1 in range(len(channels)):
    channel = channels[int(channel)-1].name

channel = AnalyticsChannel(channel, config["directory"], webhook, params)

initial_live_status: bool = False
if (response := requests.get(
        f"https://decapi.me/twitch/uptime/{channel.name}",
        params={"offline_msg": "OFFLINE"}, timeout=5
        )
    ).status_code == STATUS_CODE_OK:
    if "Bad Request" in response.text:
        raise RuntimeError("Channel not found")
    initial_live_status = response.text != "OFFLINE"
    print(f'Channel #{channel.name} is {"online" if initial_live_status else "offline"}.')
else:
    print("Initial live status unavailable.")

if input("Create/update VOD database from the API? (y/n): ") == 'y':
    channel.vod_manager.update_vods()
    channel.vod_manager.store_vods()

if input("Create/update emote database from the API? (y/n): ") == 'y':
    while True:
        try:
            channel.emote_manager.fetch_emotes()
        except RuntimeError as exc:
            print(exc)
            if input("Try again? (y/n)") == 'n': break
        else:
            channel.emote_manager.store_emotes()
            channel.preset_manager.term_manager.emotes = set(channel.emote_manager.emotes.unsorted)
            break

if input("Edit presets? (y/n): ") == 'y':
    while True:
        print("+active -inactive")
        for preset_name, preset in chain(
            channel.preset_manager.presets.global_.items(),
            channel.preset_manager.presets.local.items(),
            ):
            print(f"{'+' if preset.active else '-'} Preset name: {preset_name}")
            queries_disp = []
            for query in preset.queries:
                match query:
                    case str():
                        queries_disp.append(query)
                    case list():
                        queries_disp.append(f'[{", ".join(query)}]')
            print(f'    query: {", ".join(queries_disp)}')
        match input("Create, toggle, or delete presets? " \
                    "[leave blank to continue] (c/t/d): "):
            case 'c':
                channel.preset_manager.write_preset()
            case 't':
                toggles = input("Input exact preset names to toggle separated by ';': ").split(';')
                toggles = [preset_name.strip() for preset_name in toggles]
                channel.preset_manager.presets.toggle(toggles)
            case 'd':
                deletes = input("Input exact preset names to delete separated by ';': ").split(';')
                deletes = [preset_name.strip() for preset_name in deletes]
                channel.preset_manager.presets.delete(deletes)
            case _:
                break

if input("Write new VOD logs? (y/n): ") == 'y':
    while True:
        print("All VODs (< local, + api, & both, - neither):")
        for vods_i, (vod_id, vod_data) in enumerate(channel.vod_manager.vods.items(), start=1):
            in_api = '-<+&'[2*int(vod_data["api"])+int(vod_data["local"])]
            print(f'    {in_api} {vods_i} -{" "+vod_data["title"] if vod_data["title"] else ""} ' \
                f'[{vod_data["duration"]}] ({vod_id})')
        if not(new_nums := input("\n[New VODs] Input VOD #s (not IDs) " \
               "(e.g. 1 2 3, 4-6, or 7+) [leave blank to continue]: ")): break
        vods = sorted(channel.vod_manager.vods.items())
        vod_count = len(channel.vod_manager.vods)
        if (add_match := re.fullmatch(r"^(\d+)-(\d+)", new_nums)):
            start_num, end_num = add_match.groups()
            new_nums = list(range(int(start_num), int(end_num)+1))
        elif (add_match := re.fullmatch(r"^(\d+)\+", new_nums)):
            start_num = add_match[1]
            new_nums = list(range(int(start_num), vod_count+1))
        elif all(new_num.isnumeric() for new_num in new_nums.strip().split(' ')):
            new_nums = [int(new_num) for new_num in new_nums.strip().split(' ')]
        else:
            raise ValueError("Invalid VOD numbers")
        new_ids = []
        for new_num in new_nums:
            if new_num in range(1, vod_count+1):
                new_id, new_data = vods[new_num-1]
                if not new_data["local"]:
                    new_ids.append(new_id)
        for new_id in new_ids:
            write_logs(channel.directory, channel.name, new_id)
        channel.vod_manager.update_vods()
        channel.vod_manager.store_vods()

if not channel.vod_manager.vods:
    raise VODError("No VOD log files found")
vods = sorted(
    [(vod_id, vod_data) for vod_id, vod_data in channel.vod_manager.vods.items()
        if (vod_data["local"] if config["allow_non_api"]
            else (vod_data["local"] and vod_data["api"]))]
    )
print(f'Available VOD logs{" (* Twitch VOD available)" if config["allow_non_api"] else ""}:')
for vods_i, (vod_id, vod_data) in enumerate(vods, start=1):
    in_api = '*' if vod_data["api"] else ''
    print(f'    {in_api} {vods_i} -{" "+vod_data["title"] if vod_data["title"] else ""} ' \
        f'[{vod_data["duration"]}] ({vod_id})')
vod_nums = input(
    "\n[Analyze VOD logs] Input VOD #s (not IDs) separated by spaces: "
    ).strip().split(' ')
vod_count = len(vods)
vod_ids = tuple(
    vods[int(vod_num)-1][0] for vod_num in vod_nums
    if vod_num.isnumeric() and int(vod_num) in range(1, vod_count+1)
    )
print("    Selected VOD(s):")
print('\n'.join(
        [f'     {channel.vod_manager.vods[vod_id]["title"]} ({vod_id})'
        for vod_id in vod_ids]
        )
    )
for vod_id in vod_ids:
    try:
        channel.handle_vod(vod_id)
    except RuntimeError as e:
        print(f"Error handling VOD {vod_id}: {e}")
