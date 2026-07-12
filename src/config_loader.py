import yaml
import os
import logging

class ConfigLoader:
    _instance = None
    _config = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = ConfigLoader()
        return cls._instance

    def __init__(self):
        # Load .env file if it exists (for local development)
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        try:
            from dotenv import load_dotenv
            env_path = os.path.join(base_dir, '.env')
            load_dotenv(env_path)
            logging.info(f"Loaded environment variables from {env_path}")
        except ImportError:
            logging.warning("python-dotenv not installed, skipping .env loading")



    def load_config(self, config_path="config.yaml"):
        """Loads configuration from a YAML file."""
        if not os.path.isabs(config_path):
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            config_path = os.path.join(base_dir, config_path)
            
        try:
            with open(config_path, 'r', encoding='utf-8') as file:
                self._config = yaml.safe_load(file)
            
            # Override with environment variables
            self._override_from_env()

            logging.info(f"Configuration loaded from {config_path}")
        except Exception as e:
            logging.error(f"Failed to load configuration: {e}")
            raise

    def _override_from_env(self):
        """Overrides configuration with environment variables if they exist."""
        # LLM Settings
        if os.environ.get("INBRIEF_LLM_API_KEY"):
            if "llm" not in self._config: self._config["llm"] = {}
            self._config["llm"]["api_key"] = os.environ.get("INBRIEF_LLM_API_KEY")
        
        if os.environ.get("INBRIEF_LLM_BASE_URL"):
            if "llm" not in self._config: self._config["llm"] = {}
            self._config["llm"]["base_url"] = os.environ.get("INBRIEF_LLM_BASE_URL")

        # Auth Settings
        if os.environ.get("INBRIEF_JWT_SECRET"):
            if "auth" not in self._config: self._config["auth"] = {}
            self._config["auth"]["jwt_secret_key"] = os.environ.get("INBRIEF_JWT_SECRET")

        # Supabase Settings
        if os.environ.get("INBRIEF_SUPABASE_DB_URL"):
            if "supabase" not in self._config: self._config["supabase"] = {}
            self._config["supabase"]["db_url"] = os.environ.get("INBRIEF_SUPABASE_DB_URL")
            
        # Billing Settings (Creem) — secrets only from env
        if os.environ.get("INBRIEF_CREEM_API_KEY"):
            self._config.setdefault("billing", {}).setdefault("creem", {})
            self._config["billing"]["creem"]["api_key"] = os.environ.get("INBRIEF_CREEM_API_KEY")
        if os.environ.get("INBRIEF_CREEM_WEBHOOK_SECRET"):
            self._config.setdefault("billing", {}).setdefault("creem", {})
            self._config["billing"]["creem"]["webhook_secret"] = os.environ.get("INBRIEF_CREEM_WEBHOOK_SECRET")

        # Email Settings (password only from env, sender_email from config.yaml)
        if os.environ.get("INBRIEF_EMAIL_PASSWORD"):
            if "email" not in self._config: self._config["email"] = {}
            self._config["email"]["sender_password"] = os.environ.get("INBRIEF_EMAIL_PASSWORD")

        # Fetching Settings
        if os.environ.get("INBRIEF_YOUTUBE_API_KEY"):
            if "fetching" not in self._config: self._config["fetching"] = {}
            self._config["fetching"]["youtube_api_key"] = os.environ.get("INBRIEF_YOUTUBE_API_KEY")

    def get(self, key, default=None):
        """Retrieves a configuration value by key."""
        if self._config is None:
            self.load_config()
        return self._config.get(key, default)

    @property
    def config(self):
        if self._config is None:
            self.load_config()
        return self._config
