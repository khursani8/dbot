import requests
import time
import os
import json
import re
from datetime import datetime, UTC
# Import scraping and generation functions from utils
from utils import generate, scrape_web_page, generate_yt
from dotenv import load_dotenv

# Load .env file only if not running in a CI environment
if not os.getenv("CI"):
    load_dotenv()
    print("Loaded environment variables from .env file.")
else:
    print("Running in CI environment, expecting environment variables directly.")


# --- Configuration from Environment Variables ---
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("DISCORD_TOKEN environment variable not set")

guild_id_str = os.getenv("GUILD_ID")
if not guild_id_str:
    raise ValueError("GUILD_ID environment variable not set")
try:
    GUILD_ID = int(guild_id_str)
except ValueError:
    raise ValueError("Invalid format for GUILD_ID: Must be an integer.")

SOURCE_CHANNEL_NAME = "jp" # Channel to read from
TARGET_CHANNEL_NAME = "en" # Channel to post summaries to

# --- Constants ---
DISCORD_API_URL = "https://discord.com/api/v10"
# Use a file to store processed URLs instead of last message ID
PROCESSED_URLS_FILE = "processed_urls_translate.json"
MESSAGE_FETCH_LIMIT = 100  # How many recent messages to check per run (adjust as needed)
HEADERS = {
    "Authorization": f"Bot {TOKEN}",
    "User-Agent": "DiscordBot (TranslateSummarizer, v0.1)",
    "Content-Type": "application/json",
}
RATE_LIMIT_SLEEP = 1  # Simple sleep duration in seconds for rate limits
MAX_MESSAGE_LENGTH = 2000 # Discord message length limit

# --- Discord API Helper Functions ---

def handle_rate_limit(response):
    """Checks for 429 rate limit and sleeps if necessary."""
    if response.status_code == 429:
        retry_after = response.json().get("retry_after", RATE_LIMIT_SLEEP)
        print(f"Rate limited. Sleeping for {retry_after} seconds.")
        time.sleep(retry_after)
        return True
    return False

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

def find_channel_by_name(channels, name):
    """Finds the first channel ID matching the given name."""
    if not channels:
        return None
    for channel in channels:
        # Check for text channels (type 0) or announcement channels (type 5)
        if channel.get("name") == name and channel.get("type") in [0, 5]:
            return int(channel.get("id"))
    return None

# Renamed function: Fetches recent messages, not necessarily 'after' an ID
def get_channel_messages(channel_id, limit=100):
    """Fetches recent messages from a channel."""
    url = f"{DISCORD_API_URL}/channels/{channel_id}/messages?limit={limit}"

    print(f"Fetching {limit} recent messages from channel {channel_id}")
    while True:
        try:
            response = requests.get(url, headers=HEADERS)
            if handle_rate_limit(response):
                continue
            response.raise_for_status()
            messages = response.json()
            # Return newest to oldest, reversal happens in main loop if needed
            return messages
        except requests.exceptions.RequestException as e:
            print(f"Error fetching messages from channel {channel_id}: {e}")
            return None
        except Exception as e:
            print(f"Unexpected error fetching messages: {e}")
            return None

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
                print(f"Message chunk sent successfully to channel {channel_id}.")
                return True
            except requests.exceptions.RequestException as e:
                print(f"Error sending message chunk to channel {channel_id}: {e}")
                # Consider adding retry logic or specific error handling here
                return False # Indicate failure
            except Exception as e:
                print(f"Unexpected error sending message chunk: {e}")
                return False # Indicate failure
        # Should not be reachable if loop continues on rate limit
        return False

    if len(content) <= max_len:
        return post_chunk(content)
    else:
        print(f"Message too long ({len(content)} chars). Splitting...")
        chunks = []
        current_chunk = ""
        # Basic splitting by lines, could be improved (e.g., split by sentences or paragraphs)
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
                print(f"Failed to send chunk {i+1}. Aborting rest of message.")
                break # Stop sending chunks if one fails
            time.sleep(0.5) # Small delay between chunks
        return success

