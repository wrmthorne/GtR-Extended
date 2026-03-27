import asyncio
import json
import logging
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional

import httpx
from lxml import html as lxml_html
from playwright.async_api import async_playwright
from tenacity import retry, stop_after_attempt, wait_exponential



# Configuration
API_BASE = "https://gtr.ukri.org/gtr/api"
BULK_HEADERS = {"Accept": "application/vnd.rcuk.gtr.json-v7"}
OPPORTUNITY_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:147.0) Gecko/20100101 Firefox/147.0"}
SAVE_DIR = Path("./data_cache")
FETCH_STATE_DIR = Path("./fetch_states")

ENDPOINTS_KEYS = {
    "projects": "project",
    "funds": "fund",
    "organisations": "organisation",
    "persons": "person",
    "outcomes/publications": "publication",
    "outcomes/keyfindings": "keyFinding",
    "outcomes/impactsummaries": "impactSummary",
    "outcomes/collaborations": "collaboration",
    "outcomes/disseminations": "dissemination",
    "outcomes/furtherfundings": "futherfunding", # WHY????
    "outcomes/intellectualproperties": "intellectualProperty",
    "outcomes/policyinfluences": "policyInfluence",
    "outcomes/products": "product",
    "outcomes/researchmaterials": "researchMaterial",
    "outcomes/artisticandcreativeproducts": "artisticAndCreativeProduct",
    "outcomes/researchdatabaseandmodels": "researchDatabaseAndModel",
    "outcomes/softwareandtechnicalproducts": "softwareAndTechnicalProduct",
    "outcomes/spinouts": "spinOut",
}

