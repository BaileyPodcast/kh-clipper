"""
Shared yt-dlp resilience options.

YouTube blocks plain datacenter IPs ("Sign in to confirm you're not a bot"), which
breaks downloads from cloud workers (Modal, etc.). Two mitigations, both applied by
every yt-dlp call (fetch + cut):

1. Alternate player clients (tv / ios / mweb) — these often serve formats where the
   default `web` client gets the bot wall.
2. Optional cookies — if env `YT_COOKIES_FILE` points to a Netscape cookies.txt
   (exported from a logged-in YouTube session), use it. This is the reliable fix
   when the client trick isn't enough.
"""
import os

# Clients that tend to bypass the bot wall on datacenter IPs. Order = preference.
PLAYER_CLIENTS = ["tv", "ios", "mweb", "web_safari"]


def resilience_opts():
    opts = {"retries": 3, "extractor_retries": 3}
    cookies = os.environ.get("YT_COOKIES_FILE")
    if cookies and os.path.exists(cookies):
        # Cookies clear the bot wall; let yt-dlp use the default web client so all
        # normal formats (separate video+audio) are available.
        opts["cookiefile"] = cookies
    else:
        # No cookies: try alternate player clients to dodge the bot wall instead.
        opts["extractor_args"] = {"youtube": {"player_client": PLAYER_CLIENTS}}
    return opts