# --- URL Extraction Helper ---
def extract_urls_from_text(text):
    """Extracts all URLs from a given string."""
    # Simple regex, might need refinement depending on edge cases
    url_regex = r"(https?://[^\s<>\"']+)"
    return re.findall(url_regex, text)

# --- Persistence Helper Functions (for Processed URLs) ---

def load_processed_urls(filepath=PROCESSED_URLS_FILE):
    """Loads the set of processed URLs from a JSON file."""
    try:
        if os.path.exists(filepath):
            with open(filepath, 'r') as f:
                urls = json.load(f)
                print(f"Loaded {len(urls)} processed URLs from {filepath}")
                return set(urls)
        else:
            print(f"Processed URLs file ({filepath}) not found. Starting fresh.")
            return set()
    except (json.JSONDecodeError, IOError) as e:
        print(f"Error loading processed URLs from {filepath}: {e}. Starting fresh.")
        return set()

def save_processed_urls(urls_set, filepath=PROCESSED_URLS_FILE):
    """Saves the set of processed URLs to a JSON file."""
    try:
        with open(filepath, 'w') as f:
            # Save as list for readability, convert set to list first
            json.dump(list(urls_set), f, indent=2)
        # print(f"Saved {len(urls_set)} processed URLs to {filepath}") # Reduce noise
        return True
    except IOError as e:
        print(f"Error saving processed URLs to {filepath}: {e}")
        return False

# --- Main Logic ---

