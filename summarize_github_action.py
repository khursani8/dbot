import requests
import time
import os
import json
import re
from utils import scrape_web_page, generate, generate_yt # Assuming send_long_message is no longer needed directly
from dotenv import load_dotenv

load_dotenv()

# --- Configuration from Environment Variables ---
TOKEN = os.getenv('DISCORD_TOKEN')
if not TOKEN:
    raise ValueError("DISCORD_TOKEN environment variable not set")

# Source Channel IDs (expecting a JSON list string like '["123", "456"]')
source_channel_ids_str = os.getenv('SOURCE_CHANNEL_IDS')
if not source_channel_ids_str:
    raise ValueError("SOURCE_CHANNEL_IDS environment variable not set")
try:
    # Convert string IDs to integers
    SOURCE_CHANNEL_IDS = [int(id_str) for id_str in json.loads(source_channel_ids_str)]
except (json.JSONDecodeError, ValueError) as e:
    raise ValueError(f"Invalid format for SOURCE_CHANNEL_IDS: {e}. Expected JSON list of strings (e.g., '[\"12345\"]')")

# Summary Channel ID (where all summaries are sent)
summary_channel_id_str = os.getenv('SUMMARY_CHANNEL_ID')
if not summary_channel_id_str:
    raise ValueError("SUMMARY_CHANNEL_ID environment variable not set")
try:
    SUMMARY_CHANNEL_ID = int(summary_channel_id_str)
except ValueError:
     raise ValueError("Invalid format for SUMMARY_CHANNEL_ID: Must be an integer.")

# Target Channel ID (for summaries from SOURCE_CHANNEL_IDS) - NEW
target_channel_id_str = os.getenv('TARGET_CHANNEL_ID')
if not target_channel_id_str:
    raise ValueError("TARGET_CHANNEL_ID environment variable not set")
try:
    TARGET_CHANNEL_ID = int(target_channel_id_str)
except ValueError:
     raise ValueError("Invalid format for TARGET_CHANNEL_ID: Must be an integer.")

# Category Name to Monitor - NEW
BOT_CATEGORY_NAME = os.getenv('BOT_CATEGORY_NAME')
if not BOT_CATEGORY_NAME:
    raise ValueError("BOT_CATEGORY_NAME environment variable not set")

# Guild ID (to find category channels) - NEW
guild_id_str = os.getenv('GUILD_ID')
if not guild_id_str:
    raise ValueError("GUILD_ID environment variable not set")
try:
    GUILD_ID = int(guild_id_str)
except ValueError:
     raise ValueError("Invalid format for GUILD_ID: Must be an integer.")


# --- Constants ---
DISCORD_API_URL = "https://discord.com/api/v10" # Using API v10
MESSAGE_FETCH_LIMIT = 100 # How many recent messages to check per channel
SUMMARY_CHECK_LIMIT = 100 # How many recent messages to check in summary channel
HEADERS = {
    "Authorization": f"Bot {TOKEN}",
    "User-Agent": "DiscordBot (GitHub Action Summarizer, v0.1)", # Good practice
    "Content-Type": "application/json"
}
RATE_LIMIT_SLEEP = 1 # Simple sleep duration in seconds for rate limits

# --- Discord API Helper Functions ---

def handle_rate_limit(response):
    """Checks for 429 rate limit and sleeps if necessary."""
    if response.status_code == 429:
        retry_after = response.json().get("retry_after", RATE_LIMIT_SLEEP)
        print(f"Rate limited. Sleeping for {retry_after} seconds.")
        time.sleep(retry_after)
        return True
    return False

