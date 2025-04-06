import requests
import time
import os
import json
import re
from datetime import datetime, UTC  # Import UTC
from utils import (
    scrape_web_page,
    generate,
    generate_yt,
)  # Assuming these are still relevant
from dotenv import load_dotenv

load_dotenv()

# --- Configuration from Environment Variables ---
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("DISCORD_TOKEN environment variable not set")

# Source Channel IDs (expecting a JSON list string like '["123", "456"]')
# This is now unused if BOT_CATEGORY_NAME is used, but keep for potential fallback/future use
source_channel_ids_str = os.getenv("SOURCE_CHANNEL_IDS")
if not source_channel_ids_str:
    print("Warning: SOURCE_CHANNEL_IDS not set. Relying solely on BOT_CATEGORY_NAME.")
    SOURCE_CHANNEL_IDS = [] # Default to empty list
else:
    try:
        # Convert string IDs to integers
        SOURCE_CHANNEL_IDS = [int(id_str) for id_str in json.loads(source_channel_ids_str)]
    except (json.JSONDecodeError, ValueError) as e:
        raise ValueError(
            f"Invalid format for SOURCE_CHANNEL_IDS: {e}. Expected JSON list of strings (e.g., '[\"12345\"]')"
        )

# Forum Channel ID (where daily summary posts will be created)
forum_channel_id_str = os.getenv("FORUM_CHANNEL_ID")
if not forum_channel_id_str:
    raise ValueError("FORUM_CHANNEL_ID environment variable not set")
try:
    FORUM_CHANNEL_ID = int(forum_channel_id_str)
except ValueError:
    raise ValueError("Invalid format for FORUM_CHANNEL_ID: Must be an integer.")

# Category Name to Monitor - NEW
BOT_CATEGORY_NAME = os.getenv("BOT_CATEGORY_NAME")
if not BOT_CATEGORY_NAME:
    raise ValueError("BOT_CATEGORY_NAME environment variable not set")

# Guild ID (to find category channels) - NEW
guild_id_str = os.getenv("GUILD_ID")
if not guild_id_str:
    raise ValueError("GUILD_ID environment variable not set")
try:
    GUILD_ID = int(guild_id_str)
except ValueError:
    raise ValueError("Invalid format for GUILD_ID: Must be an integer.")


# --- Constants ---
DISCORD_API_URL = "https://discord.com/api/v10"
MESSAGE_FETCH_LIMIT = 50  # How many recent messages to check per source channel (adjust as needed)
FORUM_THREAD_CHECK_LIMIT = 5 # How many messages to check in the forum channel to find today's post
SUMMARY_CHECK_LIMIT = 50 # How many messages to check within a thread for duplicates
FORUM_SEARCH_THREAD_LIMIT = 25 # How many active/archived threads to search for duplicates

HEADERS = {
    "Authorization": f"Bot {TOKEN}",
    "User-Agent": "DiscordBot (Forum Summarizer, v0.1)",
    "Content-Type": "application/json",
}
RATE_LIMIT_SLEEP = 1  # Simple sleep duration in seconds for rate limits
MAX_MESSAGE_LENGTH = 2000  # Discord message length limit

# --- Discord API Helper Functions ---

def handle_rate_limit(response):
    """Checks for 429 rate limit and sleeps if necessary."""
    if response.status_code == 429:
        retry_after = response.json().get("retry_after", RATE_LIMIT_SLEEP)
        print(f"Rate limited. Sleeping for {retry_after} seconds.")
        time.sleep(retry_after)
        return True
    return False


def get_channel_messages(channel_id, limit):
    """Fetches recent messages from a channel via HTTP GET."""
    url = f"{DISCORD_API_URL}/channels/{channel_id}/messages?limit={limit}"
    while True:
        try:
            response = requests.get(url, headers=HEADERS)
            if handle_rate_limit(response):
                continue
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error fetching messages from channel {channel_id}: {e}")
            return None
        except Exception as e:
            print(f"Unexpected error fetching messages: {e}")
            return None


