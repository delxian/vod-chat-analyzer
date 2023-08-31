"""Setup for configuration file."""
# pylint: disable=invalid-name,multiple-statements
from tkinter.filedialog import askdirectory
from typing import Any
import json


DEFAULT_INTERVAL = 30
DEFAULT_MINIMUM = 10
DEFAULT_SPACING = 60
DEFAULT_MSG_RESULTS = 10
DEFAULT_TXT_RESULTS = 50
DEFAULT_WEBHOOK_USERNAME = "VOD Chat Analyzer Bot"

def make_config() -> dict:
    """Set up configuration for the bot."""
    config: dict[str, Any] = {"directory": askdirectory(title="Select your VOD logs directory")}
    print("Leave the following inputs blank for the default values. (y/n defaults to n)")
    config = set_data_params(config)
    config["allow_non_api"] = input(
        "Allow analysis of local-only VODs (links unavailable)? (y/n): ").strip() == 'y'
    config = set_webhook_data(config)
    config = set_output_params(config)
    with open("config.json", 'w', encoding="UTF-8") as file:
        file.write(json.dumps(config, indent=4, separators=(',', ': ')))

    return config

def set_data_params(config: dict[str, Any]):
    """Set up VOD analysis data parameters."""
    interval = input(f"Length (s) of each timeslice [default {DEFAULT_INTERVAL}]: ")
    minimum = input(f"Minimum frequency [score] per timeslice [default {DEFAULT_MINIMUM}]: ")
    spacing = input(f"Minimum time (s) between timestamps [default {DEFAULT_SPACING}]: ")
    msg_results = input(
        f"Timestamp count for Discord message (~20 maximum) [default {DEFAULT_MSG_RESULTS}]: ")
    txt_results = input(f"Timestamp count for .txt [default {DEFAULT_TXT_RESULTS}]: ")
    parameters = (
        ("interval", interval, DEFAULT_INTERVAL),
        ("minimum", minimum, DEFAULT_MINIMUM),
        ("spacing", spacing, DEFAULT_SPACING),
        ("msg_results", msg_results, DEFAULT_MSG_RESULTS),
        ("txt_results", txt_results, DEFAULT_TXT_RESULTS)
        )
    for label, parameter, default in parameters:
        config[label] = int(parameter.strip()) if parameter.strip() else default
    return config

def set_webhook_data(config: dict[str, Any]):
    """Set up Discord Webhook integration."""
    print("[Edit Channel >> Integrations >> Webhooks >> Create Webhook >> Copy Webhook URL]")
    config["webhook_url"] = input("Paste your Discord channel's Webhook URL (required): ").strip()
    config["webhook_username"] = input(
        'Discord username for this bot [default "VOD Chat Analyzer Bot"]: '
        ).strip() or DEFAULT_WEBHOOK_USERNAME
    config["webhook_avatar"] = input(
        "Image URL for the bot's avatar [default None]: ").strip() or None
    return config

def set_output_params(config: dict[str, Any]):
    """Set up VOD analysis output parameters."""
    config["to_discord_msg"] = input("Send preset results via Discord message? (y/n): ") == 'y'
    config["to_discord_txt"] = input("Send preset results via Discord .txt upload? (y/n): ") == 'y'
    config["to_discord_graph"] = input("Send stats graph via Discord image upload? (y/n): ") == 'y'
    config["condense"] = input(
        "Condense results (combine duplicate timestamps, list matching presets only) " \
        "in .txt upload? [not recommended for beginners] (y/n): ") == 'y'
    print("The next setting applies only to condensed results.\n" \
          "Normal results will always use the .txt timestamp count.")
    config["extend"] = input(
        f'Increase raw timestamp count (from {config["msg_results"]} ' \
        f'to {config["txt_results"]}) for condensed results? (y/n)'
        ) == 'y'
    config["aggregate"] = input(
        "Include aggregate timestamps (those shown in the graph) in .txt upload? (y/n): ") == 'y'
    return config