def send_discord_message(channel_id, content):
    """Sends a message to a Discord channel via HTTP POST, handling splitting."""
    max_len = 2000
    url = f"{DISCORD_API_URL}/channels/{channel_id}/messages"

    def post_chunk(chunk_content):
        payload = json.dumps({"content": chunk_content})
        while True:
            try:
                response = requests.post(url, headers=HEADERS, data=payload)
                if handle_rate_limit(response):
                    continue # Retry after sleep
                response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx) other than 429
                print(f"Message chunk sent successfully to channel {channel_id}.")
                return True
            except requests.exceptions.RequestException as e:
                print(f"Error sending message chunk to channel {channel_id}: {e}")
                # Basic retry logic, could be enhanced
                time.sleep(2)
                print("Retrying message send...")
                # Consider adding a max retry limit
            except Exception as e:
                 print(f"Unexpected error sending message chunk: {e}")
                 return False # Stop trying on unexpected errors
        return False # Should not be reached if retrying indefinitely

    if len(content) <= max_len:
        return post_chunk(content)
    else:
        print(f"Message too long ({len(content)} chars). Splitting...")
        chunks = []
        current_chunk = ""
        # Basic splitting by newline, could be improved to split by word/sentence
        for line in content.split('\n'):
            if len(current_chunk) + len(line) + 1 > max_len:
                chunks.append(current_chunk)
                current_chunk = line + "\n"
            else:
                current_chunk += line + "\n"
        if current_chunk: # Add the last chunk
            chunks.append(current_chunk)

        success = True
        for i, chunk in enumerate(chunks):
            print(f"Sending chunk {i+1}/{len(chunks)}...")
            if not post_chunk(chunk.strip()):
                success = False
                break # Stop sending if one chunk fails
            time.sleep(0.5) # Small delay between chunks
        return success


def get_channel_messages(channel_id, limit):
    """Fetches recent messages from a channel via HTTP GET."""
    url = f"{DISCORD_API_URL}/channels/{channel_id}/messages?limit={limit}"
    while True:
        try:
            response = requests.get(url, headers=HEADERS)
            if handle_rate_limit(response):
                continue # Retry after sleep
            response.raise_for_status()
            return response.json() # Returns list of message objects
        except requests.exceptions.RequestException as e:
            print(f"Error fetching messages from channel {channel_id}: {e}")
            return None # Indicate failure
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
                continue # Retry after sleep
            response.raise_for_status()
            print(f"Successfully fetched channels for guild {guild_id}.")
            return response.json() # Returns list of channel objects
        except requests.exceptions.RequestException as e:
            print(f"Error fetching channels for guild {guild_id}: {e}")
            return None # Indicate failure
        except Exception as e:
             print(f"Unexpected error fetching guild channels: {e}")
             return None

def check_if_summarized(url_to_check, check_channel_id):
    """Checks if a URL has already been summarized in the specified channel via HTTP GET."""
    print(f"Checking if URL already summarized in channel {check_channel_id}: {url_to_check}")
    messages = get_channel_messages(check_channel_id, SUMMARY_CHECK_LIMIT)
    if messages is None:
        print("Could not fetch summary channel messages to check for duplicates.")
        return False # Assume not summarized if check fails

    for message in messages:
        # Simple check: does the summary message content contain the URL?
        if url_to_check in message.get("content", ""):
            print(f"URL {url_to_check} found in summary message {message['id']}")
            return True
        # Could also check embeds here if summaries might be sent as embeds
        # for embed in message.get("embeds", []):
        #     if url_to_check in embed.get("description", "") or url_to_check in embed.get("url", ""):
        #         print(f"URL {url_to_check} found in embed of summary message {message['id']}")
        #         return True
    print(f"URL {url_to_check} not found in recent summary messages.")
    return False

# --- Main Processing Logic ---

