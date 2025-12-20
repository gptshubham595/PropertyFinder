import pandas as pd
import time
import random
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import re
import os
from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for
from threading import Thread
import json
from datetime import datetime

try:
    from proxy_requests.proxy_requests import ProxyRequests

    PROXY_AVAILABLE = True
except ImportError:
    PROXY_AVAILABLE = False

app = Flask(__name__)
app.config["SECRET_KEY"] = "bangalore-property-finder-2024"

# Global variables to track scraping status
scraping_status = {
    "running": False,
    "progress": 0,
    "message": "Ready to start",
    "current_site": "",
    "properties_found": 0,
    "results": None,
}


class PropertyFinder:
    def __init__(
        self,
        driver_path="/opt/homebrew/bin/chromedriver",
        budget_max=8000000,
        bhk=["2", "3"],
        rera_req="Yes",
        preferred_only=False,
    ):
        """Initialize with Chrome driver path and search criteria"""
        self.driver_path = driver_path
        self.driver = None

        # Requirements
        self.bedrooms = bhk
        self.budget_max = budget_max
        self.rera_req = rera_req
        self.preferred_only = preferred_only

        # Preferred builders
        self.preferred_builders = [
            "prestige",
            "sobha",
            "brigade",
            "puravankara",
            "godrej",
            "embassy",
            "l&t",
            "l & t",
            "salarpuria",
            "sattva",
            "assetz",
            "casagrand",
        ]

        # Data storage
        self.all_data = {
            "Source": [],
            "Property": [],
            "Project": [],
            "Builder": [],
            "Preferred Builder": [],
            "BHK": [],
            "Rating": [],
            "Places Nearby": [],
            "Price": [],
            "Price Numeric": [],
            "Price per Sq.ft": [],
            "Area": [],
            "Area in Sq.ft": [],
            "Area in Sq.m": [],
            "Description": [],
            "Posted Date": [],
            "Posted By": [],
            "RERA": [],
            "Property URL": [],
        }

    def setup_driver(self):
        """Setup Chrome driver"""
        service = Service(self.driver_path)
        chrome_options = Options()
        # chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_argument("--ignore-certificate-errors")
        # List of modern user agents for rotation
        user_agents = [
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        ]

        chrome_options.add_argument(f"--user-agent={random.choice(user_agents)}")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option("useAutomationExtension", False)

        # Proxy Logic (Attempt to use if available, otherwise just use IP rotation via 4G/VPN usually, but here we add what we can)
        if PROXY_AVAILABLE:
            try:
                # Basic usage to get a proxy - this library rotates for requests, but extracting one for Selenium is tricky.
                # We will rely on the robust user-agent and headers for now as 'ProxyRequests' is mainly for 'requests' lib.
                # However, if we wanted to use it, we'd need to fetch a proxy string.
                pass
            except:
                pass

        self.driver = webdriver.Chrome(service=service, options=chrome_options)

    def random_delay(self, min_sec=2, max_sec=5):
        time.sleep(random.uniform(min_sec, max_sec))

    def extract_price_numeric(self, price_text):
        if not price_text:
            return None
        price_text = (
            price_text.lower()
            .replace(",", "")
            .replace("₹", "")
            .replace("rs", "")
            .strip()
        )
        numbers = re.findall(r"(\d+\.?\d*)", price_text)
        if not numbers:
            return None
        value = float(numbers[0])
        if "crore" in price_text or "cr" in price_text:
            return value * 10000000
        elif "lakh" in price_text or "lac" in price_text:
            return value * 100000
        elif "k" in price_text:
            return value * 1000
        return value

    def extract_bhk(self, text):
        if not text:
            return None
        # Handle "2 BHK", "2 bedroom", "2BHK", "2 bh"
        bhk_match = re.search(r"(\d+)\s*(?:bhk|bedroom|bh)", text.lower())
        return bhk_match.group(1) if bhk_match else None

    def check_preferred_builder(self, text):
        if not text:
            return False
        return any(builder in text.lower() for builder in self.preferred_builders)

    def add_property(self, source, property_data):
        # Update internal data
        for key in self.all_data.keys():
            self.all_data[key].append(property_data.get(key.lower().replace(" ", "_")))

        # Update global status for live updates
        global scraping_status
        new_prop = {
            key: property_data.get(key.lower().replace(" ", "_"))
            for key in self.all_data.keys()
        }
        new_prop["Source"] = source
        if "results" in scraping_status:
            scraping_status["results"].append(new_prop)

    def validate_property(self, property_data):
        """Validate property against requirements"""
        # Price Check (Loose check initially, can filter stricter later)
        if self.budget_max and property_data["price_numeric"]:
            if property_data["price_numeric"] > self.budget_max:
                return False

        # BHK Check
        # If scraper found "3 BHK", and we want "2", we filter.
        # But if we want ["2", "3"], we keep.
        if property_data["bhk"] and property_data["bhk"] not in self.bedrooms:
            return False

        # RERA Check - LOOSENED: Many properties don't show RERA text in card
        # Only filter out if user explicitly wants RERA AND the property explicitly says "No"
        # (We accept "Yes" and "Not Mentioned" as most RERA properties don't display it in summary)
        # if self.rera_req == "Yes" and property_data["rera"] == "No":
        #     return False

        # Preferred Builder Check
        if self.preferred_only and property_data["preferred_builder"] != "⭐ YES":
            return False

        return True

    def build_99acres_url(self, page=1):
        # Use the FFID URL which is more reliable
        base = "https://www.99acres.com/property-in-bangalore-ffid"

        # safely handle BHK list
        if isinstance(self.bedrooms, list):
            bhk_list = self.bedrooms
        else:
            bhk_list = str(self.bedrooms).split(",")

        # 99acres uses simpler codes or comma values for bedrooms
        # typically: 2,3
        bedroom_str = ",".join([str(b).strip() for b in bhk_list])

        params = [
            "city=20",  # Bangalore
            f"bedroom_num={bedroom_str}",
            "preference=S",  # Sale
            "area_unit=1",  # Sqft
            "res_com=R",  # Residential
        ]

        # Budget in 99acres is usually passed as min_budget and max_budget in absolute numbers or codes
        # But robustly, we can filter on client side if URL params are tricky.
        # However, let's try to pass 'budget_max' in Lakhs if that's what the old code did,
        # or better, omit it to get more results and filter in Python (safest).
        # We will filter in Python to ensure we don't get 0 results due to strict URL params.

        if page > 1:
            params.append(f"page={page}")

        return f"{base}?{'&'.join(params)}"

    def scrape_99acres(self, max_pages=5):
        global scraping_status
        scraping_status["current_site"] = "99acres"
        properties_found = 0

        for page in range(1, max_pages + 1):
            if not scraping_status["running"]:
                break

            url = self.build_99acres_url(page)
            print(f"Scraping URL: {url}")  # Debug log
            scraping_status["message"] = f"Scraping 99acres page {page}/{max_pages}"

            try:
                self.driver.get(url)
                # Save screenshot for debugging
                self.driver.save_screenshot(f"debug_99acres_page_{page}.png")
                self.random_delay(3, 5)

                # Scrolling to trigger lazy loading
                for _ in range(5):  # Intead of 3, scroll more
                    self.driver.execute_script("window.scrollBy(0, 1500);")
                    self.random_delay(1, 2)

                # Wait for ANY property tuple
                try:
                    WebDriverWait(self.driver, 10).until(
                        EC.presence_of_element_located(
                            (By.CSS_SELECTOR, "[class*='tuple'], [class*='Tuple']")
                        )
                    )
                except:
                    print("Timeout waiting for tuples")  # Debug

                soup = BeautifulSoup(self.driver.page_source, "html.parser")

                # ROBUST SELECTORS 2024
                # srpTuple__tuple, projectTuple__tuple, outerTupleWrap, tupleWrap
                property_boxes = soup.select(
                    ".srpTuple__tuple, .projectTuple__tuple, [class*='outerTupleWrap'], [class*='tupleWrap'], div[class*='tuple']"
                )

                print(f"Found {len(property_boxes)} property boxes")  # Debug

                if not property_boxes:
                    print(
                        "No property boxes found! Page source might be blocked or empty."
                    )
                    # Optionally save page source to debug if needed, but for now just continue
                    continue

                for prop in property_boxes:
                    if not scraping_status["running"]:
                        break
                    try:
                        property_data = {}

                        # Extract Title
                        # srpTuple__propertyName, projectTuple__projectName
                        title_elem = prop.select_one(
                            ".srpTuple__propertyName, .projectTuple__projectName, [class*='propertyHeading'], [class*='projectHeading'], h2"
                        )
                        property_data["property"] = (
                            title_elem.text.strip()
                            if title_elem
                            else "Unknown Property"
                        )

                        # Extract Builder / Project
                        # srpTuple__builderName
                        project_elem = prop.select_one(
                            ".srpTuple__builderName, [class*='locationName'], [class*='tupleHeading']"
                        )
                        property_data["project"] = (
                            project_elem.text.strip() if project_elem else None
                        )
                        property_data["builder"] = property_data["project"]  # Fallback

                        # Extract Price
                        price_elem = prop.select_one(
                            "#srp_tuple_price, .srpTuple__price, [class*='priceVal'], [class*='priceWrap'], [class*='ccl2']"
                        )
                        property_data["price"] = (
                            price_elem.text.strip() if price_elem else None
                        )

                        property_data["price_numeric"] = self.extract_price_numeric(
                            property_data["price"]
                        )

                        # Client-side Budget Filtering (Crucial if URL param is removed)
                        # self.budget_max is in absolute (e.g., 8000000)
                        if self.budget_max and property_data["price_numeric"]:
                            if property_data["price_numeric"] > self.budget_max:
                                continue

                        # Extract Area
                        area_elem = prop.select_one(
                            "#srp_tuple_primary_area, .srpTuple__primaryArea, [class*='totolAreaWrap'], [class*='area1Type'], [class*='areaVal']"
                        )
                        property_data["area"] = (
                            area_elem.text.strip() if area_elem else None
                        )
                        property_data["area_in_sq.ft"] = property_data["area"]
                        property_data["area_in_sq.m"] = None

                        # Description / Configuration (BHK)
                        # srpTuple__bedroomNum
                        desc_elem = prop.select_one(
                            ".srpTuple__bedroomNum, .srpTuple__configuration"
                        )
                        property_data["description"] = (
                            desc_elem.text.strip()
                            if desc_elem
                            else property_data["property"]
                        )

                        # RERA
                        property_data["rera"] = (
                            "Yes"
                            if prop.find(string=re.compile("rera", re.I))
                            else "Not Mentioned"
                        )

                        # URL
                        link = prop.select_one(
                            "a.body_med, a#srp_tuple_property_title, a.srpTuple__propertyName, [class*='tuple'] a"
                        )
                        if not link:
                            link = prop.find("a", href=True)

                        if link and link.get("href"):
                            href = link["href"]
                            property_data["property_url"] = (
                                href
                                if href.startswith("http")
                                else "https://www.99acres.com" + href
                            )
                        else:
                            property_data["property_url"] = None

                        # Simple BHK extraction from title or description
                        text_for_bhk = (
                            str(property_data["property"])
                            + " "
                            + str(property_data["description"])
                        ).lower()
                        bhk_found = "Unknown"
                        if "1 bhk" in text_for_bhk or "1bhk" in text_for_bhk:
                            bhk_found = "1"
                        elif "2 bhk" in text_for_bhk or "2bhk" in text_for_bhk:
                            bhk_found = "2"
                        elif "3 bhk" in text_for_bhk or "3bhk" in text_for_bhk:
                            bhk_found = "3"
                        elif "4 bhk" in text_for_bhk or "4bhk" in text_for_bhk:
                            bhk_found = "4"

                        property_data["bhk"] = bhk_found

                        property_data["source"] = "99acres"

                        # Preferred Builder Check
                        property_data["preferred_builder"] = (
                            self.is_preferred_builder(property_data["builder"])
                            if property_data["builder"]
                            else "No"
                        )

                        self.add_property(property_data)
                        properties_found += 1

                    except Exception as e:
                        print(f"Error parsing property: {e}")
                        continue

            except Exception as e:
                print(f"Error scraping 99acres page {page}: {e}")

            self.random_delay(2, 4)

        print(f"99acres finished. Found {properties_found} properties.")

    def build_magicbricks_url(self, page=1):
        # Use user-provided URL that is known to work
        base = "https://www.magicbricks.com/2-bhk-flats-in-bangalore-for-sale-price-70-lakhs-to-80-lakhs-pppfs"
        if page > 1:
            return f"{base}?page={page}"
        return base

    def scrape_magicbricks(self, max_pages=5):
        global scraping_status
        scraping_status["current_site"] = "MagicBricks"
        properties_found = 0

        for page in range(1, max_pages + 1):
            if not scraping_status["running"]:
                break
            url = self.build_magicbricks_url(page)
            scraping_status["message"] = f"Scraping MagicBricks page {page}/{max_pages}"

            try:
                self.driver.get(url)
                self.random_delay(4, 7)

                # Wait for JS to load content - MagicBricks uses heavy JS rendering
                try:
                    WebDriverWait(self.driver, 10).until(
                        EC.presence_of_element_located((By.CLASS_NAME, "mb-srp__card"))
                    )
                except:
                    pass  # Continue even if wait times out

                soup = BeautifulSoup(self.driver.page_source, "html.parser")
                property_boxes = soup.find_all("div", class_="mb-srp__card")

                if not property_boxes:
                    break

                for prop in property_boxes:
                    if not scraping_status["running"]:
                        break
                    try:
                        property_data = {}

                        try:
                            title_elem = prop.find("h2", class_="mb-srp__card--title")
                            property_data["property"] = (
                                title_elem.text.strip() if title_elem else None
                            )
                        except:
                            property_data["property"] = None

                        try:
                            project_elem = prop.find(
                                "a", class_="mb-srp__card__society--name"
                            )
                            property_data["project"] = (
                                project_elem.text.strip() if project_elem else None
                            )
                        except:
                            property_data["project"] = None

                        property_data["builder"] = property_data["project"]

                        try:
                            price_elem = prop.find(
                                "div", class_="mb-srp__card__price--amount"
                            )
                            property_data["price"] = (
                                price_elem.text.strip() if price_elem else None
                            )
                        except:
                            property_data["price"] = None

                        property_data["price_numeric"] = self.extract_price_numeric(
                            property_data["price"]
                        )

                        try:
                            # Area is in a summary structure
                            # <div class="mb-srp__card__summary--label">Super Area</div>
                            # <div class="mb-srp__card__summary--value">1150 sqft</div>
                            # Use data-summary attribute if possible, else look for text
                            area_value = None
                            summary_elems = prop.find_all(
                                "div", class_="mb-srp__card__summary--value"
                            )
                            for elem in summary_elems:
                                parent = elem.parent
                                if (
                                    parent
                                    and "area" in parent.get("data-summary", "").lower()
                                ):
                                    area_value = elem.text.strip()
                                    break
                                # Fallback to looking at label sibling
                                label = elem.find_previous_sibling(
                                    "div", class_="mb-srp__card__summary--label"
                                )
                                if label and "area" in label.text.lower():
                                    area_value = elem.text.strip()
                                    break

                            property_data["area"] = area_value
                            property_data["area_in_sq.ft"] = property_data["area"]
                        except:
                            property_data["area"] = None
                            property_data["area_in_sq.ft"] = None

                        property_data["area_in_sq.m"] = None

                        property_data["rera"] = (
                            "Yes"
                            if prop.find(string=re.compile("rera", re.I))
                            else "Not Mentioned"
                        )

                        try:
                            # Link usually on the card or title
                            # The card itself is clickable often, but let's check title link or card attributes
                            # Inspection showed <div ... onclick="window.open(...)"> possibly?
                            # Or look for an <a> tag inside.
                            link = prop.find(
                                "a", class_="mb-srp__card__society--name"
                            )  # This is likely just society link
                            # Let's try locating the main link, usually wraps title or "View Details"
                            # Inspection didn't show explicit link in my snippet, but usually h2 is wrapped or there is a link.
                            # Fallback: find any 'a' with href that seems like a property link
                            links = prop.find_all("a", href=True)
                            prop_link = None
                            for l in links:
                                if (
                                    "property-detail" in l["href"]
                                    or "/buy/" in l["href"]
                                ):
                                    prop_link = l["href"]
                                    break

                            # If no specific property link found, grab the first one that is not society
                            if not prop_link and links:
                                prop_link = links[0]["href"]

                            property_data["property_url"] = (
                                prop_link
                                if prop_link and prop_link.startswith("http")
                                else (
                                    "https://www.magicbricks.com" + prop_link
                                    if prop_link
                                    else None
                                )
                            )
                        except:
                            property_data["property_url"] = None

                        bhk_text = f"{property_data['property']}"
                        property_data["bhk"] = self.extract_bhk(bhk_text)

                        is_preferred = self.check_preferred_builder(
                            property_data["builder"]
                        )
                        property_data["preferred_builder"] = (
                            "⭐ YES" if is_preferred else "No"
                        )

                        # Additional fields
                        property_data["rating"] = None
                        property_data["price_per_sq.ft"] = None
                        property_data["places_nearby"] = None
                        property_data["description"] = None
                        property_data["posted_date"] = None
                        property_data["posted_by"] = None

                        if self.validate_property(property_data):
                            self.add_property("MagicBricks", property_data)
                            properties_found += 1
                            scraping_status["properties_found"] = len(
                                self.all_data["Property"]
                            )

                    except Exception as e:
                        continue

                self.random_delay(2, 4)

            except Exception as e:
                continue

        return properties_found

    def build_housing_url(self, page=1):
        # Use the user-provided search URL format
        base = "https://housing.com/in/buy/searches/CcP38f9yfbk7p3m2h1fU526n4"
        if page > 1:
            return f"{base}?page={page}"
        return base

    def scrape_housing(self, max_pages=5):
        global scraping_status
        scraping_status["current_site"] = "Housing.com"
        properties_found = 0

        for page in range(1, max_pages + 1):
            if not scraping_status["running"]:
                break
            url = self.build_housing_url(page)
            scraping_status["message"] = f"Scraping Housing.com page {page}/{max_pages}"

            try:
                self.driver.set_page_load_timeout(30)  # 30 second timeout
                self.driver.get(url)
                self.random_delay(3, 5)

                # Wait for content to load
                try:
                    WebDriverWait(self.driver, 10).until(
                        EC.presence_of_element_located(
                            (By.CSS_SELECTOR, "article[data-testid='card-container']")
                        )
                    )
                except:
                    pass  # Continue even if wait times out

                soup = BeautifulSoup(self.driver.page_source, "html.parser")
                property_boxes = soup.find_all(
                    "article", attrs={"data-testid": "card-container"}
                )

                if not property_boxes:
                    break

                for prop in property_boxes:
                    if not scraping_status["running"]:
                        break
                    try:
                        property_data = {}

                        try:
                            # Housing.com places title inside an 'a' tag or h2
                            title_elem = prop.find("h2", class_="title-style")
                            property_data["property"] = (
                                title_elem.text.strip() if title_elem else None
                            )
                        except:
                            property_data["property"] = None

                        try:
                            # Project/Builder usually in subtitle
                            subtitle_elem = prop.find(
                                "div", attrs={"data-q": "subtitle"}
                            )
                            property_data["builder"] = (
                                subtitle_elem.text.strip() if subtitle_elem else None
                            )
                        except:
                            property_data["builder"] = None

                        property_data["project"] = property_data["builder"]

                        try:
                            price_elem = prop.find("div", attrs={"data-q": "price"})
                            property_data["price"] = (
                                price_elem.text.strip() if price_elem else None
                            )
                        except:
                            property_data["price"] = None

                        property_data["price_numeric"] = self.extract_price_numeric(
                            property_data["price"]
                        )

                        try:
                            area_elem = prop.find(
                                "div", attrs={"data-q": "builtup-area"}
                            )
                            property_data["area"] = (
                                area_elem.text.strip() if area_elem else None
                            )
                            property_data["area_in_sq.ft"] = property_data["area"]
                        except:
                            property_data["area"] = None
                            property_data["area_in_sq.ft"] = None

                        property_data["area_in_sq.m"] = None
                        property_data["rating"] = None
                        property_data["price_per_sq.ft"] = None
                        property_data["places_nearby"] = None
                        property_data["description"] = None
                        property_data["posted_date"] = None
                        property_data["posted_by"] = None

                        property_data["rera"] = (
                            "Yes"
                            if prop.find(string=re.compile("rera", re.I))
                            else "Not Mentioned"
                        )

                        try:
                            # Link is usually the parent of title or explicitly data-q="title"
                            link_elem = prop.find("a", attrs={"data-q": "title"})
                            if link_elem and link_elem.get("href"):
                                href = link_elem.get("href")
                                property_data["property_url"] = (
                                    "https://housing.com" + href
                                    if href.startswith("/")
                                    else href
                                )
                            else:
                                property_data["property_url"] = None
                        except:
                            property_data["property_url"] = None

                        bhk_text = f"{property_data['property']}"
                        property_data["bhk"] = self.extract_bhk(bhk_text)

                        is_preferred = self.check_preferred_builder(
                            property_data["builder"]
                        )
                        property_data["preferred_builder"] = (
                            "⭐ YES" if is_preferred else "No"
                        )

                        if self.validate_property(property_data):
                            self.add_property("Housing.com", property_data)
                            properties_found += 1
                            scraping_status["properties_found"] = len(
                                self.all_data["Property"]
                            )

                    except Exception as e:
                        continue

                self.random_delay(2, 4)

            except Exception as e:
                continue

        return properties_found

    def scrape_all(self, pages_per_site=5, sites=None):
        global scraping_status
        if sites is None:
            sites = {"99acres": True, "magicbricks": True, "housing": True}

        self.setup_driver()
        try:
            if sites.get("99acres", True) and scraping_status["running"]:
                self.scrape_99acres(max_pages=pages_per_site)
                self.random_delay(3, 5)
            if sites.get("magicbricks", True) and scraping_status["running"]:
                self.scrape_magicbricks(max_pages=pages_per_site)
                self.random_delay(3, 5)
            if sites.get("housing", True) and scraping_status["running"]:
                self.scrape_housing(max_pages=pages_per_site)
        finally:
            if self.driver:
                self.driver.quit()

    def get_results_df(self):
        df = pd.DataFrame(self.all_data)
        if len(df) > 0:
            df = df.drop_duplicates(subset=["Property", "Price"], keep="first")
            df["Sort_Preferred"] = df["Preferred Builder"].apply(
                lambda x: 0 if "⭐" in str(x) else 1
            )
            df = df.sort_values(
                by=["Sort_Preferred", "Price Numeric"], ascending=[True, True]
            )
            df = df.drop("Sort_Preferred", axis=1)
        return df