def get_guild_channels(guild_id):
    """Fetches all channels for a given guild ID via HTTP GET."""
    url = f"{DISCORD_API_URL}/guilds/{guild_id}/channels"
    print(f"Fetching channels for guild {guild_id}...")
    while True:
        try:
            response = requests.get(url, headers=HEADERS)
            if handle_rate_limit(response):
                continue
            response.raise_for_status()
            print(f"Successfully fetched channels for guild {guild_id}.")
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error fetching channels for guild {guild_id}: {e}")
            return None
        except Exception as e:
            print(f"Unexpected error fetching guild channels: {e}")
            return None

# --- Forum/Thread Fetching Functions ---

def get_active_guild_threads(guild_id):
    """Fetches all active threads for a given guild ID via HTTP GET."""
    url = f"{DISCORD_API_URL}/guilds/{guild_id}/threads/active"
    # print(f"Fetching active threads for guild {guild_id}...")
    while True:
        try:
            response = requests.get(url, headers=HEADERS)
            if handle_rate_limit(response):
                continue
            response.raise_for_status()
            data = response.json()
            threads = data.get('threads', [])
            # print(f"Successfully fetched {len(threads)} active threads for guild.")
            # Note: This includes threads from ALL channels the bot can see.
            return threads
        except requests.exceptions.RequestException as e:
            print(f"Error fetching active guild threads: {e}")
            return []
        except Exception as e:
            print(f"Unexpected error fetching active guild threads: {e}")
            return []

def get_archived_threads(channel_id, limit=50, public=True):
    """Fetches archived threads (public or private) for a specific channel."""
    thread_type = "public" if public else "private"
    url = f"{DISCORD_API_URL}/channels/{channel_id}/threads/archived/{thread_type}?limit={limit}"
    # print(f"Fetching archived {thread_type} threads for channel {channel_id}...")
    while True:
        try:
            response = requests.get(url, headers=HEADERS)
            if handle_rate_limit(response):
                continue
            response.raise_for_status()
            data = response.json()
            threads = data.get('threads', [])
            # print(f"Successfully fetched {len(threads)} archived {thread_type} threads.")
            return threads
        except requests.exceptions.RequestException as e:
            print(f"Error fetching archived {thread_type} threads: {e}")
            return []
        except Exception as e:
            print(f"Unexpected error fetching archived {thread_type} threads: {e}")
            return []

def check_if_summarized_in_thread(url_to_check, thread_id):
    """Checks if a URL has already been posted in a specific thread."""
    # print(f"  Checking messages within thread {thread_id} for URL: {url_to_check}")
    messages = get_channel_messages(thread_id, SUMMARY_CHECK_LIMIT)
    if messages is None:
        print(f"    Could not fetch messages from thread {thread_id}.")
        return False
    for message in messages:
        if url_to_check in message.get("content", ""):
            # print(f"    URL found in thread message {message['id']}")
            return True
    return False

def check_if_summarized_in_forum(forum_channel_id, url_to_check, thread_limit=FORUM_SEARCH_THREAD_LIMIT):
    """Checks if a URL exists in recent active or archived public threads of a specific forum channel."""
    # print(f"Checking forum {forum_channel_id} history for previous summary of: {url_to_check}")

    threads_to_check = []
    checked_thread_ids = set() # Keep track of IDs to avoid double-checking

    # 1. Get active threads in the guild and filter by parent_id
    active_guild_threads = get_active_guild_threads(GUILD_ID)
    active_forum_threads = []
    for thread in active_guild_threads:
        if thread.get('parent_id') == str(forum_channel_id): # parent_id is string
             active_forum_threads.append(thread)
             checked_thread_ids.add(thread.get('id')) # Add ID here
    # print(f"Found {len(active_forum_threads)} active threads in target forum.")
    threads_to_check.extend(active_forum_threads)

    # 2. Get recent archived public threads from the specific forum channel
    # Adjust limit based on how many active threads we already plan to check
    remaining_limit = max(0, thread_limit - len(threads_to_check))
    if remaining_limit > 0:
        archived_threads = get_archived_threads(forum_channel_id, limit=remaining_limit, public=True)
        # Only add archived threads we haven't already seen in the active list
        for thread in archived_threads:
            if thread.get('id') not in checked_thread_ids:
                threads_to_check.append(thread)
                checked_thread_ids.add(thread.get('id'))

    # 3. Check messages within each relevant thread
    checked_thread_count = 0
    for thread in threads_to_check: # Now iterates through combined active/archived list
        thread_id = thread.get('id')
        thread_name = thread.get('name', 'Unknown Thread')
        # No need to check thread_id again, already handled by set

        # Limit how many distinct threads we actually check messages in
        if checked_thread_count >= thread_limit:
             print(f"  Reached thread check limit ({thread_limit}). Stopping forum search.")
             break

        checked_thread_count += 1
        print(f"  Checking thread (Active/Archived): {thread_name} ({thread_id})")
        if check_if_summarized_in_thread(url_to_check, thread_id):
            #  print(f"    URL found in thread {thread_id}.")
             return True

    # print(f"URL {url_to_check} not found in checked forum threads.")
    return False


