# VOD Chat Analyzer
Manages and analyzes chat logs for Twitch VODs to find interesting timestamps.
## Features
- First-time setup (logs directory, search parameters, Discord Webhook)
- Write new VOD logs and analyze saved VOD logs
- Save results to .txt and/or send in Discord channel via Webhook integration
- Per-channel VOD logs and data management, including emotes
- Data parameters (length and spacing of timestamps, etc.)
- Default presets (general chat trends)
- Custom preset management (global and per-channel)
- Exclude known bots and command messages from results
- Suggest terms for presets using existing VOD logs
- Generate per-VOD graphs of interesting metrics
- Base support for custom per-channel filters (user-writen code)
## Requirements
- Python 3.11 or higher
- [requests](https://pypi.org/project/requests/), [chat_downloader](https://pypi.org/project/chat-downloader/), [discord_webhook](https://pypi.org/project/discord-webhook/)
## License
- [MIT](LICENSE)

## Note
This project was not inspired by [Autoclip](https://autoclip.fugi.tech/), as I thought of this before I knew Autoclip existed. I would however like to turn this into a website/GUI application eventually, as I have implemented a few more interesting features and metrics.