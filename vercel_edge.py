import os
import json
from urllib.parse import urlparse

edge_config_id_prefix = "ecfg_"


class EdgeConfigError(Exception):
    pass


class MockEdgeConfig:
    def __init__(self):
        self.items = {}

    def get(self, key):
        return self.items.get(key)

    def has(self, key):
        return key in self.items

    def __getitem__(self, key):
        return self.get(key)


class EdgeConfig:

    def __init__(self, input):
        self.is_mock = os.environ.get("VERCEL_ENV") != "production"
        if self.is_mock:
            self.config = MockEdgeConfig()
            self.id = "mock_config"
        else:
            self.id = self.parse_config_id(input)
            try:
                with open(f"/opt/edge-config/{self.id}.json", "r") as file:
                    self.config = json.load(file)
            except FileNotFoundError:
                raise EdgeConfigError("embeddedConfigNotFound")

    @property
    def digest(self):
        return getattr(self.config, "digest", None)

    @property
    def items(self):
        return self.config.items if isinstance(self.config, MockEdgeConfig) else self.config["items"]

    def get(self, key):
        return self.items.get(key)

    def has(self, key):
        return key in self.items

    def __getitem__(self, key):
        return self.get(key)

    @staticmethod
    def parse_config_id(input):
        if input.startswith(edge_config_id_prefix):
            return input
        if input.startswith("https://"):
            url = urlparse(input)
            path_components = url.path.split("/")
            for component in path_components:
                if component.startswith(edge_config_id_prefix):
                    return component
            raise EdgeConfigError("invalidConnection")
        value = os.environ.get(input)
        if value:
            return EdgeConfig.parse_config_id(value)
        raise EdgeConfigError("invalidConnection")