def process_url(url, source_channel_name, target_channel_id):
    """Scrapes, summarizes, and sends summary for a given URL via HTTP POST."""
    skip = False
    summary_text = None
    summary_prefix = ""

    print(f"Processing URL: {url} from source: {source_channel_name}")

    if 'x.com' in url: # Skip twitter links for now
        print(f"Skipping x.com URL: {url}")
        return False

    if 'youtube.com' in url or 'youtu.be' in url:
        print(f"Generating YouTube summary for: {url}")
        try:
            text = generate_yt(url) # From utils.py
            if text:
                summary_prefix = f"Summary of ({source_channel_name}) {url} :\n\n"
                summary_text = text
            else:
                print(f"YouTube summary generation returned empty for {url}")
            skip = True # Mark as processed even if empty/error
        except Exception as e:
            print(f"Error generating YouTube summary for {url}: {e}")
            skip = True # Skip further processing on error

    if not skip: # Process non-YouTube URLs if not skipped
        print(f"Scraping and summarizing general URL: {url}")
        text_content = scrape_web_page(url) # From utils.py

        if text_content:
            # Determine the prompt based on the URL type
            if 'reddit.com' in url:
                 prompt = f"""
                 Summarize the key points and main discussion from the following Reddit post content within 1500 characters. Focus on the post's topic, user opinions, and any conclusions drawn. Ignore site navigation elements and generic Reddit boilerplate. Use English point form:\n\n{text_content}
                 """.strip()
            else:
                 # Default prompt
                 prompt = f"""
                 Without any explanation, just summarize this in English point form with minimal losing in information and ignore useless information for news consumer:\n\n{text_content}
                 """.strip()

            gemini_summary = generate(prompt) # From utils.py

            if gemini_summary:
                summary_prefix = f"Summary of ({source_channel_name}) {url} :\n\n"
                summary_text = gemini_summary
            else:
                print(f"Gemini API failed to summarize {url}")
        else:
            print(f"Failed to scrape content from {url}")

    # Send the summary if generated
    if summary_text:
        full_summary = summary_prefix + summary_text
        print(f"Attempting to send summary for {url} to channel {target_channel_id}")
        if send_discord_message(target_channel_id, full_summary):
            print(f"Summary sent successfully for {url}")
            return True # Indicate summary was sent
        else:
            print(f"Failed to send summary for {url}")
            return False # Indicate failure
    else:
        print(f"No summary generated for {url}")
        return False # Indicate no summary was generated/sent

# --- Main Execution ---

def process_channel(channel_id, channel_name, destination_channel_id, check_duplicate_channel_id):
    """Processes messages for a single channel and sends summaries."""
    print(f"\nProcessing channel '{channel_name}' ({channel_id}) -> Destination: {destination_channel_id}")
    messages = get_channel_messages(channel_id, MESSAGE_FETCH_LIMIT)
    processed_in_channel = 0

    if messages is None:
        print(f"Skipping channel {channel_id} due to fetch error.")
        return 0

    print(f"Fetched {len(messages)} messages from channel '{channel_name}' ({channel_id})")

    # Process messages oldest to newest for slightly better summary ordering if multiple found
    for message in reversed(messages):
        message_id = message.get("id")
        author_name = message.get("author", {}).get("username", "Unknown")
        content = message.get("content", "")
        embeds = message.get("embeds", [])

        print(f"  Checking message: {message_id} by {author_name}")

        # Basic check to skip bot messages (can be improved if needed)
        # if message.get("author", {}).get("bot", False):
        #      print("    Skipping message from bot.")
        #      continue

        # Extract URLs
        url_regex = r"(https?://[^\s]+)"
        urls = re.findall(url_regex, content)
        if not urls and embeds:
            for embed in embeds:
                if embed.get("url"):
                    # Handle potential relative URLs in embeds if necessary, though less common
                    urls.append(embed["url"])

        if not urls:
            print("    No URLs found in message.")
            continue

        # Process the first found URL
        url_to_process = urls[0]
        print(f"    Found URL: {url_to_process}")

        # Check if already summarized IN THE CORRECT DESTINATION CHANNEL
        if check_if_summarized(url_to_process, check_duplicate_channel_id):
            print(f"    URL {url_to_process} already summarized in channel {check_duplicate_channel_id}. Skipping.")
            continue # Move to the next message

        # Process and send summary to the designated destination_channel_id
        if process_url(url_to_process, channel_name, destination_channel_id):
            processed_in_channel += 1
            # Optional: Add a small delay after successful processing
            time.sleep(1) # Be mindful of total execution time in GitHub Actions

    print(f"Finished processing channel '{channel_name}'. Summarized {processed_in_channel} new URLs.")
    return processed_in_channel


