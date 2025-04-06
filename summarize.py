import discord
import re
import os
import json # To parse the list of source channel IDs
from dotenv import load_dotenv
from utils import scrape_web_page, generate, generate_yt, send_long_message

# Load environment variables from .env file
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

# Target Channel ID (for summaries from SOURCE_CHANNEL_IDS)
target_channel_id_str = os.getenv('TARGET_CHANNEL_ID')
if not target_channel_id_str:
    raise ValueError("TARGET_CHANNEL_ID environment variable not set")
try:
    TARGET_CHANNEL_ID = int(target_channel_id_str)
except ValueError:
     raise ValueError("Invalid format for TARGET_CHANNEL_ID: Must be an integer.")

# Summary Channel ID (for summaries from BOT category channels)
summary_channel_id_str = os.getenv('SUMMARY_CHANNEL_ID')
if not summary_channel_id_str:
    raise ValueError("SUMMARY_CHANNEL_ID environment variable not set")
try:
    SUMMARY_CHANNEL_ID = int(summary_channel_id_str)
except ValueError:
     raise ValueError("Invalid format for SUMMARY_CHANNEL_ID: Must be an integer.")

# Category Name to Monitor
BOT_CATEGORY_NAME = os.getenv('BOT_CATEGORY_NAME', 'BOT') # Default to 'BOT' if not set


intents = discord.Intents.default()
intents.message_content = True # Still needed for on_message content
intents.guilds = True # Needed to get category name

client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f'We have logged in as {client.user}')


