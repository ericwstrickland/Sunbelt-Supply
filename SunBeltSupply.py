from flask import Flask, render_template, request, jsonify
import logging
import os
import asyncio
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
import aiohttp
import json
from typing import List

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

SUPPLIER_WEBSITES = [
    "https://pixustechnologies.com/",
    "https://www.champion-inc.com.tw/",
    "https://cs-electronics.com/",
    "https://www.mcmaster.com/",
    "https://www.heilind.com/",
    "https://www.fruth.com/",
    "https://www.brightonbest.com/",
    "https://www.fascomp.com/",
    "https://duraswiss.com/",
    "http://molkenbuhr.com/",
    "https://www.jmcproducts.com/"
]

class PerplexityAPI:
    def __init__(self):
        self.api_key = "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"
        self.base_url = "https://api.perplexity.ai/chat/completions"

    async def search_part(self, part_number: str, quantity: int = 1) -> Dict[str, Any]:
        try:
            websites_str = "\n".join(SUPPLIER_WEBSITES)
            prompt = f"""You are a web scraping assistant. Search for electronic component part number {part_number} on these websites and return ONLY a JSON response:
            {websites_str}

            Rules:
            1. Return ONLY valid JSON, no explanations or other text
            2. Use this exact format for each website where you find the part:
            {{
                "website_name": {{
                    "price": (numeric price),
                    "stock": (numeric stock),
                    "total_available": (numeric total),
                    "manufacturer": (manufacturer name),
                    "description": (part description),
                    "url": (direct URL to part),
                    "price_breaks": [
                        {{"quantity": number, "price": number}}
                    ]
                }}
            }}
            3. If you can't find the part on a website, don't include that website in the response
            4. If you find the part but can't find some information, use null for those fields
            5. Return an empty JSON object {{}} if you can't find the part anywhere"""

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.base_url,
                    headers=headers,
                    json={
                        "model": "llama-3.1-sonar-large-128k-online",
                        "messages": [{"role": "user", "content": prompt}]
                    }
                ) as response:
                    data = await response.json()
                    logger.debug(f"Perplexity raw response: {data}")
                    
                    if 'error' in data:
                        logger.error(f"Perplexity API error: {data['error']}")
                        return {}
                    
                    try:
                        content = data['choices'][0]['message']['content']
                        logger.debug(f"Perplexity content: {content}")
                        
                        # Look for JSON content within the response
                        if '```json' in content:
                            json_str = content.split('```json')[1].split('```')[0].strip()
                        else:
                            json_str = content.strip()
                            
                        logger.debug(f"Attempting to parse JSON: {json_str}")
                        
                        results = json.loads(json_str)
                        return results
                    except (json.JSONDecodeError, KeyError) as e:
                        logger.error(f"Error parsing Perplexity response: {str(e)}")
                        logger.error(f"Failed content: {content}")
                        return {}

        except Exception as e:
            logger.error(f"Error searching Perplexity: {str(e)}")
            return {}

class PartSearchAggregator:
    def __init__(self):
        self.nexar_api = NexarAPI()
        self.perplexity_api = PerplexityAPI()

    async def search_part(self, part_number: str, quantity: int = 1) -> dict:
        # First try Nexar
        nexar_results = await self.nexar_api.search_part(part_number, quantity)
        
        # If Nexar returns no results, try Perplexity
        if not nexar_results or (isinstance(nexar_results, dict) and not any(key for key in nexar_results if not key == 'error')):
            perplexity_results = await self.perplexity_api.search_part(part_number, quantity)
            return perplexity_results

        return nexar_results

class NexarAPI:
    def __init__(self):
        self.base_url = "https://api.nexar.com/graphql"
        self.client_id = "bdab729c-a588-4e9b-b8d8-937e3ba2473c"
        self.client_secret = "NxI80IT71uYqxVKAD6unDuqVLo16BVduuK4M"
        self._token = None
        self._token_expiry = None

    async def _get_token(self):
        if self._token and self._token_expiry and datetime.now() < self._token_expiry:
            return self._token

        auth_url = "https://identity.nexar.com/connect/token"
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(auth_url, data=data) as response:
                token_data = await response.json()
                self._token = token_data["access_token"]
                self._token_expiry = datetime.now() + timedelta(seconds=token_data["expires_in"] - 60)
                return self._token

    async def search_part(self, part_number: str, quantity: int = 1) -> Dict[str, Any]:
        try:
            query = """
            query PartSearch($q: String!, $country: String!) {
                supSearchMpn(q: $q, country: $country, limit: 10) {
                    hits
                    results {
                        description
                        part {
                            mpn
                            totalAvail
                            manufacturer {
                                name
                            }
                            sellers {
                                company {
                                    name
                                }
                                offers {
                                    inventoryLevel
                                    prices {
                                        quantity
                                        price
                                    }
                                    clickUrl
                                }
                            }
                        }
                    }
                }
            }
            """
            
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {await self._get_token()}"
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.base_url,
                    headers=headers,
                    json={
                        "query": query,
                        "variables": {
                            "q": part_number,
                            "country": "US"
                        }
                    }
                ) as response:
                    data = await response.json()
                    results = {}

                    if "data" in data and data["data"]["supSearchMpn"]["results"]:
                        for result in data["data"]["supSearchMpn"]["results"]:
                            part = result["part"]
                            for seller in part["sellers"]:
                                company = seller["company"]["name"]
                                for offer in seller["offers"]:
                                    # Find the best price for the requested quantity
                                    best_price = None
                                    for price_break in offer["prices"]:
                                        if price_break["quantity"] <= quantity:
                                            if best_price is None or price_break["price"] < best_price:
                                                best_price = price_break["price"]
                                    
                                    results[company.lower()] = {
                                        "price": best_price,
                                        "stock": offer["inventoryLevel"],
                                        "total_available": part["totalAvail"],
                                        "manufacturer": part["manufacturer"]["name"],
                                        "description": result["description"],
                                        "url": offer["clickUrl"],
                                        "price_breaks": offer["prices"]
                                    }

                    return results

        except Exception as e:
            logger.error(f"Error searching Nexar: {str(e)}")
            return {
                "error": f"API Error: {str(e)}",
                "price": None,
                "stock": None,
                "lead_time": None,
                "url": None
            }

app = Flask(__name__)

@app.route('/api/search', methods=['POST'])
async def search():
    part_number = request.json.get('part_number')
    quantity = request.json.get('quantity', 1)
    logger.debug(f"Received search request for part: {part_number}, quantity: {quantity}")
    
    aggregator = PartSearchAggregator()
    results = await aggregator.search_part(part_number, quantity)
    
    logger.debug(f"Returning results: {results}")
    return jsonify(results)

@app.route('/')
def home():
    return render_template('index.html')

if __name__ == '__main__':
    app.run(debug=True)