# Flask Routes
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/start_scraping", methods=["POST"])
def start_scraping():
    global scraping_status

    if scraping_status["running"]:
        return jsonify({"error": "Scraping already in progress"}), 400

    data = request.json
    pages = int(data.get("pages", 5))

    # Parse new filters
    budget_max = int(data.get("budget", 80)) * 100000  # Convert Lakhs to absolute
    bhk = data.get("bhk", ["2", "3"])  # list
    rera = data.get("rera", "Yes")
    preferred = data.get("preferred", False)

    # Parse website selections
    sites = data.get("sites", {"99acres": True, "magicbricks": True, "housing": True})

    if isinstance(bhk, str):
        bhk = bhk.split(",")

    scraping_status = {
        "running": True,
        "progress": 0,
        "message": "Starting scraper...",
        "current_site": "",
        "properties_found": 0,
        "results": None,
    }

    def scrape_thread():
        global scraping_status
        scraping_status["results"] = []  # Initialize for live updates
        try:
            finder = PropertyFinder(
                budget_max=budget_max, bhk=bhk, rera_req=rera, preferred_only=preferred
            )
            finder.scrape_all(pages_per_site=pages, sites=sites)

            # Final processing (sorting/deduplication)
            df = finder.get_results_df()
            scraping_status["results"] = df.to_dict("records")
            scraping_status["message"] = f"Completed! Found {len(df)} properties"
            scraping_status["progress"] = 100
        except Exception as e:
            scraping_status["message"] = f"Error: {str(e)}"
        finally:
            scraping_status["running"] = False

    Thread(target=scrape_thread).start()
    return jsonify({"status": "started"})


