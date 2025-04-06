
import requests
import cloudscraper
import os
import re # Added for whitespace splitting
from bs4 import BeautifulSoup
from google import genai
from google.genai import types

# Load Google API Key from environment variable
api_key = os.getenv("GOOGLE_API_KEY")
if not api_key:
    raise ValueError("GOOGLE_API_KEY environment variable not set")

# create client (initialize later if needed, or ensure key is available)
# client = genai.Client(api_key=api_key) # Moved initialization inside generate_yt

def scrape_web_page(url):
    try:
        try:
            response = requests.get(url)
            response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
            soup = BeautifulSoup(response.content, 'html.parser')
        except:
            scraper = cloudscraper.create_scraper()  # returns a CloudScraper instance
            text = scraper.get(url).text
            soup = BeautifulSoup(text, 'html.parser')
        text = ' '.join(soup.stripped_strings)
        return text
    except requests.exceptions.RequestException as e:
        print(f"Error scraping {url}: {e}")
        return None
    except Exception as e:
        print(f"Error processing {url}: {e}")
        return None
    
def generate(prompt):
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
        headers = {'Content-Type': 'application/json'}
        data = {
            "contents": [{
                "parts": [{"text": prompt}]
            }]
        }
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        return response.json()['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        print(f"Error summarizing with Gemini API: {e}")
        return None
    
def generate_yt(youtube_url):
    # Re-initialize client here to ensure it uses the loaded API key
    client = genai.Client(api_key=api_key)

    prompt = """Analyze the following YouTube video content. Provide a concise summary covering:

    1.  **Main Thesis/Claim:** What is the central point the creator is making?
    2.  **Key Topics:** List the main subjects discussed, referencing specific examples or technologies mentioned (e.g., AI models, programming languages, projects).
    3.  **Call to Action:** Identify any explicit requests made to the viewer.
    4.  **Summary:** Provide a concise summary of the video content.

    Use the provided title, chapter timestamps/descriptions, and description text for your analysis. Please answer without explanation"""
    
    response = client.models.generate_content(
        model="gemini-2.5-pro-exp-03-25",
        contents=types.Content(
            parts=[
                types.Part(text=prompt),
                types.Part(
                    file_data=types.FileData(file_uri=youtube_url)
                )
            ]
        )
    )
    return response.text


async def send_long_message(channel, text):
    """
    Sends a message, splitting it into chunks at word boundaries if it exceeds Discord's character limit.
    Adds header/footer markers to each chunk if splitting occurs.
    """
    max_len = 2000
    # If the message doesn't need splitting, send it directly.
    if len(text) <= max_len:
        await channel.send(text)
        return

    # --- Splitting logic with markers ---
    # Define simple markers
    header_marker = "---\n"
    footer_marker = "\n---"
    # Calculate overhead for the markers
    marker_overhead = len(header_marker) + len(footer_marker)
    effective_max_len = max_len - marker_overhead

    # Split by any whitespace but keep the delimiters
    words_and_spaces = [item for item in re.split(r'(\s+)', text) if item]

    chunks = []
    current_chunk_content = ""
    for item in words_and_spaces:
        # Handle overly long individual items (e.g., a single word longer than effective_max_len)
        if len(item) > effective_max_len:
            # Send previous chunk if any
            if current_chunk_content:
                chunks.append(current_chunk_content)
                current_chunk_content = ""
            # Add the long item as its own chunk (it will get markers later)
            chunks.append(item)
            continue

        # Check if adding the next item exceeds the effective limit
        if len(current_chunk_content) + len(item) > effective_max_len:
            # Finalize the current chunk
            chunks.append(current_chunk_content)
            # Start the new chunk, stripping leading whitespace from the item
            current_chunk_content = item.lstrip()
        else:
            # Add the item to the current chunk
            current_chunk_content += item

    # Add the last remaining chunk
    if current_chunk_content:
        chunks.append(current_chunk_content)

    # Filter out any potentially empty chunks if logic resulted in them
    chunks = [chunk for chunk in chunks if chunk.strip()]

    # Send the chunks with simple markers
    for chunk_content in chunks:
        message_to_send = header_marker + chunk_content.strip() + footer_marker

        # Final check (should be rare with overhead calculation)
        if len(message_to_send) > max_len:
             # If it still exceeds, send without footer to maximize content space
             print(f"Warning: Chunk slightly exceeded max length ({len(message_to_send)}/{max_len}) after adding markers. Sending without footer.")
             message_to_send = header_marker + chunk_content.strip()
             if len(message_to_send) > max_len: # If still too long, send raw chunk
                 print(f"Warning: Chunk still too long ({len(message_to_send)}/{max_len}). Sending raw chunk content.")
                 message_to_send = chunk_content.strip()


        await channel.send(message_to_send)
