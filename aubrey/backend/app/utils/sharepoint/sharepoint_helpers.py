import requests
import re
from urllib.parse import quote

class SharePointClient:
    def __init__(self, tenant_id, client_id, client_secret):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
        self.graph_api_url = "https://graph.microsoft.com/v1.0"
        self.access_token = self.get_access_token()

    def _get(self, url):
        headers = {'Authorization': f'Bearer {self.access_token}'}
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return response.json()
        raise Exception(f"GET failed [{response.status_code}]: {url}\n{response.text}")

    def get_access_token(self):
        payload = {
            'grant_type': 'client_credentials',
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'scope': "https://graph.microsoft.com/.default"
        }
        response = requests.post(self.token_url, data=payload, headers={'Content-Type': 'application/x-www-form-urlencoded'})
        if response.status_code == 200:
            return response.json().get('access_token')
        raise Exception(f"Failed to obtain access token: {response.text}")

    def get_site_id(self, hostname, site_path):
        data = self._get(f"{self.graph_api_url}/sites/{hostname}:/{site_path}")
        return data.get('id')

    def get_all_drives(self, site_id):
        data = self._get(f"{self.graph_api_url}/sites/{site_id}/drives")
        return data.get('value', [])

    def get_drive_root_child_count(self, drive_id):
        try:
            data = self._get(f"{self.graph_api_url}/drives/{drive_id}/root")
            return data.get('folder', {}).get('childCount')
        except Exception:
            return None

    def resolve_folder_id_by_path(self, drive_id, folder_path):
        clean = (folder_path or '').strip().strip('/')
        if not clean:
            return 'root'
        encoded = '/'.join(quote(segment) for segment in clean.split('/'))
        data = self._get(f"{self.graph_api_url}/drives/{drive_id}/root:/{encoded}")
        if 'folder' not in data:
            raise Exception(f"Path '{folder_path}' exists but is not a folder.")
        return data.get('id')

    def list_items_recursive(self, drive_id, folder_id='root'):
        url = f"{self.graph_api_url}/drives/{drive_id}/items/{folder_id}/children"
        files = []
        while url:
            data = self._get(url)
            for item in data.get('value', []):
                if 'folder' in item:
                    files.extend(self.list_items_recursive(drive_id, item['id']))
                else:
                    files.append({
                        'name': item.get('name'),
                        'id': item.get('id'),
                        'drive_id': drive_id,
                        'webUrl': item.get('webUrl'),
                        'size': item.get('size'),
                        'mimeType': item.get('file', {}).get('mimeType'),
                        'downloadUrl': item.get('@microsoft.graph.downloadUrl'),
                    })
            url = data.get('@odata.nextLink')
        return files

    def get_download_url(self, drive_id, item_id):
        data = self._get(f"{self.graph_api_url}/drives/{drive_id}/items/{item_id}")
        return data.get('@microsoft.graph.downloadUrl')

    def download_file_content(self, drive_id, item_id):
        download_url = self.get_download_url(drive_id, item_id)
        response = requests.get(download_url)
        response.raise_for_status()
        return response.content

    def search_site_documents(self, site_hostname, site_path, query="*", region="NAM"):
        url = "https://graph.microsoft.com/v1.0/search/query"
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json',
        }
        site_filter = f"site:{site_hostname}/{site_path}"
        payload = {
            "requests": [{
                "entityTypes": ["driveItem"],
                "query": {"queryString": f"{query} {site_filter}"},
                "fields": ["name", "webUrl", "id", "parentReference", "size", "fileSystemInfo"],
                "from": 0,
                "size": 100,
                "region": region
            }]
        }
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            results = []
            for container in response.json().get('value', [{}])[0].get('hitsContainers', []):
                for hit in container.get('hits', []):
                    results.append(hit.get('resource', {}))
            return results
        raise Exception(f"Search failed [{response.status_code}]: {response.text}")

    def get_drive_by_name(self, site_id, drive_name):
        for drive in self.get_all_drives(site_id):
            if drive.get('name', '').lower() == drive_name.lower():
                return drive
        raise Exception(f"Drive '{drive_name}' not found in site {site_id}.")

    def get_page_id_by_name(self, site_id, page_name="Benefits.aspx"):
        data = self._get(f"{self.graph_api_url}/sites/{site_id}/pages/microsoft.graph.sitePage")
        for page in data.get('value', []):
            if page.get('name', '').lower() == page_name.lower():
                return page.get('id')
        raise Exception(f"Page '{page_name}' not found.")

    def extract_links_from_page(self, site_id, page_id):
        page_data = self._get(
            f"{self.graph_api_url}/sites/{site_id}/pages/{page_id}"
            "/microsoft.graph.sitePage?$expand=canvasLayout"
        )
        layout = page_data.get('canvasLayout', {})
        found_links = set()

        all_web_parts = []
        for section in layout.get('horizontalSections', []):
            for column in section.get('columns', []):
                all_web_parts.extend(column.get('webParts', []))
        for wp in layout.get('verticalSection', {}).get('webParts', []):
            all_web_parts.append(wp)

        for wp in all_web_parts:
            spc = wp.get('serverProcessedContent', {})
            for val in spc.get('htmlLinks', {}).values():
                if isinstance(val, str):
                    found_links.add(val)
                elif isinstance(val, dict):
                    found_links.add(val.get('url', ''))
            for link in spc.get('links', []):
                if isinstance(link, dict):
                    found_links.add(link.get('url') or link.get('value', ''))
            inner_html = wp.get('innerHtml', '')
            if inner_html:
                found_links.update(re.findall(r'href=["\']([^"\']+)["\']', inner_html))
        return [l for l in found_links if l]
