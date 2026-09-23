import requests
from feedgen.feed import FeedGenerator
from dateutil import parser
import datetime

ROOT_FOLDER_ID = "84998de9-01ff-4434-b566-85367d2fae5b"
FOLDER_URL = "https://circabc.europa.eu/ui/group/a0b483a2-4c05-4058-addf-2a4de71b9a98/library/84998de9-01ff-4434-b566-85367d2fae5b"

def fetch_node_children(node_id):
    # Using the standard REST API which natively supports GET requests
    url = f"https://circabc.europa.eu/rest/nodes/{node_id}/children"
    response = requests.get(url, headers={"Accept": "application/json"})
    
    if response.status_code != 200:
        print(f"Failed to fetch {node_id}: {response.status_code}")
        return []
        
    try:
        data = response.json()
        if "list" in data and "entries" in data["list"]:
            # Extract the actual item from the standard Alfresco JSON wrapper
            return [item["entry"] for item in data["list"]["entries"]]
        return []
    except Exception as e:
        print(f"JSON parse error on {node_id}: {e}")
        return []

def get_all_documents(node_id, depth=0, max_depth=5):
    # Prevents infinite loops if folders are nested too deeply
    if depth > max_depth:
        return []
        
    items = fetch_node_children(node_id)
    documents = []
    
    for item in items:
        # Check if item is a folder
        is_folder = item.get("isFolder") is True or item.get("nodeType") == "cm:folder"
        
        if is_folder:
            sub_id = item.get("id")
            if sub_id:
                documents.extend(get_all_documents(sub_id, depth + 1, max_depth))
        else:
            documents.append(item)
            
    return documents

def build_rss():
    print("Crawling folders recursively using the REST API...")
    all_documents = get_all_documents(ROOT_FOLDER_ID)
    
    # Sort by modification date
    def get_date(doc):
        try:
            return parser.parse(doc.get("modifiedAt", "1970-01-01T00:00:00Z"))
        except:
            return datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
            
    all_documents.sort(key=get_date, reverse=True)
    latest_documents = all_documents[:50]
    
    # Print the top 10 to the GitHub Actions log so you can verify it worked
    print("\n--- TOP 10 RECENT DOCUMENTS FOUND ---")
    for doc in latest_documents[:10]:
        print(f"- {doc.get('name')} (Modified: {doc.get('modifiedAt')})")
    print("-------------------------------------\n")
    
    fg = FeedGenerator()
    fg.title("CIRCABC Folder Updates")
    fg.link(href=FOLDER_URL, rel="alternate")
    fg.description("Latest document changes in the CIRCABC repository")
    
    for item in latest_documents:
        fe = fg.add_entry()
        title = item.get("name", "Unknown Document")
        fe.title(title)
        
        node_id = item.get("id", "")
        download_link = f"https://circabc.europa.eu/ui/group/a0b483a2-4c05-4058-addf-2a4de71b9a98/library/{node_id}"
        fe.link(href=download_link)
        fe.id(node_id)
        
        # Alfresco nests file details inside a 'content' object
        content = item.get("content", {})
        mime_type = content.get("mimeType", "Unknown type")
        size = content.get("sizeInBytes", 0)
        
        # Extract author name
        modifier = item.get("modifiedByUser", {})
        author = modifier.get("displayName", "Unknown")
        
        fe.description(f"File: {title}<br>Type: {mime_type}<br>Size: {size} bytes<br>Modified by: {author}")
        
        modified_date = item.get("modifiedAt")
        if modified_date:
            try:
                dt = parser.parse(modified_date)
                fe.published(dt)
                fe.updated(dt)
            except Exception:
                pass
                
    fg.rss_file("rss.xml")
    print(f"Successfully generated rss.xml with {len(latest_documents)} items.")

if __name__ == "__main__":
    build_rss()