@app.route("/status")
def get_status():
    return jsonify(scraping_status)


@app.route("/stop_scraping", methods=["POST"])
def stop_scraping():
    global scraping_status
    if scraping_status["running"]:
        scraping_status["running"] = False
        scraping_status["message"] = "Search stopped by user"
        return jsonify({"status": "stopped"})
    return jsonify({"status": "not_running"})


@app.route("/results")
def view_results():
    if scraping_status["results"]:
        return render_template("results.html", properties=scraping_status["results"])
    return redirect(url_for("index"))


@app.route("/export")
def export_csv():
    if not scraping_status["results"]:
        return jsonify({"error": "No results to export"}), 400

    # Create CSV in memory
    si = io.StringIO()
    cw = csv.writer(si)

    # Get all keys from all results to ensure we have all columns
    if not scraping_status["results"]:
        return jsonify({"error": "No data"}), 400

    return send_file(
        output, mimetype="text/csv", as_attachment=True, download_name=filename
    )


@app.route("/import_csv", methods=["POST"])
def import_csv_route():
    global scraping_status
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    try:
        if file:
            # Parse CSV using pandas
            df = pd.read_csv(file)
            # Convert NaN to None/Empty string for JSON compatibility
            df = df.fillna("")

            # Update results
            results = df.to_dict("records")
            scraping_status["results"] = results
            scraping_status["properties_found"] = len(results)
            scraping_status["message"] = f"Imported {len(results)} properties from CSV"

            return jsonify(
                {"status": "success", "count": len(results), "results": results}
            )
    except Exception as e:
        return jsonify({"error": f"Failed to parse CSV: {str(e)}"}), 500

    # Write header
    cw.writerow(keys)

    # Write data
    for r in scraping_status["results"]:
        cw.writerow([r.get(k, "") for k in keys])

    output = io.BytesIO()
    output.write(si.getvalue().encode("utf-8-sig"))
    output.seek(0)

    filename = f"bangalore_properties_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return send_file(
        output, mimetype="text/csv", as_attachment=True, download_name=filename
    )


