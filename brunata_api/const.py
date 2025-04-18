"""Constants used by the Brunata API."""

from enum import Enum

from fake_useragent import UserAgent

ua_fetcher = UserAgent(
    browsers=["Edge"],
    os=["Windows"],
    platforms=["desktop"],
    min_version=130.0,
)
ua = ua_fetcher.random
ua_sec = '"Not/A)Brand";v="8", "Chromium";v="{0}", "Microsoft Edge";v="{0}"'.format(
    ua.split("Chrome/")[1].split(".")[0],
)
"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)\
 Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0"
# API Constants
BASE_URL = "https://online.brunata.com"
OAUTH2_PROFILE = "B2C_1_signin_username"
AUTHN_URL = f"https://brunatab2cprod.b2clogin.com/brunatab2cprod.onmicrosoft.com/{OAUTH2_PROFILE}"
API_URL = f"{BASE_URL}/online-webservice/v1/rest"

OAUTH2_URL = f"{AUTHN_URL}/oauth2/v2.0"
CLIENT_ID = "e1d10965-78dc-4051-a1e5-251483e74d03"
REDIRECT = f"{BASE_URL}/auth-response"

METERS_URL = f"{BASE_URL}/react-online/meters-values"
CONSUMPTION_URL = f"{BASE_URL}/react-online/consumption-overview"

# Default headers
HEADERS = {
    "User-Agent": ua,
    "Sec-Ch-Ua": ua_sec,
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "en",
    "Connection": "keep-alive",
}


class Consumption(Enum):
    """Enum for the different types of consumption."""

    HEATING = 1
    WATER = 2
    ELECTRICITY = 3


class Interval(Enum):
    """Enum for the different types of intervals."""

    DAY = "D"
    MONTH = "M"
