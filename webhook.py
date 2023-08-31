"""Class for Discord webhook operation."""
# pylint: disable=multiple-statements
from dataclasses import dataclass

from discord_webhook import DiscordWebhook


@dataclass(slots=True)
class Webhook:
    """Manage Discord webhook data and operation."""
    url: str
    username: str
    avatar: str | None
    last_sent_message: tuple[str | None, str | None, str | None] = (None, None, None)

    def send(self, label: str, message: str | None = None, filepath: str | None = None):
        """Send a message via the designated Discord Webhook URL."""
        if not (message or filepath): return
        webhook = DiscordWebhook(
            url = self.url,
            rate_limit_retry = True,
            content = message,
            username = self.username,
            avatar_url = self.avatar
            )
        if filepath:
            try:
                with open(filepath, 'rb') as file:
                    webhook.add_file(file=file.read(), filename=filepath)
            except FileNotFoundError:
                print(f'File "{filepath}" not found!')
        if not (response := webhook.execute()).ok:
            raise RuntimeError(f'Error occurred sending "{label}", ' \
                               f'status code {response.status_code}')
        print(f'Sent "{label}" to Discord successfully!')
        self.last_sent_message = (label, message, filepath)
