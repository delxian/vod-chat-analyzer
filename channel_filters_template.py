"""Per-channel filters defined manually since the purpose varies from channel to channel."""
#pylint: disable=invalid-name,missing-function-docstring,unused-argument,unused-variable

# To use channel filters, copy this code to channel_filters.py, define functions for channels,
# and add them to the channel_filters dictionary following the example below.

def channel_name(timestamp: str, user: str, message: str, emotes: set[str]):
    # return True if message is good for processing, otherwise False
    # timestamp is a string of the form "HH:MM:SS"
    timestamp = timestamp.replace(':', '')
    time_s = (60*60*int(timestamp[:2]) + 60*int(timestamp[2:4]) + int(timestamp[4:]))
    example_bad_timestamp = time_s < 600
    example_bad_user = "bot" in user
    example_bad_message = "term" in message
    return not (example_bad_timestamp or example_bad_user or example_bad_message)

channel_filters = {
    "channel_name": channel_name
}
