"""Brunata Online API Client."""
from __future__ import annotations

import re
import urllib.parse
from base64 import urlsafe_b64encode
from datetime import datetime, timedelta
from hashlib import sha256
from logging import Logger, getLogger
from os import urandom

import httpx

from .const import (
    API_URL,
    AUTHN_URL,
    CLIENT_ID,
    CONSUMPTION_URL,
    HEADERS,
    METERS_URL,
    OAUTH2_PROFILE,
    OAUTH2_URL,
    REDIRECT,
    Consumption,
    Interval,
)
from .url_formatter import RedactQueryParams

_LOGGER: Logger = getLogger(__package__)
TIMEOUT = 10


def start_of_interval(interval: Interval, offset: timedelta | None) -> str:
    """Return start of year if interval is "M", otherwise start of month."""
    date = datetime.now()
    if offset is not None:
        date += offset
    if interval is Interval.MONTH:
        date = date.replace(month=1)
    date = date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return f"{date.isoformat()}.000Z"


def end_of_interval(interval: Interval, offset: timedelta | None) -> str:
    """Return end of year if interval is "M", otherwise end of month."""
    date = datetime.now()
    if offset is not None:
        date += offset
    if interval is Interval.MONTH:
        date = date.replace(month=12)
    date = date.replace(day=28) + timedelta(days=4)
    date -= timedelta(days=date.day)
    date = date.replace(hour=23, minute=59, second=59, microsecond=0)
    return f"{date.isoformat()}.999Z"


