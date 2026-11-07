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
    except Exception:
        return []

def get_all_documents(node_id, depth=0, max_depth=5):
    if depth > max_depth:
        return []
        
    documents = fetch_items(node_id, get_folders=False)
    subfolders = fetch_items(node_id, get_folders=True)
    
    for folder in subfolders:
        sub_id = folder.get('id')
        if sub_id:
            documents.extend(get_all_documents(sub_id, depth + 1, max_depth))
            
    return documents

def extract_meta(item, possible_keys):
    # Search root level
    for k in possible_keys:
        if k in item and item[k]:
            return item[k]
    # Search nested properties level (common in Alfresco)
    props = item.get('properties', {})
    for k in possible_keys:
        if k in props and props[k]:
            return props[k]
    return None

def build_rss():
    all_documents = get_all_documents(ROOT_FOLDER_ID)
    
    def get_date(doc):
        date_val = extract_meta(doc, ['modified', 'modifiedAt', 'modifiedOn', 'cm:modified', 'date'])
        try:
            return parser.parse(str(date_val))
        except:
            return datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
            
    # Sort files globally by newest
    all_documents.sort(key=get_date, reverse=True)
    latest_documents = all_documents[:50]
    
    fg = FeedGenerator()
    fg.title('CIRCABC Folder Updates')
    fg.link(href=FOLDER_URL, rel='alternate')
    fg.description('Latest document changes in the CIRCABC repository and subfolders')
    
    for item in latest_documents:
        fe = fg.add_entry()
        
        title = extract_meta(item, ['name', 'title', 'cm:name']) or 'Unknown Document'
        fe.title(title)
        
        node_id = item.get('id', '')
        download_link = f"https://circabc.europa.eu/ui/group/a0b483a2-4c05-4058-addf-2a4de71b9a98/library/{node_id}/details"
        fe.link(href=download_link)
        fe.id(node_id)
        
        mime_type = extract_meta(item, ['mimeType', 'mimetype', 'cm:content.mimetype']) or 'Document'
        size = extract_meta(item, ['size', 'sizeInBytes', 'cm:content.size']) or 'Unknown size'
        author = extract_meta(item, ['modifier', 'modifiedBy', 'cm:modifier']) or 'System'
        
        fe.description(f"File: {title}<br>Type: {mime_type}<br>Size: {size}<br>Modified by: {author}")
        
        date_str = extract_meta(item, ['modified', 'modifiedAt', 'modifiedOn', 'cm:modified', 'date'])
        if date_str:
            try:
                dt = parser.parse(str(date_str))
                fe.published(dt)
                fe.updated(dt)
            except Exception:
                pass
                
    fg.rss_file('rss.xml')

if __name__ == '__main__':
    build_rss()
