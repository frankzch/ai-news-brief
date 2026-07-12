import trafilatura
import logging
import requests
import random
import time
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Some domains block simple requests - need special handling
DIFFICULT_DOMAINS = [
    'medium.com', 'towardsdatascience.com', 'levelup.gitconnected.com',
    'betterprogramming.pub', 'hackernoon.com',
    'marktechpost.com', 'artificialintelligence-news.com',
    'theverge.com',
]

# Domains that require TLS fingerprint impersonation (curl_cffi) to bypass WAF
CURLCFFI_DOMAINS = [
    'marktechpost.com', 'artificialintelligence-news.com',
    'techcrunch.com', 'businessinsider.com', 'bloomberg.com', 'wsj.com',
    'nytimes.com', 'washingtonpost.com',
    'theverge.com',
    'awesomeagents.ai',
]

# Realistic browser headers to avoid 403
DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-User': '?1',
    'Cache-Control': 'max-age=0',
}

# Reusable session with connection pooling to avoid "Connection pool is full" warnings
_session = None

def _get_session():
    """Get or create a reusable requests session with proper connection pooling."""
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(DEFAULT_HEADERS)
        # Configure connection pool size
        adapter = HTTPAdapter(
            pool_connections=10,
            pool_maxsize=20,
            max_retries=Retry(total=0)  # We handle retries ourselves
        )
        _session.mount('http://', adapter)
        _session.mount('https://', adapter)
    return _session


class ContentProcessor:
    @staticmethod
    def _is_difficult_domain(url: str) -> bool:
        """Check if URL is from a domain known to block simple requests."""
        url_lower = url.lower()
        return any(domain in url_lower for domain in DIFFICULT_DOMAINS)
    
    @staticmethod
    def _needs_curlcffi(url: str) -> bool:
        """Check if URL needs curl_cffi TLS impersonation to bypass WAF."""
        url_lower = url.lower()
        return any(domain in url_lower for domain in CURLCFFI_DOMAINS)

    @staticmethod
    def _fetch_with_curlcffi(url, timeout):
        """Fetch URL using curl_cffi with TLS fingerprint impersonation."""
        try:
            from curl_cffi import requests as cffi_requests
            resp = cffi_requests.get(url, impersonate="chrome", timeout=timeout)
            resp.raise_for_status()
            text = trafilatura.extract(resp.text)
            if text:
                logging.info(f"curl_cffi succeeded for {url}")
                return text
        except Exception as e:
            logging.debug(f"curl_cffi failed for {url}: {e}")
        return None

    @staticmethod
    def fetch_and_extract(url, timeout=None, max_retries=2):
        """
        Fetches the URL and extracts the main text content.
        Uses curl_cffi for WAF-protected domains, trafilatura for other difficult domains.
        """
        # Load timeout from config if not provided
        if timeout is None:
            from config_loader import ConfigLoader
            config = ConfigLoader.get_instance().get('fetching', {})
            timeout = config.get('network_timeout', 30)
        
        # For domains needing TLS impersonation, try curl_cffi first
        if ContentProcessor._needs_curlcffi(url):
            result = ContentProcessor._fetch_with_curlcffi(url, timeout)
            if result:
                return result
            # Fall through to other methods

        # For difficult domains, use trafilatura's built-in fetcher which handles more edge cases
        if ContentProcessor._is_difficult_domain(url):
            try:
                from trafilatura.settings import use_config
                traf_config = use_config()
                traf_config.set("DEFAULT", "DOWNLOAD_TIMEOUT", str(timeout))
                downloaded = trafilatura.fetch_url(url, config=traf_config)
                if downloaded:
                    text = trafilatura.extract(downloaded)
                    if text:
                        return text
            except Exception as e:
                logging.debug(f"Trafilatura fetch failed for {url}: {e}")
            # Fall through to requests-based approach
        
        # Standard approach with retries
        for attempt in range(max_retries):
            try:
                # Add slight delay between retries
                if attempt > 0:
                    time.sleep(random.uniform(0.5, 1.5))
                
                session = _get_session()
                response = session.get(
                    url, 
                    timeout=timeout,
                    allow_redirects=True
                )
                response.raise_for_status()
                
                # Use trafilatura to extract content from the HTML
                text = trafilatura.extract(response.text)
                return text
                
            except requests.exceptions.HTTPError as e:
                status_code = e.response.status_code if e.response else 0
                if status_code == 403:
                    # Try curl_cffi as fallback for 403
                    logging.info(f"HTTP 403 for {url}, trying curl_cffi fallback...")
                    result = ContentProcessor._fetch_with_curlcffi(url, timeout)
                    if result:
                        return result
                    logging.info(f"curl_cffi fallback also failed for {url}, skipping")
                    return None
                elif status_code in (429, 451, 503):
                    # Rate limited / Blocked / Service Unavailable - try curl_cffi as last resort
                    logging.info(f"Access blocked for {url} (HTTP {status_code}), trying curl_cffi fallback...")
                    result = ContentProcessor._fetch_with_curlcffi(url, timeout)
                    if result:
                        return result
                    return None
                elif status_code >= 500:
                    # Server error - might be temporary, retry
                    logging.debug(f"Server error for {url} (HTTP {status_code}), attempt {attempt + 1}")
                    continue
                else:
                    logging.warning(f"HTTP error for {url}: {e}")
                    return None
                    
            except requests.exceptions.Timeout:
                logging.debug(f"Timeout for {url}, attempt {attempt + 1}")
                continue
                
            except requests.exceptions.RequestException as e:
                logging.warning(f"Request failed for {url}: {e}")
                return None
                
            except Exception as e:
                logging.warning(f"Failed to fetch/extract {url}: {e}")
                return None
        
        logging.info(f"All retries exhausted for {url}")
        return None