class Client:
    """Brunata Online API Client."""

    def __init__(self, username: str, password: str) -> None:
        """Initialize the client."""
        self._session = httpx.AsyncClient(
            timeout=httpx.Timeout(TIMEOUT), headers=HEADERS,
        )
        getLogger("httpx").handlers[0].setFormatter(RedactQueryParams()) \
            if getLogger("httpx").handlers else None
        self._username = username
        self._password = password
        self._power = {}
        self._water = {}
        self._heating = {}
        self._tokens = {}
        self._meters = {}
        self.meter_types_map = {}
        self.meter_units_map = {}
        self.allocation_unit_map = {}

    def _is_token_valid(self, token: str) -> bool:
        """Check if token is valid."""
        if not self._tokens:
            return False
        match token:
            case "access_token":
                ts = self._tokens.get("expires_on")
                if datetime.fromtimestamp(ts) < datetime.now():
                    return False
            case "refresh":
                ts = self._tokens.get("refresh_token_expires_on")
                if datetime.fromtimestamp(ts) < datetime.now():
                    return False
        return True

    async def _renew_tokens(self) -> dict:
        """Renew access token using refresh token."""
        if self._is_token_valid("access_token"):
            _LOGGER.debug(
                "Token is not expired, expires in %d seconds",
                self._tokens.get("expires_on") - int(datetime.now().timestamp()),
            )
            return self._tokens
        # Get OAuth 2.0 token object
        try:
            tokens = await self.api_wrapper(
                method="POST",
                url=f"{OAUTH2_URL}/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self._tokens.get("refresh_token"),
                    "CLIENT_ID": CLIENT_ID,
                },
            )
        except Exception:
            _LOGGER.exception("An error occurred while trying to renew tokens")
            return {}
        return await tokens.json()

    async def _b2c_auth(self) -> dict:
        """Authenticate using Azure AD B2C."""
        # Initialize challenge values
        code_verifier = urlsafe_b64encode(urandom(40)).decode("utf-8")
        code_verifier = re.sub("[^a-zA-Z0-9]+", "", code_verifier)
        code_challenge = sha256(code_verifier.encode("utf-8")).digest()
        code_challenge = (
            urlsafe_b64encode(code_challenge).decode("utf-8").replace("=", "")
        )
        # Initial authorization call
        req_code = await self.api_wrapper(
            method="GET",
            url=f"{API_URL.replace('webservice', 'auth-webservice')}/authorize",
            params={
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT,
                "scope": f"{CLIENT_ID} offline_access",
                "response_type": "code",
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            },
            follow_redirects=True,
        )
        # Get CSRF Token & Transaction ID
        try:
            csrf_token = str(req_code.cookies.get("x-ms-cpim-csrf"))
        except KeyError:
            _LOGGER.exception("Error while retrieving CSRF Token")
            return {}
        match = re.search(r"var SETTINGS = (\{[^;]*\});", req_code.text)
        if match:  # Use a little magic to avoid proper JSON parsing ✨
            _LOGGER.debug("Match found :3")
            transaction_id = next(
                i for i in match.group(1).split('","') if i.startswith("transId")
            )[10:]
            _LOGGER.debug("Transaction ID: %s", transaction_id)
        else:
            _LOGGER.error("Failed to get Transaction ID")
            return {}
        # Post credentials to B2C Endpoint
        req_auth = await self.api_wrapper(
            method="POST",
            url=f"{AUTHN_URL}/SelfAsserted",
            params={
                "tx": transaction_id,
                "p": OAUTH2_PROFILE,
            },
            data={
                "request_type": "RESPONSE",
                "logonIdentifier": self._username,
                "password": self._password,
            },
            headers={
                "Referer": str(req_code.url),
                "X-Csrf-Token": csrf_token,
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        # Get authentication code
        req_auth = await self.api_wrapper(
            method="GET",
            url=f"{AUTHN_URL}/api/CombinedSigninAndSignup/confirmed",
            params={
                "rememberMe": str(False),
                "csrf_token": csrf_token,
                "tx": transaction_id,
                "p": OAUTH2_PROFILE,
            },
        )
        redirect = req_auth.headers["Location"]
        if not redirect.startswith(REDIRECT):
            _err = "Redirect URL does not start with the expected REDIRECT value"
            raise ValueError(_err)
        _LOGGER.debug("%d - %s", req_auth.status_code, redirect)
        try:
            auth_code = urllib.parse.parse_qs(urllib.parse.urlparse(redirect).query)[
                "code"
            ][0]
        except KeyError:
            _LOGGER.exception(
                "An error has occurred while attempting to authenticate."
                "\nPlease ensure your credentials are correct",
            )
            return {}
        # Get OAuth 2.0 token object
        tokens = await self.api_wrapper(
            method="POST",
            url=f"{OAUTH2_URL}/token",
            data={
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT,
                "code": auth_code,
                "code_verifier": code_verifier,
            },
        )
        return tokens.json()

    async def _get_tokens(self) -> bool:
        """
        Get access/refresh tokens using credentials or refresh token.

        Returns True if tokens are valid.
        """
        # Check values
        if self._is_token_valid("refresh_token"):
            tokens = await self._renew_tokens()
        else:
            tokens = await self._b2c_auth()
        # Ensure validity of tokens
        if tokens.get("access_token"):
            # Add access token to session headers
            self._session.headers.update(
                {
                    "Authorization": f"{tokens.get('token_type')} {tokens.get('access_token')}",
                },
            )
            # Calculate refresh expiry
            if tokens.get("refresh_token") != self._tokens.get("refresh_token"):
                tokens.update(
                    {
                        "refresh_token_expires_on": int(datetime.now().timestamp())
                        + tokens.get("refresh_token_expires_in"),
                    },
                )
            self._tokens.update(tokens)
        else:
            self._tokens = {}
            _LOGGER.error("Failed to get tokens")
        return bool(self._tokens)

    async def _init_mappers(self, locale: str = "en") -> bool:
        """
        Will initialize the 'mappers' the API provides for localizing information.

        If they were already initialized, this method will not fetch the resource again.
        """
        if not await self._get_tokens():
            return False
        if len(self.meter_types_map):
            return True
        result = (
            await self.api_wrapper(
                method="GET",
                url=f"{API_URL}/locales/{locale}/common",
                headers={
                    "Referer": METERS_URL,
                },
            )
        ).json()
        self.meter_types_map = { str(k): v for (k, v) in enumerate(result["mappers"]["meterType"]) }
        self.meter_units_map = {
            str(k): v for (k, v) in enumerate(result["mappers"]["measurementUnit"])
        }
        self.allocation_unit_map = result["mappers"]["allocationUnitMap"]
        return True

    async def update_meters(self) -> bool | None:
        """
        Call the meters API to initialize or update the meters associated to this account.

        Will return True when new meters are added, or new readings are available on existing meters
        """
        if not await self._get_tokens():
            return None
        if not await self._init_mappers():
            return None
        date = datetime.now()
        date = date.replace(hour=0, minute=0, second=0, microsecond=0)
        result = (
            await self.api_wrapper(
                method="GET",
                url=f"{API_URL}/consumer/meters",
                params={
                    "startdate": f"{date.isoformat()}.000Z",
                },
                headers={
                    "Referer": METERS_URL,
                },
            )
        ).json()

        updated = False
        for item in result:
            json_meter = item["meter"]
            # Each normal meter has a "shadow meter" without readings, but with the same meterNo.
            # The meterType is "Pulse Collector" and superAllocationUnit is null.
            # Since these don't do anything, we filter them out here.
            if json_meter["superAllocationUnit"] is None:
                continue
            json_reading = item["reading"]
            meter_id = str(json_meter["meterId"])

            meter = self._meters.get(meter_id)
            if meter is None:
                meter = Meter(self, json_meter)
                self._meters[meter_id] = meter
                _LOGGER.debug("New meter found: %s", meter)
            updated |= meter.add_reading(json_reading)
        return updated

    async def get_meters(self) -> object:
        """
        Return all available meters for this account.

        Calls update_meters() first to initialize/update the meters.
        """
        if not await self.update_meters():
            return None
        return self._meters.values()

    async def fetch_meters(self) -> None:
        """Get all meters associated with the account."""
        if not await self._get_tokens():
            return
        meters = (
            await self.api_wrapper(
                method="GET",
                url=f"{API_URL}/consumer/superallocationunits",
                headers={
                    "Referer": CONSUMPTION_URL,
                },
            )
        ).json()
        water_units = []
        heating_units = []
        power_units = []
        for meter in meters:
            match meter.get("superAllocationUnit"):
                case 1:  # Heating
                    heating_units += meter.get("allocationUnits")
                case 2:  # Water
                    water_units += meter.get("allocationUnits")
                case 3:  # Electricity
                    power_units += meter.get("allocationUnits")
        _LOGGER.info("Meter info: %s", str(meters))
        init = {"Meters": {"Day": {}, "Month": {}}}
        if heating_units:
            _LOGGER.debug("🔥 Heating meter(s) found")
            self._heating.update(dict(init))
            self._heating.update({"Units": heating_units})
        if water_units:
            _LOGGER.debug("💧 Water meter(s) found")
            self._water.update(dict(init))
            self._water.update({"Units": water_units})
        if power_units:
            _LOGGER.debug("⚡ Energy meter(s) found")
            self._power.update(dict(init))
            self._power.update({"Units": power_units})

    async def fetch_consumption(self, _type: Consumption, interval: Interval) -> None:
        """Get consumption data for a specific meter type."""
        if not await self._get_tokens():
            return
        match _type:
            case Consumption.ELECTRICITY:
                usage = self._power
            case Consumption.WATER:
                usage = self._water
            case Consumption.HEATING:
                usage = self._heating
        if not usage:
            _LOGGER.debug("No %s meter was found", _type.name.lower())
            return
        consumption = [
            (
                await self.api_wrapper(
                    method="GET",
                    url=f"{API_URL}/consumer/consumption",
                    params={
                        "startdate": start_of_interval(
                            interval, offset=timedelta(seconds=0),
                        ),
                        "enddate": end_of_interval(
                            interval, offset=timedelta(seconds=0),
                        ),
                        "interval": interval.value,
                        "allocationunit": unit,
                    },
                    headers={
                        "Referer": f"{CONSUMPTION_URL}/{_type.name.lower()}",
                    },
                )
            ).json()
            for unit in usage["Units"]
        ]
        _LOGGER.debug("Consumption data: %s", str(consumption))
        # Add all metrics that are not None
        usage["Meters"][interval.name.capitalize()].update(
            {
                meter.get("meter").get("meterId") or index: {
                    "Name": meter.get("meter").get("placement") or index,
                    "Values": {
                        entry.get("fromDate")[
                            : 10 if interval is Interval.DAY else 7
                        ]: entry.get("consumption")
                        for entry in meter["consumptionValues"]
                        if entry.get("consumption") is not None
                    },
                }
                for lines in consumption
                for index, meter in enumerate(lines["consumptionLines"])
            },
        )

    def get_consumption(self) -> dict:
        """Return consumption data."""
        return {
            "Heating": self._heating,
            "Water": self._water,
            "Electricity": self._power,
        }

    async def api_wrapper(self, **args: dict[str, any]) -> any:
        """HTTP request wrapper."""
        try:
            response = await self._session.request(**args)
            if not response.has_redirect_location:
                response.raise_for_status()
        except TimeoutError:
            _LOGGER.exception(
                "Timeout error fetching information from %s",
                args["url"],
            )
        except httpx.RequestError:
            _LOGGER.exception(
                "Error fetching information from %s",
                args["url"],
            )
        except (KeyError, TypeError):
            _LOGGER.exception(
                "Error parsing information from %s",
                args["url"],
            )
        except Exception:
            _LOGGER.exception("Something really wrong happened!")
        return response


class Reading:
    """
    Represents a reading by a meter (a single measurement in time).

    Note that the API has a field for a reading ID, but this seems to be None all the time.
    """

    def __init__(self,
                 meter: Meter,
                 _id: str | None,
                 reading_value: float,
                 reading_date: datetime,
                ) -> None:
        """Initialize the reading."""
        self._meter = meter
        self.id = _id
        self.value = reading_value
        self.date = reading_date

    def __str__(self) -> str:  # noqa: D105
        return f"{self.value}{self._meter.meter_unit} @{self.date}"

    def to_json(self) -> dict[str, any]:
        """Return a JSON representation of this reading."""
        return { "id": self.id, "value": self.value, "date": self.date.isoformat() }

class Meter:
    """Represents a single meter associated to an account."""

    def __init__(self, api: Client, json_meter: dict[str, str]) -> None:
        """Initialize the meter."""
        self._api = api
        self._meter_id = str(json_meter["meterId"])
        self._meter_no = str(json_meter["meterNo"])
        self._meter_type_id = str(json_meter["meterType"])
        self._meter_unit_id = str(json_meter["unit"])
        self._allocation_unit_id = str(json_meter["allocationUnit"])

        self.meter_type = api.meter_types_map[self._meter_type_id]
        self.meter_unit = api.meter_units_map[self._meter_unit_id]
        self.allocation_unit = api.allocation_unit_map[self._allocation_unit_id]
        self._readings: dict[datetime, Reading] = {}
        self.latest_reading: Reading | None = None

    def add_reading(self, reading_json: dict[str, str]) -> bool:
        """
        Add a reading if timestamp doesn't overlap, then returns True.

        `latest_reading` will be updated if the new reading is more recent.
        Will return False if a reading with the same timestamp already exists.
        """
        if reading_json["readingDate"] is None:
            return False
        value = float(reading_json["value"]) if reading_json["value"] else None
        date = datetime.fromisoformat(reading_json["readingDate"])
        rid = reading_json["readingId"]
        if date in self._readings:
            return False
        reading = Reading(self, rid, value, date)
        self._readings[date] = reading
        if self.latest_reading is None or self.latest_reading.date < date:
            self.latest_reading = reading
        _LOGGER.debug("Updated %s with new reading", self)
        return True

    def get_readings(self) -> list[Reading]:
        """
        Return a list of currently known readings by this meter, sorted by date.

        This will not fetch new readings.
        """
        return sorted(self._readings.values(), key=lambda r: r.date)

    def to_json(self) -> dict[str, any]:
        """Return a JSON representation of this meter."""
        return { "meter_id": self._meter_id,
                 "meter_no": self._meter_no,
                 "meter_type_id": self._meter_type_id,
                 "meter_unit_id": self._meter_unit_id,
                 "allocation_unit_id": self._allocation_unit_id,
                 "meter_type": self.meter_type,
                 "meter_unit": self.meter_unit,
                 "allocation_unit": self.allocation_unit,
                 "latest_reading": self.latest_reading.to_json() if self.latest_reading else None,
                 "readings": [reading.to_json() for reading in self.get_readings()],
             }

    def __str__(self) -> str:  # noqa: D105
        return (f"Meter({self.allocation_unit} ({self.meter_type})"
                f" - latest_reading: {self.latest_reading})")