# HTML Templates
@app.route("/templates/index.html")
def get_index_template():
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bangalore Property Finder</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container {
            max-width: 800px;
            margin: 0 auto;
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            padding: 40px;
        }
        h1 {
            color: #333;
            text-align: center;
            margin-bottom: 10px;
            font-size: 32px;
        }
        .subtitle {
            text-align: center;
            color: #666;
            margin-bottom: 30px;
            font-size: 16px;
        }
        .criteria {
            background: #f8f9fa;
            padding: 20px;
            border-radius: 10px;
            margin-bottom: 30px;
        }
        .criteria h3 {
            color: #667eea;
            margin-bottom: 15px;
        }
        .criteria-item {
            display: flex;
            align-items: center;
            margin: 10px 0;
            color: #555;
        }
        .criteria-item span {
            margin-left: 10px;
        }
        .icon { color: #667eea; font-size: 20px; }
        .control-panel {
            background: white;
            padding: 20px;
            border-radius: 10px;
            border: 2px solid #e0e0e0;
            margin-bottom: 20px;
        }
        .input-group {
            margin-bottom: 20px;
        }
        .input-group label {
            display: block;
            margin-bottom: 8px;
            color: #333;
            font-weight: 600;
        }
        .input-group input {
            width: 100%;
            padding: 12px;
            border: 2px solid #e0e0e0;
            border-radius: 8px;
            font-size: 16px;
            transition: border-color 0.3s;
        }
        .input-group input:focus {
            outline: none;
            border-color: #667eea;
        }
        .btn {
            width: 100%;
            padding: 15px;
            font-size: 18px;
            font-weight: 600;
            border: none;
            border-radius: 10px;
            cursor: pointer;
            transition: all 0.3s;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        .btn-primary {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }
        .btn-primary:hover { transform: translateY(-2px); box-shadow: 0 10px 25px rgba(102, 126, 234, 0.4); }
        .btn-primary:disabled {
            background: #ccc;
            cursor: not-allowed;
            transform: none;
        }
        .status-panel {
            background: #f8f9fa;
            padding: 20px;
            border-radius: 10px;
            margin-top: 20px;
            display: none;
        }
        .status-panel.active { display: block; }
        .progress-bar {
            width: 100%;
            height: 30px;
            background: #e0e0e0;
            border-radius: 15px;
            overflow: hidden;
            margin: 15px 0;
        }
        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
            transition: width 0.5s;
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-weight: 600;
        }
        .status-message {
            text-align: center;
            color: #333;
            margin: 10px 0;
            font-size: 16px;
        }
        .stats {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 15px;
            margin-top: 20px;
        }
        .stat-card {
            background: white;
            padding: 15px;
            border-radius: 8px;
            text-align: center;
            border: 2px solid #667eea;
        }
        .stat-value {
            font-size: 28px;
            font-weight: bold;
            color: #667eea;
        }
        .stat-label {
            color: #666;
            font-size: 14px;
            margin-top: 5px;
        }
        .btn-secondary {
            background: #28a745;
            color: white;
            margin-top: 15px;
        }
        .btn-secondary:hover {
            background: #218838;
            transform: translateY(-2px);
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🏠 Bangalore Property Finder</h1>
        <p class="subtitle">Find your dream home with preferred builders</p>
        
        <div class="criteria">
            <h3>Search Criteria</h3>
            <div class="criteria-item">
                <span class="icon">🏢</span>
                <span><strong>Property Type:</strong> 2BHK & 3BHK Apartments</span>
            </div>
            <div class="criteria-item">
                <span class="icon">💰</span>
                <span><strong>Budget:</strong> Up to ₹80 Lakhs</span>
            </div>
            <div class="criteria-item">
                <span class="icon">📍</span>
                <span><strong>Location:</strong> Bangalore</span>
            </div>
            <div class="criteria-item">
                <span class="icon">✅</span>
                <span><strong>RERA:</strong> Verified Properties Only</span>
            </div>
            <div class="criteria-item">
                <span class="icon">⭐</span>
                <span><strong>Builders:</strong> Prestige, Sobha, Brigade, Puravankara, Godrej, Embassy, L&T, Salarpuria Sattva, Assetz</span>
            </div>
        </div>
        
        <div class="control-panel">
            <div class="input-group">
                <label for="pages">Pages to scrape per website:</label>
                <input type="number" id="pages" value="5" min="1" max="10">
            </div>
            <button class="btn btn-primary" onclick="startScraping()" id="startBtn">
                Start Property Search
            </button>
        </div>
        
        <div class="status-panel" id="statusPanel">
            <div class="progress-bar">
                <div class="progress-fill" id="progressBar" style="width: 0%">0%</div>
            </div>
            <div class="status-message" id="statusMessage">Initializing...</div>
            <div class="stats">
                <div class="stat-card">
                    <div class="stat-value" id="propertiesFound">0</div>
                    <div class="stat-label">Properties Found</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value" id="currentSite">-</div>
                    <div class="stat-label">Current Source</div>
                </div>
            </div>
            <button class="btn btn-secondary" onclick="viewResults()" id="viewResultsBtn" style="display: none;">
                View Results
            </button>
        </div>
    </div>
    
    <script>
        let statusInterval;
        
        function startScraping() {
            const pages = document.getElementById('pages').value;
            const startBtn = document.getElementById('startBtn');
            const statusPanel = document.getElementById('statusPanel');
            
            startBtn.disabled = true;
            startBtn.textContent = 'Scraping in progress...';
            statusPanel.classList.add('active');
            
            fetch('/start_scraping', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ pages: parseInt(pages) })
            })
            .then(response => response.json())
            .then(data => {
                statusInterval = setInterval(updateStatus, 1000);
            })
            .catch(error => {
                alert('Error starting scraper: ' + error);
                startBtn.disabled = false;
                startBtn.textContent = 'Start Property Search';
            });
        }
        
        function updateStatus() {
            fetch('/status')
            .then(response => response.json())
            .then(data => {
                document.getElementById('statusMessage').textContent = data.message;
                document.getElementById('propertiesFound').textContent = data.properties_found;
                document.getElementById('currentSite').textContent = data.current_site || '-';
                
                const progress = data.running ? 50 : (data.results ? 100 : 0);
                const progressBar = document.getElementById('progressBar');
                progressBar.style.width = progress + '%';
                progressBar.textContent = progress + '%';
                
                if (!data.running && data.results) {
                    clearInterval(statusInterval);
                    document.getElementById('viewResultsBtn').style.display = 'block';
                    document.getElementById('startBtn').disabled = false;
                    document.getElementById('startBtn').textContent = 'Start Property Search';
                }
            });
        }
        
        function viewResults() {
            window.location.href = '/results';
        }
    </script>
