import requests
from feedgen.feed import FeedGenerator
from dateutil import parser
import datetime

# The API URL tracking the latest 50 document changes
API_URL = "https://circabc.europa.eu/service/circabc/spaces/84998de9-01ff-4434-b566-85367d2fae5b/children?language=en&guest=true&limit=50&page=1&order=modified_DESC&folderOnly=false&fileOnly=true&skipExpiredItems=true"

# The human-readable URL of the folder
FOLDER_URL = "https://circabc.europa.eu/ui/group/a0b483a2-4c05-4058-addf-2a4de71b9a98/library/84998de9-01ff-4434-b566-85367d2fae5b"

def build_rss():
    response = requests.get(API_URL, headers={"Accept": "application/json"})
    
    if response.status_code != 200:
        print(f"Failed to fetch data: {response.status_code}")
        return
    
    data = response.json()
    
    # Initialize feed
    fg = FeedGenerator()
    fg.title('CIRCABC Folder Updates')
    fg.link(href=FOLDER_URL, rel='alternate')
    fg.description('Latest document changes in the CIRCABC repository')
    
    # Build entries
    for item in data:
        fe = fg.add_entry()
        
        title = item.get('name', 'Unknown Document')
        fe.title(title)
        
        node_id = item.get('id', '')
        # Direct download link for the file
        download_link = f"https://circabc.europa.eu/ui/group/a0b483a2-4c05-4058-addf-2a4de71b9a98/library/{node_id}"
        fe.link(href=download_link)
        fe.id(node_id)
        
        mime_type = item.get('mimeType', 'Unknown type')
        size = item.get('size', 0)
        author = item.get('modifier', 'Unknown')
        fe.description(f"File: {title}<br>Type: {mime_type}<br>Size: {size} bytes<br>Modified by: {author}")
        
        if 'modified' in item:
            # Parse CIRCABC's timestamp safely to a timezone-aware object
            try:
                dt = parser.parse(item['modified'])
                fe.published(dt)
                fe.updated(dt)
            except Exception as e:
                print(f"Time parse error for {title}: {e}")
    
    # Save to file
    fg.rss_file('rss.xml')

if __name__ == '__main__':
    build_rss()
