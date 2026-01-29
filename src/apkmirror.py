import re
import json
import logging
import time
from bs4 import BeautifulSoup
from urllib.parse import quote_plus
import cloudscraper  # Replaces requests for Cloudflare bypass

from src import base_url  # Assuming base_url is defined, e.g., "https://www.apkmirror.com"

# Replace session with scraper
scraper = cloudscraper.create_scraper()

def get_download_link(version: str, app_name: str, config: dict, arch: str = None, timeout: int = 5) -> str:
    target_arch = arch if arch else config.get('arch', 'universal')
    
    # Handle criteria as lists or strings
    type_criteria = config.get('type', 'apk')
    arch_criteria = get_architecture_criteria(target_arch)
    dpi_criteria = config.get('dpi', 'nodpi')
    min_api_criteria = config.get('min_api', 'minapi-29')
    
    if not isinstance(dpi_criteria, list):
        dpi_criteria = [dpi_criteria]
    
    criteria = [type_criteria, min_api_criteria]  # Base criteria
    
    # --- UNIVERSAL URL FINDER WITH VALIDATION ---
    version_parts = version.split('.')
    found_soup = None
    correct_version_page = False
    
    # Use release_prefix if available, otherwise use app name
    release_name = config.get('release_prefix', config['name'])
    
    # Loop backwards: Try full version, then strip parts
    for i in range(len(version_parts), 0, -1):
        current_ver_str = "-".join(version_parts[:i])
        
        # Generate MORE possible URL patterns in priority order
        url_patterns = []
        
        # Priority 1: With release_name and -release suffix (most specific)
        url_patterns.append(f"{base_url}/apk/{config['org']}/{config['name']}/{release_name}-{current_ver_str}-release/")
        url_patterns.append(f"{base_url}/apk/{config['org']}/{config['name']}/{config['name']}-{current_ver_str}-release/")
        
        # Priority 2: With release_name without -release
        url_patterns.append(f"{base_url}/apk/{config['org']}/{config['name']}/{release_name}-{current_ver_str}/")
        url_patterns.append(f"{base_url}/apk/{config['org']}/{config['name']}/{config['name']}-{current_ver_str}/")
        
        # Remove duplicates
        url_patterns = list(dict.fromkeys(url_patterns))
        
        for url in url_patterns:
            logging.info(f"Checking potential release URL: {url}")
            time.sleep(timeout)  # Anti-scraping delay
            
            try:
                response = scraper.get(url)
                if response.status_code == 200:
                    soup = BeautifulSoup(response.content, "html.parser")
                    page_text = soup.get_text()
                    
                    # VALIDATION: Check if this page is for our EXACT version
                    version_checks = [
                        version,  # 26.1.1
                        version.replace('.', '-'),  # 26-1-1
                        current_ver_str,  # 26-1 (if stripped)
                        ".".join(version_parts[:i])  # 26.1 (if stripped)
                    ]
                    
                    # Check in meta, title, headings, and text
                    is_correct_page = False
                    meta_tags = soup.find_all('meta', attrs={'name': re.compile(r'version', re.I)})
                    for meta in meta_tags:
                        if any(check in str(meta.get('content', '')) for check in version_checks):
                            is_correct_page = True
                            break
                    
                    if not is_correct_page:
                        title_tag = soup.find('title')
                        if title_tag and any(check in title_tag.get_text() for check in version_checks):
                            is_correct_page = True
                    
                    if not is_correct_page:
                        headings = soup.find_all(['h1', 'h2', 'h3', 'h5'])
                        for heading in headings:
                            if any(check in heading.get_text() for check in version_checks):
                                is_correct_page = True
                                break
                    
                    if not is_correct_page and any(check in page_text for check in version_checks):
                        is_correct_page = True
                    
                    if is_correct_page:
                        logging.info(f"✓ Correct version page found: {response.url}")
                        found_soup = soup
                        correct_version_page = True
                        break
                    else:
                        logging.warning(f"Page found but not for version {version}: {url}")
                        if found_soup is None:
                            found_soup = soup
                            logging.warning(f"Saved as fallback page (may list multiple versions)")
                
                elif response.status_code == 404:
                    continue
                else:
                    logging.warning(f"URL {url} returned status {response.status_code}")
            
            except Exception as e:
                logging.warning(f"Error checking {url}: {str(e)[:50]}")
                continue
        
        if correct_version_page:
            break
    
    if not correct_version_page and found_soup:
        logging.warning(f"Using fallback page for {app_name} {version} (may contain multiple versions)")
    
    if not found_soup:
        logging.error(f"Could not find any release page for {app_name} {version}")
        return None
    
    # --- IMPROVED VARIANT FINDER ---
    rows = found_soup.find_all('div', class_='table-row headerFont')
    logging.debug(f"Found {len(rows)} variant rows")
    download_page_url = None
    
    # Priority 1: Exact version match with all criteria
    for row in rows:
        row_text = row.get_text().lower()  # Case-insensitive
        logging.debug(f"Checking row: {row_text[:100]}...")
        
        # Check version
        if version in row_text or version.replace('.', '-') in row_text:
            # Check arch: for universal, look for both or 'universal'
            arch_match = (target_arch == 'universal' and ('arm64-v8a' in row_text and 'armeabi-v7a' in row_text)) or arch_criteria in row_text
            # Check DPI: any match if list
            dpi_match = any(dpi.lower() in row_text for dpi in dpi_criteria)
            # Check min API
            api_match = min_api_criteria.lower() in row_text
            # Check type
            type_match = type_criteria.lower() in row_text
            
            if arch_match and dpi_match and api_match and type_match:
                sub_url = row.find('a', class_='accent_color')
                if sub_url:
                    download_page_url = base_url + sub_url['href']
                    logging.info(f"Found exact match variant: {download_page_url}")
                    break
    
    # Priority 2: Broader match without exact version
    if not download_page_url:
        for row in rows:
            row_text = row.get_text().lower()
            arch_match = (target_arch == 'universal' and ('arm64-v8a' in row_text and 'armeabi-v7a' in row_text)) or arch_criteria in row_text
            dpi_match = any(dpi.lower() in row_text for dpi in dpi_criteria)
            api_match = min_api_criteria.lower() in row_text
            type_match = type_criteria.lower() in row_text
            
            if arch_match and dpi_match and api_match and type_match:
                sub_url = row.find('a', class_='accent_color')
                if sub_url:
                    download_page_url = base_url + sub_url['href']
                    # Extract version for warning
                    match = re.search(r'(\d+(\.\d+)+(\.\w+)*)', row_text)
                    actual_version = match.group(1) if match else 'unknown'
                    logging.warning(f"Using closest variant {actual_version} (no exact version match)")
                    break
    
    # Fallback: First row matching base criteria
    if not download_page_url:
        if rows:
            row = rows[0]  # First variant as last resort
            sub_url = row.find('a', class_='accent_color')
            if sub_url:
                download_page_url = base_url + sub_url['href']
                logging.warning(f"Using first available variant as fallback: {download_page_url}")
    
    if not download_page_url:
        logging.error(f"No variant found for {app_name} {version} with criteria {criteria}")
        return None
    
    # --- STANDARD DOWNLOAD FLOW WITH ERROR HANDLING ---
    try:
        time.sleep(timeout)
        response = scraper.get(download_page_url)
        response.raise_for_status()
        logging.info(f"Accessed variant page: {response.url}")
        soup = BeautifulSoup(response.content, "html.parser")
        sub_url = soup.find('a', class_='downloadButton')
        if not sub_url:
            raise ValueError("No download button found on variant page")
        
        final_download_page_url = base_url + sub_url['href']
        time.sleep(timeout)
        response = scraper.get(final_download_page_url)
        response.raise_for_status()
        logging.info(f"Accessed download page: {response.url}")
        soup = BeautifulSoup(response.content, "html.parser")
        button = soup.find('a', id='download-link')
        if not button:
            raise ValueError("No final download link found")
        
        return base_url + button['href']
    
    except Exception as e:
        logging.error(f"Error in download flow: {e}")
        return None