</body>
</html>"""


if __name__ == "__main__":
    # Create templates directory
    os.makedirs("templates", exist_ok=True)

    # Save index template
    with open("templates/index.html", "w") as f:
        f.write(
            """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bangalore Property Finder</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container {
            max-width: 800px;
            margin: 0 auto;
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            padding: 40px;
        }
        h1 {
            color: #333;
            text-align: center;
            margin-bottom: 10px;
            font-size: 32px;
        }
        .subtitle {
            text-align: center;
            color: #666;
            margin-bottom: 30px;
            font-size: 16px;
        }
        .control-panel {
            background: white;
            padding: 20px;
            border-radius: 10px;
            border: 2px solid #e0e0e0;
            margin-bottom: 20px;
        }
        .input-group {
            margin-bottom: 20px;
        }
        .input-group label {
            display: block;
            margin-bottom: 8px;
            color: #333;
            font-weight: 600;
        }
        .input-group input, .input-group select {
            width: 100%;
            padding: 12px;
            border: 2px solid #e0e0e0;
            border-radius: 8px;
            font-size: 16px;
            transition: border-color 0.3s;
        }
        .input-group input:focus, .input-group select:focus {
            outline: none;
            border-color: #667eea;
        }
        .slider-container {
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .slider-value {
            font-weight: bold;
            color: #667eea;
            width: 80px;
        }
        .btn {
            width: 100%;
            padding: 15px;
            font-size: 18px;
            font-weight: 600;
            color: white;
            background: #667eea;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            transition: background 0.3s;
        }
        .btn:hover {
            background: #556cd6;
        }
        .btn:disabled {
            background: #ccc;
            cursor: not-allowed;
        }
        .status-panel {
            background: #f8f9fa;
            padding: 20px;
            border-radius: 10px;
            margin-top: 20px;
            display: none;
        }
        .status-panel.active { display: block; }
        .progress-bar {
            width: 100%;
            height: 30px;
            background: #e0e0e0;
            border-radius: 15px;
            overflow: hidden;
            margin: 15px 0;
        }
        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
            transition: width 0.5s;
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-weight: 600;
        }
        .status-message {
            text-align: center;
            color: #333;
            margin: 10px 0;
            font-size: 16px;
        }
        .stats {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 15px;
            margin-top: 20px;
        }
        .stat-card {
            background: white;
            padding: 15px;
            border-radius: 8px;
            text-align: center;
            border: 2px solid #667eea;
        }
        .stat-value {
            font-size: 28px;
            font-weight: bold;
            color: #667eea;
        }
        .stat-label {
            color: #666;
            font-size: 14px;
            margin-top: 5px;
        }
        .btn-secondary {
            background: #28a745;
            color: white;
            margin-top: 15px;
        }
        .btn-secondary:hover {
            background: #218838;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🏠 Bangalore Property Finder</h1>
        <p class="subtitle">Customize your search criteria below</p>
        
        <div class="control-panel">
            <!-- Pages -->
            <div class="input-group">
                <label for="pages">Pages to scrape per website:</label>
                <input type="number" id="pages" value="5" min="1" max="10">
            </div>

            <!-- Property Type -->
            <div class="input-group">
                <label for="bhk">Property Type:</label>
                <select id="bhk">
                    <option value="2,3" selected>2 BHK & 3 BHK</option>
                    <option value="2">2 BHK Only</option>
                    <option value="3">3 BHK Only</option>
                </select>
            </div>

            <!-- Budget Slider -->
            <div class="input-group">
                <label for="budget">Max Budget (Lakhs):</label>
                <div class="slider-container">
                    <input type="range" id="budget" min="10" max="200" value="80" oninput="updateBudgetDisplay(this.value)">
                    <span class="slider-value" id="budgetValue">₹80 L</span>
                </div>
            </div>

            <!-- RERA Verified -->
            <div class="input-group">
                <label for="rera">RERA Verified:</label>
                <select id="rera">
                    <option value="Yes" selected>Yes (Verified Only)</option>
                    <option value="No">No / Any</option>
                </select>
            </div>

            <!-- Preferred Builder -->
            <div class="input-group">
                <label for="preferred">Preferred Builders Only:</label>
                <select id="preferred">
                    <option value="false" selected>No (Show All)</option>
                    <option value="true">Yes (Prestige, Sobha, etc.)</option>
                </select>
            </div>

            <button class="btn" onclick="startScraping()" id="startBtn">
                Start Property Search
            </button>
        </div>
        
        <div class="status-panel" id="statusPanel">
            <div class="progress-bar">
                <div class="progress-fill" id="progressBar" style="width: 0%">0%</div>
            </div>
            <div class="status-message" id="statusMessage">Initializing...</div>
            <div class="stats">
                <div class="stat-card">
                    <div class="stat-value" id="propertiesFound">0</div>
                    <div class="stat-label">Properties Found</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value" id="currentSite">-</div>
                    <div class="stat-label">Current Source</div>
                </div>
            </div>
            <button class="btn btn-secondary" onclick="viewResults()" id="viewResultsBtn" style="display: none;">
                View Results
            </button>
        </div>
    </div>
    
    <script>
        let statusInterval;

        function updateBudgetDisplay(val) {
            document.getElementById('budgetValue').textContent = '₹' + val + ' L';
        }
        
        function startScraping() {
            const pages = document.getElementById('pages').value;
            const bhk = document.getElementById('bhk').value;
            const budget = document.getElementById('budget').value;
            const rera = document.getElementById('rera').value;
            const preferred = document.getElementById('preferred').value === 'true';

            const startBtn = document.getElementById('startBtn');
            const statusPanel = document.getElementById('statusPanel');
            
            startBtn.disabled = true;
            startBtn.textContent = 'Scraping in progress...';
            statusPanel.classList.add('active');
            
            fetch('/start_scraping', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    pages: parseInt(pages),
                    bhk: bhk.split(','),
                    budget: parseInt(budget),
                    rera: rera,
                    preferred: preferred
                })
            })
            .then(response => response.json())
            .then(data => {
                statusInterval = setInterval(updateStatus, 1000);
            })
            .catch(error => {
                alert('Error starting scraper: ' + error);
                startBtn.disabled = false;
                startBtn.textContent = 'Start Property Search';
            });
        }
        
        function updateStatus() {
            fetch('/status')
            .then(response => response.json())
            .then(data => {
                document.getElementById('statusMessage').textContent = data.message;
                document.getElementById('propertiesFound').textContent = data.properties_found;
                document.getElementById('currentSite').textContent = data.current_site || '-';
                
                const progress = data.running ? 50 : (data.results ? 100 : 0);
                const progressBar = document.getElementById('progressBar');
                progressBar.style.width = progress + '%';
                progressBar.textContent = progress + '%';
                
                if (!data.running && data.results) {
                    clearInterval(statusInterval);
                    document.getElementById('viewResultsBtn').style.display = 'block';
                    document.getElementById('startBtn').disabled = false;
                    document.getElementById('startBtn').textContent = 'Start Property Search';
                }
            });
        }
        
        function viewResults() {
            window.location.href = '/results';
        }
    </script>
