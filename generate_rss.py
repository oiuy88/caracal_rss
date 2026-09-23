import requests
from feedgen.feed import FeedGenerator
from dateutil import parser
import datetime

ROOT_FOLDER_ID = "84998de9-01ff-4434-b566-85367d2fae5b"
FOLDER_URL = "https://circabc.europa.eu/ui/group/a0b483a2-4c05-4058-addf-2a4de71b9a98/library/84998de9-01ff-4434-b566-85367d2fae5b"

HEADERS = {
    "accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://circabc.europa.eu/"
}

def fetch_items(node_id, get_folders=False):
    # This completely solves the folder vs file problem by letting the server sort it out
    folder_only = "true" if get_folders else "false"
    file_only = "false" if get_folders else "true"
    
    url = f"https://circabc.europa.eu/service/circabc/spaces/{node_id}/children?language=en&guest=true&limit=100&page=1&order=modified_DESC&folderOnly={folder_only}&fileOnly={file_only}&skipExpiredItems=true"
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        if response.status_code != 200:
            return []
            
        data = response.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            if 'data' in data:
                return data['data']
            if 'list' in data and 'entries' in data['list']:
                return [i.get('entry', i) for i in data['list']['entries']]
        return []
    except Exception as e:
        print(f"Error fetching {node_id}: {e}")
        return []

def get_all_documents(node_id, depth=0, max_depth=5):
    # Prevent infinite loops if folders are nested endlessly
    if depth > max_depth:
        return []
        
    print(f"{'  ' * depth}Crawling folder ID: {node_id}...")
    
    # 1. Grab all the actual FILES in this specific folder
    documents = fetch_items(node_id, get_folders=False)
    
    # 2. Grab all the SUBFOLDERS and crawl inside them recursively
    subfolders = fetch_items(node_id, get_folders=True)
    for folder in subfolders:
        sub_id = folder.get('id')
        if sub_id:
            documents.extend(get_all_documents(sub_id, depth + 1, max_depth))
            
    return documents

def build_rss():
    print("Starting recursive crawl of CIRCABC folders...")
    all_documents = get_all_documents(ROOT_FOLDER_ID)
    
    print(f"\nFound {len(all_documents)} total documents. Sorting by date...")
    
    def get_date(doc):
        try:
            return parser.parse(doc.get('modified', '1970-01-01T00:00:00Z'))
        except:
            return datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
            
    all_documents.sort(key=get_date, reverse=True)
    latest_documents = all_documents[:50]
    
    # Print the final list to the GitHub Actions log
    print("\n--- TOP 10 RECENT DOCUMENTS FOUND ---")
    for doc in latest_documents[:10]:
        print(f"- {doc.get('name')} (Modified: {doc.get('modified')})")
    print("-------------------------------------\n")
    
    fg = FeedGenerator()
    fg.title('CIRCABC Folder Updates')
    fg.link(href=FOLDER_URL, rel='alternate')
    fg.description('Latest document changes in the CIRCABC repository and subfolders')
    
    for item in latest_documents:
        fe = fg.add_entry()
        title = item.get('name', 'Unknown Document')
        fe.title(title)
        
        node_id = item.get('id', '')
        download_link = f"https://circabc.europa.eu/ui/group/a0b483a2-4c05-4058-addf-2a4de71b9a98/library/{node_id}"
        fe.link(href=download_link)
        fe.id(node_id)
        
        mime_type = item.get('mimeType', 'Unknown type')
        size = item.get('size', 0)
        author = item.get('modifier', 'Unknown')
        fe.description(f"File: {title}<br>Type: {mime_type}<br>Size: {size} bytes<br>Modified by: {author}")
        
        if 'modified' in item:
            try:
                dt = parser.parse(item['modified'])
                fe.published(dt)
                fe.updated(dt)
            except Exception:
                pass
                
    fg.rss_file('rss.xml')
    print("Successfully generated rss.xml")

if __name__ == '__main__':
    build_rss()