def main():
    print("Starting summarizer script (HTTP Mode)...")
    total_processed_count = 0

    # --- Identify Category Channels ---
    print(f"\nIdentifying channels in category '{BOT_CATEGORY_NAME}' for guild {GUILD_ID}...")
    all_channels = get_guild_channels(GUILD_ID)
    category_channels_to_process = []
    category_id = None

    if all_channels:
        # Find the category ID
        for channel in all_channels:
            # Channel types: 0=Text, 2=Voice, 4=Category, 5=News, 10/11/12=Thread, 13=Stage
            if channel.get("type") == 4 and channel.get("name") == BOT_CATEGORY_NAME:
                category_id = channel.get("id")
                print(f"Found category '{BOT_CATEGORY_NAME}' with ID: {category_id}")
                break

        if category_id:
            # Find text channels within that category
            for channel in all_channels:
                 # Check type is Text (0) and parent_id matches category_id
                 if channel.get("type") == 0 and channel.get("parent_id") == category_id:
                     # Exclude source/target/summary channels explicitly if they happen to be in the category
                     if int(channel.get("id")) not in SOURCE_CHANNEL_IDS + [TARGET_CHANNEL_ID, SUMMARY_CHANNEL_ID]:
                         category_channels_to_process.append(
                             {"id": int(channel.get("id")), "name": channel.get("name", f"Channel {channel.get('id')}")}
                         )
                         print(f"  + Found text channel '{channel.get('name')}' ({channel.get('id')}) in category.")
                     else:
                         print(f"  - Skipping channel '{channel.get('name')}' ({channel.get('id')}) as it's a source/target/summary channel.")
            print(f"Identified {len(category_channels_to_process)} channels to process in category '{BOT_CATEGORY_NAME}'.")
        else:
            print(f"Warning: Category '{BOT_CATEGORY_NAME}' not found in guild {GUILD_ID}.")
    else:
        print("Warning: Could not fetch guild channels. Skipping category processing.")


    # --- Process Source Channels ---
    print("\n--- Processing Source Channels ---")
    # Create a map for faster name lookup if needed, or fetch individually
    source_channel_names = {}
    for channel_id in SOURCE_CHANNEL_IDS:
         # Fetch name individually - less efficient but simpler for now
         channel_name = f"Source Channel {channel_id}"
         try:
             channel_info_resp = requests.get(f"{DISCORD_API_URL}/channels/{channel_id}", headers=HEADERS)
             if channel_info_resp.status_code == 200:
                 channel_name = channel_info_resp.json().get("name", channel_name)
             else:
                 print(f"Warning: Could not fetch name for source channel {channel_id}")
         except Exception as e:
             print(f"Warning: Error fetching name for source channel {channel_id}: {e}")
         source_channel_names[channel_id] = channel_name

         # Process this source channel, sending summaries to TARGET_CHANNEL_ID
         # Check for duplicates in TARGET_CHANNEL_ID
         count = process_channel(channel_id, channel_name, TARGET_CHANNEL_ID, TARGET_CHANNEL_ID)
         total_processed_count += count


    # --- Process Category Channels ---
    print("\n--- Processing Category Channels ---")
    if not category_channels_to_process:
        print("No category channels identified or found to process.")
    else:
        for channel_info in category_channels_to_process:
            channel_id = channel_info["id"]
            channel_name = channel_info["name"]
            # Process this category channel, sending summaries to SUMMARY_CHANNEL_ID
            # Check for duplicates in SUMMARY_CHANNEL_ID
            count = process_channel(channel_id, channel_name, SUMMARY_CHANNEL_ID, SUMMARY_CHANNEL_ID)
            total_processed_count += count


    print(f"\nScript finished. Processed and summarized a total of {total_processed_count} new URLs across all channels.")


if __name__ == "__main__":
    try:
        main()
    except ValueError as e: # Catch configuration errors
        print(f"Configuration Error: {e}")
    except KeyboardInterrupt:
        print("\nScript interrupted by user.")
    except Exception as e: # Catch other potential runtime errors
        print(f"An unexpected error occurred during execution: {e}")