@client.event
async def on_message(message):
    skip = False
    # Ignore messages from the bot itself
    if message.author == client.user:
        return

    print(f"Message received in #{message.channel.name} from {message.author.name}")

    # Check if the message is in a channel within the BOT_CATEGORY_NAME AND not one of the specific channels
    if message.channel.category and message.channel.category.name == BOT_CATEGORY_NAME and message.channel.id not in SOURCE_CHANNEL_IDS + [TARGET_CHANNEL_ID, SUMMARY_CHANNEL_ID]:
        print(f"Processing message in BOT category channel: {message.channel.name}")
        url_regex = r"(https?://[^\s]+)"
        urls = re.findall(url_regex, message.content)
        if not urls:
            # Try extracting URL from embeds if not found in content
            try:
                # Check message_snapshots first (if available, might indicate an edit)
                url = message.message_snapshots[0].embeds[0].url
                if url:
                    urls = [url]
            except (IndexError, AttributeError):
                try:
                    # Check current message embeds
                    url = message.embeds[0].url
                    if url:
                        urls = [url]
                except (IndexError, AttributeError):
                    pass # No URL found in embeds either

        if urls:
            print(f"Found URLs in BOT category ({message.channel.name}): {urls}")
            summary_channel_obj = client.get_channel(SUMMARY_CHANNEL_ID) # Use loaded variable
            if not summary_channel_obj:
                print(f"Error: Summary channel {SUMMARY_CHANNEL_ID} not found!") # Use loaded variable
                return # Exit if summary channel doesn't exist

            url_to_process = urls[0] # Process first URL

            if 'x.com' in url_to_process: # Skip twitter links for now
                print(f"Skipping x.com URL: {url_to_process}")
                return

            if 'youtube.com' in url_to_process or 'youtu.be' in url_to_process:
                print(f"Generating YouTube summary for: {url_to_process}")
                try:
                    text = generate_yt(url_to_process)
                    if text:
                        summary_prefix = f"Summary of ({message.channel.name}) {url_to_process} :\n\n"
                        await send_long_message(summary_channel_obj, summary_prefix + text)
                    else:
                        print(f"YouTube summary generation returned empty for {url_to_process}")
                    skip = True # Mark as processed
                except Exception as e:
                    print(f"Error generating YouTube summary for {url_to_process}: {e}")
                    skip = True # Skip further processing on error

            if not skip: # Process non-YouTube URLs if not skipped
                print(f"Scraping and summarizing general URL: {url_to_process}")
                # Scrape the web page content
                text_content = scrape_web_page(url_to_process)

                if text_content:
                    # Determine the prompt based on the URL type for BOT category channels
                    if 'reddit.com' in url_to_process:
                         prompt = f"""
                         Summarize the key points and main discussion from the following Reddit post content within 1500 characters. Focus on the post's topic, user opinions, and any conclusions drawn. Ignore site navigation elements and generic Reddit boilerplate. Use English point form:\n\n{text_content}
                         """.strip()
                    else:
                         # Default prompt for other URLs in BOT category
                         prompt = f"""
                         Without any explanation, just summarize this in English point form with minimal losing in information and ignore useless information for news consumer:\n\n{text_content}
                         """.strip()

                    gemini_summary = generate(prompt)

                    if gemini_summary:
                        # Send the Gemini summary to the summary channel
                        summary_prefix = f"Summary of ({message.channel.name}) {url_to_process} :\n\n"
                        try:
                            await send_long_message(summary_channel_obj, summary_prefix + gemini_summary)
                        except Exception as e:
                            print(f"Error sending summary for {url_to_process}: {e}")
                            print(f"Summary content was:\n{gemini_summary}") # Log summary on error
                    else:
                        print(f"Gemini API failed to summarize {url_to_process}")
                else:
                    print(f"Failed to scrape content from {url_to_process}") # Corrected indentation
        else:
            print("    No URLs found in message.")


    # Check if the message is in one of the SOURCE_CHANNEL_IDS
    elif message.channel.id in SOURCE_CHANNEL_IDS: # Use loaded variable
        print(f"Processing message in SOURCE channel: {message.channel.name}")
        # Extract URLs from the message content
        url_regex = r"(https?://[^\s]+)"
        urls = re.findall(url_regex, message.content)

        # Try extracting URL from embeds if not found in content
        if not urls:
            try:
                url = message.embeds[0].url
                if url:
                    urls = [url]
            except (IndexError, AttributeError):
                pass # No URL found in embeds

        if 'x.com' in url: # Skip twitter links for now
            print(f"Skipping x.com URL: {url}")
            return

        if urls:
            print(f"Found URLs in SOURCE channel ({message.channel.name}): {urls}")
            # Get the target channel for direct summaries
            target_channel_obj = client.get_channel(TARGET_CHANNEL_ID) # Use loaded variable

            if target_channel_obj:
                # Process first URL
                url_to_process = urls[0]
                print(f"Scraping and summarizing URL from source channel: {url_to_process}")
                # Scrape the web page content
                text_content = scrape_web_page(url_to_process)

                if text_content:
                    # Determine the prompt based on the URL type
                    if 'reddit.com' in url_to_process:
                         prompt = f"""
                         Summarize the key points and main discussion from the following Reddit post content within 1500 characters. Focus on the post's topic, user opinions, and any conclusions drawn. Ignore site navigation elements and generic Reddit boilerplate. Use English point form:\n\n{text_content}
                         """.strip()
                    else:
                         # Default prompt for other URLs
                         prompt = f"""
                         Without any explanation, just summarize this in English point form with minimal losing in information and ignore useless information for news consumer:\n\n{text_content}
                         """.strip()

                    gemini_summary = generate(prompt)

                    if gemini_summary:
                        # Send the Gemini summary to the target channel
                        summary_prefix = f"Summary of {url_to_process}:\n\n"
                        try:
                            await send_long_message(target_channel_obj, summary_prefix + gemini_summary)
                        except Exception as e:
                            print(f"Error sending summary for {url_to_process} to TARGET_CHANNEL: {e}")
                            print(f"Summary content was:\n{gemini_summary}") # Log summary on error
                    else:
                        print(f"Gemini API failed to summarize {url_to_process}")
                else:
                    print(f"Failed to scrape content from {url_to_process}") # Corrected indentation
            else:
                print(f"Error: Target channel {TARGET_CHANNEL_ID} not found!") # Use loaded variable
        else:
            print("    No URLs found in message.")


# Run the client
if __name__ == "__main__":
    try:
        client.run(TOKEN)
    except ValueError as e: # Catch configuration errors
        print(f"Configuration Error: {e}")
    except discord.LoginFailure:
        print("Error: Invalid Discord token.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