def send_discord_message(channel_id, content):
    """Sends a message to a Discord channel/thread via HTTP POST, handling splitting."""
    max_len = MAX_MESSAGE_LENGTH
    url = f"{DISCORD_API_URL}/channels/{channel_id}/messages"

    def post_chunk(chunk_content):
        payload = json.dumps({"content": chunk_content})
        while True:
            try:
                response = requests.post(url, headers=HEADERS, data=payload)
                if handle_rate_limit(response): continue
                response.raise_for_status()
                print(f"Message chunk sent successfully to channel/thread {channel_id}.")
                return True
            except requests.exceptions.RequestException as e:
                print(f"Error sending message chunk to channel/thread {channel_id}: {e}")
                time.sleep(2)
                print("Retrying message send...")
            except Exception as e:
                print(f"Unexpected error sending message chunk: {e}")
                return False
        return False

    if len(content) <= max_len:
        return post_chunk(content)
    else:
        print(f"Message too long ({len(content)} chars). Splitting...")
        chunks = []
        current_chunk = ""
        for line in content.split("\n"):
            if len(current_chunk) + len(line) + 1 > max_len:
                chunks.append(current_chunk)
                current_chunk = line + "\n"
            else:
                current_chunk += line + "\n"
        if current_chunk: chunks.append(current_chunk.strip())

        success = True
        for i, chunk in enumerate(chunks):
            print(f"Sending chunk {i+1}/{len(chunks)}...")
            if not post_chunk(chunk):
                success = False
                break
            time.sleep(0.5)
        return success


def send_message_to_thread(thread_id, content):
    """Sends a message to a specific thread, handling splitting."""
    print(f"Attempting to send message to thread {thread_id}...")
    return send_discord_message(thread_id, content)


def find_daily_thread(forum_channel_id, post_title):
    """
    Finds an active or recently archived thread in the forum channel with a specific title.
    """
    print(f"Searching for active/archived thread '{post_title}' in forum channel {forum_channel_id}...")

    # 1. Check Active Threads in the specific forum (more reliable)
    active_guild_threads = get_active_guild_threads(GUILD_ID)
    for thread in active_guild_threads:
         if thread.get('parent_id') == str(forum_channel_id) and thread.get('name') == post_title:
             thread_id = thread.get('id')
             print(f"Found matching active thread: ID {thread_id}")
             return int(thread_id)

    # 2. Check recent messages for thread creation message (Fallback for very recent threads)
    messages = get_channel_messages(forum_channel_id, FORUM_THREAD_CHECK_LIMIT * 2)
    if messages:
        for message in messages:
            thread_info = message.get("thread")
            if thread_info and thread_info.get("name") == post_title:
                # Verify it wasn't found in the active list already
                thread_id = thread_info.get("id")
                is_active = any(t.get('id') == thread_id for t in active_guild_threads if t.get('parent_id') == str(forum_channel_id))
                if not is_active:
                    print(f"Found matching thread via message (likely just created): ID {thread_id}")
                    return int(thread_id)

    # 3. Check Archived Threads
    archived_threads = get_archived_threads(forum_channel_id, limit=FORUM_THREAD_CHECK_LIMIT, public=True)
    for thread in archived_threads:
        if thread.get('name') == post_title:
            thread_id = thread.get('id')
            print(f"Found matching archived thread: ID {thread_id}")
            return int(thread_id)

    print(f"Thread '{post_title}' not found.")
    return None