OPPORTUNITIES_BASE = "https://www.ukri.org/opportunity/"
OPPORTUNITIES_PARAMS = (
    "?keywords="
    "&filter_status%5B%5D=open"
    "&filter_status%5B%5D=closed"
    "&filter_status%5B%5D=upcoming"
    "&filter_order=publication_date"
    "&filter_submitted=true"
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ProposalFetcher")


class BulkFetcher:

    def __init__(self, endpoint: str, page_size: int = 100, rate_limit: float = 1.0):
        self.endpoint = endpoint.strip("/")
        self.page_size = page_size
        self.delay = rate_limit
        self.cache_path = SAVE_DIR / self.endpoint
        self.cache_path.mkdir(parents=True, exist_ok=True)
        self.state = self._load_state()

    def _load_state(self) -> Dict[str, Any]:
        if self._get_state_file().exists():
            return json.loads(self._get_state_file().read_text())
        return {"last_page": 0, "total_records": 0}

    def _save_state(self, current_page: int):
        self.state["last_page"] = current_page
        self._get_state_file().write_text(json.dumps(self.state, indent=2))

    def _get_state_file(self):
        return FETCH_STATE_DIR / f"{self.endpoint.replace('/', '_')}_state.json"

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def fetch_page(self, client: httpx.AsyncClient, page: int) -> Optional[Dict]:
        params = {"p": page, "s": self.page_size}
        response = await client.get(f"{API_BASE}/{self.endpoint}", params=params)

        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def run(self):
        async with httpx.AsyncClient(headers=BULK_HEADERS, timeout=30.0) as client:
            # Initial hit to get total size if unknown
            if self.state["total_records"] == 0:
                init_data = await self.fetch_page(client, 1)
                self.state["total_records"] = init_data.get("totalSize", 0)
                logger.info(f"Total records to fetch: {self.state['total_records']}")

            current_page = self.state["last_page"] + 1
            data_key = ENDPOINTS_KEYS[self.endpoint]

            while True:
                logger.info(f"Fetching page {current_page}...")
                data = await self.fetch_page(client, current_page)

                if not isinstance(data, dict) or not data.get(data_key):
                    logger.info("Reached end of data or empty response.")
                    break

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                file_name = self.cache_path / f"batch_{current_page}_{timestamp}.json"
                with open(file_name, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)

                self._save_state(current_page)

                await asyncio.sleep(self.delay)
                current_page += 1


class OpportunityFetcher:

    def __init__(self, rate_limit: float = 1.0):
        self.delay = rate_limit
        self.listing_dir = SAVE_DIR / "opportunities" / "listings"
        self.detail_dir = SAVE_DIR / "opportunities"/ "opportunities"
        self.pdf_dir = SAVE_DIR / "opportunities" / "pdfs"
        for d in (self.listing_dir, self.detail_dir, self.pdf_dir, FETCH_STATE_DIR):
            d.mkdir(parents=True, exist_ok=True)
        self.state = self._load_state()

    def _load_state(self) -> dict:
        path = self._state_file()
        if path.exists():
            return json.loads(path.read_text())
        return {
            "listings_done": False,
            "listing_pages_fetched": 0,
            "total_listing_pages": None,
            "opportunities_parsed": False,
            "opportunity_urls": [],
            "details_completed": [],
        }

    def _save_state(self):
        self._state_file().write_text(json.dumps(self.state, indent=2))

    def _state_file(self) -> Path:
        return FETCH_STATE_DIR / "opportunities_state.json"

    def _build_listing_url(self, page: int) -> str:
        base = OPPORTUNITIES_BASE
        if page > 1:
            base = f"{OPPORTUNITIES_BASE}page/{page}/"
        return base + OPPORTUNITIES_PARAMS

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def _fetch_listing_page(self, client: httpx.AsyncClient, page: int) -> Optional[str]:
        url = self._build_listing_url(page)
        response = await client.get(url)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.text

    def _detect_total_pages(self, html_content: str) -> Optional[int]:
        tree = lxml_html.fromstring(html_content)
        page_nums = []
        for href in tree.xpath("//a/@href"):
            match = re.search(r"/opportunity/page/(\d+)/", href)
            if match:
                page_nums.append(int(match.group(1)))
        return max(page_nums) if page_nums else None

    async def fetch_all_listings(self):
        if self.state["listings_done"]:
            logger.info("Listing pages already fetched, skipping phase 1")
            return

        async with httpx.AsyncClient(headers=OPPORTUNITY_HEADERS, timeout=30.0, follow_redirects=True) as client:
            current_page = self.state["listing_pages_fetched"] + 1

            while True:
                logger.info(f"Fetching listing page {current_page}...")
                html = await self._fetch_listing_page(client, current_page)

                if html is None:
                    break

                # Detect total pages from first page
                if self.state["total_listing_pages"] is None:
                    total = self._detect_total_pages(html)
                    if total:
                        self.state["total_listing_pages"] = total
                        logger.info(f"Detected {total} total listing pages")

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                out_path = self.listing_dir / f"page_{current_page}_{timestamp}.html"
                out_path.write_text(html, encoding="utf-8")

                self.state["listing_pages_fetched"] = current_page
                self._save_state()

                if (
                    self.state["total_listing_pages"]
                    and current_page >= self.state["total_listing_pages"]
                ):
                    logger.info("Reached last listing page")
                    break

                await asyncio.sleep(self.delay)
                current_page += 1

        self.state["listings_done"] = True
        self._save_state()
        logger.info(f"{self.state['listing_pages_fetched']} listing pages saved")

    def parse_opportunity_urls(self):
        if self.state["opportunities_parsed"]:
            logger.info("Opportunities already parsed, skipping...")
            return

        urls = set()
        for listing_file in sorted(self.listing_dir.glob("page_*.html")):
            tree = lxml_html.fromstring(listing_file.read_text(encoding="utf-8"))
            for href in tree.xpath("//a/@href"):
                if re.match(r"https://www\.ukri\.org/opportunity/[a-z0-9-]+/?$", href):
                    urls.add(href.rstrip("/") + "/")

        self.state["opportunity_urls"] = sorted(urls)
        self.state["opportunities_parsed"] = True
        self._save_state()
        logger.info(f"Found {len(urls)} opportunities")

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def _fetch_detail_page(self, client: httpx.AsyncClient, url: str) -> Optional[str]:
        response = await client.get(url)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.text

    def _slug_from_url(self, url: str) -> str:
        parts = url.rstrip("/").split("/")
        return parts[-1]

    async def _fetch_and_save_detail(self, browser, url: str, html_path: Path, pdf_path: Path) -> bool:
        page = await browser.new_page(user_agent=OPPORTUNITY_HEADERS["User-Agent"])
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            if response and response.status == 404:
                return False
            try:
                await page.wait_for_selector("article, .opportunity-content, h1", timeout=15000)
            except Exception:
                logger.warning(f"Content did not render for {url}")
                return False

            html = await page.content()
            html_path.write_text(html, encoding="utf-8")
            await page.pdf(path=str(pdf_path), format="A4", print_background=True)
            return True
        finally:
            await page.close()

    async def fetch_all_details(self):
        if not self.state["opportunities_parsed"]:
            raise RuntimeError("Must run parse_opportunity_urls() before fetching details")

        urls_to_fetch = [
            url for url in self.state["opportunity_urls"]
            if url not in self.state["details_completed"]
        ]

        if not urls_to_fetch:
            logger.info("All opportunity details already fetched")
            return

        logger.info(f"Phase 3: {len(urls_to_fetch)} opportunities to fetch")

        async with async_playwright() as pw:
            browser = await pw.chromium.launch()

            for i, url in enumerate(urls_to_fetch, 1):
                slug = self._slug_from_url(url)
                logger.info(f"[{i}/{len(urls_to_fetch)}] Fetching {slug}")

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                html_path = self.detail_dir / f"{slug}_{timestamp}.html"
                pdf_path = self.pdf_dir / f"{slug}_{timestamp}.pdf"

                try:
                    ok = await self._fetch_and_save_detail(browser, url, html_path, pdf_path)
                    if not ok:
                        logger.warning(f"404 for {url}, skipping")
                        continue
                    self.state["details_completed"].append(url)
                    self._save_state()
                except Exception as e:
                    logger.error(f"Failed to process {slug}: {e}")
                    continue
                finally:
                    await asyncio.sleep(self.delay)

            await browser.close()

        logger.info(f"{len(self.state['details_completed'])} opportunities processed")

    async def run(self):
        await self.fetch_all_listings()
        self.parse_opportunity_urls()
        await self.fetch_all_details()


async def main():
    # Fetch proposal data from Gateway to Research
    for endpoint in ENDPOINTS_KEYS:
        fetcher = BulkFetcher(endpoint=endpoint, page_size=100)
        await fetcher.run()

    # Fetch opportunity data from UKRI website
    fetcher = OpportunityFetcher(rate_limit=8.0)
    await fetcher.run()


if __name__ == "__main__":
    asyncio.run(main())