def get_architecture_criteria(arch: str) -> str:  # Return str for text matching
    arch_mapping = {
        "arm64-v8a": "arm64-v8a",
        "armeabi-v7a": "armeabi-v7a",
        "universal": "universal"  # Handled specially in checks
    }
    return arch_mapping.get(arch, "universal")

def get_latest_version(app_name: str, config: dict, timeout: int = 5) -> str:
    # Improved: Parse main app page for latest stable
    try:
        main_url = f"{base_url}/apk/{config['org']}/{config['name']}/"
        time.sleep(timeout)
        response = scraper.get(main_url)
        if response.status_code == 200:
            soup = BeautifulSoup(response.content, "html.parser")
            # Find latest version link, exclude alpha/beta
            version_links = soup.find_all('a', class_='fontBlack', href=re.compile(r'-release/$'))
            for link in version_links:
                version_text = link.get_text().strip().lower()
                if 'alpha' not in version_text and 'beta' not in version_text:
                    match = re.search(r'(\d+(\.\d+)+)', version_text)
                    if match:
                        return match.group(1)
    except Exception as e:
        logging.warning(f"Main page fetch failed: {e}")
    
    # Fallback: Original uploads method
    url = f"{base_url}/uploads/?appcategory={config['name']}"
    time.sleep(timeout)
    response = scraper.get(url)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")
    app_rows = soup.find_all("div", class_="appRow")
    version_pattern = re.compile(r'\d+(\.\d+)*(-[a-zA-Z0-9]+(\.\d+)*)*')
    for row in app_rows:
        version_text = row.find("h5", class_="appRowTitle").a.text.strip().lower()
        if "alpha" not in version_text and "beta" not in version_text:
            match = version_pattern.search(version_text)
            if match:
                version = match.group()
                version_parts = version.split('.')
                base_version_parts = [part for part in version_parts if part.isdigit()]
                if base_version_parts:
                    return '.'.join(base_version_parts)
    return None