</body>
</html>"""
        )

    # Save results template
    with open("templates/results.html", "w") as f:
        f.write(
            """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bangalore Property Results</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #f4f7f6;
            min-height: 100vh;
            padding: 20px;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 30px;
            background: white;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
        }
        h1 { color: #333; }
        .btn {
            padding: 10px 20px;
            border: none;
            border-radius: 5px;
            cursor: pointer;
            font-weight: 600;
            text-decoration: none;
            display: inline-block;
        }
        .btn-primary {
            background: #667eea;
            color: white;
            margin-left: 10px;
        }
        .btn-secondary {
            background: #e0e0e0;
            color: #333;
        }
        .property-list {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(350px, 1fr));
            gap: 20px;
        }
        .property-card {
            background: white;
            border-radius: 10px;
            overflow: hidden;
            box-shadow: 0 5px 15px rgba(0,0,0,0.05);
            transition: transform 0.3s;
            border-top: 4px solid transparent;
        }
        .property-card:hover {
            transform: translateY(-5px);
            box-shadow: 0 15px 30px rgba(0,0,0,0.1);
        }
        .property-card.preferred {
            border-top-color: #ffd700;
        }
        .card-header {
            padding: 15px;
            border-bottom: 1px solid #eee;
            background: #fafafa;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .source-badge {
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: bold;
        }
        .source-99acres { background: #e3f2fd; color: #1565c0; }
        .source-magicbricks { background: #ffebee; color: #c62828; }
        .source-housing { background: #e8f5e9; color: #2e7d32; }
        
        .card-body {
            padding: 20px;
        }
        .property-title {
            font-size: 18px;
            font-weight: bold;
            margin-bottom: 5px;
            color: #333;
            line-height: 1.3;
        }
        .property-builder {
            color: #666;
            font-size: 14px;
            margin-bottom: 15px;
        }
        .preferred-tag {
            color: #f57f17;
            font-weight: bold;
            font-size: 12px;
            margin-left: 5px;
        }
        .price {
            font-size: 24px;
            font-weight: bold;
            color: #2e7d32;
            margin-bottom: 15px;
        }
        .details-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 10px;
            margin-bottom: 15px;
            font-size: 14px;
        }
        .detail-item {
            color: #555;
        }
        .detail-label {
            color: #999;
            display: block;
            font-size: 12px;
        }
        .view-btn {
            display: block;
            width: 100%;
            padding: 10px;
            text-align: center;
            background: #f8f9fa;
            color: #333;
            text-decoration: none;
            border-radius: 5px;
            font-weight: 600;
            border: 1px solid #ddd;
            transition: all 0.2s;
        }
        .view-btn:hover {
            background: #667eea;
            color: white;
            border-color: #667eea;
        }
        .empty-state {
            grid-column: 1 / -1;
            text-align: center;
            padding: 50px;
            background: white;
            border-radius: 10px;
            color: #666;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Property Search Results</h1>
            <div>
                <a href="/" class="btn btn-secondary">Back to Search</a>
                <a href="/export" class="btn btn-primary">Export CSV</a>
            </div>
        </div>
        
        <div class="property-list">
            {% if properties %}
                {% for prop in properties %}
                <div class="property-card {% if '⭐' in prop['Preferred Builder']|string %}preferred{% endif %}">
                    <div class="card-header">
                        <span class="source-badge source-{{ prop['Source'].lower().replace('.', '') }}">
                            {{ prop['Source'] }}
                        </span>
                        {% if '⭐' in prop['Preferred Builder']|string %}
                        <span class="preferred-tag">⭐ PREFERRED BUILDER</span>
                        {% endif %}
                    </div>
                    <div class="card-body">
                        <div class="property-title">
                            {{ prop['Property'] or prop['Project'] or 'Details Not Available' }}
                        </div>
                        <div class="property-builder">
                            {{ prop['Builder'] or 'Builder Not Specified' }}
                        </div>
                        
                        <div class="price">
                            {{ prop['Price'] or 'Price on Request' }}
                        </div>
                        
                        <div class="details-grid">
                            <div class="detail-item">
                                <span class="detail-label">Type</span>
                                {{ prop['BHK'] or '-' }} BHK
                            </div>
                            <div class="detail-item">
                                <span class="detail-label">Area</span>
                                {{ prop['Area in Sq.ft'] or '-' }}
                            </div>
                            <div class="detail-item">
                                <span class="detail-label">RERA</span>
                                {{ prop['RERA'] }}
                            </div>
                            <div class="detail-item">
                                <span class="detail-label">Nearby</span>
                                <span title="{{ prop['Places Nearby'] }}">
                                    {{ (prop['Places Nearby'] or '-')[:15] }}...
                                </span>
                            </div>
                        </div>
                        
                        <a href="{{ prop['Property URL'] }}" target="_blank" class="view-btn">
                            View Property
                        </a>
                    </div>
                </div>
                {% endfor %}
            {% else %}
                <div class="empty-state">
                    <h2>No properties found</h2>
                    <p>Try adjusting your search criteria or scraping more pages.</p>
                </div>
            {% endif %}
        </div>
    </div>
</body>
</html>"""
        )

    app.run(debug=True, port=5001)