def create_daily_thread(forum_channel_id, post_title, initial_content):
    """Creates a new thread in the forum channel."""
    print(f"Creating new thread '{post_title}' in forum channel {forum_channel_id}...")
    url = f"{DISCORD_API_URL}/channels/{forum_channel_id}/threads"
    if len(initial_content) > MAX_MESSAGE_LENGTH:
        print(f"Warning: Initial thread message content is too long ({len(initial_content)} chars). Truncating.")
        initial_content = initial_content[: MAX_MESSAGE_LENGTH - 10] + "..."
    payload = json.dumps({"name": post_title, "auto_archive_duration": 1440, "message": {"content": initial_content}})
    while True:
        try:
            response = requests.post(url, headers=HEADERS, data=payload)
            if handle_rate_limit(response): continue
            response.raise_for_status()
            new_thread_data = response.json()
            new_thread_id = new_thread_data.get("id")
            print(f"Successfully created thread: ID {new_thread_id}")
            return int(new_thread_id)
        except requests.exceptions.RequestException as e:
            print(f"Error creating thread: {e}")
            return None
        except Exception as e:
            print(f"Unexpected error creating thread: {e}")
            return None


def format_summaries(summaries_dict, max_length=MAX_MESSAGE_LENGTH):
    """
    Formats the collected summaries into one or more message strings,
    respecting the max_length. Returns a list of strings.
    """
    message_chunks = []
    current_chunk = ""
    if not summaries_dict: return []
    for url, [summary,channel_name] in summaries_dict.items():
        entry = f"**URL {channel_name}:** {url}\n**Summary:**\n{summary}\n\n---\n\n"
        entry_len = len(entry)
        if entry_len > max_length:
            # print(f"Warning: Single summary for {url} exceeds max length ({entry_len}). Splitting summary.")
            url_line = f"**URL:** {url}\n**Summary:**\n"
            separator = "\n\n---\n\n"
            remaining_len = max_length - len(url_line) - len(separator)
            summary_parts = [summary[i : i + remaining_len] for i in range(0, len(summary), remaining_len)]
            for i, part in enumerate(summary_parts):
                part_entry = url_line + part + (f"\n...(continued)\n{separator}" if i < len(summary_parts) - 1 else f"\n{separator}")
                if len(current_chunk) + len(part_entry) > max_length and current_chunk:
                    message_chunks.append(current_chunk.strip())
                    current_chunk = part_entry
                else: current_chunk += part_entry
        elif len(current_chunk) + entry_len > max_length:
            message_chunks.append(current_chunk.strip())
            current_chunk = entry
        else: current_chunk += entry
    if current_chunk: message_chunks.append(current_chunk.strip())
    return message_chunks

# --- Refactored Message Processing Logic ---

