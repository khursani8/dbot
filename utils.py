
import requests
import cloudscraper
import os
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
    """Sends a message, splitting it into chunks if it exceeds Discord's character limit."""
    max_len = 2000
    if len(text) <= max_len:
        await channel.send(text)
        return

    current_chunk = ""
    lines = text.split('\n')
    for i, line in enumerate(lines):
        # Check if adding the next line (plus a newline character) exceeds the limit
        if len(current_chunk) + len(line) + 1 > max_len:
            # If the current chunk is not empty, send it
            if current_chunk:
                await channel.send(current_chunk)
                current_chunk = ""

            # If the line itself is too long, split it by words
            if len(line) > max_len:
                words = line.split(' ')
                temp_line = ""
                for word in words:
                    # Check if adding the next word (plus a space) exceeds the limit
                    if len(temp_line) + len(word) + 1 > max_len:
                        await channel.send(temp_line)
                        temp_line = word
                    else:
                        # Add the word (with a space if needed)
                        temp_line += (" " + word if temp_line else word)
                # Send the remainder of the long line
                if temp_line:
                    current_chunk = temp_line + "\n" # Start next chunk with remainder
            else:
                 # Start a new chunk with the current line
                 current_chunk = line + "\n"
        else:
            # Add the line to the current chunk
            current_chunk += line + "\n"

        # Send the chunk immediately if it's the last line and there's content
        if i == len(lines) - 1 and current_chunk:
             await channel.send(current_chunk.strip())