def main():
    print("Starting translate/summarize script (URL focused)...")

    # --- Load State (Processed URLs) ---
    processed_urls = load_processed_urls()
    urls_processed_this_run = 0 # Counter for this specific execution

    # --- Find Channels ---
    print(f"Looking for channels '{SOURCE_CHANNEL_NAME}' and '{TARGET_CHANNEL_NAME}' in guild {GUILD_ID}...")
    all_channels = get_guild_channels(GUILD_ID)
    if not all_channels:
        print("Error: Could not fetch guild channels. Exiting.")
        return

    source_channel_id = find_channel_by_name(all_channels, SOURCE_CHANNEL_NAME)
    target_channel_id = find_channel_by_name(all_channels, TARGET_CHANNEL_NAME)

    if not source_channel_id:
        print(f"Error: Source channel '{SOURCE_CHANNEL_NAME}' not found. Exiting.")
        return
    if not target_channel_id:
        print(f"Error: Target channel '{TARGET_CHANNEL_NAME}' not found. Exiting.")
        return

    print(f"Found source channel '{SOURCE_CHANNEL_NAME}': ID {source_channel_id}")
    print(f"Found target channel '{TARGET_CHANNEL_NAME}': ID {target_channel_id}")

    # --- Fetch and Process Messages ---
    print(f"\nFetching last {MESSAGE_FETCH_LIMIT} messages from '{SOURCE_CHANNEL_NAME}'...")
    # Fetch recent messages, process them oldest to newest within the batch
    messages = get_channel_messages(source_channel_id, MESSAGE_FETCH_LIMIT)

    if messages is None:
        print("Failed to fetch messages. Exiting.")
        return

    if not messages:
        print("No messages found in source channel.")
    else:
        print(f"Checking {len(messages)} messages...")
        # Process messages oldest to newest in the fetched batch
        for message in reversed(messages):
            message_id = message.get("id")
            content = message.get("content", "")
            embeds = message.get("embeds", [])
            author_info = message.get("author", {})
            author_name = author_info.get("username", "Unknown")
            is_bot = author_info.get("bot", False)

            # # Skip messages from bots (optional, adjust if needed)
            # if is_bot:
            #     continue

            # --- Extract URLs ---
            urls_in_message = extract_urls_from_text(content)
            if embeds:
                for embed in embeds:
                    if embed.get("url"):
                        urls_in_message.append(embed["url"])

            if not urls_in_message:
                # print(f"  Skipping message {message_id} (no URLs found).")
                continue # Skip messages without URLs

            # Process the *first* valid URL found in the message
            url_to_process = urls_in_message[0]
            print(f"\nProcessing Message ID: {message_id} (Author: {author_name})")
            print(f"  Found URL: {url_to_process}")

            # --- Check if URL already processed ---
            if url_to_process in processed_urls:
                print(f"  Skipping (URL already processed).")
                continue

            # --- Generate Summary (from URL content) ---
            summary_text = None
            scraped_content = None # To hold content for non-YT links

            try:
                if "youtube.com" in url_to_process or "youtu.be" in url_to_process:
                    print(f"  Processing as YouTube URL...")
                    summary_text = generate_yt(url_to_process) # generate_yt should return the summary directly
                    if not summary_text: print("    YouTube summary generation returned empty.")

                elif "x.com" in url_to_process:
                     print(f"  Skipping x.com URL.")
                     # Optionally add to processed_urls here if you never want to retry x.com
                     # processed_urls.add(url_to_process)
                     # save_processed_urls(processed_urls)

                else: # General web page
                    print(f"  Processing as general URL (scraping)...")
                    scraped_content = scrape_web_page(url_to_process)
                    if scraped_content:
                        print(f"    Scraping successful ({len(scraped_content)} chars). Generating summary...")
                        # Use the prompt similar to bk.py for general URLs
                        prompt = f"""Without any explanation, just summarize this in English point form with minimal losing in information and ignore useless information for news consumer:\n\n{scraped_content}"""
                        summary_text = generate(prompt)
                        if not summary_text: print("    Summary generation returned empty string.")
                    else:
                        print(f"    Scraping failed for {url_to_process}.")

            except Exception as e:
                print(f"  Exception during URL processing/summarization for {url_to_process}: {e}")
                summary_text = None # Ensure failure state

            # --- Post Summary if Generated ---
            if summary_text:
                summary_text = summary_text.strip()
                if summary_text: # Check again after stripping
                    print(f"  Summary generated successfully.")
                    # Construct the original message link
                    original_message_link = f"https://discord.com/channels/{GUILD_ID}/{source_channel_id}/{message_id}"
                    # Format the message for the target channel, including the URL and original message link
                    output_message = (
                        f"**Summary from #{SOURCE_CHANNEL_NAME} (by {author_name}):**\n"
                        f"URL: {url_to_process}\n\n"
                        f"{summary_text}\n\n"
                        f"*Original Message: <{original_message_link}>*" # Use angle brackets to prevent embed
                    )

                    print(f"  Attempting to post summary to #{TARGET_CHANNEL_NAME} ({target_channel_id})...")
                    post_successful = send_discord_message(target_channel_id, output_message)

                    if post_successful:
                        print(f"  Successfully posted summary for URL: {url_to_process}")
                        # Add URL to processed set and save
                        processed_urls.add(url_to_process)
                        if save_processed_urls(processed_urls):
                             urls_processed_this_run += 1
                        else:
                             print(f"  WARNING: Failed to save processed URLs file after adding {url_to_process}")
                    else:
                        print(f"  Failed to post summary for URL: {url_to_process}. Will retry next run.")
                        # Don't add to processed_urls if post fails, allow retry
                else:
                     print(f"  Skipping posting for URL {url_to_process} (summary was empty after strip).")
                     # Don't add to processed_urls if summary is empty
            else:
                 print(f"  Skipping posting for URL {url_to_process} (summary generation failed or scraping failed).")
                 # Don't add to processed_urls if generation/scraping failed

            time.sleep(RATE_LIMIT_SLEEP * 2) # Be nice to the API

    print(f"\nFinished processing batch. {urls_processed_this_run} new URLs summarized and posted in this run.")
    print("Script finished.")


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:
        print(f"Configuration Error: {e}")
    except KeyboardInterrupt:
        print("\nScript interrupted by user.")
    except Exception as e:
        print(f"An unexpected error occurred during execution: {e}")