def process_message(message, processed_urls_status, current_target_thread_id, post_title,channel_name):
    """
    Processes a single message: extracts URL, checks duplicates, summarizes, and posts.
    Returns the potentially updated target_thread_id and the updated processed_urls_status.
    """
    # print(f"\n--- Checking Message ID: {message.get('id')} ---")
    content = message.get("content", "")
    # embeds = message.get("embeds", [])
    # author_info = message.get("author", {})
    # author_name = author_info.get("username", "Unknown")
    # is_bot = author_info.get("bot", False)

    # print(f"  Author: {author_name} (Bot: {is_bot})")
    # print(f"  Content Snippet: {content[:100]}...")
    # print(f"  Embeds Found: {len(embeds)}")

    # if is_bot: return current_target_thread_id, processed_urls_status # Bot check is commented out

    url_regex = r"(https?://[^\s]+)"
    urls = re.findall(url_regex, content)
    if not urls and embeds:
        for embed in embeds:
            if embed.get("url"): urls.append(embed["url"])

    if not urls:
        return current_target_thread_id, processed_urls_status

    url_to_process = urls[0]
    # print(f"  Checking URL from {author_name}: {url_to_process}")

    if url_to_process in processed_urls_status:
        # print(f"    Skipping (already processed in this run - status: {processed_urls_status[url_to_process]}).")
        return current_target_thread_id, processed_urls_status

    # --- Forum-wide duplicate check (Active & Archived) ---
    if check_if_summarized_in_forum(FORUM_CHANNEL_ID, url_to_process):
        # print(f"    Skipping (already summarized in a recent forum thread).")
        processed_urls_status[url_to_process] = "DUPLICATE_FORUM"
        return current_target_thread_id, processed_urls_status
    # --- End Forum Check ---

    summary_text = None
    print(f"    Attempting to summarize...")
    if "x.com" in url_to_process:
        # print(f"      Skipping x.com URL.")
        processed_urls_status[url_to_process] = "SKIPPED_XCOM"
    elif "youtube.com" in url_to_process or "youtu.be" in url_to_process:
        print(f"      Processing as YouTube URL...")
        try:
            summary_text = generate_yt(url_to_process)
            print(f"      generate_yt result: {'<empty>' if not summary_text else summary_text[:50] + '...'}")
        except Exception as e:
            print(f"      Exception during generate_yt: {e}")
            summary_text = None
    else:
        # print(f"      Processing as general URL...")
        text_content = None
        try:
            # print(f"        Attempting to scrape web page...")
            text_content = scrape_web_page(url_to_process)
            # print(f"        scrape_web_page result: {'<empty or failed>' if not text_content else str(len(text_content)) + ' chars'}")
        except Exception as e:
            print(f"        Exception during scrape_web_page: {e}")

        if text_content:
            prompt = f"Summarize this content concisely, focusing on the key information:\n\n{text_content}"
            try:
                # print(f"        Attempting to generate summary from scraped text...")
                summary_text = generate(prompt)
                # print(f"        generate result: {'<empty>' if not summary_text else summary_text[:50] + '...'}")
                if not summary_text: print("        Summary generation returned empty string.")
            except Exception as e:
                print(f"        Exception during generate: {e}")
                summary_text = None
        else:
             processed_urls_status[url_to_process] = "FAILED_SCRAPE"


    if summary_text:
        summary_text = summary_text.strip()
        if summary_text:
            print(f"    Successfully summarized.")
            processed_urls_status[url_to_process] = "SUMMARIZED"

            print(f"    Attempting to post summary for {url_to_process}...")
            post_successful = False
            thread_created_this_time = False
            temp_target_thread_id = current_target_thread_id

            if temp_target_thread_id is None:
                print("      Thread ID unknown, attempting to find or create...")
                temp_target_thread_id = find_daily_thread(FORUM_CHANNEL_ID, post_title)
                if temp_target_thread_id is None:
                    print("      Existing thread not found, creating new one...")
                    formatted_chunks = format_summaries({url_to_process: [summary_text,channel_name]})
                    if formatted_chunks:
                        first_chunk = formatted_chunks.pop(0)
                        temp_target_thread_id = create_daily_thread(FORUM_CHANNEL_ID, post_title, first_chunk)
                        if temp_target_thread_id:
                            print(f"      Successfully created thread {temp_target_thread_id} with first summary.")
                            thread_created_this_time = True
                            for i, chunk in enumerate(formatted_chunks):
                                print(f"        Sending chunk {i+1}/{len(formatted_chunks)} for initial summary...")
                                if not send_message_to_thread(temp_target_thread_id, chunk):
                                    print(f"        Failed to send chunk {i+1}. Summary may be incomplete.")
                                    processed_urls_status[url_to_process] = "POST_FAILED_CHUNK"
                                    break
                                time.sleep(1)
                            else: post_successful = True
                        else:
                            print("      Failed to create thread.")
                            processed_urls_status[url_to_process] = "POST_FAILED_THREAD_CREATE"
                    else:
                        print("      Failed to format summary for initial thread post.")
                        processed_urls_status[url_to_process] = "POST_FAILED_FORMATTING"
                else:
                    print(f"      Found existing thread {temp_target_thread_id}.")

            if temp_target_thread_id is not None and not thread_created_this_time:
                print(f"      Posting summary to existing/found thread {temp_target_thread_id}...")
                formatted_chunks = format_summaries({url_to_process: [summary_text,channel_name]})
                if formatted_chunks:
                    for i, chunk in enumerate(formatted_chunks):
                        print(f"        Sending chunk {i+1}/{len(formatted_chunks)}...")
                        if not send_message_to_thread(temp_target_thread_id, chunk):
                            print(f"        Failed to send chunk {i+1}. Summary post failed.")
                            processed_urls_status[url_to_process] = "POST_FAILED_CHUNK"
                            break
                        time.sleep(1)
                    else: post_successful = True
                else:
                    print("      Failed to format summary for posting.")
                    processed_urls_status[url_to_process] = "POST_FAILED_FORMATTING"

            if post_successful:
                processed_urls_status[url_to_process] = "SUMMARIZED_POSTED"
                current_target_thread_id = temp_target_thread_id # Persist the found/created ID
            elif url_to_process not in processed_urls_status:
                processed_urls_status[url_to_process] = "POST_FAILED_UNKNOWN"

        else:
            print(f"    Summary generation resulted in empty string.")
            processed_urls_status[url_to_process] = "FAILED_EMPTY_SUMMARY"
    else:
        if url_to_process not in processed_urls_status:
             print(f"    Failed to generate summary.")
             processed_urls_status[url_to_process] = "FAILED_SUMMARY"

    time.sleep(1)
    return current_target_thread_id, processed_urls_status


