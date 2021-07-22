"""ThingsBoard HTTP API Device module"""
import threading
import logging
from datetime import datetime, timezone
import requests


class TBHTTPAPIException(Exception):
    """ThingsBoard HTTP Device API Exception class."""


class TBProvisionFailure(TBHTTPAPIException):
    """Exception raised if device provisioning failed."""


class TBHTTPClient:
    """ThingsBoard HTTP Device API class."""

    def __init__(self, host: str, token: str, name: str = None):
        self.session = requests.Session()
        self.session.headers.update({'Content-Type': 'application/json'})
        self.token = token
        self.name = name
        self.host = host
        self.api_base_url = f'{self.host}/api/v1/{self.token}'
        self.subscriptions = {
            'attributes': {
                'event': threading.Event()
            },
            'rpc': {
                'event': threading.Event()
            }
        }
        self.logger = logging.getLogger('TBHTTPDevice')
        self.log_level = 'INFO'
        self.logger.setLevel(getattr(logging, self.log_level))
        self.logger.warning('Log level set to %s', self.log_level)

    def __repr__(self):
        return f'<ThingsBoard ({self.host}) HTTP client {self.name}>'

    def connect(self):
        """Publish an empty telemetry data to ThingsBoard to test the connection."""
        self.publish_data({}, 'telemetry')

    def publish_data(self, data: dict, endpoint: str) -> dict:
        """Send POST data to ThingsBoard.

        :param data: The data dictionary to send.
        :param endpoint: The receiving API endpoint.
        """
        response = self.session.post(f'{self.api_base_url}/{endpoint}', json=data)
        response.raise_for_status()
        return response.json() if response.content else {}

    def get_data(self, params: dict, endpoint: str) -> dict:
        """Retrieve data with GET from ThingsBoard.

        :param params: A dictionary with the parameters for the request.
        :param endpoint: The receiving API endpoint.
        :return: A dictionary with the response from the ThingsBoard instance.
        """
        response = self.session.get(f'{self.api_base_url}/{endpoint}', params=params)
        response.raise_for_status()
        return response.json()

    def send_telemetry(self, telemetry: dict, timestamp: datetime = None):
        """Publish telemetry to ThingsBoard.

        :param telemetry: A dictionary with the telemetry data to send.
        :param timestamp: Timestamp to set for the values. If not set the ThingsBoard server uses
            the time of reception as timestamp.
        """
        if timestamp:
            # Convert timestamp to UTC milliseconds as required by API specification.
            payload = {
                'ts': int(timestamp.replace(tzinfo=timezone.utc).timestamp()*1000),
                'values': telemetry
            }
        else:
            payload = telemetry
        self.publish_data(payload, 'telemetry')

    def send_attributes(self, attributes: dict):
        """Send attributes to ThingsBoard.

        :param attributes: Attributes to send.
        """
        self.publish_data(attributes, 'attributes')

    def send_rpc(self, name: str, params: dict = None) -> dict:
        """Send RPC to ThingsBoard and return response.

        :param name: Name of the RPC method.
        :param params: Parameter for the RPC.
        :return: A dictionary with the response.
        """
        return self.publish_data({'method': name, 'params': params or {}}, 'rpc')

    def request_attributes(self, client_keys: list = None, shared_keys: list = None) -> dict:
        """Request attributes from ThingsBoard.

        :param client_keys: A list of keys for client attributes.
        :param shared_keys: A list of keys for shared attributes.
        :return: A dictionary with the request attributes.
        """
        params = {'client_keys': client_keys, 'shared_keys': shared_keys}
        return self.get_data(params=params, endpoint='attributes')

    def subscribe_to_attributes(self, callback, timeout: int = None):
        """Subscribe to shared attributes updates.

        :param callback: A callback tacking one argument (dict) that is called for each received
            shared attribute update.
        :param timeout: Connection timeout. If not set, the subscription is not limited in time.
        """
        params = {'timeout': timeout} if timeout else {}

        def subscription():
            self.subscriptions['attributes']['event'].clear()
            self.logger.info('Start subscription to attribute updates.')
            while True:
                response = self.session.get(url=f'{self.api_base_url}/attributes/updates',
                                            params=params)
                if self.subscriptions['attributes']['event'].is_set():
                    break
                if response.status_code == 408 and timeout:
                    break
                if response.status_code == 504:  # Gateway Timeout
                    continue  # Reconnect
                response.raise_for_status()
                callback(response.json())
            self.subscriptions['attributes']['event'].clear()
            self.logger.info('Stop subscription to attribute updates.')

        self.subscriptions['attributes']['thread'] = threading.Thread(
            name='subscribe_attributes',
            target=subscription,
            daemon=True)
        self.subscriptions['attributes']['thread'].start()

    def unsubscribe_from_attributes(self):
        """Unsubscribe from shared attributes updates."""
        self.logger.debug('Set stop event for attributes subscription.')
        self.subscriptions['attributes']['event'].set()

    def subscribe_to_rpc(self, callback, timeout: int = None):
        """Subscribe to RPC.

        :param callback: A callback tacking one argument (dict) that is called for each received
            RPC event.
        :param timeout: Connection timeout. If not set, the subscription is not limited in time.
        """
        params = {'timeout': timeout} if timeout else {}

        def subscription():
            self.subscriptions['rpc']['event'].clear()
            self.logger.info('Start subscription to RPCs.')
            while True:
                response = self.session.get(url=f'{self.api_base_url}/rpc',
                                            params=params)
                if self.subscriptions['rpc']['event'].is_set():
                    break
                if response.status_code == 408 and timeout:
                    break
                if response.status_code == 504:  # Gateway Timeout
                    continue  # Reconnect
                response.raise_for_status()
                callback(response.json())
            self.subscriptions['rpc']['event'].clear()
            self.logger.info('Stop subscription to attribute updates.')

        self.subscriptions['rpc']['thread'] = threading.Thread(
            name='subscribe_rpc',
            target=subscription,
            daemon=True)
        self.subscriptions['rpc']['thread'].start()

    def unsubscribe_from_rpc(self):
        """Unsubscribe from RPC."""
        self.logger.debug('Set stop event for RPC subscription.')
        self.subscriptions['rpc']['event'].set()

    @classmethod
    def provision(cls, host: str, device_name: str, device_key: str, device_secret: str):
        """Initiate device provisioning and return a client instance.

        :param host: The root URL to the ThingsBoard instance.
        :param device_name: Name of the device to provision.
        :param device_key: Provisioning device key from ThingsBoard.
        :param device_secret: Provisioning secret from ThingsBoard.
        :return: Instance of :class:`TBHTTPClient`
        """
        data = {
            'deviceName': device_name,
            'provisionDeviceKey': device_key,
            'provisionDeviceSecret': device_secret
        }
        response = requests.post(f'{host}/api/v1/provision', json=data)
        response.raise_for_status()
        device = response.json()
        if device['status'] == 'SUCCESS' and device['credentialsType'] == 'ACCESS_TOKEN':
            return cls(host=host, token=device['credentialsValue'], name=device_name)
        raise TBProvisionFailure(device)