# --- Main Logic ---

def main():
    print("Starting forum summarizer script...")
    now = datetime.now(UTC)
    today_str = now.strftime("%Y-%m-%d")
    day_name = now.strftime("%A")
    post_title = f"Summary for {today_str} ({day_name})"
    print(f"Target post title: {post_title}")

    target_thread_id = None
    processed_urls_status = {}

    print("\n--- Fetching messages, Summarizing, and Posting Incrementally ---")

    print(f"\nIdentifying channels in category '{BOT_CATEGORY_NAME}' for guild {GUILD_ID}...")
    all_channels = get_guild_channels(GUILD_ID)
    category_channels_to_process = []
    category_id = None

    if all_channels:
        for channel in all_channels:
            if channel.get("type") == 4 and channel.get("name") == BOT_CATEGORY_NAME:
                category_id = channel.get("id")
                print(f"Found category '{BOT_CATEGORY_NAME}' with ID: {category_id}")
                break
        if category_id:
            for channel in all_channels:
                if channel.get("type") == 0 and channel.get("parent_id") == category_id:
                    channel_id_int = int(channel.get("id"))
                    if channel_id_int != FORUM_CHANNEL_ID:
                        if channel.get("name") != 'jp':
                            category_channels_to_process.append(
                                {"id": channel_id_int, "name": channel.get("name", f"Channel {channel_id_int}")}
                            )
                        print(f"  + Found text channel '{channel.get('name')}' ({channel_id_int}) in category.")
                    else:
                        print(f"  - Skipping channel '{channel.get('name')}' ({channel_id_int}) as it's the target forum channel.")
            print(f"Identified {len(category_channels_to_process)} channels to process in category '{BOT_CATEGORY_NAME}'.")
        else:
            print(f"Warning: Category '{BOT_CATEGORY_NAME}' not found in guild {GUILD_ID}.")
    else:
        print("Warning: Could not fetch guild channels. Cannot process category channels.")

    if not category_channels_to_process:
        print("No category channels to process.")
    else:
        for channel_info in category_channels_to_process:
            channel_id = channel_info["id"]
            channel_name = channel_info["name"]
            print(f"\nProcessing category channel: {channel_name} ({channel_id})")

            messages = get_channel_messages(channel_id, MESSAGE_FETCH_LIMIT)
            if not messages:
                print(f"No messages found or error fetching from channel {channel_name} ({channel_id}).")
                continue

            print(f"Fetched {len(messages)} messages from channel {channel_name} ({channel_id})")
            for message in reversed(messages):
                 target_thread_id, processed_urls_status = process_message(
                     message,
                     processed_urls_status,
                     target_thread_id,
                     post_title,
                     channel_name
                 )

    print("\nScript finished.")


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:
        print(f"Configuration Error: {e}")
    except KeyboardInterrupt:
        print("\nScript interrupted by user.")
    except Exception as e:
        print(f"An unexpected error occurred during execution: {e}")
